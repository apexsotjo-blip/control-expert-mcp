# Building the DDT Mirror installer

Produces `DDTMirror-Setup-<version>.exe`: a single file that installs the
**web UI** (no Python, no admin rights, no dependencies) on any Windows PC
with a Desktop + Start Menu shortcut and a normal uninstaller listed in
Add/Remove Programs.

The installer is per-user (`%LOCALAPPDATA%\Programs\DDTMirror`), so it
works on a locked-down engineering laptop with no admin rights. It does
**not** bundle Control Expert or the Qt desktop UI — the web UI's PLC/RTU
generate steps still need Control Expert installed on the machine that
runs them, same as always.

## One-time setup

Portable NSIS is not committed to the repo (`installer/tools/` is
gitignored — it's a ~2MB third-party binary). Fetch it once:

```powershell
Invoke-WebRequest `
  "https://sourceforge.net/projects/nsis/files/NSIS%203/3.10/nsis-3.10.zip/download" `
  -OutFile nsis.zip   # SourceForge's redirect confuses curl less than IWR;
                      # if IWR gives you an HTML page instead of a zip, use
                      # `curl.exe -L -o nsis.zip <url>` instead
Expand-Archive nsis.zip -DestinationPath nsis_tmp
Copy-Item nsis_tmp\nsis-3.10\* -Destination installer\tools\nsis -Recurse
```

## Build

From `New Project Build`:

```powershell
# 1. Bundle the app (PyInstaller) - onedir, windowless, ~40 MB unpacked
.venv\Scripts\pyinstaller installer\ddtmirror.spec --noconfirm `
    --distpath installer\dist --workpath installer\build

# 2. Compile the installer (NSIS) - reads the version from pyproject.toml
installer\tools\nsis\makensis.exe /DVERSION=2.3.0 installer\ddtmirror.nsi
```

Output: `installer\DDTMirror-Setup-<version>.exe` (~16 MB, not committed —
copy it wherever it needs to go: a shared drive, email, a USB stick).

## What's in here

- `launcher.py` — the frozen entry point. Starts the FastAPI server on
  `127.0.0.1:8177` and opens the default browser. Logs to
  `%LOCALAPPDATA%\DdtMirror\launcher.log` (there's no console in a
  `--windowed` build, so this is the only place errors show up). If the
  app is already running, a second launch just opens another browser tab
  instead of failing to bind the port.
- `ddtmirror.spec` — PyInstaller spec. Bundles `ddt_mirror` (this repo)
  and `control_expert_mcp` (the parent repo, editable-installed) plus the
  static frontend files. Deliberately excludes PySide6 — the web server
  never imports `gui/`, so leaving Qt out keeps the build small and fast.
- `ddtmirror.nsi` — NSIS script: copies the PyInstaller output to
  `%LOCALAPPDATA%\Programs\DDTMirror`, creates the shortcuts, writes an
  uninstaller and an HKCU Add/Remove Programs entry (HKCU, not HKLM, so
  no admin prompt).

## Verifying a build works

```powershell
installer\dist\DDTMirror\DDTMirror.exe        # should open a browser tab
# or, for the installer itself:
installer\DDTMirror-Setup-<version>.exe /S    # silent install
installer\DDTMirror-Setup-<version>.exe       # normal install with UI
```

Silent uninstall: `"%LOCALAPPDATA%\Programs\DDTMirror\Uninstall.exe" /S`.

## Known gaps

- No custom icon yet (uses PyInstaller's default). Add one with
  `--icon path\to\icon.ico` in the spec's `EXE(...)` call and NSIS's
  `Icon "path\to\icon.ico"` directive.
- Desktop (Qt) UI isn't packaged by this tooling — it's a separate, less
  polished UI the team has moved away from in favor of the web UI. Add
  `gui/app.py` as a second PyInstaller entry point if it's ever needed.
- Not code-signed, so Windows SmartScreen may warn on first run on
  another PC ("Windows protected your PC" → More info → Run anyway).
  That's expected for an unsigned internal tool; a code-signing
  certificate would remove it if this ever leaves the team.
