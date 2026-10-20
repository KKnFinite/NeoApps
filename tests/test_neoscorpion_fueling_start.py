"""Focused ON/resource/travel hotfix regressions using the locked save path."""
from datetime import datetime, timedelta
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.extensions import db
from app.models import NeoScorpionFuelWorkState, NeoScorpionFuelTankState, NeoScorpionFuelAuditEntry, NeoScorpionTruckInventoryAudit, SortDateParkingAssignment
from app.services import neoscorpion as service
from app.services.neoscorpion_fueling_start import fueling_start_state
from app.services.neoscorpion_spear import SpearSettings
from app.services.neoscorpion_schema import NEOSCORPION_ADDITIVE_COLUMNS
from app.services.schema_sync import LOCAL_SQLITE_OPTIONAL_COLUMNS, POSTGRES_OPTIONAL_COLUMNS
from tests import test_neoscorpion_fueling_status as fixtures


class FuelingStartHotfixTest(unittest.TestCase):
    setUp = fixtures.FuelingStatusIntegrationTest.setUp
    login = fixtures.FuelingStatusIntegrationTest.login

    def prepare(self, *, truck=True):
        self.now = datetime.utcnow()
        self.mission.planned_fuel_load = 52000
        self.assignment.assigned_fueler_user_id = self.fueler.id
        self.assignment.created_at = self.now-timedelta(hours=1)
        self.truck = self.fixture._nightly_truck() if truck else None
        self.assignment.assigned_truck_id = self.truck.id if self.truck else None
        db.session.commit()

    def save(self, now=None, **values):
        result = service.save_fueler_entry(self.gateway, self.fueler,
            {"assignment_id":str(self.assignment.id), **values}, now_utc=now or self.now)
        db.session.commit()
        return result

    def readings(self):
        return dict(remaining_left="12.0", remaining_ctr="0.0", remaining_right="0.0", apu_running="no")

    def confirm(self, now=None):
        result = service.confirm_fueler_truck_on(
            self.gateway, self.fueler, self.assignment.id,
            expected_cycle=self.assignment.current_cycle_number,
            expected_tail=self.mission.assigned_tail_number,
            expected_truck_id=self.truck.id,
            now_utc=now or self.now,
        )
        db.session.commit()
        return result

    def test_manual_truck_on_before_readings_is_atomic_persistent_and_idempotent(self):
        self.prepare()
        changed, _revision, work = self.confirm()
        self.assertTrue(changed)
        self.assertEqual(work.manual_on_at_utc, self.now)
        self.assertEqual(work.manual_on_by_user_id, self.fueler.id)
        self.assertIsNone(work.on_at_utc)
        self.assertEqual(self.row()["dispatch_status_key"], "fueling")
        self.assertEqual(self.row()["fueling_effective_on_utc"], self.now)
        self.assertTrue(self.row()["manual_on_confirmed"])
        self.assertFalse(self.confirm(now=self.now+timedelta(minutes=1))[0])
        self.assertEqual(NeoScorpionFuelAuditEntry.query.filter_by(action="truck_on").count(), 1)
        self.save(now=self.now+timedelta(minutes=2), **self.readings())
        db.session.expire_all()
        work = NeoScorpionFuelWorkState.query.filter_by(fuel_assignment_id=self.assignment.id).one()
        self.assertEqual(work.manual_on_at_utc, self.now)
        self.assertEqual(self.row(self.now+timedelta(minutes=3))["fueling_effective_on_utc"], self.now)

    def test_manual_on_overrides_inferred_on_and_predicted_travel_not_busy_truck(self):
        self.prepare()
        work = self.save(**self.readings()).fuel_work_state
        self.assertEqual(work.on_at_utc, self.now)
        confirmed = self.now+timedelta(minutes=5)
        self.confirm(now=confirmed)
        self.assertEqual(self.row(confirmed)["fueling_effective_on_utc"], confirmed)
        self.assertEqual(work.on_at_utc, self.now)
        self.assertEqual(work.manual_on_at_utc, confirmed)

    def test_manual_on_respects_busy_unavailable_and_stale_assignment(self):
        self.prepare()
        previous, previous_work = self.previous()
        with self.assertRaisesRegex(ValueError, "STILL FUELING"):
            self.confirm()
        db.session.rollback()
        previous_work.off_at_utc = self.now
        db.session.commit()
        self.assertTrue(self.row()["truck_start_reason"].startswith("COMING FROM"))
        self.assertTrue(self.confirm()[0])  # Physical confirmation beats estimated travel.
        self.assertEqual(self.row()["truck_start_reason"], "")
        selection = service.NeoScorpionSortTruck.query.filter_by(fuel_truck_id=self.truck.id).one()
        selection.status = "topping_off"
        db.session.commit()
        self.assignment.current_cycle_number += 1
        db.session.commit()
        with self.assertRaisesRegex(ValueError, "topping off"):
            self.confirm()
        db.session.rollback()
        selection.status = "available"
        db.session.commit()
        with self.assertRaisesRegex(ValueError, "Refresh"):
            service.confirm_fueler_truck_on(self.gateway, self.fueler, self.assignment.id,
                expected_cycle=1, expected_tail=self.mission.assigned_tail_number,
                expected_truck_id=self.truck.id, now_utc=self.now+timedelta(minutes=2))

    def test_truck_on_route_is_fueler_only_and_repeated_request_does_not_reset_time(self):
        self.prepare()
        url = f"/neoscorpion/fueler/assignments/{self.assignment.id}/on"
        data = dict(expected_cycle=1, expected_tail=self.mission.assigned_tail_number,
                    expected_truck_id=self.truck.id)
        self.login(self.dispatcher)
        denied = self.client.post(url, data=data, headers={"Accept": "application/json"})
        self.assertNotEqual(denied.status_code, 200)
        self.login(self.fueler)
        first = self.client.post(url, data=data, headers={"Accept": "application/json"})
        self.assertEqual(first.status_code, 200)
        self.assertTrue(first.json["ok"])
        work = NeoScorpionFuelWorkState.query.filter_by(fuel_assignment_id=self.assignment.id).one()
        first_time = work.manual_on_at_utc
        repeated = self.client.post(url, data=data, headers={"Accept": "application/json"})
        self.assertEqual(repeated.status_code, 200)
        self.assertTrue(repeated.json["ok"])
        self.assertEqual(work.manual_on_at_utc, first_time)
        self.assertEqual(NeoScorpionFuelAuditEntry.query.filter_by(action="truck_on").count(), 1)

    def row(self, now=None):
        assignments = service._assignments_by_mission(self.operation)
        return service._fuel_rows(self.operation, [self.mission], assignments_by_mission=assignments,
            fuel_work_states_by_assignment_tail=service._fuel_work_states_by_assignment_tail(assignments.values()),
            fuel_density_lbs_per_gallon=6.7,
            status_settings=self.settings, now_utc=now or self.now)[0]

    def previous(self):
        mission = self.fixture._mission("PREVIOUS", "N492UP", 52000, 0)
        assignment = self.fixture._assignment(mission, truck=self.truck)
        assignment.assigned_fueler_user_id = self.dispatcher.id
        assignment.created_at = self.now-timedelta(hours=1)
        work = NeoScorpionFuelWorkState(fuel_assignment_id=assignment.id, tail_number="N492UP",
            on_at_utc=self.now-timedelta(minutes=30), confirmed_on_at_utc=self.now-timedelta(minutes=30))
        db.session.add_all([work, SortDateParkingAssignment(sort_date_operation_id=self.operation.id,
                           tail_number="N492UP", ramp_code="A", position_code="A04")])
        db.session.flush()
        for code, lbs in (("left",12000),("ctr",0),("right",0)):
            db.session.add(NeoScorpionFuelTankState(fuel_work_state_id=work.id, tank_code=code,
                remaining_lbs=lbs, created_at=self.now-timedelta(minutes=31)))
        db.session.commit()
        return assignment, work

    def test_partial_or_truckless_non_fob_does_not_start_and_complete_valid_save_does(self):
        self.prepare(truck=False)
        work = self.save(**self.readings()).fuel_work_state
        self.assertIsNone(work.on_at_utc)
        row = self.row()
        self.assertEqual((row["dispatch_status_key"], row["fuel_status_secondary"]), ("ready", "Needs Truck"))
        self.truck = self.fixture._nightly_truck()
        self.assignment.assigned_truck_id = self.truck.id; db.session.commit()
        work = self.save(remaining_right="").fuel_work_state
        self.assertIsNone(work.on_at_utc)
        work = self.save(now=self.now+timedelta(minutes=1), **self.readings()).fuel_work_state
        self.assertEqual(work.on_at_utc, self.now+timedelta(minutes=1))
        self.assertEqual(work.confirmed_on_at_utc, work.on_at_utc)
        self.assertEqual(self.row(self.now+timedelta(minutes=1))["dispatch_status_key"], "fueling")
        again = self.save(now=self.now+timedelta(minutes=2), **self.readings())
        self.assertFalse(again.changed)
        self.assertEqual(again.fuel_work_state.on_at_utc, self.now+timedelta(minutes=1))

    def test_preassigned_truck_release_travel_and_expiry_never_auto_create_on(self):
        self.prepare(); previous, previous_work = self.previous()
        work = self.save(**self.readings()).fuel_work_state
        self.assertIsNone(work.on_at_utc)
        self.assertEqual(self.row()["dispatch_status_key"], "assigned")
        self.assertEqual(self.row()["fuel_status_secondary"], "TRUCK STILL FUELING")
        previous_work.off_at_utc = self.now; db.session.commit()
        row = self.row()
        self.assertEqual(row["truck_start_reason"], "COMING FROM ALPHA · ~2 MIN")
        self.assertEqual(row["truck_travel_until"], (self.now+timedelta(minutes=2)).isoformat()+"Z")
        self.save(now=self.now+timedelta(minutes=1), **self.readings())
        self.assertIsNone(work.on_at_utc)
        row = self.row(self.now+timedelta(minutes=3))
        self.assertEqual(row["truck_start_reason"], "TRUCK ON PENDING")
        self.assertEqual(row["dispatch_status_key"], "assigned")
        self.assertIsNone(work.on_at_utc)
        result = self.save(now=self.now+timedelta(minutes=3), **self.readings())
        self.assertTrue(result.changed)  # Same readings still confirm a real start.
        self.assertEqual(work.on_at_utc, self.now+timedelta(minutes=3))
        self.assertEqual(previous_work.off_at_utc, self.now)

    def test_legacy_premature_on_preserved_but_never_used_for_overdue_estimate(self):
        self.prepare(); previous, previous_work = self.previous()
        work = self.save(**self.readings()).fuel_work_state
        bad_on = self.now-timedelta(minutes=20)
        work.on_at_utc = bad_on; db.session.commit()
        row = self.row()
        self.assertFalse(row["fueling_on_trusted"])
        self.assertEqual(row["dispatch_status_key"], "assigned")
        self.assertIsNone(service.active_fueler_off(row, self.operation,
            service.assignment_planning_settings(self.gateway, settings=self.settings)))
        previous_work.off_at_utc = self.now; db.session.commit()
        self.save(now=self.now+timedelta(minutes=3), **self.readings())
        row = self.row(self.now+timedelta(minutes=3))
        self.assertEqual(work.on_at_utc, bad_on)
        self.assertEqual(row["fueling_effective_on_utc"], self.now+timedelta(minutes=3))
        finish = service.active_fueler_off(row, self.operation,
            service.assignment_planning_settings(self.gateway, settings=self.settings))
        self.assertAlmostEqual((finish-row["fueling_effective_on_utc"]).total_seconds()/60,
                          10+row["planning_demand_gallons"]/300, places=5)
        row["fueling_on_trusted"] = False
        self.assertIsNone(service.active_fueler_off(row, self.operation,
            service.assignment_planning_settings(self.gateway, settings=self.settings)))

    def test_identified_truckless_fob_verification_is_preserved(self):
        self.prepare(truck=False); self.mission.planned_fuel_load = 20000; db.session.commit()
        work = self.save(remaining_left="12", apu_running="no").fuel_work_state
        self.assertEqual(work.on_at_utc, self.now)
        row = self.row()
        self.assertEqual(row["dispatch_status_key"], "fueling")
        self.assertIn("FOB CHECK", row["fuel_status_warnings"])

    def test_legacy_legitimate_and_completed_timestamps_are_not_rewritten(self):
        self.prepare()
        work = self.save(**self.readings()).fuel_work_state
        for tank in work.tank_states:
            tank.created_at = self.now-timedelta(seconds=1)
        work.confirmed_on_at_utc = None; db.session.commit()
        self.assertTrue(self.row()["fueling_on_trusted"])
        self.save(now=self.now+timedelta(minutes=10), remaining_left="13")
        self.assertEqual(work.on_at_utc, self.now)
        self.assertIsNone(work.confirmed_on_at_utc)
        work.off_at_utc = self.now+timedelta(minutes=20)
        self.assignment.completed_at_utc = self.now+timedelta(minutes=25)
        self.assignment.review_status = "complete"; self.mission.fuel_status="complete"; db.session.commit()
        self.assertEqual(self.row(self.now+timedelta(minutes=30))["dispatch_status_key"], "complete")
        self.assertEqual(work.on_at_utc, self.now)
        self.assertEqual(work.off_at_utc, self.now+timedelta(minutes=20))

    def test_travel_math_unknown_location_and_nondefault_setting(self):
        self.prepare(); previous, previous_work = self.previous()
        previous_work.off_at_utc = self.now; db.session.commit()
        parking = SortDateParkingAssignment.query.filter_by(tail_number="N491UP").one()
        parking.ramp_code="E"; parking.position_code="E03"; db.session.commit()
        self.assertEqual(self.row()["truck_start_reason"], "COMING FROM ALPHA · ~8 MIN")
        self.settings.spear_truck_minutes_per_ramp_move = 3; db.session.commit()
        self.assertEqual(self.row()["truck_start_reason"], "COMING FROM ALPHA · ~12 MIN")
        parking.position_code="BAD"; db.session.commit()
        self.assertEqual(self.row()["truck_start_reason"], "TRUCK LOCATION UNKNOWN")

    def test_schema_marker_is_nullable_and_present_in_all_bootstrap_paths(self):
        self.assertTrue(NeoScorpionFuelWorkState.__table__.c.confirmed_on_at_utc.nullable)
        for mapping in (NEOSCORPION_ADDITIVE_COLUMNS, LOCAL_SQLITE_OPTIONAL_COLUMNS, POSTGRES_OPTIONAL_COLUMNS):
            self.assertIn("confirmed_on_at_utc", mapping["neoscorpion_fuel_work_states"])
            for field in ("manual_on_at_utc", "manual_on_by_user_id", "manual_on_truck_id", "manual_on_cycle_number"):
                self.assertIn(field, mapping["neoscorpion_fuel_work_states"])
            self.assertIn("inventory_verified", mapping["neoscorpion_sort_trucks"])
        self.assertEqual(NeoScorpionTruckInventoryAudit.__tablename__, "neoscorpion_truck_inventory_audits")

    def test_unavailable_truck_and_missing_fueler_cannot_start(self):
        self.prepare()
        selection = service.NeoScorpionSortTruck.query.filter_by(fuel_truck_id=self.truck.id).one()
        selection.status = "topping_off"; db.session.commit()
        work = self.save(**self.readings()).fuel_work_state
        self.assertIsNone(work.on_at_utc)
        self.assertEqual(self.row()["truck_start_reason"], "TRUCK UNAVAILABLE")
        self.assignment.assigned_fueler_user_id = None; db.session.commit()
        with self.assertRaisesRegex(ValueError, "not found"):
            self.save(**self.readings())
        self.assertIsNone(work.on_at_utc)

    def test_truck_changed_during_edit_conflicts_and_waiting_work_cannot_mark_off(self):
        self.prepare(); previous, previous_work = self.previous()
        work = self.save(**self.readings()).fuel_work_state
        self.save(actual_left="52",actual_ctr="0",actual_right="0",transfer_fuel_gallons="1000")
        with self.assertRaisesRegex(ValueError, "TRUCK ON"):
            service.mark_fueler_off(self.gateway,self.fueler,self.assignment.id,now_utc=self.now)
        self.assertIsNone(work.off_at_utc)
        self.assertFalse(self.row()["off_ready"])
        baseline = service.fueler_context(self.gateway,self.fueler)["rows"][0]["edit_baseline"]
        replacement = self.fixture._nightly_truck(number="21")
        self.assignment.assigned_truck_id = replacement.id; db.session.commit()
        with self.assertRaises(service.FuelerDataConflict):
            self.save(expected=json.dumps(baseline), **self.readings())
        self.assertIsNone(work.on_at_utc)

    def test_uncertain_legacy_readings_never_slide_a_legitimate_on_forward(self):
        self.prepare()
        work = self.save(**self.readings()).fuel_work_state
        work.confirmed_on_at_utc = None
        for tank in work.tank_states:
            tank.created_at = self.now-timedelta(seconds=1)
        work.tank_states[0].remaining_lbs = None; db.session.commit()
        row = self.row(self.now+timedelta(minutes=60))
        self.assertEqual(row["dispatch_status_key"], "fueling")
        self.assertFalse(row["fueling_on_trusted"])
        self.save(now=self.now+timedelta(minutes=60), **self.readings())
        self.assertEqual(work.on_at_utc,self.now)
        self.assertIsNone(work.confirmed_on_at_utc)
        row = self.row(self.now+timedelta(minutes=60))
        finish = service.active_fueler_off(row,self.operation,service.assignment_planning_settings(self.gateway,settings=self.settings))
        self.assertLess(finish,self.now+timedelta(minutes=60))
