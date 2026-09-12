"""Current-sort accountability and shared attendance write integrity."""
from datetime import date, datetime, timedelta
import unittest
from unittest.mock import patch

from app.extensions import db
from sqlalchemy import event, inspect
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.models import StaffingDailyAttendance, StaffingAttendanceOccurrence as Occurrence, StaffingPerson
from app.services import neostaffing as staffing
from app.services import neostaffing_accountability as accountability
from tests import test_neostaffing_attendance_counts as fixture


class AttendanceAccountabilityTest(unittest.TestCase):
    _person = fixture.NeoStaffingAttendanceCountsTest._person
    tearDown = fixture.NeoStaffingAttendanceCountsTest.tearDown

    def setUp(self):
        fixture.NeoStaffingAttendanceCountsTest.setUp(self)
        self.person = self._person('ACCOUNT1', self.door)
        self.second = self._person('ACCOUNT2', self.door)
        for person in (self.person, self.second):
            staffing.create_shift_flow_plan(person, {
                'shift_flow_sort_start_work_area_id': str(self.door.id),
                'shift_flow_final_door_work_area_id': str(self.door.id),
            }, self.door)
        db.session.commit()
        self.app.config['CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE'] = datetime(2026, 8, 21, 23)
        current = patch.object(staffing, 'current_night_attendance_operation', return_value=self.operation)
        current.start()
        self.addCleanup(current.stop)

    def _form(self):
        rows = staffing.operational_manage_employees_context([self.door.id])['here']
        values = {'sort_date_operation_id': str(self.operation.id)}
        for row in rows:
            pid = row['person'].id
            values[f'status_{pid}'] = row['status']
            values[f'original_{pid}'] = staffing._attendance_snapshot_serializer().dumps({
                'person_id': pid, 'operation_id': self.operation.id,
                'original': staffing._attendance_original(row['attendance']),
            })
        return values

    def _save(self, values, **kwargs):
        return staffing.save_operational_manage_attendance(values, None, [self.door.id], form_submission=True, **kwargs)

    def test_stale_operational_blank_does_not_erase_newer_status(self):
        older = self._form()
        newer = self._form()
        newer[f'status_{self.person.id}'] = 'call_in'
        self._save(newer)
        db.session.commit()
        older[f'status_{self.second.id}'] = 'here'
        self._save(older)
        db.session.commit()
        self.assertEqual(StaffingDailyAttendance.query.filter_by(person_id=self.person.id).one().status, 'call_in')

    def _change(self, status, *, main=False, person=None):
        person = person or self.person
        if main:
            return staffing.save_attendance({
                'sort_date_operation_id': str(self.operation.id),
                'work_area_id': str(self.door.id), 'sort_id': str(self.night.id),
                f'status_{person.id}': status,
            }, None)
        form = self._form()
        form[f'status_{person.id}'] = status
        return self._save(form)

    def test_both_writers_track_switch_correct_and_clear_same_occurrence(self):
        for main in (False, True):
            with self.subTest(main=main):
                self._change('call_in', main=main)
                db.session.commit()
                row = Occurrence.query.one()
                original_id, recorded_at = row.id, row.recorded_at
                self.assertTrue(row.reconciliation_needed)
                self.assertEqual(row.sort_date_operation_id, self.operation.id)
                self._change('no_call', main=main)
                db.session.commit()
                row = Occurrence.query.one()
                self.assertEqual((row.id, row.recorded_at, row.status), (original_id, recorded_at, 'no_call'))
                self._change('call_in', main=main)
                db.session.commit()
                self.assertEqual(Occurrence.query.one().id, original_id)
                for status in ('here', 'scheduled_off', 'vacation', ''):
                    self._change(status, main=main)
                    db.session.commit()
                    self.assertEqual(Occurrence.query.count(), 0)
                    self._change('no_call', main=main)
                    db.session.commit()
                self._change('', main=main)
                db.session.commit()

    def test_same_status_noop_and_failed_stale_form_do_not_change_occurrence(self):
        self._change('call_in')
        db.session.commit()
        stale = self._form()
        self._change('no_call')
        db.session.commit()
        before = Occurrence.query.one().updated_at
        statements = []
        def capture(_c, _cursor, sql, *_args):
            if sql.lstrip().lower().startswith(('insert', 'update', 'delete')):
                statements.append(sql)
        event.listen(db.engine, 'before_cursor_execute', capture)
        try:
            self.assertEqual(self._change('no_call'), 0)
            db.session.commit()
        finally:
            event.remove(db.engine, 'before_cursor_execute', capture)
        self.assertEqual(statements, [])
        stale[f'status_{self.person.id}'] = ''
        with self.assertRaisesRegex(ValueError, 'changed while'):
            self._save(stale)
        db.session.rollback()
        self.assertEqual((Occurrence.query.one().status, Occurrence.query.one().updated_at), ('no_call', before))
        self.assertEqual(StaffingDailyAttendance.query.one().status, 'no_call')

    def test_two_stale_forms_same_employee_conflict_different_employees_survive(self):
        first, second = self._form(), self._form()
        first[f'status_{self.person.id}'] = 'call_in'
        self._save(first)
        db.session.commit()
        second[f'status_{self.person.id}'] = 'no_call'
        with self.assertRaisesRegex(ValueError, 'changed while'):
            self._save(second)
        db.session.rollback()
        second[f'status_{self.person.id}'] = ''  # Unchanged blank from the old form.
        second[f'status_{self.second.id}'] = 'no_call'
        self._save(second)
        db.session.commit()
        self.assertEqual({r.person_id: r.status for r in Occurrence.query.all()}, {
            self.person.id: 'call_in', self.second.id: 'no_call',
        })

    def test_invalid_operation_scope_or_form_never_records_occurrence(self):
        form = self._form()
        form[f'status_{self.person.id}'] = 'call_in'
        with patch.object(staffing, 'current_night_attendance_operation', return_value=None):
            with self.assertRaises(ValueError):
                self._save(form)
        stale = dict(form, sort_date_operation_id=str(self.operation.id + 100))
        with self.assertRaises(ValueError):
            self._save(stale)
        with self.assertRaises(ValueError):
            staffing.save_operational_manage_attendance(form, None, [self.outside.id], form_submission=True)
        form.pop(f'original_{self.person.id}')
        with self.assertRaises(ValueError):
            self._save(form)
        db.session.rollback()
        self.assertEqual(Occurrence.query.count(), 0)
        self.assertEqual(StaffingDailyAttendance.query.count(), 0)

    def test_occurrence_survives_daily_rollover_and_is_transactional(self):
        self._change('call_in')
        db.session.rollback()
        self.assertEqual(Occurrence.query.count(), 0)
        self._change('call_in')
        db.session.commit()
        StaffingDailyAttendance.query.delete()
        db.session.commit()
        self.assertEqual(Occurrence.query.one().status, 'call_in')

    def test_calendar_retention_and_fixed_physical_batch(self):
        self.assertEqual(accountability.occurrence_cutoff(date(2026, 11, 30)), date(2026, 2, 28))
        self.assertEqual(accountability.occurrence_cutoff(date(2024, 11, 30)), date(2024, 2, 29))
        cutoff = accountability.occurrence_cutoff(date(2026, 8, 21))
        self._change('call_in')
        db.session.commit()
        boundary = Occurrence.query.one()
        boundary.attendance_date = cutoff
        people = [StaffingPerson(employee_id=f'OLD{i}', first_name='Old', last_name=str(i),
            classification='part_time', seniority_date=date(2020, 1, 1)) for i in range(252)]
        db.session.add_all(people)
        db.session.flush()
        db.session.add_all([Occurrence(person_id=p.id, sort_date_operation_id=self.operation.id,
            attendance_date=cutoff-timedelta(days=1), status='call_in') for p in people])
        db.session.commit()
        self.assertEqual(accountability.purge_expired_occurrences(date(2026, 8, 21)), 250)
        db.session.commit()
        self.assertEqual(Occurrence.query.count(), 3)
        self.assertEqual(accountability.retained_occurrences_query(date(2026, 8, 21)).count(), 1)
        self.assertEqual(accountability.purge_expired_occurrences(date(2026, 8, 21)), 2)
        db.session.commit()
        self.assertEqual(Occurrence.query.one().id, boundary.id)

    def test_additive_schema_sync_is_repeatable_and_preserves_attendance(self):
        from app.services.schema_sync import _create_missing_application_tables
        self._change('here')
        db.session.commit()
        Occurrence.__table__.drop(db.engine)
        for _ in range(2):
            _create_missing_application_tables(set(inspect(db.engine).get_table_names()))
        self.assertEqual(StaffingDailyAttendance.query.one().status, 'here')
        self._change('call_in')
        db.session.commit()
        self.assertEqual(Occurrence.query.count(), 1)
        ddl = str(CreateTable(Occurrence.__table__).compile(dialect=postgresql.dialect()))
        self.assertIn('UNIQUE (person_id, sort_date_operation_id)', ddl)
        self.assertIn("status IN ('call_in', 'no_call')", ddl)

    def test_tracking_is_set_based_and_read_paths_do_not_collect_or_prompt(self):
        # Additional occurrence work: one bounded SELECT plus one bounded DELETE,
        # regardless of employee count. No recommendation/count or tracker setting.
        counts = []
        for size in (1, 30):
            people = [self._person(f'BATCH{size}-{i}', self.door) for i in range(size)]
            db.session.commit()
            values = {'sort_date_operation_id': str(self.operation.id),
                'sort_id': str(self.night.id), 'work_area_id': str(self.door.id)}
            values.update({f'status_{p.id}': 'call_in' for p in people})
            statements = []
            def capture(_c, _cursor, sql, *_args):
                if sql.lstrip().lower().startswith('select'):
                    statements.append(sql)
            event.listen(db.engine, 'before_cursor_execute', capture)
            try:
                staffing.save_attendance(values, None)
                db.session.commit()
            finally:
                event.remove(db.engine, 'before_cursor_execute', capture)
            counts.append(len(statements))
            self.assertEqual(sum('staffing_attendance_occurrences' in s for s in statements), 1)
        self.assertEqual(counts[0], counts[1])
        self.assertLessEqual(max(counts), 10)
        self.assertEqual(Occurrence.query.count(), 31)
        self.assertTrue(all(r.reconciliation_needed for r in Occurrence.query.all()))

    def test_retention_runs_without_a_current_sort_through_existing_maintenance(self):
        from app.services.neostaffing_attendance_history import maintain_current_attendance_rollover
        self._change('call_in')
        db.session.commit()
        Occurrence.query.one().attendance_date = date(2025, 1, 1)
        db.session.commit()
        result = maintain_current_attendance_rollover(now_local=datetime(2026, 8, 25, 12))
        self.assertTrue(result.changed)
        self.assertEqual(result.purged_expired_occurrence_count, 1)
        db.session.commit()
        self.assertEqual(Occurrence.query.count(), 0)
