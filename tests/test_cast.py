import unittest
import threading
from types import SimpleNamespace
from unittest.mock import Mock, patch

from backend import cast, dlna


def state(**overrides):
    return {"uuid": "test", "content_id": "http://lan/video", "playing": False, "paused": False,
            "idle": False, "buffering": False, "idle_reason": "", "current_time": 0,
            "duration": 120, **overrides}


class CastTests(unittest.TestCase):
    def setUp(self):
        registry = patch("backend.cast_devices.remember", side_effect=lambda devices: devices)
        registry.start()
        self.addCleanup(registry.stop)
        for name, value in (("_casts", {}), ("_active", {}), ("_browsers", {})):
            p = patch.object(cast, name, value)
            p.start()
            self.addCleanup(p.stop)

    def test_discovery_lists_all_devices_without_saving_an_arbitrary_target(self):
        cc = Mock(uuid="cc", cast_info=SimpleNamespace(host="192.168.1.4", friendly_name="Living room"))
        tv = dlna.Renderer("dlna:lg", "LG", "192.168.1.11", "http://192.168.1.11/control")
        library = Mock()
        library.get_chromecasts.return_value = ([cc], Mock())
        with patch.object(cast, "_cc", return_value=library), patch.object(dlna, "discover", return_value=[tv]), patch.object(cast.db, "set_setting") as save:
            devices = cast.discover(.01)
        self.assertEqual([d["uuid"] for d in devices], ["cc", "dlna:lg"])
        library.get_listed_chromecasts.assert_not_called()
        save.assert_not_called()

    def test_scan_preserves_an_active_chromecast_connection(self):
        old = Mock(uuid="cc", cast_info=SimpleNamespace(host="192.168.1.4", friendly_name="Living room"))
        new = Mock(uuid="cc")
        cast._casts["cc"] = old
        cast._active["cc"] = "http://lan/video"
        library = Mock()
        library.get_chromecasts.return_value = ([new], Mock())
        with patch.object(cast, "_cc", return_value=library), patch.object(dlna, "discover", return_value=[]):
            cast.discover(.01)
        self.assertIs(cast._casts["cc"], old)
        old.socket_client.disconnect.assert_not_called()
        new.socket_client.disconnect.assert_called_once()

    def test_socket_writes_are_serialized_including_background_sends(self):
        entered, release, second_started, second_sent = (threading.Event() for _ in range(4))
        def send(message):
            if message == "first":
                entered.set()
                release.wait(2)
            else:
                second_sent.set()
        device = SimpleNamespace(socket_client=SimpleNamespace(send_message=send))
        cast._serialize_cast_writes(device)
        first = threading.Thread(target=device.socket_client.send_message, args=("first",))
        def background():
            second_started.set()
            device.socket_client.send_message("heartbeat")
        second = threading.Thread(target=background)
        first.start()
        try:
            self.assertTrue(entered.wait(1))
            second.start()
            self.assertTrue(second_started.wait(1))
            self.assertFalse(second_sent.wait(.02))
        finally:
            release.set()
            first.join(2)
            if second.ident is not None:
                second.join(2)
        self.assertTrue(second_sent.is_set())

    def test_socket_write_wrapper_is_reentrant_and_installed_once(self):
        messages = []
        client = SimpleNamespace()
        def send(message):
            messages.append(message)
            if message == "app":
                client.send_message("channel")
        client.send_message = send
        device = SimpleNamespace(socket_client=client)
        cast._serialize_cast_writes(device)
        wrapper = client.send_message
        cast._serialize_cast_writes(device)
        self.assertIs(client.send_message, wrapper)
        client.send_message("app")
        self.assertEqual(messages, ["app", "channel"])

    def test_rescan_keeps_the_discovery_service_needed_for_reconnection(self):
        old = Mock(uuid="cc", cast_info=SimpleNamespace(host="192.168.1.4", friendly_name="Living room"))
        old_browser, new_browser = Mock(), Mock()
        cast._casts["cc"] = old
        cast._browsers["cc"] = old_browser
        library = Mock()
        library.get_chromecasts.return_value = ([Mock(uuid="cc")], new_browser)
        with patch.object(cast, "_cc", return_value=library), patch.object(dlna, "discover", return_value=[]):
            cast.discover(.01)
        self.assertIs(cast._browsers["cc"], old_browser)
        old_browser.stop_discovery.assert_not_called()
        new_browser.stop_discovery.assert_called_once()

    def test_unused_discovery_service_is_closed_after_last_receiver_is_removed(self):
        old, old_browser, new_browser = Mock(), Mock(), Mock()
        cast._casts["cc"] = old
        cast._browsers["cc"] = old_browser
        library = Mock()
        library.get_chromecasts.return_value = ([], new_browser)
        with patch.object(cast, "_cc", return_value=library), patch.object(dlna, "discover", return_value=[]):
            self.assertEqual(cast.discover(.01), [])
        self.assertEqual(cast._browsers, {})
        old_browser.stop_discovery.assert_called_once()
        new_browser.stop_discovery.assert_called_once()

    def test_cold_idle_and_buffering_are_not_success(self):
        reports = [state(idle=True), state(buffering=True), state(playing=True)]
        with patch.object(cast, "_fresh_status", side_effect=reports) as report, patch.object(cast.time, "sleep"):
            result = cast._confirm("test", Mock(), "http://lan/video", "play")
        self.assertTrue(result["playing"])
        self.assertEqual(report.call_count, 3)

    def test_other_content_never_confirms_success(self):
        with patch.object(cast, "_fresh_status", return_value=state(playing=True, content_id="other")), patch.object(cast, "CONFIRM_TIMEOUT", .01), patch.object(cast.time, "sleep"):
            with self.assertRaisesRegex(TimeoutError, "確認"):
                cast._confirm("test", Mock(), "http://lan/video", "play")

    def test_receiver_error_is_not_swallowed(self):
        with patch.object(cast, "_fresh_status", return_value=state(idle=True, idle_reason="ERROR")):
            with self.assertRaisesRegex(RuntimeError, "無法播放"):
                cast._confirm("test", Mock(), "http://lan/video", "play")

    def test_cold_receiver_can_become_available_after_load(self):
        with patch.object(cast, "_fresh_status", side_effect=[cast.ReceiverUnavailable("connecting"), state(playing=True)]), patch.object(cast.time, "sleep"):
            self.assertTrue(cast._confirm("test", Mock(), "http://lan/video", "play")["playing"])

    def test_pychromecast_buffering_flag_is_not_playing(self):
        device = Mock()
        device.media_controller.status = SimpleNamespace(player_state="BUFFERING", player_is_playing=True,
            content_id="http://lan/video", idle_reason="", current_time=0, duration=120, title="Test")
        result = cast._status("test", device)
        self.assertFalse(result["playing"])
        self.assertTrue(result["buffering"])

    def test_control_waits_for_pause_confirmation(self):
        device = Mock()
        cast._active["test"] = "http://lan/video"
        with patch.object(cast, "_cast", return_value=("test", device)), patch.object(cast, "_fresh_status", side_effect=[state(playing=True), state(playing=True), state(paused=True)]), patch.object(cast.time, "sleep"):
            result = cast.control("pause", uuid="test", content_id="http://lan/video")
        device.media_controller.pause.assert_called_once()
        self.assertTrue(result["paused"])

    def test_stale_page_cannot_stop_replacement_media(self):
        device = Mock()
        cast._active["test"] = "new"
        with patch.object(cast, "_cast", return_value=("test", device)), patch.object(cast, "_fresh_status", return_value=state(playing=True, content_id="new")):
            with self.assertRaisesRegex(RuntimeError, "切換"):
                cast.control("stop", uuid="test", content_id="old")
        device.media_controller.stop.assert_not_called()

    def test_lg_refused_resume_position_does_not_hide_successful_playback(self):
        device = dlna.Renderer("test", "LG", "192.168.1.11", "http://192.168.1.11/control")
        with patch.object(cast, "_cast", return_value=("test", device)), patch.object(device, "play"), patch.object(device, "control", side_effect=RuntimeError("Action Failed")), patch.object(cast, "_confirm", return_value=state(playing=True)):
            result = cast.play("http://lan/video", "video/mp4", "Test", 50, "test")
        self.assertTrue(result["playing"])
        self.assertIn("原進度", result["warning"])

    def test_lg_paused_seek_resumes_temporarily_then_restores_pause(self):
        device = dlna.Renderer("test", "LG", "192.168.1.11", "http://192.168.1.11/control")
        cast._active["test"] = "http://lan/video"
        with patch.object(cast, "_cast", return_value=("test", device)), patch.object(cast, "_fresh_status", return_value=state(paused=True)), patch.object(device, "control") as command, patch.object(cast, "_confirm", side_effect=[state(playing=True), state(playing=True, current_time=35), state(paused=True, current_time=35)]):
            result = cast.control("seek", 35, "test", "http://lan/video")
        self.assertEqual([c.args[0] for c in command.call_args_list], ["resume", "seek", "pause"])
        self.assertTrue(result["paused"])

    def test_target_route_wins_over_first_network_interface(self):
        device = dlna.Renderer("test", "LG", "192.168.1.11", "http://192.168.1.11/control")
        sock = Mock()
        sock.__enter__ = Mock(return_value=sock)
        sock.__exit__ = Mock(return_value=False)
        sock.getsockname.return_value = ("192.168.1.10", 1234)
        with patch.object(cast, "_cast", return_value=("test", device)), patch.object(cast.socket, "socket", return_value=sock):
            self.assertEqual(cast.lan_media_origin("test"), "http://192.168.1.10:6970")
        sock.connect.assert_called_once_with(("192.168.1.11", 9))

    def test_dlna_metadata_is_escaped_and_seek_uses_rel_time(self):
        renderer = dlna.Renderer("test", "LG", "192.168.1.11", "http://192.168.1.11/control")
        from xml.etree import ElementTree as ET
        with patch.object(dlna, "_xml", return_value=ET.fromstring("<root><CurrentTransportState>STOPPED</CurrentTransportState></root>")) as send:
            renderer.play("http://lan/api/hls?a=1&b=2", "video/mp4", "Test & <clip>")
            renderer.control("seek", 65)
        body = ET.fromstring(send.call_args_list[1].args[0].data)
        self.assertEqual(body.findtext(".//CurrentURI"), "http://lan/api/hls?a=1&b=2")
        metadata = ET.fromstring(body.findtext(".//CurrentURIMetaData"))
        self.assertEqual(metadata.findtext(".//{*}title"), "Test & <clip>")
        seek = ET.fromstring(send.call_args_list[-1].args[0].data)
        self.assertEqual(seek.findtext(".//Target"), "00:01:05")
        self.assertEqual(seek.findtext(".//Unit"), "REL_TIME")

    def test_dlna_description_rejects_another_host(self):
        with self.assertRaises(ValueError):
            dlna.describe("http://127.0.0.1/private", "192.168.1.11")

    def test_lg_title_fits_receiver_utf8_buffer_while_full_title_is_preserved(self):
        from xml.etree import ElementTree as ET
        receiver = dlna.Renderer("test", "LG", "192.168.1.11", "http://192.168.1.11/control")
        for title in ("測" * 100, "測" * 21 + "😀", "A & <test> " * 40):
            with self.subTest(title_bytes=len(title.encode())), patch.object(dlna, "_xml", return_value=ET.fromstring("<root><CurrentTransportState>STOPPED</CurrentTransportState></root>")) as send:
                receiver.play("http://lan/neutral.m3u8", "application/vnd.apple.mpegurl", title)
                envelope = ET.fromstring(send.call_args_list[1].args[0].data)
                metadata = ET.fromstring(envelope.findtext(".//CurrentURIMetaData"))
                sent_title = metadata.findtext(".//{*}title")
                self.assertLessEqual(len(sent_title.encode("utf-8")), 64)
                self.assertTrue(title.startswith(sent_title))
                self.assertNotIn("\ufffd", sent_title)
                self.assertEqual(receiver.title, title)

    def test_lg_retries_only_transient_play_transition_error(self):
        from xml.etree import ElementTree as ET
        renderer = dlna.Renderer("test", "LG", "192.168.1.11", "http://192.168.1.11/control")
        stopped = ET.fromstring("<root><CurrentTransportState>STOPPED</CurrentTransportState></root>")
        with patch.object(renderer, "command", side_effect=[stopped, None, stopped, dlna.ActionError("701", "Transition not available"), stopped, None]) as command, patch.object(dlna.time, "sleep"):
            renderer.play("http://lan/clip.mp4", "video/mp4", "Test")
        self.assertEqual([c.args[0] for c in command.call_args_list].count("Play"), 2)

    def test_startup_requires_progress_after_playing(self):
        reports = [state(playing=True), state(playing=True), state(playing=True, current_time=1)]
        with patch.object(cast, "_fresh_status", side_effect=reports) as report, patch.object(cast.time, "sleep"):
            result = cast._confirm("test", Mock(), "http://lan/video", "play", require_progress=True)
        self.assertEqual(result["current_time"], 1)
        self.assertEqual(report.call_count, 3)

    def test_playing_without_progress_times_out(self):
        now = [0.0]
        with patch.object(cast.time, "monotonic", side_effect=lambda: now[0]), patch.object(cast.time, "sleep", side_effect=lambda seconds: now.__setitem__(0, now[0] + seconds)), patch.object(cast, "_fresh_status", return_value=state(playing=True)):
            with self.assertRaisesRegex(TimeoutError, "PLAYING.*0.0"):
                cast._confirm("test", Mock(), "http://lan/video", "play", deadline=1, require_progress=True)
        self.assertEqual(now[0], 1)

    def test_progress_confirmation_restarts_after_buffering(self):
        reports = [state(playing=True, current_time=10), state(buffering=True, current_time=20),
                   state(playing=True, current_time=30), state(playing=True, current_time=31)]
        with patch.object(cast, "_fresh_status", side_effect=reports) as report, patch.object(cast.time, "sleep"):
            cast._confirm("test", Mock(), "http://lan/video", "play", require_progress=True)
        self.assertEqual(report.call_count, 4)

    def test_transient_lg_status_error_is_retried(self):
        reports = [dlna.ActionError("701", "Transition not available"), state(playing=True), state(playing=True, current_time=1)]
        with patch.object(cast, "_fresh_status", side_effect=reports), patch.object(cast.time, "sleep"):
            result = cast._confirm("test", Mock(), "http://lan/video", "play", require_progress=True)
        self.assertTrue(result["playing"])

    def test_permanent_lg_status_error_is_not_retried(self):
        with patch.object(cast, "_fresh_status", side_effect=dlna.ActionError("714", "Illegal MIME-type")) as report:
            with self.assertRaises(dlna.ActionError):
                cast._confirm("test", Mock(), "http://lan/video", "play")
        self.assertEqual(report.call_count, 1)

    def test_lg_waits_for_stop_before_setting_uri(self):
        from xml.etree import ElementTree as ET
        def transport(phase):
            return ET.fromstring(f"<root><CurrentTransportState>{phase}</CurrentTransportState></root>")
        receiver = dlna.Renderer("test", "LG", "192.168.1.11", "http://192.168.1.11/control")
        replies = [transport("PLAYING"), None, transport("TRANSITIONING"), transport("STOPPED"), None, transport("STOPPED"), None]
        with patch.object(receiver, "command", side_effect=replies) as command, patch.object(dlna.time, "sleep"):
            receiver.play("http://lan/neutral.mp4", "video/mp4", "Neutral")
        self.assertEqual([c.args[0] for c in command.call_args_list],
                         ["GetTransportInfo", "Stop", "GetTransportInfo", "GetTransportInfo", "SetAVTransportURI", "GetTransportInfo", "Play"])

    def test_lg_waits_for_initial_transition_without_sending_stop(self):
        from xml.etree import ElementTree as ET
        def transport(phase):
            return ET.fromstring(f"<root><CurrentTransportState>{phase}</CurrentTransportState></root>")
        receiver = dlna.Renderer("test", "LG", "192.168.1.11", "http://192.168.1.11/control")
        with patch.object(receiver, "command", side_effect=[transport("TRANSITIONING"), transport("STOPPED"), None, transport("PLAYING")]) as command, patch.object(dlna.time, "sleep"):
            receiver.play("http://lan/neutral.mp4", "video/mp4", "Neutral")
        self.assertNotIn("Stop", [c.args[0] for c in command.call_args_list])

    def test_lg_command_preserves_action_stage_and_error_code(self):
        receiver = dlna.Renderer("test", "LG", "192.168.1.11", "http://192.168.1.11/control")
        with patch.object(dlna, "_xml", side_effect=dlna.ActionError("714", "Illegal MIME-type")):
            with self.assertRaisesRegex(dlna.ActionError, "設定新片.*SetAVTransportURI.*714"):
                receiver.command("SetAVTransportURI", stage="設定新片", CurrentURI="http://lan/neutral.mp4")

    def test_lg_soap_timeout_is_limited_to_remaining_budget(self):
        receiver = dlna.Renderer("test", "LG", "192.168.1.11", "http://192.168.1.11/control")
        with patch.object(dlna.time, "monotonic", return_value=10), patch.object(dlna, "_xml") as xml:
            receiver.command("Play", Speed=1, deadline=10.2, stage="啟動播放")
        self.assertAlmostEqual(xml.call_args.kwargs["timeout"], 0.2)
        self.assertNotIn(b"deadline", xml.call_args.args[0].data)
        self.assertNotIn(b"stage", xml.call_args.args[0].data)

    def test_lg_play_and_confirmation_share_deadline(self):
        receiver = dlna.Renderer("test", "LG", "192.168.1.11", "http://192.168.1.11/control")
        with patch.object(cast.time, "monotonic", return_value=100), patch.object(cast, "_cast", return_value=("test", receiver)), patch.object(receiver, "play") as play, patch.object(cast, "_confirm", return_value=state(playing=True)) as confirm:
            cast.play("http://lan/video", "video/mp4", "Neutral", uuid="test")
        self.assertEqual(play.call_args.kwargs["deadline"], 130)
        self.assertEqual(confirm.call_args.kwargs["deadline"], 130)
        self.assertTrue(confirm.call_args.kwargs["require_progress"])

    def test_lg_variant_resolution_uses_startup_deadline_and_selected_identity(self):
        device = dlna.Renderer("test", "LG", "192.168.1.11", "http://192.168.1.11/control")
        chosen = "http://lan/720p/video.m3u8"
        with patch.object(cast.time, "monotonic", return_value=100), patch.object(cast, "_cast", return_value=("test", device)), patch.object(cast.hls_proxy, "dlna_media_url", return_value=chosen) as select, patch.object(device, "play") as play, patch.object(cast, "_confirm", return_value=state(playing=True, content_id=chosen)) as confirm:
            cast.play("http://lan/master.m3u8", "application/vnd.apple.mpegurl", "Neutral", uuid="test")
        select.assert_called_once_with("http://lan/master.m3u8", 130)
        self.assertEqual(play.call_args.args[0], chosen)
        self.assertEqual(confirm.call_args.args[2], chosen)
        self.assertEqual(cast._active["test"], chosen)

    def test_chromecast_keeps_original_master_playlist(self):
        device = Mock()
        with patch.object(cast, "_cast", return_value=("test", device)), patch.object(cast.hls_proxy, "dlna_media_url") as select, patch.object(cast, "_confirm", return_value=state(playing=True)):
            cast.play("http://lan/master.m3u8", "application/vnd.apple.mpegurl", "Neutral", uuid="test")
        select.assert_not_called()
        self.assertEqual(device.media_controller.play_media.call_args.args[0], "http://lan/master.m3u8")

    def test_lg_vendor_transition_is_buffering_and_can_be_cancelled_before_reload(self):
        from xml.etree import ElementTree as ET
        def transport(phase):
            return ET.fromstring(f"<root><CurrentTransportState>{phase}</CurrentTransportState></root>")
        device = dlna.Renderer("test", "LG", "192.168.1.11", "http://192.168.1.11/control")
        with patch.object(device, "command", side_effect=[transport("LG_TRANSITIONING"), ET.fromstring("<root/>")]):
            self.assertTrue(device.status()["buffering"])
        with patch.object(device, "command", side_effect=[transport("LG_TRANSITIONING"), None, transport("STOPPED"), None, transport("PLAYING")]) as command, patch.object(dlna.time, "sleep"):
            device.play("http://lan/neutral.m3u8", "application/vnd.apple.mpegurl", "Neutral")
        self.assertEqual([c.args[0] for c in command.call_args_list], ["GetTransportInfo", "Stop", "GetTransportInfo", "SetAVTransportURI", "GetTransportInfo"])

    def test_waiting_for_another_operation_is_bounded(self):
        with patch.object(cast, "_operations", Mock(acquire=Mock(return_value=False))) as lock, patch.object(cast, "_cast") as find:
            with self.assertRaises(TimeoutError):
                cast.play("http://lan/video", "video/mp4", "Neutral", uuid="test")
        self.assertLessEqual(lock.acquire.call_args.kwargs["timeout"], cast.CONFIRM_TIMEOUT)
        find.assert_not_called()

    def test_failed_paused_seek_still_restores_pause_with_reserved_time(self):
        receiver = dlna.Renderer("test", "LG", "192.168.1.11", "http://192.168.1.11/control")
        cast._active["test"] = "http://lan/video"
        reports = [state(playing=True), TimeoutError("seek timeout"), state(paused=True)]
        with patch.object(cast, "_cast", return_value=("test", receiver)), patch.object(cast, "_fresh_status", return_value=state(paused=True)), patch.object(receiver, "control") as command, patch.object(cast, "_confirm", side_effect=reports):
            with self.assertRaisesRegex(TimeoutError, "seek timeout"):
                cast.control("seek", 35, "test", "http://lan/video")
        self.assertEqual([c.args[0] for c in command.call_args_list], ["resume", "seek", "pause"])
        self.assertLess(command.call_args_list[1].kwargs["deadline"], command.call_args_list[2].kwargs["deadline"])

    def test_status_poll_waits_until_control_releases_the_connection(self):
        entered = threading.Event()
        completed = []
        with patch.object(cast, "_cast", return_value=("test", Mock())), patch.object(cast, "_fresh_status", side_effect=lambda *args: entered.set() or state(playing=True)):
            cast._operations.acquire()
            worker = threading.Thread(target=lambda: completed.append(cast.status("test")), daemon=True)
            try:
                worker.start()
                self.assertFalse(entered.wait(0.05))
            finally:
                cast._operations.release()
                worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(completed), 1)

    def test_control_waits_for_transient_reconnection_before_sending(self):
        device = Mock()
        cast._active["test"] = "http://lan/video"
        reports = [cast.ReceiverUnavailable("connecting"), state(playing=True), state(paused=True)]
        with patch.object(cast, "_cast", return_value=("test", device)), patch.object(cast, "_fresh_status", side_effect=reports), patch.object(cast.time, "sleep"):
            self.assertTrue(cast.control("pause", uuid="test", content_id="http://lan/video")["paused"])
        device.media_controller.pause.assert_called_once()

    def test_pychromecast_not_connected_is_a_retryable_status(self):
        from pychromecast.error import NotConnected
        device = Mock()
        device.media_controller.update_status.side_effect = NotConnected("connecting")
        with self.assertRaises(cast.ReceiverUnavailable):
            cast._fresh_status("test", device)


if __name__ == "__main__":
    unittest.main()
