"""Validate the locked build environment and collect complete license texts."""

from __future__ import annotations

import hashlib
import importlib.metadata as metadata
import json
from pathlib import Path
import re
import shutil
import struct
import sys


ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "packaging" / "_build"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def main(version: str) -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", version), "Version must contain three numeric components"
    toolchain = json.loads((ROOT / "packaging" / "toolchain.json").read_text(encoding="utf-8"))
    assert sys.version.split()[0] == toolchain["python"]["version"], "Unexpected Python version"
    assert struct.calcsize("P") == 8 and sys.platform == "win32", "Use Windows x64 Python"
    lock = (ROOT / "requirements-build.txt").read_text(encoding="utf-8")
    expected = {normalized(name): value for name, value in re.findall(r"^([\w.-]+)==([^\s]+)", lock, re.MULTILINE)}
    installed = {normalized(dist.metadata["Name"]): dist.version for dist in metadata.distributions()}
    assert installed == expected, f"Build environment differs from lock: {installed!r}"

    licenses = BUILD / "licenses"
    # The output is generated entirely from pinned inputs; never remove a user path.
    assert licenses.resolve().is_relative_to(BUILD.resolve())
    if licenses.exists():
        shutil.rmtree(licenses)
    licenses.mkdir(parents=True)
    license_counts = {}
    for name in sorted(expected):
        distribution = metadata.distribution(name)
        copied = 0
        for entry in distribution.files or ():
            relative = Path(str(entry))
            if ".." in relative.parts:
                continue
            lower = str(relative).lower()
            if not any(term in lower for term in ("license", "copying", "notice", "patents")) and relative.name != "METADATA":
                continue
            source = Path(distribution.locate_file(entry))
            if not source.is_file():
                continue
            target = licenses / name / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            copied += relative.name != "METADATA"
        assert copied, f"No complete license files found for {name}"
        license_counts[name] = copied

    base = Path(sys.base_prefix)
    python_license = base / "LICENSE.txt"
    tk_license = base / "tcl" / "tk8.6" / "license.terms"
    assert python_license.is_file() and tk_license.is_file(), "Official Python/Tk license is missing"
    for source, target in (
        (python_license, licenses / "Python" / "LICENSE.txt"),
        (tk_license, licenses / "Tk" / "license.terms"),
        (ROOT / "packaging" / "inno-license.txt", licenses / "InnoSetup" / "LICENSE.txt"),
        (ROOT / "packaging" / "ChineseSimplified.isl", licenses / "InnoSetup" / "ChineseSimplified.isl"),
    ):
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)

    sources_file = ROOT / "packaging" / "licenses" / "SOURCES.json"
    sources = json.loads(sources_file.read_text(encoding="utf-8"))
    for item in sources:
        source = sources_file.parent / item["file"]
        assert digest(source) == item["sha256"], f"Vendored license changed: {source.name}"
        target = licenses / "Upstream" / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    shutil.copyfile(sources_file, licenses / "Upstream" / sources_file.name)
    translation_hash = digest(ROOT / "packaging" / "ChineseSimplified.isl")
    assert translation_hash == toolchain["chineseTranslation"]["sha256"], "Chinese translation changed"
    for required in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
        assert (ROOT / required).is_file(), f"Release documentation is missing: {required}"

    license_manifest = {str(path.relative_to(licenses)).replace("\\", "/"): digest(path)
                        for path in sorted(licenses.rglob("*")) if path.is_file()}
    (licenses / "MANIFEST.json").write_text(json.dumps(license_manifest, indent=2) + "\n", encoding="utf-8")
    source_files = sorted((ROOT / "call_recorder").glob("*.py")) + [ROOT / "call_recorder" / "recorder.ico"]
    source_files += [ROOT / "build.ps1", ROOT / "requirements-build.txt", ROOT / "LICENSE", ROOT / "THIRD_PARTY_NOTICES.md"]
    source_files += sorted(path for path in (ROOT / "packaging").glob("*") if path.is_file())
    manifest = {
        "version": version,
        "platform": "windows-x64",
        "python": sys.version.split()[0],
        "packages": installed,
        "toolchain": toolchain,
        "source_sha256": {str(path.relative_to(ROOT)).replace("\\", "/"): digest(path) for path in source_files},
        "license_files_by_distribution": license_counts,
        "license_manifest_sha256": digest(licenses / "MANIFEST.json"),
    }
    (BUILD / "build-info.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    components = tuple(map(int, version.split("."))) + (0,)
    resource = f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(filevers={components!r}, prodvers={components!r},
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('080404B0', [
    StringStruct('CompanyName', 'Call Recorder contributors'),
    StringStruct('FileDescription', '通话录音'),
    StringStruct('FileVersion', '{version}.0'),
    StringStruct('InternalName', 'CallRecorder'),
    StringStruct('OriginalFilename', 'CallRecorder.exe'),
    StringStruct('ProductName', 'Call Recorder'),
    StringStruct('ProductVersion', '{version}')
  ])]), VarFileInfo([VarStruct('Translation', [2052, 1200])])]
)
"""
    (BUILD / "version-info.txt").write_text(resource, encoding="utf-8")
    print(json.dumps({"python": manifest["python"], "packages": installed, "license_counts": license_counts}, indent=2))


if __name__ == "__main__":
    main(sys.argv[1])
