"""Check binary architecture, redistributable DLLs, resources and license copies."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

import pefile


def verify(folder: Path) -> dict:
    application = folder / "CallRecorder.exe"
    with pefile.PE(str(application), fast_load=True) as binary:
        assert binary.FILE_HEADER.Machine == 0x8664, "Application must be AMD64"
        assert binary.OPTIONAL_HEADER.Subsystem == 2, "Application must use the Windows GUI subsystem"
    internal = folder / "_internal"
    required = (
        "python313.dll", "_tkinter.pyd", "tcl86t.dll", "tk86t.dll",
        "_tcl_data/init.tcl", "_tk_data/tk.tcl", "call_recorder/recorder.ico",
        "build-info.json", "licenses/MANIFEST.json",
    )
    for name in required:
        assert (internal / name).is_file(), f"Missing runtime resource: {name}"
    assert list(internal.rglob("*portaudio*.pyd")), "PyAudioWPatch extension missing"
    assert list(internal.rglob("*multiarray_umath*.pyd")), "NumPy extension missing"
    assert list(internal.rglob("*openblas*.dll")), "NumPy OpenBLAS runtime missing"
    assert (internal / "vcruntime140.dll").is_file(), "Python MSVC redistributable is missing"
    for name in ("LICENSE", "THIRD_PARTY_NOTICES.md", "使用说明.txt"):
        assert (folder / name).is_file(), f"Missing installed documentation: {name}"
    licenses = internal / "licenses"
    manifest = json.loads((licenses / "MANIFEST.json").read_text(encoding="utf-8"))
    for relative, expected in manifest.items():
        source = licenses / relative
        assert source.is_file(), f"Missing full license: {relative}"
        assert hashlib.sha256(source.read_bytes()).hexdigest() == expected, f"Altered license: {relative}"
    info = json.loads((internal / "build-info.json").read_text(encoding="utf-8"))
    assert info["license_files_by_distribution"]["numpy"] >= 15, "Incomplete NumPy third-party license collection"
    assert (licenses / "Upstream" / "Apache-2.0.txt").is_file()
    assert (licenses / "Upstream" / "PortAudio-LICENSE.txt").is_file()
    assert (licenses / "Upstream" / "Tcl-8.6.15-license.terms").is_file()
    assert any("COPYING" in str(path) for path in (licenses / "pyinstaller").rglob("*"))
    return {"architecture": "AMD64", "subsystem": "Windows GUI", "license_files_verified": len(manifest), "version": info["version"]}


if __name__ == "__main__":
    print(json.dumps(verify(Path(sys.argv[1]).resolve()), indent=2))
