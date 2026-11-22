"""Operation-owned, bounded requests transport for read-only Google diagnostics.

requests runs response hooks before buffering or resolving redirects. Read the
urllib3 wire stream with decoding disabled, then let bounded_http enforce both
wire and decompressed limits before gspread/google-auth can access content.
"""
from functools import partial
import json
import logging
import sys

import gspread
from google.auth.transport.requests import AuthorizedSession, Request
from requests import Session
from urllib3.exceptions import HTTPError

from app.services.bounded_http import ExternalResponseError, read_body


METADATA_MAX_BYTES = 1024 * 1024  # Workbook metadata and fixed, small cell ranges.
AUTH_MAX_BYTES = 256 * 1024
AUTH_TIMEOUT_SECONDS = 5
logger = logging.getLogger(__name__)


class _GoogleTransfer:
    """Count declared Sheets body lengths on an existing operation's session."""

    def __init__(self, component):
        self.component = component
        self.responses = self.sent = self.received = self.unknown_out = self.unknown_in = 0

    def observe(self, response, **kwargs):
        try:
            self.responses += 1
            request = response.request
            outgoing = request.headers.get('Content-Length', '0' if request.body is None else '')
            incoming = response.headers.get('Content-Length', '')
            if request.method == 'HEAD' or response.status_code in (204, 304):
                incoming = '0'
            if outgoing.isascii() and outgoing.isdecimal() and len(outgoing) <= 20:
                self.sent += int(outgoing)
            else:
                self.unknown_out += 1
            if incoming.isascii() and incoming.isdecimal() and len(incoming) <= 20:
                self.received += int(incoming)
            else:
                self.unknown_in += 1
        except Exception:
            # Diagnostics have no authority over a provider call.
            pass

    def emit(self):
        if self.responses:
            print(f'INFO NeoApps google_sheets_transfer component={self.component} responses={self.responses} '
                  f'declared_out_body_bytes={self.sent} declared_in_body_bytes={self.received} '
                  f'unknown_out_lengths={self.unknown_out} unknown_in_lengths={self.unknown_in}',
                  file=sys.stderr, flush=True)


def observe_google_transfer(client, component):
    if component not in ('motherbrain_missions', 'staffing_hours_log'):
        return
    transport = getattr(getattr(client, 'http_client', None), 'session', None)
    session = transport.session if isinstance(transport, _Transport) else transport
    if isinstance(session, Session) and not isinstance(getattr(transport, '_transfer_meter', None), _GoogleTransfer):
        meter = _GoogleTransfer(component)
        session.hooks['response'].append(meter.observe)
        transport._transfer_meter = meter


def _close(resource):
    try:
        resource.close()
    except Exception:
        # Never mask the primary failure or include provider/credential details.
        logger.warning('Google diagnostic resource cleanup failed.')


def close_google_clients(clients):
    """Only pass clients owned by this operation; deduplicate shared sessions."""
    seen = set()
    for client in clients:
        try:
            session = client.http_client.session
        except Exception:
            logger.warning('Google diagnostic resource cleanup failed.')
            continue
        if id(session) not in seen:
            seen.add(id(session))
            _close(session)
            meter = getattr(session, '_transfer_meter', None)
            if isinstance(meter, _GoogleTransfer):
                session._transfer_meter = None
                try:
                    meter.emit()
                except (OSError, ValueError):
                    pass


def _bounded_response(response, *, maximum, **_kwargs):
    class WireBody:
        headers = response.headers

        def read(self, size):
            try:
                return response.raw.read(size, decode_content=False)
            except HTTPError:
                raise ExternalResponseError('Provider response is incomplete or unavailable.') from None

    try:
        body = read_body(WireBody(), maximum=maximum)
    finally:
        _close(response)
    # Standard requests content cache: subsequent SDK json/text access is safe.
    response._content = body
    response._content_consumed = True
    if 300 <= response.status_code < 400:
        error = ExternalResponseError('Google diagnostic redirects are not accepted.')
        error.response = response
        raise error
    return response


class _Transport:
    def __init__(self, session, *, maximum, owned):
        self.session, self.maximum, self.owned = session, maximum, owned
        self.closed = False

    def request(self, *args, **kwargs):
        headers = dict(kwargs.get('headers') or {})
        headers['Accept-Encoding'] = 'gzip, deflate'
        hooks = dict(kwargs.get('hooks') or self.session.hooks)
        previous = hooks.get('response') or []
        if callable(previous):
            previous = [previous]
        hooks['response'] = [partial(_bounded_response, maximum=self.maximum), *previous]
        kwargs.update(stream=True, allow_redirects=False, hooks=hooks, headers=headers)
        return self.session.request(*args, **kwargs)

    def close(self):
        if not self.closed:
            self.closed = True
            if self.owned:
                _close(self.session)


def bounded_auth_request(*args, **kwargs):
    kwargs['timeout'] = AUTH_TIMEOUT_SECONDS
    transport = _Transport(Session(), maximum=AUTH_MAX_BYTES, owned=True)
    try:
        return Request(session=transport)(*args, **kwargs)
    finally:
        transport.close()


class DiagnosticHTTPClient(gspread.HTTPClient):
    def request(self, *args, **kwargs):
        response = super().request(*args, **kwargs)
        if 'text/event-stream' not in response.headers.get('Content-Type', '').lower():
            def invalid_constant(_value):
                raise ValueError('Invalid JSON constant')
            try:
                value = json.loads(response.content.decode('utf-8'), parse_constant=invalid_constant)
                if not isinstance(value, dict):
                    raise ValueError('Expected a JSON object')
            except (ValueError, RecursionError):
                error = ExternalResponseError('Google diagnostic returned invalid JSON.')
                error.response = response
                raise error from None
        return response


def diagnostic_http_client(auth, session=None, *, maximum=METADATA_MAX_BYTES):
    """gspread's supported http_client factory; a supplied session is borrowed."""
    owned = session is None
    session = AuthorizedSession(auth, auth_request=bounded_auth_request) if owned else session
    transport = _Transport(session, maximum=maximum, owned=owned)
    try:
        client = DiagnosticHTTPClient(auth, session=transport)
        client.auth = auth
        return client
    except Exception:
        transport.close()
        raise
