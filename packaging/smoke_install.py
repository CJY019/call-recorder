"""Install, show a capture-disabled GUI, uninstall, and verify user data remains."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid
import winreg

from verify_bundle import verify


def run(arguments: list[str], *, env: dict[str, str], cwd: Path, timeout: int = 90) -> None:
    completed = subprocess.run(arguments, cwd=cwd, env=env, timeout=timeout, check=False)
    if completed.returncode:
        raise RuntimeError(f"Process exited {completed.returncode}: {arguments[0]}")


def main(installer: Path) -> None:
    build = Path(__file__).resolve().parent / "_build"
    sandbox = Path(tempfile.mkdtemp(prefix="smoke-", dir=build))
    app = sandbox / "installed-app"
    local = sandbox / "user-data"
    empty = sandbox / "empty-working-directory"
    empty.mkdir()
    settings = local / "CallRecorder" / "settings.json"
    settings.parent.mkdir(parents=True)
    recording = sandbox / "recordings" / "existing-recording.wav"
    recording.parent.mkdir()
    recording.write_bytes(b"Synthetic preservation sentinel, never captured audio.\n")
    settings.write_text(json.dumps({"save_folder": str(recording.parent)}), encoding="utf-8")
    original_settings, original_recording = settings.read_bytes(), recording.read_bytes()
    environment = dict(os.environ)
    environment["LOCALAPPDATA"] = str(local)
    environment["PATH"] = os.pathsep.join((str(Path(os.environ["WINDIR"]) / "System32"), os.environ["WINDIR"]))
    for name in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
        environment.pop(name, None)
    smoke_tag = uuid.uuid4().hex
    uninstall_key = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\CallRecorder.BuildSmoke.{smoke_tag}_is1"
    install_log = sandbox / "install.log"
    uninstall_log = sandbox / "uninstall.log"
    report_path = sandbox / "gui-report.json"
    result = {"sandbox": str(sandbox), "installer": installer.name, "ok": False}
    uninstaller = app / "unins000.exe"
    try:
        run([str(installer), "/VERYSILENT", "/SP-", "/SUPPRESSMSGBOXES", "/NORESTART", "/NOICONS",
             "/TASKS=", "/LANG=chinesesimp", f"/DIR={app}", f"/LOG={install_log}",
             "/buildsmoke=1", f"/smoketag={smoke_tag}"], env=environment, cwd=empty)
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, uninstall_key) as key:
            assert "通话录音" in winreg.QueryValueEx(key, "DisplayName")[0]
        result["bundle"] = verify(app)
        unrelated = app / "user-recording-do-not-delete.wav"
        unrelated.write_bytes(original_recording)
        run([str(app / "CallRecorder.exe"), "--smoke-test", str(report_path)],
            env=environment, cwd=empty, timeout=30)
        gui = json.loads(report_path.read_text(encoding="utf-8"))
        assert gui["ok"] and not gui["recording_started"]
        result["gui"] = gui
    finally:
        if uninstaller.is_file():
            run([str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", f"/LOG={uninstall_log}"],
                env=environment, cwd=empty)
            deadline = time.monotonic() + 10
            while (app / "CallRecorder.exe").exists() and time.monotonic() < deadline:
                time.sleep(0.1)
        result["program_removed"] = not (app / "CallRecorder.exe").exists()
        result["settings_retained"] = settings.is_file() and settings.read_bytes() == original_settings
        result["recording_retained"] = recording.is_file() and recording.read_bytes() == original_recording
        unrelated = app / "user-recording-do-not-delete.wav"
        result["unrelated_file_retained"] = unrelated.is_file() and unrelated.read_bytes() == original_recording
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, uninstall_key):
                result["uninstall_entry_removed"] = False
        except FileNotFoundError:
            result["uninstall_entry_removed"] = True
        result["ok"] = all(result.get(name, False) for name in (
            "program_removed", "settings_retained", "recording_retained", "unrelated_file_retained", "uninstall_entry_removed"
        )) and result.get("gui", {}).get("ok", False)
        (sandbox / "verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (build / "smoke-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert result["ok"], f"Smoke verification failed; inspect {sandbox}"
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())
