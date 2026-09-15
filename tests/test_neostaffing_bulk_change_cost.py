"""Bulk Change transaction cost and persistence regression proof."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from threading import Barrier
import unittest

from flask import g
from sqlalchemy import event

from app.extensions import db
from app.models import (StaffingPerson, StaffingWorkAssignment, User,
                        StaffingVacationManagementSelection, StaffingVacationManagementTurnState,
                        StaffingShiftFlowPlan)
from app.services import neostaffing_bulk_change as service
from app.services import neostaffing as staffing_service
from tests import test_neostaffing_bulk_change as fixtures


class BulkChangeCostTest(unittest.TestCase):
    _unit = fixtures.NeoStaffingBulkChangeTest._unit
    _person = fixtures.NeoStaffingBulkChangeTest._person
    _user = fixtures.NeoStaffingBulkChangeTest._user
    _lead = fixtures.NeoStaffingBulkChangeTest._lead
    _work = fixtures.NeoStaffingBulkChangeTest._work
    _reports = fixtures.NeoStaffingBulkChangeTest._reports
    _login = fixtures.NeoStaffingBulkChangeTest._login
    _service_call = fixtures.NeoStaffingBulkChangeTest._service_call
    tearDown = fixtures.NeoStaffingBulkChangeTest.tearDown

    def setUp(self):
        fixtures.NeoStaffingBulkChangeTest.setUp(self)
        self.employee_ids = []
        for index in range(110):
            person = self._person(f'COST-{index}', 'part_time', 'Cost', str(index))
            self._work(person, self.area_one)
            self.employee_ids.append(person.id)
        self.actor_id = self.ft_editor_user.id
        self.target_id = self.area_two.id
        db.session.commit()

    def workspace(self, ids, target_id=None):
        user = db.session.get(User, self.actor_id)
        workspace = service.new_workspace(user)
        workspace['base_revision'] = service.BulkChangeDataBundle().revision
        for pid in ids:
            workspace['people'][f'p:{pid}'] = {
                'kind': 'existing', 'person_id': pid,
                'changes': {'work_area_unit_id': target_id or self.target_id},
                'request_note': None,
            }
        return workspace

    def profile(self, workspace, *, action='apply', extra=None, method='POST'):
        self._login(db.session.get(User, self.actor_id))
        token = service.encode_workspace(workspace)
        g.pop('_login_user', None)
        db.session.remove()
        sql, hydrated = [], Counter()

        def capture(_connection, _cursor, statement, parameters, _context, many):
            sql.append((statement, parameters, many))

        def loaded(_session, obj):
            hydrated[type(obj).__name__] += 1

        event.listen(db.engine, 'before_cursor_execute', capture)
        event.listen(db.session.session_factory, 'loaded_as_persistent', loaded)
        try:
            response = self.client.open('/neostaffing/bulk-change', method=method, data={
                'action': action, 'workspace_token': token, **(extra or {})})
        finally:
            event.remove(db.engine, 'before_cursor_execute', capture)
            event.remove(db.session.session_factory, 'loaded_as_persistent', loaded)
        return response, sql, hydrated

    def test_stage_and_get_read_budget(self):
        workspace = service.new_workspace(db.session.get(User, self.actor_id))
        for method in ('GET', 'POST'):
            response, sql, hydrated = self.profile(workspace, method=method,
                action='stage_person', extra={'person_id':self.employee_ids[0],
                    'change_work_area_unit_id':'1', 'work_area_unit_id':self.target_id})
            self.assertEqual(response.status_code, 200)
            counts = Counter(s.split()[0] for s, _, _ in sql)
            print(method, dict(counts), dict(hydrated))
            self.assertEqual(counts['SELECT'], 15)
            self.assertEqual(hydrated['StaffingPerson'], 119)
            self.assertEqual(hydrated['StaffingWorkAssignment'], 112)
            self.assertEqual(sum(counts[verb] for verb in ('UPDATE', 'INSERT', 'DELETE')), 0)

    def test_profile_small_and_large(self):
        for count in (2, 100):
            workspace = self.workspace(self.employee_ids[:count])
            response, sql, hydrated = self.profile(workspace)
            self.assertEqual(response.status_code, 302)
            counts = Counter(statement.split()[0] for statement, _, _ in sql)
            writes = [(statement.split(' SET ')[0], len(parameters) if many else 1)
                      for statement, parameters, many in sql if statement.startswith('UPDATE')]
            print('BULK', count, dict(counts), dict(hydrated), writes)
            # Six fresh snapshot reads after one set-based dependency-lock CTE.
            # Fixed overhead buys narrow locks; never per-employee query fanout.
            self.assertEqual(counts['SELECT'], 22)
            self.assertEqual(counts['WITH'], 1)
            self.assertEqual(counts['UPDATE'], 2)
            self.assertEqual(counts['INSERT'], 0)
            self.assertEqual(writes, [('UPDATE staffing_people', 2 if count == 2 else 98),
                                     ('UPDATE staffing_work_assignments', 2 if count == 2 else 98)])
            self.assertNotIn('staffing_assignment_snapshot', db.session.info)
            db.session.remove()
            rows = StaffingWorkAssignment.query.filter(
                StaffingWorkAssignment.person_id.in_(self.employee_ids[:count]),
                StaffingWorkAssignment.active.is_(True)).all()
            self.assertEqual(len(rows), count)
            self.assertTrue(all(row.work_area_unit_id == self.target_id for row in rows))

    def test_profile_ft_noop(self):
        sort = self._unit('sort', 'Day')
        operation = self._unit('operation', 'Day Op', sort)
        department = self._unit('department', 'Day Dept', operation)
        area = self._unit('work_area', 'Day Area', department)
        self._work(self.employee_two, area)
        pid = self.employee_two.id
        db.session.commit()
        user = db.session.get(User, self.actor_id)
        workspace = service.new_workspace(user)
        self._service_call(service.stage_workspace_change, workspace, 'stage_person', {
            'person_id':pid, 'change_work_area_unit_id':'1', 'work_area_unit_id':self.target_id}, user)
        response, sql, hydrated = self.profile(workspace)
        self.assertEqual(response.status_code, 302)
        print('NOOP', dict(Counter(s.split()[0] for s, _, _ in sql)),
              [s for s, _, _ in sql if s.startswith('UPDATE')])
        self.assertFalse(any(s.startswith('UPDATE') for s, _, _ in sql))
        self.assertEqual(sum(s.startswith('SELECT') for s, _, _ in sql), 19)
        self.assertEqual(sum(s.startswith('WITH') for s, _, _ in sql), 1)
        db.session.remove()
        self.assertEqual(StaffingWorkAssignment.query.filter_by(person_id=pid, active=True).count(), 2)

    def test_hourly_status_changes_do_not_fan_out_management_reads(self):
        for count in (2, 100):
            workspace = self.workspace(self.employee_ids[:count])
            for row in workspace['people'].values():
                row['changes'] = {'employee_status': 'fmla'}
            response, sql, _ = self.profile(workspace)
            self.assertEqual(response.status_code, 302)
            counts = Counter(s.split()[0] for s, _, _ in sql)
            print('STATUS', count, dict(counts))
            self.assertEqual(counts['SELECT'], 20)
            self.assertEqual(counts['WITH'], 1)
            self.assertEqual(counts['UPDATE'], 1)
            db.session.remove()
            self.assertEqual(StaffingPerson.query.filter(
                StaffingPerson.id.in_(self.employee_ids[:count]),
                StaffingPerson.employee_status == 'fmla').count(), count)

    def test_former_management_retained_state_still_reconciles(self):
        today = date.today()
        pick = StaffingVacationManagementSelection(staffing_person_id=self.employee_ids[0],
            vacation_year=today.year, week_ending=today + timedelta(days=14),
            selected_by_user_id=self.actor_id)
        turn = StaffingVacationManagementTurnState(current_person_id=self.employee_ids[1],
            vacation_year=today.year, area_unit_id=self.operation_one.id)
        db.session.add_all([pick, turn])
        db.session.flush()
        pick_id, turn_id = pick.id, turn.id
        db.session.commit()
        workspace = self.workspace(self.employee_ids[:3])
        for row in workspace['people'].values():
            row['changes'] = {'employee_status': 'fmla'}
        response, _, _ = self.profile(workspace)
        self.assertEqual(response.status_code, 302)
        db.session.remove()
        selection = db.session.get(StaffingVacationManagementSelection, pick_id)
        self.assertIsNotNone(selection.cancelled_at)
        self.assertEqual(selection.cancellation_reason, 'left_management')
        turn = db.session.get(StaffingVacationManagementTurnState, turn_id)
        self.assertTrue(turn.completed_at is not None or turn.current_person_id != self.employee_ids[1])

    def test_postgres_cached_snapshot_rejects_newer_assignment_atomically(self):
        if db.engine.dialect.name != 'postgresql':
            self.skipTest('Requires disposable PostgreSQL')
        ids = [self.employee_one.id, self.employee_ids[0]]
        original_area = self.area_one.id
        workspace = self.workspace(ids)
        # Keep the ORM objects alive, as a caller reusing a request-local bundle
        # can do. SELECT FOR UPDATE must refresh, not trust the identity map.
        snapshot = service.BulkChangeDataBundle()

        def concurrent_clear():
            with self.app.app_context():
                person = db.session.get(StaffingPerson, ids[0])
                staffing_service.clear_work_assignment(person,
                    expected_version=str(person.shift_flow_version or 0))
                db.session.commit()

        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(concurrent_clear).result(timeout=10)
        with self.assertRaisesRegex(ValueError, service.LIVE_DATA_CHANGED_MESSAGE):
            self._service_call(service.apply_workspace, workspace,
                               db.session.get(User, self.actor_id))
        db.session.rollback()
        db.session.remove()
        self.assertEqual(StaffingWorkAssignment.query.filter_by(person_id=ids[0], active=True).count(), 0)
        self.assertEqual(StaffingWorkAssignment.query.filter_by(person_id=ids[1], active=True).one().work_area_unit_id,
                         original_area)
        self.assertIsNotNone(snapshot)
        response, _, _ = self.profile(self.workspace(ids))
        self.assertEqual(response.status_code, 302)
        db.session.remove()
        self.assertEqual(StaffingWorkAssignment.query.filter(
            StaffingWorkAssignment.person_id.in_(ids),
            StaffingWorkAssignment.active.is_(True),
            StaffingWorkAssignment.work_area_unit_id == self.target_id).count(), 2)

    def test_stale_post_performs_zero_writes(self):
        ids = self.employee_ids[:2]
        original_area = self.area_one.id
        workspace = self.workspace(ids)
        person = db.session.get(StaffingPerson, ids[0])
        staffing_service.clear_work_assignment(person,
            expected_version=str(person.shift_flow_version or 0))
        db.session.commit()
        response, sql, _ = self.profile(workspace)
        self.assertEqual(response.status_code, 200)
        self.assertIn(service.LIVE_DATA_CHANGED_MESSAGE, response.get_data(as_text=True))
        counts = Counter(s.split()[0] for s, _, _ in sql)
        print('STALE', dict(counts))
        # Base and fixed route both use 24 reads: rollback invalidates loaded
        # access/identity rows and the error page needs a fresh display snapshot.
        self.assertLessEqual(counts['SELECT'], 24)
        self.assertEqual(sum(counts[v] for v in ('INSERT', 'UPDATE', 'DELETE')), 0)
        db.session.remove()
        self.assertEqual(StaffingWorkAssignment.query.filter_by(person_id=ids[0], active=True).count(), 0)
        self.assertEqual(StaffingWorkAssignment.query.filter_by(person_id=ids[1], active=True).one().work_area_unit_id,
                         original_area)

    def test_postgres_lock_footprint(self):
        if db.engine.dialect.name != 'postgresql':
            self.skipTest('Requires disposable PostgreSQL')
        from sqlalchemy import text
        from sqlalchemy.exc import OperationalError
        workspace = self.workspace(self.employee_ids[:2])
        db.session.remove()
        self._service_call(service.apply_workspace, workspace, db.session.get(User, self.actor_id))
        with db.engine.connect() as connection:
            with self.assertRaises(OperationalError) as error:
                connection.execute(text('SELECT id FROM staffing_people WHERE id=:id FOR UPDATE NOWAIT'),
                                   {'id':self.employee_ids[0]})
            self.assertEqual(error.exception.orig.pgcode, '55P03')
        with db.engine.connect() as connection:
            connection.execute(text('SELECT id FROM staffing_people WHERE id=:id FOR UPDATE NOWAIT'),
                               {'id':self.employee_ids[-1]})
        db.session.rollback()

    def test_ft_move_preserves_other_sort_and_fresh_login(self):
        sort = self._unit('sort', 'Day')
        operation = self._unit('operation', 'Day Op', sort)
        department = self._unit('department', 'Day Dept', operation)
        area = self._unit('work_area', 'Day Area', department)
        other = self._work(self.employee_two, area)
        pid, other_id, target, other_area_id = self.employee_two.id, other.id, self.area_one.id, area.id
        db.session.commit()
        before = other.updated_at
        response, sql, _ = self.profile(self.workspace([pid], target))
        self.assertEqual(response.status_code, 302)
        db.session.remove()
        self.assertEqual(db.session.get(StaffingWorkAssignment, other_id).updated_at, before)
        self.assertEqual({row.work_area_unit_id for row in StaffingWorkAssignment.query.filter_by(person_id=pid, active=True)},
                         {target, other_area_id})
        self.client = self.app.test_client()
        self._login(db.session.get(User, self.actor_id))
        response = self.client.get('/neostaffing/bulk-change')
        self.assertEqual(response.status_code, 200)
        db.session.remove()
        person = db.session.get(StaffingPerson, pid)
        self.assertEqual({row.work_area_unit_id for row in person.work_assignments if row.active}, {target, other_area_id})

    def test_postgres_two_bulk_writers_have_one_winner(self):
        if db.engine.dialect.name != 'postgresql':
            self.skipTest('Requires disposable PostgreSQL')
        workspace = self.workspace(self.employee_ids[:2])
        barrier = Barrier(2)

        def apply():
            with self.app.test_request_context('/neostaffing/bulk-change'):
                actor = db.session.get(User, self.actor_id)
                barrier.wait(timeout=10)
                try:
                    service.apply_workspace(workspace, actor)
                    db.session.commit()
                    return 'changed'
                except ValueError as error:
                    db.session.rollback()
                    self.assertEqual(str(error), service.LIVE_DATA_CHANGED_MESSAGE)
                    return 'conflict'

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(apply) for _ in range(2)]
            self.assertEqual(sorted(future.result(timeout=15) for future in futures), ['changed', 'conflict'])
        db.session.remove()
        self.assertEqual(StaffingWorkAssignment.query.filter(
            StaffingWorkAssignment.person_id.in_(self.employee_ids[:2]),
            StaffingWorkAssignment.active.is_(True),
            StaffingWorkAssignment.work_area_unit_id == self.target_id).count(), 2)

    def test_bulk_home_move_and_shift_exit_use_existing_lifecycle(self):
        self.operation_one.name = 'Ramp'
        self.department_one.name = 'Shift'
        door = self._unit('work_area', 'Door 6', self.department_one)
        pid, door_id, setup_id = self.employee_one.id, door.id, self.area_one.id
        db.session.add(StaffingShiftFlowPlan(staffing_person_id=pid,
            sort_start_work_area_id=setup_id, setup_work_area_id=setup_id,
            final_door_work_area_id=door_id))
        db.session.commit()
        response, _, _ = self.profile(self.workspace([pid], door_id))
        self.assertEqual(response.status_code, 302)
        db.session.remove()
        plan = StaffingShiftFlowPlan.query.filter_by(staffing_person_id=pid).one()
        self.assertEqual(plan.sort_start_work_area_id, door_id)
        self.assertEqual(plan.setup_work_area_id, setup_id)
        self.assertEqual(plan.final_door_work_area_id, door_id)
        for target in (self.target_id, door_id):
            response, _, _ = self.profile(self.workspace([pid], target))
            self.assertEqual(response.status_code, 302)
            db.session.remove()
            self.assertIsNone(StaffingShiftFlowPlan.query.filter_by(staffing_person_id=pid).first())

    def test_lifecycle_failure_rolls_back_and_clears_snapshot(self):
        sort = self._unit('sort', 'Day')
        operation = self._unit('operation', 'Day Op', sort)
        department = self._unit('department', 'Day Dept', operation)
        area = self._unit('work_area', 'Day Area', department)
        self._work(self.employee_two, area)
        pid, peer = self.employee_two.id, self.employee_ids[0]
        db.session.commit()
        workspace = self.workspace([peer, pid])
        workspace['people'][f'p:{peer}']['changes'] = {'first_name':'Must Roll Back'}
        workspace['people'][f'p:{pid}']['changes'] = {'classification':'part_time'}
        response, _, _ = self.profile(workspace)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Only FT Combo', response.get_data(as_text=True))
        self.assertNotIn('staffing_assignment_snapshot', db.session.info)
        db.session.remove()
        self.assertEqual(db.session.get(StaffingPerson, peer).first_name, 'Cost')
        self.assertEqual(db.session.get(StaffingPerson, pid).classification, 'full_time_combo')
        self.assertEqual(StaffingWorkAssignment.query.filter_by(person_id=pid, active=True).count(), 2)
