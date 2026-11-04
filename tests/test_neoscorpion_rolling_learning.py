"""Deterministic rolling learning; synthetic evidence, no providers or live writes."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from dataclasses import replace
from types import SimpleNamespace as NS
import copy
import unittest
from unittest.mock import patch
from flask import Flask
from sqlalchemy import event as sql_event

from app.extensions import db
from app.models import NeoScorpionFuelingEvent
from app.services import neoscorpion as fuel
from app.services.neoscorpion_prof import event_math, load_observation_jobs
from app.services.neoscorpion_spear_calibration import (learning_window,recency_weight,learning_reasons,
    robust_weighted_samples,build_live_calibration,calibrated_planning_settings,reset_live_calibration)
from app.services.neoscorpion_spear_forecasts import physical_off_forecast
from app.services.neoscorpion_spear_optimizer import top_off_policy
from app.services.neoscorpion_spear import SpearSettings,build_spear_plan
from tests import test_neoscorpion_spear as scenarios
from tests import test_dispatch_refresh_fragments as fixtures

NOW=datetime(2026,11,4,18)


def job(identifier,days=0,ratio=Decimal('1.5'),**changes):
    start=NOW-timedelta(days=days,hours=6)
    end=start+timedelta(minutes=float(Decimal(10)*Decimal(str(ratio))))
    facts=dict(version=4,aircraft_type='B757',learning_origin='production',on_source='manual',
        fueler_id=1,truck_id=2,pump_rate='100',setup_allowance='0',exclusions=[],
        fueler_assigned_at=(start-timedelta(minutes=10)).isoformat(),
        truck_assigned_at=(start-timedelta(minutes=10)).isoformat(),
        completed_at=(end+timedelta(minutes=6)).isoformat(),
        actuals_received_at=(end+timedelta(minutes=5)).isoformat(),
        fueler_work_end_at=(end+timedelta(minutes=5)).isoformat(),
        prof_participation=dict(version=1,verified=True,fueler_ids=[1],truck_ids=[2]),
        resource_timing=dict(work_id=identifier,on_at=start.isoformat(),fueler_reasons=[],
            fueler_available_preparation_at=(start-timedelta(minutes=10)).isoformat(),
            preparation_ready_at=(start-timedelta(minutes=2)).isoformat()))
    facts.update(changes)
    e=NS(id=identifier,fuel_work_state_id=identifier,fuel_assignment_id=identifier,cycle_number=1,
        fueler_user_id=1,fuel_truck_id=2,started_at_utc=start,ended_at_utc=end,
        transfer_fuel_gallons=1000,event_type='fuel',tail_number='N100UP',performance_snapshot=facts)
    return dict(event=e,**event_math(e))


def current_row():
    return dict(assignment=NS(id=900,current_cycle_number=1,assigned_fueler_user_id=1,
        assigned_truck_id=2,transfer_fuel_gallons=1000),fuel_work_state=NS(id=900,off_at_utc=None,
        ended_early_at_utc=None,performance_context=dict(version=3,pump_rate='100',setup_allowance='0')),
        detailed_aircraft_type='B757',fueling_effective_on_utc=NOW-timedelta(minutes=2))


class RollingLearningMathTest(unittest.TestCase):
    def test_calendar_cutoff_includes_thirty_dates_across_dst(self):
        app=Flask(__name__);app.config['DEFAULT_GATEWAY_TIMEZONE']='America/Chicago'
        with app.app_context():
            cutoff,asof=learning_window(NOW.replace(tzinfo=timezone.utc))
            self.assertEqual(cutoff,datetime(2026,10,6,5))
            self.assertEqual(asof,NOW)
            self.assertEqual(recency_weight(NOW-timedelta(days=7),NOW),Decimal('0.5'))

    def test_window_and_future_samples_are_excluded(self):
        self.assertEqual(learning_reasons(job(1,days=29),NOW),())
        self.assertTrue(learning_reasons(job(1,days=30),NOW))
        self.assertTrue(learning_reasons(job(1,days=-1),NOW))

    def test_unmarked_legacy_origin_still_requires_and_accepts_canonical_proof(self):
        self.assertEqual(learning_reasons(job(1,learning_origin=None),NOW),())
        self.assertTrue(learning_reasons(job(1,learning_origin=None,completed_at=None),NOW))

    def test_recent_evidence_has_more_weight_and_extreme_outlier_is_rejected(self):
        samples=[(Decimal(1),NOW-timedelta(days=28),1),
                 (Decimal('1.5'),NOW-timedelta(days=1),1),(Decimal('1.5'),NOW,1)]
        kept,rejected,mean=robust_weighted_samples(samples,NOW)
        self.assertGreater(mean,Decimal('1.4'));self.assertEqual(len(kept),3)
        kept,rejected,mean=robust_weighted_samples(samples+[(Decimal(20),NOW,1)],NOW)
        self.assertEqual(len(rejected),1);self.assertLess(mean,Decimal('1.5'))

    def test_test_incomplete_untrusted_and_multi_fueler_samples_are_excluded(self):
        variants=[dict(learning_origin='test'),dict(synthetic=True),dict(completed_at=None),
            dict(on_source='unknown'),dict(training_eligible=False),dict(pump_rate='NaN'),
            dict(prof_participation=dict(version=1,verified=False,fueler_ids=[1],truck_ids=[2])),
            dict(prof_participation=dict(version=1,verified=True,fueler_ids=[1,3],truck_ids=[2]))]
        for changes in variants:
            with self.subTest(changes=changes):self.assertTrue(learning_reasons(job(1,**changes),NOW))

    def test_observe_apply_minimum_outliers_and_duplicate_visit(self):
        planning=NS(pump_rate_for=lambda _:100,setup_minutes=0)
        history=[job(i) for i in range(3)]
        observed=physical_off_forecast(current_row(),planning,jobs=history,mode='observe',now_utc=NOW)
        applied=physical_off_forecast(current_row(),planning,jobs=history,mode='apply',now_utc=NOW)
        self.assertEqual(observed['likely'],observed['goal'])
        self.assertGreater(applied['likely'],applied['goal']);self.assertEqual(applied['samples'],3)
        for invalid in (history[:2],history[:2]+[job(99,ratio=20)],history[:2]+[history[0]]):
            result=physical_off_forecast(current_row(),planning,jobs=invalid,mode='apply',now_utc=NOW)
            self.assertEqual(result['likely'],result['goal']);self.assertIn('Fewer than 3',result['unavailable_reason'])

    def test_old_qualified_data_has_less_influence_without_deleting_samples(self):
        planning=NS(pump_rate_for=lambda _:100,setup_minutes=0)
        recent=physical_off_forecast(current_row(),planning,jobs=[job(i) for i in range(3)],mode='apply',now_utc=NOW)
        old=physical_off_forecast(current_row(),planning,jobs=[job(i,days=28) for i in range(3)],mode='apply',now_utc=NOW)
        self.assertGreater(recent['likely'],old['likely']);self.assertEqual(old['samples'],3)

    def test_top_off_window_recency_observe_apply_and_independent_enable(self):
        pairs=[]
        for i in range(12):
            at=NOW-timedelta(days=i%3,hours=4)
            sent=NS(id=i,kind='sent',fuel_truck_id=2,sort_date_operation_id=i%2,occurred_at_utc=at,
                actor_user_id=1,spear_roundtrip_policy={'learning_origin':'production'})
            returned=NS(kind='returned',sent_event_id=i,fuel_truck_id=2,sort_date_operation_id=i%2,
                occurred_at_utc=at+timedelta(minutes=60),actor_user_id=1,inventory_verified=True,gallons=4000)
            pairs.append((sent,returned))
        settings=SpearSettings(use_learned_top_off=True,live_calibration_mode='apply')
        result=top_off_policy(pairs,2,settings,now_utc=NOW)
        self.assertEqual(result['minutes'],60);self.assertEqual(result['sample_count'],12)
        for mode,enabled in (('observe',True),('apply',False)):
            result=top_off_policy(pairs,2,replace(settings,live_calibration_mode=mode,use_learned_top_off=enabled),now_utc=NOW)
            self.assertEqual(result['minutes'],40);self.assertEqual(result['candidate_minutes'],60)
        mixed=[]
        for i in range(36):
            sent,returned=copy.deepcopy(pairs[i%12]);sent.id=100+i;returned.sent_event_id=sent.id
            sent.occurred_at_utc=NOW-timedelta(days=29 if i<12 else 0,hours=4)
            returned.occurred_at_utc=sent.occurred_at_utc+timedelta(minutes=90 if i<12 else 30)
            mixed.append((sent,returned))
        self.assertEqual(top_off_policy(mixed,2,settings,now_utc=NOW)['minutes'],30)
        for sent,returned in pairs:
            sent.occurred_at_utc-=timedelta(days=31);returned.occurred_at_utc-=timedelta(days=31)
        self.assertEqual(top_off_policy(pairs,2,settings,now_utc=NOW)['sample_count'],0)


class RollingLearningDatabaseTest(unittest.TestCase):
    def setUp(self):
        self.case=fixtures.DispatchFragmentsTest();self.case.setUp();self.addCleanup(self.case.doCleanups)

    def test_real_completed_test_visit_is_excluded_without_history_writes(self):
        c=self.case;c.ready()
        fuel.complete_fueled_assignment(c.gateway,c.dispatcher,c.assignment.id);db.session.commit()
        jobs=load_observation_jobs(c.gateway.id,learning=True)
        self.assertEqual(len(jobs),1)
        self.assertIn('Test/simulated evidence',learning_reasons(jobs[0]))
        before=NeoScorpionFuelingEvent.query.count();statements=[]
        _ = c.operation.gateway_id # Resolve the expired ORM identity before measuring the history query.
        def record(conn,cur,sql,*args):statements.append(sql.lstrip().split()[0].upper())
        sql_event.listen(db.engine,'before_cursor_execute',record)
        try:
            result=build_live_calibration(c.operation,scenarios._PlanningSettings(),[dict(detailed_aircraft_type='B757')])
        finally:sql_event.remove(db.engine,'before_cursor_execute',record)
        self.assertFalse(result[('service_ratio','B757')].active)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),before)
        self.assertFalse({'INSERT','UPDATE','DELETE'}.intersection(statements))
        self.assertLessEqual(statements.count('SELECT'),4)

    def test_canonical_completed_cycles_produce_rolling_candidates(self):
        c=self.case;original=fuel.policy_snapshot
        # This fixture represents production provenance in an isolated database;
        # all actual lifecycle, permission, inventory and event guards execute.
        def production_fixture(*args,**kwargs):
            facts=original(*args,**kwargs);facts.pop('learning_origin',None)
            return facts # Legacy tag absent; canonical physical/completion proof is still required.
        with patch.object(fuel,'policy_snapshot',side_effect=production_fixture):
            c.ready();fuel.complete_fueled_assignment(c.gateway,c.dispatcher,c.assignment.id);db.session.commit()
        asof=datetime.utcnow()
        jobs=load_observation_jobs(c.gateway.id,learning=True,now_utc=asof)
        self.assertEqual(len(jobs),1)
        self.assertTrue(all(not learning_reasons(j,asof) for j in jobs),[learning_reasons(j,asof) for j in jobs])
        result=build_live_calibration(c.operation,scenarios._PlanningSettings(),[dict(detailed_aircraft_type='B757')],jobs=jobs,now_utc=asof)
        self.assertEqual(result[('service_ratio','B757')].samples,1)
        self.assertEqual(result[('service_ratio','B757')].observations[0].mission_id,c.mission.id)
        self.assertFalse(result[('service_ratio','B757')].active)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)

    def test_shared_history_apply_wrap_service_reset_and_automation_preserved(self):
        c=self.case;history=[job(i) for i in range(1,5)]
        rows=[dict(detailed_aircraft_type='B757')]
        baseline=scenarios._PlanningSettings()
        calibrated=build_live_calibration(c.operation,baseline,rows,jobs=history,now_utc=NOW)
        self.assertTrue(calibrated[('service_ratio','B757')].active)
        self.assertTrue(calibrated[('actual_wrapup_minutes','B757')].active)
        self.assertEqual(calibrated[('preparation_minutes','B757')].samples,4)
        self.assertFalse(calibrated[('preparation_minutes','B757')].active)
        self.assertIn('Independent measured travel',calibrated[('truck_travel_minutes','B757')].explanation)
        observe=calibrated_planning_settings(baseline,calibrated,mode='observe')
        apply=calibrated_planning_settings(baseline,calibrated,mode='apply')
        self.assertIs(observe,baseline);self.assertGreater(apply.service_factor_for('B757'),1)
        row=scenarios._row();row['mission'].planned_datetime_utc=NOW+timedelta(hours=2)
        row['arrival_mission'].actual_block_in_datetime_utc=NOW;row['arrival_mission'].eta_datetime_utc=NOW
        plan=build_spear_plan([row],operation=NS(id=1),planning_settings=baseline,
            spear_settings=SpearSettings(live_calibration_mode='apply'),nightly_fuelers=[scenarios._fueler()],
            nightly_trucks=[scenarios._truck()],now_utc=NOW,calibrations=calibrated)
        self.assertFalse(SpearSettings().automation_enabled)
        self.assertTrue(plan.steps);self.assertIsNone(baseline.__dict__.get('service_factor_for'))
        observed_plan=build_spear_plan([row],operation=NS(id=1),planning_settings=baseline,
            spear_settings=SpearSettings(live_calibration_mode='observe'),nightly_fuelers=[scenarios._fueler()],
            nightly_trucks=[scenarios._truck()],now_utc=NOW,calibrations=calibrated)
        self.assertGreater(plan.steps[0].projected_complete_at_utc,observed_plan.steps[0].projected_complete_at_utc)
        self.assertEqual((plan.steps[0].fueler_id,plan.steps[0].truck_id),
                         (observed_plan.steps[0].fueler_id,observed_plan.steps[0].truck_id))
        self.assertFalse(c.settings.spear_automation_enabled)
        self.assertTrue(c.settings.spear_recommendations_enabled)
        self.assertEqual(build_live_calibration(c.operation,baseline,rows,jobs=history,now_utc=NOW+timedelta(minutes=20)),calibrated)
        reset_live_calibration(c.operation,'service_ratio','B757',c.dispatcher,now_utc=NOW)
        db.session.commit()
        reset=build_live_calibration(c.operation,baseline,rows,jobs=history,now_utc=NOW)
        self.assertEqual(reset[('service_ratio','B757')].samples,0)
        self.assertEqual(reset[('service_ratio','B757')].effective,1)
