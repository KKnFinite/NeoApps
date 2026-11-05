"""Optional event observations, official reason integrity and testing exclusion."""
import copy
import re
import unittest
from unittest.mock import patch
from flask import g
from tests import test_neosubzero_ucc as fixtures
from app.extensions import db
from app.models import NeoSubZeroAuditEntry, NeoSubZeroSprayRecord, NeoSubZeroUccTruckAssignment
from app.services.live_collaboration import entity_version
from app.services import frost_treatment_observation as observation, frost_analysis, frost_model
from app.services.neosubzero_departure_deice import mutate_departure_deice, departure_deice_context, departure_deice_revision
from app.services.neosubzero_evidence import evidence_values
from app.services.neosubzero_spray import set_departure_deice_reason
from app.services.neosubzero_reporting import actual_passes, archive_payload, learning_context
from app.services.neosubzero_ucc import set_neosubzero_ucc_assignment


class FrostTreatmentObservationTest(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.NeoSubZeroUccTest()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)
        self.actor = self.f._user('observation-coordinator','simulator')
        self.event = mutate_departure_deice(self.f.operation,self.f.alpha,'initial_contact',{})
        set_departure_deice_reason(self.event,'Frost')
        db.session.commit()
        self.client = self.f.app.test_client()
        self.f._login(self.client,self.actor)

    def audits(self):
        return NeoSubZeroAuditEntry.query.filter_by(action=observation.ACTION).order_by(NeoSubZeroAuditEntry.id).all()

    def post(self, client=None, **changes):
        latest = self.audits()[-1] if self.audits() else None
        values = dict(board='coordinator', operation_id=self.f.operation.id,mission_id=self.f.alpha.id,
            physical_tail=self.f.alpha.assigned_tail_number,event_id=self.event.id,
            expected_version=entity_version(self.event),expected_observation_id=str(latest.id) if latest else '')
        values.update(changes)
        g.pop('_login_user',None)
        with patch('app.neonodes.neosubzero.routes.current_neosubzero_operation',return_value=self.f.operation):
            return (client or self.client).post('/neosubzero/frost-treatment-observation',data=values,headers={'Accept':'application/json'})

    def physical(self):
        return {k:v for k,v in copy.deepcopy(evidence_values(self.event)).items() if k!='updated_at'}

    def spray(self, end='2205'):
        set_neosubzero_ucc_assignment(self.f.operation,'Alpha',1,'driver',self.f.permanent)
        db.session.add(NeoSubZeroUccTruckAssignment(sort_date_operation_id=self.f.operation.id,ramp='Alpha',position_number=1,truck_number='TEST-12'))
        mutate_departure_deice(self.f.operation,self.f.alpha,'save_treatment',dict(treatment_plan='one_type_i',pass1_surface_area='wings_only',pass1_start='2200',pass1_end=end),event=self.event)
        db.session.commit()

    def test_unchecked_legacy_is_unknown_and_noop(self):
        version = entity_version(self.event)
        before = self.physical()
        self.assertEqual(self.post().status_code,200)
        self.assertEqual(self.audits(),[])
        self.assertEqual(entity_version(self.event),version)
        self.assertEqual(before,self.physical())
        row = next(r for r in departure_deice_context(self.f.gateway,self.f.operation)['rows'] if r['mission_id']==self.f.alpha.id)
        self.assertIsNone(row['frost_treatment_observation'])
        self.assertTrue(row['frost_observation_available'])
        self.assertEqual(observation.interpretation('Frost',None)['label'],'UNKNOWN')

    def test_checked_records_actor_time_event_and_preserves_official_evidence(self):
        before = self.physical()
        revision = departure_deice_revision(self.f.gateway,self.f.operation)
        self.assertEqual(self.post(no_frost_present='yes',training_eligible='true').status_code,200)
        entry = self.audits()[0]
        payload = entry.after_values['observation']
        self.assertEqual((entry.event_id,entry.actor_user_id,entry.tail_number),(self.event.id,self.actor.id,self.event.tail_number))
        self.assertEqual(payload['state'],'absent')
        self.assertTrue(payload['observed_at_utc'].endswith('Z'))
        self.assertFalse(payload['training_eligible'])
        self.assertEqual(before,self.physical())
        self.assertNotEqual(revision,departure_deice_revision(self.f.gateway,self.f.operation))
        self.assertEqual(NeoSubZeroSprayRecord.query.count(),0)

    def test_identical_save_is_noop_and_stale_change_conflicts(self):
        stale = entity_version(self.event)
        self.assertEqual(self.post(no_frost_present='yes').status_code,200)
        version = entity_version(self.event)
        self.assertEqual(self.post(no_frost_present='yes').status_code,200)
        self.assertEqual(entity_version(self.event),version)
        self.assertEqual(len(self.audits()),1)
        self.assertEqual(self.post(expected_version=stale,correction_reason='stale correction').status_code,409)
        self.assertEqual(self.post(expected_observation_id='',correction_reason='stale observation').status_code,409)
        self.assertEqual(self.audits()[0].after_values['observation']['state'],'absent')

    def test_withdrawal_requires_reason_and_retains_both_audit_revisions(self):
        self.assertEqual(self.post(no_frost_present='yes').status_code,200)
        original = copy.deepcopy(self.audits()[0].after_values)
        self.assertEqual(self.post().status_code,409)
        self.assertEqual(self.post(correction_reason='Observation entered for wrong condition').status_code,200)
        first,second = self.audits()
        self.assertEqual(first.after_values,original)
        self.assertEqual(second.before_values['observation']['state'],'absent')
        self.assertEqual(second.after_values['observation']['state'],'unknown')
        self.assertIsNone(observation.interpretation('Frost',second.after_values['observation'])['physical_frost_confirmed'])
        self.assertEqual(second.reason,'Observation entered for wrong condition')
        self.assertEqual(self.event.reason_for_application,'Frost')

    def test_wrong_reason_is_rejected_and_other_reason_classification_unchanged(self):
        set_departure_deice_reason(self.event,'Snow')
        db.session.commit()
        self.assertEqual(self.post(no_frost_present='yes').status_code,409)
        self.assertEqual(self.audits(),[])
        self.assertEqual(observation.interpretation('Snow',dict(state='absent')),frost_analysis.classification('Snow'))
        set_departure_deice_reason(self.event,'Frost')
        db.session.commit()
        self.assertEqual(self.post(no_frost_present='yes').status_code,200)
        set_departure_deice_reason(self.event,'Snow')
        db.session.commit()
        self.assertEqual(self.post(correction_reason='Withdraw prior observation after review').status_code,200)
        self.assertEqual(self.event.reason_for_application,'Snow')
        self.assertEqual(self.audits()[-1].after_values['observation']['state'],'unknown')

    def test_identity_sort_and_authority_rejections_do_not_partially_commit(self):
        before = self.physical()
        for values in (dict(event_id=-1),dict(operation_id=-1),dict(physical_tail='N-WRONG'),dict(mission_id=self.f.remote.id,physical_tail=self.f.remote.assigned_tail_number)):
            with self.subTest(values=values):
                self.assertEqual(self.post(no_frost_present='yes',**values).status_code,409)
                self.assertEqual(self.audits(),[])
                self.assertEqual(before,self.physical())
        watcher = self.f._user('observation-watcher','watcher')
        client = self.f.app.test_client()
        g.pop('_login_user',None)
        self.f._login(client,watcher)
        self.assertEqual(self.post(client=client,no_frost_present='yes').status_code,403)
        self.assertEqual(self.post(board='deicer',no_frost_present='yes').status_code,403)

    def test_csrf_is_required(self):
        self.f.app.config['CSRF_PROTECT_TESTING'] = True
        self.assertEqual(self.post(no_frost_present='yes').status_code,400)
        self.assertEqual(self.audits(),[])
        g.pop('_login_user',None)
        with patch('app.neonodes.neosubzero.routes.current_neosubzero_operation',return_value=self.f.operation):
            page = self.client.get('/neosubzero/coordinator?ramp=Alpha&mission='+str(self.f.alpha.id))
        token = re.search(rb'name="csrf_token"[^>]*value="([^"]+)"',page.data).group(1).decode()
        self.assertEqual(self.post(no_frost_present='yes',csrf_token=token).status_code,200)

    def test_operations_transport_uses_same_permissions_versions_and_mode_guard(self):
        values = dict(target='/neosubzero/frost-treatment-observation',mode='outbound',board='outbound',
            operation_id=self.f.operation.id,mission_id=self.f.alpha.id,physical_tail=self.event.tail_number,
            event_id=self.event.id,expected_version=entity_version(self.event),no_frost_present='yes')
        g.pop('_login_user',None)
        with patch('app.neonodes.neosubzero.routes.current_neosubzero_operation',return_value=self.f.operation):
            response = self.client.post('/neosubzero/operations/mutate',data=values)
            self.assertEqual(response.status_code,200)
            self.assertTrue(response.json['ok'])
            self.assertEqual(len(self.audits()),1)
            rejected = self.client.post('/neosubzero/operations/mutate',data=values)
            self.assertEqual(rejected.status_code,409)
            self.assertEqual(self.client.post('/neosubzero/operations/mutate',data={**values,'mode':'pretreat'}).status_code,403)
        self.assertEqual(len(self.audits()),1)

    def test_failed_correction_rolls_back_revision_and_preserves_audit(self):
        self.assertEqual(self.post(no_frost_present='yes').status_code,200)
        version = entity_version(self.event)
        self.assertEqual(self.post(correction_reason='x'*501).status_code,409)
        self.assertEqual(entity_version(self.event),version)
        self.assertEqual(len(self.audits()),1)
        self.assertEqual(self.audits()[0].after_values['observation']['state'],'absent')

    def test_checkbox_is_optional_unchecked_and_saved_observation_is_visible(self):
        g.pop('_login_user',None)
        with patch('app.neonodes.neosubzero.routes.current_neosubzero_operation',return_value=self.f.operation):
            page = self.client.get('/neosubzero/coordinator?ramp=Alpha&mission='+str(self.f.alpha.id))
        self.assertEqual(page.status_code,200)
        tag = re.search(rb'<input[^>]*name="no_frost_present"[^>]*>',page.data).group()
        self.assertNotIn(b'checked',tag)
        self.assertNotIn(b'required',tag)
        self.assertIn(observation.LABEL.encode(),page.data)
        self.assertEqual(self.post(no_frost_present='yes').status_code,200)
        g.pop('_login_user',None)
        with patch('app.neonodes.neosubzero.routes.current_neosubzero_operation',return_value=self.f.operation):
            page = self.client.get('/neosubzero/coordinator?ramp=Alpha&mission='+str(self.f.alpha.id))
        self.assertIn(b'data-frost-treatment-observation',page.data)
        self.assertIn(('Observer #'+str(self.actor.id)).encode(),page.data)

    def test_spraying_never_requires_observation_and_cryotech_text_stays_identical(self):
        self.spray()
        self.assertEqual(self.event.status,'finished')
        row = actual_passes(self.f.operation)[0]
        original_text, original_payload = row['copy_text'],archive_payload(row)
        self.assertNotIn('physical_condition_observation',original_payload)
        self.assertEqual(self.post().status_code,200)
        self.assertEqual(archive_payload(actual_passes(self.f.operation)[0]),original_payload)
        before = self.physical()
        self.assertEqual(self.post(no_frost_present='yes').status_code,200)
        row = actual_passes(self.f.operation)[0]
        self.assertEqual(row['copy_text'],original_text)
        self.assertEqual(before,self.physical())
        self.assertEqual(row['reason'],'Frost')
        payload = archive_payload(row)
        self.assertEqual(payload['physical_condition_observation']['state'],'absent')
        context = learning_context(payload)
        self.assertIs(context['frost_observed'],False)
        self.assertFalse(context['training_eligible'])
        self.assertFalse(context['outcome_validation_eligible'])
        self.assertIsNone(frost_model.training_label(context))

    def test_during_treatment_observation_survives_fin_and_testing_is_permanent(self):
        self.spray(end='')
        with patch.dict(self.f.app.config,{'TESTING':False}):
            values = dict(event_id=self.event.id,physical_tail=self.event.tail_number,expected_version=entity_version(self.event),no_frost_present='yes')
            entry = observation.save(self.f.operation,self.f.alpha,self.event,values,actor_id=self.actor.id)
            db.session.commit()
        metadata = entry.after_values['observation']
        self.assertTrue(metadata['testing_phase'])
        self.assertFalse(metadata['outcome_validation_eligible'])
        mutate_departure_deice(self.f.operation,self.f.alpha,'save_treatment',dict(treatment_plan='one_type_i',pass1_surface_area='wings_only',pass1_start='2200',pass1_end='2205'),event=self.event)
        db.session.commit()
        self.assertEqual(self.event.status,'finished')
        self.assertEqual(observation.latest(self.f.operation,self.event).id,entry.id)
        classified = frost_analysis.classification('Frost')
        self.assertEqual(frost_model.training_label(dict(classification=classified)),1)
        self.assertIsNone(frost_model.training_label(dict(metadata,classification=classified)))
        self.assertIsNone(frost_model.training_label(dict(record_type='unlabeled_treatment_context',classification=classified)))
