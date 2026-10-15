"""Exact resource-aware PROF mathematics; no operational side effects."""
import copy
import unittest
from datetime import datetime,timedelta,timezone
from decimal import Decimal
from types import SimpleNamespace as NS
from app.services.neoscorpion_resource_timing import resource_policy,aircraft_evidence,truck_interval,fueler_work_end,availability_window
from app.services.neoscorpion_prof import event_math,totals
from app.services.neoscorpion_spear_forecasts import physical_off_forecast
from app.services.neoscorpion_spear import SpearSettings,_ResourceState,_schedule_pair,_advance_resources,effective_spear_settings

T=datetime(2026,10,15)
def at(minutes):return T+timedelta(minutes=minutes)


class ResourceProfMathTest(unittest.TestCase):
    def evidence(self,**changes):
        values=dict(work_id=99,truck_id=1,fueler_id=2,on=at(30),truck_assigned=at(5),fueler_assigned=at(5),
            preparation=dict(at=at(16).isoformat(),fueler_id=2,assigned_at=at(5).isoformat()),
            aircraft=aircraft_evidence(NS(actual_block_in_datetime_utc=at(0)),None,15),
            parking='B4',parking_at=at(0),travel_per_move=2,window=10,setup=8,
            visits=[dict(id=10,truck_id=1,fueler_id=3,on=at(0),off=at(10),fueler_release=at(10),parking='A4',on_source='manual',finalized=True),
                    dict(id=11,truck_id=3,fueler_id=2,on=at(0),off=at(5),fueler_release=at(6),parking='B4',on_source='manual',finalized=True)])
        values.update(changes)
        result=resource_policy(**values)
        result.update(availability_continuity='verified',availability_evidence=[
            dict(at=at(0).isoformat(),available=True,scope='nightly',status='available',source='fixture_observation',actor_id=1),
            dict(at=at(0).isoformat(),available=True,scope='master',source='fixture_observation',actor_id=1)])
        return result

    def event(self,resources=None,**changes):
        resources=resources or self.evidence()
        facts=dict(version=4,resource_timing=resources,on_source='manual',fueler_id=2,truck_id=1,
            fueler_assigned_at=at(5).isoformat(),truck_assigned_at=at(5).isoformat(),
            setup_allowance=resources['setup_allowance'],pump_rate='150',exclusions=[],aircraft_type='A300')
        values=dict(id=1,fuel_work_state_id=99,fuel_assignment_id=1,cycle_number=1,
            performance_snapshot=facts,fueler_user_id=2,fuel_truck_id=1,event_type='fuel',transfer_fuel_gallons=3000,
            started_at_utc=at(30),ended_at_utc=at(40))
        values.update(changes)
        return NS(**values)

    def test_new_formulas_with_early_preparation_while_truck_busy(self):
        evidence=self.evidence()
        math=event_math(self.event(evidence))
        self.assertEqual(evidence['truck_eligible_start_at'],at(10).isoformat())
        self.assertEqual(evidence['setup_allowance'],'0')
        self.assertEqual(math['expected_truck'],22)
        self.assertEqual(math['truck_actual_minutes'],30)
        self.assertEqual(math['truck_prof'],73)
        self.assertEqual(math['fueler_prof'],200)

    def test_advance_preparation_exact_window_all_or_nothing(self):
        for start,expected in ((20,'0'),(21,'8'),(30,'8')):
            preparation=dict(at=at(25).isoformat(),fueler_id=2,assigned_at=at(start).isoformat())
            evidence=self.evidence(fueler_assigned=at(start),preparation=preparation)
            self.assertEqual(evidence['setup_allowance'],expected)
        self.assertEqual(self.evidence(window=None)['setup_allowance'],'8')
        self.assertEqual(self.evidence(preparation={})['setup_allowance'],'8')

    def test_busy_fueler_cannot_earn_assignment_only_setup_credit(self):
        visits=[dict(id=10,truck_id=1,fueler_id=3,on=at(0),off=at(10),fueler_release=at(10),parking='A4',on_source='manual'),
                dict(id=11,truck_id=3,fueler_id=2,on=at(0),off=at(5),fueler_release=at(25),on_source='manual')]
        evidence=self.evidence(visits=visits,preparation=dict(at=at(26).isoformat(),fueler_id=2,assigned_at=at(5).isoformat()))
        math=event_math(self.event(evidence))
        self.assertEqual(evidence['setup_allowance'],'8')
        self.assertEqual(math['fueler_prof'],280)
        self.assertIsNone(math['truck_prof'])
        self.assertIn('Pre-ON Fueler waiting uncertain',';'.join(math['truck_reasons']))
        self.assertEqual(math['excluded_wait_minutes'],0)

    def test_physical_on_cannot_be_relabelled_as_waiting_for_busy_fueler(self):
        visits=[dict(id=10,truck_id=1,fueler_id=3,on=at(0),off=at(28),fueler_release=at(28),parking='A4',on_source='manual'),
                dict(id=11,truck_id=3,fueler_id=2,on=at(0),off=at(5),fueler_release=at(35),on_source='manual')]
        math=event_math(self.event(self.evidence(visits=visits)))
        self.assertEqual(math['excluded_wait_minutes'],0)
        self.assertEqual(math['truck_actual_minutes'],12)
        self.assertIsNone(math['truck_prof'])
        self.assertIsNone(math['fueler_prof'])
        self.assertEqual(math['excluded_wait_intervals'],[])
        self.assertIn('waiting is not proven',';'.join(math['truck_reasons']))

    def test_truck_assignment_after_staging_and_previous_release_bounds(self):
        evidence=self.evidence(truck_assigned=at(25))
        self.assertEqual(evidence['truck_eligible_start_at'],at(25).isoformat())
        self.assertEqual(event_math(self.event(evidence))['truck_actual_minutes'],15)

    def test_same_ramp_and_zero_configured_travel(self):
        evidence=self.evidence(parking='A9')
        self.assertEqual(evidence['expected_travel_minutes'],'0')
        self.assertEqual(event_math(self.event(evidence))['truck_prof'],67)
        self.assertEqual(self.evidence(travel_per_move=0)['expected_travel_minutes'],'0')

    def test_unknown_location_disqualifies_truck_not_physical_fueler_math(self):
        evidence=self.evidence(parking='UNKNOWN')
        math=event_math(self.event(evidence))
        self.assertIsNone(math['truck_prof'])
        self.assertEqual(math['fueler_prof'],200)

    def test_missing_continuity_cannot_silently_qualify_new_truck_policy(self):
        evidence=self.evidence();evidence.pop('availability_continuity')
        math=event_math(self.event(evidence))
        self.assertIsNone(math['truck_prof'])
        self.assertEqual(math['fueler_prof'],200)

    def test_missing_fueler_work_end_is_not_dispatcher_complete(self):
        work=NS(ended_early_at_utc=None,performance_context={},off_at_utc=at(10),actuals_received_at_utc=at(15),
            physical_fueler_user_id=2,tail_number='N491UP',completed_at_utc=at(20))
        self.assertIsNone(fueler_work_end(work))
        work.performance_context=dict(fueler_data_ready=dict(at=at(16).isoformat(),fueler_id=2,tail='N491UP'))
        self.assertEqual(fueler_work_end(work),at(16))
        work.performance_context['fueler_data_ready']['at']=at(5).isoformat();work.actuals_received_at_utc=at(5)
        self.assertEqual(fueler_work_end(work),at(10))

    def test_missing_preparation_access_and_invalid_preparation_identity(self):
        evidence=self.evidence(aircraft=aircraft_evidence(None,None,15))
        self.assertIsNone(event_math(self.event(evidence))['fueler_prof'])
        self.assertEqual(self.evidence(preparation=dict(at=at(16).isoformat(),fueler_id=999,assigned_at=at(5).isoformat()))['setup_allowance'],'8')

    def test_late_aircraft_access_and_staging_are_independent(self):
        evidence=aircraft_evidence(NS(actual_block_in_datetime_utc=at(25),eta_datetime_utc=at(5)),None,15)
        self.assertEqual(evidence['aircraft_staging_at'],at(10).isoformat())
        self.assertEqual(evidence['aircraft_access_at'],at(25).isoformat())
        self.assertEqual(self.evidence(aircraft=evidence,preparation=dict(at=at(26).isoformat(),fueler_id=2,assigned_at=at(5).isoformat()))['setup_allowance'],'8')

    def test_overlap_reversed_and_legacy_evidence_never_qualifies(self):
        for change in (dict(on=at(45)),dict(truck_assigned=None),dict(travel_per_move=None)):
            self.assertIsNone(event_math(self.event(self.evidence(**change)))['truck_prof'])
        bad=[dict(id=10,truck_id=1,fueler_id=3,on=at(0),off=at(-1),parking='A4',on_source='legacy')]
        math=event_math(self.event(self.evidence(visits=bad)))
        self.assertIsNone(math['truck_prof'])

    def test_v3_math_and_mixed_totals_keep_resource_denominators(self):
        v3=self.event(started_at_utc=at(20),ended_at_utc=at(40))
        v3.performance_snapshot={**v3.performance_snapshot,'version':3,'setup_allowance':'8'}
        old=event_math(v3);new_event=self.event();new=event_math(new_event)
        self.assertEqual((old['truck_prof'],old['fueler_prof']),(100,140))
        jobs=[dict(event=v3,**old),dict(event=new_event,**new)]
        self.assertEqual(totals(jobs,'truck')['prof'],84)
        self.assertEqual(totals(jobs,'fueler')['prof'],160)
        self.assertEqual(event_math(v3),old)

    def test_midnight_timezone_and_fractional_seconds(self):
        e=self.event(started_at_utc=datetime(2026,10,14,19,30,tzinfo=timezone(timedelta(hours=-5))),
            ended_at_utc=at(40)+timedelta(seconds=30))
        math=event_math(e)
        self.assertEqual(math['actual_minutes'],Decimal('10.5'))
        self.assertEqual(math['truck_actual_minutes'],Decimal('30.5'))

    def test_hold_defuel_and_resource_mismatch_keep_exclusions(self):
        for change in (dict(event_type='defuel'),dict(fuel_truck_id=9),dict(transfer_fuel_gallons=0)):
            math=event_math(self.event(**change));self.assertIsNone(math['truck_prof']);self.assertIsNone(math['fueler_prof'])
        e=self.event();e.performance_snapshot['exclusions']=['Hold/review affected this cycle']
        self.assertIsNone(event_math(e)['truck_prof'])

    def test_forecast_on_anchor_does_not_add_travel_again_or_slide(self):
        e=self.event();work=NS(performance_context=e.performance_snapshot,id=99,off_at_utc=None,ended_early_at_utc=None)
        assignment=NS(id=1,assigned_fueler_user_id=2,assigned_truck_id=1,transfer_fuel_gallons=3000,current_cycle_number=1)
        row=dict(assignment=assignment,fuel_work_state=work,detailed_aircraft_type='A300',fueling_effective_on_utc=at(30))
        planning=NS(pump_rate_for=lambda _:150,setup_minutes=8)
        first=physical_off_forecast(row,planning)
        self.assertEqual(first['goal'],at(50))
        self.assertEqual(physical_off_forecast(row,planning),first)
        work.off_at_utc=at(40)
        self.assertIsNone(physical_off_forecast(row,planning)['goal'])

    def test_spear_travel_once_and_postoff_wrapup_only_reserves_fueler(self):
        fueler=_ResourceState(at(0),'Alpha');truck=_ResourceState(at(0),'Alpha')
        timing=NS(total_duration_minutes=33,finishing_minutes=5,aircraft_ready_utc=at(0))
        start,finish=_schedule_pair('Bravo',timing,fueler,truck,SpearSettings(),staging_allowed_at_utc=at(10))
        self.assertEqual((start,finish),(at(12),at(40)))
        _advance_resources(fueler,truck,'Bravo',finish,finishing_minutes=5)
        self.assertEqual(truck.available_at,at(40));self.assertEqual(fueler.available_at,at(45))

    def test_v4_forecast_observe_apply_and_legacy_sample_isolation(self):
        event=self.event()
        work=NS(performance_context=event.performance_snapshot,id=99,off_at_utc=None,ended_early_at_utc=None)
        assignment=NS(id=1,assigned_fueler_user_id=2,assigned_truck_id=1,transfer_fuel_gallons=3000,current_cycle_number=1)
        row=dict(assignment=assignment,fuel_work_state=work,detailed_aircraft_type='A300',fueling_effective_on_utc=at(30))
        planning=NS(pump_rate_for=lambda _:150,setup_minutes=8)
        jobs=[]
        for identity in (2,3,4):
            history=self.event(id=identity,fuel_assignment_id=identity,fuel_work_state_id=identity)
            history.performance_snapshot=copy.deepcopy(history.performance_snapshot)
            history.performance_snapshot['resource_timing']['work_id']=identity
            jobs.append(dict(event=history,**event_math(history)))
        observed=physical_off_forecast(row,planning,jobs=jobs,mode='observe')
        applied=physical_off_forecast(row,planning,jobs=jobs,mode='apply')
        self.assertEqual(observed['likely'],observed['goal'])
        self.assertEqual(applied['goal'],at(50))
        self.assertEqual(applied['likely'],at(42))
        self.assertEqual(applied['samples'],3)
        jobs[0]['facts']['version']=3
        insufficient=physical_off_forecast(row,planning,jobs=jobs,mode='apply')
        self.assertEqual(insufficient['samples'],2)
        self.assertEqual(insufficient['likely'],insufficient['goal'])

    def test_availability_history_proves_eligible_start_after_oos_return(self):
        nightly=[dict(at=at(0).isoformat(),available=False,status='unavailable_oos'),
                 dict(at=at(20).isoformat(),available=True,status='available')]
        master=[dict(at=at(0).isoformat(),available=True)]
        start,evidence,reasons=availability_window(at(10),at(30),nightly,master)
        self.assertEqual(start,at(20));self.assertEqual(reasons,[]);self.assertEqual(len(evidence),3)

    def test_intervening_oos_missing_and_conflicting_history_is_unqualified(self):
        master=[dict(at=at(0).isoformat(),available=True)]
        for history in (None,[],[dict(at=at(0).isoformat(),available=True),dict(at=at(15).isoformat(),available=False),dict(at=at(20).isoformat(),available=True)],
                        [dict(at=at(0).isoformat(),available=True),dict(at=at(0).isoformat(),available=False)]):
            self.assertTrue(availability_window(at(10),at(30),history,master)[2])

    def test_locked_same_timestamp_status_sequence_preserves_later_return(self):
        nightly=[dict(at=at(0).isoformat(),available=True,source='nightly_setup'),
                 dict(at=at(10).isoformat(),available=True,source='physical_off'),
                 dict(at=at(10).isoformat(),available=False,source='defuel_needs_sump'),
                 dict(at=at(20).isoformat(),available=True,source='sumped')]
        master=[dict(at=at(0).isoformat(),available=True,source='master_settings')]
        start,_,reasons=availability_window(at(20),at(30),nightly,master)
        self.assertEqual(start,at(20))
        self.assertEqual(reasons,[])

    def test_zero_travel_and_zero_staging_configuration_are_preserved(self):
        settings=NS(spear_recommendations_enabled=True,spear_automation_enabled=False,spear_minimum_truck_reserve_gallons=500,
            spear_do_not_top_off_above_percent=70,spear_truck_minutes_per_ramp_move=0,spear_fueler_begins_at='Remote',
            spear_truck_begins_at='Remote',spear_truck_after_top_off='Remote',spear_incoming_early_staging_minutes=0,
            spear_recalculation_interval_minutes=2,spear_automation_stability_delay_seconds=5,spear_priority_order_json=None)
        actual=effective_spear_settings(settings)
        self.assertEqual(actual.truck_minutes_per_ramp_move,0)
        self.assertEqual(actual.incoming_early_staging_minutes,0)
