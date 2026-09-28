import sqlite3
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from backend import cast, cast_devices, db, main
from backend.models import CastPlayIn, SettingsIn


class CastLocationTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        db._init(self.conn)
        self.addCleanup(self.conn.close)
        for target, name, value in (
            (db, "connect", self.conn),
            (cast_devices, "learn_mac", "10:20:30:40:50:60"),
            (main.lan, "ipv4_lan", ["192.168.1.2"]),
            (cast, "lan_media_origin", "http://192.168.1.2:6970"),
        ):
            mock = patch.object(target, name, return_value=value)
            mock.start()
            self.addCleanup(mock.stop)
        self.tv = dict(uuid="philips", name="PHILIPS TV", host="192.168.1.3", kind="dlna", location="kaohsiung")
        self.other = dict(uuid="lg", name="LG", host="192.168.1.4", kind="dlna")
        cast_devices.remember([self.tv, self.other])
        db.set_setting("lan_tv", "1")

    def devices(self, online=True):
        found = cast_devices.remember([self.tv, self.other] if online else [])
        with patch.object(cast, "discover", return_value=found):
            return main.cast_devices()

    def test_default_hides_only_kaohsiung_and_setting_round_trips(self):
        self.assertFalse(main.get_settings().in_kaohsiung)
        self.assertEqual([d["uuid"] for d in self.devices()["devices"]], ["lg"])
        saved = main.put_settings(SettingsIn(in_kaohsiung=True))
        self.assertTrue(saved.in_kaohsiung)
        self.assertTrue(saved.lan_tv)
        self.assertEqual([d["uuid"] for d in self.devices()["devices"]], ["philips", "lg"])
        main.put_settings(SettingsIn(in_kaohsiung=False))
        self.assertEqual([d["uuid"] for d in self.devices()["devices"]], ["lg"])

    def test_location_survives_rescan_ip_change_and_offline(self):
        discovered = {k: v for k, v in self.tv.items() if k != "location"}
        cast_devices.remember([{**discovered, "host": "192.168.1.8"}])
        record = cast_devices.known("philips")
        self.assertEqual(record["location"], "kaohsiung")
        self.assertEqual(record["host"], "192.168.1.8")
        self.assertEqual(record["mac"], "10:20:30:40:50:60")
        self.assertEqual([d["uuid"] for d in self.devices(False)["devices"]], ["lg"])
        db.set_setting("in_kaohsiung", "1")
        tv = self.devices(False)["devices"][0]
        self.assertFalse(tv["online"])
        self.assertTrue(tv["can_wake"])

    def test_manual_power_survives_rescan_and_only_disables_this_receiver_wake(self):
        cast_devices.remember([{**self.tv, "manual_power_on": True}])
        db.set_setting("in_kaohsiung", "1")
        found = self.devices()["devices"]
        self.assertTrue(found[0]["manual_power_on"])
        self.assertTrue(found[0]["online"])
        self.assertTrue(found[0]["mac"])
        self.assertFalse(found[0]["can_wake"])
        self.assertTrue(found[1]["can_wake"])
        offline = self.devices(False)["devices"][0]
        self.assertTrue(offline["manual_power_on"])
        self.assertFalse(offline["can_wake"])

    def test_manual_power_rejects_stale_wake_request_without_network_activity(self):
        cast_devices.remember([{**self.tv, "manual_power_on": True}])
        with patch.object(cast_devices.socket, "socket") as socket, patch.object(cast, "discover") as discover:
            with self.assertRaisesRegex(ValueError, "需手動開機"):
                cast_devices.wake_and_wait("philips", lambda: True)
            socket.assert_not_called()
            discover.assert_not_called()

    def test_hidden_target_cannot_be_selected_or_used_from_stale_page(self):
        db.set_setting("cast_uuid", "philips")
        self.assertEqual(self.devices()["selected"], "")
        with self.assertRaisesRegex(RuntimeError, "我在高雄"):
            cast.select("philips")
        for managed, uuid in ((False, "philips"), (True, "philips"), (False, ""), (True, "")):
            with self.subTest(managed=managed, uuid=uuid), patch.object(main.cast_session, "start") as start, patch.object(cast, "play") as play:
                with self.assertRaises(HTTPException) as error:
                    main.cast_play(CastPlayIn(uuid=uuid, managed=managed))
                self.assertEqual(error.exception.status_code, 400)
                self.assertIn("我在高雄", error.exception.detail)
                start.assert_not_called()
                play.assert_not_called()

    def test_enable_restores_saved_selection_and_other_devices_stay_selectable(self):
        db.set_setting("cast_uuid", "philips")
        db.set_setting("in_kaohsiung", "1")
        self.assertEqual(cast.selected_uuid(), "philips")
        cast.select("philips")
        db.set_setting("in_kaohsiung", "0")
        cast.select("lg")
        self.assertEqual(cast.selected_uuid(), "lg")

    def test_hiding_does_not_cancel_existing_playback(self):
        db.set_setting("in_kaohsiung", "1")
        cast.select("philips")
        with patch.object(cast, "_active", {"philips": "media"}), patch.object(main.cast_session, "cancel") as cancel:
            main.put_settings(SettingsIn(in_kaohsiung=False))
            cancel.assert_not_called()
            self.assertEqual(cast._active, {"philips": "media"})
            self.assertEqual(db.get_setting("cast_uuid"), "philips")
        with patch.object(main.cast_session, "get", return_value={"phase": "playing"}) as get:
            self.assertEqual(main.get_cast_session(), {"phase": "playing"})
            get.assert_called_once_with("philips")


if __name__ == "__main__":
    unittest.main()
