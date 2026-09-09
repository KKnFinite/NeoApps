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
    update_ballmat_mode,
)
from app.services.neosektor_sheets_compat import NEO_PRIMARY_GOOGLE_MIRROR
from tests import test_neosektor_integration_modes as existing
from tests.test_neosektor_integration_modes import _complete_sheet_values, _FakeWorksheet, FAKE_SHEETS_ENV


class BallmatSpotterTest(unittest.TestCase):
    def test_absolute_spotter_entries_preserve_allocations_and_canonical_derived_state(self):
        for side in ('east', 'west'):
            for metric in ('first', 'second', 'open'):
                with self.subTest(side=side, metric=metric, mode=1):
                    state = self.post(side, expected_mode=1, metric=metric, position='total', value=10)
                    self.assertEqual(state['spotters']['counts'][metric], {'left':10, 'right':0, 'total':10})
            self.post(side, expected_mode=1, mode=2)
            for metric in ('first', 'second', 'open'):
                with self.subTest(side=side, metric=metric, mode=2):
                    self.post(side, expected_mode=2, metric=metric, position='right', value=4)
                    state = self.post(side, expected_mode=2, metric=metric, position='left', value=12)
                    self.assertEqual(state['spotters']['counts'][metric], {'left':12, 'right':4, 'total':16})
                    canonical = self.client.get('/neosektor/ballmat/state?side='+side).json['state']
                    for key in ('spotters', 'waves', 'routing', 'ballmat_routing'):
                        self.assertEqual(state[key], canonical[key])
                    limited = self.post(side, expected_mode=2, metric=metric, position='right', value=99)
                    self.assertEqual(limited['spotters']['counts'][metric], {'left':12, 'right':87, 'total':99})

    def test_absolute_spotter_entries_reject_stale_modes_readonly_total_and_invalid_values(self):
        self.post(expected_mode=1, mode=2)
        before = self.client.get('/neosektor/ballmat/state?side=east').json['state']['spotters']
        for metric in ('first', 'second', 'open'):
            for mode, version, position in ((1,0,'total'), (2,0,'right'), (2,1,'total')):
                with self.subTest(metric=metric, mode=mode, version=version, position=position):
                    response = self.client.post('/neosektor/ballmat/update?side=east', json={
                        'side':'east', 'spotter':{'expected_mode':mode, 'expected_mode_version':version,
                            'metric':metric, 'position':position, 'value':30}})
                    self.assertEqual(response.status_code, 409)
                    self.assertEqual(response.json['state']['spotters'], before)
        for value in (-1, 100, 1.5, True, '5'):
            response = self.client.post('/neosektor/ballmat/update?side=east', json={
                'side':'east', 'spotter':{'expected_mode':2, 'expected_mode_version':1,
                    'metric':'first', 'position':'right', 'value':value}})
            self.assertEqual(response.status_code, 403)  # Existing invalid-count response contract.
        self.assertEqual(self.client.get('/neosektor/ballmat/state?side=east').json['state']['spotters'], before)

    def test_mobile_numeric_markup_keeps_published_total_noninteractive(self):
        from tests.html_contracts import document
        for path in ('ebm', 'wbm'):
            page = self.client.get('/neosektor/'+path)
            self.assertEqual(page.status_code, 200)
            dom = document(page)
            inputs = dom.findall('input', **{'data-bm-input':None})
            self.assertEqual(len(inputs), 9)
            for field in inputs:
                self.assertEqual(field.attrs.get('type'), 'number')
                self.assertEqual((field.attrs.get('min'), field.attrs.get('max')), ('0', '99'))
            totals = dom.findall(cls='bm-published')
            self.assertEqual(len(totals), 3)
            self.assertTrue(all(total.tag == 'output' for total in totals))

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
        row = NeoSektorBallmatCount.query.filter_by(side=side.upper()).one()
        command.setdefault('expected_mode_version', row.mode_version)
        if 'mode' in command:
            response = self.client.post('/neosektor/tunnel-conductor/spotter-mode', json={
                'side':side, 'action':'set', 'confirm_clear_right':True, **command})
            self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
            return self.client.get('/neosektor/ballmat/state?side='+side).json['state']
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

    def mode_action(self, side='east', **command):
        detail = self.client.get('/neosektor/ballmat/state?side='+side).json['state']['spotters']
        return self.client.post('/neosektor/tunnel-conductor/spotter-mode', json={
            'side':side, 'expected_mode':detail['mode'], 'expected_mode_version':detail['mode_version'],
            'request_version':detail['request_version'], **command})

    def request_mode(self, side='east', mode=2):
        detail = self.client.get('/neosektor/ballmat/state?side='+side).json['state']['spotters']
        return self.client.post('/neosektor/ballmat/mode-request?side='+side, json={
            'side':side, 'mode':mode, 'expected_mode':detail['mode'], 'expected_mode_version':detail['mode_version']})

    def test_request_dedupe_persistence_independence_approval_and_denial(self):
        requested = self.request_mode()
        self.assertEqual(requested.status_code, 200)
        row = NeoSektorBallmatCount.query.filter_by(side='EAST').one()
        version, stamp = row.mode_request_version, row.updated_at
        self.assertEqual(self.request_mode().status_code, 200)
        self.assertEqual((row.mode_request_version, row.updated_at), (version, stamp))
        self.assertEqual((row.spotter_mode, row.mode_version, row.pending_mode), (1, 0, 2))
        self.assertEqual(self.request_mode('west').status_code, 200)
        page = self.client.get('/neosektor/tunnel-conductor')
        self.assertIn(b'data-conductor-mode="east"', page.data)
        self.assertIn(b'data-conductor-mode="west"', page.data)
        self.assertIn(b'REQUEST 2 PENDING', page.data)
        operator = self.client.get('/neosektor/ebm')
        self.assertIn(b'REQUEST 2 SPOTTERS', operator.data)
        self.assertIn(b'data-mode-request-url=', operator.data)
        tunnel = self.client.get('/neosektor/tunnel-conductor/state').json['state']['ballmat_modes']
        self.assertEqual([tunnel[s]['pending_mode'] for s in ('east','west')], [2,2])
        self.assertEqual(self.mode_action(action='approve').status_code, 200)
        detail = self.client.get('/neosektor/ballmat/state?side=east').json['state']['spotters']
        self.assertEqual((detail['mode'], detail['mode_version'], detail['pending_mode']), (2,1,None))
        before = self.client.get('/neosektor/ballmat/state?side=west').json['state']['spotters']
        self.assertEqual(self.mode_action('west', action='deny').status_code, 200)
        after = self.client.get('/neosektor/ballmat/state?side=west').json['state']['spotters']
        self.assertEqual((after['mode'], after['mode_version'], after['counts']),
                         (before['mode'], before['mode_version'], before['counts']))
        self.assertIsNone(after['pending_mode'])
        self.request_mode('west')
        stale = self.mode_action('west', action='approve', request_version=before['request_version'])
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json['state']['ballmat_modes']['west']['pending_mode'], 2)

    def test_spotter_cannot_set_mode_or_approve_but_can_request(self):
        self.fixture._login('operator')
        self.assertEqual(self.request_mode().status_code, 200)
        response = self.client.post('/neosektor/ballmat/update?side=east', json={
            'side':'east', 'spotter':{'mode':2, 'expected_mode':1, 'expected_mode_version':0}})
        self.assertEqual(response.status_code, 403)
        for action in ('set', 'approve', 'deny'):
            self.assertEqual(self.mode_action(action=action, mode=2).status_code, 403)
        self.assertEqual(NeoSektorBallmatCount.query.filter_by(side='EAST').one().spotter_mode, 1)

    def test_nonzero_right_requires_explicit_confirmation_for_every_counter(self):
        for metric in ('first','second','open'):
            with self.subTest(metric=metric):
                self.post(expected_mode=1, mode=2)
                before = self.post(expected_mode=2, metric=metric, position='right', delta=1)['spotters']
                self.request_mode(mode=1)
                rejected = self.mode_action(action='approve')
                self.assertEqual(rejected.status_code, 409)
                self.assertTrue(rejected.json['confirmation_required'])
                self.assertEqual(rejected.json['state']['ballmat_modes']['east']['mode'], 2)
                direct_rejected = self.mode_action(action='set', mode=1)
                self.assertEqual(direct_rejected.status_code, 409)
                self.assertTrue(direct_rejected.json['confirmation_required'])
                self.assertEqual(self.mode_action(action='approve', confirm_clear_right=True).status_code, 200)
                after = self.client.get('/neosektor/ballmat/state?side=east').json['state']['spotters']
                self.assertEqual(after['mode_version'], before['mode_version']+1)
                for key in ('first','second','open'):
                    self.assertEqual(after['counts'][key], {'left':before['counts'][key]['total'],
                        'total':before['counts'][key]['total'], 'right':0})

    def test_generation_rejects_stale_counts_even_after_roundtrip_to_same_mode(self):
        self.post(expected_mode=1, mode=2)
        self.post(expected_mode=2, mode=1)
        for metric in ('first','second','open'):
            for version in (None, 0, 1):
                with self.subTest(metric=metric, version=version):
                    response = self.client.post('/neosektor/ballmat/update?side=east', json={
                        'side':'east', 'spotter':{'expected_mode':1, 'expected_mode_version':version,
                            'metric':metric, 'position':'total', 'delta':1}})
                    self.assertEqual(response.status_code, 409)
                    self.assertEqual(response.json['state']['spotters']['mode_version'], 2)
        self.assertEqual(self.post(expected_mode=1,metric='first',position='total',delta=1)['spotters']['counts']['first']['total'],8)

    def test_operator_aggregate_editor_requires_current_generation_and_preserves_right(self):
        self.post(expected_mode=1,mode=2)
        self.post(expected_mode=2,metric='first',position='right',delta=1)
        for guard in (None, {'expected_mode':1,'expected_mode_version':0}):
            response = self.client.post('/neosektor/ballmat/update?side=east&operator=1', json={
                'side':'east', 'waves':{'first':{'count':50}}, 'mode_guard':guard})
            self.assertEqual(response.status_code,409)
            self.assertEqual(response.json['state']['spotters']['counts']['first']['total'],8)
        response = self.client.post('/neosektor/ballmat/update?side=east&operator=1', json={
            'side':'east', 'waves':{'first':{'count':12}},
            'mode_guard':{'expected_mode':2,'expected_mode_version':1}})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json['state']['spotters']['counts']['first'],{'left':11,'right':1,'total':12})

    def test_two_devices_refresh_and_reload_preserve_allocations_with_zero_get_writes(self):
        from sqlalchemy import event
        from flask import template_rendered
        from app.services.neosektor_live_refresh import neosektor_state_revision, ROUTING_STATE_SCOPE
        self.client.post('/neosektor/ballmat/update?side=east', json={
            'side':'east', 'waves':{'first':{'count':10},'second':{'count':10}}, 'open_bays':10})
        old_revision = neosektor_state_revision(self.gateway, ROUTING_STATE_SCOPE)
        self.post(expected_mode=1,mode=2)
        for metric in ('first','second','open'):
            for _ in range(2):
                state = self.post(expected_mode=2,metric=metric,position='right',delta=1)
        expected = {'left':10,'right':2,'total':12}
        self.assertTrue(all(value == expected for value in state['spotters']['counts'].values()))
        writes, rendered = [], []
        def sql(_c,_cu,statement,*_args):
            if statement.lstrip().split()[0].upper() in ('INSERT','UPDATE','DELETE'):
                writes.append(statement)
        def capture(_sender, template, context, **_extra):
            if template.name.endswith('/ballmat.html'): rendered.append(context['state']['spotters'])
        engine = db.engine
        event.listen(engine, 'before_cursor_execute', sql)
        template_rendered.connect(capture, self.fixture.app)
        try:
            with patch('app.services.neosektor_live_counts._neosektor_screen_refresh_status', return_value={
                **state['refresh'], 'auto_refresh_enabled':True}):
                for _device in range(2):
                    for _ in range(3):
                        response = self.client.get('/neosektor/ballmat/state?side=east&revision='+old_revision)
                        self.assertTrue(response.json['changed'])
                        detail = response.json['state']['spotters']
                        self.assertEqual((detail['mode'],detail['mode_version']), (2,1))
                        self.assertTrue(all(value == expected for value in detail['counts'].values()))
                self.assertEqual(self.client.get('/neosektor/ebm').status_code, 200)
                self.assertEqual(self.client.get('/neosektor/wbm').status_code, 200)
                self.assertEqual(self.client.get('/neosektor/tunnel-conductor').status_code, 200)
                self.assertEqual(self.client.get('/neosektor/tunnel-conductor/state').status_code, 200)
            self.assertEqual(writes, [])
            self.assertEqual(rendered[0]['counts']['first'], expected)
            self.assertEqual(rendered[1]['mode'], 1)
        finally:
            event.remove(engine, 'before_cursor_execute', sql)
            template_rendered.disconnect(capture, self.fixture.app)

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
        for column in ('spotter_mode','right_first','right_second','right_open','mode_version','pending_mode','mode_request_version'):
            db.session.execute(text('ALTER TABLE neosektor_ballmat_counts DROP COLUMN '+column))
        db.session.commit()
        sync_database_schema(self.fixture.app)
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
                update_ballmat_mode(NeoSektorOperationalStateBundle.load(gateway, for_update=True), 'east',
                                   {'expected_mode':1, 'expected_mode_version':0, 'action':'set', 'mode':2}, conductor=True)
                db.session.commit()
            barrier = Barrier(2)
            def device(position):
                with app.app_context():
                    gateway = db.session.get(Gateway,gateway_id)
                    barrier.wait(timeout=10)
                    update_ballmat_side(gateway,'east',{'side':'east','spotter':{
                        'expected_mode':2,'expected_mode_version':1,'metric':'first','position':position,'delta':1}})
                    db.session.commit()
                    db.session.remove()
            with ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(device,('left','right')))
            with app.app_context():
                detail = ballmat_operator_state_payload(db.session.get(Gateway,gateway_id))['spotters']
                self.assertEqual(detail['counts']['first'],{'left':8,'right':1,'total':9})
                db.session.remove()
                db.engine.dispose()
