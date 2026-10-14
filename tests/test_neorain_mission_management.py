"""Rain uses MotherBrain's canonical current-sort mission/tail operations."""
import unittest
from unittest.mock import patch
from datetime import datetime
from tests import test_neorain_outbound as fixtures
from app.extensions import db
from app.models import SortDateMission, SortDateTailState
from app.services.live_collaboration import entity_version


class RainMissionManagementTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.NeoRainOutboundTest(); self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.client = self.fixture.client
        self.operation = self.fixture._operation()
        self.actor = self.fixture._user('rain_mission_operator', 'simulator', rain_role='simulator')
        db.session.commit(); self.fixture._login(self.actor)
        self.scope = patch('app.neonodes.neorain.mission_routes.current_neorain_outbound_operation', return_value=self.operation)
        self.scope.start(); self.addCleanup(self.scope.stop)

    def form(self, flight='UPS0948', **extra):
        return dict(operation_id=self.operation.id, flight_number=flight, origin='SDF', destination='RFD',
            assigned_tail_number='N438UP', planned_time_local='02:35', timezone='America/Chicago', wave='1', **extra)

    def test_create_edit_and_canonical_tail(self):
        response = self.client.post('/neorain/inbound/missions/new', data=self.form())
        self.assertEqual(response.status_code,302,response.get_data(as_text=True))
        arrival = SortDateMission.query.one()
        self.assertEqual(arrival.mission_source,'manual'); self.assertEqual(arrival.mission_type,'arrival')
        self.assertEqual(arrival.planned_datetime_local.strftime('%H:%M'),'02:35')
        self.assertIsNotNone(SortDateTailState.query.filter_by(tail_number='N438UP').first())
        version = entity_version(arrival)
        response = self.client.post(f'/neorain/inbound/missions/{arrival.id}/edit', data=self.form('UPS900',expected_version=version))
        self.assertEqual(response.status_code,302,response.get_data(as_text=True))
        self.assertEqual(arrival.flight_number,'UPS900')
        self.assertEqual(self.client.post(f'/neorain/inbound/missions/{arrival.id}/edit',data=self.form('UPS901',expected_version=version)).status_code,409)
        response = self.client.post('/neorain/outbound/missions/new',data={**self.form('UPS910'), 'origin':'RFD','destination':'LAX'})
        self.assertEqual(response.status_code,302,response.get_data(as_text=True))
        self.assertEqual(SortDateMission.query.count(),2)

    def test_duplicate_cross_direction_master_and_edit_rejection(self):
        existing = self.fixture._mission(self.operation,'UPS948','SDF',planned=datetime(2026,8,31,7))
        db.session.commit()
        self.assertEqual(self.client.post('/neorain/inbound/missions/new',data=self.form()).status_code,400)
        self.assertEqual(SortDateMission.query.count(),1)
        self.assertEqual(self.client.get(f'/neorain/outbound/missions/{existing.id}/edit').status_code,404)
        self.assertEqual(self.client.post('/neorain/inbound/missions/new',data=self.form('UPS999')).status_code,302)
        manual=SortDateMission.query.filter_by(flight_number='UPS999').one()
        self.assertEqual(self.client.post(f'/neorain/inbound/missions/{manual.id}/edit',data=self.form(expected_version=entity_version(manual))).status_code,400)
        self.assertEqual(manual.flight_number,'UPS999')

    def test_permissions_changed_sort_and_locked_sort(self):
        watcher=self.fixture._user('rain_mission_watcher','watcher',rain_role='watcher');db.session.commit();self.fixture._login(watcher)
        self.assertEqual(self.client.post('/neorain/inbound/missions/new',data=self.form()).status_code,403)
        self.fixture._login(self.actor)
        self.assertEqual(self.client.post('/neorain/inbound/missions/new',data={**self.form(),'operation_id':999}).status_code,409)
        self.operation.locked_at_utc=datetime.utcnow();db.session.commit()
        self.assertEqual(self.client.post('/neorain/inbound/missions/new',data=self.form()).status_code,409)

    def test_standalone_spare_hot_and_clear(self):
        response=self.client.post('/neorain/outbound/tails',data=dict(operation_id=self.operation.id,action='add_spare',tail_number='N438UP',aircraft_type='A300'))
        self.assertEqual(response.status_code,302,response.get_data(as_text=True))
        tail=SortDateTailState.query.one()
        for action in ('clear_spare',):
            response=self.client.post('/neorain/outbound/tails',data=dict(operation_id=self.operation.id,action=action,tail_number=tail.tail_number,expected_version=entity_version(tail)))
            self.assertEqual(response.status_code,302,response.get_data(as_text=True))
        self.assertEqual(self.client.post('/neorain/outbound/tails',data=dict(operation_id=self.operation.id,action='add_spare',tail_number='N438UP',aircraft_type='A300')).status_code,409)
        self.assertEqual(self.client.post('/neorain/inbound/missions/new',data=self.form('UPS901')).status_code,302)
        for action in ('hot','clear_hot'):
            response=self.client.post('/neorain/outbound/tails',data=dict(operation_id=self.operation.id,action=action,tail_number=tail.tail_number,expected_version=entity_version(tail)))
            self.assertEqual(response.status_code,302,response.get_data(as_text=True))
