"""Synthetic-only tests: never instantiate a real audio driver or device."""

from pathlib import Path
from queue import Queue
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import wave

import numpy as np

from call_recorder.capture import (
    AudioRecorder,
    CaptureError,
    RecorderConfig,
    _Packet,
    _PcmWaveWriter,
    _SourceTimeline,
    _StreamingResampler,
    _TrackBuffer,
    _to_stereo,
    list_devices,
)


class FakeClock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value


class FakeStream:
    def __init__(self, kwargs):
        self.kwargs = kwargs
        self.active = False
        self.closed = False

    def start_stream(self):
        self.active = True

    def stop_stream(self):
        self.active = False

    def close(self):
        self.closed = True

    def is_active(self):
        return self.active

    def emit(self, values, *, age, status=0):
        values = np.asarray(values, dtype="<f4")
        return self.kwargs["stream_callback"](
            values.tobytes(), len(values),
            {"current_time": 1000.0, "input_buffer_adc_time": 1000.0 - age},
            status,
        )


class FakeManager:
    def __init__(self, system_rate=48000, microphone_rate=48000):
        self.devices = [
            dict(index=0, name="Speakers", maxInputChannels=0, hostApi=0,
                 defaultSampleRate=system_rate, isLoopbackDevice=False),
            dict(index=1, name="Speakers [Loopback]", maxInputChannels=2,
                 hostApi=0, defaultSampleRate=system_rate, isLoopbackDevice=True),
            dict(index=2, name="Microphone", maxInputChannels=1, hostApi=0,
                 defaultSampleRate=microphone_rate, isLoopbackDevice=False),
            dict(index=3, name="Legacy duplicate", maxInputChannels=1, hostApi=1,
                 defaultSampleRate=44100, isLoopbackDevice=False),
        ]
        self.streams = []
        self.terminated = False
        self.microphone_error = None

    def get_host_api_info_by_type(self, kind):
        return dict(index=0, defaultInputDevice=2, defaultOutputDevice=0)

    def get_default_wasapi_loopback(self):
        return self.devices[1]

    def get_device_count(self):
        return len(self.devices)

    def get_device_info_by_index(self, index):
        return self.devices[index]

    def open(self, **kwargs):
        if kwargs["input_device_index"] == 2 and self.microphone_error:
            raise self.microphone_error
        stream = FakeStream(kwargs)
        self.streams.append(stream)
        return stream

    def terminate(self):
        self.terminated = True


class FakeAudio:
    paWASAPI = 13
    paFloat32 = 1
    paContinue = 0
    paComplete = 1
    paAbort = 2

    def __init__(self, manager=None):
        self.manager = manager or FakeManager()

    def PyAudio(self):
        return self.manager


def eventually(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        threading.Event().wait(0.005)
    raise AssertionError("Synthetic worker did not reach the expected state")


def read_wav(path):
    with wave.open(str(path), "rb") as recording:
        metadata = (
            recording.getnchannels(), recording.getsampwidth(),
            recording.getframerate(), recording.getnframes(),
        )
        pcm = np.frombuffer(recording.readframes(recording.getnframes()), dtype="<i2")
    return metadata, pcm.reshape(-1, 2).astype(np.float32) / 32767.0


class DeviceTests(unittest.TestCase):
    def test_enumeration_is_wasapi_only_and_never_opens_a_stream(self):
        module = FakeAudio()
        devices = list_devices(_audio_module=module)
        self.assertEqual([device.index for device in devices.loopbacks], [1])
        self.assertEqual([device.index for device in devices.microphones], [2])
        self.assertTrue(devices.loopbacks[0].is_default)
        self.assertTrue(devices.microphones[0].is_default)
        self.assertEqual(module.manager.streams, [])
        self.assertTrue(module.manager.terminated)


class MixerTests(unittest.TestCase):
    def test_resampling_has_no_packet_boundary_phase_resets(self):
        signal = np.column_stack((np.sin(np.arange(4410) * 0.04), np.cos(np.arange(4410) * 0.02)))
        complete = _StreamingResampler(44100).process(signal)
        streaming = _StreamingResampler(44100)
        pieces = [streaming.process(signal[index:index + 113]) for index in range(0, len(signal), 113)]
        assembled = np.concatenate(pieces)
        np.testing.assert_array_equal(complete, assembled)
        self.assertLessEqual(abs(len(assembled) - 4800), 1)

    def test_one_sample_packets_and_upsampling_remain_continuous(self):
        resampler = _StreamingResampler(16000)
        result = np.concatenate([resampler.process(np.full((1, 2), value)) for value in range(4)])
        np.testing.assert_allclose(result[:, 0], np.arange(10) / 3, atol=1e-6)

    def test_multichannel_downmix_keeps_center_voice(self):
        audio = np.zeros((32, 6), dtype=np.float32)
        audio[:, 2] = 0.8
        stereo = _to_stereo(audio)
        self.assertTrue(np.all(stereo > 0.1))
        np.testing.assert_array_equal(stereo[:, 0], stereo[:, 1])

    def test_sparse_timeline_inserts_silence_and_drops_consumed_data(self):
        buffer = _TrackBuffer()
        buffer.add(3, np.ones((4, 2), dtype=np.float32))
        result = buffer.render(0, 5)
        np.testing.assert_array_equal(result[:3], 0)
        np.testing.assert_array_equal(result[3:], 1)
        second = buffer.render(5, 5)
        np.testing.assert_array_equal(second[:2], 1)
        np.testing.assert_array_equal(second[2:], 0)
        self.assertEqual(len(buffer._chunks), 0)

    def test_long_capture_gap_reanchors_instead_of_shortening_recording(self):
        timeline = _SourceTimeline(48000, 48000)
        data = np.full((480, 2), 0.25, dtype="<f4").tobytes()
        timeline.append(_Packet("system", 0, 100.0, data, 2, 48000, 0), 100.0)
        timeline.append(_Packet("system", 0, 101.0, data, 2, 48000, 0), 100.0)
        result = timeline.buffer.render(0, 48480)
        np.testing.assert_array_equal(result[480:48000], 0)
        np.testing.assert_array_equal(result[48000:], 0.25)

    def test_clock_correction_replaces_future_overlap_in_order(self):
        buffer = _TrackBuffer()
        buffer.add(0, np.full((10, 2), 0.2))
        buffer.add(10, np.full((10, 2), 0.3))
        buffer.add(5, np.full((10, 2), 0.7))
        result = buffer.render(0, 20)
        np.testing.assert_allclose(result[:5], 0.2)
        np.testing.assert_allclose(result[5:15], 0.7)
        np.testing.assert_array_equal(result[15:], 0)


class WriterTests(unittest.TestCase):
    def test_checkpoint_is_playable_before_close_and_clips_without_wrapping(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "recording.wav"
            writer = _PcmWaveWriter(path)
            try:
                writer.write(np.array([[1.5, -1.5], [float("nan"), 0.5]], dtype=np.float32))
                writer.checkpoint()
                metadata, audio = read_wav(path)
                self.assertEqual(metadata, (2, 2, 48000, 2))
                np.testing.assert_allclose(audio[0], [1, -1], atol=1e-6)
                self.assertEqual(audio[1, 0], 0)
            finally:
                writer.close()

    def test_existing_file_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "existing.wav"
            path.write_bytes(b"existing recording")
            with self.assertRaises(FileExistsError):
                _PcmWaveWriter(path)
            self.assertEqual(path.read_bytes(), b"existing recording")

    def test_partial_disk_write_is_truncated_to_whole_pcm_frames_and_finalized(self):
        class FailOnce:
            def __init__(self, original):
                self.original = original
                self.calls = 0

            def write(self, data):
                self.calls += 1
                if self.calls == 1:
                    return self.original.write(data[:5])
                if self.calls == 2:
                    raise OSError("synthetic full disk")
                return self.original.write(data)

            def __getattr__(self, name):
                return getattr(self.original, name)

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "partial.wav"
            writer = _PcmWaveWriter(path)
            writer._file = FailOnce(writer._file)
            with self.assertRaisesRegex(OSError, "full disk"):
                writer.write(np.full((3, 2), 0.25, dtype=np.float32))
            writer.close()
            metadata, audio = read_wav(path)
            self.assertEqual(metadata[-1], 1)
            np.testing.assert_allclose(audio, 0.25, atol=4e-5)

    def test_riff_size_limit_keeps_prior_recording_valid(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "limit.wav"
            writer = _PcmWaveWriter(path)
            writer.MAX_DATA_BYTES = 8
            writer.write(np.zeros((2, 2), dtype=np.float32))
            with self.assertRaises(CaptureError):
                writer.write(np.zeros((1, 2), dtype=np.float32))
            writer.close()
            self.assertEqual(read_wav(path)[0][-1], 2)


class RecorderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "test.wav"
        self.clock = FakeClock()
        self.module = FakeAudio()
        self.recorders = []

    def tearDown(self):
        for recorder in self.recorders:
            recorder.stop(wait=True, timeout=2)
        self.temporary.cleanup()

    def start_recorder(self, microphone_enabled=True):
        recorder = AudioRecorder(
            RecorderConfig(self.path, microphone_enabled=microphone_enabled),
            _audio_module=self.module, _clock=self.clock,
        )
        self.recorders.append(recorder)
        recorder.start()
        eventually(lambda: recorder.snapshot().state == "recording")
        if microphone_enabled and self.module.manager.microphone_error is None:
            eventually(lambda: len(self.module.manager.streams) == 2)
        return recorder

    def test_different_native_rates_mix_to_fixed_stereo_wav(self):
        self.module = FakeAudio(FakeManager(system_rate=44100, microphone_rate=16000))
        recorder = self.start_recorder()
        self.clock.value = 100.1
        self.module.manager.streams[0].emit(np.full((4410, 2), 0.25), age=0.1)
        self.module.manager.streams[1].emit(np.full((1600, 1), 0.5), age=0.1)
        self.assertTrue(recorder.stop(wait=True, timeout=2))
        metadata, audio = read_wav(self.path)
        self.assertEqual(metadata, (2, 2, 48000, 4800))
        np.testing.assert_allclose(audio[20:-20], 0.75, atol=4e-5)
        self.assertEqual(recorder.snapshot().state, "stopped")
        for stream in self.module.manager.streams:
            self.assertTrue(stream.kwargs["input"])
            self.assertFalse(stream.kwargs["output"])
            self.assertFalse(stream.kwargs["start"])
            self.assertTrue(stream.closed)
        self.assertTrue(self.module.manager.terminated)

    def test_mic_off_immediately_removes_already_queued_audio(self):
        recorder = self.start_recorder()
        self.clock.value = 100.1
        self.module.manager.streams[0].emit(np.full((4800, 2), 0.2), age=0.1)
        old_microphone = self.module.manager.streams[1]
        old_microphone.emit(np.full((4800, 1), 0.6), age=0.1)
        recorder.set_microphone_enabled(False)
        self.assertFalse(recorder.snapshot().microphone_enabled)
        old_microphone.emit(np.full((4800, 1), 0.9), age=0.1)
        recorder.stop(wait=True, timeout=2)
        np.testing.assert_allclose(read_wav(self.path)[1], 0.2, atol=4e-5)

    def test_mic_can_be_enabled_and_disabled_while_recording(self):
        recorder = self.start_recorder(microphone_enabled=False)
        recorder.set_microphone_enabled(True)
        eventually(lambda: len(self.module.manager.streams) == 2)
        microphone = self.module.manager.streams[-1]
        recorder.set_microphone_enabled(False)
        eventually(lambda: microphone.closed)
        recorder.set_microphone_enabled(True)
        eventually(lambda: len(self.module.manager.streams) == 3)
        self.clock.value = 100.1
        microphone.emit(np.full((4800, 1), 0.9), age=0.1)
        self.module.manager.streams[-1].emit(np.full((4800, 1), 0.3), age=0.1)
        recorder.stop(wait=True, timeout=2)
        np.testing.assert_allclose(read_wav(self.path)[1], 0.3, atol=4e-5)

    def test_silent_playback_device_still_produces_wall_clock_duration(self):
        recorder = self.start_recorder(microphone_enabled=False)
        self.clock.value = 100.25
        recorder.stop(wait=True, timeout=2)
        metadata, audio = read_wav(self.path)
        self.assertEqual(metadata[-1], 12000)
        self.assertFalse(np.any(audio))

    def test_microphone_open_failure_continues_system_recording(self):
        self.module.manager.microphone_error = OSError("synthetic unavailable mic")
        recorder = self.start_recorder()
        eventually(lambda: not recorder.snapshot().microphone_enabled)
        self.clock.value = 100.1
        self.module.manager.streams[0].emit(np.full((4800, 2), 0.4), age=0.1)
        recorder.stop(wait=True, timeout=2)
        self.assertEqual(recorder.snapshot().state, "stopped")
        self.assertTrue(recorder.snapshot().warnings)
        np.testing.assert_allclose(read_wav(self.path)[1], 0.4, atol=4e-5)

    def test_system_disconnect_reports_error_and_finalizes_wav(self):
        recorder = self.start_recorder(microphone_enabled=False)
        self.module.manager.streams[0].active = False
        self.clock.value = 100.4
        recorder._wake.set()
        self.assertTrue(recorder.wait(2))
        self.assertEqual(recorder.snapshot().state, "error")
        self.assertIn("系统声音设备", recorder.snapshot().error)
        metadata, _ = read_wav(self.path)
        self.assertGreater(metadata[-1], 0)

    def test_disk_failure_reports_error_and_closes_input_streams(self):
        recorder = self.start_recorder(microphone_enabled=False)
        with patch("call_recorder.capture._PcmWaveWriter.write", side_effect=OSError("synthetic full disk")):
            self.clock.value = 100.4
            recorder._wake.set()
            self.assertTrue(recorder.wait(2))
        self.assertEqual(recorder.snapshot().state, "error")
        self.assertIn("full disk", recorder.snapshot().error)
        self.assertEqual(read_wav(self.path)[0][-1], 0)
        self.assertTrue(all(stream.closed for stream in self.module.manager.streams))

    def test_stop_before_start_does_not_touch_devices_or_files(self):
        recorder = AudioRecorder(RecorderConfig(self.path), _audio_module=self.module)
        self.assertTrue(recorder.stop(wait=True, timeout=0.1))
        self.assertEqual(recorder.snapshot().state, "stopped")
        self.assertFalse(self.path.exists())
        self.assertEqual(self.module.manager.streams, [])

    def test_cancel_during_startup_does_not_start_capture_or_create_file(self):
        started, release = threading.Event(), threading.Event()
        original = self.module.manager.get_device_count

        def delayed_enumeration():
            started.set()
            if not release.wait(2):
                raise AssertionError("Test did not release enumeration")
            return original()

        self.module.manager.get_device_count = delayed_enumeration
        recorder = AudioRecorder(RecorderConfig(self.path), _audio_module=self.module)
        self.recorders.append(recorder)
        recorder.start()
        self.assertTrue(started.wait(1))
        recorder.stop()
        release.set()
        self.assertTrue(recorder.wait(2))
        self.assertEqual(recorder.snapshot().state, "stopped")
        self.assertFalse(self.path.exists())
        self.assertTrue(all(stream.closed and not stream.active for stream in self.module.manager.streams))

    def test_callback_queue_overflow_aborts_instead_of_silently_losing_audio(self):
        recorder = AudioRecorder(RecorderConfig(self.path), _audio_module=self.module)
        recorder._packets = Queue(maxsize=1)
        device = list_devices(_audio_module=self.module).loopbacks[0]
        callback = recorder._callback("system", device, 0, self.module)
        data = np.zeros((16, 2), dtype="<f4").tobytes()
        timing = {"current_time": 1000.0, "input_buffer_adc_time": 999.99}
        self.assertEqual(callback(data, 16, timing, 0), (None, self.module.paContinue))
        self.assertEqual(callback(data, 16, timing, 0), (None, self.module.paAbort))
        self.assertIn("缓冲区已满", recorder._callback_error)


if __name__ == "__main__":
    unittest.main()
