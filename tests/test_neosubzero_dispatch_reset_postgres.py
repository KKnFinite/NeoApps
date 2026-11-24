"""Coordinator corrections keep evidence but remove superseded work from live projections."""
import unittest
from unittest.mock import patch

from app.extensions import db
from app.models import Gateway, SortDateOperation, SortDateMission, NeoSubZeroAuditEntry as Audit, NeoSubZeroDepartureDeiceEvent as Event, NeoSubZeroSprayRecord as Spray
from app.services.neosubzero_evidence import evidence_values
from app.services.neosubzero_operations import operations_context
from app.services.neosubzero_reporting import actual_passes
from tests import test_neosubzero_radio_postgres as radio


@unittest.skipUnless(radio.pg.URI, 'Requires disposable loopback PostgreSQL')
class DispatchResetTest(unittest.TestCase):
    def setUp(self):
        self.case = radio.RadioPostgresTest()
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.flight = self.case.f.alpha
        self.operation = self.case.f.operation

    def row(self):
        rows = operations_context(self.case.f.gateway, self.operation, 'outbound')['rows']
        return next(row for row in rows if row['mission_id'] == self.flight.id)

    def check_stage(self, stage):
        self.flight.actual_block_out_datetime_utc = None
        self.case.second.actual_block_out_datetime_utc = None
        db.session.commit()
        event = self.case.mutate(self.flight, 'initial_contact')
        if stage == 'configured':
            event = self.case.mutate(self.flight, 'toggle_configured')
        if stage in ('partial', 'start'):
            start = '22:10' if stage == 'start' else ''
            event = self.case.mutate(self.flight, 'save_treatment', treatment_plan='one_type_i', pass1_surface_area='wings_only', pass1_start=start)
        if stage in ('fin', 'clear', 'radio'):
            event = self.case.finish()
        if stage in ('negative', 'not_sprayed'):
            self.case.mutate(self.flight, 'set_negative' if stage == 'negative' else 'manual_not_sprayed')
        if stage in ('clear', 'radio', 'negative', 'not_sprayed'):
            self.case.mutate(self.flight, 'clear')
        if stage == 'radio':
            self.case.mutate(self.flight, 'radio_complete')
        before = evidence_values(event)
        children = [evidence_values(r) for r in Spray.query.filter_by(departure_deice_event_id=event.id).order_by(Spray.id)]
        response = self.case.post(event, 'reset', confirm_reset='1', correction_reason='Training entry')
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:400])
        db.session.expire_all()
        self.assertIsNotNone(event.reset_at_utc)
        for key, value in before.items():
            if key not in ('updated_at', 'reset_at_utc'):
                self.assertEqual(evidence_values(event)[key], value, key)
        self.assertEqual(children, [evidence_values(r) for r in Spray.query.filter_by(departure_deice_event_id=event.id).order_by(Spray.id)])
        audit = Audit.query.filter_by(event_id=event.id, action='departure_reset').one()
        self.assertEqual((audit.reason, audit.actor_user_id), ('Training entry', self.case.actor.id))
        self.assertFalse(any(row['parent'].id == event.id for row in actual_passes(self.operation, activity='departure')))
        self.assertIsNone(self.row()['event'])
        self.assertFalse(self.row()['terminal'])
        self.assertIsNone(self.case.ramp()['pad'])
        self.assertFalse(self.case.ramp()['active_conflicts'])

    def test_planned(self):
        self.check_stage('planned')

    def test_configured(self):
        self.check_stage('configured')

    def test_partial(self):
        self.check_stage('partial')

    def test_start(self):
        self.check_stage('start')

    def test_fin(self):
        self.check_stage('fin')

    def test_clear(self):
        self.check_stage('clear')

    def test_radio(self):
        self.check_stage('radio')

    def test_negative(self):
        self.check_stage('negative')

    def test_not_sprayed(self):
        self.check_stage('not_sprayed')

    def test_new_mistake_does_not_reopen_prior_legitimate_clear(self):
        prior = self.case.mutate(self.flight, 'clear')
        replacement = self.case.mutate(self.flight, 'initial_contact')
        self.assertNotEqual(prior.id, replacement.id)
        response = self.case.post(replacement, 'reset', confirm_reset='1', correction_reason='Duplicate initiation')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.row()['event'].id, prior.id)
        self.assertEqual(self.row()['visual_state'], 'gone')
        self.assertIsNone(prior.reset_at_utc)

    def test_second_coordinators_note_rejects_stale_reset(self):
        event = self.case.finish()
        before = self.case.data(event, self.flight, 'reset', confirm_reset='1', correction_reason='Training entry')
        peer = self.case.f._user('reset-peer','grandmaster')
        note = self.case.data(event,self.flight,'save_notes',notes='Hold for review')
        client = self.case.f.app.test_client()
        with self.case.f.app.app_context():
            self.case.f._login(client,peer)
        with self.case.f.app.app_context():
            response = client.post('/neosubzero/operations/mutate',data=note)
            self.assertEqual(response.status_code,200,response.location or response.json)
        self.assertEqual(self.case.client.post('/neosubzero/operations/mutate',data=before).status_code,409)
        self.assertIsNone(event.reset_at_utc)
        self.assertEqual(Audit.query.filter_by(action='departure_save_notes',actor_user_id=peer.id).count(),1)

    def test_gallons_change_invalidates_reset_and_archive_keeps_both_revisions(self):
        from app.services.live_collaboration import entity_version
        from app.services.neosubzero_archive import drain
        from app.services.neosubzero_spray import set_neosubzero_spray_gallons, neosubzero_deice_log
        event = self.case.finish()
        record = Spray.query.filter_by(departure_deice_event_id=event.id).one()
        stale = entity_version(event)
        set_neosubzero_spray_gallons(self.operation, self.flight, event, 1, record.position_number,
            '12.5', record=record, user_id=self.case.actor.id)
        db.session.commit()
        reason = dict(confirm_reset='1', correction_reason='Incorrect training pass')
        self.assertEqual(self.case.post(event, 'reset', expected_version=stale, **reason).status_code, 409)
        from app.services import neoscorpion_learning_vault as vault
        payloads = []
        def write(config, key, payload, checksum, metadata):
            payloads.append(payload)
            return dict(key=key, checksum=checksum, verified=True)
        with patch.object(vault, '_require_r2_config', return_value={}), patch.object(vault, '_verified_write', side_effect=write):
            self.assertEqual(drain(self.case.f.gateway.id),1)
            self.assertEqual(self.case.post(event, 'reset', **reason).status_code,200)
            self.assertEqual(drain(self.case.f.gateway.id),1)
            self.assertEqual(drain(self.case.f.gateway.id),0)
        self.assertEqual(payloads[0]['record_type'],'actual_fluid_application')
        self.assertEqual(payloads[2]['state'],'event_superseded')
        self.assertFalse(payloads[3]['training_eligible'])
        self.assertEqual(float(record.gallons),12.5)
        self.assertFalse(neosubzero_deice_log(self.operation))
        with self.assertRaisesRegex(ValueError,'superseded'):
            set_neosubzero_spray_gallons(self.operation,self.flight,event,1,record.position_number,
                '14',record=record,user_id=self.case.actor.id)

    def test_confirmation_reason_identity_and_competing_corrections(self):
        event = Event.query.filter_by(sort_date_mission_id=self.flight.id).one()
        for values in ({}, {'confirm_reset':'1'}, {'correction_reason':'test'}, {'confirm_reset':'1','correction_reason':'test','event_id':''}):
            self.assertEqual(self.case.post(event, 'reset', **values).status_code, 409)
        request = self.case.data(event, self.flight, 'reset', confirm_reset='1', correction_reason='Test correction')
        block = self.flight.actual_block_out_datetime_utc
        gateway_id, operation_id, mission_id = self.case.f.gateway.id, self.operation.id, self.flight.id
        self.assertCountEqual(self.case.race(request, request), [200,409])
        self.case.f.gateway = db.session.get(Gateway, gateway_id)
        self.operation = db.session.get(SortDateOperation, operation_id)
        self.flight = db.session.get(SortDateMission, mission_id)
        self.assertEqual(Audit.query.filter_by(action='departure_reset').count(),1)
        self.assertEqual(self.flight.actual_block_out_datetime_utc, block)
        self.assertEqual(self.row()['visual_state'], 'gone')
