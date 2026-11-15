"""Minimal desktop controls for recording system audio and microphone input.

The recorder never monitors either source back to an output device.  In
particular, the checkbox below controls the *recording*, not Windows' or the
calling application's microphone setting.
"""

from __future__ import annotations

import os
import json
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from call_recorder.capture import AudioRecorder, RecorderConfig, list_devices


BG = "#f4f6f6"
CARD = "#ffffff"
TEXT = "#172a29"
MUTED = "#60716f"
BORDER = "#e0e8e6"
ACCENT = "#1d7668"
ACCENT_DARK = "#155b51"
RED = "#b94242"
FONT = "Microsoft YaHei UI"


def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}" if hours else f"{minutes:02}:{seconds:02}"


def next_output_path(folder: Path, now: datetime | None = None) -> Path:
    """Choose a readable recording name without replacing an existing file."""
    stamp = (now or datetime.now()).strftime("%Y-%m-%d %H%M%S")
    candidate = folder / f"通话录音 {stamp}.wav"
    suffix = 2
    while candidate.exists():
        candidate = folder / f"通话录音 {stamp} ({suffix}).wav"
        suffix += 1
    return candidate


def _settings_file() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "CallRecorder" / "settings.json"


def load_save_folder() -> str:
    default = str(Path.home() / "Music" / "通话录音")
    try:
        with _settings_file().open(encoding="utf-8") as handle:
            saved = json.load(handle).get("save_folder")
        return saved if isinstance(saved, str) and saved.strip() else default
    except (OSError, ValueError, AttributeError):
        return default


def save_folder_preference(folder: str) -> None:
    """Remember the chosen destination without affecting the recording."""
    settings = _settings_file()
    try:
        settings.parent.mkdir(parents=True, exist_ok=True)
        temporary = settings.with_suffix(".tmp")
        temporary.write_text(json.dumps({"save_folder": folder}, ensure_ascii=False), encoding="utf-8")
        temporary.replace(settings)
    except OSError:
        pass


class RecorderWindow:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("通话录音")
        try:
            self.root.iconbitmap(str(Path(__file__).with_name("recorder.ico")))
        except tk.TclError:
            pass
        self.root.configure(bg=BG)
        self.root.geometry("650x680")
        self.root.minsize(620, 650)
        self.root.protocol("WM_DELETE_WINDOW", self._close)

        self.recorder: AudioRecorder | None = None
        self.last_gate: bool | None = None
        self.loopbacks = ()
        self.microphones = ()
        self.output_path: Path | None = None
        self.last_error: str | None = None
        self.last_warning: str | None = None
        self.last_terminal_recorder: AudioRecorder | None = None
        self.closing = False

        self.want_mic = tk.BooleanVar(value=True)
        self.keep_on_top = tk.BooleanVar(value=False)
        self.save_folder = tk.StringVar(value=load_save_folder())
        self._styles()
        self._build()
        self._update_gate()
        self.root.after(100, self._refresh_devices)
        self.root.after(200, self._tick)

    def _styles(self) -> None:
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure(
            "Recorder.Horizontal.TProgressbar", troughcolor="#edf2f1", background=ACCENT,
            bordercolor="#edf2f1", lightcolor=ACCENT, darkcolor=ACCENT, thickness=6,
        )
        style.configure(
            "Recorder.TCombobox", fieldbackground="#f8faf9", background="#f8faf9",
            foreground=TEXT, bordercolor=BORDER, arrowcolor=MUTED, padding=6,
        )
        style.map("Recorder.TCombobox", fieldbackground=[("readonly", "#f8faf9")])

    def _label(self, parent: tk.Misc, text: str = "", **kw: object) -> tk.Label:
        options: dict[str, object] = {"text": text, "bg": CARD, "fg": TEXT, "font": (FONT, 10), "anchor": "w"}
        options.update(kw)
        return tk.Label(parent, **options)

    def _card(self, parent: tk.Misc) -> tk.Frame:
        return tk.Frame(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1, bd=0)

    def _check(self, parent: tk.Misc, text: str, variable: tk.BooleanVar, command: object, **kw: object) -> tk.Checkbutton:
        options: dict[str, object] = {
            "text": text, "variable": variable, "command": command, "bg": CARD, "fg": TEXT,
            "activebackground": CARD, "activeforeground": TEXT, "selectcolor": CARD,
            "font": (FONT, 10), "bd": 0, "highlightthickness": 0, "cursor": "hand2",
        }
        options.update(kw)
        return tk.Checkbutton(parent, **options)

    def _button(self, parent: tk.Misc, text: str, command: object, **kw: object) -> tk.Button:
        options: dict[str, object] = {
            "text": text, "command": command, "bg": "#edf4f2", "fg": ACCENT_DARK,
            "activebackground": "#dbece7", "activeforeground": ACCENT_DARK,
            "font": (FONT, 9), "relief": "flat", "bd": 0, "padx": 12, "pady": 5,
            "cursor": "hand2", "highlightthickness": 0,
        }
        options.update(kw)
        return tk.Button(parent, **options)

    def _build(self) -> None:
        outer = tk.Frame(self.root, bg=BG, padx=24, pady=17)
        outer.pack(fill="both", expand=True)

        header = tk.Frame(outer, bg=BG)
        header.pack(fill="x", pady=(0, 12))
        tk.Label(header, text="通话录音", bg=BG, fg=TEXT, font=(FONT, 21, "bold")).pack(side="left")
        tk.Checkbutton(
            header, text="窗口置顶", variable=self.keep_on_top, command=self._set_topmost,
            bg=BG, fg=MUTED, activebackground=BG, selectcolor=BG, font=(FONT, 9), bd=0,
        ).pack(side="right", anchor="s", pady=(0, 5))

        hero = self._card(outer)
        hero.pack(fill="x", pady=(0, 10))
        hero_inner = tk.Frame(hero, bg=CARD, padx=18, pady=13)
        hero_inner.pack(fill="x")
        state_row = tk.Frame(hero_inner, bg=CARD)
        state_row.pack(fill="x")
        self.status_label = self._label(state_row, "●  准备就绪", font=(FONT, 12, "bold"), fg=ACCENT)
        self.status_label.pack(side="left")
        self.timer_label = self._label(state_row, "00:00", font=("Consolas", 20), fg=TEXT)
        self.timer_label.pack(side="right")
        self.status_detail = self._label(hero_inner, "录制电脑播放声，并按需录入麦克风", fg=MUTED, font=(FONT, 9))
        self.status_detail.pack(fill="x", pady=(3, 10))
        self.record_button = self._button(
            hero_inner, "●   开始录音", self._toggle_recording, bg=ACCENT, fg="white",
            activebackground=ACCENT_DARK, activeforeground="white", font=(FONT, 12, "bold"), pady=9,
        )
        self.record_button.pack(fill="x")

        sources = self._card(outer)
        sources.pack(fill="x", pady=(0, 10))
        source_inner = tk.Frame(sources, bg=CARD, padx=18, pady=12)
        source_inner.pack(fill="x")
        title_row = tk.Frame(source_inner, bg=CARD)
        title_row.pack(fill="x", pady=(0, 7))
        self._label(title_row, "声音来源", font=(FONT, 11, "bold")).pack(side="left")
        self.refresh_button = self._button(title_row, "刷新设备", self._refresh_devices, padx=8, pady=2)
        self.refresh_button.pack(side="right")

        self._source_header(source_inner, "电脑播放声", "包含所选设备上的其他应用声音")
        self.loopback_combo = ttk.Combobox(source_inner, state="readonly", style="Recorder.TCombobox", font=(FONT, 9))
        self.loopback_combo.pack(fill="x", pady=(3, 5))
        self.system_meter = ttk.Progressbar(source_inner, maximum=100, style="Recorder.Horizontal.TProgressbar")
        self.system_meter.pack(fill="x", pady=(0, 8))

        mic_row = tk.Frame(source_inner, bg=CARD)
        mic_row.pack(fill="x")
        self.mic_check = self._check(mic_row, "录制我的麦克风", self.want_mic, self._update_gate, font=(FONT, 10, "bold"))
        self.mic_check.pack(side="left", padx=(0, 5))
        self.mic_state_label = self._label(mic_row, "", fg=ACCENT, font=(FONT, 9))
        self.mic_state_label.pack(side="right")
        self.mic_combo = ttk.Combobox(source_inner, state="readonly", style="Recorder.TCombobox", font=(FONT, 9))
        self.mic_combo.pack(fill="x", pady=(3, 5))
        self.mic_meter = ttk.Progressbar(source_inner, maximum=100, style="Recorder.Horizontal.TProgressbar")
        self.mic_meter.pack(fill="x", pady=(0, 5))

        self.gate_detail = self._label(source_inner, "", fg=MUTED, font=(FONT, 9), wraplength=550, justify="left")
        self.gate_detail.pack(fill="x", pady=(1, 0))

        destination = self._card(outer)
        destination.pack(fill="x", pady=(0, 8))
        dest_inner = tk.Frame(destination, bg=CARD, padx=18, pady=12)
        dest_inner.pack(fill="x")
        self._label(dest_inner, "保存位置", font=(FONT, 11, "bold")).pack(anchor="w")
        dest_row = tk.Frame(dest_inner, bg=CARD)
        dest_row.pack(fill="x", pady=(5, 0))
        self._label(dest_row, textvariable=self.save_folder, fg=MUTED, font=(FONT, 9),
                    wraplength=325, justify="left").pack(side="left", fill="x", expand=True)
        self.open_button = self._button(dest_row, "打开", self._open_folder)
        self.open_button.pack(side="right", padx=(5, 0))
        self.folder_button = self._button(dest_row, "更改", self._choose_folder)
        self.folder_button.pack(side="right")

        tk.Label(outer, text="仅采集音频，不会把麦克风声音播放到耳机。", bg=BG, fg=MUTED,
                 font=(FONT, 9), anchor="w").pack(fill="x", padx=2)

    def _source_header(self, parent: tk.Misc, name: str, detail: str) -> None:
        row = tk.Frame(parent, bg=CARD)
        row.pack(fill="x")
        self._label(row, name, font=(FONT, 10, "bold")).pack(side="left")
        self._label(row, detail, fg=MUTED, font=(FONT, 9)).pack(side="right")

    def _set_topmost(self) -> None:
        self.root.attributes("-topmost", self.keep_on_top.get())

    @staticmethod
    def _populate(combo: ttk.Combobox, devices: tuple) -> None:
        names = [f"{d.name}{'  ·  默认' if d.is_default else ''}" for d in devices]
        combo.configure(values=names)
        if devices:
            default = next((i for i, d in enumerate(devices) if d.is_default), 0)
            combo.current(default)
        else:
            combo.set("未找到可用设备")

    def _refresh_devices(self) -> None:
        if self._is_busy():
            return
        try:
            catalog = list_devices()
            self.loopbacks, self.microphones = catalog.loopbacks, catalog.microphones
            self._populate(self.loopback_combo, self.loopbacks)
            self._populate(self.mic_combo, self.microphones)
            if not self.loopbacks:
                self.status_detail.config(text="未找到可录制的播放设备，请检查声音设置后刷新设备。")
            elif not self.microphones:
                self.status_detail.config(text="未找到麦克风；仍可单独录制电脑播放声。")
            else:
                self.status_detail.config(text="录制电脑播放声，并按需录入麦克风")
        except Exception as exc:
            self.status_detail.config(text="设备读取失败，请检查音频设备后重试。")
            messagebox.showerror("无法读取声音设备", str(exc), parent=self.root)

    def _choose_folder(self) -> None:
        selected = filedialog.askdirectory(parent=self.root, initialdir=self.save_folder.get(), title="选择录音保存位置")
        if selected:
            self.save_folder.set(selected)
            save_folder_preference(selected)

    def _open_folder(self) -> None:
        try:
            folder = Path(self.save_folder.get())
            folder.mkdir(parents=True, exist_ok=True)
            os.startfile(str(folder))  # type: ignore[attr-defined]
        except OSError as exc:
            messagebox.showerror("无法打开保存位置", str(exc), parent=self.root)

    def _update_gate(self) -> None:
        requested = self.want_mic.get()
        if not requested:
            self.mic_state_label.config(text="未录入", fg=MUTED)
        else:
            self.mic_state_label.config(text="将录入" if not self._is_busy() else "正在录入", fg=ACCENT)

        self.gate_detail.config(text="这里只控制录音，不影响通话；通话内静音也不会自动关闭此开关。", fg=MUTED)

        if self.recorder is not None and self._is_busy() and self.last_gate != requested:
            self.recorder.set_microphone_enabled(requested)
            self.last_gate = requested

    def _is_busy(self) -> bool:
        return self.recorder is not None and self.recorder.snapshot().state in {"starting", "recording", "stopping"}

    def _toggle_recording(self) -> None:
        if self._is_busy():
            if self.recorder is not None:
                self.recorder.stop(wait=False)
            return
        if not self.loopbacks:
            messagebox.showinfo("没有播放设备", "请连接播放设备，然后点击“刷新设备”。", parent=self.root)
            return
        loopback_index = self.loopback_combo.current()
        if loopback_index < 0:
            messagebox.showinfo("请选择播放设备", "请选择正在使用的电脑播放设备。", parent=self.root)
            return
        mic_index = self.mic_combo.current()
        if self.want_mic.get() and not self.microphones:
            messagebox.showinfo("没有麦克风", "请连接麦克风并刷新设备，或取消勾选麦克风后开始。", parent=self.root)
            return
        try:
            folder = Path(self.save_folder.get()).expanduser()
            folder.mkdir(parents=True, exist_ok=True)
            self.output_path = next_output_path(folder)
            enabled = self.want_mic.get()
            config = RecorderConfig(
                output_path=self.output_path,
                loopback_device_index=self.loopbacks[loopback_index].index,
                microphone_device_index=self.microphones[mic_index].index if mic_index >= 0 else None,
                microphone_enabled=enabled,
            )
            self.recorder = AudioRecorder(config)
            self.last_gate = enabled
            self.last_error = None
            self.last_warning = None
            self.last_terminal_recorder = None
            self.recorder.start()
            self._render_busy(True)
            self._update_gate()
        except Exception as exc:
            self.recorder = None
            messagebox.showerror("无法开始录音", str(exc), parent=self.root)

    def _render_busy(self, busy: bool) -> None:
        self.loopback_combo.configure(state="disabled" if busy else "readonly")
        self.mic_combo.configure(state="disabled" if busy else "readonly")
        self.refresh_button.configure(state="disabled" if busy else "normal")
        self.folder_button.configure(state="disabled" if busy else "normal")
        self.record_button.configure(
            text="■   结束并保存" if busy else "●   开始录音",
            bg=RED if busy else ACCENT, activebackground="#993535" if busy else ACCENT_DARK,
        )

    def _tick(self) -> None:
        self._update_gate()

        if self.recorder is not None:
            status = self.recorder.snapshot()
            # The backend can turn off a disconnected or inaccessible microphone.
            # Reflect that change instead of displaying a false recording status.
            if status.state == "recording" and self.last_gate and not status.microphone_enabled:
                self.want_mic.set(False)
                self.last_gate = False
                self._update_gate()
            if status.warnings:
                self.last_warning = status.warnings[-1]
                self.gate_detail.config(text=self.last_warning, fg="#a45d25")
            self.timer_label.config(text=format_duration(status.elapsed_seconds))
            self.system_meter["value"] = max(0, min(100, status.system_peak * 100))
            self.mic_meter["value"] = max(0, min(100, status.microphone_peak * 100))
            if status.state in {"starting", "recording", "stopping"}:
                labels = {"starting": "●  正在准备", "recording": "●  正在录音", "stopping": "●  正在保存"}
                self.status_label.config(text=labels[status.state], fg=RED)
                self.status_detail.config(text=f"保存为  {self.output_path.name if self.output_path else '录音文件'}")
                self.record_button.configure(state="disabled" if status.state == "stopping" else "normal")
            elif status.state in {"stopped", "error"} and self.last_terminal_recorder is not self.recorder:
                self.last_terminal_recorder = self.recorder
                self.record_button.configure(state="normal")
                self._render_busy(False)
                self.system_meter["value"] = self.mic_meter["value"] = 0
                if status.state == "error":
                    self.status_label.config(text="●  录音已中断", fg=RED)
                    self.status_detail.config(text="请检查设备；已写入的录音可能仍可播放。")
                    if not self.closing:
                        messagebox.showerror("录音中断", status.error or "无法继续录音，请检查音频设备。", parent=self.root)
                elif status.file_bytes >= 44:
                    self.status_label.config(text="●  已保存", fg=ACCENT)
                    self.status_detail.config(text=f"已保存  {self.output_path.name if self.output_path else '录音文件'}")
                else:
                    self.status_label.config(text="●  已取消", fg=MUTED)
                    self.status_detail.config(text="录音尚未开始，没有创建文件。")
                self._update_gate()

        if self.closing and not self._is_busy():
            self.root.destroy()
            return
        self.root.after(200, self._tick)

    def _close(self) -> None:
        if self._is_busy():
            if not messagebox.askyesno("结束录音", "结束当前录音并保存，然后关闭窗口？", parent=self.root):
                return
            self.closing = True
            if self.recorder is not None:
                self.recorder.stop(wait=False)
            self.status_label.config(text="●  正在保存", fg=RED)
            return
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    RecorderWindow(root)
    root.mainloop()
