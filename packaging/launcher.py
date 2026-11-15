"""Frozen Windows entry point, including persistent launch diagnostics.

The optional --smoke-test argument uses synthetic devices and refuses all audio
stream opens. It is for verifying the installed build without recording audio.
"""

from __future__ import annotations

import ctypes
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import traceback


def _open_log():
    for base in (Path(os.environ.get("LOCALAPPDATA", Path.home())), Path(tempfile.gettempdir())):
        try:
            folder = base / "CallRecorder" / "logs"
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / "startup.log"
            if path.exists() and path.stat().st_size > 1_000_000:
                path.replace(folder / "startup.previous.log")
            stream = path.open("a", encoding="utf-8", buffering=1)
            return path, stream
        except OSError:
            continue
    return None, None


def _smoke_test(report_path: Path) -> None:
    import tkinter as tk
    import numpy as np
    import pyaudiowpatch
    from call_recorder import ui
    from call_recorder.capture import AudioDevice, AudioRecorder, DeviceCatalog

    def refuse_audio(*_args, **_kwargs):
        raise AssertionError("Audio capture is forbidden during the installation smoke test")

    pyaudiowpatch.PyAudio.open = refuse_audio
    AudioRecorder.start = refuse_audio
    ui.list_devices = lambda: DeviceCatalog(
        (AudioDevice(0, "Smoke test: synthetic playback", 2, 48000, True),),
        (AudioDevice(1, "Smoke test: synthetic microphone", 1, 48000, True),),
    )
    root = tk.Tk()
    window = ui.RecorderWindow(root)
    # The application normally tolerates an absent icon. Release verification must not.
    root.iconbitmap(str(Path(ui.__file__).with_name("recorder.ico")))
    root.update_idletasks()

    def finish() -> None:
        assert window.recorder is None
        assert window.output_path is None
        assert len(window.loopbacks) == len(window.microphones) == 1
        assert float(np.dot(np.array([1.0, 2.0]), np.array([3.0, 4.0]))) == 11.0
        report = {
            "ok": True,
            "python": sys.version.split()[0],
            "numpy": np.__version__,
            "portaudio": pyaudiowpatch.get_portaudio_version_text(),
            "tk": root.tk.call("info", "patchlevel"),
            "window_title": root.title(),
            "window_geometry": root.geometry(),
            "recording_started": False,
            "device_source": "synthetic; all PyAudio stream opens blocked",
        }
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        window._close()

    root.after(1500, finish)
    root.mainloop()


def main() -> int:
    log_path, log_stream = _open_log()
    if log_stream is not None:
        sys.stdout = sys.stderr = log_stream
        logging.basicConfig(stream=log_stream, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        logging.info("Call Recorder launch; Python %s; %s", sys.version.split()[0], datetime.now(timezone.utc).isoformat())
        if len(sys.argv) == 3 and sys.argv[1] == "--smoke-test":
            _smoke_test(Path(sys.argv[2]).resolve())
        elif len(sys.argv) == 1:
            from call_recorder.ui import main as run_window

            run_window()
        else:
            raise ValueError("Unsupported command-line arguments")
        return 0
    except Exception as exc:
        traceback.print_exc()
        detail = f"通话录音无法启动。\n\n{exc}"
        if log_path is not None:
            detail += f"\n\n诊断日志：\n{log_path}"
        # Automated verification must exit rather than wait on a hidden modal dialog.
        if "--smoke-test" not in sys.argv:
            ctypes.windll.user32.MessageBoxW(None, detail, "通话录音", 0x10)
        return 1
    finally:
        if log_stream is not None:
            log_stream.flush()


if __name__ == "__main__":
    raise SystemExit(main())
