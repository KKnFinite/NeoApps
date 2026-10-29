"""Cooperative priority for operational requests; no timer, thread or DB writes.

The current deployment has one non-preloaded web worker. Request activity is
worker-local; Linux CPU pressure covers the container. The existing PostgreSQL
lease still serializes FROST jobs across processes. This is admission control,
not a reservation against memory that an arriving request may allocate.
"""
import threading
import time
from pathlib import Path

from flask import current_app, g, has_app_context, request


class Deferred(RuntimeError):
    """Resource contention, not a failed provider or exhausted capture attempt."""


class Activity:
    def __init__(self):
        self.lock = threading.Lock()
        self.active = 0
        self.slow_until = 0.0
        self.pressure_at = 0.0
        self.pressure = None


def initialize(app):
    if 'frost_request_activity' in app.extensions: return
    state = app.extensions['frost_request_activity'] = Activity()

    @app.before_request
    def started():
        if request.path == '/healthz' or request.path.startswith('/static/'): return
        g.frost_request_started = time.monotonic()
        with state.lock: state.active += 1

    @app.teardown_request
    def ended(_error):
        start = g.pop('frost_request_started', None)
        if start is None: return
        at = time.monotonic()
        with state.lock:
            state.active = max(0, state.active - 1)
            if at - start >= 1.0: state.slow_until = at + 2.0


def cpu_pressure():
    """Kernel PSI: percent of the last 10s spent waiting for CPU, not host load."""
    try:
        line = Path('/sys/fs/cgroup/cpu.pressure').read_text().splitlines()[0]
        return float(dict(field.split('=') for field in line.split()[1:])['avg10'])
    except (OSError, ValueError, KeyError, IndexError): return None


def reason():
    if not has_app_context(): return None
    state = current_app.extensions.get('frost_request_activity')
    if state is None: return None
    at = time.monotonic()
    with state.lock:
        if state.active: return 'Operational request in progress'
        if at < state.slow_until: return 'Operational response recovery (2 seconds)'
        if at - state.pressure_at >= 1.0:
            state.pressure, state.pressure_at = cpu_pressure(), at
        if state.pressure is not None and state.pressure >= 20.0:
            return 'Container CPU pressure (10-second wait >= 20%)'
    return None


def checkpoint():
    why = reason()
    if why: raise Deferred('FROST research deferred: ' + why)
