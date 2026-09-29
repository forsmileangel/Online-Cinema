import asyncio
import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

from starlette.requests import Request
from starlette.responses import Response

from backend import lan, main


class PhoneConnectionTests(unittest.TestCase):
    def setUp(self):
        for target, name, value in ((main.db, 'get_setting', '1'),
                                    (main.tv_session, 'ensure_code', 'ABC234')):
            mock = patch.object(target, name, return_value=value)
            mock.start()
            self.addCleanup(mock.stop)

    def test_network_change_is_detected_without_restarting_and_uses_server_port(self):
        with patch.object(lan, 'ipv4_lan', side_effect=[['192.168.1.10'], ['192.168.50.22']]), \
                patch.object(lan, 'port_open', return_value=True), patch.object(main.S, 'PORT', 7000):
            first = main.get_connection()
            second = main.get_connection()
        self.assertEqual(first.headers['cache-control'], 'no-store')
        self.assertEqual(json.loads(first.body)['addresses'][0]['url'], 'http://192.168.1.10:7000')
        self.assertEqual(json.loads(second.body)['addresses'][0]['connect_url'], 'http://192.168.50.22:7000/connect?c=ABC234')
        self.assertNotIn('192.168.1.10', second.body.decode())

    def test_disabled_lan_does_not_advertise_a_pairing_link_or_probe(self):
        with patch.object(main.db, 'get_setting', return_value=''), patch.object(lan, 'ipv4_lan', return_value=['192.168.1.10']), \
                patch.object(lan, 'port_open') as probe, patch.object(main.tv_session, 'ensure_code') as code:
            result = json.loads(main.get_connection().body)
        self.assertFalse(result['enabled'])
        self.assertFalse(result['addresses'][0]['listening'])
        self.assertEqual(result['addresses'][0]['connect_url'], '')
        probe.assert_not_called()
        code.assert_not_called()

    def test_no_lan_address_and_multiple_interfaces_have_distinct_states(self):
        with patch.object(lan, 'ipv4_lan', return_value=[]):
            self.assertEqual(json.loads(main.get_connection().body)['addresses'], [])
        with patch.object(lan, 'ipv4_lan', return_value=['192.168.1.10', '10.0.0.9']), \
                patch.object(lan, 'port_open', side_effect=[False, True]):
            addresses = json.loads(main.get_connection().body)['addresses']
        self.assertEqual([a['listening'] for a in addresses], [False, True])
        self.assertEqual(len(addresses), 2)

    def test_probe_is_bounded_and_closes_socket(self):
        connection = Mock()
        with patch.object(lan.socket, 'create_connection') as connect:
            connect.return_value.__enter__.return_value = connection
            self.assertTrue(lan.port_open('192.168.1.10', 6970))
            connect.assert_called_once_with(('192.168.1.10', 6970), timeout=.4)
            connect.return_value.__exit__.assert_called_once()
        with patch.object(lan.socket, 'create_connection', side_effect=TimeoutError):
            self.assertFalse(lan.port_open('192.168.1.10', 6970))

    def test_only_usable_private_ipv4_addresses_are_advertised(self):
        for ip in ('127.0.0.1', '169.254.1.2', '0.0.0.0', '224.0.0.1', '8.8.8.8', '::1', 'fd00::1', 'invalid'):
            with self.subTest(ip=ip):
                self.assertFalse(lan._ok_lan(ip))
        for ip in ('192.168.1.10', '10.0.0.9', '172.16.1.2'):
            self.assertTrue(lan._ok_lan(ip))

    def test_pairing_information_remains_protected_on_lan(self):
        request = Request({'type': 'http', 'method': 'GET', 'path': '/api/connection', 'scheme': 'http',
                           'query_string': b'', 'headers': [], 'client': ('192.168.1.20', 12345),
                           'server': ('192.168.1.10', 6970)})
        next_handler = AsyncMock(return_value=Response())
        with patch.object(main.tv_session, 'valid_cookie', return_value=False):
            response = asyncio.run(main.AccessGuard(main.app).dispatch(request, next_handler))
        self.assertEqual(response.status_code, 401)
        next_handler.assert_not_called()


if __name__ == '__main__':
    unittest.main()
