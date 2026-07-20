"""Frozen-app entry point for the DDT Mirror web UI.

Runs windowless (no console) once packaged. Logs to
%LOCALAPPDATA%\\DdtMirror\\launcher.log so a crash before the browser
opens is still diagnosable. If the app is already running (user
double-clicked the shortcut twice), just focuses a new browser tab
instead of failing to bind the port.
"""

from __future__ import annotations

import logging
import os
import socket
import sys
import threading
import webbrowser

PORT = 8177
LOG_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                       "DdtMirror")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "launcher.log")
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("launcher")

# A --windowed PyInstaller build has no console: sys.stdout/stderr are None,
# which crashes anything that probes them (uvicorn's default color
# formatter calls stderr.isatty()). Give every downstream library a real,
# file-backed stream instead of patching each one individually.
if sys.stdout is None or sys.stderr is None:
    _stream = open(LOG_FILE, "a", buffering=1, encoding="utf-8")
    sys.stdout = sys.stderr = _stream


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex(("127.0.0.1", port)) == 0


def main() -> None:
    url = f"http://127.0.0.1:{PORT}"
    if _port_in_use(PORT):
        log.info("Already running on %s - opening a new tab only.", url)
        webbrowser.open(url)
        return

    log.info("Starting DDT Mirror on %s (frozen=%s)",
             url, getattr(sys, "frozen", False))
    try:
        import uvicorn

        from ddt_mirror.web.server import app
    except Exception:
        log.exception("Failed to import the app")
        raise

    threading.Timer(1.3, webbrowser.open, args=(url,)).start()
    try:
        # log_config=None: skip uvicorn's own dictConfig (its formatters
        # assume a real, isatty()-capable stream) and let our logging
        # module + the sys.stdout/stderr shim above handle everything.
        uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning",
                   log_config=None)
    except Exception:
        log.exception("Server crashed")
        raise


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log.exception("Fatal error in launcher")
        # keep a window open long enough to see the error if run from a
        # console during debugging; frozen --windowed builds have none.
        if sys.stdout is not None:
            input("DDT Mirror failed to start - see launcher.log. Press Enter.")
