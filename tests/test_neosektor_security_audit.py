"""Local authorization, malformed-input and fulfillment race regressions."""
import os
import re
import unittest
from datetime import date, datetime, time, timedelta
from unittest.mock import patch

from sqlalchemy import event

from app.extensions import db
from app.models import Gateway, NeoErmacUldRequest, NeoSektorOperationalSetting, NeoSektorUldOnTheWayEvent, PermissionRule
from app.services import uld_requests
from app.services.neosektor_sheets_compat import clear_neosektor_google_cache
from tests import test_neosektor_routes as fixtures
from tests.test_neosektor_integration_modes import _FakeWorksheet, FAKE_SHEETS_ENV


class NeoSektorSecurityAuditTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.NeoSektorRoutesTest()
        self.fixture.setUp()
        self.fixture._login_approved_user('simulator')
        self.client = self.fixture.client

    def tearDown(self):
        clear_neosektor_google_cache()
        self.fixture.tearDown()

    def test_google_primary_bay_edits_cannot_cross_authorized_side(self):
        settings = NeoSektorOperationalSetting.query.one()
        settings.integration_mode = 'google_primary'
        for side, allowed, forbidden, own_bay, other_bay, own_cell, other_cell in (
            ('east', 'ebm', 'wbm', 'Bay 1', 'Bay 4', 'B6', 'C6'),
            ('west', 'wbm', 'ebm', 'Bay 4', 'Bay 1', 'C6', 'B6'),
        ):
            with self.subTest(side=side):
                for name, role in ((allowed, 'operator'), (forbidden, 'grandmaster')):
                    PermissionRule.query.filter_by(permission_key=f'neosektor.{name}.edit').one().minimum_role = role
                db.session.commit()
                clear_neosektor_google_cache()
                sheet = _FakeWorksheet()
                previous_other = sheet.values[other_cell]
                opposite = 'west' if side == 'east' else 'east'
                with patch.dict(os.environ, FAKE_SHEETS_ENV), patch(
                    'app.services.neosektor_sheets_compat._get_worksheet', return_value=sheet
                ):
                    self.assertEqual(self.client.post(f'/neosektor/ballmat/update?side={opposite}',
                        json={'side': opposite, 'bay_statuses': {other_bay: 'Light'}}).status_code, 403)
                    response = self.client.post(f'/neosektor/ballmat/update?side={side}', json={
                        'side': side, 'bay_statuses': {own_bay: 'Light', other_bay: 'Light'}})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(sheet.updates, [(own_cell, 'Light')])
                self.assertEqual(sheet.values[other_cell], previous_other)

    def test_discharge_refreshes_request_after_concurrent_send_and_locks_before_read(self):
        for explicit_id in (True, False):
            with self.subTest(explicit_id=explicit_id):
                row = NeoErmacUldRequest(gateway_id=self.fixture.gateway.id,
                                        door='D1', a2_count=10)
                db.session.add(row)
                db.session.commit()
                row_id = row.id
                self.assertEqual(row.a2_count, 10)  # Hold an older ORM snapshot.
                with self.fixture.app.app_context():
                    gateway = db.session.get(type(self.fixture.gateway), self.fixture.gateway.id)
                    uld_requests.send_uld_on_the_way(gateway, 'D1', 'A2', 3, request_id=row_id)
                    db.session.commit()
                sql = []
                def capture(conn, cursor, statement, params, context, many):
                    sql.append(' '.join(statement.lower().split()))
                event.listen(db.engine, 'before_cursor_execute', capture)
                try:
                    uld_requests.send_uld_on_the_way(self.fixture.gateway, 'D1', 'A2', 2,
                                                   request_id=row_id if explicit_id else None)
                    db.session.commit()
                finally:
                    event.remove(db.engine, 'before_cursor_execute', capture)
                db.session.expire_all()
                self.assertEqual(db.session.get(NeoErmacUldRequest, row_id).a2_count, 5)
                lock = sql.index('update gateways set id=id where id=?')
                request_read = next(i for i, query in enumerate(sql)
                                    if query.startswith('select ') and 'from neoermac_uld_requests' in query)
                self.assertLess(lock, request_read)
                db.session.delete(db.session.get(NeoErmacUldRequest, row_id))
                db.session.commit()

    def test_malformed_json_commands_are_rejected_before_operational_work(self):
        cases = [
            ('tunnel-conductor/wave', [1]),
            ('tunnel-conductor/offset', True),
            ('tunnel-conductor/offset', {'west_offset': float('inf')}),
            ('tunnel-conductor/settings', 'bad'),
            ('tunnel-conductor/ballmat', {'side': 'east', 'waves': {'first': [1]}}),
            ('ballmat/update?side=east', {'side': 'east', 'bay_statuses': [1]}),
            ('discharge/send', [1]),
        ]
        for path, body in cases:
            with self.subTest(path=path), patch(
                'app.neonodes.neosektor.routes._neosektor_write_bundle',
                side_effect=AssertionError('Malformed input acquired operational state'),
            ):
                response = self.client.post('/neosektor/' + path, json=body)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json, {'ok': False, 'error': 'Invalid JSON command.'})

    def test_discharge_rejects_integer_overflow_before_lock_or_event_insert(self):
        row = NeoErmacUldRequest(gateway_id=self.fixture.gateway.id, door='D1', a2_count=5)
        db.session.add(row)
        db.session.commit()
        with patch.object(uld_requests, '_lock_uld_increment', side_effect=AssertionError('Unexpected lock')):
            response = self.client.post('/neosektor/discharge/send', json={
                'door': 'D1', 'request_id': row.id, 'uld_type': 'A2', 'quantity': 2**63})
        self.assertEqual(response.status_code, 400)
        db.session.refresh(row)
        self.assertEqual(row.a2_count, 5)
        self.assertEqual(NeoSektorUldOnTheWayEvent.query.count(), 0)
        response = self.client.post('/neosektor/discharge/send', json={
            'door': 'D1', 'request_id': 2**63, 'uld_type': 'A2', 'quantity': 1})
        self.assertEqual(response.status_code, 400)
        db.session.refresh(row)
        self.assertEqual(row.a2_count, 5)

    def test_mutations_require_csrf_and_spotters_cannot_enter_conductor_endpoints(self):
        paths = ['tunnel-conductor/' + suffix for suffix in
                 ('wave', 'offset', 'settings', 'ballmat', 'discharge-controls', 'spotter-mode')]
        self.fixture.app.config['CSRF_PROTECT_TESTING'] = True
        for path in paths + ['ballmat/update?side=east', 'ballmat/mode-request?side=east', 'discharge/send']:
            with self.subTest(csrf=path):
                response = self.client.post('/neosektor/' + path, json={})
                self.assertEqual(response.status_code, 400)
                self.assertIn('CSRF validation failed', response.json['error'])
        page = self.client.get('/neosektor/tunnel-conductor')
        token = re.search(rb'<meta name="csrf-token" content="([^"]+)"', page.data).group(1).decode()
        self.assertEqual(self.client.post('/neosektor/tunnel-conductor/discharge-controls',
            json={'action': 'back_pickup', 'side': 'east', 'enabled': True, 'expected_enabled': False},
            headers={'X-CSRF-Token': token}).status_code, 200)
        PermissionRule.query.filter_by(permission_key='neosektor.tunnel_conductor.edit').one().minimum_role = 'grandmaster'
        db.session.commit()
        for path in paths:
            with self.subTest(permission=path):
                response = self.client.post('/neosektor/' + path, json={}, headers={'X-CSRF-Token': token})
                self.assertEqual(response.status_code, 403)

    def test_discharge_rejects_other_gateway_operation_and_door(self):
        day = date(2026, 9, 10)
        current = self.fixture._add_sort_operation(day, 'night')
        old = self.fixture._add_sort_operation(day - timedelta(days=1), 'night')
        self.fixture._set_sort_window('night', time(22), time(2))
        self.fixture.app.config['CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE'] = datetime(2026, 9, 11, 0, 30)
        other = Gateway(code='AUDIT', name='Audit only')
        db.session.add(other)
        db.session.flush()
        rows = [NeoErmacUldRequest(gateway_id=gateway_id, sort_date_operation_id=operation_id,
                                  door=door, a2_count=5)
                for gateway_id, operation_id, door in (
                    (other.id, current.id, 'D1'), (self.fixture.gateway.id, old.id, 'D1'),
                    (self.fixture.gateway.id, current.id, 'D2'))]
        db.session.add_all(rows)
        db.session.commit()
        for row in rows:
            with self.subTest(request_id=row.id):
                response = self.client.post('/neosektor/discharge/send', json={
                    'request_id': row.id, 'door': 'D1', 'uld_type': 'A2', 'quantity': 2,
                    'gateway_id': row.gateway_id, 'operation_id': row.sort_date_operation_id})
                self.assertEqual(response.status_code, 400)
                db.session.refresh(row)
                self.assertEqual(row.a2_count, 5)
        self.assertEqual(NeoSektorUldOnTheWayEvent.query.count(), 0)

    def test_poll_apis_require_login(self):
        self.client.post('/logout')
        for path in ('live-counts/state', 'ballmat/state?side=east', 'ballmat/state?side=west',
                     'tunnel-conductor/state', 'driver-routing/state', 'driver-routing/version', 'discharge/state'):
            with self.subTest(path=path):
                response = self.client.get('/neosektor/' + path)
                self.assertEqual(response.status_code, 302)
                self.assertIn('/login', response.location)
