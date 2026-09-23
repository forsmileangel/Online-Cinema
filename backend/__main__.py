from __future__ import annotations

import threading
import argparse
import time
import webbrowser

import uvicorn

from .settings import PORT


def _open():
    time.sleep(0.9)
    webbrowser.open(f"http://127.0.0.1:{PORT}")


def main() -> None:
    from . import db

    parser = argparse.ArgumentParser()
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    host = "0.0.0.0" if db.get_setting("lan_tv", "") == "1" else "127.0.0.1"
    if not args.no_browser:
        threading.Thread(target=_open, daemon=True).start()
    uvicorn.run(
        "backend.main:app",
        host=host,
        port=PORT,
        reload=False,
        log_level="info",
        proxy_headers=False,
        forwarded_allow_ips="",
    )


if __name__ == "__main__":
    main()
