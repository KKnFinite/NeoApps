"""OUTBOUND quick lifecycle, canonical reset and retained evidence contracts."""
import copy
import unittest
from app.extensions import db
from app.models import NeoSubZeroAuditEntry as Audit, NeoSubZeroDepartureDeiceEvent as Event, NeoSubZeroSprayRecord as Spray, SortDateTailState, NeoSubZeroPretreatState, SortDateParkingAssignment
from app.services.live_collaboration import entity_version
from app.services.neosubzero_evidence import departure_event_for_tail
from app.services.neosubzero_operations import operations_context
from tests import test_neosubzero_operations as fixtures


class OutboundRedesignTest(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.OperationsTest()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.op = self.f.f.operation
        self.mission = self.f.f.alpha

    def post(self, action, event=None, **values):
        return self.f.post(action=action, event_id=event.id if event else '',
                           expected_version=entity_version(event), **values)

    def initiate(self):
        response = self.post('initial_contact')
        self.assertEqual(response.status_code,200,response.json)
        return departure_event_for_tail(self.op,self.mission)

    def row(self):
        return next(r for r in operations_context(self.f.f.gateway,self.op,'outbound')['rows']
                    if r['mission_id']==self.mission.id)

    def test_reset_initial_contact_retains_identity_pretreat_and_history(self):
        state=NeoSubZeroPretreatState(sort_date_operation_id=self.op.id,tail_number=self.mission.assigned_tail_number,pretreat_planned=True)
        db.session.add(state);db.session.commit()
        identity=(self.mission.id,self.mission.assigned_tail_number,self.mission.flight_number)
        parking=[(p.id,p.tail_number,p.position_code) for p in SortDateParkingAssignment.query.all()]
        event=self.initiate()
        self.assertEqual(self.post('reset',event).status_code,200)
        self.assertIsNotNone(event.reset_at_utc)
        self.assertIsNone(departure_event_for_tail(self.op,self.mission))
        self.assertIsNone(self.row()['event'])
        self.assertEqual(SortDateTailState.query.filter_by(tail_number=self.mission.assigned_tail_number).one().deice_status,'unknown')
        self.assertEqual(Event.query.count(),1)
        self.assertTrue(state.pretreat_planned)
        self.assertEqual(identity,(self.mission.id,self.mission.assigned_tail_number,self.mission.flight_number))
        self.assertEqual(parking,[(p.id,p.tail_number,p.position_code) for p in SortDateParkingAssignment.query.all()])
        audit=Audit.query.filter_by(action='departure_reset').one()
        self.assertEqual((audit.event_id,audit.actor_user_id),(event.id,self.f.user.id))
        self.assertIsNone(audit.before_values['reset_at_utc'])
        self.assertTrue(audit.after_values['reset_at_utc'])
        replacement=self.initiate()
        self.assertNotEqual(replacement.id,event.id)
        self.assertIsNone(replacement.reset_at_utc)

    def test_clear_configuration_and_reset_are_distinct_without_spray(self):
        event=self.initiate()
        self.assertEqual(self.post('toggle_configured',event).status_code,200)
        self.assertEqual(event.status,'configured')
        self.assertEqual(self.post('toggle_configured',event).status_code,200)
        self.assertEqual(event.status,'deice_planned')
        self.assertEqual(self.post('toggle_configured',event).status_code,200)
        self.assertEqual(self.post('reset',event).status_code,200)
        self.assertIsNone(event.configured_at_utc)
        self.assertIsNone(self.row()['event'])
        self.assertEqual(Spray.query.count(),0)
        replacement=self.initiate()
        self.assertEqual(self.post('toggle_configured',replacement).status_code,200)
        self.assertEqual(replacement.status,'configured')

    def test_reset_rejects_stale_foreign_or_duplicate_requests(self):
        event=self.initiate()
        old=entity_version(event)
        for values in ({'operation_id':-1},{'physical_tail':'FOREIGN'}):
            self.assertEqual(self.post('reset',event,**values).status_code,409)
        self.assertEqual(self.f.post(action='reset',event_id=event.id,expected_version='stale').status_code,409)
        self.assertEqual(self.post('reset',event).status_code,200)
        self.assertEqual(self.f.post(action='reset',event_id=event.id,expected_version=old).status_code,409)
        self.assertEqual(Audit.query.filter_by(action='departure_reset').count(),1)

    def test_actual_spray_fin_and_radio_evidence_cannot_be_reset(self):
        event=self.initiate()
        self.assertEqual(self.post('save_treatment',event,treatment_plan='one_type_i',pass1_surface_area='wings_only',pass1_start='22:10').status_code,200)
        before=copy.deepcopy(event.pass_evidence)
        self.assertEqual(self.post('reset',event).status_code,409)
        self.assertEqual(event.pass_evidence,before)
        self.assertEqual(self.post('save_treatment',event,treatment_plan='one_type_i',pass1_surface_area='wings_only',pass1_start='22:10',pass1_end='22:15').status_code,200)
        for action in ('reset','clear','reset','radio_complete','reset'):
            response=self.post(action,event)
            self.assertEqual(response.status_code,409 if action=='reset' else 200,response.json)
        self.assertIsNone(event.reset_at_utc)
        self.assertEqual(Audit.query.filter_by(action='departure_reset').count(),0)

    def test_final_required_pass_start_and_radio_group_are_independent(self):
        event=self.initiate()
        fields=dict(treatment_plan='type_i_type_iv',pass1_surface_area='wings_only',pass2_surface_area='wings_tail',pass1_start='22:10')
        self.assertEqual(self.post('save_treatment',event,**fields).status_code,200)
        self.assertEqual(self.row()['final_start'],'')
        self.assertEqual(self.row()['quick_clock'],('pass1_end','END PASS 1'))
        fields.update(pass1_end='22:15',pass2_start='22:16')
        self.assertEqual(self.post('save_treatment',event,**fields).status_code,200)
        self.assertEqual(self.row()['final_start'],'22:16')
        self.assertEqual(self.row()['quick_clock'],('pass2_end','END PASS 2'))
        fields['pass2_end']='22:20'
        self.assertEqual(self.post('save_treatment',event,**fields).status_code,200)
        self.assertEqual(self.row()['quick_actions'],(('clear','CLEAR'),))
        self.assertEqual(self.post('clear',event).status_code,200)
        row=self.row()
        self.assertEqual(row['visual_state'],'radio-pending')
        self.assertFalse(row['terminal'])
        self.assertIn('2216',row['script']['text'])
        self.assertEqual(row['quick_actions'],(('radio_complete','RADIO COMPLETE'),))
        self.mission.departure_status='departed';db.session.commit()
        self.assertEqual(self.row()['quick_actions'],(('radio_complete','RADIO COMPLETE'),))
        self.assertEqual(self.post('radio_complete',event).status_code,200)
        self.assertEqual(self.row()['visual_state'],'gone')
        self.assertEqual(self.row()['quick_actions'],())

    def test_reason_catalog_and_finalized_corrections_preserve_snapshots(self):
        from app.services.neosubzero_spray import departure_reason_options
        event=self.initiate()
        self.assertIn(('Freezing Rain','FZRA · Freezing Rain'),departure_reason_options())
        self.assertNotIn('Pretreat',[value for value,_ in departure_reason_options()])
        for value in ('arbitrary text','PRETREAT','not a reason'):
            result=self.f.post(target='spray-reason',expected_version=entity_version(event),reason_for_application=value)
            self.assertEqual(result.status_code,409,result.json)
            self.assertIsNone(event.reason_for_application)
        result=self.f.post(target='spray-reason',expected_version=entity_version(event),reason_for_application='FZRA')
        self.assertEqual(result.status_code,200,result.json)
        self.assertEqual(event.reason_for_application,'Freezing Rain')
        fields=dict(treatment_plan='one_type_i',pass1_surface_area='wings_only',pass1_start='22:10',pass1_end='22:15')
        self.assertEqual(self.post('save_treatment',event,**fields).status_code,200)
        original=copy.deepcopy(event.pass_evidence)
        self.assertEqual(self.f.post(target='spray-reason',expected_version=entity_version(event),reason_for_application='Frost').status_code,409)
        result=self.f.post(target='spray-reason',expected_version=entity_version(event),reason_for_application='Frost',correction_reason='Verified reason correction')
        self.assertEqual(result.status_code,200,result.json)
        self.assertEqual(event.pass_evidence,original)
        self.assertEqual(Audit.query.filter_by(action='application_reason').order_by(Audit.id.desc()).first().reason,'Verified reason correction')
