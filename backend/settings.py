from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "OnlineCinema"
HOST = "127.0.0.1"
PORT = 6970

IMPERSONATE = "safari17_2_ios"

CDN_HOSTS = (
    "bfllvip.com",
    "ppqrrs.com",
    "fengbao8.com",
    "fengbao9.com",
    "fengbao10.com",
    "fengbao11.com",
    "fengbao12.com",
    "baofeng8.com",
    "baofeng9.com",
    "baofeng10.com",
    "baofeng11.com",
    "baofeng12.com",
    "10cong.com",
    "wangwangzyvod.com",
)

IMAGE_HOSTS = (
    "picbf.com",
    "wangwangzyimg.com",
    "hongguoapp.cn",
    "chinaq.fun",
    "1777cdn.com",
    "gimyai.tw",
    "dramasq.io",
)


def app_dir() -> Path:
    base = os.environ.get("APPDATA")
    root = Path(base) / APP_NAME if base else Path.home() / f".{APP_NAME.lower()}"
    root.mkdir(parents=True, exist_ok=True)
    return root


def db_path() -> Path:
    return app_dir() / "app.sqlite"
