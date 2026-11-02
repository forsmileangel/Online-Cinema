import asyncio
import json
import unittest
from unittest.mock import patch

from backend import host_guard
from backend import settings as S

PORT = S.PORT
LAN_IP = "192.168.1.2"


def call(method="GET", host=f"127.0.0.1:{PORT}", origin=None, site=None, path="/api/home"):
    """Run HostGuard around a stub app; returns (status, inner app reached)."""
    reached = []

    async def inner(scope, receive, send):
        reached.append(scope["path"])
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": b'{"ok": true}'})

    headers = []
    if host is not None:
        headers.append((b"host", host.encode()))
    if origin is not None:
        headers.append((b"origin", origin.encode()))
    if site is not None:
        headers.append((b"sec-fetch-site", site.encode()))
    scope = dict(type="http", method=method, path=path, scheme="http", query_string=b"", server=("127.0.0.1", PORT),
                 client=("127.0.0.1", 5000), headers=headers)
    messages = []

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        messages.append(message)

    asyncio.run(host_guard.HostGuard(inner)(scope, receive, send))
    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return status, bool(reached), json.loads(body or b"{}")


class HostGuardTests(unittest.TestCase):
    def setUp(self):
        host_guard.reset()
        self.addCleanup(host_guard.reset)
        for target, name, value in ((host_guard.lan, "ipv4_lan", lambda: [LAN_IP]),
                                    (host_guard.socket, "gethostname", lambda: "Desktop-PC"),
                                    (host_guard.os, "environ", {})):
            p = patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)

    def test_our_own_names_pass_with_or_without_the_port(self):
        for host in (f"127.0.0.1:{PORT}", f"localhost:{PORT}", f"LOCALHOST:{PORT}", f"{LAN_IP}:{PORT}", LAN_IP,
                     f"[::1]:{PORT}", f"desktop-pc:{PORT}", f"desktop-pc.local:{PORT}", ""):
            with self.subTest(host=host):
                self.assertEqual(call(host=host)[:2], (200, True))
        self.assertEqual(call(host=None)[:2], (200, True))  # HTTP/1.0 renderers send no Host at all

    def test_another_name_or_port_is_refused_before_the_app_runs(self):
        for host in (f"evil.example:{PORT}", "evil.example", f"127.0.0.1:{PORT + 1}", f"{LAN_IP}:80", "192.168.1.99",
                     f"127.0.0.1.evil.example:{PORT}", "[::2]"):
            with self.subTest(host=host):
                status, reached, body = call(host=host)
                self.assertEqual((status, reached, body), (400, False, {"detail": "拒絕"}))

    def test_cinema_hosts_adds_names_you_use_yourself(self):
        with patch.object(host_guard.os, "environ", {"CINEMA_HOSTS": " media-pc.home , nas.lan"}):
            host_guard.reset()
            self.assertEqual(call(host=f"media-pc.home:{PORT}")[:2], (200, True))
            self.assertEqual(call(host=f"nas.lan:{PORT}")[:2], (200, True))
            self.assertEqual(call(host=f"other.lan:{PORT}")[0], 400)

    def test_a_new_lan_address_is_picked_up_after_a_minute_without_holding_requests(self):
        self.assertEqual(call(host=f"{LAN_IP}:{PORT}")[0], 200)
        with patch.object(host_guard.lan, "ipv4_lan", lambda: ["10.0.0.7"]):
            self.assertEqual(call(host=f"10.0.0.7:{PORT}")[0], 400)  # still the cached list
            with patch.object(host_guard.time, "monotonic", return_value=host_guard.time.monotonic() + host_guard.TTL + 1):
                self.assertEqual(call(host=f"{LAN_IP}:{PORT}")[0], 200)  # answered from the old list at once
                host_guard.join_refresh()
            self.assertEqual(call(host=f"10.0.0.7:{PORT}")[0], 200)
            self.assertEqual(call(host=f"{LAN_IP}:{PORT}")[0], 400)

    def test_start_up_lookup_fills_the_list_before_the_first_request(self):
        self.assertIn(LAN_IP, host_guard.refresh())
        with patch.object(host_guard.lan, "ipv4_lan", side_effect=AssertionError("looked up on a request")):
            self.assertEqual(call(host=f"{LAN_IP}:{PORT}")[0], 200)

    def test_posts_from_our_own_pages_and_from_non_browsers_pass(self):
        for origin in (None, f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}", f"http://{LAN_IP}:{PORT}", f"http://desktop-pc:{PORT}"):
            with self.subTest(origin=origin):
                self.assertEqual(call("POST", origin=origin)[:2], (200, True))
                self.assertEqual(call("PUT", origin=origin, site="same-origin")[:2], (200, True))
                self.assertEqual(call("DELETE", origin=origin)[:2], (200, True))

    def test_posts_from_another_site_are_refused(self):
        for origin in ("http://evil.example", "null", f"https://{LAN_IP}:{PORT}", f"http://{LAN_IP}", f"http://{LAN_IP}:80",
                       f"http://127.0.0.1:{PORT + 1}", "http://localhost:5173"):
            with self.subTest(origin=origin):
                self.assertEqual(call("POST", origin=origin)[:2], (403, False))
        self.assertEqual(call("POST", site="cross-site")[:2], (403, False))
        self.assertEqual(call("PUT", origin="http://evil.example")[:2], (403, False))

    def test_reads_and_preflights_from_elsewhere_are_left_to_cors(self):
        self.assertEqual(call("GET", origin="http://evil.example")[:2], (200, True))
        self.assertEqual(call("OPTIONS", origin="https://www.gstatic.com", path="/api/hls")[:2], (200, True))

    def test_the_vite_dev_server_counts_as_ours_only_in_dev_mode(self):
        self.assertEqual(call("POST", origin="http://localhost:5173")[0], 403)
        self.assertEqual(host_guard.allowed_origins(), [f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"])
        with patch.object(host_guard.os, "environ", {"CINEMA_DEV": "1"}):
            self.assertEqual(call("POST", origin="http://localhost:5173")[0], 200)
            self.assertIn("http://localhost:5173", host_guard.allowed_origins())

    def test_split_host(self):
        self.assertEqual(host_guard.split_host("192.168.1.2:6969"), ("192.168.1.2", 6969))
        self.assertEqual(host_guard.split_host("[::1]:6969"), ("::1", 6969))
        self.assertEqual(host_guard.split_host("[::1]"), ("::1", None))
        self.assertEqual(host_guard.split_host("LocalHost"), ("localhost", None))
        self.assertEqual(host_guard.split_host("host:abc"), ("host:abc", None))


if __name__ == "__main__":
    unittest.main()
