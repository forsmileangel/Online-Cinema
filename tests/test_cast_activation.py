import unittest
from unittest.mock import Mock, patch

from backend import cast


class CastActivationTests(unittest.TestCase):
    def test_explicit_cast_launches_receiver_before_loading_media(self):
        receiver = Mock()
        receiver.status.is_active_input = False
        sequence = []
        receiver.start_app.side_effect = lambda *a, **k: sequence.append('launch')
        receiver.media_controller.play_media.side_effect = lambda *a, **k: sequence.append('load')
        with patch.object(cast, '_active', {}), patch.object(cast, '_cast', return_value=('tv', receiver)), patch.object(cast, '_confirm', return_value={'playing': True}), patch.object(cast.time, 'monotonic', return_value=100):
            cast.play('http://lan/video', 'video/mp4', 'Neutral', uuid='tv')
        self.assertEqual(sequence, ['launch', 'load'])
        receiver.start_app.assert_called_once_with('CC1AD845', force_launch=True, timeout=10)

    def test_episode_change_reuses_receiver_without_relaunching(self):
        receiver = Mock()
        current = {'content_id': 'old-episode', 'idle': False}
        with patch.object(cast, '_active', {'tv': 'old-episode'}), patch.object(cast, '_cast', return_value=('tv', receiver)), patch.object(cast, '_available_status', return_value=current), patch.object(cast, '_confirm', return_value={'playing': True}):
            cast.play('http://lan/episode2', 'video/mp4', 'Neutral', uuid='tv', expected='old-episode')
        receiver.start_app.assert_not_called()
        receiver.media_controller.play_media.assert_called_once()

    def test_cancel_during_activation_does_not_load_or_claim_media(self):
        receiver = Mock()
        active = {'tv': 'previous-media'}
        with patch.object(cast, '_active', active), patch.object(cast, '_cast', return_value=('tv', receiver)), patch.object(cast, '_confirm') as confirm:
            with self.assertRaisesRegex(RuntimeError, '取消'):
                cast.play('http://lan/video', 'video/mp4', 'Neutral', uuid='tv', guard=Mock(side_effect=[True, False]))
        receiver.media_controller.play_media.assert_not_called()
        confirm.assert_not_called()
        self.assertEqual(active['tv'], 'previous-media')


if __name__ == '__main__':
    unittest.main()
