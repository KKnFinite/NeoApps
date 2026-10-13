"""Read-only physical OFF estimates reuse SPEAR's pump component."""
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from jinja2 import Environment, FileSystemLoader

from app.services.neoscorpion import _attach_dispatch_off_estimates
from app.services.neoscorpion_spear import active_fueler_off, active_fueling_finish
from app.services.neoscorpion_spear_calibration import calibrated_planning_settings
from tests import test_neoscorpion_spear as fixtures
from tests import test_neoscorpion_fueling_status as http_fixtures
from tests.html_contracts import document
from app.extensions import db
from app.models import NeoScorpionFuelWorkState, NeoScorpionFuelingEvent, NeoScorpionSortFueler
from app.services import neoscorpion as service


NOW = datetime(2026, 10, 14, 4, 34)


class OffProjectionTest(unittest.TestCase):
    def row(self, *, demand=1200, aircraft="B757"):
        row = fixtures._row(demand=demand)
        row.update(dispatch_status_key="fueling", dispatch_status_label="FUELING",
            assignment=SimpleNamespace(assigned_fueler_user_id=1, assigned_truck_id=10),
            fuel_work_state=SimpleNamespace(on_at_utc=NOW, off_at_utc=None),
            detailed_aircraft_type=aircraft, fuel_status_secondary="", cycle_type="fuel")
        return row

    def planning(self, **rates):
        planning = fixtures._PlanningSettings()
        planning.pump_rates_gallons_per_minute = {"B757": Decimal(100), "A300": Decimal(200), **rates}
        planning.pump_rate_for = lambda kind: planning.pump_rates_gallons_per_minute.get(kind)
        return planning

    def attach(self, row, *, mode="observe", profiles=None, calibrations=None, now=NOW):
        context = dict(rows=[row], operation=SimpleNamespace(id=1),
            spear_settings=SimpleNamespace(live_calibration_mode=mode), fueling_performance=profiles or {})
        with patch("app.services.neoscorpion.gateway_timezone", return_value="America/Chicago"):
            _attach_dispatch_off_estimates(context, SimpleNamespace(id=1), self.planning(),
                                          calibrations or {}, now_utc=now)
        return row

    def test_physical_off_excludes_pre_on_setup_and_post_off_completion(self):
        row = self.row()
        off = active_fueler_off(row, SimpleNamespace(id=1), self.planning())
        self.assertEqual(off, NOW+timedelta(minutes=12))
        complete = active_fueling_finish(row, SimpleNamespace(id=1), self.planning())
        self.assertEqual(complete-off, timedelta(minutes=10))
        before = dict(row)
        self.attach(row)
        self.assertEqual(row["fueler_off_estimate_display"], "EST OFF 23:46 · ~12 MIN")
        self.assertEqual(row["fueler_off_estimate_utc"], "2026-10-14T04:46:00Z")
        for key, value in before.items():
            self.assertEqual(row[key], value)

    def test_different_aircraft_rates_and_missing_rate_or_demand(self):
        for aircraft, minutes in (("B757", 12), ("A300", 6)):
            self.assertEqual(active_fueler_off(self.row(aircraft=aircraft), SimpleNamespace(), self.planning()),
                             NOW+timedelta(minutes=minutes))
        for rate in (None, Decimal(0), Decimal(-1)):
            self.assertIsNone(active_fueler_off(self.row(), SimpleNamespace(), self.planning(B757=rate)))
        self.assertIsNone(active_fueler_off(self.row(demand=None), SimpleNamespace(), self.planning()))

    def test_recorded_on_anchors_physical_timing_without_etd_or_arrival_timestamp(self):
        row = self.row()
        row["mission"].planned_datetime_utc = None
        self.assertEqual(active_fueler_off(row, SimpleNamespace(), self.planning()), NOW+timedelta(minutes=12))
        row["arrival_mission"] = None
        self.assertEqual(active_fueler_off(row, SimpleNamespace(), self.planning()), NOW+timedelta(minutes=12))
        row["fuel_work_state"].on_at_utc = None
        self.assertIsNone(active_fueler_off(row, SimpleNamespace(), self.planning()))
        self.attach(row)
        self.assertEqual(row["fueler_off_estimate_display"], "TIMING UNKNOWN")

    def test_observe_apply_aircraft_calibration_and_profiles_do_not_multiply_adjustments(self):
        calibrated = {("pump_rate", "B757"): SimpleNamespace(active=True, effective=Decimal(200))}
        for mode, profiles, minutes in (
            ("observe", {}, 12), ("apply", {}, 6),
            ("observe", {"fuelers": [{"id":1,"jobs":3,"prof":50}]}, 12),
            ("apply", {"fuelers": [{"id":1,"jobs":2,"prof":50}]}, 6),
            ("apply", {"fuelers": [{"id":1,"jobs":3,"prof":None}]}, 6),
            ("apply", {"fuelers": [{"id":1,"jobs":3,"prof":0}]}, 6),
            ("apply", {"fuelers": [{"id":1,"jobs":3,"prof":50}]}, 24),
            ("apply", {"trucks": [{"id":10,"jobs":3,"prof":50}]}, 24),
            ("apply", {"fuelers": [{"id":1,"jobs":3,"prof":200}],
                       "trucks": [{"id":10,"jobs":3,"prof":50}]}, 24),
        ):
            with self.subTest(mode=mode, profiles=profiles):
                row = self.attach(self.row(), mode=mode, profiles=profiles, calibrations=calibrated)
                self.assertEqual(row["fueler_off_estimate_utc"], (NOW+timedelta(minutes=minutes)).isoformat()+"Z")

    def test_overdue_zero_valid_unknown_and_off_remove_projection(self):
        row = self.attach(self.row(), now=NOW+timedelta(minutes=12))
        self.assertEqual(row["fueler_off_estimate_display"], "EST OFF OVERDUE")
        self.assertNotIn("-", row["fueler_off_estimate_display"])
        self.assertEqual(self.attach(self.row(demand=0))["fueler_off_estimate_display"], "EST OFF OVERDUE")
        row["fuel_work_state"].off_at_utc = NOW+timedelta(minutes=12)
        row["dispatch_status_key"] = "off"
        self.assertIsNone(active_fueler_off(row, SimpleNamespace(), self.planning()))
        self.attach(row)
        self.assertEqual(row["fueler_off_estimate_utc"], "")

    def test_template_only_dispatch_fueling_one_line_warning_priority_and_actual_off(self):
        env = Environment(loader=FileSystemLoader(Path(__file__).resolve().parents[1]/"app/templates"))
        template = env.from_string('{% from "neonodes/neoscorpion/_fuel_status.html" import fuel_status %}{{ fuel_status(row, dispatcher=dispatcher, can_ack=true) }}')
        row = self.attach(self.row())
        html = template.render(row=row, dispatcher=True)
        self.assertIn("EST OFF 23:46 · ~12 MIN", html)
        self.assertEqual(html.count("data-fuel-status-secondary"), 1)
        self.assertNotIn("data-fueler-off-estimate", template.render(row=row, dispatcher=False))
        for warning in ("Fuel direction discrepancy", "Dispatcher review requested", "FOB CHECK", "FOB LIKELY"):
            row["fuel_status_secondary"] = warning
            self.assertNotIn("data-fueler-off-estimate", template.render(row=row, dispatcher=True))
        row["call_dispatch_alert"] = True
        self.assertIn("CALL DISPATCH", template.render(row=row, dispatcher=True))
        self.assertNotIn("data-fueler-off-estimate", template.render(row=row, dispatcher=True))
        row.update(call_dispatch_alert=None, fuel_status_secondary="", dispatch_status_key="off", off_time="23:46")
        html = template.render(row=row, dispatcher=True)
        self.assertIn("OFF 23:46", html)
        self.assertNotIn("data-fueler-off-estimate", html)
        self.assertNotIn("OFF 23:46", template.render(row=row, dispatcher=False))


class OffProjectionHttpTest(unittest.TestCase):
    setUp = http_fixtures.FuelingStatusIntegrationTest.setUp
    login = http_fixtures.FuelingStatusIntegrationTest.login
    work = http_fixtures.FuelingStatusIntegrationTest.work
    row = http_fixtures.FuelingStatusIntegrationTest.row

    def test_live_initial_unknown_off_and_canonical_performance_apply(self):
        truck = self.fixture._nightly_truck()
        self.assignment.assigned_truck_id = truck.id
        self.assignment.assigned_fueler_user_id = self.fueler.id
        self.mission.planned_fuel_load = 52000
        work = self.work(remaining=12000, actual=None, apu=False)
        work.on_at_utc = NOW
        db.session.add(NeoScorpionSortFueler(sort_date_operation_id=self.operation.id, user_id=self.fueler.id))
        for i, tail in enumerate(("N492UP", "N493UP", "N494UP")):
            mission = self.fixture._mission(f"UPS-H{i}", tail, 52000, i)
            mission.fuel_status = "complete"
            assignment = self.fixture._assignment(mission, truck=truck)
            assignment.assigned_fueler_user_id = self.fueler.id
            assignment.completed_at_utc = NOW-timedelta(hours=i+1)
            assignment.review_status = "complete"
            start = NOW-timedelta(hours=i+2)
            end = start+timedelta(minutes=40)
            history_work = NeoScorpionFuelWorkState(fuel_assignment_id=assignment.id,
                tail_number=tail, on_at_utc=start, off_at_utc=end)
            db.session.add(history_work); db.session.flush()
            db.session.add(NeoScorpionFuelingEvent(sort_date_operation_id=self.operation.id,
                fuel_assignment_id=assignment.id, fuel_work_state_id=history_work.id, tail_number=tail,
                fuel_truck_id=truck.id, fueler_user_id=self.fueler.id, sequence_number=1,
                event_type="fuel", started_at_utc=start, ended_at_utc=end, transfer_fuel_gallons=6000))
        self.settings.spear_live_calibration_mode = "apply"
        db.session.commit()
        context = service.fuel_dispatch_context(self.gateway)
        profile = next(item for item in context["fueling_performance"]["fuelers"] if item["id"] == self.fueler.id)
        self.assertEqual((profile["jobs"], profile["prof"]), (3, 50))
        row = next(row for row in context["rows"] if row["mission"].id == self.mission.id)
        finish = datetime.fromisoformat(row["fueler_off_estimate_utc"].removesuffix("Z"))
        self.assertAlmostEqual((finish-NOW).total_seconds()/60, row["planning_demand_gallons"]/150, places=5)
        # Existing completion estimate still uses the shared aircraft calibration.
        completion = row["fueling_predicted_finish_utc"]
        calibrated = {(item.metric, item.scope_key):item for item in context["spear_calibration"]["items"]}
        effective = calibrated_planning_settings(service.assignment_planning_settings(self.gateway, settings=self.settings),
                                                calibrated, mode="apply")
        self.assertEqual(completion, active_fueling_finish(row, self.operation, effective))
        self.settings.spear_live_calibration_mode = "observe"
        observe = self.row()
        self.assertNotEqual(observe["fueler_off_estimate_utc"], row["fueler_off_estimate_utc"])
        self.assertIsNone(self.assignment.transfer_fuel_gallons)
        self.assertEqual(self.mission.planned_fuel_load, 52000)
        self.assertIsNone(self.assignment.completed_at_utc)
        self.assertIsNone(work.off_at_utc)
        self.login(self.dispatcher)
        response = self.client.get("/neoscorpion/fuel-dispatch")
        active = document(response).one(**{"data-dispatch-mission-id":str(self.mission.id)})
        self.assertEqual(len(active.findall("td")), 16)
        self.assertEqual(active.one(**{"data-fueler-off-estimate": None}).attrs["data-fueler-off-estimate"], observe["fueler_off_estimate_utc"])
        live = self.client.get("/neoscorpion/fuel-dispatch/live-panel")
        self.assertEqual(live.status_code, 200)
        self.assertIn(observe["fueler_off_estimate_utc"], live.json["html"])
        self.mission.planned_fuel_load = None; db.session.commit()
        unknown = self.client.get("/neoscorpion/fuel-dispatch/live-panel")
        self.assertIn('data-fueler-off-estimate=""', unknown.json["html"])
        work.off_at_utc = NOW+timedelta(minutes=25); db.session.commit()
        off = document(self.client.get("/neoscorpion/fuel-dispatch")).one(**{"data-dispatch-mission-id":str(self.mission.id)})
        self.assertFalse(off.findall(**{"data-fueler-off-estimate":None}))
        self.assertIn("OFF ", off.one(**{"data-fuel-status-secondary":None}).text)
