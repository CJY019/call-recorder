Build with 64-bit PowerShell on Windows 10 1809 or newer:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build.ps1 -Version 0.1.0 -SmokeTest
```

The script downloads the official CPython 3.13.17 x64 and Inno Setup 6.7.3
installers, checks their pinned SHA256 and Authenticode publisher signatures,
and prepares the build tools in `packaging/_build/`. Inno Setup uses its official
portable mode. The CPython installer temporarily registers the private runtime
for the current user; the script refuses to replace any existing per-user Python
3.13 and uses the official uninstaller in `finally` to remove its own temporary
registration. Existing machine Python registrations are checked before and
after. It never adds Python to PATH, installs the Python launcher, creates
associations or needs administrator rights. Downloads, the private virtual environment and complete diagnostics
remain under `packaging/_build/`. All wheels, including pip and transitive build
dependencies, are locked by version and SHA256 in `requirements-build.txt`.

No pre-existing Python, Inno Setup, cached download or locally configured pip
index is required. If a different per-user Python 3.13 is already registered,
use a clean Windows runner to preserve it. Internet access to python.org, the official jrsoftware GitHub
release and PyPI is required for a fresh build. Authenticode validation also
requires Windows to trust the publishers' certificate chains.

The script validates the exact environment, collects complete package licenses
(including NumPy's nested licenses), runs synthetic unit tests, builds a GUI
application with PyInstaller, verifies native resources and all license hashes,
then compiles the installer. The checked-in license supplements retain the
PyAudio MIT notice, full Apache 2.0 text, PortAudio and Tcl terms that are not
fully present in the runtime wheels/installation. Their source URLs and SHA256
are in `licenses/SOURCES.json`. The original Chinese translation is copied from
the official Inno Setup `is-6_7_3` source release and retains its maintainer
attribution; `inno-license.txt` supplies that distribution's license.

Outputs are `dist/CallRecorder-Setup-0.1.0-x64.exe`, `dist/SHA256SUMS.txt`, and
the standalone application folder `dist/CallRecorder/`. End users need only
the installer. Build information is embedded in `_internal/build-info.json`;
the full third-party license inventory is `_internal/licenses/MANIFEST.json`.
These lock inputs and validate their contents; no claim of identical executable
bytes across different Windows builds or signing runs is made.

`-SmokeTest` installs the actual setup into a new isolated
`packaging/_build/smoke-*/installed-app` folder with its own uninstall registry
entry and settings directory. It checks a visible Tk window, icon, NumPy and
PortAudio DLL loading with Python removed from PATH and an empty working
directory. Synthetic devices are injected and audio stream opening/recording is
explicitly blocked. The runner closes the GUI, uninstalls, and verifies that
existing settings, recording sentinels and unrelated files in the installation
directory are retained. Start Menu/desktop shortcuts are disabled only for this
isolated verification. Detailed results are retained in
`packaging/_build/smoke-result.json` and the named sandbox's installation logs.

The application installer defaults to `%LOCALAPPDATA%\Programs\CallRecorder`,
creates a Start Menu shortcut, offers an optional desktop shortcut, and keeps
recordings/settings/logs on uninstall. Neither the application nor setup is
code signed by this project. Python, PyInstaller and Inno Setup upstream
signatures do not sign the resulting application. A publisher certificate can
be used in a separate authorized release process; Windows may otherwise show
an unknown publisher warning for the downloaded setup.

The bundled third-party components retain their own licenses. In particular,
PyInstaller's GPL license contains its bootloader exception, Apache 2.0 requires
preservation of applicable notices, and Python's license explains conditions
for the Microsoft runtime DLLs. Do not remove `LICENSE`,
`THIRD_PARTY_NOTICES.md`, or `_internal/licenses/` when redistributing the app.
