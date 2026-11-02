"""Bounded read-only check for Google's revision-conditional Sheets MCP API.

This is not a credential audit or a writer. Never infer a revision from an ETag,
Drive version or timestamp; only a revision returned with the inspected snapshot
could qualify for a subsequent requiredRevisionId operation.
"""
import json
from functools import partial

from app.services.google_motherbrain_sheets import (
    _credential_json, _parse_service_account_json, _create_gspread_writer,
)
from app.services.neorain_google_overrides import MB
from app.services.google_diagnostic_transport import (
    diagnostic_http_client, bounded_auth_request, close_google_clients,
)

MCP_ENDPOINT = 'https://sheetsmcp.googleapis.com/mcp/v1'
MCP_MAX_BYTES = 256 * 1024


def _invalid_constant(_value):
    raise ValueError('Invalid JSON constant')


def _payload(response):
    """Accept the two documented Streamable HTTP response content types."""
    # The diagnostic HTTP transport bounded/closed the raw stream already.
    # Keep a parser bound too for supplied fixtures and future callers.
    raw = response.content
    if len(raw) > MCP_MAX_BYTES:
        raise ValueError('Oversized metadata response')
    text = raw.decode('utf-8-sig')
    def decode(value):
        body = json.loads(value, parse_constant=_invalid_constant)
        if not isinstance(body, dict):
            raise ValueError('Invalid MCP object')
        return body
    if 'text/event-stream' not in response.headers.get('Content-Type', '').lower():
        return decode(text)
    # Retain only the matching response, not every heartbeat/event object.
    match = None
    events = text.replace('\r\n', '\n').split('\n\n')
    if any(line.startswith('data:') for line in events[-1].splitlines()):
        raise ValueError('Incomplete MCP event')
    for event in events[:-1]:
        data = '\n'.join(line[5:].lstrip() for line in event.splitlines() if line.startswith('data:'))
        if data:
            message = decode(data)
            if message.get('id') == 1:
                if match is not None:
                    raise ValueError('Ambiguous MCP response')
                match = message
    if match is None:
        raise ValueError('Ambiguous MCP response')
    return match


def probe_revision_fence(config):
    """Exactly one read-tool call, fixed workbook/range, sanitized output only."""
    result = {'probe': 'neorain_revision_fence_v1', 'status': 'BLOCKED',
              'operational_writes': 0, 'revision_snapshot_available': False}
    client = None
    try:
        raw, _source = _credential_json(config)
        client = _create_gspread_writer(_parse_service_account_json(raw),
            {**config, 'GOOGLE_SHEETS_REQUEST_TIMEOUT_SECONDS': 5},
            http_client=partial(diagnostic_http_client, maximum=MCP_MAX_BYTES))
        # Bound initial OAuth refresh too; gspread's API timeout alone does not.
        client.http_client.auth.refresh(bounded_auth_request)
        response = client.http_client.request('post', MCP_ENDPOINT,
            headers={'Accept': 'application/json, text/event-stream'}, json={
                'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                'params': {'name': 'get_spreadsheet', 'arguments': {
                    'spreadsheetId': MB, 'includeGridData': False,
                    'ranges': ['Inbound!AB4'],
                }},
            })
        body = _payload(response)
        if body.get('id') != 1 or body.get('error'):
            return {**result, 'reason': 'MCP read rejected or unsupported.'}
        tool_result = body.get('result', {})
        if tool_result.get('isError'):
            return {**result, 'reason': 'MCP read tool unavailable; no mutation attempted.'}
        snapshot = tool_result.get('structuredContent')
        if snapshot is None:
            texts = [part.get('text', '') for part in tool_result.get('content', [])
                     if part.get('type') == 'text']
            if len(texts) != 1:
                raise ValueError('Missing single snapshot')
            snapshot = json.loads(texts[0], parse_constant=_invalid_constant)
        if not isinstance(snapshot, dict) or snapshot.get('spreadsheetId') != MB:
            return {**result, 'reason': 'MCP did not return the expected spreadsheet snapshot.'}
        revision = snapshot.get('revisionId')
        available = isinstance(revision, str) and bool(revision.strip())
        return {**result, 'revision_snapshot_available': available,
                'status': 'CANDIDATE_ONLY' if available else 'BLOCKED',
                'reason': ('Snapshot revision returned; isolated stale-revision and formula/reorder tests still required.'
                           if available else 'MCP read returned no snapshot revision. Conditional write cannot be safely bound to this read.')}
    except Exception as error:
        # Provider exception text/response bodies may contain identifiers or secrets.
        response = getattr(error, 'response', None)
        status = getattr(response, 'status_code', None)
        return {**result, 'http_status': status if type(status) is int else None,
                'reason': 'Revision-capable MCP read unavailable; no mutation attempted.'}
    finally:
        if client is not None:
            close_google_clients([client])


def report_deployment_revision_probe(config):
    """Non-blocking, metadata-only pre-deploy evidence; no app startup hook."""
    print('NeoRain write-fence capability: ' + json.dumps(probe_revision_fence(config), sort_keys=True), flush=True)
