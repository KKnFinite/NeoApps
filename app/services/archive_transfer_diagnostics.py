"""Bounded R2 body-length counters, emitted when an existing owner closes.

These are declared HTTP body bytes, not a packet meter or Render billing data.
Never read a stream, retain a request, or log URLs, headers, keys or payloads.
"""
import sys
from datetime import datetime, timezone


def _length(value):
    if isinstance(value, bytes):
        value = value.decode('ascii', errors='replace')
    text = str(value)
    return int(text) if len(text) <= 20 and text.isascii() and text.isdecimal() else None


class _Meter:
    def __init__(self):
        # One sequential owned transport operation; no global registry/timer.
        self.requests = self.puts = self.gets = self.heads = self.lists = 0
        self.out_bytes = self.in_bytes = self.responses = self.failures = 0
        self.unknown_out = self.unknown_in = self.decoded_lengths = 0
        self.emitted = False
        self.first_request_utc = None

    def before_send(self, request, event_name='', **_kwargs):
        try:
            operation = event_name.rsplit('.', 1)[-1]
            if self.first_request_utc is None:
                self.first_request_utc = datetime.now(timezone.utc).isoformat()
            self.requests += 1
            self.puts += operation in {'PutObject', 'UploadPart'}
            self.gets += operation == 'GetObject'
            self.heads += operation == 'HeadObject'
            self.lists += operation == 'ListObjectsV2'
            # AWS checksum framing can omit Content-Length. In that case count
            # the declared decoded body and explicitly report that limitation.
            length = _length(request.headers.get('Content-Length'))
            if length is None:
                length = _length(request.headers.get('X-Amz-Decoded-Content-Length'))
                if length is not None:
                    self.decoded_lengths += 1
            if length is None and request.body is None:
                length = 0
            if length is None:
                self.unknown_out += 1
            else:
                self.out_bytes += length
        except Exception:
            # Diagnostics must never alter transport or mask its primary error.
            self.unknown_out += 1

    def after_call(self, http_response, model=None, **_kwargs):
        try:
            self.responses += 1
            self.failures += http_response.status_code >= 400
            operation = getattr(model, 'name', '')
            # HEAD Content-Length describes the object, not a received body.
            length = 0 if operation == 'HeadObject' else _length(http_response.headers.get('content-length'))
            if length is None:
                self.unknown_in += 1
            else:
                self.in_bytes += length
        except Exception:
            self.unknown_in += 1


def attach(client):
    """Observe supported SDK events without changing client/stream ownership."""
    try:
        if isinstance(getattr(client, '_neo_archive_transfer_meter', None), _Meter):
            return client
        meter = _Meter()
        client.meta.events.register('before-send.s3.*', meter.before_send)
        client.meta.events.register('after-call.s3.*', meter.after_call)
        client._neo_archive_transfer_meter = meter
    except Exception:
        pass
    return client


def emit(client, component):
    """At most one aggregate line per owned client; no credentials or records."""
    try:
        meter = getattr(client, '_neo_archive_transfer_meter', None)
        if not isinstance(meter, _Meter) or meter.emitted or not meter.requests:
            return
        meter.emitted = True
        component = component if component in {'frost_archive', 'spear_archive'} else 'archive'
        # Match existing bounded memory diagnostics: reliable worker stderr,
        # independent of Flask/root logger configuration. Only fixed fields.
        print(f'INFO NeoApps archive_transfer component={component} first_request_utc={meter.first_request_utc} requests={meter.requests} '
              f'puts={meter.puts} gets={meter.gets} heads={meter.heads} lists={meter.lists} '
              f'declared_out_body_bytes={meter.out_bytes} declared_in_body_bytes={meter.in_bytes} '
              f'responses={meter.responses} failures={meter.failures} unknown_out_lengths={meter.unknown_out} '
              f'unknown_in_lengths={meter.unknown_in} decoded_out_lengths={meter.decoded_lengths}',
              file=sys.stderr, flush=True)
    except Exception:
        pass
