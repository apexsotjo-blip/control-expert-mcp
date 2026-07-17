"""Run the DDT Mirror web UI:  python -m ddt_mirror.web  [--port 8177]"""

from __future__ import annotations

import argparse
import threading
import webbrowser

import uvicorn


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8177)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    url = f"http://127.0.0.1:{args.port}"
    if not args.no_browser:
        threading.Timer(1.2, webbrowser.open, args=(url,)).start()
    print(f"DDT Mirror web UI on {url}  (Ctrl+C to stop)")
    uvicorn.run("ddt_mirror.web.server:app", host="127.0.0.1",
                port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
