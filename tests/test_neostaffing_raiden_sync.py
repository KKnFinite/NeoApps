"""Night Ramp Shift HERE -> one protected Raiden Display cell; no external test writes."""
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from app.extensions import db
from app.models import StaffingDailyAttendance
from app.models.staffing_raiden_sync import StaffingRaidenSync as Receipt
from app.services import neostaffing as staffing, neostaffing_raiden_sync as sync
from tests.test_neostaffing_attendance_counts import NeoStaffingAttendanceCountsTest as Fixture


class Adapter:
    def __init__(self, value=None):
        self.value = value
        self.calls = []
        self.closed = 0
        self.read_calls = 0
        self.formula = '=M23+P23'
        self.book = sync.RAIDEN_WORKBOOK_ID

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed += 1

    def read(self):
        self.read_calls += 1
        rows = [[{'userEnteredValue': {'stringValue': 'SHIFT'}},
                 ({'userEnteredValue': {'numberValue': self.value}} if self.value is not None else {}),
                 {'userEnteredValue': {'formulaValue': self.formula}},
                 {'userEnteredValue': {'stringValue': 'RAMP'}},
                 {'userEnteredValue': {'formulaValue': '=EXTERNAL()'}}]]
        return {'spreadsheetId': self.book,
                'sheets': [{'properties': {'sheetId': sync.DISPLAY_SHEET_ID, 'title': 'Display'},
                            'data': [{'startRow': 22, 'startColumn': 11, 'rowData': [{'values': rows[0]}]}]}]}

    def write(self, count):
        self.calls.append(count)
        self.value = count


class RaidenMirrorTest(unittest.TestCase):
    setUp = Fixture.setUp
    tearDown = Fixture.tearDown
    _person = Fixture._person

    def person(self, eid, area, status=None):
        person = self._person(eid, area)
        db.session.flush()
        if status is not None:
            db.session.add(StaffingDailyAttendance(
                person_id=person.id,
                attendance_date=self.operation.sort_date,
                sort_unit_id=self.night.id,
                sort_date_operation_id=self.operation.id,
                work_area_unit_id=area.id,
                status=status,
            ))
        db.session.commit()
        return person

    def test_fixed_five_minute_boundaries_and_first_clock_initialization(self):
        from datetime import datetime
        self.assertEqual(sync.next_check(datetime(2026, 11, 10, 5, 1, 12)),
                         datetime(2026, 11, 10, 5, 5))
        self.assertEqual(sync.next_check(datetime(2026, 11, 10, 5, 5)),
                         datetime(2026, 11, 10, 5, 10))
        self.app.config['NEOSTAFFING_RAIDEN_SYNC_ENABLED'] = True
        with patch.object(staffing, 'current_night_attendance_operation', return_value=self.operation):
            self.assertIsNone(db.session.get(Receipt, 1))
            self.assertLessEqual(sync.next_deadline(sync.now()), sync.now())
            self.assertFalse(sync.drain(adapter_factory=lambda: Adapter()))
            self.assertGreater(db.session.get(Receipt, 1).retry_at, sync.now())

    def test_counts_only_eligible_here_at_night_ramp_shift(self):
        self.person('RAIDEN-HERE', self.door, 'here')
        self.person('RAIDEN-ABSENT', self.door, 'call_in')
        self.person('RAIDEN-UNMARKED', self.door)
        self.person('RAIDEN-OUTSIDE', self.outside, 'here')
        with patch.object(staffing, 'current_night_attendance_operation', return_value=self.operation):
            self.assertEqual(sync.source_snapshot(), (self.operation.id, 1))

    def test_one_cell_write_and_readback_idempotent(self):
        adapter = Adapter()
        sync.deliver(3, adapter)
        self.assertEqual(adapter.calls, [3])
        sync.deliver(3, adapter)
        self.assertEqual(adapter.calls, [3])
        self.assertEqual(adapter.formula, '=M23+P23')
        adapter.formula = '=BROKEN()'
        with self.assertRaises(sync.RaidenSyncError):
            sync.deliver(4, adapter)
        self.assertEqual(adapter.calls, [3])
        adapter.formula = '=M23+P23'
        adapter.book = 'wrong_workbook'
        with self.assertRaises(sync.RaidenSyncError):
            sync.deliver(4, adapter)
        self.assertEqual(adapter.calls, [3])

    def due_now(self):
        row = db.session.get(Receipt, 1)
        row.retry_at = sync.now() - timedelta(seconds=1)
        db.session.commit()

    def test_saves_coalesce_and_provider_is_only_contacted_on_count_change(self):
        self.app.config['NEOSTAFFING_RAIDEN_SYNC_ENABLED'] = True
        with patch.object(staffing, 'current_night_attendance_operation', return_value=self.operation):
            self.person('RAIDEN-COMMITTED', self.door, 'here')
            row = db.session.get(Receipt, 1)
            self.assertIsNotNone(row)
            self.assertGreater(row.retry_at, sync.now())
            first_due = row.retry_at
            self.assertFalse(sync.drain(adapter_factory=lambda: Adapter()))

            # Repeated supervisor saves may dirty the receipt but never accelerate Google.
            person = self.person('RAIDEN-SECOND', self.door, 'call_in')
            attendance = StaffingDailyAttendance.query.filter_by(person_id=person.id).one()
            attendance.status = 'here'
            db.session.commit()
            self.assertEqual(db.session.get(Receipt, 1, populate_existing=True).retry_at, first_due)

            self.due_now()
            adapter = Adapter()
            self.assertTrue(sync.drain(adapter_factory=lambda: adapter))
            self.assertEqual(adapter.calls, [2])
            self.assertEqual(adapter.closed, 1)
            row = db.session.get(Receipt, 1, populate_existing=True)
            self.assertEqual((row.delivered_operation_id, row.delivered_count),
                             (self.operation.id, 2))
            self.assertGreater(row.retry_at, sync.now())
            self.assertLessEqual((row.retry_at - sync.now()).total_seconds(), 300)

            # A scheduled check with no count change must do ZERO Google requests.
            self.due_now()
            before_verified = db.session.get(Receipt, 1).verified_at
            def forbid_adapter():
                raise AssertionError('Unchanged count must not contact Google')
            self.assertTrue(sync.drain(adapter_factory=forbid_adapter))
            self.assertEqual(db.session.get(Receipt, 1).verified_at, before_verified)

            # Even a dirty revision that nets to the SAME HERE count makes no calls.
            attendance = StaffingDailyAttendance.query.filter_by(person_id=person.id).one()
            attendance.status = 'call_in'
            db.session.commit()
            attendance.status = 'here'
            db.session.commit()
            self.due_now()
            self.assertTrue(sync.drain(adapter_factory=forbid_adapter))
            row = db.session.get(Receipt, 1)
            self.assertEqual(row.delivered_revision, row.requested_revision)

    def test_failed_google_write_retries_no_faster_than_five_minutes(self):
        self.app.config['NEOSTAFFING_RAIDEN_SYNC_ENABLED'] = True
        with patch.object(staffing, 'current_night_attendance_operation', return_value=self.operation):
            self.person('RAIDEN-RETRY', self.door, 'here')
            self.due_now()
            bad = Adapter()
            with patch.object(bad, 'write', side_effect=TimeoutError('timeout')):
                self.assertFalse(sync.drain(adapter_factory=lambda: bad))
            row = db.session.get(Receipt, 1, populate_existing=True)
            self.assertGreaterEqual((row.retry_at - sync.now()).total_seconds(), 299)
            self.assertEqual(bad.closed, 1)
            # New attendance edits cannot bypass the provider failure cooldown.
            next_due = row.retry_at
            self.person('RAIDEN-ANOTHER', self.door, 'here')
            self.assertEqual(db.session.get(Receipt, 1, populate_existing=True).retry_at, next_due)
            self.due_now()
            recovered = Adapter()
            self.assertTrue(sync.drain(adapter_factory=lambda: recovered))
            self.assertEqual(recovered.calls, [2])

    def test_no_operation_does_not_erase_prior_value(self):
        self.app.config['NEOSTAFFING_RAIDEN_SYNC_ENABLED'] = True
        adapter = Adapter(12)
        with patch.object(staffing, 'current_night_attendance_operation', return_value=None):
            self.assertIsNone(sync.next_deadline(sync.now()))
            self.assertFalse(sync.drain(adapter_factory=lambda: adapter))
        self.assertEqual(adapter.calls, [])

    def test_current_night_operation_change_forces_refresh(self):
        self.app.config['NEOSTAFFING_RAIDEN_SYNC_ENABLED'] = True
        adapter = Adapter()
        with patch.object(staffing, 'current_night_attendance_operation', return_value=self.operation):
            self.person('RAIDEN-ONE', self.door, 'here')
            self.due_now()
            self.assertTrue(sync.drain(adapter_factory=lambda: adapter))
            self.assertEqual(adapter.value, 1)
        # Simulate a new active Night sort, before anyone is marked HERE.
        newer = SimpleNamespace(
            id=self.operation.id + 10, sort_date=self.operation.sort_date + timedelta(days=1),
            sort_name='night', gateway_code='RFD',
        )
        with patch.object(staffing, 'current_night_attendance_operation', return_value=newer):
            self.assertIsNotNone(sync.next_deadline(sync.now()))
            self.due_now()
            self.assertTrue(sync.drain(adapter_factory=lambda: adapter))
            self.assertEqual(adapter.value, 0)


if __name__ == '__main__':
    unittest.main()
