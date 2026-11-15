"""Input-only WASAPI loopback + optional microphone recorder.

The Tk UI owns an AudioRecorder and polls snapshot().  start(), stop(), and
set_microphone_enabled() do not wait for audio hardware.  This module never
opens an output stream, changes device defaults, or monitors captured audio.
PyAudioWPatch is imported lazily so the mixer and writer can be tested without
audio hardware (or without the Windows-only dependency).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Full, Queue
import math
import os
import struct
import threading
import time
from typing import Any, Callable

import numpy as np


OUTPUT_SAMPLE_RATE = 48_000
OUTPUT_CHANNELS = 2
CAPTURE_BUFFER_FRAMES = 512
MIX_BLOCK_FRAMES = 960
MIX_LATENCY_SECONDS = 0.18


class CaptureError(RuntimeError):
    """A user-visible recording error; a partial WAV may still be usable."""


@dataclass(frozen=True)
class AudioDevice:
    index: int
    name: str
    channels: int
    sample_rate: int
    is_default: bool = False


@dataclass(frozen=True)
class DeviceCatalog:
    loopbacks: tuple[AudioDevice, ...]
    microphones: tuple[AudioDevice, ...]


@dataclass(frozen=True)
class RecorderConfig:
    output_path: Path | str
    loopback_device_index: int | None = None
    microphone_device_index: int | None = None
    microphone_enabled: bool = True
    sample_rate: int = OUTPUT_SAMPLE_RATE
    system_gain: float = 1.0
    microphone_gain: float = 1.0


@dataclass(frozen=True)
class RecorderStatus:
    state: str
    elapsed_seconds: float
    frames_written: int
    file_bytes: int
    microphone_enabled: bool
    system_peak: float
    microphone_peak: float
    error: str | None
    warnings: tuple[str, ...]


def _load_pyaudio() -> Any:
    try:
        import pyaudiowpatch
    except ImportError as exc:
        raise CaptureError(
            "缺少录音依赖 PyAudioWPatch；请安装 call_recorder/requirements.txt。"
        ) from exc
    return pyaudiowpatch


def _devices_from_manager(manager: Any, module: Any) -> DeviceCatalog:
    wasapi = manager.get_host_api_info_by_type(module.paWASAPI)
    api_index = int(wasapi["index"])
    try:
        default_loopback = int(manager.get_default_wasapi_loopback()["index"])
    except (OSError, LookupError, ValueError, KeyError):
        default_loopback = -1
    default_microphone = int(wasapi.get("defaultInputDevice", -1))
    loopbacks: list[AudioDevice] = []
    microphones: list[AudioDevice] = []
    for index in range(manager.get_device_count()):
        info = manager.get_device_info_by_index(index)
        if int(info.get("hostApi", -1)) != api_index:
            continue
        channels = int(info.get("maxInputChannels", 0))
        if channels < 1:
            continue
        is_loopback = bool(info.get("isLoopbackDevice", False))
        device = AudioDevice(
            index=int(info["index"]),
            name=str(info["name"]),
            channels=channels,
            sample_rate=int(round(float(info["defaultSampleRate"]))),
            is_default=int(info["index"]) == (
                default_loopback if is_loopback else default_microphone
            ),
        )
        (loopbacks if is_loopback else microphones).append(device)
    return DeviceCatalog(tuple(loopbacks), tuple(microphones))


def list_devices(*, _audio_module: Any | None = None) -> DeviceCatalog:
    """Enumerate WASAPI inputs and speaker loopbacks without opening streams."""
    module = _audio_module if _audio_module is not None else _load_pyaudio()
    manager = None
    try:
        manager = module.PyAudio()
        return _devices_from_manager(manager, module)
    except (OSError, KeyError, ValueError) as exc:
        raise CaptureError(f"无法枚举 Windows WASAPI 音频设备：{exc}") from exc
    finally:
        if manager is not None:
            manager.terminate()


def _select_device(
    devices: tuple[AudioDevice, ...], selected: int | None, label: str
) -> AudioDevice:
    if selected is not None:
        for device in devices:
            if device.index == selected:
                return device
        raise CaptureError(f"所选{label}设备已不可用，请刷新设备列表后重选。")
    for device in devices:
        if device.is_default:
            return device
    if devices:
        return devices[0]
    raise CaptureError(f"未找到可用的{label}设备。")


def _to_stereo(samples: np.ndarray) -> np.ndarray:
    """Downmix normal Windows speaker order, including center-channel speech."""
    samples = np.asarray(samples, dtype=np.float32)
    if samples.ndim != 2 or samples.shape[1] < 1:
        raise ValueError("Audio must have shape (frames, channels).")
    channels = samples.shape[1]
    if channels == 1:
        return np.repeat(samples, 2, axis=1)
    if channels == 2:
        return samples.copy()
    weights = np.zeros((channels, 2), dtype=np.float32)
    weights[0, 0] = weights[1, 1] = 1.0
    weights[2] = math.sqrt(0.5)
    if channels > 3:
        weights[3] = 0.5  # LFE, when supplied by a multichannel endpoint.
    for channel in range(4, channels):
        weights[channel, channel % 2] = math.sqrt(0.5)
    weights /= max(1.0, float(np.max(np.sum(weights, axis=0))))
    return samples @ weights


class _StreamingResampler:
    """Continuous linear interpolation with integer phase across packet edges."""

    def __init__(self, input_rate: int, output_rate: int = OUTPUT_SAMPLE_RATE):
        if input_rate <= 0 or output_rate <= 0:
            raise ValueError("Sample rates must be positive.")
        self.input_rate = input_rate
        self.output_rate = output_rate
        self._input_frames = 0
        self._output_frames = 0
        self._last: np.ndarray | None = None

    def process(self, samples: np.ndarray) -> np.ndarray:
        samples = np.asarray(samples, dtype=np.float32)
        if len(samples) == 0:
            return np.empty((0, 2), dtype=np.float32)
        previous_count = self._input_frames
        self._input_frames += len(samples)
        if self._last is None:
            data, origin = samples, previous_count
        else:
            data = np.concatenate((self._last[None, :], samples), axis=0)
            origin = previous_count - 1
        self._last = samples[-1].copy()
        end = ((self._input_frames - 1) * self.output_rate) // self.input_rate + 1
        indices = np.arange(self._output_frames, end, dtype=np.float64)
        positions = indices * self.input_rate / self.output_rate - origin
        self._output_frames = end
        lower = np.floor(positions).astype(np.int64)
        lower = np.clip(lower, 0, len(data) - 1)
        upper = np.minimum(lower + 1, len(data) - 1)
        fraction = (positions - lower)[:, None]
        return (data[lower] + (data[upper] - data[lower]) * fraction).astype(np.float32)


class _TrackBuffer:
    """Sparse audio timeline; absent WASAPI callbacks become genuine silence."""

    def __init__(self) -> None:
        self._chunks: deque[tuple[int, np.ndarray]] = deque()

    def clear(self) -> None:
        self._chunks.clear()

    def add(self, start: int, samples: np.ndarray) -> None:
        if len(samples):
            # A device clock may move the newest packet slightly backwards.
            # Replace overlapping future samples and retain chronological order.
            while self._chunks and self._chunks[-1][0] >= start:
                self._chunks.pop()
            if self._chunks:
                previous_start, previous = self._chunks[-1]
                if previous_start + len(previous) > start:
                    self._chunks[-1] = (previous_start, previous[: start - previous_start])
            self._chunks.append((start, samples))

    def render(self, start: int, count: int) -> np.ndarray:
        result = np.zeros((count, 2), dtype=np.float32)
        end = start + count
        while self._chunks:
            position, samples = self._chunks[0]
            sample_end = position + len(samples)
            if sample_end <= start:
                self._chunks.popleft()
                continue
            if position >= end:
                break
            left, right = max(start, position), min(end, sample_end)
            result[left - start : right - start] = samples[
                left - position : right - position
            ]
            if sample_end <= end:
                self._chunks.popleft()
            else:
                self._chunks[0] = (end, samples[end - position :])
                break
        return result


class _PcmWaveWriter:
    """Exclusive-create PCM WAV, with a durable header checkpoint every second."""

    MAX_DATA_BYTES = 0xFFFFFFFF - 36

    def __init__(self, path: Path, sample_rate: int = OUTPUT_SAMPLE_RATE):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.sample_rate = sample_rate
        self.frames_written = 0
        self._file = path.open("x+b", buffering=0)
        self._closed = False
        try:
            self.checkpoint()
        except BaseException:
            self._file.close()
            raise

    @property
    def file_bytes(self) -> int:
        return 44 + self.frames_written * 4

    def _header(self) -> bytes:
        size = self.frames_written * 4
        return struct.pack(
            "<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + size, b"WAVE", b"fmt ",
            16, 1, 2, self.sample_rate, self.sample_rate * 4, 4, 16, b"data", size,
        )

    def write(self, samples: np.ndarray) -> None:
        values = np.nan_to_num(samples, nan=0.0, posinf=1.0, neginf=-1.0)
        pcm = np.rint(np.clip(values, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
        if self.frames_written * 4 + len(pcm) > self.MAX_DATA_BYTES:
            raise CaptureError("录音已达到标准 WAV 的约 4 GiB 上限，已保存当前录音；请开始新的录音。")
        view = memoryview(pcm)
        try:
            while view:
                written = self._file.write(view)
                if not written:
                    raise OSError("音频写盘未取得进展")
                view = view[written:]
            self.frames_written += len(pcm) // 4
        except BaseException:
            self._recover_length()
            raise

    def _recover_length(self) -> None:
        end = self._file.seek(0, os.SEEK_END)
        self.frames_written = max(0, end - 44) // 4
        self._file.truncate(44 + self.frames_written * 4)

    def checkpoint(self) -> None:
        self._file.seek(0)
        header = memoryview(self._header())
        while header:
            written = self._file.write(header)
            if not written:
                raise OSError("无法更新 WAV 文件头")
            header = header[written:]
        self._file.seek(0, os.SEEK_END)
        self._file.flush()
        os.fsync(self._file.fileno())

    def close(self) -> None:
        if not self._closed:
            try:
                self._recover_length()
                self.checkpoint()
            finally:
                self._closed = True
                self._file.close()


@dataclass
class _Packet:
    source: str
    generation: int
    timestamp: float
    data: bytes
    channels: int
    rate: int
    status: int


class _SourceTimeline:
    def __init__(self, rate: int, output_rate: int):
        self.rate = rate
        self.output_rate = output_rate
        self.resampler = _StreamingResampler(rate, output_rate)
        self.cursor: int | None = None
        self.buffer = _TrackBuffer()

    def append(self, packet: _Packet, epoch: float) -> None:
        samples = np.frombuffer(packet.data, dtype="<f4")
        if len(samples) % packet.channels:
            raise CaptureError("音频设备返回了不完整的采样数据。")
        stereo = _to_stereo(samples.reshape(-1, packet.channels))
        desired = max(0, round((packet.timestamp - epoch) * self.output_rate))
        # The two devices have independent clocks.  Preserve packet continuity,
        # but re-anchor real gaps or sustained clock drift to the common clock.
        if self.cursor is None or abs(desired - self.cursor) > self.output_rate * 0.06:
            self.cursor = desired
            self.resampler = _StreamingResampler(self.rate, self.output_rate)
        converted = self.resampler.process(stereo)
        self.buffer.add(self.cursor, converted)
        self.cursor += len(converted)


class AudioRecorder:
    """Thread-safe controller for one recording; create a new instance to retry."""

    def __init__(
        self,
        config: RecorderConfig,
        *,
        _audio_module: Any | None = None,
        _clock: Callable[[], float] = time.monotonic,
    ):
        if config.sample_rate != OUTPUT_SAMPLE_RATE:
            raise ValueError("输出采样率固定为 48000 Hz。")
        for gain in (config.system_gain, config.microphone_gain):
            if not math.isfinite(gain) or not 0 <= gain <= 4:
                raise ValueError("音量增益必须是 0 到 4 之间的有限数值。")
        self.config = config
        self.output_path = Path(config.output_path).expanduser().resolve()
        if self.output_path.suffix.lower() != ".wav":
            raise ValueError("录音文件必须使用 .wav 扩展名。")
        self._audio_module = _audio_module
        self._clock = _clock
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._done = threading.Event()
        self._packets: Queue[_Packet] = Queue(maxsize=512)
        self._thread: threading.Thread | None = None
        self._state = "idle"
        self._mic_enabled = bool(config.microphone_enabled)
        self._mic_generation = 0
        self._epoch: float | None = None
        self._stop_time: float | None = None
        self._finished_time: float | None = None
        self._frames_written = 0
        self._file_bytes = 0
        self._system_peak = 0.0
        self._microphone_peak = 0.0
        self._error: str | None = None
        self._callback_error: str | None = None
        self._warnings: list[str] = []

    def start(self) -> None:
        with self._lock:
            if self._state != "idle":
                raise RuntimeError("此录音实例已启动或结束，请创建新的 AudioRecorder。")
            self._state = "starting"
            self._thread = threading.Thread(
                target=self._run, name="call-recorder-capture", daemon=False
            )
            self._thread.start()

    def set_microphone_enabled(self, enabled: bool) -> None:
        with self._lock:
            if self._state in ("stopping", "stopped", "error"):
                return
            if self._mic_enabled != bool(enabled):
                self._mic_enabled = bool(enabled)
                self._mic_generation += 1
                self._microphone_peak = 0.0
        self._wake.set()

    def stop(self, wait: bool = False, timeout: float | None = None) -> bool:
        with self._lock:
            if self._stop_time is None:
                self._stop_time = self._clock()
            self._stop.set()
            if self._state == "idle":
                self._state = "stopped"
                self._finished_time = self._stop_time
                self._done.set()
            elif self._state in ("starting", "recording"):
                self._state = "stopping"
        self._wake.set()
        return self.wait(timeout) if wait else self._done.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._done.wait(timeout)

    def snapshot(self) -> RecorderStatus:
        with self._lock:
            now = self._finished_time or self._stop_time or self._clock()
            elapsed = max(0.0, now - self._epoch) if self._epoch is not None else 0.0
            return RecorderStatus(
                self._state, elapsed, self._frames_written, self._file_bytes,
                self._mic_enabled, self._system_peak, self._microphone_peak,
                self._error, tuple(self._warnings),
            )

    def _warn(self, message: str) -> None:
        with self._lock:
            if message not in self._warnings:
                self._warnings.append(message)
                self._warnings = self._warnings[-32:]

    def _callback(self, source: str, device: AudioDevice, generation: int, module: Any):
        def callback(data: bytes, frame_count: int, timing: dict, status: int):
            try:
                if self._stop.is_set():
                    return None, module.paComplete
                with self._lock:
                    if source == "microphone" and (
                        not self._mic_enabled or generation != self._mic_generation
                    ):
                        return None, module.paContinue
                if data:
                    now = self._clock()
                    adc = float(timing.get("input_buffer_adc_time", 0.0))
                    current = float(timing.get("current_time", 0.0))
                    age = current - adc
                    if adc <= 0 or not math.isfinite(age) or not 0 <= age <= 5:
                        age = frame_count / device.sample_rate
                    packet = _Packet(
                        source, generation, now - age, bytes(data), device.channels,
                        device.sample_rate, int(status),
                    )
                    self._packets.put_nowait(packet)
                    self._wake.set()
                return None, module.paContinue
            except Full:
                with self._lock:
                    self._callback_error = "音频缓冲区已满，写盘速度不足，已停止以避免悄悄丢失录音。"
            except BaseException as exc:
                with self._lock:
                    self._callback_error = f"音频采集回调失败：{exc}"
            self._wake.set()
            return None, module.paAbort
        return callback

    def _open_stream(self, manager: Any, module: Any, device: AudioDevice, source: str, generation: int):
        return manager.open(
            format=module.paFloat32,
            channels=device.channels,
            rate=device.sample_rate,
            input=True,
            output=False,
            input_device_index=device.index,
            frames_per_buffer=CAPTURE_BUFFER_FRAMES,
            stream_callback=self._callback(source, device, generation, module),
            start=False,
        )

    @staticmethod
    def _close_stream(stream: Any | None) -> None:
        if stream is not None:
            try:
                stream.stop_stream()
            finally:
                stream.close()

    def _run(self) -> None:
        manager = system_stream = microphone_stream = writer = None
        system_timeline = microphone_timeline = None
        generation = -1
        try:
            module = self._audio_module if self._audio_module is not None else _load_pyaudio()
            manager = module.PyAudio()
            devices = _devices_from_manager(manager, module)
            system_device = _select_device(devices.loopbacks, self.config.loopback_device_index, "系统声音")
            system_stream = self._open_stream(manager, module, system_device, "system", 0)
            system_timeline = _SourceTimeline(system_device.sample_rate, self.config.sample_rate)
            try:
                microphone_device = _select_device(
                    devices.microphones, self.config.microphone_device_index, "麦克风"
                )
            except CaptureError as exc:
                microphone_device = None
                if self._mic_enabled:
                    self._warn(str(exc) + " 本次将继续录制系统声音。")
                    with self._lock:
                        self._mic_enabled = False
            if self._stop.is_set():
                return
            writer = _PcmWaveWriter(self.output_path, self.config.sample_rate)
            with self._lock:
                self._epoch = self._clock()
                self._file_bytes = writer.file_bytes
            system_stream.start_stream()
            with self._lock:
                if not self._stop.is_set():
                    self._state = "recording"
            last_checkpoint = last_health = self._clock()
            while not self._stop.is_set():
                self._wake.clear()
                with self._lock:
                    enabled, requested_generation = self._mic_enabled, self._mic_generation
                    callback_error = self._callback_error
                if callback_error:
                    raise CaptureError(callback_error)
                if requested_generation != generation:
                    old_stream, microphone_stream = microphone_stream, None
                    try:
                        self._close_stream(old_stream)
                    except OSError as exc:
                        self._warn(f"关闭麦克风设备时发生错误：{exc}")
                    microphone_timeline = None
                    generation = requested_generation
                    if enabled:
                        try:
                            if microphone_device is None:
                                raise CaptureError("未找到可用的麦克风设备。")
                            microphone_stream = self._open_stream(
                                manager, module, microphone_device, "microphone", generation
                            )
                            microphone_timeline = _SourceTimeline(
                                microphone_device.sample_rate, self.config.sample_rate
                            )
                            microphone_stream.start_stream()
                        except Exception as exc:
                            old_stream, microphone_stream = microphone_stream, None
                            try:
                                self._close_stream(old_stream)
                            except Exception:
                                pass
                            microphone_timeline = None
                            with self._lock:
                                # A newer UI command must not be overwritten.
                                if self._mic_generation == generation:
                                    self._mic_enabled = False
                                    self._mic_generation += 1
                            self._warn(f"麦克风无法录制，系统声音继续录制：{exc}")
                self._drain_packets(system_timeline, microphone_timeline, generation)
                now = self._clock()
                target = max(0, int((now - self._epoch - MIX_LATENCY_SECONDS) * self.config.sample_rate))
                self._render_to(writer, system_timeline, microphone_timeline, generation, target)
                if now - last_checkpoint >= 1.0:
                    writer.checkpoint()
                    last_checkpoint = now
                if now - last_health >= 0.25:
                    if not system_stream.is_active():
                        raise CaptureError("系统声音设备已停止或断开，录音已结束。")
                    if microphone_stream is not None:
                        try:
                            mic_active = microphone_stream.is_active()
                        except OSError:
                            mic_active = False
                        if not mic_active:
                            self.set_microphone_enabled(False)
                            self._warn("麦克风设备已停止或断开，系统声音继续录制。")
                    last_health = now
                self._wake.wait(0.02)
            # Stop the capture sources before draining their last callback data.
            for stream in (system_stream, microphone_stream):
                try:
                    self._close_stream(stream)
                except Exception as exc:
                    self._warn(f"结束采集时设备已不可用：{exc}")
            system_stream = microphone_stream = None
            self._drain_packets(system_timeline, microphone_timeline, generation)
            end = self._stop_time if self._stop_time is not None else self._clock()
            target = max(0, round((end - self._epoch) * self.config.sample_rate))
            self._render_to(writer, system_timeline, microphone_timeline, generation, target)
        except Exception as exc:
            with self._lock:
                self._error = str(exc) or type(exc).__name__
        finally:
            for stream in (microphone_stream, system_stream):
                try:
                    self._close_stream(stream)
                except Exception as exc:
                    self._warn(f"音频设备关闭时发生错误：{exc}")
            if writer is not None:
                try:
                    writer.close()
                except Exception as exc:
                    with self._lock:
                        if self._error is None:
                            self._error = f"录音文件保存失败：{exc}"
                with self._lock:
                    self._frames_written = writer.frames_written
                    self._file_bytes = writer.file_bytes
            if manager is not None:
                try:
                    manager.terminate()
                except Exception as exc:
                    self._warn(f"音频驱动释放时发生错误：{exc}")
            with self._lock:
                self._finished_time = self._stop_time or self._clock()
                self._state = "error" if self._error else "stopped"
                self._system_peak = self._microphone_peak = 0.0
            self._done.set()

    def _drain_packets(
        self, system: _SourceTimeline, microphone: _SourceTimeline | None, generation: int
    ) -> None:
        while True:
            try:
                packet = self._packets.get_nowait()
            except Empty:
                return
            if packet.status:
                label = "系统声音" if packet.source == "system" else "麦克风"
                self._warn(f"{label}采集曾报告缓冲状态 {packet.status}，录音中可能有短暂缺口。")
            if packet.source == "system":
                system.append(packet, self._epoch)
            else:
                with self._lock:
                    valid = self._mic_enabled and packet.generation == self._mic_generation
                if valid and packet.generation == generation and microphone is not None:
                    microphone.append(packet, self._epoch)

    def _render_to(
        self, writer: _PcmWaveWriter, system: _SourceTimeline,
        microphone: _SourceTimeline | None, generation: int, target: int,
    ) -> None:
        while writer.frames_written < target:
            count = min(MIX_BLOCK_FRAMES, target - writer.frames_written)
            system_audio = system.buffer.render(writer.frames_written, count)
            with self._lock:
                mic_valid = self._mic_enabled and generation == self._mic_generation
                if not mic_valid and microphone is not None:
                    microphone.buffer.clear()
                microphone_audio = (
                    microphone.buffer.render(writer.frames_written, count)
                    if microphone is not None and mic_valid
                    else np.zeros_like(system_audio)
                )
                mixed = system_audio * self.config.system_gain + microphone_audio * self.config.microphone_gain
            # Disk I/O cannot hold the UI/control lock.  An off command clears
            # every pending microphone block; only a block already submitted
            # to the disk writer (at most 20 ms) can still be completing.
            writer.write(mixed)
            with self._lock:
                self._frames_written = writer.frames_written
                self._file_bytes = writer.file_bytes
                self._system_peak = min(1.0, float(np.max(np.abs(system_audio))))
                self._microphone_peak = min(1.0, float(np.max(np.abs(microphone_audio))))
