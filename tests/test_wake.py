import time
import unittest
from unittest.mock import MagicMock, Mock, patch

from backend import cast, cast_devices, dlna, read_ahead

LG = "dlna:lg-uuid"
RECORD = dict(uuid=LG, name="[LG] webOS TV", host="192.168.1.13", kind="dlna", mac="10:20:30:40:50:60")


def renderer(uuid=LG, host="192.168.1.13"):
    return dlna.Renderer(uuid, "[LG] webOS TV", host, f"http://{host}:1738/AVTransport/control")


class WakeTests(unittest.TestCase):
    def setUp(self):
        self.sock = MagicMock()
        for target, name, value in ((cast_devices, "known", Mock(return_value=dict(RECORD))),
                                    (cast_devices.socket, "socket", Mock(return_value=self.sock)),
                                    (cast, "adopt", Mock()), (cast, "discover", Mock(side_effect=AssertionError("no full scan")))):
            p = patch.object(target, name, value); p.start(); self.addCleanup(p.stop)
        p = patch("ifaddr.get_adapters", return_value=[]); p.start(); self.addCleanup(p.stop)

    def sent(self):
        return [call.args for call in self.sock.__enter__.return_value.sendto.call_args_list]

    def test_wake_returns_as_soon_as_this_tv_announces_itself(self):
        with patch.object(dlna, "discover", side_effect=[[], [renderer("dlna:other")], [renderer(host="192.168.1.21")]]) as scan:
            started = time.monotonic()
            cast_devices.wake_and_wait(LG, lambda: True, timeout=30)
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(self.sent(), [(cast_devices.magic_packet(RECORD["mac"]), ("255.255.255.255", 9))])
        self.assertTrue(all(call.kwargs["timeout"] <= 2 for call in scan.call_args_list))  # Short, LG-only polls.
        # Found by identity even though waking gave it a new address.
        self.assertEqual(cast.adopt.call_args.args[0].host, "192.168.1.21")

    def test_a_tv_that_never_answers_times_out_with_advice(self):
        with patch.object(dlna, "discover", return_value=[]), self.assertRaises(TimeoutError) as caught:
            cast_devices.wake_and_wait(LG, lambda: True, timeout=0.3)
        self.assertIn("電視", str(caught.exception))
        cast.adopt.assert_not_called()

    def test_chromecasts_are_never_sent_a_magic_packet(self):
        cast_devices.known.return_value = dict(RECORD, uuid="cc", kind="chromecast")
        with patch.object(dlna, "discover") as scan:
            cast_devices.wake_and_wait("cc", lambda: True)
            with self.assertRaisesRegex(RuntimeError, "取消"):
                cast_devices.wake_and_wait("cc", lambda: False)
        scan.assert_not_called()
        self.assertEqual(self.sent(), [])


class AdoptTests(unittest.TestCase):
    def test_adopted_renderer_is_castable_read_ahead_and_remembered(self):
        with patch.object(cast, "_casts", {}), patch.object(read_ahead, "_receivers", set()), \
                patch.object(cast_devices, "remember") as remember:
            cast.adopt(renderer(host="192.168.1.21"))
            self.assertEqual(cast._casts[LG].host, "192.168.1.21")
            self.assertTrue(read_ahead.is_receiver("192.168.1.21"))
        self.assertEqual(remember.call_args.args[0], [dict(uuid=LG, name="[LG] webOS TV", host="192.168.1.21", kind="dlna")])

    def test_only_dlna_tvs_offer_wake(self):
        saved = {}
        with patch.object(cast_devices.db, "get_setting", side_effect=lambda key, default="": saved.get(key, default)), \
                patch.object(cast_devices.db, "set_setting", side_effect=lambda key, value: saved.__setitem__(key, value)), \
                patch.object(cast_devices, "learn_mac", return_value="10:20:30:40:50:60"):
            cast_devices.remember([dict(uuid="lg", name="LG", host="192.168.1.13", kind="dlna"),
                                   dict(uuid="cc", name="客廳", host="192.168.1.7", kind="chromecast")])
            offline = {d["uuid"]: d["can_wake"] for d in cast_devices.remember([])}
        self.assertEqual(offline, {"lg": True, "cc": False})


if __name__ == "__main__":
    unittest.main()
