import json
import unittest
from unittest.mock import Mock, patch

from app.services.neorain_google_revision_probe import probe_revision_fence, _payload
from app.services.neorain_google_overrides import MB


class RevisionProbeTest(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.response = self.client.http_client.request.return_value
        self.response.headers = {'Content-Type': 'application/json'}
        self.response.content = b'{}'
        self.loader = patch('app.services.neorain_google_revision_probe._credential_json', return_value=('existing', 'fallback'))
        self.parser = patch('app.services.neorain_google_revision_probe._parse_service_account_json', return_value={})
        self.factory = patch('app.services.neorain_google_revision_probe._create_gspread_writer', return_value=self.client)
        for item in (self.loader, self.parser, self.factory):
            item.start(); self.addCleanup(item.stop)

    def snapshot(self, **extra):
        self.response.json.return_value = {'jsonrpc': '2.0', 'id': 1, 'result': {
            'structuredContent': {'spreadsheetId': MB, **extra}}}

    def test_fixed_read_only_request_and_revision_is_not_activation(self):
        self.snapshot(revisionId='snapshot-revision')
        result = probe_revision_fence({})
        self.assertEqual(result['status'], 'CANDIDATE_ONLY')
        self.assertEqual(result['operational_writes'], 0)
        call = self.client.http_client.request.call_args
        self.assertEqual(call.kwargs['json']['params']['name'], 'get_spreadsheet')
        self.assertEqual(call.kwargs['json']['params']['arguments'], {
            'spreadsheetId': MB, 'includeGridData': False, 'ranges': ['Inbound!AB4']})
        self.assertNotIn('snapshot-revision', json.dumps(result))
        self.client.http_client.request.assert_called_once()

    def test_etag_version_or_missing_revision_never_qualifies(self):
        for data in ({}, {'revisionId': ''}, {'revisionId': 12}, {'etag': '12', 'version': '12'}):
            self.snapshot(**data)
            self.assertFalse(probe_revision_fence({})['revision_snapshot_available'])

    def test_rejection_timeout_and_tool_error_are_sanitized_and_not_retried(self):
        self.client.http_client.request.side_effect = TimeoutError('PRIVATE SECRET')
        result = probe_revision_fence({})
        self.assertEqual(result['status'], 'BLOCKED')
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.client.http_client.request.assert_called_once()
        self.client.http_client.request.side_effect = None
        self.response.json.return_value = {'id': 1, 'result': {'isError': True, 'content': [{'text': 'PRIVATE'}]}}
        self.assertNotIn('PRIVATE', json.dumps(probe_revision_fence({})))

    def test_sse_snapshot_and_ambiguous_events(self):
        self.response.headers = {'Content-Type': 'text/event-stream'}
        value = {'id': 1, 'result': {'content': [{'type': 'text', 'text': json.dumps({'spreadsheetId': MB})}]}}
        self.response.text = 'event: message\ndata: ' + json.dumps(value) + '\n\n'
        self.response.content = self.response.text.encode()
        self.assertEqual(_payload(self.response), value)
        self.assertEqual(probe_revision_fence({})['status'], 'BLOCKED')
        self.response.text *= 2
        with self.assertRaises(ValueError): _payload(self.response)
