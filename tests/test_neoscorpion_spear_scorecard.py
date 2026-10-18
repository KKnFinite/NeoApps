"""Evidence-first scorecard: exact identities, missingness, no operational writes."""
from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace as NS
from unittest import TestCase
from unittest.mock import patch

from app.extensions import db
from app.models import NeoScorpionFuelingEvent, NeoScorpionTopOffEvent
from app.models.neoscorpion_spear_observation import NeoScorpionSpearObservation as Observation
from app.services import neoscorpion_spear_scorecard as score
from tests import test_neoscorpion_operational_timeline as fixture
from tests.neoscorpion_handoff_fixture import submit_and_verify


def evidence_event():
    at = datetime(2026,10,17,23,55)
    return NS(id=9, fuel_assignment_id=1, fuel_work_state_id=7, cycle_number=1,
        tail_number='N168UP', fuel_truck_id=2, fueler_user_id=3, event_type='fuel',
        required_fuel_lbs=30000, transfer_fuel_gallons=1000, started_at_utc=at,
        ended_at_utc=at+timedelta(minutes=20), performance_snapshot=dict(version=3,
            fueler_id=3, truck_id=2, on_source='manual', pump_rate='100', setup_allowance='0',
            fueler_assigned_at=at.isoformat(), truck_assigned_at=at.isoformat(), exclusions=[],
            forecasts=[dict(observed_at=at.isoformat(), likely=(at+timedelta(minutes=15)).isoformat(),
                            source='BASELINE')]))


def decision(event):
    captured=event.started_at_utc-timedelta(minutes=2)
    rec=dict(action_type='assign',truck_id=event.fuel_truck_id,fueler_id=event.fueler_user_id,
             risk='AT RISK',projected_complete_at_utc=(event.started_at_utc+timedelta(minutes=15)).isoformat())
    original=dict(gateway_id=1,algorithm_version='spear-v4',generated_at=captured.isoformat(),alternatives=[],
                  recommendation=rec,scope=dict(mission_id=1,tail=event.tail_number,required=event.required_fuel_lbs,
                      departure=(event.started_at_utc+timedelta(minutes=18)).isoformat()))
    record=dict(id='a'*32,gateway_id=1,operation_id=1,mission_id=1,assignment_id=event.fuel_assignment_id,
        cycle_number=event.cycle_number,captured_at=captured.isoformat(),schema_version='spear-decision/v1',
        record_type='decision',training_eligible=False,payload=dict(action='initial_assignment',actor_id=3,
            before={},selected=dict(truck_id=event.fuel_truck_id,fueler_id=event.fueler_user_id),
            work_id=event.fuel_work_state_id,tail=event.tail_number,original_spear=original,
            reference_state='current',comparison='matched'))
    return record


class ScorecardMathTest(TestCase):
    def test_earliest_prediction_once_signed_error_cross_midnight(self):
        e=evidence_event()
        e.performance_snapshot['forecasts'].append(dict(observed_at=(e.started_at_utc+timedelta(minutes=5)).isoformat(),
            likely=e.ended_at_utc.isoformat(),source='QUALIFIED SERVICE HISTORY'))
        r=score.off_result(e)
        self.assertEqual(r['error'],5); self.assertEqual(r['revisions'],2)
        self.assertEqual(score.summary([r])['verified'],1)

    def test_missing_or_post_off_prediction_not_poor_performance(self):
        e=evidence_event();e.performance_snapshot['forecasts'][0]['observed_at']=e.ended_at_utc.isoformat()
        r=score.off_result(e);self.assertIsNone(r['error'])
        self.assertEqual(score.summary([r]),dict(total=1,verified=0,unavailable=1,bias=None,mae=None,within5=None))

    def test_untrusted_interrupted_and_resource_mismatch_excluded(self):
        for mutation in (dict(on_source='unverified'),dict(exclusions=['interrupted']),dict(truck_id=99)):
            e=evidence_event();e.performance_snapshot.update(mutation)
            self.assertIsNone(score.off_result(e)['error'])
        self.assertIsNone(score.off_result(evidence_event(),identity_reasons=['tail mismatch'])['error'])

    def test_timezone_equivalent_prediction_and_conservative_error(self):
        e=evidence_event();e.performance_snapshot['forecasts'][0]['observed_at']='2026-10-17T18:55:00-05:00'
        e.performance_snapshot['forecasts'][0]['likely']='2026-10-17T19:20:00-05:00'
        self.assertEqual(score.off_result(e)['error'],-5)

    def test_top_off_frozen_roundtrip_and_missing_return(self):
        at=datetime(2026,10,17,23,55)
        sent=NS(id=1,occurred_at_utc=at,fuel_truck_id=2,sort_date_operation_id=1,
            spear_predicted_return_at_utc=at+timedelta(minutes=30),spear_roundtrip_policy={'minutes':30},inventory_verified=True)
        returned=NS(sent_event_id=1,occurred_at_utc=at+timedelta(minutes=40),fuel_truck_id=2,
            sort_date_operation_id=1,inventory_verified=True)
        r=score.top_off_result(sent,returned)
        self.assertEqual((r['predicted_minutes'],r['actual_minutes'],r['error']),(30,40,10))
        self.assertIsNone(score.top_off_result(sent,None)['error'])
        returned.fuel_truck_id=99;self.assertIsNone(score.top_off_result(sent,returned)['error'])

    def test_decision_exact_outcome_and_risk_confusion(self):
        e=evidence_event();r=decision(e)
        outcomes=[dict(payload=dict(decision_id=r['id'],qualified=True,exclusions=[],event_ids=[e.id]))]
        d=score.decision_result(r,outcomes,{e.id:e})
        self.assertEqual((d['predicted_risk'],d['actual_risk']),('AT RISK','LATE'))
        self.assertEqual(score.risk_summary([d],'AT RISK')['fp'],1)
        self.assertEqual(score.risk_summary([d],'LATE')['fn'],1)
        self.assertEqual(score.risk_summary([],'LATE')['precision'],None)

    def test_different_resource_is_not_counterfactual_failure(self):
        e=evidence_event();r=decision(e);r['payload']['selected']['truck_id']=99
        d=score.decision_result(r,[],{e.id:e})
        self.assertEqual(d['comparison'],'different');self.assertIsNone(d['actual_risk'])

    def test_stale_ambiguous_mismatched_and_changed_target_risk_excluded(self):
        e=evidence_event()
        for change in ('stale','tail','requirement','timing','ambiguous'):
            r=decision(e);outcome=dict(decision_id=r['id'],qualified=True,exclusions=[],event_ids=[e.id])
            if change=='stale':r['payload']['reference_state']='stale'
            if change=='tail':r['payload']['tail']='OTHER'
            if change=='requirement':r['payload']['original_spear']['scope']['required']=40000
            if change=='timing':r['captured_at']=e.ended_at_utc.isoformat()
            if change=='ambiguous':outcome['event_ids'].append(10)
            self.assertIsNone(score.decision_result(r,[dict(payload=outcome)],{e.id:e})['actual_risk'],change)

    def test_latest_unqualified_outcome_does_not_reuse_earlier_success(self):
        e=evidence_event();r=decision(e)
        old=dict(captured_at='2026-10-18T01:00:00',payload=dict(decision_id=r['id'],qualified=True,exclusions=[],event_ids=[e.id]))
        new=dict(captured_at='2026-10-18T01:05:00',payload=dict(decision_id=r['id'],qualified=False,exclusions=['corrected'],event_ids=[e.id]))
        self.assertIsNone(score.decision_result(r,[old,new],{e.id:e})['actual_risk'])

    def test_malformed_snapshot_not_a_score_or_server_failure(self):
        e=evidence_event();e.performance_snapshot={'version':3,'exclusions':None,'forecasts':None}
        self.assertIsNone(score.off_result(e)['error'])

    def test_trends_require_six_verified_outcomes_and_exclude_missing(self):
        rows=[dict(error=value,at=datetime(2026,10,18)+timedelta(minutes=i))
              for i,value in enumerate((10,10,10,2,2,2,None))]
        self.assertIsNone(score.trend_summary(rows[:5]))
        trend=score.trend_summary(list(reversed(rows)))
        self.assertEqual(trend['earlier']['mae'],10)
        self.assertEqual(trend['recent']['mae'],2)
        self.assertEqual(trend['recent']['verified'],3)

    def test_same_timestamp_conflicting_forecasts_are_not_guessed(self):
        e=evidence_event();other=dict(e.performance_snapshot['forecasts'][0],likely=e.ended_at_utc.isoformat())
        e.performance_snapshot['forecasts'].append(other)
        result=score.off_result(e)
        self.assertIsNone(result['error']);self.assertIn('Conflicting predictions',result['reasons'][0])


class ScorecardIntegrationTest(TestCase):
    setUp=fixture.OperationalTimelineTest.setUp
    prepare=fixture.OperationalTimelineTest.prepare
    save=fixture.OperationalTimelineTest.save
    off=fixture.OperationalTimelineTest.off
    final_data=fixture.OperationalTimelineTest.final_data
    login=fixture.OperationalTimelineTest.login

    def completed(self):
        self.prepare();self.save(self.start+timedelta(minutes=10),**self.final_data());self.off()
        submit_and_verify(self.gateway,self.dispatcher,self.assignment.id);db.session.commit()
        return NeoScorpionFuelingEvent.query.one()

    def report(self,**kw):return score.report_context(self.gateway,self.operation,**kw)

    def test_real_outcome_read_only_and_filters(self):
        e=self.completed();before=self.selection.current_gallons
        report=self.report();self.assertEqual(len(report['off']),1)
        self.assertEqual(report['off'][0]['event'].fuel_work_state_id,self.work.id)
        self.assertFalse(db.session.dirty);self.assertFalse(db.session.new)
        self.assertEqual(self.report(truck_id=self.truck.id,fueler_id=self.fueler.id)['off_summary']['total'],1)
        self.assertEqual(self.selection.current_gallons,before)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)
        with self.assertRaises(ValueError):self.report(truck_id=99999)

    def test_r2_archive_deduplication_and_scope(self):
        self.completed()
        from app.services.neoscorpion_spear_learning_capture import build_completed_learning_record
        archive=build_completed_learning_record(self.gateway,self.operation,self.assignment,self.mission)
        self.assertEqual(self.report(archive=archive)['off_summary']['total'],1)
        self.assertIn('0 additional',self.report(archive=archive)['archive_note'])
        archive['gateway']['id']=999
        with self.assertRaisesRegex(ValueError,'gateway'):self.report(archive=archive)

    def test_archive_only_unreliable_not_scored(self):
        self.completed()
        from app.services.neoscorpion_spear_learning_capture import build_completed_learning_record
        archive=deepcopy(build_completed_learning_record(self.gateway,self.operation,self.assignment,self.mission))
        archive['fuel_events'][0]['id']=999
        archive['operational_timelines'][0]['event_id']=999
        archive['training_eligible']=False
        report=self.report(archive=archive)
        self.assertEqual(report['off_summary']['total'],2)
        self.assertIsNone(next(r for r in report['off'] if r['event'].id==999)['error'])

    def test_route_permission_selection_no_post_and_no_delivery_worker(self):
        self.completed();self.login(self.dispatcher)
        url=f'/neoscorpion/spear/scorecard?operation_id={self.operation.id}'
        with patch('app.services.neoscorpion_spear_decisions.schedule_delivery') as worker:
            response=self.client.get(url)
            self.assertEqual(response.status_code,200,response.get_data(as_text=True))
            worker.assert_not_called()
        self.assertIn('SPEAR Intelligence Scorecard',response.get_data(as_text=True))
        self.assertEqual(self.client.post(url).status_code,405)
        self.assertEqual(self.client.get(url.replace(str(self.operation.id),'99999')).status_code,404)
        self.assertEqual(self.client.get(url+'&truck_id=oops').status_code,400)
        with patch('app.neonodes.neoscorpion.routes.user_can',return_value=False):
            self.assertEqual(self.client.get(url).status_code,403)
        from tests.test_neoscorpion_vendor_workflow import VendorWorkflowTest
        user,_account,_token=VendorWorkflowTest.vendor(self)
        self.client.get('/logout')
        self.client.post('/login',data={'email':user.email,'password':'VendorPassword123!'})
        self.assertEqual(self.client.get(url).status_code,403)

    def test_duplicate_decisions_do_not_multiply_risk_sample(self):
        e=self.completed()
        # Supply an independently qualified snapshot for the scorecard contract;
        # the actual visit, receipts and inventory remain the real workflow output.
        e.performance_snapshot=dict(evidence_event().performance_snapshot,fueler_id=e.fueler_user_id,
            truck_id=e.fuel_truck_id,fueler_assigned_at=self.start.isoformat(),truck_assigned_at=self.start.isoformat())
        for index in range(2):
            rec=decision(e);rec['id']=str(index)*32;rec['gateway_id']=self.gateway.id
            rec['operation_id']=self.operation.id;rec['mission_id']=self.mission.id
            rec['payload']['original_spear']['gateway_id']=self.gateway.id
            rec['payload']['original_spear']['scope']['mission_id']=self.mission.id
            db.session.add(Observation(id=rec['id'],event_key=rec['id']*2,kind='decision',gateway_id=self.gateway.id,
                operation_id=self.operation.id,mission_id=self.mission.id,assignment_id=self.assignment.id,
                cycle_number=e.cycle_number,payload=rec))
            out=dict(rec,id=chr(98+index)*32,record_type='outcome',payload=dict(decision_id=rec['id'],
                event_ids=[e.id],qualified=True,exclusions=[],comparison_conclusion='observed_only'))
            db.session.add(Observation(id=out['id'],event_key=out['id']*2,kind='outcome',gateway_id=self.gateway.id,
                operation_id=self.operation.id,mission_id=self.mission.id,assignment_id=self.assignment.id,
                cycle_number=e.cycle_number,payload=out))
        db.session.commit()
        report=self.report()
        self.assertEqual(report['risks'][0]['n'],1)
        self.assertEqual(sum(d['actual_risk'] is not None for d in report['decisions']),1)
