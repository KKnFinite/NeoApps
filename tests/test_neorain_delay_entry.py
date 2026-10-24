"""Delay entry obeys MotherBrain's gateway role and configurable edit rules."""
from datetime import date
import unittest
from unittest.mock import patch

from tests import test_neorain_routes as fixtures
from app.extensions import db
from app.models import (Gateway, GatewayMembership, GatewayNodeRole, NeoNode,
    NeoRainDelayInfo, PermissionRule, PortalAppAccess, SortDateMission, SortDateOperation)
from app.services.live_collaboration import entity_version
from app.services.request_cache import clear_request_cache


class RainDelayEntryTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.NeoRainRoutesTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.app, self.client = self.fixture.app, self.fixture.client
        self.user = self.fixture._rain_user('rain_delay_master', 'master')
        self.user.role = 'watcher'
        PortalAppAccess.query.filter_by(user_id=self.user.id, app_code='neogateway').one().role = 'watcher'
        gateway = Gateway.query.filter_by(code='RFD').one()
        self.operation = SortDateOperation(gateway_id=gateway.id, gateway_code='RFD',
            sort_date=date(2026,10,23), sort_name='night')
        db.session.add(self.operation)
        db.session.flush()
        self.missions = {}
        for board, kind in [('inbound','arrival'), ('outbound','departure')]:
            mission = SortDateMission(sort_date_operation_id=self.operation.id,
                sort_date=self.operation.sort_date, gateway_code='RFD', sort_name='night',
                mission_type=kind, mission_source='manual', flight_number='UPS123',
                assigned_tail_number='N123UP', origin='SDF', destination='RFD')
            db.session.add(mission)
            self.missions[board] = mission
        db.session.commit()
        self.fixture._login(self.user)
        self.current = patch('app.neonodes.neorain.routes.current_neorain_outbound_operation',
            return_value=self.operation)
        self.operation_mock = self.current.start()
        self.addCleanup(self.current.stop)

    def post(self, board='inbound', **changes):
        data = dict(board=board, mission_id=self.missions[board].id, action='add',
                    minutes='10', code='RS', notes='Canonical mission delay')
        data.update(changes)
        return self.client.post('/neorain/delay-info', data=data,
            headers={'X-Requested-With':'XMLHttpRequest'})

    def test_master_crud_both_boards_with_watcher_account(self):
        for board in self.missions:
            saved = self.post(board)
            self.assertEqual(saved.status_code, 200, saved.data)
            row = NeoRainDelayInfo.query.filter_by(sort_date_mission_id=self.missions[board].id).one()
            self.assertEqual(row.notes, 'Canonical mission delay')
            updated = self.post(board, action='update', delay_id=row.id,
                expected_version=entity_version(row), notes='Saved correction')
            self.assertEqual(updated.status_code, 200, updated.data)
            self.assertIn('Saved correction', updated.json['html'])
            removed = self.post(board, action='delete', delay_id=row.id,
                expected_version=entity_version(row))
            self.assertEqual(removed.status_code, 200, removed.data)
        self.assertEqual(NeoRainDelayInfo.query.count(), 0)

    def test_motherbrain_configured_threshold_controls_view_and_post(self):
        for board in self.missions:
            rule = PermissionRule.query.filter_by(permission_key=f'neorain.{board}.edit').one()
            rule.minimum_role = 'grandmaster'
            db.session.commit(); clear_request_cache()
            self.assertEqual(self.post(board).status_code, 403)
            page = self.client.get('/neorain/' + board)
            self.assertNotIn(b'class="neorain-delay-line-form"', page.data)
            rule.minimum_role = 'master'
            db.session.commit(); clear_request_cache()
            self.assertEqual(self.post(board).status_code, 200)
            self.assertIn(b'class="neorain-delay-line-form"', self.client.get('/neorain/' + board).data)

    def test_gateway_watcher_cannot_use_account_role_to_edit(self):
        membership = GatewayMembership.query.filter_by(user_id=self.user.id).one()
        rain = NeoNode.query.filter_by(code='rain').one()
        GatewayNodeRole.query.filter_by(gateway_membership_id=membership.id, node_id=rain.id).one().role = 'watcher'
        self.user.role = 'master'
        db.session.commit(); clear_request_cache()
        for board in self.missions:
            self.assertEqual(self.post(board).status_code, 403)
        self.assertEqual(NeoRainDelayInfo.query.count(), 0)

    def test_invalid_values_return_actionable_errors_without_records(self):
        for board in self.missions:
            for values in ({'minutes':''}, {'minutes':'NaN'}, {'minutes':'-1'}, {'code':'LONG'}):
                response = self.post(board, **values)
                self.assertEqual(response.status_code, 400, response.data)
                self.assertTrue(response.json['error'])
        self.assertEqual(NeoRainDelayInfo.query.count(), 0)

    def test_stale_delay_cannot_overwrite_newer_entry(self):
        self.assertEqual(self.post().status_code, 200)
        row = NeoRainDelayInfo.query.one()
        version = entity_version(row)
        self.assertEqual(self.post(action='update', delay_id=row.id,
            expected_version=version, notes='Newer notes').status_code, 200)
        for action in ('update', 'delete'):
            response = self.post(action=action, delay_id=row.id,
                expected_version=version, notes='Stale notes')
            self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(NeoRainDelayInfo.query.one().notes, 'Newer notes')

    def test_cross_board_and_sort_identity_rejected(self):
        response = self.post('inbound', mission_id=self.missions['outbound'].id)
        self.assertEqual(response.status_code, 409)
        self.operation_mock.return_value = None
        self.assertEqual(self.post().status_code, 409)
        self.assertEqual(NeoRainDelayInfo.query.count(), 0)

    def test_normal_form_submission_remains_compatible(self):
        response = self.client.post('/neorain/delay-info', data=dict(board='inbound',
            mission_id=self.missions['inbound'].id, action='add', minutes='5', code='GL'))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith('#delay-info-' + str(self.missions['inbound'].id)))
        self.assertEqual(NeoRainDelayInfo.query.one().minutes, 5)
