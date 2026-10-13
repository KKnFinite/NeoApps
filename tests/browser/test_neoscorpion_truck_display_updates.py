"""Painted desktop/mobile cards, menu ordering and real existing live refresh."""
import unittest
from datetime import date, datetime, timedelta
from unittest.mock import patch

from playwright.sync_api import expect

from app.extensions import db
from app.models import (NeoScorpionFuelAssignment, NeoScorpionFuelTruck, NeoScorpionFuelWorkState,
                        NeoScorpionSortAssetState, NeoScorpionSortTruck, SortDateMission,
                        SortDateOperation, SortDateParkingAssignment, SortDateTailState, User)
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.neoscorpion_assets import record_nightly_operational_change
from tests.browser import test_mobile_drawer as fixture_module


class TruckDisplayUpdatesBrowserTest(unittest.TestCase):
    def test_rendered_views_and_live_top_off_preserve_dirty_fueler_input(self):
        fixture = fixture_module.MobileDrawerBrowserTest
        fixture.setUpClass()
        fixture.app.config["LIVE_SCREEN_REFRESH_INTERVAL_MS"] = 5000
        browser = None
        try:
            with fixture.app.app_context():
                gateway = ensure_default_gateway_and_nodes()
                admin = User.query.filter_by(username="drawer-admin").one()
                other = User.query.filter_by(username="drawer-watcher").one()
                operation = SortDateOperation(generated_by_user_id=admin.id, gateway_id=gateway.id,
                    gateway_code=gateway.code, sort_date=date.today(), sort_name="night", window_minutes=60)
                truck = NeoScorpionFuelTruck(gateway_id=gateway.id, truck_number="20", capacity_gallons=20000)
                db.session.add_all([operation, truck]); db.session.flush()
                selection = NeoScorpionSortTruck(sort_date_operation_id=operation.id, fuel_truck_id=truck.id,
                    status="available", starting_gallons=10000, current_gallons=10000)
                db.session.add_all([selection, NeoScorpionSortAssetState(sort_date_operation_id=operation.id, revision=1)])
                work_ids = []
                for i, (tail, user) in enumerate((("N491UP", admin), ("N492UP", other))):
                    mission = SortDateMission(sort_date_operation_id=operation.id, sort_date=operation.sort_date,
                        gateway_code=gateway.code, sort_name="night", mission_type="departure", mission_source="manual",
                        flight_number=f"UPS90{i}", origin=gateway.code, destination="SDF", timezone="America/Chicago",
                        planned_datetime_utc=datetime.now()+timedelta(hours=2), assigned_tail_number=tail,
                        planned_fuel_load=25000, fuel_status="assigned")
                    db.session.add(mission); db.session.flush()
                    assignment = NeoScorpionFuelAssignment(sort_date_operation_id=operation.id, sort_date_mission_id=mission.id,
                        assigned_fueler_user_id=user.id, assigned_truck_id=truck.id, confirmed_tail_number=tail)
                    db.session.add(assignment); db.session.flush()
                    work = NeoScorpionFuelWorkState(fuel_assignment_id=assignment.id, tail_number=tail,
                        on_at_utc=datetime.now() if i else None, apu_running=True,
                        automatic_apu_allowance_lbs=500, apu_allowance_lbs=500)
                    db.session.add_all([work, SortDateTailState(sort_date=operation.sort_date, gateway_code=gateway.code,
                        sort_name="night", tail_number=tail, aircraft_type="757", aircraft_type_source="derived"),
                        SortDateParkingAssignment(sort_date_operation_id=operation.id, tail_number=tail,
                        ramp_code="A", position_code=f"A{3+i}")])
                    db.session.flush(); work_ids.append(work.id)
                db.session.commit()
                operation_id, selection_id = operation.id, selection.id

            with patch("app.services.neoscorpion.current_existing_operational_sort_operations",
                       side_effect=lambda gateway, now=None: [db.session.get(SortDateOperation, operation_id)]):
                browser = fixture.pw.chromium.launch()
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                fixture().login(page)
                page.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1', 'seen')")
                fixture().ready(page, "/neoscorpion/fueler")
                status = page.locator("[data-assigned-truck-status]")
                expect(status).to_have_text("CURRENTLY FUELING ON A4")
                for width in (1440, 390):
                    page.set_viewport_size({"width": width, "height": 1000})
                    metrics = status.evaluate("""el => {
                        const truck = el.previousElementSibling.getBoundingClientRect(), value = el.getBoundingClientRect();
                        return {below:value.top >= truck.bottom-1, fits:el.scrollWidth <= el.clientWidth+1,
                            font:parseFloat(getComputedStyle(el).fontSize)};
                    }""")
                    self.assertTrue(metrics["below"]); self.assertTrue(metrics["fits"])
                    self.assertLessEqual(metrics["font"], 14)
                reading = page.locator('[data-fuel-reading="remaining"]').first
                original = reading.input_value()
                reading.fill("9.9")
                with fixture.app.app_context():
                    db.session.get(NeoScorpionFuelWorkState, work_ids[1]).off_at_utc = datetime.now()
                    selection = db.session.get(NeoScorpionSortTruck, selection_id)
                    selection.status = "topping_off"
                    selection.top_off_sent_at_utc = datetime(2026, 10, 14, 3, 42)
                    state = NeoScorpionSortAssetState.query.filter_by(sort_date_operation_id=operation_id).one()
                    record_nightly_operational_change(state, operation_id)
                    db.session.commit()
                # Allow the existing revision poll to run while the form is dirty.
                with page.expect_response(lambda response: "/fuel-assignments/revision" in response.url, timeout=15000):
                    page.locator(".neoscorpion-fueler-truck").click()
                expect(reading).to_have_value("9.9")
                expect(status).to_have_text("CURRENTLY FUELING ON A4")
                reading.fill(original)
                page.locator(".neoscorpion-fueler-truck").click()
                expect(status).to_have_text("CURRENTLY TOPPING OFF", timeout=20000)
                expect(page.locator("[data-assigned-truck-top-off-sent]")).to_have_text("TOP OFF SENT 22:42")

                for width in (1440, 390):
                    page.set_viewport_size({"width": width, "height": 1000})
                    fixture().ready(page, "/neoscorpion/fuel-dispatch")
                    row = page.locator("tr[data-dispatch-mission-id]").first
                    expect(row.locator("[data-dispatch-actual]")).to_have_text("ACTUAL: -")
                    expect(row.locator("[data-dispatch-apu-effective]")).to_have_text("APU: 0.5")
                    self.assertEqual(row.locator("td").count(), 16)
                    self.assertEqual(row.locator("td:visible").count(), 16 if width == 1440 else 5)
                    if width == 1440:
                        metrics = row.locator(".neoscorpion-dispatch-actual-apu").evaluate("""el => ({
                            font:parseFloat(getComputedStyle(el.firstElementChild).fontSize),
                            apuFont:parseFloat(getComputedStyle(el.querySelector('[data-dispatch-apu-effective]')).fontSize),
                            fits:el.scrollWidth <= el.clientWidth+1,
                            display:getComputedStyle(el).display})""")
                        self.assertEqual(metrics["font"], metrics["apuFont"])
                        self.assertLessEqual(metrics["font"], 14); self.assertTrue(metrics["fits"])
                        self.assertEqual(metrics["display"], "table-cell")
                    self.assertFalse(page.evaluate("document.documentElement.scrollWidth > innerWidth + 1"))
                    labels = page.locator(".neoscorpion-screen-menu a").all_text_contents()
                    labels = [label.strip() for label in labels]
                    self.assertEqual(labels[labels.index("Fueler") + 1], "Quick Fuel")
                    fixture().ready(page, "/neoscorpion")
                    labels = page.locator(".neoscorpion-dashboard-grid a strong").all_text_contents()
                    self.assertEqual(labels[labels.index("Fueler") + 1], "Quick Fuel")
        finally:
            if browser:
                browser.close()
            fixture.tearDownClass()
