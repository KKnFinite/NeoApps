"""Fueler APU changes refresh real Dispatch projections without changing EST."""
from datetime import date, datetime, timedelta
import unittest
import re
from unittest.mock import patch

from playwright.sync_api import expect

from app.extensions import db
from app.models import (
    NeoScorpionFuelAssignment, NeoScorpionFuelTruck, NeoScorpionFuelWorkState,
    NeoScorpionSortAssetState, NeoScorpionSortFueler, NeoScorpionSortTruck,
    NeoScorpionTailFuelState, SortDateMission, SortDateOperation, SortDateTailState, User,
)
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.neoscorpion import _effective_apu_allowance_lbs, lbs_to_gallons
from app.services.neoscorpion_assets import record_nightly_operational_change
from tests.browser.test_mobile_drawer import MobileDrawerBrowserTest as Fixture


class ApuTruckProjectionsBrowserTest(unittest.TestCase):
    def test_fueler_recommended_manual_off_and_tf_live_projections(self):
        Fixture.setUpClass()
        Fixture.app.config["LIVE_SCREEN_REFRESH_INTERVAL_MS"] = 5000
        browser = None
        try:
            with Fixture.app.app_context():
                gateway = ensure_default_gateway_and_nodes()
                admin = User.query.filter_by(username="drawer-admin").one()
                operation = SortDateOperation(generated_by_user_id=admin.id, gateway_id=gateway.id,
                    gateway_code=gateway.code, sort_date=date.today(), sort_name="night", window_minutes=60)
                truck = NeoScorpionFuelTruck(gateway_id=gateway.id, truck_number="20",
                    capacity_gallons=20_000, remaining_fuel_gallons=10_000)
                db.session.add_all([operation, truck])
                db.session.flush()
                db.session.add_all([
                    NeoScorpionSortTruck(sort_date_operation_id=operation.id, fuel_truck_id=truck.id,
                        status="available", starting_gallons=10_000, current_gallons=10_000),
                    NeoScorpionSortFueler(sort_date_operation_id=operation.id, user_id=admin.id),
                    NeoScorpionSortAssetState(sort_date_operation_id=operation.id, revision=1),
                ])
                assignments = []
                for index in range(2):
                    tail = f"N49{index}UP"
                    mission = SortDateMission(sort_date_operation_id=operation.id, sort_date=operation.sort_date,
                        gateway_code=gateway.code, sort_name="night", mission_type="departure",
                        mission_source="manual", flight_number=f"UPS90{index}", origin=gateway.code,
                        destination="SDF", assigned_tail_number=tail, tail_source="manual",
                        timezone="America/Chicago", planned_fuel_load=25_400,
                        planned_datetime_utc=datetime.utcnow() + timedelta(hours=2, minutes=index),
                        planned_source="manual", departure_status="loading", fuel_status="waiting")
                    db.session.add(mission)
                    db.session.add(SortDateTailState(sort_date=operation.sort_date, gateway_code=gateway.code,
                        sort_name="night", tail_number=tail, aircraft_type="757", aircraft_type_source="derived"))
                    db.session.add(NeoScorpionTailFuelState(sort_date_operation_id=operation.id,
                        tail_number=tail, inbound_fuel_lbs=12_000))
                    db.session.flush()
                    assignment = NeoScorpionFuelAssignment(sort_date_operation_id=operation.id,
                        sort_date_mission_id=mission.id, confirmed_tail_number=tail,
                        assigned_truck_id=truck.id, assigned_fueler_user_id=admin.id)
                    db.session.add(assignment)
                    assignments.append(assignment)
                db.session.commit()
                operation_id, assignment_id = operation.id, assignments[0].id

            with patch("app.services.neoscorpion.current_existing_operational_sort_operations",
                    side_effect=lambda gateway, now=None: [db.session.get(SortDateOperation, operation_id)]):
                browser = Fixture.pw.chromium.launch()
                context = browser.new_context(viewport={"width": 1440, "height": 900})
                dispatch = context.new_page()
                Fixture().login(dispatch)
                dispatch.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1', 'seen')")
                requests, errors = [], []
                dispatch.on("request", lambda request: requests.append(request.url))
                dispatch.on("pageerror", lambda error: errors.append(str(error)))
                Fixture().ready(dispatch, "/neoscorpion/fuel-dispatch")
                truck_card = dispatch.locator(".neoscorpion-truck-visual-card").first
                mission_bars = dispatch.locator(".neoscorpion-mission-truck-bars")

                def check_projection(allowance_gallons, *, first_demand=2000):
                    projected = 10_000 - first_demand - 2000 - allowance_gallons
                    expect(truck_card).to_contain_text(f"Projected {projected:,} gal", timeout=20000)
                    expect(truck_card).to_contain_text("Current 10,000 gal / 50%")
                    expect(mission_bars.nth(0)).to_have_attribute("aria-label", re.compile(
                        f"projected after this mission {10_000 - first_demand - allowance_gallons:,} gal"))
                    expect(mission_bars.nth(1)).to_have_attribute("aria-label", re.compile(
                        f"projected after this mission {projected:,} gal"))
                    percent = int((projected * 100 / 20_000) + 0.5)
                    expect(truck_card.locator(".neoscorpion-truck-gauge--projected")).to_have_attribute("value", str(percent))
                    expect(dispatch.locator(".neoscorpion-dispatch-transfer-estimate small")).to_have_text(
                        ["EST 2,000 gal", "EST 2,000 gal"])
                    expect(dispatch.locator('[data-sort-fuel-total="estimated"] strong')).to_have_text("4,000 GAL")
                    expect(dispatch.locator('[data-sort-fuel-total="required"] strong')).to_have_text("7,582 GAL")

                check_projection(0)
                fueler = context.new_page()
                fueler.on("pageerror", lambda error: errors.append(str(error)))
                Fixture().ready(fueler, "/neoscorpion/fueler")
                card = fueler.locator(f'[data-fuel-assignment-id="{assignment_id}"]')
                form = card.locator("[data-fuel-planning-form]")

                def save_entry():
                    with fueler.expect_response(lambda response: response.request.method == "POST"
                            and f"/fueler/assignments/{assignment_id}" in response.url) as response:
                        form.get_by_role("button", name="Save Fuel Entry", exact=True).click()
                    self.assertEqual(response.value.status, 200, response.value.text())
                    expect(card.locator("[data-fuel-data-status]")).to_contain_text("Saved")

                form.locator("[data-apu-running]").select_option("yes")
                form.locator("[data-apu-source]").select_option("left")
                form.locator("[data-apu-use-recommended]").click()
                save_entry()
                with Fixture.app.app_context():
                    work = NeoScorpionFuelWorkState.query.filter_by(fuel_assignment_id=assignment_id).one()
                    recommended = lbs_to_gallons(_effective_apu_allowance_lbs(work), 6.7)
                    self.assertGreater(recommended, 0)
                check_projection(recommended)

                form.locator("[data-apu-override-value]").fill("1.34")
                form.locator("[data-apu-use-manual]").click()
                save_entry()
                check_projection(200)
                dispatch.screenshot(path=str(Fixture.evidence / "dispatch-apu-projection.png"), full_page=True)

                form.locator("[data-apu-running]").select_option("no")
                save_entry()
                check_projection(0)

                form.locator("[data-apu-running]").select_option("yes")
                form.locator("[data-apu-source]").select_option("left")
                form.locator("[data-apu-override-value]").fill("1.34")
                form.locator("[data-apu-use-manual]").click()
                save_entry()
                check_projection(200)
                with Fixture.app.app_context():
                    assignment = db.session.get(NeoScorpionFuelAssignment, assignment_id)
                    assignment.transfer_fuel_gallons = 2500
                    state = NeoScorpionSortAssetState.query.filter_by(sort_date_operation_id=operation_id).one()
                    record_nightly_operational_change(state, operation_id)
                    db.session.commit()
                check_projection(0, first_demand=2500)
                self.assertTrue(any("live-panel" in url for url in requests))
                self.assertEqual(requests.count(Fixture.origin + "/neoscorpion/fuel-dispatch"), 1)
                self.assertFalse(errors)
                with Fixture.app.app_context():
                    self.assertEqual(NeoScorpionSortTruck.query.one().current_gallons, 10_000)
                    self.assertEqual(NeoScorpionFuelTruck.query.one().remaining_fuel_gallons, 10_000)
        finally:
            if browser:
                browser.close()
            Fixture.tearDownClass()
