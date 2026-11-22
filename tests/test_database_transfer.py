"""The outbound-byte meter only observes existing PostgreSQL sockets."""
import io
import json
import socket
import sys
import unittest
from contextlib import redirect_stderr
from types import SimpleNamespace
from unittest.mock import Mock, patch

from flask import Blueprint, Flask

from app.services import database_transfer as meter


class DatabaseTransferTest(unittest.TestCase):
    def listeners(self, dialect='postgresql', platform='linux'):
        hooks = {}
        engine = SimpleNamespace(dialect=SimpleNamespace(name=dialect))

        def listen(target, name):
            self.assertIs(target, engine)
            return lambda callback: hooks.setdefault(name, callback)

        with patch.object(meter.event, 'listens_for', listen), patch.object(sys, 'platform', platform):
            meter.instrument_database_transfer(engine)
        return hooks

    def test_install_performs_no_io_and_skips_unsupported_engines(self):
        with patch.object(meter, '_tcp_bytes') as read:
            self.assertEqual(set(self.listeners()), {'checkout', 'checkin'})
            self.assertEqual(self.listeners(dialect='sqlite'), {})
            self.assertEqual(self.listeners(platform='win32'), {})
            read.assert_not_called()

    def test_checkins_count_tcp_deltas_and_reconnect_once_without_identifiers(self):
        app = Flask(__name__)
        node = Blueprint('neosektor', __name__)
        node.add_url_rule('/scope/<secret>', 'scope', lambda secret: '')
        app.register_blueprint(node)
        hooks = self.listeners()
        record = SimpleNamespace(info={})
        connection = Mock()
        stream = io.StringIO()
        with redirect_stderr(stream), patch.object(meter.time, 'monotonic', side_effect=[0, 1, 2, 3, 301]), \
                patch.object(meter, '_tcp_bytes', side_effect=[(100, 250), (120, 350), OSError(), (12, 15)]):
            with app.test_request_context('/scope/private-path?token=secret'):
                hooks['checkout'](connection, record, None)
                hooks['checkin'](connection, record)
            for current in (connection, connection, None):
                hooks['checkout'](current, record, None)
                hooks['checkin'](current, record)
            # SQLAlchemy clears ConnectionRecord.info on a new DBAPI connection.
            record.info.clear()
            hooks['checkout'](connection, record, None)
            hooks['checkin'](connection, record)
        lines = stream.getvalue().splitlines()
        self.assertEqual(len(lines), 2)
        rows = [json.loads(line[line.index('{'):]) for line in lines]
        self.assertEqual(rows[0], {'neosektor': {'checkins': 1, 'sent_acked': 100, 'received': 250}})
        self.assertEqual(rows[1], {'background': {'checkins': 4, 'sent_acked': 32, 'received': 115,
                                                  'unmeasured': 2}})
        self.assertNotIn('private-path', stream.getvalue())

    @unittest.skipUnless(sys.platform.startswith('linux'), 'Linux TCP_INFO ABI')
    def test_linux_counter_observes_real_loopback_bytes_and_preserves_socket(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            with socket.create_connection(listener.getsockname(), timeout=2) as client, listener.accept()[0] as peer:
                initial = meter._tcp_bytes(client)
                payload = b'x' * 1024
                client.sendall(payload)
                self.assertEqual(peer.recv(len(payload), socket.MSG_WAITALL), payload)
                peer.sendall(b'ok')
                self.assertEqual(client.recv(2, socket.MSG_WAITALL), b'ok')
                final = meter._tcp_bytes(client)
                self.assertGreaterEqual(final[0] - initial[0], len(payload))
                self.assertGreaterEqual(final[1] - initial[1], 2)
                client.sendall(b'1')
                self.assertEqual(peer.recv(1), b'1')
