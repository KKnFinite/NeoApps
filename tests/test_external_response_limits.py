"""No network: realistic stream boundaries for urllib provider adapters."""
import gzip
import io
import json
import unittest
import http.client
import zlib
from datetime import datetime
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from flask import Flask
from app.services import bounded_http, flight_api, neosubzero_weather as weather


class Response(io.BytesIO):
    def __init__(self, raw, headers=None, chunk=None):
        super().__init__(raw)
        self.headers = headers or {}
        self.chunk = chunk
        self.requested = []

    def read(self, size=-1):
        assert size >= 0, 'Unbounded provider read'
        self.requested.append(size)
        return super().read(min(size, self.chunk) if self.chunk else size)


class ExternalResponseLimitsTest(unittest.TestCase):
    def test_exact_limit_and_partial_reads(self):
        for chunk in (None, 1, 7):
            with Response(b'{"a":1}', chunk=chunk) as response:
                self.assertEqual(bounded_http.read_json(response, maximum=7), {'a': 1})
                self.assertLessEqual(max(response.requested), 8)

    def test_oversized_body_reads_only_limit_plus_one(self):
        with Response(b'x' * 100000, chunk=3) as response:
            with self.assertRaises(bounded_http.ExternalResponseError):
                bounded_http.read_json(response, maximum=10)
            self.assertEqual(response.tell(), 11)

    def test_large_declared_length_rejected_without_read(self):
        with Response(b'{}', {'Content-Length': '9999999'}) as response:
            with self.assertRaises(bounded_http.ExternalResponseError):
                bounded_http.read_json(response, maximum=10)
            self.assertEqual(response.requested, [])

    def test_malformed_truncated_and_invalid_structures(self):
        for raw, headers in ((b'{', {}), (b'{}', {'Content-Length': '3'}),
                             (b'{}', {'Content-Length': 'bad'}), (b'NaN', {}),
                             (b'\xff', {}), (b'{}', {'Content-Encoding': 'br'})):
            with self.subTest(raw=raw, headers=headers), Response(raw, headers) as response:
                with self.assertRaises(bounded_http.ExternalResponseError):
                    bounded_http.read_json(response, maximum=100)

    def test_compressed_limits_integrity_and_normal_decoding(self):
        raw = json.dumps({'value': 'a' * 1000}).encode()
        for body, limit, valid in ((gzip.compress(raw), 2000, True),
                                  (gzip.compress(raw), 100, False),
                                  (gzip.compress(raw)[:-1], 2000, False),
                                  (gzip.compress(raw) + b'trailing', 2000, False)):
            with self.subTest(limit=limit, valid=valid), Response(body, {'Content-Encoding': 'gzip'}) as response:
                if valid:
                    self.assertEqual(bounded_http.read_json(response, maximum=limit)['value'], 'a' * 1000)
                else:
                    with self.assertRaises(bounded_http.ExternalResponseError):
                        bounded_http.read_json(response, maximum=limit)

    def test_deflate_and_midstream_failure_are_bounded(self):
        with Response(zlib.compress(b'{"value":1}'), {'Content-Encoding': 'deflate'}) as response:
            self.assertEqual(bounded_http.read_json(response, maximum=100), {'value': 1})
        response = Response(b'{}')
        with response, patch.object(response, 'read', side_effect=http.client.IncompleteRead(b'{', 1)):
            with self.assertRaises(bounded_http.ExternalResponseError):
                bounded_http.read_json(response, maximum=100)
        self.assertTrue(response.closed)

    def test_flight_success_failure_and_stream_closure(self):
        args = ('RFD', datetime(2026, 11, 1), datetime(2026, 11, 2), 'secret-key')
        for payload, valid in ((b'{"arrivals":[],"departures":[]}', True),
                               (b'[]', True), (b'{"error":"unavailable"}', False),
                               (b'{"arrivals":{}}', False), (b'[1]', False),
                               (b'{', False), (b'null', False), (b'x' * 101, False)):
            response = Response(payload, chunk=2)
            with patch.object(flight_api, 'urlopen', return_value=response), patch.object(flight_api, 'MAX_FIDS_RESPONSE_BYTES', 100):
                if valid:
                    flight_api.RapidApiFlightClient().fetch_fids(*args)
                else:
                    with self.assertRaises(flight_api.FlightApiProviderError):
                        flight_api.RapidApiFlightClient().fetch_fids(*args)
            self.assertTrue(response.closed)

    def test_provider_http_errors_close_and_redact(self):
        body = io.BytesIO(b'secret-key token=private')
        error = HTTPError('https://example.test', 403, 'Forbidden', {}, body)
        with patch.object(flight_api, 'urlopen', side_effect=error):
            with self.assertRaises(flight_api.FlightApiProviderError) as caught:
                flight_api.RapidApiFlightClient().fetch_fids('RFD', datetime(2026, 11, 1), datetime(2026, 11, 2), 'secret-key')
        self.assertTrue(body.closed)
        self.assertNotIn('secret-key', str(caught.exception.diagnostics))

    def test_weather_read_errors_and_timeout(self):
        app = Flask(__name__)
        with app.app_context():
            for raw in (b'{}', b'[]', b'null', b'{', b'x' * 11):
                response = Response(raw, chunk=1)
                with patch.object(weather, 'urlopen', return_value=response):
                    if raw in (b'{}', b'[]'):
                        weather._request_json('https://example.test', max_bytes=10)
                    else:
                        with self.assertRaises(weather.NeoSubZeroWeatherError):
                            weather._request_json('https://example.test', max_bytes=10)
                self.assertTrue(response.closed)
            for error in (TimeoutError(), URLError('offline')):
                with patch.object(weather, 'urlopen', side_effect=error):
                    with self.assertRaises(weather.NeoSubZeroWeatherError):
                        weather._request_json('https://example.test')
            body = io.BytesIO(b'private')
            with patch.object(weather, 'urlopen', side_effect=HTTPError('https://example.test', 503, 'Unavailable', {}, body)):
                with self.assertRaises(weather.NeoSubZeroWeatherError):
                    weather._request_json('https://example.test')
            self.assertTrue(body.closed)

    def test_endpoint_specific_limits_and_shapes(self):
        points = {'properties': {'forecastHourly': 'https://api.weather.gov/gridpoints/LOT/1,1/forecast/hourly',
                                  'forecastGridData': 'https://api.weather.gov/gridpoints/LOT/1,1'}}
        weather.clear_neosubzero_weather_cache()
        with patch.object(weather, '_request_json', side_effect=[points, {'properties': {'periods': [{}]}}, {'properties': {}}]) as request:
            weather._fetch_forecast(now=datetime(2026, 11, 1))
        self.assertEqual([call.kwargs['max_bytes'] for call in request.call_args_list],
                         [weather.POINTS_MAX_BYTES, weather.HOURLY_MAX_BYTES, weather.GRID_MAX_BYTES])
        for payload in ([{'icaoId': 'KORD'}], [{}], [], {}):
            with patch.object(weather, '_request_json', return_value=payload):
                with self.assertRaises(weather.NeoSubZeroWeatherError):
                    weather._fetch_metar()
        with patch.object(weather, '_request_json', return_value=[{'icaoId': 'KRFD'}]) as request:
            self.assertEqual(weather._fetch_metar(), [{'icaoId': 'KRFD'}])
            self.assertEqual(request.call_args.kwargs['max_bytes'], weather.METAR_MAX_BYTES)
        weather.clear_neosubzero_weather_cache()


if __name__ == '__main__':
    unittest.main()
