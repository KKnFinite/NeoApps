"""Mapped input revisions, rollout gates, failure isolation and immutable plans."""
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
from app.extensions import db
from app.models import NeoScorpionFuelTruck,NeoScorpionSettings
from app.models.neoscorpion_spear_plan import NeoScorpionSpearPlanSnapshot as Snapshot
from app.services import neoscorpion_spear_replanning as live
from app.services.neoscorpion_spear import SpearSettings,save_spear_settings,effective_spear_settings
from app.services.neoscorpion_spear_optimizer import validate_activation,VERSION
from tests import test_neoscorpion_spear_learning_capture as fixtures
from tests import test_neoscorpion_spear as base


class ReplanningTest(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.SpearLearningCaptureTest();self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        live._cache.clear();live._history.clear()
        f=self.fixture
        f.mission.planned_datetime_utc=base.NOW+timedelta(hours=2)
        db.session.commit()
        r=base._row(f.mission.id);r['mission']=f.mission
        truck=db.session.get(NeoScorpionFuelTruck,f.assignment.assigned_truck_id)
        selection=NS(status='available',current_gallons=9000,inventory_verified=True,inventory_epoch=1,top_off_sent_at_utc=None)
        self.context=dict(operation=f.operation,rows=[r],nightly_trucks=[dict(truck=truck,selection=selection)],
            nightly_assignment_fuelers=[f.user],spear_settings=SpearSettings(),truck_visuals=[])
        self.baseline=base._plan([r],trucks=self.context['nightly_trucks'],fuelers=[f.user])

    def enrich(self,now=base.NOW):
        return live.enrich(self.context,self.baseline,base._PlanningSettings(),{},now=now)

    def test_shadow_keeps_one_current_source_active_uses_new_planner(self):
        plan=self.enrich()
        self.assertEqual(plan.steps,self.baseline.steps)
        self.assertEqual(self.context['spear_whole_sort']['mode'],'shadow')
        self.context['spear_settings']=replace(self.context['spear_settings'],whole_sort_mode='active')
        plan=self.enrich()
        self.assertEqual(plan.algorithm_version,VERSION)
        self.assertFalse(self.context['spear_whole_sort'].get('fallback',False))

    def test_cache_avoids_requeries_until_revision_or_periodic_reconciliation(self):
        with patch.object(live,'load_top_off_history',wraps=live.load_top_off_history) as history:
            p=self.enrich();q=self.enrich(base.NOW+timedelta(seconds=20))
            self.assertEqual(p.token,q.token);self.assertEqual(history.call_count,1)
            self.enrich(base.NOW+timedelta(minutes=3));self.assertEqual(history.call_count,2)
            self.fixture.mission.eta_datetime_utc=base.NOW+timedelta(minutes=10)
            r=self.enrich();self.assertEqual(history.call_count,3);self.assertNotEqual(p.token,r.token)

    def test_material_external_changes_invalidate_without_fueling_revision(self):
        fingerprint=live.inputs(self.context,{})
        for field,value in (('assigned_tail_number','N999UP'),('planned_fuel_load',70000),('arrival_status','arrived')):
            with self.subTest(field=field):
                old=getattr(self.fixture.mission,field)
                setattr(self.fixture.mission,field,value)
                self.assertNotEqual(fingerprint,live.inputs(self.context,{}))
                setattr(self.fixture.mission,field,old)
        for key,value in (('parking_position','E03'),('planning_demand_gallons',1000),('is_off',True)):
            before=live.inputs(self.context,{})
            self.context['rows'][0][key]=value
            self.assertNotEqual(before,live.inputs(self.context,{}))
        before=live.inputs(self.context,{})
        self.context['nightly_trucks'][0]['selection'].inventory_epoch+=1
        self.assertNotEqual(before,live.inputs(self.context,{}))

    def test_completed_work_calibration_tuple_keys_are_stable_and_replan(self):
        from decimal import Decimal
        from app.services.neoscorpion_spear_calibration import LiveCalibration
        calibration=LiveCalibration(metric='pump_rate',scope_key='B757',configured=Decimal(100),
            observed=Decimal(140),effective=Decimal(120),samples=3,excluded_samples=0,
            first_observation_utc=None,most_recent_observation_utc=None,observations=(),excluded_observations=())
        data={('pump_rate','B757'):calibration}
        fingerprint=live.inputs(self.context,data)
        self.assertEqual(fingerprint,live.inputs(self.context,dict(reversed(list(data.items())))))
        plan=live.enrich(self.context,self.baseline,base._PlanningSettings(),data,now=base.NOW)
        self.assertFalse(self.context['spear_whole_sort'].get('fallback',False))
        self.assertEqual(plan.steps,self.baseline.steps)
        self.assertNotEqual(fingerprint,live.inputs(self.context,{('pump_rate','B757'):replace(calibration,samples=4)}))

    def test_fingerprint_failure_retains_existing_spear_not_unavailable(self):
        with patch.object(live,'inputs',side_effect=ValueError('unexpected optional data')):
            plan=self.enrich()
        self.assertEqual(plan,self.baseline)
        self.assertTrue(self.context['spear_whole_sort']['fallback'])

    def test_failure_keeps_manual_and_existing_spear_without_canonical_writes(self):
        self.context['spear_settings']=replace(self.context['spear_settings'],whole_sort_mode='active')
        old=self.fixture.assignment.assigned_truck_id
        with patch.object(live,'optimize',side_effect=ValueError('bounded solver failure')):
            plan=self.enrich()
        self.assertEqual(plan.steps,self.baseline.steps)
        self.assertEqual(self.fixture.assignment.assigned_truck_id,old)
        self.assertTrue(self.context['spear_whole_sort']['fallback'])

    def test_versioned_snapshot_idempotent_and_immutable(self):
        values=dict(gateway_id=self.fixture.gateway.id,operation_id=self.fixture.operation.id,mode='shadow',
            fingerprint='a'*64,algorithm_version=VERSION,token='original-plan',payload={'schema_version':'spear-plan/v2'})
        live._write_snapshot(values);live._write_snapshot(values)
        self.assertEqual(Snapshot.query.count(),1)
        live._write_snapshot(dict(values,fingerprint='b'*64,token='revised-plan'))
        self.assertEqual(Snapshot.query.count(),2)
        saved=Snapshot.query.first();saved.token='changed'
        with self.assertRaisesRegex(ValueError,'immutable'):db.session.flush()
        db.session.rollback()

    def test_activation_rejected_during_sort_never_automatic(self):
        f=self.fixture
        with patch('app.services.neoscorpion.current_sort_operation',return_value=f.operation):
            with self.assertRaisesRegex(ValueError,'before sort assignments'):
                validate_activation(f.gateway,f.user)
        self.assertEqual(effective_spear_settings(NeoScorpionSettings.query.one()).whole_sort_mode,'shadow')

    def test_positive_duration_settings_backward_compatibility_and_controls_independent(self):
        f=self.fixture
        form=dict(recommendations_enabled='1',minimum_truck_reserve_gallons='500',do_not_top_off_above_percent='70',
            truck_minutes_per_ramp_move='2',fueler_begins_at='Remote',truck_begins_at='Remote',truck_after_top_off='Echo',
            incoming_early_staging_minutes='15',recalculation_interval_minutes='2',automation_stability_delay_seconds='5',
            whole_sort_settings_present='1',whole_sort_mode='shadow',top_off_roundtrip_minutes='55',use_learned_top_off='1')
        save_spear_settings(f.gateway,f.user,form)
        settings=effective_spear_settings(NeoScorpionSettings.query.one())
        self.assertEqual(settings.top_off_roundtrip_minutes,55);self.assertTrue(settings.use_learned_top_off)
        self.assertFalse(settings.learning_capture_enabled);self.assertEqual(settings.live_calibration_mode,'observe')
        self.assertEqual(settings.truck_after_top_off,'Remote')
        form['top_off_roundtrip_minutes']='0'
        with self.assertRaisesRegex(ValueError,'Roundtrip'):save_spear_settings(f.gateway,f.user,form)
