import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from backend import cast, cast_devices, dlna


def receiver(uuid="tv", *, stopped=False):
    device = Mock(uuid=uuid, cast_info=SimpleNamespace(host="192.168.1.26", friendly_name="Display", model_name="Google Nest Hub"))
    device.socket_client.is_stopped = stopped
    device.socket_client.ident = 1
    device.socket_client.is_alive.return_value = not stopped
    device.socket_client.receiver_controller.update_status.side_effect = lambda callback_function: callback_function(True, {})
    return device


class StandbyTests(unittest.TestCase):
    def setUp(self):
        for name in ("_casts", "_browsers", "_active"):
            p = patch.object(cast, name, {})
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(cast_devices, "remember", side_effect=lambda devices: devices)
        p.start()
        self.addCleanup(p.stop)

    def test_standby_checks_receiver_without_loading_or_requesting_media_status(self):
        device = receiver()
        cast._casts["tv"] = device
        self.assertIs(cast._cast("tv")[1], device)
        device.socket_client.receiver_controller.update_status.assert_called_once()
        device.media_controller.update_status.assert_not_called()
        device.start_app.assert_not_called()
        device.media_controller.play_media.assert_not_called()

    def test_scan_replaces_stopped_thread_and_retains_active_content(self):
        old, new = receiver(stopped=True), receiver()
        cast._casts["tv"] = old
        cast._active["tv"] = "owned-media"
        old_browser, new_browser = Mock(), Mock()
        cast._browsers["tv"] = old_browser
        library = Mock()
        library.get_chromecasts.return_value = ([new], new_browser)
        with patch.object(cast, "_cc", return_value=library), patch.object(dlna, "discover", return_value=[]):
            self.assertIs(cast._cast("tv")[1], new)
        old.wait.assert_not_called()
        old.socket_client.disconnect.assert_called_once()
        old_browser.stop_discovery.assert_called_once()
        self.assertIs(cast._browsers["tv"], new_browser)
        self.assertEqual(cast._active["tv"], "owned-media")

    def test_stale_ready_event_recovers_once_without_replaying(self):
        old, new = receiver(), receiver()
        old.socket_client.receiver_controller.update_status.side_effect = lambda callback_function: callback_function(False, {})
        cast._casts["tv"] = old
        def discover(**_kwargs):
            cast._casts["tv"] = new
        with patch.object(cast, "discover", side_effect=discover) as scan:
            self.assertIs(cast._cast("tv")[1], new)
        scan.assert_called_once()
        old.socket_client.disconnect.assert_called_once()
        new.start_app.assert_not_called()

    def test_reconnect_is_bounded_and_does_not_launch_an_unresponsive_receiver(self):
        old, new = receiver(), receiver()
        for device in (old, new):
            device.socket_client.receiver_controller.update_status.side_effect = lambda callback_function: callback_function(False, {})
        cast._casts["tv"] = old
        with patch.object(cast, "discover", side_effect=lambda **_kw: cast._casts.update(tv=new)) as scan:
            with self.assertRaisesRegex(cast.ReceiverUnavailable, "重新連線失敗"):
                cast._cast("tv")
        scan.assert_called_once()
        self.assertNotIn("tv", cast._casts)
        new.start_app.assert_not_called()

    def test_missing_device_is_discovered_even_with_an_operation_deadline(self):
        device = receiver()
        with patch.object(cast, "discover", side_effect=lambda **_kw: cast._casts.update(tv=device)):
            self.assertIs(cast._cast("tv", time.monotonic() + 10)[1], device)

    def test_expired_operation_does_not_start_discovery(self):
        with patch.object(cast, "discover") as scan, self.assertRaises(TimeoutError):
            cast._cast("tv", time.monotonic() - 1)
        scan.assert_not_called()

    def test_legacy_cast_wake_does_not_require_mac_or_send_wol(self):
        with patch.object(cast_devices, "known", return_value={"kind": "chromecast"}), patch.object(cast_devices.socket, "socket") as sock:
            cast_devices.wake_and_wait("tv", lambda: True)
            with self.assertRaisesRegex(RuntimeError, "取消"):
                cast_devices.wake_and_wait("tv", lambda: False)
        sock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
