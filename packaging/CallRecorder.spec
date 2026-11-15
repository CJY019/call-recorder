# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

repo = Path(SPECPATH).parent
build = repo / "packaging" / "_build"
pa_datas, pa_binaries, pa_hidden = collect_all("pyaudiowpatch")
datas = pa_datas + [
    (str(repo / "call_recorder" / "recorder.ico"), "call_recorder"),
    (str(build / "licenses"), "licenses"),
    (str(build / "build-info.json"), "."),
]

a = Analysis(
    [str(repo / "packaging" / "launcher.py")],
    pathex=[str(repo)],
    binaries=pa_binaries,
    datas=datas,
    hiddenimports=pa_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter.test", "numpy.tests"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CallRecorder",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=str(repo / "call_recorder" / "recorder.ico"),
    version=str(build / "version-info.txt"),
    uac_admin=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="CallRecorder")
