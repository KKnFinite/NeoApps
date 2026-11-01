"""Finite urllib response reads; callers retain ownership of response streams.

Both the wire representation and the decoded body are limited. urllib does not
automatically decompress Content-Encoding. Never use an unbounded read or a
decompress/flush operation without an output limit here.
"""
import http.client
import json
import zlib


class ExternalResponseError(ValueError):
    """Safe, payload-free description of an invalid provider response."""


def read_body(response, *, maximum):
    if not isinstance(maximum, int) or maximum <= 0:
        raise ValueError('A positive response limit is required.')
    headers = getattr(response, 'headers', None) or {}
    encoding = str(headers.get('Content-Encoding', 'identity')).strip().lower()
    if encoding not in ('identity', '', 'gzip', 'deflate'):
        raise ExternalResponseError('Provider response encoding is unsupported.')
    length = headers.get('Content-Length')
    try:
        length = int(length) if length is not None else None
    except (TypeError, ValueError):
        raise ExternalResponseError('Provider response length is invalid.') from None
    if length is not None and (length < 0 or length > maximum):
        raise ExternalResponseError('Provider response exceeds the size limit.')
    raw = bytearray()
    try:
        while len(raw) <= maximum:
            chunk = response.read(min(64 * 1024, maximum + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
    except http.client.HTTPException:
        raise ExternalResponseError('Provider response is incomplete.') from None
    if len(raw) > maximum:
        raise ExternalResponseError('Provider response exceeds the size limit.')
    if length is not None and len(raw) != length:
        raise ExternalResponseError('Provider response is incomplete.')
    if encoding in ('gzip', 'deflate'):
        try:
            decoder = zlib.decompressobj(31 if encoding == 'gzip' else 15)
            decoded = decoder.decompress(raw, maximum + 1)
            if len(decoded) > maximum or decoder.unconsumed_tail:
                raise ExternalResponseError('Decoded provider response exceeds the size limit.')
            if not decoder.eof or decoder.unused_data:
                raise ExternalResponseError('Compressed provider response is incomplete or invalid.')
            return decoded
        except zlib.error:
            raise ExternalResponseError('Compressed provider response is invalid.') from None
    return bytes(raw)


def read_json(response, *, maximum):
    raw = read_body(response, maximum=maximum)
    try:
        return json.loads(raw.decode('utf-8'), parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        raise ExternalResponseError('Provider returned invalid JSON.') from None


def _reject_constant(_value):
    raise ValueError('Nonfinite JSON value.')
