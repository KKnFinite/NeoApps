"""Narrow Bulk Change lock proof using real independent PostgreSQL sessions."""
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import os
import unittest

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app.extensions import db
from app.models import User, StaffingPerson, StaffingWorkAssignment, StaffingShiftFlowPlan
from app.services import neostaffing_bulk_change as service
from app.services import neostaffing as staffing
from app.services.neostaffing_assignment_schema import sync_staffing_assignment_schema
from app.services.neostaffing_employee_id_schema import sync_staffing_employee_id_schema
from tests import test_neostaffing_employee_id_schema as fixtures


@unittest.skipUnless(os.environ.get('NEOSTAFFING_TEST_POSTGRES_URL'), 'Requires disposable PostgreSQL')
class BulkLockScopePostgresTest(unittest.TestCase):
    tearDown = fixtures.EmployeeIdPostgresTest.tearDown

    def setUp(self):
        fixtures.EmployeeIdPostgresTest.setUp(self)
        sync_staffing_assignment_schema()
        sync_staffing_employee_id_schema()
        db.session.commit()
        self.original_area = self.fixture.area_one.id

    def footprint(self):
        counts = []
        for table in ('staffing_people', 'staffing_work_assignments', 'staffing_units',
                      'staffing_leadership_assignments', 'staffing_reporting_relationships'):
            with self.engine.connect() as c:
                total = c.execute(text('SELECT count(*) FROM ' + table)).scalar()
                available = c.execute(text('SELECT id FROM ' + table + ' FOR UPDATE SKIP LOCKED')).all()
                counts.append(total - len(available))
        return counts

    def apply(self, package):
        self.fixture._service_call(service.apply_workspace, package,
                                   db.session.get(User, self.fixture.actor_id))

    def test_exact_small_and_large_lock_footprints(self):
        for size in (2, 100):
            package = self.fixture.workspace(self.ids[:size])
            service.BulkChangeDataBundle(lock=True)  # Previous lock boundary.
            self.assertEqual(self.footprint(), [119, 112, 7, 7, 6])
            db.session.rollback()
            db.session.remove()
            self.apply(package)
            print('BULK LOCKS', size, self.footprint())
            self.assertEqual(self.footprint(), [size + 1, size, 7, 1, 0])
            db.session.rollback()
            db.session.remove()

    def test_unrelated_assignment_write_commits_while_bulk_is_open(self):
        self.apply(self.fixture.workspace(self.ids[:2]))

        def clear(pid):
            with self.fixture.app.app_context():
                db.session.execute(text("SET LOCAL lock_timeout='250ms'"))
                person = db.session.get(StaffingPerson, pid)
                staffing.clear_work_assignment(person, expected_version=str(person.shift_flow_version or 0))
                db.session.commit()

        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(clear, self.ids[-1]).result(timeout=5)
            with self.assertRaises(OperationalError) as caught:
                pool.submit(clear, self.ids[0]).result(timeout=5)
            self.assertEqual(caught.exception.orig.pgcode, '55P03')
        db.session.commit()
        db.session.remove()
        self.assertEqual(StaffingWorkAssignment.query.filter_by(person_id=self.ids[-1], active=True).count(), 0)
        self.assertEqual(StaffingWorkAssignment.query.filter(
            StaffingWorkAssignment.person_id.in_(self.ids[:2]), StaffingWorkAssignment.active.is_(True),
            StaffingWorkAssignment.work_area_unit_id == self.fixture.target_id).count(), 2)

    def test_committed_change_between_snapshot_and_locks_rejects_package(self):
        package = self.fixture.workspace(self.ids[:2])
        lock = service._lock_workspace_dependencies

        def edited(*args):
            with self.engine.begin() as c:
                c.execute(text('UPDATE staffing_people SET first_name=:name WHERE id=:id'),
                          {'name':'Newer', 'id':self.ids[0]})
            return lock(*args)

        with patch.object(service, '_lock_workspace_dependencies', edited):
            with self.assertRaisesRegex(ValueError, service.LIVE_DATA_CHANGED_MESSAGE):
                self.apply(package)
        db.session.rollback()
        db.session.remove()
        self.assertEqual(db.session.get(StaffingPerson, self.ids[0]).first_name, 'Newer')
        self.assertEqual(StaffingWorkAssignment.query.filter(
            StaffingWorkAssignment.person_id.in_(self.ids[:2]), StaffingWorkAssignment.active.is_(True),
            StaffingWorkAssignment.work_area_unit_id == self.original_area).count(), 2)

    def test_hierarchy_package_does_not_lock_employee_outside_subtree(self):
        package = self.fixture.workspace([])
        package['units'][str(self.original_area)] = {'parent_id':self.fixture.department_two.id}
        simulation = service._simulate(package, service.BulkChangeDataBundle())
        for row in simulation['relationship_reviews']:
            package['reporting'][row['person']['ref']] = {'action':'keep', 'target_ref':None}
        unrelated_id = self.fixture.employee_two.id
        self.apply(package)
        print('HIERARCHY BULK LOCKS', self.footprint())
        self.assertEqual(self.footprint(), [114, 111, 6, 3, 2])
        with self.engine.connect() as c:
            c.execute(text('SELECT id FROM staffing_people WHERE id=:id FOR UPDATE NOWAIT'), {'id':unrelated_id})
        db.session.rollback()

    def test_actor_leadership_and_used_ancestry_stay_protected(self):
        actor_person = self.fixture.ft_one.id
        self.apply(self.fixture.workspace(self.ids[:2]))
        for statement, identity in (
            ('UPDATE staffing_leadership_assignments SET active=false WHERE person_id=:id', actor_person),
            ('UPDATE staffing_units SET active=false WHERE id=:id', self.original_area),
        ):
            with self.engine.connect() as c:
                c.execute(text("SET LOCAL lock_timeout='250ms'"))
                with self.assertRaises(OperationalError) as caught:
                    c.execute(text(statement), {'id':identity})
                self.assertEqual(caught.exception.orig.pgcode, '55P03')
        db.session.rollback()

    def test_bulk_reparent_preserves_canonical_home_lifecycle_with_database_guards(self):
        self.fixture.operation_one.name = 'Ramp'
        self.fixture.department_one.name = 'Shift'
        person_id = self.fixture.employee_one.id
        department_id = self.fixture.department_one.id
        target_id = self.fixture.operation_two.id
        db.session.add(StaffingShiftFlowPlan(staffing_person_id=person_id,
            sort_start_work_area_id=self.original_area, setup_work_area_id=self.original_area))
        db.session.commit()
        package = self.fixture.workspace([])
        package['units'][str(department_id)] = {'parent_id':target_id}
        simulation = service._simulate(package, service.BulkChangeDataBundle())
        for row in simulation['relationship_reviews']:
            package['reporting'][row['person']['ref']] = {'action':'keep', 'target_ref':None}
        response, _, _ = self.fixture.profile(package)
        self.assertEqual(response.status_code, 302)
        db.session.remove()
        self.assertIsNone(StaffingShiftFlowPlan.query.filter_by(staffing_person_id=person_id).first())
        self.assertEqual(StaffingWorkAssignment.query.filter_by(person_id=person_id, active=True).one().work_area_unit_id,
                         self.original_area)

    def test_atomic_assignment_failure_with_all_database_guards_installed(self):
        self.fixture.test_lifecycle_failure_rolls_back_and_clears_snapshot()
