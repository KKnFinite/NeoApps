"""Reliable one-cell mirror: Night / Ramp / Shift HERE -> Raiden Display!M23.

Canonical NeoStaffing Attendance always wins. Neither attendance requests nor
database commits perform Google I/O. This job shares the existing bounded
background-deadline worker but has an independent receipt from Hours Log sync.
"""
from datetime import datetime, timedelta, timezone
from functools import partial
from uuid import uuid4

from flask import current_app, has_app_context
from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session

from app.extensions import db
from app.models.staffing_raiden_sync import StaffingRaidenSync as Receipt

RAIDEN_WORKBOOK_ID = '1geZ6x1fJUPu7cDF9LDlFmX6FhwE25NqrXkqe5Ire-qM'
DISPLAY_SHEET_ID = 1866078079
DISPLAY_TITLE = 'Display'
DESTINATION_ROW_INDEX = 22  # M23
DESTINATION_COLUMN_INDEX = 12
STOCK_SORT_NAME = 'night'


class RaidenSyncError(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


CHECK_MINUTES = 5


def next_check(at):
    """Next shared five-minute UTC boundary; does not drift with supervisor saves."""
    return (at.replace(minute=at.minute - at.minute % CHECK_MINUTES,
                       second=0, microsecond=0)
            + timedelta(minutes=CHECK_MINUTES))


def enabled():
    override = current_app.config.get('NEOSTAFFING_RAIDEN_SYNC_ENABLED')
    if override is not None:
        return override is True
    return not current_app.testing and db.engine.dialect.name == 'postgresql'


def current_operation():
    from app.services import neostaffing as staffing
    operation = staffing.current_night_attendance_operation()
    if not operation or str(operation.gateway_code).upper() != 'RFD':
        return None
    if staffing._normalize_staffing_sort_name(operation.sort_name) != STOCK_SORT_NAME:
        return None
    return operation


def source_snapshot():
    """Reuse the exact active-assignment and attendance rules of the Attendance UI."""
    from app.services import neostaffing as staffing
    operation = current_operation()
    if operation is None:
        return None
    hierarchy = staffing._daily_attendance_hierarchy()
    staffing_sort = staffing._staffing_sort_for_operation(operation, hierarchy)
    by_id = hierarchy['by_id']
    scopes = [
        unit for unit in hierarchy['units']
        if unit.unit_type == 'department'
        and unit.name.strip().casefold() == 'shift'
        and (parent := by_id.get(unit.parent_id)) is not None
        and parent.unit_type == 'operation'
        and parent.name.strip().casefold() == 'ramp'
        and parent.parent_id == staffing_sort.id
    ]
    if len(scopes) != 1:
        raise RaidenSyncError('Night / Ramp / Shift scope missing or ambiguous.')
    assignments = staffing._daily_attendance_assignments(scopes[0], staffing_sort, hierarchy)
    by_person = {row.person_id: row for row in assignments}
    if len(by_person) != len(assignments):
        raise RaidenSyncError('Duplicate active Shift assignment; no Raiden write.')
    records = staffing._daily_attendance_records(list(by_person), operation, staffing_sort)
    rows = staffing._daily_attendance_rows(list(by_person.values()), records, hierarchy)
    return operation.id, staffing._daily_attendance_summary(rows)['here']


def _cell_map(metadata):
    if metadata.get('spreadsheetId') != RAIDEN_WORKBOOK_ID:
        raise RaidenSyncError('Wrong Raiden workbook; no write.')
    sheet = next((s for s in metadata.get('sheets', [])
                  if s.get('properties', {}).get('sheetId') == DISPLAY_SHEET_ID), None)
    if not sheet or sheet.get('properties', {}).get('title') != DISPLAY_TITLE:
        raise RaidenSyncError('Raiden Display tab changed; no write.')
    cells = {}
    for chunk in sheet.get('data', []):
        for i, row in enumerate(chunk.get('rowData', [])):
            for j, cell in enumerate(row.get('values', [])):
                cells[(chunk.get('startRow', 0) + i, chunk.get('startColumn', 0) + j)] = cell
    def value(row, col):
        return cells.get((row, col), {}).get('userEnteredValue', {})
    if value(22, 11).get('stringValue') != 'SHIFT':
        raise RaidenSyncError('Raiden SHIFT label changed; no write.')
    if value(22, 13).get('formulaValue', '').replace(' ', '') != '=M23+P23':
        raise RaidenSyncError('Raiden total formula changed; no write.')
    if value(22, 14).get('stringValue') != 'RAMP':
        raise RaidenSyncError('Raiden RAMP label changed; no write.')
    target = value(DESTINATION_ROW_INDEX, DESTINATION_COLUMN_INDEX)
    if 'formulaValue' in target or 'stringValue' in target or 'boolValue' in target:
        raise RaidenSyncError('Raiden M23 is not a writable numeric cell.')
    number = target.get('numberValue')
    if number is not None and (isinstance(number, bool) or
                               not isinstance(number, (int, float)) or
                               int(number) != number):
        raise RaidenSyncError('Raiden M23 is not an integer.')
    return None if number is None else int(number)


class GoogleAdapter:
    def __init__(self):
        from app.services.google_motherbrain_sheets import (
            _credential_json, _parse_service_account_json, _create_gspread_writer,
        )
        from app.services.google_diagnostic_transport import diagnostic_http_client
        creds, _ = _credential_json(current_app.config)
        self.client = _create_gspread_writer(
            _parse_service_account_json(creds), current_app.config,
            http_client=partial(diagnostic_http_client, maximum=1024 * 1024),
        )

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        from app.services.google_diagnostic_transport import close_google_clients
        close_google_clients([self.client])

    def read(self):
        return self.client.http_client.request(
            'get', f'https://sheets.googleapis.com/v4/spreadsheets/{RAIDEN_WORKBOOK_ID}',
            params={'ranges': "'Display'!L23:P23", 'includeGridData': 'true',
                    'fields': 'spreadsheetId,sheets(properties(sheetId,title),data(startRow,startColumn,rowData(values(userEnteredValue))))'},
        ).json()

    def write(self, count):
        return self.client.http_client.request(
            'post', f'https://sheets.googleapis.com/v4/spreadsheets/{RAIDEN_WORKBOOK_ID}:batchUpdate',
            json={'requests': [{
                'updateCells': {
                    'start': {'sheetId': DISPLAY_SHEET_ID, 'rowIndex': DESTINATION_ROW_INDEX,
                              'columnIndex': DESTINATION_COLUMN_INDEX},
                    'rows': [{'values': [{'userEnteredValue': {'numberValue': count}}]}],
                    'fields': 'userEnteredValue',
                },
            }], 'includeSpreadsheetInResponse': False},
        ).json()


def deliver(count, adapter):
    """Never write another cell; independently verify uncertain or accepted writes."""
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        raise RaidenSyncError('Invalid Night Shift staffing count.')
    before = _cell_map(adapter.read())
    if before != count:
        adapter.write(count)
    if _cell_map(adapter.read()) != count:
        raise RaidenSyncError('Raiden M23 read-back mismatch; retry pending.')


def next_deadline(at):
    """Only local receipt/sort checks; Raiden is contacted by the timed drain."""
    if not enabled() or current_operation() is None:
        return None  # Keep the last value outside an active Night sort.
    receipt = db.session.get(Receipt, 1)
    if receipt is None:
        return at  # Initialize the durable polling clock on the first cycle.
    # The existing shared deadline worker uses this persistent clock,
    # regardless of how many attendance edits have been committed.
    return max(receipt.retry_at, receipt.lease_until or at)


def drain(_gateway_id=None, *, adapter_factory=GoogleAdapter):
    """Check canonical HERE once per interval; Google I/O only on a real change."""
    if not enabled() or current_operation() is None:
        return False
    _dirty(db.session(), initial=True)
    db.session.commit()
    receipt = db.session.execute(
        select(Receipt).where(Receipt.id == 1).with_for_update(skip_locked=True)
    ).scalar_one_or_none()
    at = now()
    if (receipt is None or receipt.retry_at > at
            or receipt.lease_until and receipt.lease_until > at):
        db.session.rollback()
        return False
    last_operation = receipt.delivered_operation_id
    last_count = receipt.delivered_count
    last_verified = receipt.verified_at
    token = uuid4().hex
    receipt.lease_token = token
    receipt.lease_until = at + timedelta(minutes=5)
    receipt.attempts += 1
    db.session.commit()
    revision = None
    snapshot = None
    error = None
    needs_google = False
    try:
        if db.engine.dialect.name == 'postgresql':
            db.session.connection(execution_options={'isolation_level': 'REPEATABLE READ'})
        revision = db.session.get(Receipt, 1, populate_existing=True).requested_revision
        snapshot = source_snapshot()
        if snapshot is None:
            raise RaidenSyncError('Current Night Sort unavailable; no write.')
        needs_google = (last_verified is None or last_operation != snapshot[0]
                        or last_count != snapshot[1])
        db.session.remove()  # No database connection while contacting Google.
        if needs_google:
            with adapter_factory() as adapter:
                deliver(snapshot[1], adapter)
    except Exception as exc:
        error = str(exc) if isinstance(exc, RaidenSyncError) else type(exc).__name__
        current_app.logger.warning('NeoStaffing Raiden M23 sync deferred: %s', error)
    finally:
        db.session.remove()
    receipt = db.session.execute(
        select(Receipt).where(Receipt.id == 1).with_for_update()
    ).scalar_one()
    if receipt.lease_token != token:
        _dirty(db.session())
        db.session.commit()
        return False
    receipt.lease_token = None
    receipt.lease_until = None
    receipt.last_error = error[:100] if error else None
    if error:
        # Never hammer Google on provider failures: 5, 10, 20, then 30 min.
        receipt.retry_at = now() + timedelta(
            minutes=min(30, 5 * 2 ** min(receipt.attempts - 1, 3))
        )
    else:
        receipt.delivered_revision = revision
        receipt.delivered_operation_id = snapshot[0]
        receipt.delivered_count = snapshot[1]
        if needs_google:
            receipt.verified_at = now()
        receipt.attempts = 0
        receipt.retry_at = next_check(now())
    db.session.commit()
    return not error

def _dirty(session, *, initial=False):
    """Commit an intent without moving the already-scheduled five-minute check."""
    dialect = session.bind.dialect.name if session.bind is not None else db.engine.dialect.name
    if dialect == 'postgresql':
        from sqlalchemy.dialects.postgresql import insert
    elif dialect == 'sqlite':
        from sqlalchemy.dialects.sqlite import insert
    else:
        raise RuntimeError('Unsupported Raiden sync database')
    statement = insert(Receipt.__table__).values(
        id=1, requested_revision=1, delivered_revision=0,
        retry_at=next_check(now()), attempts=0
    )
    statement = (statement.on_conflict_do_nothing(index_elements=['id']) if initial
                 else statement.on_conflict_do_update(
                     index_elements=['id'],
                     set_={'requested_revision': Receipt.requested_revision + 1},
                 ))
    session.execute(statement)

_TRACKED = {
    'StaffingDailyAttendance': ('status', 'person_id', 'sort_date_operation_id',
                                'sort_unit_id', 'attendance_date'),
    'StaffingPerson': ('classification', 'active'),
    'StaffingWorkAssignment': ('person_id', 'work_area_unit_id', 'active'),
    'StaffingUnit': ('name', 'parent_id', 'active'),
    'SortDateOperation': ('sort_date', 'sort_name', 'gateway_code'),
}


def _changed(session, _context):
    if not has_app_context() or not enabled() or session.info.get('staffing_raiden_dirty'):
        return
    for row in [*session.new, *session.dirty, *session.deleted]:
        fields = _TRACKED.get(type(row).__name__)
        if fields and (row in session.new or row in session.deleted or
                       any(inspect(row).attrs[field].history.has_changes() for field in fields)):
            _dirty(session)
            session.info['staffing_raiden_dirty'] = True
            session.info['archive_deadline_hint'] = True
            break


def _clear(session, *_args):
    session.info.pop('staffing_raiden_dirty', None)


_installed = False


def install(_app):
    global _installed
    if not _installed:
        event.listen(Session, 'after_flush', _changed)
        event.listen(Session, 'after_commit', _clear)
        event.listen(Session, 'after_rollback', _clear)
        _installed = True
