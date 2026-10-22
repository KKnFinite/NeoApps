"""Package 2 real concurrent HTTP requests, in disposable UUID schemas only."""
from threading import Barrier, Event
import unittest
from unittest.mock import patch
from sqlalchemy.orm import Query
from app.extensions import db
from app.models import NeoScorpionSettings, NeoScorpionFuelTruck, NeoScorpionSortTruck
from tests import test_neoscorpion_segments_postgres as fixtures
from tests.test_neoscorpion_package2 import Forms


@unittest.skipUnless(fixtures.URI, 'Requires disposable local PostgreSQL')
class AdminReliabilityPostgresTest(unittest.TestCase):
    setUp=fixtures.PhysicalSegmentPostgresTest.setUp
    drop_schema=fixtures.PhysicalSegmentPostgresTest.drop_schema
    prepare=fixtures.PhysicalSegmentPostgresTest.prepare
    concurrent=fixtures.PhysicalSegmentPostgresTest.concurrent

    def admins(self):
        for name in ('pg-admin-a','pg-admin-b'): self.fixture._login_user(name,'master')
        clients=[self.fixture.app.test_client() for _ in range(2)]
        for client,name in zip(clients,('pg-admin-a','pg-admin-b')):
            # The reused fixture retains a parent app context. Isolate Flask-
            # Login's g cache just as independent real HTTP requests do.
            with self.fixture.app.app_context():
                response=client.post('/login',data=dict(email=name+'@example.test',password='TestPassword123!'))
                self.assertEqual(response.status_code,302)
        return clients

    def form(self, client):
        with self.fixture.app.app_context():
            response=client.get('/neoscorpion/settings')
            self.assertEqual(response.status_code,200,response.get_data(as_text=True)[:200])
            return next(f for f in Forms(response.get_data(as_text=True)).forms if f.get('action')=='save_settings')

    def test_simultaneous_settings_accept_one_baseline_and_preserve_winner(self):
        clients=self.admins()
        forms=[self.form(c) for c in clients]
        responses=[]
        def save(i,_g,_f,_d):
            response=clients[i].post('/neoscorpion/settings',data={**forms[i],'fuel_density_lbs_per_gallon':str(6.8+i/10)})
            responses.append((i,response.status_code)); return response.status_code
        self.assertEqual(sorted(self.concurrent(save)),[302,409])
        winner=next(i for i,status in responses if status==302)
        self.assertAlmostEqual(NeoScorpionSettings.query.one().fuel_density_lbs_per_gallon,6.8+winner/10)

    def test_racing_first_settings_creation_and_independent_apu_form(self):
        clients=self.admins()
        # Missing shared-row creation is also serialized by the stable gateway.
        db.session.delete(self.settings);db.session.commit()
        forms=[self.form(c) for c in clients]
        result=self.concurrent(lambda i,_g,_f,_d:clients[i].post('/neoscorpion/settings',
            data={**forms[i],'fuel_density_lbs_per_gallon':str(6.8+i/10)}).status_code)
        self.assertEqual(sorted(result),[302,409]);self.assertEqual(NeoScorpionSettings.query.count(),1)

    def test_simultaneous_duplicate_create_uses_constraint_and_rolls_back_cleanly(self):
        clients=self.admins(); barrier=Barrier(2); original=Query.first; replies=[]
        def after_duplicate_check(query):
            result=original(query)
            descriptions=query.column_descriptions
            if descriptions and descriptions[0].get('entity') is NeoScorpionFuelTruck:
                params=query.statement.compile().params
                if params.get('truck_number_1')=='PG-RACE':
                    self.assertIsNone(result);barrier.wait(timeout=10)
            return result
        def create(i,_g,_f,_d):
            response=clients[i].post('/neoscorpion/truck-manager',data=dict(action='save_truck',truck_number=' pg-race ',
                capacity_gallons='10000',description='Race draft '+str(i),is_active='1'))
            replies.append(response);return response.status_code
        before=NeoScorpionSortTruck.query.count()
        with patch.object(Query,'first',after_duplicate_check):
            self.assertEqual(sorted(self.concurrent(create)),[302,400])
        error=next(r for r in replies if r.status_code==400).get_data(as_text=True)
        self.assertIn('already exists',error);self.assertNotIn('FUEL TRUCK SAVED.',error)
        self.assertEqual(NeoScorpionFuelTruck.query.filter_by(truck_number='PG-RACE').count(),1)
        self.assertEqual(NeoScorpionSortTruck.query.count(),before)
        self.assertEqual(clients[0].get('/neoscorpion/truck-manager').status_code,200)

    def test_independent_sections_save_concurrently_without_overwriting_each_other(self):
        clients=self.admins(); main=self.form(clients[0])
        with self.fixture.app.app_context():
            apu=next(f for f in Forms(clients[1].get('/neoscorpion/settings').get_data(as_text=True)).forms if f.get('action')=='save_apu_rates')
        result=self.concurrent(lambda i,_g,_f,_d:clients[i].post('/neoscorpion/settings',data=(
            {**main,'fuel_density_lbs_per_gallon':'6.8'} if i==0 else {**apu,'apu_rate_b757':'0.42'})).status_code)
        self.assertEqual(result,[302,302])
        from app.models import NeoScorpionAircraftFuelSetting
        self.assertEqual(float(NeoScorpionAircraftFuelSetting.query.filter_by(aircraft_type='B757').one().apu_rate_thousand_lbs_per_hour),0.42)
        self.assertEqual(NeoScorpionSettings.query.one().fuel_density_lbs_per_gallon,6.8)

    def test_settings_guard_does_not_block_operational_gateway_foreign_keys(self):
        from sqlalchemy import text
        from app.services.neoscorpion_admin_forms import settings_baseline, guard_settings
        baseline=settings_baseline(self.gateway,'save_settings')
        held,inserted=Event(),Event()
        def action(i,gateway,_f,_d):
            if i==0:
                guard_settings(gateway,'save_settings',{'settings_baseline':baseline})
                held.set();self.assertTrue(inserted.wait(8));return 'guarded'
            self.assertTrue(held.wait(8))
            try:
                db.session.execute(text("SET LOCAL lock_timeout='2s'"))
                db.session.add(NeoScorpionFuelTruck(gateway_id=gateway.id,truck_number='FK-CHECK'))
                db.session.commit();return 'inserted'
            finally:inserted.set()
        self.assertEqual(self.concurrent(action),['guarded','inserted'])
