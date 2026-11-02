"""Server logging: timestamps, a size-capped file, and access lines without noise or secrets.

Everything goes to server-logs/app.log (5 MB, 5 kept). The console (which the background runner
redirects to stdout.log) gets the same minus access lines, so it stays small. Access lines for polling
and media requests are dropped when they succeed, and query values that carry a pairing code, a search
or an upstream URL are blanked.
"""
from __future__ import annotations

import logging
import logging.handlers
import re
import sys
from pathlib import Path

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
DATE = "%Y-%m-%d %H:%M:%S"
MAX_BYTES = 5 * 1024 * 1024
KEEP = 5
# Successful requests to these are routine (a page polls the cast session every few seconds; every
# segment and cover is one line) and say nothing worth keeping.
QUIET = ("/api/cast/session", "/api/health", "/api/hls", "/api/img", "/api/offline/media/", "/assets")
SECRET = re.compile(r"([?&](?:c|q|u|code|token)=)[^&#\s]*")


def redact(path: str) -> str:
    return SECRET.sub(r"\1…", path)


def quiet(path: str) -> bool:
    """The path is one of QUIET or below it ("/api/health?x" yes, "/api/healthz" no)."""
    for prefix in QUIET:
        base = prefix.rstrip("/")
        if path == base or path.startswith(base + "/") or path.startswith(base + "?"):
            return True
    return False


class AccessFilter(logging.Filter):
    """uvicorn's access record: args = (client, method, path, http version, status)."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not (isinstance(args, tuple) and len(args) == 5):
            return True
        client, method, path, version, status = args
        path = str(path)
        try:
            routine = 200 <= int(status) < 400
        except (TypeError, ValueError):
            routine = False
        if routine and quiet(path):
            return False
        record.args = (client, method, redact(path), version, status)
        return True


class NoAccess(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.name != "uvicorn.access"


def configure(log_dir: Path, console=None) -> logging.Logger:
    """Set up the root logger; uvicorn must then run with log_config=None so its records come here."""
    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(FORMAT, DATE)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in list(root.handlers):
        root.removeHandler(handler)
    file = logging.handlers.RotatingFileHandler(log_dir / "app.log", maxBytes=MAX_BYTES, backupCount=KEEP, encoding="utf-8")
    file.setFormatter(formatter)
    root.addHandler(file)
    stream = logging.StreamHandler(console or sys.stdout)
    stream.setFormatter(formatter)
    stream.addFilter(NoAccess())
    root.addHandler(stream)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
        logger.setLevel(logging.INFO)
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, AccessFilter) for f in access.filters):
        access.addFilter(AccessFilter())
    return root
