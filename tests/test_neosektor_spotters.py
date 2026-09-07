"""Current-sort spotter detail, canonical totals, and serialized mutations."""
import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

from app import create_app
from app.extensions import db
from app.models import Gateway, NeoSektorBallmatCount, NeoSektorOperationalSetting
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.neosektor_live_counts import (
    NeoSektorOperationalStateBundle, ballmat_operator_state_payload,
    ballmat_state_payload, update_ballmat_side, apply_standalone_compat_values,
)
from app.services.neosektor_sheets_compat import NEO_PRIMARY_GOOGLE_MIRROR
from tests import test_neosektor_integration_modes as existing
from tests.test_neosektor_integration_modes import _complete_sheet_values, _FakeWorksheet, FAKE_SHEETS_ENV


class BallmatSpotterTest(unittest.TestCase):
    def setUp(self):
        self.fixture = existing.NeoSektorIntegrationModesTest()
        self.fixture.setUp()
        self.fixture._set_mode(NEO_PRIMARY_GOOGLE_MIRROR)
        self.gateway = self.fixture.gateway
        apply_standalone_compat_values(self.gateway, _complete_sheet_values())
        db.session.commit()
        self.fixture._login('simulator')
        self.client = self.fixture.client

    def tearDown(self):
        self.fixture.tearDown()

    def post(self, side='east', **command):
        response = self.client.post('/neosektor/ballmat/update?side='+side,
                                    json={'side':side, 'spotter':command})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.json['state']

    def test_modes_independent_transition_preserves_all_totals(self):
        before = ballmat_state_payload(self.gateway)['sides']
        state = self.post(expected_mode=1, mode=2)
        self.assertEqual(state['sides'], before)
        self.assertEqual(state['spotters']['counts']['first'], {'left':7,'right':0,'total':7})
        self.assertEqual(ballmat_operator_state_payload(self.gateway, selected_side='west')['spotters']['mode'],1)
        self.post('west', expected_mode=1, mode=2)
        state = self.post(expected_mode=2, metric='first', position='right', delta=1)
        self.assertEqual(state['spotters']['counts']['first'], {'left':7,'right':1,'total':8})
        state = self.post(expected_mode=2, mode=1)
        self.assertEqual(state['spotters']['counts']['first'], {'left':8,'right':0,'total':8})
        self.assertEqual(ballmat_operator_state_payload(self.gateway, selected_side='west')['spotters']['mode'],2)
        self.post(expected_mode=1, mode=2)
        self.assertEqual(NeoSektorBallmatCount.query.filter_by(side='EAST').one().right_first,0)

    def test_three_counters_sum_and_shared_consumers_have_no_split(self):
        self.post(expected_mode=1, mode=2)
        for metric in ('first','second','open'):
            for position in ('left','right'):
                state = self.post(expected_mode=2, metric=metric, position=position, delta=1)
                value = state['spotters']['counts'][metric]
                self.assertEqual(value['left']+value['right'],value['total'])
        bundle = NeoSektorOperationalStateBundle.load(self.gateway)
        for state in (bundle.ballmat_state_payload(), bundle.driver_routing_state_payload()):
            self.assertNotIn('spotters',state)
            self.assertNotIn('right_first',json.dumps(state))
        self.assertEqual(bundle.operational_cell_values()['B2'],9)
        self.assertEqual(bundle.operational_cell_values()['B3'],7)
        self.assertEqual(bundle.operational_cell_values()['B4'],4)

    def test_mirror_receives_total_after_commit_no_google_reads(self):
        worksheet = _FakeWorksheet()
        with patch.dict(os.environ, FAKE_SHEETS_ENV, clear=False), patch(
                'app.services.neosektor_sheets_compat._get_worksheet',return_value=worksheet):
            self.fixture._enable_mirror_writes(worksheet)
            worksheet.updates.clear()
            self.post(expected_mode=1, mode=2)
            self.assertEqual(worksheet.updates,[])
            original = worksheet.update_acell
            def verify_commit(cell,value):
                self.assertFalse(db.session.new)
                self.assertFalse(db.session.dirty)
                original(cell,value)
            worksheet.update_acell = verify_commit
            self.post(expected_mode=2, metric='first',position='right',delta=1)
            self.assertIn(('B2',8),worksheet.updates)
            self.assertEqual(worksheet.batch_reads,[])

    def test_stale_mode_rejected_without_count_change(self):
        self.post(expected_mode=1,mode=2)
        response = self.client.post('/neosektor/ballmat/update?side=east',json={
            'side':'east','spotter':{'expected_mode':1,'metric':'first','position':'total','delta':1}})
        self.assertEqual(response.status_code,409)
        self.assertEqual(ballmat_state_payload(self.gateway)['sides']['east']['waves'][0]['count'],7)

    def test_limits_and_sort_isolation(self):
        from datetime import date, timedelta
        self.post(expected_mode=1,mode=2)
        state = self.post(expected_mode=2,metric='first',position='right',delta=-1)
        self.assertEqual(state['spotters']['counts']['first']['right'],0)
        fresh = ballmat_operator_state_payload(self.gateway,sort_date=date.today()+timedelta(days=1))
        self.assertEqual(fresh['spotters']['mode'],1)
        self.assertEqual(fresh['spotters']['counts']['first']['total'],0)

    def test_aggregate_edit_reconciles_split_and_retains_existing_limit(self):
        self.post(expected_mode=1,mode=2)
        self.post(expected_mode=2,metric='first',position='right',delta=1)
        for count in (0,99):
            response = self.client.post('/neosektor/ballmat/update?side=east',json={
                'side':'east','waves':{'first':{'count':count}}})
            self.assertEqual(response.status_code,200)
        state = self.post(expected_mode=2,metric='first',position='right',delta=1)
        self.assertEqual(state['spotters']['counts']['first'],{'left':99,'right':0,'total':99})

    def test_bootstrap_additive_columns_and_readonly_revision(self):
        from sqlalchemy import text, inspect
        from app.services.schema_sync import sync_database_schema, POSTGRES_OPTIONAL_COLUMNS
        from app.services.neosektor_live_refresh import neosektor_state_revision, ROUTING_STATE_SCOPE
        for column in ('spotter_mode','right_first','right_second','right_open'):
            db.session.execute(text('ALTER TABLE neosektor_ballmat_counts DROP COLUMN '+column))
        db.session.commit()
        sync_database_schema(self.fixture.app)
        db.session.commit()
        columns = {c['name'] for c in inspect(db.engine).get_columns('neosektor_ballmat_counts')}
        self.assertTrue(set(POSTGRES_OPTIONAL_COLUMNS['neosektor_ballmat_counts']).issubset(columns))
        self.assertEqual(db.session.execute(text('SELECT spotter_mode, right_first FROM neosektor_ballmat_counts WHERE side=\'EAST\'')).one(),(1,0))
        before = neosektor_state_revision(self.gateway,ROUTING_STATE_SCOPE)
        self.post(expected_mode=1,mode=2)
        self.assertNotEqual(before,neosektor_state_revision(self.gateway,ROUTING_STATE_SCOPE))
        response = self.client.get('/neosektor/ballmat/state?side=east')
        self.assertEqual(response.json['state']['spotters']['mode'],2)
        self.assertEqual(response.json['state']['spotters']['counts']['first']['total'],7)

    def test_readonly_and_local_bay_scope(self):
        self.client.post('/neosektor/ballmat/update?side=east',json={
            'side':'east','bay_statuses':{'Bay 4':'Empty'},'planned_count':99})
        state = ballmat_state_payload(self.gateway)
        self.assertEqual(state['sides']['west']['bays'][0]['status'],'Overflowing')
        self.fixture._login('watcher')
        response = self.client.post('/neosektor/ballmat/update?side=east',json={
            'side':'east','spotter':{'expected_mode':1,'mode':2}})
        self.assertEqual(response.status_code,403)

    def test_concurrent_opposite_devices_preserve_both_deltas(self):
        # Independent sessions/connections against a file DB, no process lock.
        with tempfile.TemporaryDirectory() as folder:
            app = create_app(type('Config',(),{
                'TESTING':True,'SECRET_KEY':'isolated','SQLALCHEMY_DATABASE_URI':
                'sqlite:///'+str(Path(folder,'concurrency.db')).replace('\\','/'),
                'SQLALCHEMY_TRACK_MODIFICATIONS':False}))
            with app.app_context():
                db.create_all()
                gateway = ensure_default_gateway_and_nodes()
                gateway_id = gateway.id
                db.session.add(NeoSektorOperationalSetting(gateway_id=gateway.id,gateway_code=gateway.code,integration_mode='neo_only'))
                apply_standalone_compat_values(gateway,_complete_sheet_values())
                update_ballmat_side(gateway,'east',{'side':'east','spotter':{'expected_mode':1,'mode':2}})
                db.session.commit()
            barrier = Barrier(2)
            def device(position):
                with app.app_context():
                    gateway = db.session.get(Gateway,gateway_id)
                    barrier.wait(timeout=10)
                    update_ballmat_side(gateway,'east',{'side':'east','spotter':{
                        'expected_mode':2,'metric':'first','position':position,'delta':1}})
                    db.session.commit()
                    db.session.remove()
            with ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(device,('left','right')))
            with app.app_context():
                detail = ballmat_operator_state_payload(db.session.get(Gateway,gateway_id))['spotters']
                self.assertEqual(detail['counts']['first'],{'left':8,'right':1,'total':9})
                db.session.remove()
                db.engine.dispose()
