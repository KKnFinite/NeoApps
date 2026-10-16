"""Behavioral schedules, not source-string assertions or invented physical events."""
from datetime import timedelta
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
from tests import test_neoscorpion_spear as fixture
from app.services.neoscorpion_spear import SpearSettings
from app.services.neoscorpion_spear_optimizer import optimize,top_off_policy,VERSION

NOW=fixture.NOW


def row(identifier, *, etd=60, arrival=0, demand=500, ramp='A4', **values):
    r=fixture._row(identifier,demand=demand)
    r['mission'].planned_datetime_utc=NOW+timedelta(minutes=etd)
    r['mission'].timezone='America/Chicago'
    r['mission'].assigned_tail_number='N123UP'
    r['arrival_mission'].actual_block_in_datetime_utc=NOW+timedelta(minutes=arrival) if arrival<=0 else None
    r['arrival_mission'].eta_datetime_utc=NOW+timedelta(minutes=arrival)
    r['parking_position']=ramp
    r.update(values)
    return r


def plan(rows, *, trucks=None,fuelers=None,settings=None,**kwargs):
    return optimize(rows,operation=NS(id=1),planning_settings=fixture._PlanningSettings(),
        spear_settings=settings or SpearSettings(),nightly_fuelers=fuelers or [fixture._fueler()],
        nightly_trucks=trucks if trucks is not None else [fixture._truck()],now_utc=NOW,**kwargs)


def jobs(p):
    return {j['mission_id']:j for j in p.optimizer['schedules']}


def assignment(fueler=1,truck=10,**kw):
    return NS(assigned_fueler_user_id=fueler,assigned_truck_id=truck,operational_status='active',
        completed_at_utc=None,fuel_on_board_at_utc=None,current_cycle_number=1,**kw)


class WholeSortTest(unittest.TestCase):
    def test_strict_earliest_even_if_already_late(self):
        early=row(100,etd=-15,arrival=40)
        later=row(200,etd=20,demand=1500)
        p=plan([later,early],trucks=[fixture._truck(current=2000,capacity=3000)])
        protected=jobs(plan([early],trucks=[fixture._truck(current=2000,capacity=3000)]))[100]
        self.assertEqual(jobs(p)[100]['finish'],protected['finish'])
        self.assertEqual(p.risks_by_mission_id[100],'LATE')
        self.assertGreater(jobs(p)[200]['start'],jobs(p)[100]['finish'])

    def test_gap_filling_does_not_delay_inaccessible_earlier(self):
        p=plan([row(100,etd=90,arrival=50),row(200,etd=120)])
        self.assertLess(jobs(p)[200]['finish'],jobs(p)[100]['start'])
        self.assertEqual(jobs(p)[100]['start'],NOW+timedelta(minutes=55))

    def test_authoritative_etd_changes_priority_prediction_does_not(self):
        a,b=row(100),row(200,etd=90)
        a['predicted_departure']=NOW+timedelta(hours=5)
        self.assertLess(jobs(plan([a,b]))[100]['start'],jobs(plan([a,b]))[200]['start'])
        b['mission'].eta_datetime_utc=NOW+timedelta(minutes=30)
        self.assertLess(jobs(plan([a,b]))[200]['start'],jobs(plan([a,b]))[100]['start'])

    def test_whole_sort_keeps_equal_early_pair_for_later_commitment(self):
        a=row(100,etd=60)
        b=row(200,etd=70,assignment=assignment(1,10))
        p=plan([a,b],trucks=[fixture._truck(10),fixture._truck(20)],fuelers=[fixture._fueler(1),fixture._fueler(2)])
        self.assertEqual((jobs(p)[100]['truck_id'],jobs(p)[100]['fueler_id']),(20,2))
        self.assertEqual((jobs(p)[200]['truck_id'],jobs(p)[200]['fueler_id']),(10,1))
        self.assertNotIn(200,[s.mission_id for s in p.steps])

    def test_separate_resource_calendars_do_not_overlap(self):
        p=plan([row(i,etd=50+i) for i in range(10)],trucks=[fixture._truck(10,capacity=10000,current=9000),fixture._truck(20,capacity=10000,current=9000)])
        for name in ('truck_calendars','fueler_calendars'):
            for blocks in p.optimizer[name].values():
                for a,b in zip(blocks,blocks[1:]):self.assertLessEqual(a['end'],b['start'])
        self.assertGreater(jobs(p)[1]['start'],jobs(p)[0]['finish'])

    def test_top_off_before_earlier_preserves_reserve_and_future_gap(self):
        trucks=[fixture._truck(current=1000,capacity=3000)]
        a=row(100,etd=100,arrival=50,demand=2000)
        b=row(200,etd=130,demand=500)
        p=plan([a,b],trucks=trucks)
        self.assertTrue(any(b['reset'] for b in p.optimizer['truck_calendars']['10']))
        self.assertEqual(jobs(p)[100]['start'],NOW+timedelta(minutes=55))
        self.assertLess(jobs(p)[200]['finish'],jobs(p)[100]['start'])
        self.assertEqual(p.risks_by_mission_id[100],'AT RISK')
        self.assertEqual(trucks[0]['selection'].current_gallons,1000)

    def test_later_consumption_cannot_break_earlier_reserve(self):
        a=row(100,etd=90,arrival=20,demand=1500)
        b=row(200,etd=120,demand=1000)
        p=plan([a,b],trucks=[fixture._truck(current=2000,capacity=3000)])
        self.assertEqual(jobs(p)[100]['start'],NOW+timedelta(minutes=25))
        self.assertGreater(jobs(p)[200]['start'],jobs(p)[100]['finish'])

    def test_defuel_needs_empty_capacity(self):
        a=row(100,demand=-2000,cycle_type='defuel')
        self.assertNotIn(100,jobs(plan([a],trucks=[fixture._truck(current=2000,capacity=3000)])))
        self.assertIn(100,jobs(plan([a],trucks=[fixture._truck(current=500,capacity=3000)])))

    def test_top_off_prediction_fixed_and_overdue_never_available(self):
        truck=fixture._truck(status='topping_off',current=200,capacity=4000)
        truck['selection'].top_off_sent_at_utc=NOW-timedelta(minutes=10)
        p=plan([row(100,demand=2000)],trucks=[truck])
        self.assertEqual(p.optimizer['top_off_status']['10']['return_at'],NOW+timedelta(minutes=30))
        self.assertTrue(p.steps[0].requires_resource_release)
        self.assertFalse(p.steps[0].automatic_eligible)
        truck['selection'].top_off_sent_at_utc=NOW-timedelta(minutes=50)
        p=plan([row(100)],trucks=[truck])
        self.assertFalse(p.steps)
        self.assertTrue(p.optimizer['top_off_status']['10']['overdue'])
        self.assertEqual(p.optimizer['top_off_status']['10']['return_at'],NOW-timedelta(minutes=10))

    def test_sent_prediction_survives_changed_manual_setting_and_legacy_unknown(self):
        truck=fixture._truck(status='topping_off',capacity=4000)
        truck['selection'].top_off_sent_at_utc=NOW-timedelta(minutes=50)
        event=NS(kind='sent',fuel_truck_id=10,occurred_at_utc=truck['selection'].top_off_sent_at_utc,
            spear_predicted_return_at_utc=NOW-timedelta(minutes=10))
        p=plan([row(100)],trucks=[truck],settings=SpearSettings(top_off_roundtrip_minutes=90),top_events=[event])
        self.assertTrue(p.optimizer['top_off_status']['10']['overdue'])
        self.assertFalse(p.steps)
        event.spear_predicted_return_at_utc=None
        p=plan([row(100)],trucks=[truck],top_events=[event])
        self.assertIsNone(p.optimizer['top_off_status']['10']['return_at'])
        self.assertFalse(p.steps)

    def test_truckless_fob_is_not_converted_to_transfer_assignment(self):
        fob=row(100,assignment=assignment(truck=None),fob_likely=True)
        p=plan([fob,row(200)])
        self.assertFalse(p.steps)

    def test_returned_low_gallons_overrides_capacity_forecast(self):
        truck=fixture._truck(current=600,capacity=4000)
        p=plan([row(100,demand=2000)],trucks=[truck])
        self.assertEqual(p.steps[0].action_type,'top_off')
        self.assertFalse(p.steps[0].automatic_eligible)
        self.assertGreaterEqual(jobs(p)[100]['start'],NOW+timedelta(minutes=42))

    def test_physical_occupancy_and_overrun_no_fabricated_release(self):
        work=NS(id=1,on_at_utc=NOW-timedelta(minutes=10),off_at_utc=None,ended_early_at_utc=None,
            physical_truck_id=10,physical_fueler_user_id=1,performance_context={},tail_number='N123UP')
        active=row(100,assignment=assignment(),fuel_work_state=work,fueling_on_trusted=True,
            physical_off_forecast={'likely':NOW-timedelta(minutes=1)})
        p=plan([active,row(200)])
        self.assertNotIn(200,jobs(p))
        active['physical_off_forecast']['likely']=NOW+timedelta(minutes=10)
        p=plan([active,row(200)])
        self.assertTrue(p.steps[0].requires_resource_release)
        self.assertFalse(p.steps[0].automatic_eligible)

    def test_truck_off_does_not_release_unfinished_fueler_or_admin_complete(self):
        work=NS(id=1,on_at_utc=NOW-timedelta(minutes=20),off_at_utc=NOW-timedelta(minutes=1),
            ended_early_at_utc=None,physical_truck_id=10,physical_fueler_user_id=1,performance_context={},
            actuals_received_at_utc=None,tail_number='N123UP')
        active=row(100,assignment=assignment(),fuel_work_state=work,is_off=True,administratively_complete=True)
        p=plan([active,row(200)],fuelers=[fixture._fueler(1),fixture._fueler(2)])
        self.assertEqual(p.steps[0].fueler_id,2)
        self.assertEqual(p.steps[0].truck_id,10)

    def test_unready_committed_resources_not_borrowed(self):
        a=row(100,etd=120,assignment=assignment(),required_fuel_lbs=None)
        p=plan([row(200,etd=60),a])
        self.assertFalse(p.steps)

    def test_partial_preserved_oos_sump_unverified_excluded(self):
        a=row(100,assignment=assignment(fueler=2,truck=None))
        p=plan([a],fuelers=[fixture._fueler(1),fixture._fueler(2)])
        self.assertEqual(p.steps[0].fueler_id,2)
        for status in ('unavailable_oos','needs_sump'):
            self.assertFalse(plan([row(100)],trucks=[fixture._truck(status=status)]).steps)
        truck=fixture._truck();truck['selection'].inventory_verified=False
        self.assertFalse(plan([row(100)],trucks=[truck]).steps)

    def test_missing_inputs_and_midnight(self):
        a=row(100,demand=None)
        self.assertIn('UNKNOWN',plan([a]).unavailable_by_mission_id[100])
        a=row(100);a['arrival_mission']=None
        self.assertTrue(plan([a]).waiting_for_data_by_mission_id)
        a=row(100,etd=60*24,arrival=60*23)
        self.assertEqual(jobs(plan([a]))[100]['finish'].day,(NOW+timedelta(days=1)).day)

    def test_plan_token_stable_equivalent_but_relevant_fingerprint_invalidates(self):
        p=plan([row(100)],fingerprint='a')
        self.assertEqual(p.token,plan([row(100)],fingerprint='a').token)
        self.assertNotEqual(p.token,plan([row(100)],fingerprint='b').token)

    def test_aircraft_rates_and_observe_apply_use_existing_calibration_once(self):
        from app.services.neoscorpion_spear_calibration import LiveCalibration
        calibration=LiveCalibration(metric='pump_rate',scope_key='B757',configured=Decimal(100),
            observed=Decimal(200),effective=Decimal(150),samples=3,excluded_samples=0,
            first_observation_utc=None,most_recent_observation_utc=None,observations=(),excluded_observations=())
        observe=plan([row(100,demand=1500)],calibrations={('pump_rate','B757'):calibration})
        apply=plan([row(100,demand=1500)],settings=SpearSettings(live_calibration_mode='apply'),
            calibrations={('pump_rate','B757'):calibration})
        self.assertEqual(jobs(observe)[100]['finish']-jobs(apply)[100]['finish'],timedelta(minutes=5))
        self.assertEqual(apply.steps[0].explanation['live_calibration'][0]['using'],'150 gal/min')

    def test_prior_sort_unreleased_visit_and_history_bound_fail_safe(self):
        a=row(100)
        a['spear_physical_visits']=[dict(id=9,on=str(NOW-timedelta(hours=1)),off=None,
            fueler_release=None,truck_id=10,fueler_id=1,parking='E03',finalized=False)]
        self.assertFalse(plan([a]).steps)
        a['spear_visits_truncated']=True
        with self.assertRaisesRegex(ValueError,'history bound'):plan([a])

    def test_bounded_performance_and_fail_closed(self):
        rows=[row(i,etd=60+i*4,demand=200) for i in range(40)]
        p=plan(rows,trucks=[fixture._truck(i,current=10000,capacity=12000) for i in (10,20,30)],
            fuelers=[fixture._fueler(i) for i in (1,2,3)])
        self.assertEqual(len(jobs(p)),40)
        self.assertLess(p.optimizer['elapsed_ms'],1000)
        with self.assertRaisesRegex(ValueError,'budget'):
            plan(rows,max_evaluations=1)

    def test_pending_unknown_review_tail_swap_and_grounded_forecast(self):
        a=row(100,assignment=assignment())
        pending=NS(id=7,tail_number='N123UP',parent_cycle_number=1,required_fuel_lbs=27000,
            pending_cycle_identity='pending-7',version=1,status='pending',proposed_truck_id=10,proposed_fueler_user_id=1)
        a['pending_uplift']=pending
        p=plan([a]);self.assertIsNone(p.optimizer['future_demands'][0]['demand_gallons'])
        a.update(actual_total_lbs=24000,actual_complete=True,fuel_density=Decimal(6))
        p=plan([a]);future=p.optimizer['future_demands'][0]
        self.assertEqual(future['demand_gallons'],'500')
        self.assertIn('projected_start',future)
        self.assertNotIn(-7,[s.mission_id for s in p.steps])
        pending.status='review';self.assertNotIn('projected_start',plan([a]).optimizer['future_demands'][0])
        pending.tail_number='N999UP';self.assertIsNone(plan([a]).optimizer['future_demands'][0]['demand_gallons'])


class TopOffLearningTest(unittest.TestCase):
    def pair(self,i,duration=40,truck=10,op=None):
        sent=NS(id=i,kind='sent',fuel_truck_id=truck,sort_date_operation_id=op if op is not None else i%2,
            occurred_at_utc=NOW,actor_user_id=1)
        ret=NS(kind='returned',sent_event_id=i,fuel_truck_id=truck,sort_date_operation_id=sent.sort_date_operation_id,
            occurred_at_utc=NOW+timedelta(minutes=duration),inventory_verified=True,gallons=3000,actor_user_id=1)
        return sent,ret

    def test_default_manual_and_p80_not_average(self):
        pairs=[self.pair(i,duration=20+i*5) for i in range(10)]
        self.assertEqual(top_off_policy(pairs,10,SpearSettings())['minutes'],40)
        policy=top_off_policy(pairs,10,SpearSettings(use_learned_top_off=True))
        self.assertEqual(policy['minutes'],55)
        self.assertEqual(policy['source'],'truck p80')
        self.assertEqual(policy['confidence'],'qualified')

    def test_insufficient_single_sort_invalid_pairs_fallback(self):
        for pairs in ([self.pair(i) for i in range(9)], [self.pair(i,op=1) for i in range(12)],
                [self.pair(i,duration=-1) for i in range(12)]):
            self.assertEqual(top_off_policy(pairs,10,SpearSettings(use_learned_top_off=True))['source'],'manual')
        pairs=[self.pair(i) for i in range(10)]
        pairs[-1][1].sent_event_id=999
        self.assertEqual(top_off_policy(pairs,10,SpearSettings(use_learned_top_off=True))['minutes'],40)

    def test_gateway_fallback_and_independent_controls(self):
        pairs=[self.pair(i,truck=i%3+10,duration=50) for i in range(12)]
        policy=top_off_policy(pairs,99,SpearSettings(use_learned_top_off=True,live_calibration_mode='observe',learning_capture_enabled=False))
        self.assertEqual(policy['source'],'gateway p80');self.assertEqual(policy['minutes'],50)
