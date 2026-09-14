"""Independent PostgreSQL transactions at the final authorization boundary."""
from concurrent.futures import ThreadPoolExecutor
import os
from threading import Event
from time import monotonic, sleep
import unittest
from unittest.mock import patch

from sqlalchemy import text, event
from sqlalchemy.exc import OperationalError
from flask_login import login_user

from app.extensions import db
from app.models import User, PortalAppAccess, PermissionRule, StaffingWorkAssignment
from app.auth.routes import _deny_portal_app_access
from app.services import neostaffing_bulk_change as service
from app.services.permission_rules import user_can
from app.services.neostaffing_assignment_schema import sync_staffing_assignment_schema
from app.services.neostaffing_employee_id_schema import sync_staffing_employee_id_schema
from tests import test_neostaffing_employee_id_schema as fixture_module


@unittest.skipUnless(os.environ.get('NEOSTAFFING_TEST_POSTGRES_URL'), 'Requires disposable PostgreSQL')
class BulkAuthorityPostgresTest(unittest.TestCase):
    tearDown = fixture_module.EmployeeIdPostgresTest.tearDown

    def setUp(self):
        fixture_module.EmployeeIdPostgresTest.setUp(self)
        sync_staffing_assignment_schema()
        sync_staffing_employee_id_schema()
        self.fixture.grandmaster_user.role = 'grandmaster'
        unlinked_pt = self.fixture._person('AUTH-PT', 'part_time_supervisor', 'Auth', 'PT')
        db.session.commit()
        self.actor_id = self.fixture.actor_id
        self.admin_id = self.fixture.grandmaster_user.id
        self.other_id = self.fixture.watcher_user.id
        self.package = self.fixture.workspace(self.ids[:2])
        self.original_area = self.fixture.area_one.id
        self.pt_employee_id = unlinked_pt.employee_id
        db.session.remove()

    def change_authority(self, kind, actor_id=None):
        with self.fixture.app.test_request_context('/authorization-proof'):
            db.session.execute(text("SET LOCAL lock_timeout='250ms'"))
            actor_id = actor_id or self.actor_id
            if kind in ('deny', 'role'):
                login_user(db.session.get(User, self.admin_id))
                access = PortalAppAccess.query.filter_by(user_id=actor_id, app_code='neostaffing').one()
                if kind == 'deny':
                    _deny_portal_app_access(access, 'Disposable race proof')
                else:
                    access.role = 'watcher'
            elif kind in ('rule', 'structure_rule', 'management_rule'):
                key = {'rule':service.PEOPLE_EDIT_PERMISSION,
                       'structure_rule':service.ORG_CHART_EDIT_STRUCTURE_PERMISSION,
                       'management_rule':service.MANAGEMENT_ASSIGN_PERMISSION}[kind]
                PermissionRule.query.filter_by(permission_key=key).one().minimum_role = 'grandmaster'
            elif kind == 'deactivate':
                db.session.get(User, actor_id).is_active = False
            elif kind == 'session':
                db.session.get(User, actor_id).auth_session_version += 1
            elif kind == 'identity':
                db.session.get(User, actor_id).employee_id = self.pt_employee_id
            db.session.commit()

    def concurrent_change(self, kind, actor_id=None):
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(self.change_authority, kind, actor_id).result(timeout=5)

    def denied_after_change(self, kind):
        original = service.lock_staffing_write_authority
        writes = []

        def capture(conn, _cursor, statement, _parameters, _context, _many):
            if conn is mutation_connection and statement.lstrip().split()[0] in ('UPDATE','INSERT','DELETE'):
                writes.append(statement)

        def change_then_lock(user, keys):
            self.assertTrue(user_can(service.PEOPLE_EDIT_PERMISSION, user))  # Seed stale cache.
            user_can(service.MANAGEMENT_ASSIGN_PERMISSION, user)
            user_can(service.ORG_CHART_EDIT_STRUCTURE_PERMISSION, user)
            self.concurrent_change(kind)
            return original(user, keys)

        with self.fixture.app.test_request_context('/neostaffing/bulk-change'):
            actor = db.session.get(User, self.actor_id)
            mutation_connection = db.session.connection()
            event.listen(self.engine, 'before_cursor_execute', capture)
            try:
                with patch.object(service, 'lock_staffing_write_authority', change_then_lock):
                    with self.assertRaises(ValueError):
                        service.apply_workspace(self.package, actor)
                self.assertEqual(writes, [])
            finally:
                event.remove(self.engine, 'before_cursor_execute', capture)
                db.session.rollback()
        db.session.remove()
        areas = db.session.query(StaffingWorkAssignment.work_area_unit_id).filter(
            StaffingWorkAssignment.person_id.in_(self.ids[:2])).all()
        self.assertEqual(areas, [(self.original_area,), (self.original_area,)])

    def test_original_committed_revocation_rejects_cached_permissions_atomically(self):
        self.denied_after_change('deny')

    def test_committed_role_downgrade_rejects(self):
        self.denied_after_change('role')

    def test_committed_permission_threshold_change_rejects(self):
        self.denied_after_change('rule')

    def test_committed_user_deactivation_rejects(self):
        self.denied_after_change('deactivate')

    def test_committed_session_revocation_rejects(self):
        self.denied_after_change('session')

    def test_committed_user_identity_change_recomputes_actor_classification(self):
        self.denied_after_change('identity')

    def hierarchy_package(self):
        self.package['people'] = {}
        self.package['units'][str(self.fixture.area_one.id)] = {'parent_id':self.fixture.department_two.id}
        simulation = service._simulate(self.package, service.BulkChangeDataBundle())
        for row in simulation['relationship_reviews']:
            self.package['reporting'][row['person']['ref']] = {'action':'keep', 'target_ref':None}

    def test_hierarchy_permission_change_is_freshly_validated(self):
        self.hierarchy_package()
        self.denied_after_change('structure_rule')

    def test_management_permission_change_is_freshly_validated(self):
        self.hierarchy_package()
        self.denied_after_change('management_rule')

    def test_revoker_wins_lock_race_waiting_authorization_reads_committed_denial(self):
        ready, release = Event(), Event()
        revoker_pid = []

        def revoke_in_flight():
            with self.engine.begin() as connection:
                revoker_pid.append(connection.execute(text('SELECT pg_backend_pid()')).scalar())
                connection.execute(text("UPDATE portal_app_accesses SET status='denied' "
                    "WHERE user_id=:id AND app_code='neostaffing'"), {'id':self.actor_id})
                ready.set()
                if not release.wait(timeout=10):
                    raise AssertionError('Revoker release timed out')

        def apply():
            with self.fixture.app.test_request_context('/neostaffing/bulk-change'):
                try:
                    service.apply_workspace(self.package, db.session.get(User,self.actor_id))
                    db.session.commit()
                    return 'committed'
                except ValueError:
                    db.session.rollback()
                    return 'denied'

        with ThreadPoolExecutor(max_workers=2) as pool:
            revoker = pool.submit(revoke_in_flight)
            try:
                self.assertTrue(ready.wait(timeout=5))
                writer = pool.submit(apply)
                blocked = False
                deadline = monotonic() + 5
                with self.engine.connect().execution_options(isolation_level='AUTOCOMMIT') as observer:
                    while monotonic() < deadline:
                        blocked = observer.execute(text('SELECT EXISTS (SELECT 1 FROM pg_stat_activity '
                            'WHERE :pid=ANY(pg_blocking_pids(pid)))'), {'pid':revoker_pid[0]}).scalar()
                        if blocked:
                            break
                        sleep(0.01)
                self.assertTrue(blocked, 'Final authorization must wait for the revoker')
            finally:
                release.set()
            revoker.result(timeout=5)
            self.assertEqual(writer.result(timeout=5), 'denied')
        db.session.remove()
        self.assertTrue(all(row.work_area_unit_id == self.original_area for row in
            StaffingWorkAssignment.query.filter(StaffingWorkAssignment.person_id.in_(self.ids[:2])).all()))

    def test_valid_apply_holds_only_required_authority_rows_until_commit(self):
        with self.fixture.app.test_request_context('/neostaffing/bulk-change'):
            service.apply_workspace(self.package, db.session.get(User,self.actor_id))
            for kind in ('deny', 'role', 'rule', 'deactivate', 'session'):
                with self.subTest(kind=kind), self.assertRaises(OperationalError) as caught:
                    self.concurrent_change(kind)
                self.assertEqual(caught.exception.orig.pgcode, '55P03')
            self.concurrent_change('deny', self.other_id)  # Unrelated user is not blocked.
            self.concurrent_change('deactivate', self.other_id)
            with self.engine.begin() as observer:
                observer.execute(text("SET LOCAL lock_timeout='250ms'"))
                rules = observer.execute(text(
                    'SELECT id,permission_key FROM permission_rules ORDER BY id')).all()
                blocked = []
                for rid, key in rules:
                    try:
                        with observer.begin_nested():
                            observer.execute(text('SELECT id FROM permission_rules WHERE id=:id FOR UPDATE NOWAIT'), {'id':rid})
                    except OperationalError as error:
                        self.assertEqual(error.orig.pgcode, '55P03')
                        blocked.append(key)
                self.assertCountEqual(blocked, [service.BULK_CHANGE_PERMISSION,service.PEOPLE_EDIT_PERMISSION])
            db.session.commit()
        # Once the successful mutation commits, this revocation can commit.
        self.concurrent_change('deny')
        db.session.remove()
        self.assertTrue(all(row.work_area_unit_id == self.fixture.target_id for row in
            StaffingWorkAssignment.query.filter(StaffingWorkAssignment.person_id.in_(self.ids[:2])).all()))
        with self.fixture.app.test_request_context('/neostaffing/bulk-change'):
            self.assertFalse(user_can(service.BULK_CHANGE_PERMISSION, db.session.get(User,self.actor_id)))
