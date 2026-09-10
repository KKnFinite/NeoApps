"""Opt-in real PostgreSQL tests with isolated synthetic schemas.

Set NEOERMAC_TEST_POSTGRES_URL to a loopback database named neoermac_test_*.
Never reads DATABASE_URL or production configuration.
"""
import os
import unittest
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from datetime import time
from unittest.mock import patch
from sqlalchemy import create_engine, text, event
from sqlalchemy.engine import make_url
from app import create_app
from app.extensions import db
from app.models import NeoErmacDoorPull, NeoErmacUldRequest, SortDateMission
from tests import test_neoermac_routes as fixtures
from tests import test_neoermac_pull_integrity as pulls
from app.services import uld_requests

URI = os.environ.get('NEOERMAC_TEST_POSTGRES_URL')

@unittest.skipUnless(URI, 'Requires disposable local NEOERMAC_TEST_POSTGRES_URL')
class NeoErmacPostgresTest(unittest.TestCase):
    _assign_lineup_destination = pulls.NeoErmacPullIntegrityTest._assign_lineup_destination
    _add_operation_departure = pulls.NeoErmacPullIntegrityTest._add_operation_departure
    _login_approved_user = pulls.NeoErmacPullIntegrityTest._login_approved_user
    form = pulls.NeoErmacPullIntegrityTest.form
    post = pulls.NeoErmacPullIntegrityTest.post
    _add_master_departure = fixtures.NeoErmacRoutesTest._add_master_departure

    def setUp(self):
        url = make_url(URI)
        if (url.get_backend_name() != 'postgresql' or url.host not in ('127.0.0.1', 'localhost')
                or not (url.database or '').startswith('neoermac_test_')):
            raise ValueError('PostgreSQL proof requires a loopback neoermac_test_* database')
        self.schema = 'proof_' + uuid4().hex
        self.admin = create_engine(URI)
        with self.admin.begin() as conn:
            conn.execute(text('CREATE SCHEMA ' + self.schema))
        def pg_app(config):
            config.SQLALCHEMY_DATABASE_URI = URI
            config.SQLALCHEMY_ENGINE_OPTIONS = {'connect_args': {
                'options': '-csearch_path=' + self.schema + ' -clock_timeout=10000 -cstatement_timeout=30000'}}
            return create_app(config, auto_bootstrap=False)
        with patch.object(fixtures, 'create_app', side_effect=pg_app):
            pulls.NeoErmacPullIntegrityTest.setUp(self)
        self.assertEqual(db.session.execute(text('SHOW transaction_isolation')).scalar(), 'read committed')

    def tearDown(self):
        db.session.remove()
        db.engine.dispose()
        self.context.pop()
        with self.admin.begin() as conn:
            conn.execute(text('DROP SCHEMA ' + self.schema + ' CASCADE'))
        self.admin.dispose()

    def race(self, forms, path='/neoermac/door-view/pull-autosave'):
        cookie = self.client.get_cookie('session').value
        db.session.commit()
        barrier = Barrier(len(forms))
        backend_pids = set()
        def connection_used(conn, cursor, statement, parameters, context, many):
            backend_pids.add(conn.connection.driver_connection.get_backend_pid())
        def submit(form):
            client = self.app.test_client()
            client.set_cookie('session', cookie)
            barrier.wait(timeout=15)
            response = client.post(path, data=form)
            return response.status_code
        event.listen(db.engine, 'before_cursor_execute', connection_used)
        try:
            with ThreadPoolExecutor(max_workers=len(forms)) as pool:
                result = list(pool.map(submit, forms))
        finally:
            event.remove(db.engine, 'before_cursor_execute', connection_used)
        self.assertEqual(len(backend_pids), len(forms))
        db.session.expire_all()
        return sorted(result)

    def test_pg_first_creation(self):
        self.assertEqual(self.race([self.form(), self.form('mix','01:55')]), [200,200])
        row = NeoErmacDoorPull.query.one()
        self.assertEqual((row.actual_pure_pull_time_local,row.actual_mix_pull_time_local), (time(1,45),time(1,55)))
        self.assertEqual(row.sort_date_mission_id, self.mission.id)
        self.assertEqual((self.mission.actual_pure_pull_time_local, self.mission.actual_mix_pull_time_local),
                         (time(1,45),time(1,55)))

    def test_pg_same_field(self):
        self.assertEqual(self.post(self.form()).status_code,200)
        self.assertEqual(self.race([self.form(value='01:46'),self.form(value='01:47')]),[200,409])
        self.assertIn(NeoErmacDoorPull.query.one().actual_pure_pull_time_local, (time(1,46),time(1,47)))
        self.assertEqual(self.mission.actual_pure_pull_time_local,
                         NeoErmacDoorPull.query.one().actual_pure_pull_time_local)

    def test_pg_different_doors_aggregate(self):
        self.assertEqual(self.race([self.form(value='01:45'),self.form(door='D4',value='01:55')]),[200,200])
        self.assertEqual(NeoErmacDoorPull.query.count(),2)
        self.assertEqual(db.session.get(SortDateMission,self.mission.id).actual_pure_pull_time_local,time(1,55))

    def _assert_uld_increments(self, seed):
        form={'action':'save_uld_request','door':'D1','uld_a2_count':'1','uld_a1_count':'2','uld_amp_count':'3'}
        if seed:
            self.assertEqual(self.client.post('/neoermac/door-view',data=form).status_code,302)
        # Preload the same old ORM row on both connections before contending
        # for the real lock. The locked read must refresh those identity maps.
        rendezvous=Barrier(2)
        original=uld_requests._lock_uld_increment
        stale_rows=[]
        backend_pids=set()
        def synchronized(gateway):
            backend_pids.add(db.session.connection().connection.driver_connection.get_backend_pid())
            stale_rows.append(NeoErmacUldRequest.query.first())
            rendezvous.wait(timeout=15)
            return original(gateway)
        with patch.object(uld_requests,'_lock_uld_increment',side_effect=synchronized):
            self.assertEqual(self.race([form,form],'/neoermac/door-view'),[302,302])
        self.assertEqual(len(backend_pids),2)
        row=NeoErmacUldRequest.query.one()
        self.assertEqual((row.a2_count,row.a1_count,row.amp_count),(3,6,9) if seed else (2,4,6))

    def test_pg_uld_first_increments(self):
        self._assert_uld_increments(seed=False)

    def test_pg_uld_increments(self):
        self._assert_uld_increments(seed=True)

    def test_pg_revision_query_plans_are_scoped_and_read_only(self):
        from tests.neoermac_postgres_plans import explain_polls
        report = explain_polls(self)
        large_tables = {
            'sort_date_missions', 'neoermac_door_pulls',
            'sort_date_parking_assignments', 'sort_date_google_mission_links',
            'neoermac_uld_requests', 'neosektor_uld_on_the_way_events',
        }
        for name, screen in report['screens'].items():
            with self.subTest(screen=name):
                self.assertEqual(screen['writes'], 0)
                self.assertEqual(screen['commits'], 0)
                self.assertLessEqual(screen['selects'], 30 if screen['changed'] else 12)
                scans = [scan for entry in screen['plans'] for scan in entry['scans']
                         if scan.get('Relation Name') in large_tables]
                self.assertTrue(scans)
                for scan in scans:
                    self.assertNotEqual(scan['Node Type'], 'Seq Scan', scan)
                    self.assertLessEqual(scan['Actual Rows'], 100, scan)
