import unittest
from unittest.mock import patch

from backend import __main__ as startup
from backend import db


class StartupTests(unittest.TestCase):
    def test_background_launch_honors_saved_lan_setting_without_opening_browser(self):
        for enabled, host in (("1", "0.0.0.0"), ("", "127.0.0.1")):
            with self.subTest(enabled=enabled), patch("sys.argv", ["backend", "--no-browser"]), patch.object(db, "get_setting", return_value=enabled), patch.object(startup.uvicorn, "run") as run, patch.object(startup.threading, "Thread") as thread:
                startup.main()
                self.assertEqual(run.call_args.kwargs["host"], host)
                self.assertFalse(run.call_args.kwargs["proxy_headers"])
                thread.assert_not_called()


if __name__ == "__main__":
    unittest.main()
