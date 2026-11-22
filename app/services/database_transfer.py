"""Passive Linux TCP byte accounting for the existing PostgreSQL pool.

No queries, addresses, SQL, parameters, payloads, timers or additional network
connections. Categories identify the borrower of the connection, not a user.
"""
import json
import os
import socket
import struct
import sys
import threading
import time
from collections import Counter

from flask import has_request_context, request
from sqlalchemy import event


_NODES = frozenset(('neosektor', 'neoermac', 'neoraiden', 'neosubzero', 'neoscorpion',
                    'neorain', 'neostaffing', 'neomotherbrain', 'neogateway'))
_TOTALS = '_neo_transfer_previous'
_BORROWER = '_neo_transfer_borrower'


def _component():
    if not has_request_context():
        return 'background'
    blueprint = request.blueprint
    return blueprint if blueprint in _NODES else 'shared'


def _tcp_bytes(connection):
    # Linux UAPI struct tcp_info: bytes_acked and bytes_received are consecutive
    # native-endian u64s at offsets 120 and 128. Only close our duplicate fd.
    with socket.fromfd(connection.fileno(), socket.AF_INET, socket.SOCK_STREAM) as stream:
        info = stream.getsockopt(socket.IPPROTO_TCP, socket.TCP_INFO, 136)
    return struct.unpack_from('=QQ', info, 120)


class _TransferTotals:
    def __init__(self):
        self.lock = threading.Lock()
        self.rows = {}
        self.last_log = None

    def record(self, component, delta):
        now = time.monotonic()
        with self.lock:
            row = self.rows.setdefault(component, Counter())
            row['checkins'] += 1
            if delta is None:
                row['unmeasured'] += 1
            else:
                row['sent_acked'] += delta[0]
                row['received'] += delta[1]
            if self.last_log is not None and now - self.last_log < 300:
                return
            self.last_log = now
            rows, self.rows = self.rows, {}
        print('INFO NeoApps postgres_transfer pid=' + str(os.getpid()) + ' ' +
              json.dumps(rows, separators=(',', ':')), file=sys.stderr, flush=True)


def instrument_database_transfer(engine):
    if engine.dialect.name != 'postgresql' or not sys.platform.startswith('linux'):
        return
    totals = _TransferTotals()

    @event.listens_for(engine, 'checkout')
    def checkout(connection, record, proxy):
        try:
            record.info[_BORROWER] = _component()
        except Exception:
            # Observability must never interrupt a checkout.
            pass

    @event.listens_for(engine, 'checkin')
    def checkin(connection, record):
        try:
            delta = None
            component = record.info.pop(_BORROWER, 'background')
            if connection is not None:
                try:
                    latest = _tcp_bytes(connection)
                except (OSError, ValueError, AttributeError, struct.error):
                    pass
                else:
                    # The first sample includes connection setup and pre-ping;
                    # SQLAlchemy clears record.info whenever it replaces a socket.
                    previous = record.info.get(_TOTALS, (0, 0))
                    record.info[_TOTALS] = latest
                    if latest[0] >= previous[0] and latest[1] >= previous[1]:
                        delta = (latest[0] - previous[0], latest[1] - previous[1])
            totals.record(component, delta)
        except Exception:
            # Metrics cannot prevent a release, rollback or pool return.
            pass
