"""Real requests/gspread buffering boundary; providers are entirely mocked."""
import gzip
import hashlib
import io
import json
import unittest
from urllib.error import HTTPError
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests
from flask import Flask
from gspread.exceptions import APIError
from google.auth.exceptions import TransportError
from requests.adapters import BaseAdapter
from urllib3.response import HTTPResponse

from app.services import google_diagnostic_transport as transport
from app.services import password_policy as policy
from app.services import neorain_google_revision_probe as probe
from app.services import neorain_google_runtime as runtime
from app.services.bounded_http import ExternalResponseError
from app.services.google_motherbrain_sheets import GOOGLE_MOTHERBRAIN_WRITE_SCOPES
from app.services.neorain_google_overrides import MB, certified_write, GoogleOverrideBlocked


class TrackedBody(io.BytesIO):
    def __init__(self, body):
        super().__init__(body)
        self.bytes_read = 0
        self.read_sizes = []

    def read(self, size=-1):
        if size < 0:
            raise AssertionError('Unbounded transport read')
        self.read_sizes.append(size)
        result = super().read(size)
        self.bytes_read += len(result)
        return result


class ProviderAdapter(BaseAdapter):
    def __init__(self, body, headers=None, status=200):
        self.body = TrackedBody(body)
        self.headers = headers or {'Content-Type': 'application/json'}
        self.status = status
        self.calls = []
        self.response = None

    def send(self, request, **kwargs):
        self.calls.append((request, kwargs))
        response = requests.Response()
        response.status_code = self.status
        response.headers.update(self.headers)
        response.request, response.url = request, request.url
        response.raw = HTTPResponse(body=self.body, headers=self.headers,
                                    preload_content=False, decode_content=False)
        response.close = Mock(wraps=response.close)
        self.response = response
        return response

    def close(self):
        pass


class GoogleTransportTest(unittest.TestCase):
    def client(self, body, *, headers=None, maximum=transport.METADATA_MAX_BYTES, status=200):
        session = requests.Session()
        session.request = Mock(wraps=session.request)
        self.addCleanup(session.close)
        adapter = ProviderAdapter(body, headers=headers, status=status)
        session.mount('https://', adapter)
        client = transport.diagnostic_http_client(None, session, maximum=maximum)
        client.set_timeout(5)
        return client, adapter, session

    def test_real_gspread_reads_json_before_requests_default_buffering(self):
        client, provider, session = self.client(b'{"id":"fixed","capabilities":{"canEdit":true}}')
        response = client.request('get', 'https://provider.invalid/metadata')
        self.assertTrue(response.json()['capabilities']['canEdit'])
        self.assertTrue(provider.calls[0][1]['stream'])
        self.assertFalse(session.request.call_args.kwargs['allow_redirects'])
        self.assertEqual(provider.calls[0][1]['timeout'], 5)
        self.assertEqual(provider.calls[0][0].headers['Accept-Encoding'],'gzip, deflate')
        provider.response.close.assert_called_once()
        self.assertTrue(provider.body.closed)

    def test_exact_limit_and_one_over_reject_before_entire_body_is_buffered(self):
        cap = transport.METADATA_MAX_BYTES
        exact = b'{"value":"' + b'a' * (cap-12) + b'"}'
        self.assertEqual(len(exact), cap)
        client, provider, _ = self.client(exact)
        self.assertEqual(len(client.request('get', 'https://provider.invalid/metadata').content), cap)
        self.assertEqual(provider.body.bytes_read, cap)
        for payload in (exact+b' ', b'x'*(cap*4)):
            with self.subTest(size=len(payload)):
                client, provider, _ = self.client(payload)
                with self.assertRaises(ExternalResponseError):
                    client.request('get', 'https://provider.invalid/metadata')
                self.assertEqual(provider.body.bytes_read, cap+1)
                provider.response.close.assert_called_once()
                self.assertTrue(provider.body.closed)

    def test_compressed_expansion_and_truncation_are_rejected_and_closed(self):
        for body in (gzip.compress(b'x'*(1024*1024)), gzip.compress(b'{"a":1}')[:-4]):
            client, provider, _ = self.client(body, headers={'Content-Encoding':'gzip'}, maximum=1024)
            with self.assertRaises(ExternalResponseError):
                client.request('get','https://provider.invalid/metadata')
            self.assertLessEqual(provider.body.bytes_read,1025)
            provider.response.close.assert_called_once()
        client, provider, _ = self.client(b'{}', headers={'Content-Length':'4'})
        with self.assertRaises(ExternalResponseError):
            client.request('get','https://provider.invalid/metadata')
        provider.response.close.assert_called_once()

    def test_malformed_json_and_google_permission_error_close_bodies(self):
        client, provider, _ = self.client(b'{"id":')
        with self.assertRaises(ValueError):
            client.request('get','https://provider.invalid/metadata').json()
        provider.response.close.assert_called_once()
        client, provider, _ = self.client(b'{"error":{"code":403,"message":"denied"}}',status=403)
        with self.assertRaises(APIError):
            client.request('get','https://provider.invalid/metadata')
        provider.response.close.assert_called_once()

    def test_event_stream_and_oversized_event_stream_use_same_transport_bound(self):
        body=b'data: '+json.dumps({'id':1,'result':{'structuredContent':{'spreadsheetId':MB}}}).encode()+b'\n\n'
        client, provider, _ = self.client(body,headers={'Content-Type':'text/event-stream'},maximum=probe.MCP_MAX_BYTES)
        self.assertEqual(probe._payload(client.request('post',probe.MCP_ENDPOINT))['id'],1)
        provider.response.close.assert_called_once()
        client, provider, _ = self.client(body+b':'+b'x'*(probe.MCP_MAX_BYTES*4),headers={'Content-Type':'text/event-stream'},maximum=probe.MCP_MAX_BYTES)
        with self.assertRaises(ExternalResponseError):
            client.request('post',probe.MCP_ENDPOINT)
        self.assertEqual(provider.body.bytes_read,probe.MCP_MAX_BYTES+1)
        provider.response.close.assert_called_once()

    def test_owned_and_borrowed_sessions_and_factory_failure(self):
        session=requests.Session()
        session.close=Mock(wraps=session.close)
        with patch.object(transport,'AuthorizedSession',return_value=session):
            client=transport.diagnostic_http_client(None)
            transport.close_google_clients([SimpleNamespace(http_client=client)]*2)
            client.session.close()
        session.close.assert_called_once()
        borrowed=requests.Session()
        self.addCleanup(borrowed.close)
        borrowed.close=Mock(wraps=borrowed.close)
        client=transport.diagnostic_http_client(None,borrowed)
        client.session.close()
        borrowed.close.assert_not_called()
        session=requests.Session();session.close=Mock(wraps=session.close)
        with patch.object(transport,'AuthorizedSession',return_value=session), \
                patch.object(transport,'DiagnosticHTTPClient',side_effect=RuntimeError('primary')):
            with self.assertRaisesRegex(RuntimeError,'primary'):
                transport.diagnostic_http_client(None)
        session.close.assert_called_once()

    def test_nonfinite_or_nonobject_metadata_is_rejected(self):
        for body in (b'{"value":NaN}', b'[]'):
            client, provider, _ = self.client(body)
            with self.assertRaises(ExternalResponseError):
                client.request('get','https://provider.invalid/metadata')
            provider.response.close.assert_called_once()

    def test_redirect_is_blocked_without_following_or_double_closing(self):
        client,provider,_=self.client(b'{}',headers={'Location':'https://other.invalid'},status=302)
        with self.assertRaises(ExternalResponseError):
            client.request('get','https://provider.invalid/metadata')
        self.assertEqual(len(provider.calls),1)
        provider.response.close.assert_called_once()

    def test_revision_probe_cannot_certify_oversized_response_and_closes_owned_client(self):
        body=json.dumps({'id':1,'result':{'structuredContent':{'spreadsheetId':MB,'revisionId':'fixture'}}}).encode()
        for oversized in (False,True):
            session=requests.Session();session.close=Mock(wraps=session.close)
            provider=ProviderAdapter(b'x'*(probe.MCP_MAX_BYTES*4) if oversized else body)
            session.mount('https://',provider)
            auth=Mock()
            with patch.object(transport,'AuthorizedSession',return_value=session):
                client=SimpleNamespace(http_client=transport.diagnostic_http_client(auth,maximum=probe.MCP_MAX_BYTES))
            client.http_client.set_timeout(5)
            with patch.object(probe,'_credential_json',return_value=('fixture','fallback')), \
                 patch.object(probe,'_parse_service_account_json',return_value={}), \
                 patch.object(probe,'_create_gspread_writer',return_value=client) as factory:
                result=probe.probe_revision_fence({})
            self.assertEqual(result['status'],'BLOCKED' if oversized else 'CANDIDATE_ONLY')
            self.assertEqual(result['operational_writes'],0)
            self.assertEqual(factory.call_args.kwargs['http_client'].keywords['maximum'],probe.MCP_MAX_BYTES)
            self.assertEqual(provider.body.bytes_read,probe.MCP_MAX_BYTES+1 if oversized else len(body))
            provider.response.close.assert_called_once()
            session.close.assert_called_once()

    def test_oauth_refresh_uses_bounded_closed_session(self):
        session=requests.Session();session.close=Mock(wraps=session.close)
        provider=ProviderAdapter(b'{"access_token":"synthetic","expires_in":300}')
        session.mount('https://',provider)
        with patch.object(transport,'Session',return_value=session):
            response=transport.bounded_auth_request('https://provider.invalid/token',timeout=900)
        self.assertIn(b'access_token',response.data)
        self.assertEqual(provider.calls[0][1]['timeout'],5)
        provider.response.close.assert_called_once()
        session.close.assert_called_once()

    def test_oauth_timeout_still_closes_owned_session(self):
        session=requests.Session();session.close=Mock(wraps=session.close)
        session.request=Mock(side_effect=requests.Timeout('private provider detail'))
        with patch.object(transport,'Session',return_value=session):
            with self.assertRaises(TransportError):
                transport.bounded_auth_request('https://provider.invalid/token')
        session.close.assert_called_once()


class HibpBoundTest(unittest.TestCase):
    def test_http_error_stream_is_closed_and_local_policy_remains_enforced(self):
        body=io.BytesIO(b'private provider error')
        error=HTTPError('https://provider.invalid',503,'unavailable',{},body)
        with patch.object(policy,'_hibp_check_enabled',return_value=True), \
             patch.object(policy,'urlopen',side_effect=error):
            policy.validate_password('cobalt meadow lantern phrase')
        self.assertTrue(body.closed)

    def test_bounds_and_advisory_failure_preserve_local_policy(self):
        password='cobalt meadow lantern phrase'
        valid=(b'A'*35+b':1\r\n')
        for body,cap,headers,expected in (
            (valid,len(valid),{},len(valid)),
            (valid+b'x',len(valid),{},len(valid)+1),
            (b'x'*(policy.HIBP_MAX_RESPONSE_BYTES*4),policy.HIBP_MAX_RESPONSE_BYTES,{},policy.HIBP_MAX_RESPONSE_BYTES+1),
            (valid[:-3],256,{'Content-Length':str(len(valid))},len(valid)-3),
            (b'not a range',256,{},11),
        ):
            with self.subTest(size=len(body),cap=cap):
                stream=TrackedBody(body)
                response=Mock(status=200,headers=headers)
                response.read.side_effect=stream.read
                response.__enter__=Mock(return_value=response)
                response.__exit__=Mock(side_effect=lambda *_args:stream.close())
                with patch.object(policy,'_hibp_check_enabled',return_value=True), \
                     patch.object(policy,'HIBP_MAX_RESPONSE_BYTES',cap), \
                     patch.object(policy,'urlopen',return_value=response):
                    policy.validate_password(password)
                    with self.assertRaises(policy.PasswordPolicyError):
                        policy.validate_password('password12345')
                self.assertEqual(stream.bytes_read,expected)
                self.assertTrue(stream.closed)
                response.__exit__.assert_called_once()

    def test_full_ascii_validation_including_trailing_invalid_data_and_padding(self):
        suffix=hashlib.sha1(b'cobalt meadow lantern phrase').hexdigest().upper()[5:]
        self.assertFalse(policy._hibp_response_contains_suffix(suffix+':0',suffix))
        self.assertTrue(policy._hibp_response_contains_suffix(suffix+':2\r\n'+suffix+':0',suffix))
        with self.assertRaises(ValueError):
            policy._hibp_response_contains_suffix(suffix+':2\r\ntruncated',suffix)


class RuntimeOwnershipTest(unittest.TestCase):
    def factories(self):
        writer,metadata=Mock(),Mock()
        writer.http_client.auth.scopes=GOOGLE_MOTHERBRAIN_WRITE_SCOPES
        config={'GOOGLE_SERVICE_ACCOUNT_JSON':json.dumps({'client_email':'fixture@example.test',
                'private_key':'synthetic','token_uri':'https://oauth2.googleapis.com/token'})}
        return writer,metadata,config

    def test_partial_construction_and_cleanup_failure_preserve_primary_error(self):
        writer,metadata,config=self.factories()
        writer.http_client.session.close.side_effect=RuntimeError('private cleanup detail')
        with patch.object(runtime,'_create_gspread_writer',return_value=writer), \
             patch.object(runtime,'_create_gspread',side_effect=ValueError('primary')):
            with self.assertRaisesRegex(ValueError,'primary'):
                runtime.RuntimeGoogleAdapter(config)
        writer.http_client.session.close.assert_called_once()

    def test_factory_closes_created_client_if_timeout_configuration_fails(self):
        from app.services import google_motherbrain_sheets as sheets
        client=Mock()
        client.set_timeout.side_effect=ValueError('private provider detail')
        with patch.object(sheets.gspread,'service_account_from_dict',return_value=client):
            with self.assertRaises(sheets.GoogleMotherBrainReaderError):
                sheets._create_gspread({},GOOGLE_MOTHERBRAIN_WRITE_SCOPES,{},
                                        http_client=transport.diagnostic_http_client)
        client.http_client.session.close.assert_called_once()

    def test_scope_closes_success_failure_and_shared_session_once(self):
        for failed,same in ((False,False),(True,False),(False,True)):
            writer,metadata,config=self.factories()
            if same: metadata=writer
            with patch.object(runtime,'_create_gspread_writer',return_value=writer), \
                 patch.object(runtime,'_create_gspread',return_value=metadata):
                try:
                    with runtime.RuntimeGoogleAdapter(config) as adapter:
                        if failed:raise ValueError('primary')
                except ValueError:
                    self.assertTrue(failed)
                adapter.close()
            writer.http_client.session.close.assert_called_once()
            metadata.http_client.session.close.assert_called_once()

    def test_default_uncertified_adapter_is_owned_but_configured_adapter_is_borrowed(self):
        writer,metadata,config=self.factories()
        app=Flask(__name__);app.config.update(config)
        with app.app_context(), patch.object(runtime,'_create_gspread_writer',return_value=writer), \
             patch.object(runtime,'_create_gspread',return_value=metadata):
            with self.assertRaises(GoogleOverrideBlocked):
                certified_write(Mock(),'sta',{})
        writer.http_client.session.close.assert_called_once()
        metadata.http_client.session.close.assert_called_once()
        borrowed=Mock(certified_fields={'sta'})
        borrowed.permission_verified.return_value=False
        app.config['NEORAIN_CERTIFIED_GOOGLE_ADAPTER']=borrowed
        with app.app_context(),self.assertRaises(GoogleOverrideBlocked):
            certified_write(Mock(),'sta',{})
        borrowed.close.assert_not_called()
