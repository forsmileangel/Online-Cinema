import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pydantic import ValidationError

from backend import cast, cast_session, dlna
from backend.models import CastControlIn, CastSessionControlIn


def device(control_type="master"):
    result = Mock()
    result.status = SimpleNamespace(volume_control_type=control_type, volume_level=.6, volume_muted=False)
    result.media_controller.status = SimpleNamespace(volume_level=.8, volume_muted=False, supports_stream_volume=True,
                                                    supports_stream_mute=True, media_session_id=42)
    result.media_controller.send_message.side_effect = lambda _message, **kw: kw['callback_function'](True, {})
    return result


def state(**values):
    return dict(content_id='our-movie', idle=False, paused=True, playing=False, current_time=150, duration=300,
                **values)


class CastVolumeTests(unittest.TestCase):
    def setUp(self):
        p = patch.object(cast, '_active', {'tv': 'our-movie'})
        p.start()
        self.addCleanup(p.stop)

    def control(self, receiver, action, before, after, **values):
        with patch.object(cast, '_cast', return_value=('tv', receiver)), patch.object(cast, '_available_status', side_effect=[before, after]):
            return cast.control(action, uuid='tv', **values)

    def test_status_uses_device_volume_for_nest_and_stream_volume_for_fixed_cast(self):
        for control_type, level, scope in [('master', .6, 'device'), ('attenuation', .6, 'device'), ('fixed', .8, 'stream')]:
            with self.subTest(control_type=control_type):
                result = cast._volume_status(device(control_type))
                self.assertEqual(result['volume_level'], level)
                self.assertEqual(result['volume_scope'], scope)
                self.assertTrue(result['can_set_volume'])
                self.assertTrue(result['can_mute'])

    def test_unsupported_stream_does_not_advertise_controls(self):
        receiver = device('fixed')
        receiver.media_controller.status.supports_stream_volume = False
        receiver.media_controller.status.supports_stream_mute = False
        result = cast._volume_status(receiver)
        self.assertFalse(result['can_set_volume'])
        self.assertFalse(result['can_mute'])

    def test_nest_volume_is_confirmed_without_playing_or_seeking(self):
        receiver = device()
        before = state(**cast._volume_status(receiver))
        after = {**before, 'volume_level': .4}
        result = self.control(receiver, 'volume', before, after, volume_level=.4)
        receiver.set_volume.assert_called_once()
        self.assertEqual(receiver.set_volume.call_args.args, (.4,))
        self.assertEqual(result['current_time'], 150)
        self.assertTrue(result['paused'])
        receiver.media_controller.play.assert_not_called()
        receiver.media_controller.seek.assert_not_called()

    def test_fixed_receiver_uses_owned_media_session_and_unmutes_slider(self):
        receiver = device('fixed')
        before = state(**{**cast._volume_status(receiver), 'volume_muted': True})
        after = {**before, 'volume_level': .3, 'volume_muted': False}
        self.control(receiver, 'volume', before, after, volume_level=.3)
        payload = receiver.media_controller.send_message.call_args.args[0]
        self.assertEqual(payload, {'type': 'SET_VOLUME', 'mediaSessionId': 42, 'volume': {'level': .3, 'muted': False}})
        receiver.set_volume.assert_not_called()

    def test_mute_preserves_current_level(self):
        receiver = device('fixed')
        before = state(**cast._volume_status(receiver))
        result = self.control(receiver, 'mute', before, {**before, 'volume_muted': True}, muted=True)
        self.assertEqual(receiver.media_controller.send_message.call_args.args[0]['volume'], {'muted': True})
        self.assertEqual(result['volume_level'], .8)

    def test_replaced_idle_or_cancelled_sessions_cannot_adjust_sound(self):
        receiver = device()
        normal = state(**cast._volume_status(receiver))
        for before, guard in [({**normal, 'content_id': 'someone-else'}, None), ({**normal, 'idle': True}, None), (normal, lambda: False)]:
            with self.subTest(before=before), patch.object(cast, '_cast', return_value=('tv', receiver)), patch.object(cast, '_available_status', return_value=before):
                with self.assertRaises(RuntimeError):
                    cast.control('volume', uuid='tv', volume_level=.5, guard=guard)
        receiver.set_volume.assert_not_called()
        receiver.media_controller.send_message.assert_not_called()

    def test_dlna_has_clear_unsupported_error(self):
        receiver = dlna.Renderer('tv', 'LG', '192.168.1.3', 'http://192.168.1.3/control')
        with patch.object(cast, '_cast', return_value=('tv', receiver)), patch.object(cast, '_available_status', return_value=state()), patch.object(receiver, 'command') as send:
            with self.assertRaisesRegex(ValueError, '遙控器'):
                cast.control('volume', uuid='tv', volume_level=.5)
        send.assert_not_called()

    def test_ack_without_matching_volume_is_not_success(self):
        receiver = device()
        before = state(**cast._volume_status(receiver))
        for level in (.2, .59):
            with self.subTest(level=level), patch.object(cast, '_cast', return_value=('tv', receiver)), patch.object(cast, '_available_status', side_effect=[before, before, TimeoutError('volume unconfirmed')]), patch.object(cast.time, 'sleep'):
                with self.assertRaises(TimeoutError):
                    cast.control('volume', uuid='tv', volume_level=level)

    def test_invalid_levels_are_rejected_before_contacting_receiver(self):
        for value in (None, True, -.1, 1.1, float('nan'), float('inf')):
            with self.subTest(value=value), patch.object(cast, '_cast') as connect:
                with self.assertRaises(ValueError):
                    cast.control('volume', uuid='tv', volume_level=value)
                connect.assert_not_called()
        for model in (CastControlIn, CastSessionControlIn):
            for value in (-1, 2, float('nan'), True):
                with self.assertRaises(ValidationError):
                    model(action='volume', uuid='tv', session_id='session', volume_level=value)

    def test_session_passes_audio_fields_and_preserves_pause(self):
        session = cast_session.PlaybackSession({'uuid': 'tv'})
        session.snapshot.update(phase='paused', content_id='our-movie', paused=True)
        with patch.object(cast, 'control', return_value=state(volume_level=.4)) as control:
            session.handle('volume', {'volume_level': .4})
        self.assertEqual(control.call_args.kwargs['volume_level'], .4)
        self.assertEqual(session.snapshot['phase'], 'paused')

    def test_audio_failure_does_not_mark_playback_failed(self):
        session = cast_session.PlaybackSession({'uuid': 'tv'})
        session.snapshot.update(phase='paused', content_id='our-movie', paused=True)
        session.load = Mock()
        session.commands.put(('volume', {'volume_level': .4}))
        original = session.publish
        def publish(**values):
            original(**values)
            if values.get('error'):
                session.cancelled.set()
        with patch.object(session, 'publish', side_effect=publish), patch.object(cast, 'control', side_effect=RuntimeError('volume failed')):
            session.run()
        self.assertEqual(session.snapshot['phase'], 'paused')
        self.assertTrue(session.snapshot['paused'])


if __name__ == '__main__':
    unittest.main()
