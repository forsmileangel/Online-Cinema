import unittest
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

from backend import main, tv_session

LAN = "192.168.1.50"


def request(client=LAN, cookie=""):
    headers = [(b"cookie", cookie.encode())] if cookie else []
    return Request(dict(type="http", method="GET", path="/api/tv/status", scheme="http", query_string=b"",
                        server=("192.168.1.2", 6969), client=(client, 5000), headers=headers))


class PairingTests(unittest.TestCase):
    def setUp(self):
        values = {"tv_code": "ABC234", "tv_secret": "secret"}
        for target, name, value in ((main.db, "get_setting", lambda key, default="": values.get(key, default)),
                                    (main.db, "set_setting", lambda key, value: values.__setitem__(key, value)),
                                    (tv_session.db, "get_setting", lambda key, default="": values.get(key, default)),
                                    (tv_session.db, "set_setting", lambda key, value: values.__setitem__(key, value)),
                                    (tv_session, "_failures", {})):
            p = patch.object(target, name, value); p.start(); self.addCleanup(p.stop)

    def cookies(self):
        token = tv_session.cookie_token()
        return f"midnight_tv={token}; cinema_tv={token}"

    def test_status_tells_a_lan_device_whether_it_needs_the_code(self):
        self.assertEqual(main.tv_status(request()), {"paired": False})
        self.assertEqual(main.tv_status(request(cookie="midnight_tv=wrong; cinema_tv=wrong")), {"paired": False})
        self.assertEqual(main.tv_status(request(cookie=self.cookies())), {"paired": True})
        self.assertEqual(main.tv_status(request("127.0.0.1")), {"paired": True})  # This PC never needs it.

    def test_status_is_reachable_before_pairing_but_other_api_calls_are_not(self):
        import asyncio

        async def call(path):
            seen = []
            async def next_(req):
                seen.append(path)
                from starlette.responses import JSONResponse
                return JSONResponse({"ok": True})
            scope = dict(type="http", method="GET", path=path, scheme="http", query_string=b"", server=("192.168.1.2", 6969),
                         client=(LAN, 5000), headers=[])
            with patch.object(main.db, "get_setting", lambda key, default="": "1" if key == "lan_tv" else default), \
                    patch.object(main.lan, "is_allowed_client", return_value=True):
                return await main.AccessGuard(main.app).dispatch(Request(scope), next_)
        self.assertEqual(asyncio.run(call("/api/tv/status")).status_code, 200)
        self.assertEqual(asyncio.run(call("/api/tv/pair")).status_code, 200)
        self.assertEqual(asyncio.run(call("/api/home")).status_code, 401)
        self.assertEqual(asyncio.run(call("/api/cast/devices")).status_code, 401)  # casting is not open to unpaired devices
        self.assertEqual(asyncio.run(call("/api/hls")).status_code, 200)  # the TV itself fetches media without a cookie

    def test_typed_code_pairs_with_any_case_and_spacing(self):
        for typed in ("ABC234", "abc234", " abc 234 "):
            response = main.tv_pair(main.TvPairIn(code=typed), request())
            self.assertEqual(response.status_code, 200)
            self.assertIn("httponly", response.headers["set-cookie"].lower())

    def test_wrong_codes_are_slowed_down_per_device(self):
        for _ in range(tv_session.ATTEMPT_LIMIT):
            with self.assertRaises(HTTPException) as caught:
                main.tv_pair(main.TvPairIn(code="ZZZZZZ"), request())
            self.assertEqual(caught.exception.status_code, 403)
        with self.assertRaises(HTTPException) as caught:
            main.tv_pair(main.TvPairIn(code="ABC234"), request())  # Even the right one waits.
        self.assertEqual(caught.exception.status_code, 429)
        self.assertGreater(int(caught.exception.headers["Retry-After"]), 0)
        # Another device is not affected, and the first may try again once the window passes.
        self.assertEqual(main.tv_pair(main.TvPairIn(code="ABC234"), request("192.168.1.51")).status_code, 200)
        with patch.object(tv_session.time, "monotonic", return_value=tv_session.time.monotonic() + tv_session.ATTEMPT_WINDOW + 1):
            self.assertEqual(main.tv_pair(main.TvPairIn(code="ABC234"), request()).status_code, 200)


if __name__ == "__main__":
    unittest.main()
