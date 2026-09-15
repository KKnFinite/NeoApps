"""Assignment/topology interleavings in disposable local PostgreSQL schemas."""
from concurrent.futures import ThreadPoolExecutor
import os
from threading import Barrier, Event
from time import monotonic, sleep
import unittest

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError

from app.extensions import db
from app.models import StaffingWorkAssignment
from app.services.neostaffing_assignment_schema import (
    sync_staffing_assignment_schema, install_assignment_guards, INVALID,
)
from tests import test_neostaffing_employee_id_schema as fixtures


@unittest.skipUnless(os.environ.get('NEOSTAFFING_TEST_POSTGRES_URL'), 'Requires disposable PostgreSQL')
class StaffingTopologyPostgresTest(unittest.TestCase):
    tearDown = fixtures.EmployeeIdPostgresTest.tearDown

    def setUp(self):
        fixtures.EmployeeIdPostgresTest.setUp(self)
        sync_staffing_assignment_schema()
        db.session.commit()
        self.night_id = self.fixture.sort.id
        self.ft_id = self.fixture.employee_two.id

    def new_branch(self, name):
        sort = self.fixture._unit('sort', name)
        operation = self.fixture._unit('operation', name + ' Op', sort)
        department = self.fixture._unit('department', name + ' Dept', operation)
        area = self.fixture._unit('work_area', name + ' Area', department)
        return sort.id, operation.id, department.id, area.id

    def locks(self):
        result = []
        for table in ('staffing_people', 'staffing_work_assignments', 'staffing_units'):
            with self.engine.connect() as c:
                total = c.execute(text('SELECT count(*) FROM ' + table)).scalar()
                available = c.execute(text('SELECT id FROM ' + table + ' FOR UPDATE SKIP LOCKED')).all()
                result.append(total - len(available))
        return result

    def move(self, connection, unit_id, parent_id):
        connection.execute(text('UPDATE staffing_units SET parent_id=:parent WHERE id=:id'),
                           {'parent': parent_id, 'id': unit_id})

    def old_trigger_baseline(self, unit_id, target):
        # Frozen pre-change trigger, installed only in this disposable schema.
        # Commit DDL before measuring so its table locks do not pollute counts.
        try:
            db.session.execute(text('DROP TRIGGER staffing_topology_lock ON staffing_units'))
            db.session.execute(text(f'''CREATE OR REPLACE FUNCTION staffing_topology_validate()
              RETURNS trigger AS $$ BEGIN
              PERFORM id FROM staffing_people WHERE id IN
                (SELECT person_id FROM staffing_work_assignments WHERE active) ORDER BY id FOR UPDATE;
              IF EXISTS ({INVALID}) THEN RAISE EXCEPTION 'Reparent would duplicate a Sort assignment.'
                USING ERRCODE='23514'; END IF;
              RETURN NULL; END $$ LANGUAGE plpgsql'''))
            db.session.commit()
            self.move(db.session, unit_id, target)
            before = self.locks()
            print('OLD TOPOLOGY LOCKS People/Assignments/Units:', before)
            return before
        finally:
            db.session.rollback()
            install_assignment_guards(db.session.connection())
            db.session.commit()

    def wait_blocked(self, pid):
        deadline = monotonic() + 5
        while monotonic() < deadline:
            with self.engine.connect() as c:
                if c.execute(text('SELECT cardinality(pg_blocking_pids(:pid))'), {'pid':pid}).scalar():
                    return
            sleep(0.01)
        self.fail('Expected a real PostgreSQL lock wait')

    def test_direct_reparent_lock_scope_and_repeat_bootstrap(self):
        area = self.fixture._unit('work_area', 'Isolated', self.fixture.department_one)
        area_id, target = area.id, self.fixture.department_two.id
        person_id = self.fixture.employee_one.id
        StaffingWorkAssignment.query.filter_by(person_id=person_id, active=True).one().work_area_unit_id = area_id
        db.session.commit()
        sync_staffing_assignment_schema()
        sync_staffing_assignment_schema()
        db.session.commit()
        self.assertEqual(self.old_trigger_baseline(area_id, target), [112, 0, 2])
        self.move(db.session, area_id, target)
        print('DIRECT REPARENT LOCKS People/Assignments/Units:', self.locks())
        self.assertEqual(self.locks(), [1, 1, 4])
        with self.engine.connect() as c:
            c.execute(text('SELECT id FROM staffing_people WHERE id=:id FOR UPDATE NOWAIT'), {'id':self.ft_id})
        db.session.commit()
        db.session.remove()
        self.assertEqual(db.session.execute(text('SELECT parent_id FROM staffing_units WHERE id=:id'),
                                           {'id':area_id}).scalar(), target)

    def test_invalid_ft_reparent_is_rejected_and_valid_move_keeps_both_assignments(self):
        sort, operation, _, area = self.new_branch('Day')
        other_sort, _, _, _ = self.new_branch('Other')
        db.session.add(StaffingWorkAssignment(person_id=self.ft_id, work_area_unit_id=area, active=True))
        db.session.commit()
        with self.assertRaises(IntegrityError) as caught:
            self.move(db.session, operation, self.night_id)
        self.assertEqual(caught.exception.orig.pgcode, '23514')
        db.session.rollback()
        self.assertEqual(db.session.execute(text('SELECT parent_id FROM staffing_units WHERE id=:id'),
                                           {'id':operation}).scalar(), sort)
        self.assertEqual(self.old_trigger_baseline(operation, other_sort), [112, 0, 2])
        self.move(db.session, operation, other_sort)
        print('FT REPARENT LOCKS People/Assignments/Units:', self.locks())
        self.assertEqual(self.locks(), [1, 1, 4])
        db.session.commit()
        self.assertEqual(StaffingWorkAssignment.query.filter_by(person_id=self.ft_id, active=True).count(), 2)

    def test_concurrent_hierarchy_moves_cannot_merge_ft_assignments_into_one_sort(self):
        _, op_a, _, area_a = self.new_branch('A')
        _, op_b, _, area_b = self.new_branch('B')
        assignment = StaffingWorkAssignment.query.filter_by(person_id=self.ft_id, active=True).one()
        assignment.work_area_unit_id = area_a
        db.session.add(StaffingWorkAssignment(person_id=self.ft_id, work_area_unit_id=area_b, active=True))
        db.session.commit()
        barrier = Barrier(2)

        def reparent(op):
            try:
                with self.engine.begin() as c:
                    barrier.wait(timeout=10)
                    self.move(c, op, self.night_id)
                return 'committed'
            except IntegrityError as error:
                self.assertEqual(error.orig.pgcode, '23514')
                return 'rejected'

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [pool.submit(reparent, op) for op in (op_a, op_b)]
            self.assertCountEqual([r.result(timeout=15) for r in results], ['committed', 'rejected'])

    def test_incoming_insert_waits_then_validates_new_topology(self):
        _, op, _, area = self.new_branch('Day')
        db.session.commit()
        self.move(db.session, op, self.night_id)

        def insert():
            with self.engine.begin() as c:
                c.execute(text("SET LOCAL lock_timeout='250ms'"))
                c.execute(text('INSERT INTO staffing_work_assignments '
                    '(person_id,work_area_unit_id,active,created_at,updated_at) '
                    'VALUES (:person,:area,true,now(),now())'), {'person':self.ft_id, 'area':area})

        with self.assertRaises(OperationalError) as caught:
            insert()
        self.assertEqual(caught.exception.orig.pgcode, '55P03')
        db.session.commit()
        with self.assertRaises(IntegrityError) as caught:
            insert()
        self.assertEqual(caught.exception.orig.pgcode, '23514')

    def test_inactive_assignment_cannot_reactivate_through_topology_lock(self):
        _, op, _, area = self.new_branch('Day')
        row = StaffingWorkAssignment(person_id=self.ft_id, work_area_unit_id=area, active=False)
        db.session.add(row)
        db.session.commit()
        row_id = row.id
        self.move(db.session, op, self.night_id)

        def activate():
            with self.engine.begin() as c:
                c.execute(text("SET LOCAL lock_timeout='250ms'"))
                c.execute(text('UPDATE staffing_work_assignments SET active=true WHERE id=:id'), {'id':row_id})

        with self.assertRaises(OperationalError) as caught:
            activate()
        self.assertEqual(caught.exception.orig.pgcode, '55P03')
        db.session.commit()
        with self.assertRaises(IntegrityError) as caught:
            activate()
        self.assertEqual(caught.exception.orig.pgcode, '23514')

    def test_invalid_parent_type_rolls_back(self):
        area_id = self.fixture.area_one.id
        with self.assertRaises(IntegrityError) as caught:
            self.move(db.session, area_id, self.night_id)
        self.assertEqual(caught.exception.orig.pgcode, '23514')
        db.session.rollback()

    def test_noop_parent_update_does_not_lock_people(self):
        area_id = self.fixture.area_one.id
        db.session.execute(text('UPDATE staffing_units SET parent_id=parent_id WHERE id=:id'), {'id':area_id})
        self.assertEqual(self.locks(), [0, 0, 1])
        db.session.rollback()

    def test_multirow_swap_validates_final_topology_not_intermediate_row(self):
        sort_a, op_a, _, area_a = self.new_branch('A')
        sort_b, op_b, _, area_b = self.new_branch('B')
        StaffingWorkAssignment.query.filter_by(person_id=self.ft_id, active=True).one().work_area_unit_id = area_a
        db.session.add(StaffingWorkAssignment(person_id=self.ft_id, work_area_unit_id=area_b, active=True))
        db.session.commit()
        db.session.execute(text('UPDATE staffing_units SET parent_id=CASE WHEN id=:a THEN :sb ELSE :sa END '
            'WHERE id IN (:a,:b)'), {'a':op_a, 'b':op_b, 'sa':sort_a, 'sb':sort_b})
        db.session.commit()
        self.assertEqual(StaffingWorkAssignment.query.filter_by(person_id=self.ft_id, active=True).count(), 2)

    def test_assignment_commits_first_waiting_reparent_sees_new_assignment(self):
        _, op, _, area = self.new_branch('Day')
        db.session.commit()
        ready, pids = Event(), []

        def reparent():
            try:
                with self.engine.begin() as c:
                    pids.append(c.execute(text('SELECT pg_backend_pid()')).scalar())
                    ready.set()
                    self.move(c, op, self.night_id)
                return 'committed'
            except IntegrityError as error:
                self.assertEqual(error.orig.pgcode, '23514')
                return 'rejected'

        with self.engine.connect() as writer:
            transaction = writer.begin()
            writer.execute(text('INSERT INTO staffing_work_assignments '
                '(person_id,work_area_unit_id,active,created_at,updated_at) '
                'VALUES (:person,:area,true,now(),now())'), {'person':self.ft_id, 'area':area})
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(reparent)
                try:
                    self.assertTrue(ready.wait(timeout=5))
                    self.wait_blocked(pids[0])
                finally:
                    transaction.commit()
                self.assertEqual(pending.result(timeout=10), 'rejected')

    def test_other_sort_assignment_waits_then_rejects_conflict_after_reparent(self):
        _, op, _, area = self.new_branch('Day')
        _, target_op, _, target_area = self.new_branch('Target')
        target_sort = db.session.execute(text('SELECT parent_id FROM staffing_units WHERE id=:id'),
                                         {'id':target_op}).scalar()
        row = StaffingWorkAssignment.query.filter_by(person_id=self.ft_id, active=True).one()
        row_id = row.id
        db.session.add(StaffingWorkAssignment(person_id=self.ft_id, work_area_unit_id=area, active=True))
        db.session.commit()
        self.move(db.session, op, target_sort)
        ready, pids = Event(), []

        def change_other_assignment():
            try:
                with self.engine.begin() as c:
                    pids.append(c.execute(text('SELECT pg_backend_pid()')).scalar())
                    ready.set()
                    c.execute(text('UPDATE staffing_work_assignments SET work_area_unit_id=:area WHERE id=:id'),
                              {'area':target_area, 'id':row_id})
                return 'committed'
            except IntegrityError as error:
                self.assertEqual(error.orig.pgcode, '23514')
                return 'rejected'

        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(change_other_assignment)
            try:
                self.assertTrue(ready.wait(timeout=5))
                self.wait_blocked(pids[0])
            finally:
                db.session.commit()
            self.assertEqual(pending.result(timeout=10), 'rejected')
