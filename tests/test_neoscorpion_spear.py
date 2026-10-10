from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import unittest

from sqlalchemy import inspect

from app import create_app
from app.extensions import db
from app.models import Gateway, NeoScorpionSettings, User
from app.services.neoscorpion_spear import (
    SPEAR_DEFAULT_PRIORITY_ORDER,
    SpearPlan,
    SpearSettings,
    build_spear_plan,
    _parking_ramp,
    execute_spear_step,
    save_spear_settings,
    spear_dispatch_status,
)
from app.services.neoscorpion_spear_calibration import LiveCalibration
from app.services.neoscorpion_spear_learning import (
    SPEAR_LEARNING_PAYLOAD_VERSION,
    build_learning_recommendation_payload,
)
from app.services.neoscorpion_learning_vault import LearningVaultNotConfigured, export_learning_record


NOW = datetime(2026, 9, 3, 1, 0)


class _PlanningSettings:
    setup_minutes = Decimal("5")
    finishing_minutes = Decimal("5")
    eta_safety_buffer_minutes = Decimal("5")
    pump_rates_gallons_per_minute = {"B757": Decimal("100")}

    @staticmethod
    def pump_rate_for(_aircraft_type):
        return Decimal("100")

    @staticmethod
    def is_complete_for(_aircraft_type):
        return True


def _fueler(identifier=1, name="Smith"):
    return SimpleNamespace(
        id=identifier,
        first_name=name,
        last_name="",
        username=name.lower(),
        display_name=name,
    )


def _truck(identifier=10, *, current=2000, capacity=3000, status="available"):
    return {
        "truck": SimpleNamespace(
            id=identifier,
            truck_number=str(identifier),
            capacity_gallons=capacity,
            is_active=True,
            is_out_of_service=False,
        ),
        "selection": SimpleNamespace(status=status, current_gallons=current),
    }


def _row(identifier=100, *, demand=500, assignment=None, work_has_begun=False):
    departure = NOW + timedelta(hours=2)
    return {
        "mission": SimpleNamespace(
            id=identifier,
            flight_number=f"UPS{identifier}",
            eta_datetime_utc=None,
            planned_datetime_utc=departure,
        ),
        "arrival_mission": SimpleNamespace(
            actual_block_in_datetime_utc=NOW,
            eta_datetime_utc=NOW,
            planned_datetime_utc=NOW,
        ),
        "administratively_complete": False,
        "planning_demand_gallons": demand,
        "required_fuel_lbs": 20_000,
        "inbound_fuel_lbs": 12_000,
        "measured_inbound_fuel_lbs": None,
        "parking_position": "C04",
        "parking_valid": True,
        "detailed_aircraft_type": "B757",
        "assignment": assignment,
        "work_has_begun": work_has_begun,
    }


def _plan(rows, *, trucks=None, fuelers=None, settings=None):
    return build_spear_plan(
        rows,
        operation=SimpleNamespace(id=1),
        planning_settings=_PlanningSettings(),
        spear_settings=settings or SpearSettings(),
        nightly_fuelers=(_fueler(),) if fuelers is None else fuelers,
        nightly_trucks=(_truck(),) if trucks is None else trucks,
        now_utc=NOW,
    )


class NeoScorpionSpearPlanningTest(unittest.TestCase):
    def commitment(self, *, fueler=1, truck=10, **values):
        return SimpleNamespace(id=7, assigned_fueler_user_id=fueler, assigned_truck_id=truck,
            operational_status='active', completed_at_utc=None, fuel_on_board_at_utc=None, **values)

    def test_occupancy_audited_before_departure_order_including_pending_partial_and_hold(self):
        for fueler, truck in ((1,10),(1,None),(None,10)):
            for hold in (False,True):
                with self.subTest(fueler=fueler,truck=truck,hold=hold):
                    owner=_row(200,assignment=self.commitment(fueler=fueler,truck=truck))
                    owner.update(required_fuel_lbs=None,parking_valid=False)
                    if hold: owner['assignment'].operational_status='hold_review'
                    owner['mission'].planned_datetime_utc=NOW+timedelta(hours=5)
                    plan=_plan([_row(100),owner],trucks=(_truck(10),_truck(20),_truck(30)),
                        fuelers=(_fueler(1),_fueler(2),_fueler(3)))
                    step=next(s for s in plan.steps if s.mission_id==100)
                    if fueler: self.assertNotEqual(step.fueler_id,fueler)
                    if truck: self.assertNotEqual(step.truck_id,truck)
                    self.assertFalse(step.requires_resource_release)

    def test_occupied_resource_never_becomes_available_from_projected_finish(self):
        owner=_row(200,assignment=self.commitment())
        owner['mission'].planned_datetime_utc=NOW-timedelta(hours=2)
        owner['fuel_work_state']=SimpleNamespace(on_at_utc=NOW-timedelta(hours=3),off_at_utc=None,ended_early_at_utc=None)
        plan=_plan([_row(100),owner])
        self.assertFalse(plan.steps)
        self.assertIn('NO AVAILABLE FUELER',plan.unavailable_by_mission_id[100])
        owner['assignment'].assigned_fueler_user_id=None
        plan=_plan([_row(100),owner],fuelers=(_fueler(1),_fueler(2)))
        self.assertNotIn(100,[step.mission_id for step in plan.steps])
        self.assertIn('NO AVAILABLE TRUCK',plan.unavailable_by_mission_id[100])

    def test_occupied_truck_gets_advisory_next_without_executable_step(self):
        owner = _row(200, assignment=self.commitment())
        next_mission = _row(100)
        next_mission['parking_position'] = 'D07'
        plan = _plan([next_mission, owner])
        self.assertFalse(plan.steps)
        next_job = plan.occupied_truck_next[10]
        self.assertEqual(next_job.status, 'NEXT AFTER CURRENT')
        self.assertEqual((next_job.step.mission_id, next_job.step.flight_number,
                          next_job.step.fueler_id, next_job.step.truck_id), (100, 'UPS100', 1, 10))
        self.assertFalse(next_job.step.automatic_eligible)
        self.assertTrue(next_job.step.requires_resource_release)
        self.assertGreaterEqual(next_job.step.projected_start_at_utc,
                                NOW + timedelta(minutes=15))
        self.assertEqual(next_job.step.risk, 'COVERED')

    def test_next_forecast_never_recommends_current_mission_or_guesses_missing_data(self):
        owner = _row(200, assignment=self.commitment())
        self.assertIsNone(_plan([owner]).occupied_truck_next[10].step)
        candidate = _row(100)
        candidate['mission'].planned_datetime_utc = None
        plan = _plan([owner, candidate])
        self.assertIsNone(plan.occupied_truck_next[10].step)
        self.assertIn('PENDING', plan.occupied_truck_next[10].status)
        candidate['mission'].planned_datetime_utc = NOW + timedelta(hours=2)
        owner['planning_demand_gallons'] = None
        plan = _plan([owner, candidate])
        self.assertIsNone(plan.occupied_truck_next[10].step)
        self.assertIn('CURRENT WORK', plan.occupied_truck_next[10].status)

    def test_next_forecast_respects_fuel_and_truck_availability(self):
        owner = _row(200, demand=500, assignment=self.commitment())
        candidate = _row(100, demand=800)
        plan = _plan([owner, candidate], trucks=(_truck(current=1000),))
        self.assertIsNone(plan.occupied_truck_next[10].step)
        self.assertIn('FUEL / CAPACITY', plan.occupied_truck_next[10].status)
        plan = _plan([owner, candidate], trucks=(_truck(status='unavailable_oos'),))
        self.assertIsNone(plan.occupied_truck_next[10].step)
        self.assertIn('CURRENT WORK', plan.occupied_truck_next[10].status)

    def test_competing_next_jobs_do_not_double_book_missions_or_fuelers(self):
        owners = [_row(200, assignment=self.commitment(fueler=1, truck=10)),
                  _row(201, assignment=self.commitment(fueler=2, truck=20))]
        candidates = [_row(100), _row(101)]
        plan = _plan([*candidates, *owners], trucks=(_truck(10), _truck(20)),
                     fuelers=(_fueler(1), _fueler(2)))
        next_steps = [item.step for item in plan.occupied_truck_next.values() if item.step]
        self.assertEqual(len(next_steps), 2)
        self.assertEqual({step.mission_id for step in next_steps}, {100, 101})
        self.assertEqual({step.fueler_id for step in next_steps}, {1, 2})
        self.assertEqual({step.truck_id for step in next_steps}, {10, 20})
        self.assertFalse(plan.steps)

    def test_released_truck_uses_normal_actionable_plan_instead_of_forecast(self):
        owner = _row(200, assignment=self.commitment())
        owner['fuel_work_state'] = SimpleNamespace(off_at_utc=NOW-timedelta(minutes=5),
                                                   ended_early_at_utc=None)
        plan = _plan([owner, _row(100)])
        self.assertEqual(plan.occupied_truck_next, {})
        self.assertEqual(plan.steps[0].mission_id, 100)
        self.assertTrue(plan.steps[0].automatic_eligible)

    def test_idle_truck_card_uses_plan_but_pends_unknown_timing(self):
        from app.services.neoscorpion import _attach_spear_plan

        row = _row(100)
        visual = {"truck_id": 10, "status": "available"}
        _attach_spear_plan([row], [visual], _plan([row]))
        self.assertEqual(visual["spear_recommendation"].mission_id, 100)
        self.assertFalse(visual["spear_advisory_only"])
        row["mission"].planned_datetime_utc = None
        _attach_spear_plan([row], [visual], _plan([row]))
        self.assertIsNone(visual["spear_recommendation"])
        self.assertIn("PENDING · MISSION DATA", visual["spear_next_status"])

    def test_recorded_release_allows_reuse_without_re_reserving_finished_work(self):
        for boundary in ('off','ended_early','complete','fob'):
            with self.subTest(boundary=boundary):
                assignment=self.commitment()
                owner=_row(99,assignment=assignment)
                owner['fuel_work_state']=SimpleNamespace(off_at_utc=None,ended_early_at_utc=None)
                if boundary in ('off','ended_early'):
                    setattr(owner['fuel_work_state'],boundary+'_at_utc',NOW-timedelta(minutes=10))
                else:
                    setattr(assignment,'completed_at_utc' if boundary=='complete' else 'fuel_on_board_at_utc',NOW-timedelta(minutes=10))
                    owner['administratively_complete']=True
                plan=_plan([owner,_row(100)])
                self.assertEqual(len(plan.steps),1)
                step=plan.steps[0]
                self.assertEqual((step.mission_id,step.fueler_id,step.truck_id),(100,1,10))
                self.assertFalse(step.requires_resource_release)
                self.assertTrue(step.automatic_eligible)
                self.assertEqual(step.truck_location_provenance['location'],'Charlie')

    def test_free_resources_preferred_over_hypothetical_reuse_and_future_plans_labeled(self):
        plan=_plan([_row(100),_row(101)],trucks=(_truck(10),_truck(20)),fuelers=(_fueler(1),_fueler(2)))
        self.assertEqual([(s.fueler_id,s.truck_id) for s in plan.steps],[(1,10),(2,20)])
        self.assertTrue(all(not s.requires_resource_release for s in plan.steps))
        future=_plan([_row(100),_row(101)])
        self.assertFalse(future.steps[0].requires_resource_release)
        self.assertTrue(future.steps[1].requires_resource_release)
        self.assertFalse(future.steps[1].automatic_eligible)
        self.assertIn('release required',future.steps[1].reason)
        self.assertGreaterEqual(future.steps[1].projected_start_at_utc,future.steps[0].projected_complete_at_utc)

    def test_latest_recorded_release_sets_location_across_release_types(self):
        older = _row(98, assignment=self.commitment())
        older['parking_position'] = 'B06'
        older['fuel_work_state'] = SimpleNamespace(off_at_utc=NOW-timedelta(minutes=30))
        newer = _row(99, assignment=self.commitment())
        newer['parking_position'] = 'D07'
        newer['assignment'].completed_at_utc = NOW-timedelta(minutes=5)
        newer['administratively_complete'] = True
        for rows in ((older, newer), (newer, older)):
            step = _plan([*rows, _row(100)]).steps[0]
            self.assertEqual(step.truck_location_provenance['location'], 'Delta')
            self.assertEqual(step.fueler_location_provenance['location'], 'Delta')

    def test_occupied_truck_not_top_off_candidate_and_own_partial_assignment_kept(self):
        owner=_row(200,assignment=self.commitment(fueler=None))
        plan=_plan([_row(100,demand=100),owner],trucks=(_truck(10,current=550,capacity=2000),))
        self.assertNotIn(100,[step.mission_id for step in plan.steps])
        self.assertIn('NO AVAILABLE TRUCK',plan.unavailable_by_mission_id[100])
        partial=_row(100,assignment=self.commitment(truck=None))
        plan=_plan([partial],trucks=(_truck(20),),fuelers=(_fueler(1),_fueler(2)))
        self.assertEqual((plan.steps[0].fueler_id,plan.steps[0].truck_id),(1,20))

    def test_real_parking_codes_use_canonical_ramps_and_travel(self):
        for parking, ramp, travel in (("B06", "Bravo", "4"), ("D07", "Delta", "8"),
                                      ("E03", "Echo", "10"), ("B06 / S2", "Bravo", "4")):
            with self.subTest(parking=parking):
                row = _row()
                row["parking_position"] = parking
                plan = _plan([row])
                self.assertEqual(_parking_ramp(parking), ramp)
                self.assertEqual(plan.readiness_by_mission_id[100], ())
                self.assertEqual(plan.waiting_for_data_count, 0)
                self.assertEqual(len(plan.steps), 1)
                self.assertEqual(plan.steps[0].explanation["truck_travel_minutes"], f"{travel} min")

    def test_parking_readiness_uses_dispatch_validity_not_position_text(self):
        row = _row()
        row.update(parking_position="B06", parking_valid=False)
        plan = _plan([row])
        self.assertEqual(plan.readiness_by_mission_id[100], ("parking",))
        self.assertFalse(plan.steps)

    def test_unmapped_parking_blocks_travel_without_guessing(self):
        for parking in ("Bravo anything", "B99", "D07 unknown", "E03 / S3", "AB12", ""):
            with self.subTest(parking=parking):
                row = _row()
                row.update(parking_position=parking, parking_valid=True)
                plan = _plan([row])
                self.assertIsNone(_parking_ramp(parking))
                self.assertFalse(plan.steps)
                self.assertEqual(plan.waiting_for_data_count, 0)
                self.assertIn("PARKING RAMP NOT MAPPED", plan.unavailable_by_mission_id[100])

    def test_on_ground_and_arrived_without_timestamps_are_ready_with_unknown_timing(self):
        for status in ("On Ground", "Arrived", "on ground", "ARRIVED"):
            for arrival in (None, SimpleNamespace(actual_block_in_datetime_utc=None,
                                                 eta_datetime_utc=None, planned_datetime_utc=None)):
                with self.subTest(status=status, arrival=arrival):
                    row = _row()
                    row.update(arrival_status=status, arrival_mission=arrival)
                    plan = _plan([row])
                    self.assertEqual(plan.readiness_by_mission_id[100], ())
                    self.assertEqual(plan.waiting_for_data_count, 0)
                    step = plan.steps[0]
                    self.assertEqual(step.risk, "TIMING UNKNOWN")
                    self.assertIsNone(step.projected_complete_at_utc)
                    self.assertEqual(step.explanation["aircraft_ready_source"], "Unknown")
                    self.assertFalse(step.automatic_eligible)

    def test_on_ground_clears_gate_but_preserves_eta_buffer_timing(self):
        row = _row()
        row["arrival_status"] = "On Ground"
        row["arrival_mission"].actual_block_in_datetime_utc = None
        row["arrival_mission"].eta_datetime_utc = NOW + timedelta(minutes=45)
        step = _plan([row]).steps[0]
        self.assertEqual(step.projected_start_at_utc, NOW + timedelta(minutes=50))
        self.assertEqual(step.explanation["aircraft_ready_source"], "ETA + 5")

    def test_missing_basic_data_reports_only_actual_missing_fields(self):
        for field, value, reason in (("required_fuel_lbs", None, "required_fuel"),
                                     ("inbound_fuel_lbs", None, "inbound_fuel"),
                                     ("parking_valid", False, "parking"),
                                     ("arrival_status", "Scheduled", "arrival_timing")):
            with self.subTest(field=field):
                row = _row()
                row.update(arrival_status="On Ground", arrival_mission=None)
                row[field] = value
                plan = _plan([row])
                self.assertEqual(plan.readiness_by_mission_id[100], (reason,))
                self.assertFalse(plan.steps)

    def test_resource_blockers_are_evaluated_not_waiting(self):
        for fuelers, trucks, blocker in (((), (_truck(),), "NO ELIGIBLE FUELER"),
                                         ((_fueler(),), (), "NO ELIGIBLE TRUCK"),
                                         ((_fueler(),), (_truck(status="needs_sump"),), "NO ELIGIBLE TRUCK"),
                                         ((_fueler(),), (_truck(current=600, capacity=600),), "truck fuel / capacity constraints")):
            with self.subTest(blocker=blocker):
                plan = _plan([_row(demand=1000)], fuelers=fuelers, trucks=trucks)
                self.assertEqual(plan.waiting_for_data_count, 0)
                self.assertEqual(plan.readiness_by_mission_id[100], ())
                self.assertFalse(plan.steps)
                self.assertIn(blocker, plan.unavailable_by_mission_id[100])

    def test_waiting_to_ready_and_resource_blocked_transition_is_recalculated(self):
        from app.services.neoscorpion import _attach_spear_plan
        row = _row()
        row.update(arrival_status="On Ground", arrival_mission=None, parking_position="D07",
                   inbound_fuel_lbs=None)
        _attach_spear_plan([row], [], _plan([row]))
        self.assertFalse(row["spear_ready"])
        self.assertTrue(row["spear_readiness_reasons"])
        row["inbound_fuel_lbs"] = 12_000
        _attach_spear_plan([row], [], _plan([row]))
        self.assertTrue(row["spear_ready"])
        self.assertFalse(row["spear_readiness_reasons"])
        self.assertIsNotNone(row["spear_step"])
        _attach_spear_plan([row], [], _plan([row], fuelers=()))
        self.assertTrue(row["spear_ready"])
        self.assertFalse(row["spear_readiness_reasons"])
        self.assertIsNone(row["spear_step"])
        self.assertEqual(row["spear_problem"], "NO ELIGIBLE FUELER")

    def test_operational_nickname_changes_displayed_plan_not_canonical_step(self):
        from app.services.neoscorpion import _apply_operational_fueler_names

        rows = [_row()]
        plan = _plan(rows)
        self.assertTrue(plan.steps)
        fueler = _fueler()
        context = {
            "nightly_fuelers": [{"user": fueler}],
            "eligible_nightly_fuelers": [],
            "nightly_assignment_fuelers": [fueler],
            "rows": rows,
            "truck_visuals": [],
            "spear_plan": plan,
        }
        _apply_operational_fueler_names(context, {fueler.id: "Ace"})
        self.assertEqual(context["nightly_fuelers"][0]["user"].display_name, "Ace")
        self.assertEqual(context["spear_plan"].steps[0].fueler_name, "Ace")
        self.assertEqual(context["spear_plan"].steps[0].fueler_id, fueler.id)
        self.assertEqual(context["spear_plan"].token, plan.token)
        self.assertEqual(plan.steps[0].fueler_name, fueler.display_name)

    def test_occupied_next_card_uses_nickname_without_changing_canonical_forecast(self):
        from app.services.neoscorpion import _apply_operational_fueler_names

        rows = [_row(200, assignment=self.commitment()), _row(100)]
        plan = _plan(rows)
        fueler = _fueler()
        visual = {"truck_id": 10, "status": "available"}
        context = {
            "nightly_fuelers": [{"user": fueler}],
            "eligible_nightly_fuelers": [],
            "nightly_assignment_fuelers": [fueler],
            "rows": rows,
            "truck_visuals": [visual],
            "spear_plan": plan,
        }
        _apply_operational_fueler_names(context, {fueler.id: "Ace"})
        self.assertEqual(visual["spear_recommendation"].fueler_name, "Ace")
        self.assertEqual(visual["spear_parking_position"], "C04")
        self.assertTrue(visual["spear_advisory_only"])
        self.assertEqual(plan.occupied_truck_next[10].step.fueler_name, fueler.display_name)
        self.assertEqual(context["spear_plan"].token, plan.token)

    def test_compact_dispatch_status_prioritizes_risk_over_automation(self):
        plan = SpearPlan((), {}, {}, 0, 0, 0, 0, "", "token")
        self.assertEqual(
            spear_dispatch_status(plan, SpearSettings(recommendations_enabled=False))["state"],
            "off",
        )
        self.assertEqual(spear_dispatch_status(plan, SpearSettings())["state"], "ready")
        self.assertEqual(
            spear_dispatch_status(plan, SpearSettings(automation_enabled=True))["state"],
            "auto",
        )
        timing_unknown = SpearPlan(
            (), {}, {}, 0, 0, 0, 0, "", "token", timing_unknown_count=1
        )
        self.assertEqual(
            spear_dispatch_status(
                timing_unknown,
                SpearSettings(automation_enabled=True),
            )["state"],
            "timing",
        )
        at_risk = SpearPlan((), {}, {}, 0, 1, 0, 0, "", "token")
        self.assertEqual(
            spear_dispatch_status(at_risk, SpearSettings(automation_enabled=True))["state"],
            "at-risk",
        )
        late = SpearPlan((), {}, {}, 0, 0, 1, 0, "", "token")
        self.assertEqual(
            spear_dispatch_status(late, SpearSettings(automation_enabled=True))["state"],
            "late",
        )

    def test_normal_recommendation_is_deterministic_and_covered(self):
        plan = _plan([_row()])

        self.assertEqual(len(plan.steps), 1)
        step = plan.steps[0]
        self.assertEqual((step.action_type, step.truck_id, step.fueler_id), ("assign", 10, 1))
        self.assertEqual(step.risk, "COVERED")
        self.assertEqual(plan.status_text, "SPEAR: ALL LOADS COVERED")

    def test_sparse_and_aware_mission_times_do_not_break_planning(self):
        scheduled = _row(100)
        scheduled["mission"].planned_datetime_utc = (
            NOW + timedelta(hours=2)
        ).replace(tzinfo=timezone.utc)
        incomplete = _row(101)
        incomplete["mission"].planned_datetime_utc = None
        incomplete["arrival_mission"].actual_block_in_datetime_utc = None
        incomplete["arrival_mission"].eta_datetime_utc = None
        incomplete["arrival_mission"].planned_datetime_utc = None

        plan = _plan([scheduled, incomplete])

        self.assertEqual(plan.steps[0].mission_id, 100)
        self.assertEqual(
            plan.readiness_by_mission_id[101],
            ("arrival_timing",),
        )

    def test_incomplete_fuel_data_is_waiting_not_covered_or_at_risk(self):
        row = _row(demand=None)
        row["required_fuel_lbs"] = None
        row["inbound_fuel_lbs"] = None
        plan = _plan([row])

        self.assertEqual(plan.waiting_for_data_count, 1)
        self.assertEqual(plan.covered_count, 0)
        self.assertEqual(plan.at_risk_count, 0)
        self.assertEqual(plan.status_text, "SPEAR: 1 WAITING FOR DATA")
        self.assertEqual(
            plan.readiness_by_mission_id[100],
            ("required_fuel", "inbound_fuel"),
        )

    def test_basic_gate_requires_required_and_inbound_even_when_demand_exists(self):
        missing_required = _row(100)
        missing_required["required_fuel_lbs"] = None
        missing_inbound = _row(101)
        missing_inbound["inbound_fuel_lbs"] = None

        plan = _plan([missing_required, missing_inbound])

        self.assertEqual(plan.waiting_for_data_count, 2)
        self.assertEqual(
            plan.readiness_by_mission_id[100],
            ("required_fuel",),
        )
        self.assertEqual(
            plan.readiness_by_mission_id[101],
            ("inbound_fuel",),
        )

    def test_measured_inbound_satisfies_readiness_without_dispatcher_inbound(self):
        row = _row()
        row["inbound_fuel_lbs"] = None
        row["measured_inbound_fuel_lbs"] = 11_500

        plan = _plan([row])

        self.assertEqual(plan.waiting_for_data_count, 0)
        self.assertEqual(plan.readiness_by_mission_id[100], ())
        self.assertEqual(len(plan.steps), 1)

    def test_fallback_estimate_does_not_satisfy_inbound_readiness(self):
        row = _row()
        row["inbound_fuel_lbs"] = None
        row["measured_inbound_fuel_lbs"] = None
        row["planning_demand_gallons"] = 500

        plan = _plan([row])

        self.assertEqual(plan.waiting_for_data_count, 1)
        self.assertEqual(
            plan.readiness_by_mission_id[100],
            ("inbound_fuel",),
        )

    def test_dispatch_arrived_status_satisfies_spear_without_block_in(self):
        row = _row()
        row["arrival_status"] = "Arrived"
        row["arrival_mission"].actual_block_in_datetime_utc = None
        row["arrival_mission"].eta_datetime_utc = NOW + timedelta(minutes=45)
        row["arrival_mission"].planned_datetime_utc = NOW + timedelta(minutes=45)

        plan = _plan([row])

        self.assertEqual(plan.readiness_by_mission_id[100], ())
        self.assertEqual(plan.waiting_for_data_count, 0)
        self.assertEqual(len(plan.steps), 1)

    def test_arrival_gate_opens_only_at_block_in_or_early_staging_window(self):
        outside = _row(100)
        outside["arrival_mission"].actual_block_in_datetime_utc = None
        outside["arrival_mission"].eta_datetime_utc = NOW + timedelta(minutes=30)
        outside["arrival_mission"].planned_datetime_utc = NOW + timedelta(minutes=30)

        inside = _row(101)
        inside["arrival_mission"].actual_block_in_datetime_utc = None
        inside["arrival_mission"].eta_datetime_utc = NOW + timedelta(minutes=10)
        inside["arrival_mission"].planned_datetime_utc = NOW + timedelta(minutes=10)

        plan = _plan([outside, inside])

        self.assertEqual(
            plan.readiness_by_mission_id[100],
            ("arrival_timing",),
        )
        self.assertEqual(plan.readiness_by_mission_id[101], ())
        self.assertEqual([step.mission_id for step in plan.steps], [101])

    def test_missing_departure_keeps_recommendation_with_neutral_timing(self):
        row = _row()
        row["mission"].planned_datetime_utc = None

        plan = _plan([row])

        self.assertEqual(plan.waiting_for_data_count, 0)
        self.assertEqual(len(plan.steps), 1)
        self.assertEqual(plan.steps[0].risk, "TIMING UNKNOWN")
        self.assertFalse(plan.steps[0].automatic_eligible)
        self.assertEqual(plan.late_count, 0)
        self.assertEqual(plan.timing_unknown_count, 1)
        self.assertEqual(plan.status_text, "SPEAR: 1 TIMING UNKNOWN")

    def test_missing_derived_gallons_does_not_become_waiting_for_data(self):
        row = _row(demand=None)

        plan = _plan([row])

        self.assertEqual(plan.waiting_for_data_count, 0)
        self.assertEqual(len(plan.steps), 1)
        self.assertEqual(plan.steps[0].risk, "TIMING UNKNOWN")
        self.assertFalse(plan.steps[0].automatic_eligible)

    def test_incomplete_planning_settings_degrade_timing_without_blocking(self):
        class _IncompletePlanningSettings:
            setup_minutes = None
            finishing_minutes = None

            @staticmethod
            def pump_rate_for(_aircraft_type):
                return None

            @staticmethod
            def is_complete_for(_aircraft_type):
                return False

        plan = build_spear_plan(
            [_row()],
            operation=SimpleNamespace(id=1),
            planning_settings=_IncompletePlanningSettings(),
            spear_settings=SpearSettings(),
            nightly_fuelers=(_fueler(),),
            nightly_trucks=(_truck(),),
            now_utc=NOW,
        )

        self.assertEqual(plan.waiting_for_data_count, 0)
        self.assertEqual(len(plan.steps), 1)
        self.assertEqual(plan.steps[0].risk, "TIMING UNKNOWN")
        self.assertFalse(plan.steps[0].automatic_eligible)

    def test_temporary_resource_shortage_is_evaluable_not_waiting_for_data(self):
        plan = _plan([_row()], trucks=(_truck(status="unavailable_oos"),))

        self.assertEqual(plan.waiting_for_data_count, 0)
        self.assertEqual(plan.unplanned_count, 1)
        self.assertEqual(plan.readiness_by_mission_id[100], ())

    def test_assignment_explanation_uses_planned_values_and_ranked_alternatives(self):
        plan = _plan([_row()], trucks=(_truck(10), _truck(20)))
        explanation = plan.steps[0].explanation

        self.assertEqual(explanation["kind"], "assignment")
        self.assertEqual(explanation["safe_completion_target"], "21:40")
        self.assertEqual(explanation["truck_gallons_before"], 2000)
        self.assertEqual(explanation["truck_gallons_after"], 1500)
        self.assertEqual(len(explanation["alternatives"]), 2)
        self.assertIn("Selected", explanation["alternatives"][0]["reason"])

    def test_why_spear_identifies_the_same_active_calibration_used_by_planning(self):
        calibration = LiveCalibration(
            metric="pump_rate", scope_key="B757", configured=Decimal("100"),
            observed=Decimal("140"), effective=Decimal("120"), samples=3,
            excluded_samples=0, first_observation_utc=None, most_recent_observation_utc=None,
            observations=(), excluded_observations=(),
        )
        plan = build_spear_plan(
            [_row()], operation=SimpleNamespace(id=1), planning_settings=_PlanningSettings(),
            spear_settings=SpearSettings(), nightly_fuelers=(_fueler(),),
            nightly_trucks=(_truck(),), now_utc=NOW,
            calibrations={("pump_rate", "B757"): calibration},
        )
        self.assertEqual(plan.steps[0].explanation["live_calibration"][0]["samples"], 3)
        self.assertEqual(plan.steps[0].explanation["live_calibration"][0]["candidate"], "120 gal/min")
        self.assertEqual(plan.steps[0].explanation["live_calibration"][0]["using"], "100 gal/min")
        self.assertEqual(plan.steps[0].explanation["live_calibration"][0]["mode"], "OBSERVE")

    def test_top_off_explanation_identifies_reserve_protection(self):
        plan = _plan([_row(demand=100)], trucks=(_truck(current=550, capacity=2000),))
        explanation = plan.steps[0].explanation

        self.assertEqual(explanation["kind"], "top_off")
        self.assertEqual(explanation["current_gallons"], 550)
        self.assertEqual(explanation["reserve_gallons"], 500)
        self.assertIn("Reserve", explanation["reason"])

    def test_learning_payload_is_versioned_deterministic_and_has_no_persistence(self):
        step = _plan([_row()]).steps[0]
        kwargs = {
            "captured_at_utc": NOW,
            "gateway_id": 1,
            "operation_id": 2,
            "mission_id": 100,
            "recommendation_token": "stable-token",
            "soft_priority_order": SPEAR_DEFAULT_PRIORITY_ORDER,
            "recommendation": step,
            "mission_facts": {"required_fuel": 500, "mission_ramp": "Charlie"},
            "candidate_trucks": ({"truck_id": 10, "location": "Remote"},),
            "candidate_fuelers": ({"user_id": 1, "location": "Remote"},),
        }
        self.assertEqual(
            build_learning_recommendation_payload(**kwargs),
            build_learning_recommendation_payload(**kwargs),
        )
        payload = build_learning_recommendation_payload(**kwargs)
        self.assertEqual(payload["schema_version"], SPEAR_LEARNING_PAYLOAD_VERSION)
        self.assertEqual(payload["record_type"], "recommendation_snapshot")
        with self.assertRaises(LearningVaultNotConfigured):
            export_learning_record(payload)

    def test_reserve_shortfall_recommends_existing_top_off_workflow(self):
        plan = _plan([_row(demand=100)], trucks=(_truck(current=550, capacity=2000),))

        self.assertEqual(len(plan.steps), 1)
        self.assertEqual(plan.steps[0].action_type, "top_off")
        self.assertEqual(plan.steps[0].truck_id, 10)

    def test_partial_manual_assignment_is_preserved_while_spear_fills_the_gap(self):
        fueler_only = SimpleNamespace(
            id=7,
            assigned_fueler_user_id=1,
            assigned_truck_id=None,
            operational_status="active",
            completed_at_utc=None,
        )
        truck_only = SimpleNamespace(
            id=8,
            assigned_fueler_user_id=None,
            assigned_truck_id=10,
            operational_status="active",
            completed_at_utc=None,
        )

        fueler_plan = _plan(
            [_row(100, assignment=fueler_only)],
            trucks=(_truck(10), _truck(20)),
        )
        truck_plan = build_spear_plan(
            [_row(101, assignment=truck_only)],
            operation=SimpleNamespace(id=1),
            planning_settings=_PlanningSettings(),
            spear_settings=SpearSettings(),
            nightly_fuelers=(_fueler(1, "Smith"), _fueler(2, "Jones")),
            nightly_trucks=(_truck(10),),
            now_utc=NOW,
        )

        self.assertEqual(fueler_plan.steps[0].fueler_id, 1)
        self.assertIn(fueler_plan.steps[0].truck_id, {10, 20})
        self.assertEqual(truck_plan.steps[0].truck_id, 10)
        self.assertIn(truck_plan.steps[0].fueler_id, {1, 2})

    def test_dispatch_rows_show_spear_recommendation_for_each_unassigned_resource(self):
        root = Path(__file__).resolve().parents[1]
        template = (
            root / "app/templates/neonodes/neoscorpion/_fuel_dispatch_panel.html"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "SPEAR → {{ row.spear_step.fueler_name }}",
            template,
        )
        self.assertIn(
            "SPEAR → {{ row.spear_step.truck_number }}",
            template,
        )
        self.assertIn(
            "and not row.assigned_fueler",
            template,
        )
        self.assertIn(
            "and not row.assigned_truck",
            template,
        )

    def test_sent_work_locks_valid_resources_but_invalidity_replans(self):
        assignment = SimpleNamespace(
            id=7,
            assigned_fueler_user_id=1,
            assigned_truck_id=10,
            operational_status="active",
            completed_at_utc=None,
        )
        locked = _plan([_row(assignment=assignment)])
        self.assertEqual(locked.steps, ())

        replanned = _plan(
            [_row(assignment=assignment, work_has_begun=True)],
            trucks=(
                _truck(10, status="unavailable_oos"),
                _truck(20),
            ),
        )
        self.assertEqual(replanned.steps[0].truck_id, 20)
        self.assertEqual(replanned.steps[0].reason, "Replace invalid sent resource")
        self.assertFalse(replanned.steps[0].automatic_eligible)

    def test_automation_execution_uses_the_same_canonical_action_boundary(self):
        step = _plan([_row()]).steps[0]
        calls = []
        result = execute_spear_step(
            step,
            assign_action=lambda selected: calls.append(("assign", selected.mission_id)) or "saved",
            top_off_action=lambda selected: calls.append(("top_off", selected.truck_id)),
        )

        self.assertEqual(result, "saved")
        self.assertEqual(calls, [("assign", 100)])


class NeoScorpionSpearSettingsTest(unittest.TestCase):
    def setUp(self):
        config = type(
            "TestConfig",
            (),
            {
                "SECRET_KEY": "spear-test",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            },
        )
        self.app = create_app(config, auto_bootstrap=False)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.gateway = Gateway(code="RFD", name="Rockford")
        self.user = User(username="dispatcher", password_hash="x", role="master")
        db.session.add_all((self.gateway, self.user))
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_settings_and_drag_order_persist(self):
        reversed_order = tuple(reversed(SPEAR_DEFAULT_PRIORITY_ORDER))
        result = save_spear_settings(
            self.gateway,
            self.user,
            {
                "recommendations_enabled": "1",
                "automation_enabled": "1",
                "minimum_truck_reserve_gallons": "650",
                "do_not_top_off_above_percent": "75",
                "truck_minutes_per_ramp_move": "3.5",
                "fueler_begins_at": "Alpha",
                "truck_begins_at": "Bravo",
                "truck_after_top_off": "Remote",
                "incoming_early_staging_minutes": "15",
                "recalculation_interval_minutes": "2",
                "automation_stability_delay_seconds": "5",
                "priority_order": ",".join(reversed_order),
            },
        )
        db.session.commit()
        db.session.expire_all()
        saved = NeoScorpionSettings.query.filter_by(gateway_id=self.gateway.id).one()

        self.assertTrue(result.automation_just_enabled)
        self.assertTrue(saved.spear_automation_enabled)
        self.assertFalse(saved.spear_learning_capture_enabled)
        self.assertEqual(saved.spear_minimum_truck_reserve_gallons, 650)
        self.assertEqual(tuple(__import__("json").loads(saved.spear_priority_order_json)), reversed_order)

        root = Path(__file__).resolve().parents[1]
        template = (root / "app/templates/neonodes/neoscorpion/spear_settings.html").read_text(encoding="utf-8")
        script = (root / "app/static/js/neoscorpion_spear_settings.js").read_text(encoding="utf-8")
        self.assertIn("SPEAR Fleet Optimizer", template)
        self.assertIn("data-spear-priority-list", template)
        self.assertIn('addEventListener("dragover"', script)

    def test_learning_capture_defaults_off_and_cannot_enable_without_vault(self):
        with self.assertRaisesRegex(ValueError, "durable Learning Vault"):
            save_spear_settings(
                self.gateway,
                self.user,
                {
                    "recommendations_enabled": "1",
                    "learning_capture_enabled": "1",
                    "minimum_truck_reserve_gallons": "500",
                    "do_not_top_off_above_percent": "70",
                    "truck_minutes_per_ramp_move": "2",
                    "fueler_begins_at": "Remote",
                    "truck_begins_at": "Remote",
                    "truck_after_top_off": "Remote",
                    "incoming_early_staging_minutes": "15",
                    "recalculation_interval_minutes": "2",
                    "automation_stability_delay_seconds": "5",
                    "priority_order": ",".join(SPEAR_DEFAULT_PRIORITY_ORDER),
                },
            )
        self.assertIsNone(
            NeoScorpionSettings.query.filter_by(gateway_id=self.gateway.id).first()
        )

    def test_teach_spear_is_visible_but_disabled_while_learning_is_off(self):
        root = Path(__file__).resolve().parents[1]
        template = (root / "app/templates/neonodes/neoscorpion/_fuel_dispatch_panel.html").read_text(encoding="utf-8")
        settings_template = (root / "app/templates/neonodes/neoscorpion/spear_settings.html").read_text(encoding="utf-8")

        self.assertIn("TEACH SPEAR", template)
        self.assertIn("LEARNING OFF", template)
        self.assertIn("SPEAR Learning Capture is not enabled yet.", template)
        self.assertIn("LEARNING VAULT NOT CONFIGURED", settings_template)

    def test_vault_test_button_uses_its_own_form(self):
        root = Path(__file__).resolve().parents[1]
        template = (root / "app/templates/neonodes/neoscorpion/spear_settings.html").read_text(encoding="utf-8")

        self.assertIn('form="spear-vault-test-form"', template)
        self.assertIn(
            'id="spear-vault-test-form" method="post" action="{{ url_for(\'neoscorpion.spear_vault_test\') }}"',
            template,
        )
        self.assertNotIn(
            '<form method="post" action="{{ url_for(\'neoscorpion.spear_vault_test\') }}">',
            template,
        )

    def test_dispatch_automation_only_arms_with_an_eligible_spear_step(self):
        root = Path(__file__).resolve().parents[1]
        template = (
            root / "app/templates/neonodes/neoscorpion/fuel_dispatch.html"
        ).read_text(encoding="utf-8")
        css = (
            root / "app/static/css/14-neoscorpion.css"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "spear_settings.automation_enabled and spear_automatic_available",
            template,
        )
        self.assertNotIn(
            "spear_settings.automation_enabled and spear_plan and spear_plan.steps",
            template,
        )
        self.assertIn(".neoscorpion-spear-indicator.is-timing", css)

    def test_dispatch_renders_compact_readiness_and_collapsed_why_hook(self):
        root = Path(__file__).resolve().parents[1]
        template = (root / "app/templates/neonodes/neoscorpion/_fuel_dispatch_panel.html").read_text(encoding="utf-8")

        self.assertIn("SPEAR DATA READINESS", template)
        self.assertIn("WAITING FOR DATA", template)
        self.assertIn("<details class=\"neoscorpion-spear-why\"", template)
        self.assertIn("WHY SPEAR?", template)

    def test_dispatch_splash_requires_explicit_close_before_marking_seen(self):
        root = Path(__file__).resolve().parents[1]
        template = (root / "app/templates/neonodes/neoscorpion/fuel_dispatch.html").read_text(encoding="utf-8")
        script = (root / "app/static/js/neoscorpion_fuel_dispatch_live.js").read_text(encoding="utf-8")

        self.assertIn("data-spear-splash", template)
        self.assertIn("images/neoscorpion/spear-promo.png", template)
        self.assertIn("can_view_spear_settings", template)
        self.assertTrue((root / "app/static/images/neoscorpion/spear-promo.png").is_file())
        self.assertIn("neoapps.neoscorpion.spear-splash.v1", script)
        self.assertIn("window.localStorage.getItem", script)
        self.assertIn("data-spear-splash-close", template)
        self.assertIn("CLOSE", template)
        self.assertIn("data-spear-splash-close", script)
        self.assertIn('window.localStorage.setItem(storageKey, "seen")', script)
        self.assertNotIn("dismissTimer", script)
        self.assertNotIn("4700", script)

    def test_schema_contains_settings_and_execution_audit(self):
        inspector = inspect(db.engine)

        self.assertIn("neoscorpion_spear_audit_entries", inspector.get_table_names())
        self.assertIn("neoscorpion_spear_calibration_resets", inspector.get_table_names())
        columns = {
            column["name"]
            for column in inspector.get_columns("neoscorpion_settings")
        }
        self.assertIn("spear_automation_enabled", columns)
        self.assertIn("spear_learning_capture_enabled", columns)
        self.assertIn("spear_live_calibration_mode", columns)
        self.assertIn("spear_priority_order_json", columns)
        assignment_columns = {
            column["name"] for column in inspector.get_columns("neoscorpion_fuel_assignments")
        }
        self.assertIn("ready_for_fuel_at_utc", assignment_columns)


if __name__ == "__main__":
    unittest.main()
