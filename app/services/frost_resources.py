"""Cooperative priority for operational requests; no timer, thread or DB writes.

The current deployment has one non-preloaded web worker. Request activity is
worker-local; Linux CPU pressure covers the container. The existing PostgreSQL
lease still serializes FROST jobs across processes. This is admission control,
not a reservation against memory that an arriving request may allocate.
"""
import threading
import time
from pathlib import Path
from contextvars import ContextVar
from contextlib import contextmanager
from functools import wraps

from flask import current_app, g, has_app_context, has_request_context, request


class Deferred(RuntimeError):
    """Resource contention, not a failed provider or exhausted capture attempt."""
    def __init__(self, message, *, details=None):
        super().__init__(message)
        self.details = details or {}


MAX_WAIT_SECONDS = 20.0
_cooperation = ContextVar('frost_cooperative_capture', default=None)


@contextmanager
def cooperate(*, max_wait=None):
    """Pause one bounded background capture, without retaining a DB session.

    No request waits for research. Other jobs retain fail-fast admission.
    The wait allowance is cumulative, not restarted at every checkpoint.
    """
    state = dict(remaining=MAX_WAIT_SECONDS if max_wait is None else max_wait,
        waited=0.0, waits=0, valid=None)
    token = _cooperation.set(state)
    try:yield state
    finally:_cooperation.reset(token)


def cooperative_capture(function):
    @wraps(function)
    def capture(*args, **kwargs):
        with cooperate():return function(*args, **kwargs)
    return capture


def limit_to_window(valid):
    state = _cooperation.get()
    if state is not None:state['valid'] = valid


def wait_metrics():
    state = _cooperation.get()
    return dict(resource_waits=state['waits'],resource_wait_seconds=round(state['waited'],3)) if state else {}


def deadline_error(message):
    state = _cooperation.get()
    return Deferred(message + ' after yielding to operational work') if state and state['waits'] else TimeoutError(message)


class Activity:
    def __init__(self):
        self.lock = threading.Lock()
        self.changed = threading.Condition(self.lock)
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
            state.changed.notify_all()


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
    budget = _cooperation.get()
    while True:
        if budget and budget['valid'] and not budget['valid']():
            raise Deferred('FROST hourly acquisition window closed; never backfill predictions.')
        why = reason()
        if not why:return
        if not budget or budget['remaining'] <= 0 or has_request_context():
            raise Deferred('FROST research deferred: ' + why)
        state = current_app.extensions.get('frost_request_activity')
        before = time.monotonic()
        # Condition.wait releases the activity lock; foreground requests never
        # wait for this capture. Short checks also retain CPU/cooldown guards.
        delay = min(.1, budget['remaining'])
        if state is None:
            time.sleep(delay)
        else:
            with state.changed:state.changed.wait(timeout=delay)
        elapsed = max(0, time.monotonic()-before)
        budget['remaining'] -= elapsed
        budget['waited'] += elapsed
        budget['waits'] += 1
