"""Exercise GUI controls with fake devices and without recording audio."""

from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from call_recorder import ui


class FakeRecorder:
    def __init__(self, config):
        self.config = config
        self.state = "idle"
        self.microphone_enabled = config.microphone_enabled
        self.warnings = ()
        self.file_bytes = 0

    def start(self):
        self.state = "recording"

    def set_microphone_enabled(self, enabled):
        self.microphone_enabled = enabled

    def stop(self, wait=False):
        self.state = "stopped"

    def snapshot(self):
        return SimpleNamespace(
            state=self.state, microphone_enabled=self.microphone_enabled,
            elapsed_seconds=0, system_peak=0, microphone_peak=0,
            warnings=self.warnings, file_bytes=self.file_bytes, error=None,
        )


class RecorderUiTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = tk.Tk()
        self.root.withdraw()
        self.catalog = SimpleNamespace(
            loopbacks=(SimpleNamespace(index=3, name="Speakers", is_default=True),),
            microphones=(SimpleNamespace(index=4, name="Microphone", is_default=True),),
        )
        self.patches = [
            patch.object(ui, "AudioRecorder", FakeRecorder),
            patch.object(ui, "list_devices", return_value=self.catalog),
        ]
        for p in self.patches:
            p.start()
        self.window = ui.RecorderWindow(self.root)
        self.window.save_folder.set(self.temp.name)
        self.window._refresh_devices()

    def tearDown(self):
        self.root.destroy()
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def test_microphone_switch_controls_active_recording(self):
        self.window._toggle_recording()
        recorder = self.window.recorder
        self.assertTrue(recorder.microphone_enabled)
        self.window.want_mic.set(False)
        self.window._update_gate()
        self.assertFalse(recorder.microphone_enabled)
        self.assertEqual(self.window.mic_state_label.cget("text"), "未录入")
        self.window.want_mic.set(True)
        self.window._update_gate()
        self.assertTrue(recorder.microphone_enabled)

    def test_failed_microphone_is_not_shown_as_recording(self):
        self.window._toggle_recording()
        recorder = self.window.recorder
        recorder.microphone_enabled = False
        recorder.warnings = ("麦克风无法录制，系统声音继续录制。",)
        self.window._tick()
        self.assertFalse(self.window.want_mic.get())
        self.assertEqual(self.window.mic_state_label.cget("text"), "未录入")
        self.assertIn("麦克风无法录制", self.window.gate_detail.cget("text"))

    def test_cancelled_start_is_not_reported_as_a_saved_file(self):
        self.window._toggle_recording()
        self.window._toggle_recording()
        self.window._tick()
        self.assertIn("已取消", self.window.status_label.cget("text"))


class PreferenceTests(unittest.TestCase):
    def test_duplicate_names_do_not_overwrite_existing_audio(self):
        with TemporaryDirectory() as temporary:
            folder = Path(temporary)
            stamp = datetime(2026, 11, 16, 12, 34, 56)
            first = ui.next_output_path(folder, stamp)
            first.write_bytes(b"existing audio")
            second = ui.next_output_path(folder, stamp)
            self.assertNotEqual(first, second)
            self.assertEqual(first.read_bytes(), b"existing audio")

    def test_save_folder_is_remembered(self):
        with TemporaryDirectory() as temporary, patch.dict("os.environ", {"LOCALAPPDATA": temporary}):
            ui.save_folder_preference("D:/录音")
            self.assertEqual(ui.load_save_folder(), "D:/录音")


if __name__ == "__main__":
    unittest.main()
