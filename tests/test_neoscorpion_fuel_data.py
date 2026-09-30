import json
import unittest
from datetime import datetime
from html import unescape
import re
from unittest.mock import patch
from flask import g
from flask.testing import FlaskClient

from app.extensions import db
from app.models import NeoScorpionFuelTruck, NeoScorpionFuelWorkState
from app.services.neoscorpion import fueler_context
from tests import test_neoscorpion_fueler_off as fixtures


class ActorClient(FlaskClient):
    def open(self, *args, **kwargs):
        # The fixture keeps an app context alive. Real requests each have fresh g.
        for key in list(g):
            g.pop(key, None)
        return super().open(*args, **kwargs)


class FuelerDataTest(unittest.TestCase):
    setUp = fixtures.NeoScorpionFuelerOffTest.setUp
    tearDown = fixtures.NeoScorpionFuelerOffTest.tearDown
    _assignment = fixtures.NeoScorpionFuelerOffTest._assignment
    _add_user = fixtures.NeoScorpionFuelerOffTest._add_user
    _save_complete = fixtures.NeoScorpionFuelerOffTest._save_complete
    _form = staticmethod(fixtures.NeoScorpionFuelerOffTest._form)

    def client_for(self, user):
        self.app.test_client_class = ActorClient
        client = self.app.test_client()
        response = client.post('/login', data={'email': user.email, 'password': 'TestPassword123!'})
        self.assertEqual(response.status_code, 302)
        return client

    def setup_assignment(self):
        self.operation, self.mission, self.assignment = self._assignment()
        self.dispatcher = self._add_user('dispatcher', 'master')
        db.session.commit()
        self.dispatch = self.client_for(self.dispatcher)
        self.fueler = self.client_for(self.user)
        self.dispatch_url = f'/neoscorpion/fuel-dispatch/fueler-data/{self.assignment.id}'
        self.fueler_url = f'/neoscorpion/fueler/assignments/{self.assignment.id}'

    def baseline(self, client=None, url=None):
        response = (client or self.dispatch).get(url or self.dispatch_url)
        self.assertEqual(response.status_code, 200, response.data)
        html = response.json['html']
        return json.loads(unescape(re.search(r"data-edit-baseline='([^']+)'", html).group(1)))

    def save(self, client, url, expected, **fields):
        return client.post(url, data={'expected': json.dumps(expected), **fields})

    def test_assigned_active_only_and_shared_card(self):
        self.setup_assignment()
        page = self.dispatch.get('/neoscorpion/fuel-dispatch').get_data(as_text=True)
        self.assertIn(f'data-fuel-data-open="{self.dispatch_url}"', page)
        self.assertIn('<th>Fueler</th><th>FUELER DATA</th><th>Truck</th>', page)
        panel = self.dispatch.get(self.dispatch_url).json['html']
        self.assertNotIn('FUELER DATA · UPS801 · N412UP', panel)
        self.assertNotIn('<html', panel)
        self.assertRegex(panel, rf'<form\s+action="{self.dispatch_url}"\s+method="post"')
        self.assertIn('data-fuel-planning-form', panel)
        self.assertIn('data-dispatcher-panel="true"', panel)
        dispatch_page = self.dispatch.get('/neoscorpion/fuel-dispatch').get_data(as_text=True)
        self.assertIn('neoscorpion-fuel-data-dialog neoscorpion-fueler-screen', dispatch_page)
        self.assertIn('Fueler Data mobile preview', dispatch_page)
        self.assertIn('Fuel Load', panel)
        self.assertNotIn('Fueling Target', panel)
        self.assertIn('data-fuel-load-output', panel)
        self.assertIn('neoscorpion-fuel-tank-total', panel)
        self.assertIn('>TOTAL</strong>', panel)
        fueler_page = self.fueler.get('/neoscorpion/fueler').get_data(as_text=True)
        self.assertIn('data-fuel-data-card', fueler_page)
        self.assertIn('TRUCK UNASSIGNED', fueler_page)
        self.assignment.assigned_fueler_user_id = None
        db.session.commit()
        self.assertEqual(self.dispatch.get(self.dispatch_url).status_code, 404)
        self.assertNotIn('data-fuel-data-open=', self.dispatch.get('/neoscorpion/fuel-dispatch').get_data(as_text=True))
        self.assignment.assigned_fueler_user_id = self.user.id
        self.assignment.review_status = 'complete'
        db.session.commit()
        self.assertEqual(self.dispatch.get(self.dispatch_url).status_code, 404)
        self.assertNotIn('data-fuel-data-open=', self.dispatch.get('/neoscorpion/fuel-dispatch').get_data(as_text=True))

    def test_fueler_truck_header_includes_vendor_driver(self):
        self.setup_assignment()
        truck = NeoScorpionFuelTruck(
            gateway_id=self.gateway.id,
            truck_number="708",
            vendor_driver_name="Vendor Driver",
        )
        db.session.add(truck)
        db.session.flush()
        self.assignment.assigned_truck_id = truck.id
        db.session.commit()

        fueler_page = self.fueler.get('/neoscorpion/fueler').get_data(as_text=True)

        self.assertIn('TRUCK 708', fueler_page)
        self.assertIn('neoscorpion-fueler-truck-vendor', fueler_page)
        self.assertIn('Vendor Driver', fueler_page)

    def test_shared_tank_grid_headers_and_totals_in_editable_and_readonly_cards(self):
        self.setup_assignment()
        self._save_complete(self.assignment)
        db.session.commit()
        for readonly in (False, True):
            if readonly:
                NeoScorpionFuelWorkState.query.one().off_at_utc = datetime.utcnow()
                db.session.commit()
            row = fueler_context(self.gateway, self.user)['rows'][0]
            pages = (
                self.fueler.get('/neoscorpion/fueler').get_data(as_text=True),
                self.dispatch.get(self.dispatch_url).json['html'],
            )
            for page in pages:
                with self.subTest(readonly=readonly, dispatcher='data-dispatcher-panel="true"' in page):
                    headers = re.findall(r'<span role="columnheader">([^<]+)</span>', page)
                    self.assertEqual(headers, ['Tank', 'Remaining', 'Planned', 'Actual'])
                    total = re.search(r'class="neoscorpion-fuel-tank-row neoscorpion-fuel-tank-total" role="row">(.*?)</div>', page, re.S)
                    self.assertIsNotNone(total)
                    cells = re.findall(r'<strong role="(?:rowheader|cell)">([^<]+)</strong>', total.group(1))
                    self.assertEqual(cells, ['TOTAL', row['remaining_total_display'], row['planned_total_display'], row['actual_total_display']])
                    self.assertEqual(page.count('neoscorpion-fuel-tank-total'), 1)
                    self.assertLess(total.end(), page.index('<dt>T/F</dt>' if readonly else '<label>T/F'))
                    self.assertEqual('name="remaining_left"' in page, not readonly)
                    if '<html' in page:
                        self.assertIn('scorpion=20260930-fueler-grid-alignment-v1', page)

    def test_both_actors_share_canonical_data_without_owner_change(self):
        self.setup_assignment()
        response = self.save(self.dispatch, self.dispatch_url, self.baseline(), notes='Dispatcher note', remaining_left='10')
        self.assertEqual(response.status_code, 200, response.data)
        row = fueler_context(self.gateway, self.user)['rows'][0]
        self.assertEqual(row['tail_fuel_state'].notes, 'Dispatcher note')
        baseline = self.baseline(self.fueler, self.fueler_url)
        self.assertEqual(baseline['remaining_left'], 10000)
        response = self.save(self.fueler, self.fueler_url, baseline, actual_left='12', transfer_fuel_gallons='100')
        self.assertEqual(response.status_code, 200, response.data)
        state = self.baseline()
        self.assertEqual(state['actual_left'], 12000)
        self.assertEqual(state['notes'], 'Dispatcher note')
        self.assertEqual(state['assigned_fueler_user_id'], self.user.id)

    def test_same_field_conflict_atomic_but_different_field_safe_and_idempotent(self):
        self.setup_assignment()
        stale = self.baseline()
        first = self.save(self.fueler, self.fueler_url, stale, notes='Fueler note')
        self.assertEqual(first.status_code, 200)
        conflict = self.save(self.dispatch, self.dispatch_url, stale, notes='Stale note', remaining_left='10')
        self.assertEqual(conflict.status_code, 409)
        self.assertIn('DATA CHANGED', conflict.json['error'])
        self.assertIn('Fueler note', conflict.json['html'])
        self.assertIsNone(self.baseline()['remaining_left'])
        different = self.save(self.dispatch, self.dispatch_url, stale, remaining_left='10')
        self.assertEqual(different.status_code, 200)
        already = self.save(self.dispatch, self.dispatch_url, stale, notes='Fueler note')
        self.assertEqual(already.status_code, 200)
        self.assertEqual(self.baseline()['notes'], 'Fueler note')

    def test_baselines_required_and_cycle_identity_rejected(self):
        self.setup_assignment()
        self.assertEqual(self.dispatch.post(self.dispatch_url, data={'notes': 'unsafe'}).status_code, 400)
        stale = self.baseline()
        self.assignment.current_cycle_number = 2
        db.session.commit()
        self.assertEqual(self.save(self.dispatch, self.dispatch_url, stale, notes='old cycle').status_code, 409)
        self.assertEqual(self.baseline()['notes'], '')

    def test_dispatcher_off_actor_and_spear_failure_isolation(self):
        self.setup_assignment()
        self._save_complete(self.assignment)
        db.session.commit()
        expected = self.baseline()
        with patch('app.neonodes.neoscorpion.routes.effective_spear_settings') as settings, patch(
            'app.neonodes.neoscorpion.routes.complete_fueled_assignment', side_effect=RuntimeError('test failure')
        ):
            settings.return_value.automation_enabled = True
            response = self.save(self.dispatch, self.dispatch_url + '/off', expected)
        self.assertEqual(response.status_code, 200, response.data)
        work = NeoScorpionFuelWorkState.query.filter_by(fuel_assignment_id=self.assignment.id).one()
        self.assertIsNotNone(work.off_at_utc)
        self.assertEqual(work.off_by_user_id, self.dispatcher.id)
        self.assertEqual(self.assignment.assigned_fueler_user_id, self.user.id)
        self.assertNotEqual(self.assignment.review_status, 'complete')
        self.assertNotIn('data-fuel-planning-form', response.json['html'])
        self.assertNotIn('data-apu-editor-form', response.json['html'])

    def test_off_stale_data_cannot_finalize_and_uses_normal_validation(self):
        self.setup_assignment()
        self.assertEqual(self.save(self.dispatch, self.dispatch_url + '/off', self.baseline()).status_code, 400)
        self._save_complete(self.assignment)
        db.session.commit()
        expected = self.baseline()
        self.assertEqual(self.save(self.fueler, self.fueler_url, expected, actual_left='11').status_code, 200)
        self.assertEqual(self.save(self.dispatch, self.dispatch_url + '/off', expected).status_code, 409)
        self.assertIsNone(NeoScorpionFuelWorkState.query.one().off_at_utc)

    def test_permissions_and_fueler_ownership(self):
        self.setup_assignment()
        other = self._add_user('other_fueler', 'operator')
        reader = self._add_user('read_only', 'operator')
        db.session.commit()
        wrong = self.client_for(other)
        self.assertEqual(wrong.get(self.fueler_url).status_code, 404)
        self.assertEqual(self.save(wrong, self.fueler_url, self.baseline(), notes='forbidden').status_code, 400)
        readonly = self.client_for(reader)
        read_panel = readonly.get(self.dispatch_url)
        self.assertEqual(read_panel.status_code, 200)
        self.assertNotIn('data-fuel-planning-form', read_panel.json['html'])
        self.assertNotIn('data-apu-editor-form', read_panel.json['html'])
        self.assertNotIn('neoscorpion-fueler-off-form', read_panel.json['html'])
        read_page = readonly.get('/neoscorpion/fuel-dispatch').get_data(as_text=True)
        self.assertIn(f'data-fuel-data-open="{self.dispatch_url}"', read_page)
        self.assertEqual(self.save(readonly, self.dispatch_url, self.baseline(), notes='forbidden').status_code, 403)
        self.assertEqual(self.save(readonly, self.dispatch_url + '/off', self.baseline()).status_code, 403)
        self.assertEqual(self.baseline()['notes'], '')

    def test_all_editable_fields_have_conflict_protection(self):
        self.setup_assignment()
        self._save_complete(self.assignment)
        db.session.commit()
        for field, first, second in [
            ('remaining_left', '11', '12'), ('actual_left', '12', '13'),
            ('transfer_fuel_gallons', '200', '300'), ('notes', 'first', 'second'),
            ('apu_running', 'yes', 'not_confirmed'),
        ]:
            with self.subTest(field=field):
                old = self.baseline()
                extras = {'apu_source_tank_code': 'left'} if field == 'apu_running' else {}
                self.assertEqual(self.save(self.fueler, self.fueler_url, old, **{field: first}, **extras).status_code, 200)
                self.assertEqual(self.save(self.dispatch, self.dispatch_url, old, **{field: second}).status_code, 409)
        for fields, stale_fields in [
            ({'apu_source_tank_code': 'right'}, {'apu_source_tank_code': 'ctr'}),
            ({'apu_override_present': '1', 'apu_override_enabled': '1', 'apu_override_allowance': '2'},
             {'apu_override_present': '1', 'apu_override_enabled': '0'}),
            ({'apu_override_present': '1', 'apu_override_allowance': '3'},
             {'apu_override_present': '1', 'apu_override_allowance': '4'}),
        ]:
            old = self.baseline()
            self.assertEqual(self.save(self.fueler, self.fueler_url, old, **fields).status_code, 200)
            self.assertEqual(self.save(self.dispatch, self.dispatch_url, old, **stale_fields).status_code, 409)
