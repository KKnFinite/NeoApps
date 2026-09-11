"""Conductor discharge controls: canonical state, transition, and read-only refresh."""
import unittest
import os
from datetime import date, datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import select, text

from app.extensions import db
from app.models import NeoSektorSortState, NeoSektorOperationalSetting, SortDateMission, User
from app.services import neosektor_live_counts as live
from tests import test_neosektor_routes as fixtures
from tests.html_contracts import document


class BayRankingTest(unittest.TestCase):
    def test_status_first_saved_ties_disabled_and_back_capacity(self):
        order = SimpleNamespace(bay_priority_order='2,5,1,4,3')
        sides = {'east': {'label': 'EAST', 'bays': []}, 'west': {'label': 'WEST', 'bays': []}}
        for n in range(1, 6):
            sides['east' if n <= 3 else 'west']['bays'].append(
                {'bay_name': f'Bay {n}', 'status': 'Overflowing', 'back_pickup': False})
        bays = [bay for side in sides.values() for bay in side['bays']]
        routes = live._driver_routes_from_rows([])
        for backs in range(6):
            with self.subTest(backs=backs):
                for n, bay in enumerate(bays):
                    bay['back_pickup'] = n < backs
                cards = live._driver_bay_priority(sides, routes, order)
                normal = min(3, 5 - backs)
                self.assertEqual(len(cards), backs + normal)
                self.assertEqual([b['pickup'] for b in cards], ['back'] * backs + ['front'] * normal)
                self.assertEqual([b['arrow'] for b in cards], ['left'] * backs + ['right'] * normal)
                self.assertEqual([b['bay_name'] for b in cards[:backs]],
                                 [f'Bay {n}' for n in (2, 5, 1, 4, 3) if n <= backs])
                if backs == 2:
                    self.assertTrue(set(b['bay_name'] for b in cards[:backs]) & set(b['bay_name'] for b in cards[backs:]))
        for bay, status in zip(bays, ['Overflowing', 'Full', 'Moderate', 'Light', 'Full']):
            bay.update(status=status, back_pickup=False)
        self.assertEqual([b['bay_name'] for b in live._driver_bay_priority(sides, routes, order)], ['Bay 1', 'Bay 2', 'Bay 5'])
        disabled = live._driver_routes_from_rows([SimpleNamespace(route_name='BAY 1 PRIORITY ENABLED', route_value='false')])
        self.assertEqual([b['bay_name'] for b in live._driver_bay_priority(sides, disabled, order)], ['Bay 2', 'Bay 5', 'Bay 3'])
        for bay in bays:
            bay['status'] = 'Empty'
        self.assertEqual(live._driver_bay_priority(sides, routes, order), [])


class DischargeControlsTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.NeoSektorRoutesTest()
        self.fixture.setUp()
        self.fixture._login_approved_user(role='simulator')
        self.client = self.fixture.client
        self.gateway = self.fixture.gateway
        self.day = date.today()
        self.operation = self.fixture._add_sort_operation(self.day, 'night')
        self.operation.generated_by_user_id = User.query.first().id
        self.fixture._set_sort_window('night', time(0), time(23, 59, 59))
        self.fixture.app.config['CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE'] = datetime.combine(self.day, time(23))
        self.post('wave', {'wave': 'first', 'value': 10})

    def tearDown(self):
        self.fixture.tearDown()

    def post(self, endpoint, payload, status=200):
        response = self.client.post('/neosektor/tunnel-conductor/' + endpoint, json=payload)
        self.assertEqual(response.status_code, status, response.json)
        return response.json

    def state(self, url='/neosektor/driver-routing/state'):
        response, sql, commits, _ = self.fixture._capture_get_metrics(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(commits, 0)
        self.assertFalse(any(s.startswith(('insert', 'update', 'delete')) for s in sql), sql)
        return response.json

    def arrival(self, wave, operation=None, arrived=True):
        op = operation or self.operation
        mission = SortDateMission(sort_date_operation=op, sort_date=op.sort_date,
            gateway_code=op.gateway_code, sort_name=op.sort_name, mission_type='arrival',
            mission_source='manual', wave=wave, flight_number='TEST' + wave,
            origin='OAK', destination='RFD',
            actual_block_in_datetime_utc=datetime.utcnow() if arrived else None)
        db.session.add(mission)
        db.session.commit()
        return mission

    def stored(self):
        return {table.name: list(db.session.execute(select(table).order_by(table.c.id)).tuples())
                for table in db.metadata.sorted_tables if table.name.startswith('neosektor_')}

    def cut(self, enabled, expected):
        return self.post('discharge-controls', {'action': 'cut', 'enabled': enabled, 'expected_cut': expected})['state']

    def test_saved_order_cross_sort_and_disabled_preserves_position_and_back_state(self):
        initial = self.state()['state']['routing']['bay_priority_order']
        self.assertEqual(initial, ['Bay 5', 'Bay 4', 'Bay 3', 'Bay 2', 'Bay 1'])
        order = ['Bay 2', 'Bay 5', 'Bay 1', 'Bay 3', 'Bay 4']
        self.post('discharge-controls', {'action': 'priority', 'order': order, 'expected_order': initial})
        self.post('ballmat', {'side': 'east', 'bay_statuses': {'Bay 2': 'Overflowing'}, 'back_pickups': {'Bay 2': True}})
        disabled = self.post('settings', {'bay_priority_enabled': {'Bay 2': False}})['state']
        self.assertEqual(disabled['routing']['bay_priority'], [])
        self.assertTrue(disabled['sides']['east']['bays'][1]['back_pickup'])
        enabled = self.post('settings', {'bay_priority_enabled': {'Bay 2': True}})['state']
        self.assertEqual([b['pickup'] for b in enabled['routing']['bay_priority']], ['back', 'front'])
        self.assertEqual(enabled['routing']['bay_priority_order'], order)
        for day in (self.day, self.day + timedelta(days=1)):
            with self.fixture.app.test_request_context('/neosektor/driver-routing/state'):
                other = live.driver_routing_state_payload(self.gateway, day, 'night', initialize=False)
            self.assertEqual(other['routing']['bay_priority_order'], order)

    def test_back_pickup_validation_local_scope_clear_and_repeated_refresh(self):
        before = self.stored()
        self.post('ballmat', {'side': 'east', 'back_pickups': {'Bay 1': True}}, 400)
        self.post('ballmat', {'side': 'east', 'back_pickups': {'Bay 5': True}}, 400)
        self.assertEqual(self.stored(), before)
        for side, names in [('east', ['Bay 1', 'Bay 2']), ('west', ['Bay 5'])]:
            response = self.client.post('/neosektor/ballmat/update?operator=1&side=' + side,
                json={'side': side, 'bay_statuses': {n: 'Overflowing' for n in names}, 'back_pickups': {n: True for n in names}})
            self.assertEqual(response.status_code, 200, response.json)
        before = self.stored()
        for _ in range(2):
            for url in ['/neosektor/tunnel-conductor/state', '/neosektor/ballmat/state?side=east', '/neosektor/ballmat/state?side=west', '/neosektor/driver-routing/state']:
                state = self.state(url)['state']
                self.assertEqual([b['bay_name'] for b in state['routing']['bay_priority'][:3]], ['Bay 5', 'Bay 2', 'Bay 1'])
                self.assertEqual(len(state['routing']['bay_priority']), 5)
        self.assertEqual(self.stored(), before)
        self.post('settings', {'bay_priority_enabled': {'Bay 2': False}})
        self.post('ballmat', {'side': 'east', 'bay_statuses': {'Bay 2': 'Full'}})
        self.assertFalse(NeoSektorSortState.query.one().back_pickup_mask & 2)
        self.post('ballmat', {'side': 'east', 'bay_statuses': {'Bay 2': 'Overflowing'}})
        self.assertFalse(self.state()['state']['sides']['east']['bays'][1]['back_pickup'])

    def test_first_wave_gate_and_block_in_revision_current_sort_only(self):
        wrong = self.fixture._add_sort_operation(self.day - timedelta(days=1), 'night')
        self.arrival('1', wrong)
        self.arrival('2')
        mission = self.arrival('1st Wave', arrived=False)
        before = self.state()
        self.assertEqual(before['state']['routing']['routes']['first']['display_message'], '1ST WAVE NOT ARRIVED')
        mission.actual_block_in_datetime_utc = datetime.utcnow()
        db.session.commit()
        after = self.state('/neosektor/driver-routing/state?revision=' + before['revision'])
        self.assertTrue(after['changed'])
        self.assertEqual(after['state']['routing']['routes']['first']['display_state'], 'route')
        state = self.post('wave', {'wave': 'first', 'value': 0})['state']
        self.assertEqual(state['routing']['routes']['first']['display_message'], '1ST WAVE ALL IN')

    def test_manual_cut_restores_cards_without_erasing_back_state(self):
        self.post('ballmat', {'side': 'east', 'bay_statuses': {'Bay 1': 'Overflowing'}, 'back_pickups': {'Bay 1': True}})
        before = self.state()['state']
        cut = self.cut(True, False)
        self.assertEqual(cut['routing']['bay_priority'], [])
        self.assertEqual(cut['routing']['discharge_message'], 'Discharge cut. Report to doors.')
        self.assertTrue(cut['sides']['east']['bays'][0]['back_pickup'])
        self.assertFalse(cut['routing']['discharge_auto_fired'])
        self.assertEqual(self.cut(False, True)['routing']['bay_priority'], before['routing']['bay_priority'])

    def test_new_inputs_advance_existing_fast_signal_and_revision(self):
        self.post('ballmat', {'side': 'east', 'bay_statuses': {'Bay 1': 'Overflowing'}})
        paths = ['/neosektor/driver-routing/state', '/neosektor/tunnel-conductor/state',
                 '/neosektor/ballmat/state?side=east', '/neosektor/ballmat/state?side=west']
        for endpoint, command in [
            ('ballmat', {'side': 'east', 'back_pickups': {'Bay 1': True}}),
            ('discharge-controls', {'action': 'priority', 'order': ['Bay 1','Bay 2','Bay 3','Bay 4','Bay 5'],
                'expected_order': ['Bay 5','Bay 4','Bay 3','Bay 2','Bay 1']}),
            ('discharge-controls', {'action': 'cut', 'enabled': True, 'expected_cut': False}),
            ('discharge-controls', {'action': 'cut', 'enabled': False, 'expected_cut': True}),
            ('ballmat', {'side': 'east', 'bay_statuses': {'Bay 1': 'Light'}}),
        ]:
            previous = {url: self.state(url) for url in paths}
            self.post(endpoint, command)
            for url in paths:
                changed = self.state(url + ('&' if '?' in url else '?') + 'revision=' + previous[url]['revision'])
                self.assertTrue(changed['changed'])
                if url == paths[0]:
                    self.assertNotEqual(changed['state']['routing_watch']['version'], previous[url]['state']['routing_watch']['version'])

    def prepare_second(self, arrived=True, pending=1):
        self.arrival('1')
        self.arrival('2', arrived=arrived)
        self.post('wave', {'wave': 'first', 'value': 0})
        self.post('wave', {'wave': 'second', 'value': pending})
        row = live.NeoSektorWaveState.query.filter_by(wave_name='1ST WAVE').one()
        row.all_up_started_at = datetime.utcnow() - timedelta(minutes=16)
        db.session.commit()

    def test_auto_cut_once_manual_off_sticks_and_next_sort_resets(self):
        self.prepare_second()
        self.assertFalse(self.state()['state']['routing']['cut_discharge'])
        result = self.post('wave', {'wave': 'second', 'value': 0})['state']
        self.assertEqual(result['waves'][1]['left'], 'ALL UP')
        self.assertTrue(result['routing']['cut_discharge'])
        row = NeoSektorSortState.query.one()
        self.assertTrue(row.cut_discharge and row.discharge_auto_fired)
        self.assertFalse(self.cut(False, True)['routing']['cut_discharge'])
        for _ in range(3):
            self.assertFalse(self.state()['state']['routing']['cut_discharge'])
        self.assertFalse(self.post('wave', {'wave': 'second', 'value': 0})['state']['routing']['cut_discharge'])
        with self.fixture.app.test_request_context('/neosektor/driver-routing/state'):
            next_state = live.driver_routing_state_payload(self.gateway, self.day + timedelta(days=1), 'night', initialize=False)
        self.assertFalse(next_state['routing']['cut_discharge'])
        self.assertFalse(next_state['routing']['discharge_auto_fired'])

    def test_timer_only_cut_is_read_only_and_manual_off_acknowledges_once(self):
        self.prepare_second(pending=0)
        before = self.stored()
        for _ in range(3):
            self.assertTrue(self.state()['state']['routing']['cut_discharge'])
        self.assertEqual(self.stored(), before)
        self.assertFalse(self.cut(False, True)['routing']['cut_discharge'])
        self.assertTrue(NeoSektorSortState.query.one().discharge_auto_fired)
        self.assertFalse(self.state()['state']['routing']['cut_discharge'])

    def test_not_arrived_zero_counts_do_not_auto_cut(self):
        self.prepare_second(arrived=False, pending=0)
        state = self.state()['state']
        self.assertEqual(state['routing']['routes']['second']['display_state'], 'not_arrived')
        self.assertFalse(state['routing']['cut_discharge'])
        self.assertFalse(self.post('wave', {'wave': 'second', 'value': 0})['state']['routing']['discharge_auto_fired'])
        self.assertTrue(self.cut(True, False)['routing']['cut_discharge'])

    def test_google_primary_uses_shared_neo_discharge_state_without_get_writes(self):
        from tests.test_neosektor_integration_modes import FAKE_SHEETS_ENV, _FakeWorksheet, _complete_sheet_values
        from app.services.neosektor_sheets_compat import clear_neosektor_google_cache
        settings = NeoSektorOperationalSetting.query.one()
        settings.integration_mode = 'google_primary'
        db.session.commit()
        sheet = _FakeWorksheet(_complete_sheet_values(B6='Overflowing'))
        with patch.dict(os.environ, FAKE_SHEETS_ENV), patch('app.services.neosektor_sheets_compat._get_worksheet', return_value=sheet):
            try:
                self.post('ballmat', {'side': 'east', 'back_pickups': {'Bay 1': True}})
                before = self.state()
                self.assertTrue(before['state']['sides']['east']['bays'][0]['back_pickup'])
                self.cut(True, False)
                after = self.state('/neosektor/driver-routing/state?revision=' + before['revision'])
                self.assertTrue(after['changed'])
                self.assertTrue(after['state']['routing']['cut_discharge'])
                self.post('ballmat', {'side': 'east', 'bay_statuses': {'Bay 1': 'Full'}})
                self.assertFalse(NeoSektorSortState.query.one().back_pickup_mask & 1)
                self.assertIn(('B6', 'Full'), sheet.updates)
                self.cut(False, True)
                self.assertFalse(self.state()['state']['sides']['east']['bays'][0]['back_pickup'])
            finally:
                clear_neosektor_google_cache()

    def test_additive_bootstrap_defaults_and_idempotency(self):
        from app.services.schema_sync import LOCAL_SQLITE_OPTIONAL_COLUMNS, POSTGRES_OPTIONAL_COLUMNS, sync_database_schema
        tables = {'neosektor_sort_states': ['back_pickup_mask', 'cut_discharge', 'discharge_auto_fired'],
                  'neosektor_operational_settings': ['bay_priority_order']}
        for table, columns in tables.items():
            for column in columns:
                self.assertEqual(LOCAL_SQLITE_OPTIONAL_COLUMNS[table][column], POSTGRES_OPTIONAL_COLUMNS[table][column])
        # Disposable SQLite fixture only: simulate an existing pre-feature schema.
        db.session.remove()
        with db.engine.begin() as conn:
            for table, columns in tables.items():
                for column in columns:
                    conn.execute(text(f'ALTER TABLE {table} DROP COLUMN {column}'))
        for _ in range(2):
            sync_database_schema(self.fixture.app)
            db.session.commit()
        row = NeoSektorSortState.query.one()
        self.assertEqual(row.back_pickup_mask, 0)
        self.assertFalse(row.cut_discharge)
        self.assertFalse(row.discharge_auto_fired)
        self.assertEqual(NeoSektorOperationalSetting.query.one().bay_priority_order, '5,4,3,2,1')

    def test_stale_controls_rollback_and_authorization(self):
        order = self.state()['state']['routing']['bay_priority_order']
        self.cut(True, False)
        before = self.stored()
        conflict = self.post('discharge-controls', {'action': 'cut', 'enabled': False, 'expected_cut': False}, 409)
        self.assertTrue(conflict['state']['routing']['cut_discharge'])
        self.post('discharge-controls', {'action': 'priority', 'order': order, 'expected_order': []}, 409)
        self.post('discharge-controls', {'action': 'priority', 'order': [{}, 2, 3, 4, 5]}, 400)
        self.assertEqual(self.stored(), before)
        with patch('app.neonodes.neosektor.routes._neosektor_access', return_value={'can_edit': False}):
            self.post('discharge-controls', {'action': 'cut', 'enabled': False, 'expected_cut': True}, 403)
        self.assertEqual(self.stored(), before)
        self.assertFalse(self.cut(False, True)['routing']['cut_discharge'])

    def test_presentation_controls_local_bays_and_override_without_banner(self):
        self.arrival('1')
        for override in ('west', 'east', 'auto'):
            state = self.post('settings', {'first_override': override})['state']
            self.assertEqual(state['routing']['routes']['first']['override'], override)
        for path in ['/neosektor/driver-routing', '/neosektor/driver-routing?tv=1']:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertNotIn(b'MANUAL OVERRIDE', response.data)
            dom = document(response)
            self.assertEqual(len(dom.findall(**{'data-driver-priority-index': None})), 5)
            self.assertEqual(dom.one(**{'data-driver-discharge-cut': None}).text, 'Discharge cut. Report to doors.')
        tunnel = document(self.client.get('/neosektor/tunnel-conductor'))
        self.assertEqual(len(tunnel.findall(**{'data-priority-bay': None})), 5)
        self.assertEqual(len(tunnel.findall(**{'data-discharge-back': None})), 5)
        self.assertEqual(len(tunnel.findall(**{'data-discharge-cut': None})), 1)
        for page, count in [('ebm', 3), ('wbm', 2)]:
            response = self.client.get('/neosektor/' + page)
            dom = document(response)
            self.assertEqual(len(dom.findall(**{'data-bm-back': None})), count)
            self.assertEqual(len(dom.findall(**{'data-discharge-back': None})), count)
            self.assertFalse(dom.findall(**{'data-discharge-cut': None}))
            self.assertFalse(dom.findall(**{'data-priority-bay': None}))
        css = Path('app/static/css/neosektor_discharge_controls.css').read_text()
        self.assertIn('white-space:nowrap', css)
        self.assertIn('clamp(8px,2.25vw,11px)', Path('app/static/css/neosektor_tunnel_mobile.css').read_text())
        self.assertIn('min-height:40px', css)
        # The generic :is(output,[data-bm-input]) rule has attribute specificity;
        # this status-only rule must match it, not lose to 21px count styling.
        mobile_css = Path('app/static/css/neosektor_ballmat_mobile.css').read_text()
        self.assertIn('.bm-bays [data-bm-bay-value] { font-size:clamp(10px,2.6vw,11px)', mobile_css)
        driver_css = Path('app/static/css/neosektor_driver_routing.css').read_text()
        self.assertIn('grid-auto-flow:column; grid-auto-columns:minmax(0,1fr)', driver_css)


if __name__ == '__main__':
    unittest.main()
