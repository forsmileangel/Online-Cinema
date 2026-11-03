"""Shared set-up for the headless checks: the server to test and the skip rule."""
import asyncio
import os
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def default_base() -> str:
    port = re.search(r"^PORT\s*=\s*(\d+)", (ROOT / "backend" / "settings.py").read_text(encoding="utf-8"), re.M)
    return f"http://127.0.0.1:{port.group(1) if port else 6969}"


ENABLED = os.environ.get("CINEMA_E2E") == "1"
BASE = os.environ.get("CINEMA_BASE") or default_base()
WATCH = os.environ.get("CINEMA_WATCH", "")
# Grown-up titles stay off the terminal: only element kinds and numbers are printed.
ADULT = "6969" in BASE


@unittest.skipUnless(ENABLED, "set CINEMA_E2E=1 to run the headless browser checks")
class E2ETest(unittest.TestCase):
    base = BASE

    def run_async(self, coroutine):
        return asyncio.run(coroutine)

    async def open(self, browser, path, settle=2.5):
        await browser.goto("/", 1.5)
        await browser.ev("localStorage.setItem('midnight.ageOk','1'); localStorage.setItem('midnight.autoplay.next','0');"
                         "localStorage.setItem('cinema.autoplay.next','0'); true")
        await browser.goto(path, settle)
