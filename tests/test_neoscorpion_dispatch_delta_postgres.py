"""Private mission deltas and independent readers in loopback PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from unittest.mock import patch
from uuid import uuid4
import os
import json
import unittest
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from app import create_app
from app.extensions import db
from app.models import NeoScorpionFuelingEvent, NeoScorpionFuelWorkState, NeoScorpionSortTruck, SortDateMission
from tests import test_neoscorpion_dispatch_delta as cases
from tests import test_neoscorpion_dispatch_planning as fixtures

URI=os.environ.get('NEOSTAFFING_TEST_POSTGRES_URL')


@unittest.skipUnless(URI, 'Requires disposable loopback PostgreSQL')
class DispatchDeltaPostgresTest(cases.DispatchDeltaTest):
    def setUp(self):
        url=make_url(URI)
        if url.host not in {'localhost','127.0.0.1'} or not url.database.startswith('neostaffing_test_'):
            raise RuntimeError('Refusing non-disposable PostgreSQL')
        schema='dispatch_delta_'+uuid4().hex
        engine=create_engine(URI,connect_args={'connect_timeout':5})
        with engine.begin() as connection: connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        def drop():
            with engine.begin() as connection: connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            engine.dispose()
        self.addCleanup(drop)
        def local(config):
            return create_app(type('DeltaPostgresConfig',(config,),dict(SQLALCHEMY_DATABASE_URI=URI,
                SQLALCHEMY_ENGINE_OPTIONS={'connect_args':{'connect_timeout':5,'options':
                    f'-csearch_path={schema} -clock_timeout=3000 -cstatement_timeout=10000'}})))
        with patch.object(fixtures,'create_app',local): super().setUp()

    def test_arrival_changes_reach_critical_rows_without_full_arrival_hydration(self):
        case = self.case
        app = case.client.application
        _, previous = self.initial()
        revision = case.client.get('/neoscorpion/fuel-dispatch/revision').json
        case.arrival.arrival_status = 'unloaded'
        db.session.commit()
        self.assertEqual(case.client.get('/neoscorpion/fuel-dispatch/revision').json, revision)

        arrivals, selects = [], []
        def loaded(session, item):
            if isinstance(item, SortDateMission) and item.mission_type == 'arrival':
                arrivals.append(item.id)
        def record(conn, cursor, sql, *args):
            selects.append(' '.join(sql.lower().split()))
        with app.app_context():
            engine = db.engine
            event.listen(Session, 'loaded_as_persistent', loaded)
            event.listen(engine, 'before_cursor_execute', record)
            try:
                response = case.client.get('/neoscorpion/fuel-dispatch/live-panel',
                    query_string={'completion_only': '1', 'delta': '1'},
                    headers={'X-Neo-Dispatch-Fingerprints': json.dumps(previous)})
            finally:
                event.remove(engine, 'before_cursor_execute', record)
                event.remove(Session, 'loaded_as_persistent', loaded)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Unloaded', response.json['html'])
        self.assertIn('>COMPLETE</button>', response.json['html'])
        self.assertEqual(arrivals, [])
        self.assertEqual(sum('from neoscorpion_fueling_events ' in sql for sql in selects), 1)
        self.assertEqual(sum('from users where users.is_active' in sql for sql in selects), 1)
        self.assertEqual(sum('from gateway_memberships join neo_nodes' in sql for sql in selects), 1)
        _, full_page_critical = self.initial()
        self.assertEqual(response.json['fingerprints'], full_page_critical)

    def test_new_uplift_keeps_posted_inventory_and_matches_initial_controls(self):
        from app.services import neoscorpion as fuel
        c = self.case
        fuel.complete_fueled_assignment(c.gateway, c.dispatcher, c.assignment.id)
        db.session.commit()
        posting = NeoScorpionFuelingEvent.query.filter_by(fuel_assignment_id=c.assignment.id).one()
        _, completed = self.initial()
        fuel.start_follow_up_fuel_cycle(c.gateway, c.dispatcher, c.assignment.id, "uplift", "55.0",
                                       c.fueler.id, c.truck.id)
        db.session.commit()
        critical = c.capture(completed).json
        self.assertIn("UPLIFT", critical["html"])
        self.assertEqual(c.assignment.current_cycle_number, 2)
        self.assertEqual(c.selection.current_gallons, 7000)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(), 1)
        with c.client.application.test_request_context():
            context = fuel.fuel_dispatch_context(c.gateway, include_spear=False)
            truck = next(v for v in context["truck_visuals"] if v["truck_id"] == c.truck.id)
            self.assertEqual(truck["inventory_posting_id"], posting.id)
        _, expected = self.initial()
        self.assertEqual(critical["fingerprints"], expected)

    def test_independent_simultaneous_readers_preserve_visit_event_and_inventory(self):
        c=self.case;app=c.client.application;email=c.dispatcher.email
        before=(NeoScorpionFuelWorkState.query.count(),NeoScorpionFuelingEvent.query.count(),c.selection.current_gallons)
        readers=[]
        # Establish authentication before racing read-only requests. Holding a
        # synthetic outer app context across login can retain last-login locks.
        for _ in range(2):
            with app.app_context():
                client=app.test_client();client.post('/login',data={'email':email,'password':'TestPassword123!'})
                initial=client.get('/neoscorpion/fuel-dispatch')
                readers.append((client,*cases.fingerprints(initial.text)))
        capturing=Event();start=Barrier(2,action=capturing.set);writes=[]
        engine=db.engine
        def record(conn,cur,sql,*args):
            if capturing.is_set() and sql.lstrip().split()[0].upper() in {'INSERT','UPDATE','DELETE'}:writes.append(sql)
        def read(index):
            with app.app_context():
                client,full,critical=readers[index]
                start.wait(10)
                try:
                    response=client.get('/neoscorpion/fuel-dispatch/live-panel?missions_delta=1',headers={
                        'X-Neo-Dispatch-Parts':__import__('json').dumps(full),
                        'X-Neo-Dispatch-Fingerprints':__import__('json').dumps(critical)})
                    return response.status_code, response.json['html'].count('data-dispatch-reuse=')
                finally:
                    db.session.remove()
        event.listen(engine,'before_cursor_execute',record)
        try:
            with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(read,range(2)))
        finally: event.remove(engine,'before_cursor_execute',record)
        self.assertEqual(writes,[])
        self.assertTrue(all(status==200 and reused>0 for status,reused in results))
        db.session.expire_all()
        self.assertEqual((NeoScorpionFuelWorkState.query.count(),NeoScorpionFuelingEvent.query.count(),
            db.session.get(NeoScorpionSortTruck,c.selection.id).current_gallons),before)
