"""Rendered Dispatch totals refresh in place and agree with the Fuel Report."""
from datetime import date, datetime, timedelta
from io import BytesIO
import unittest
from unittest.mock import patch

from pypdf import PdfReader
from playwright.sync_api import expect

from app.extensions import db
from app.models import (NeoScorpionFuelAssignment, NeoScorpionFuelCycleHistory,
    NeoScorpionFuelingEvent, NeoScorpionFuelTruck, NeoScorpionFuelWorkState,
    NeoScorpionSortAssetState, NeoScorpionSortFueler, NeoScorpionSortTruck,
    NeoScorpionTailFuelState, SortDateMission, SortDateOperation, SortDateTailState, User)
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.neoscorpion_assets import record_nightly_operational_change
from tests.browser.test_mobile_drawer import MobileDrawerBrowserTest as Fixture


class DispatchSortFuelTotalsBrowserTest(unittest.TestCase):
    def test_responsive_totals_live_refresh_and_report_pdf(self):
        Fixture.setUpClass()
        Fixture.app.config["LIVE_SCREEN_REFRESH_INTERVAL_MS"] = 5000
        browser = None
        try:
            with Fixture.app.app_context():
                gateway = ensure_default_gateway_and_nodes()
                admin = User.query.filter_by(username="drawer-admin").one()
                operation = SortDateOperation(generated_by_user_id=admin.id, gateway_id=gateway.id,
                    gateway_code=gateway.code, sort_date=date.today(), sort_name="night", window_minutes=60)
                truck = NeoScorpionFuelTruck(gateway_id=gateway.id, truck_number="20", capacity_gallons=20_000)
                db.session.add_all([operation, truck])
                db.session.flush()
                db.session.add_all([
                    NeoScorpionSortTruck(sort_date_operation_id=operation.id, fuel_truck_id=truck.id,
                        status="available", starting_gallons=10_000, current_gallons=10_000),
                    NeoScorpionSortFueler(sort_date_operation_id=operation.id, user_id=admin.id),
                    NeoScorpionSortAssetState(sort_date_operation_id=operation.id, revision=1),
                ])
                missions = []
                for index, required in enumerate((25_400, 32_100)):
                    tail = f"N49{index}UP"
                    mission = SortDateMission(sort_date_operation_id=operation.id, sort_date=operation.sort_date,
                        gateway_code=gateway.code, sort_name="night", mission_type="departure",
                        mission_source="manual", flight_number="UPS901", origin=gateway.code, destination="SDF",
                        assigned_tail_number=tail, tail_source="manual", timezone="America/Chicago",
                        planned_fuel_load=required, planned_datetime_utc=datetime.utcnow() + timedelta(hours=2),
                        planned_source="manual", departure_status="loading", fuel_status="waiting")
                    db.session.add(mission)
                    db.session.add(SortDateTailState(sort_date=operation.sort_date, gateway_code=gateway.code,
                        sort_name="night", tail_number=tail, aircraft_type="757", aircraft_type_source="derived"))
                    db.session.add(NeoScorpionTailFuelState(sort_date_operation_id=operation.id,
                        tail_number=tail, inbound_fuel_lbs=12_000, fob_lbs=12_000))
                    missions.append(mission)
                db.session.flush()
                assignment = NeoScorpionFuelAssignment(sort_date_operation_id=operation.id,
                    sort_date_mission_id=missions[0].id, confirmed_tail_number=missions[0].assigned_tail_number,
                    assigned_truck_id=truck.id, assigned_fueler_user_id=admin.id,
                    current_cycle_number=2, transfer_fuel_gallons=200)
                db.session.add(assignment)
                db.session.flush()
                work = NeoScorpionFuelWorkState(fuel_assignment_id=assignment.id,
                                                tail_number=missions[0].assigned_tail_number)
                db.session.add(work)
                db.session.flush()
                event_values = dict(sort_date_operation_id=operation.id, fuel_assignment_id=assignment.id,
                    fuel_work_state_id=work.id, tail_number=work.tail_number, fuel_truck_id=truck.id,
                    fueler_user_id=admin.id, required_fuel_lbs=999_999)
                for sequence, kind, gallons, cycle in ((1, "fuel", 1500, 1), (2, "uplift", 500, 1),
                                                      (3, "defuel", 200, 2)):
                    db.session.add(NeoScorpionFuelingEvent(**event_values, sequence_number=sequence,
                        event_type=kind, transfer_fuel_gallons=gallons, cycle_number=cycle))
                db.session.add(NeoScorpionFuelCycleHistory(sort_date_operation_id=operation.id,
                    fuel_assignment_id=assignment.id, mission_id=missions[0].id, cycle_number=1,
                    label="COMPLETED", snapshot={"required_fuel_lbs": 999_999,
                        "transfer_fuel_gallons": 999_999, "estimated_fuel_gallons": 999_999}))
                db.session.commit()
                operation_id, second_id = operation.id, missions[1].id

            with patch("app.services.neoscorpion.current_existing_operational_sort_operations",
                    side_effect=lambda gateway, now=None: [db.session.get(SortDateOperation, operation_id)]):
                browser = Fixture.pw.chromium.launch()
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                Fixture().login(page)
                page.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1', 'seen')")
                requests = []
                errors = []
                page.on("request", lambda request: requests.append((request.method, request.url)))
                page.on("pageerror", lambda error: errors.append(str(error)))
                Fixture().ready(page, "/neoscorpion/fuel-dispatch")
                performance = page.locator("[data-fueling-performance]")
                def check_totals(scope, values):
                    for key, value in zip(("estimated", "transfer", "required"), values):
                        expect(scope.locator(f'[data-sort-fuel-total="{key}"] strong')).to_have_text(value,
                                                                                                      timeout=20000)
                check_totals(performance, ("5,000 GAL", "2,200 GAL", "57.5 K LBS"))
                expect(performance.locator("[data-performance-fueler-id]")).to_have_count(1)
                expect(performance.locator("[data-performance-truck-id]")).to_have_count(1)
                self.assertTrue(performance.evaluate("""panel => {
                    const title = panel.querySelector('h2').getBoundingClientRect();
                    const totals = panel.querySelector('.neoscorpion-sort-fuel-totals').getBoundingClientRect();
                    return title.right <= totals.left && Math.abs(title.top - totals.top) < 20;
                }"""), "Desktop title and totals must share a header row")
                self.assertIn("20261008-dispatch-check-excess-v1",
                              page.locator('link[href*="26-neoscorpion.css"]').get_attribute("href"))
                for width in (1440, 1024, 390, 375):
                    page.set_viewport_size({"width": width, "height": 900})
                    self.assertTrue(performance.evaluate("""panel => {
                        const box = panel.getBoundingClientRect();
                        return box.left >= 0 && box.right <= window.innerWidth &&
                            [...panel.querySelectorAll('[data-sort-fuel-total]')].every(stat => {
                                const rect = stat.getBoundingClientRect();
                                return rect.left >= box.left && rect.right <= box.right;
                            });
                    }"""), f"Totals must fit at {width}px")
                    if width in (1440, 390):
                        page.screenshot(path=str(Fixture.evidence / f"dispatch-sort-totals-{width}.png"), full_page=True)
                with Fixture.app.app_context():
                    db.session.get(SortDateMission, second_id).planned_fuel_load = 38_800
                    db.session.add(NeoScorpionFuelingEvent(**event_values, sequence_number=4,
                        event_type="uplift", transfer_fuel_gallons=800, cycle_number=2))
                    state = NeoScorpionSortAssetState.query.filter_by(sort_date_operation_id=operation_id).one()
                    record_nightly_operational_change(state, operation_id)
                    db.session.commit()
                check_totals(performance, ("6,000 GAL", "3,000 GAL", "64.2 K LBS"))
                self.assertTrue(any("live-panel" in url for _, url in requests))
                self.assertEqual(sum(url == Fixture.origin + "/neoscorpion/fuel-dispatch"
                                     for _, url in requests), 1)
                self.assertFalse(errors)
                Fixture().ready(page, "/neoscorpion/reports/fuel")
                check_totals(page, ("6,000 GAL", "3,000 GAL", "64.2 K LBS"))
                expect(page.locator("[data-fueling-event-id]")).to_have_count(4)
                self.assertTrue(page.locator(".neoscorpion-report-summary").evaluate(
                    "el => el.getBoundingClientRect().right <= window.innerWidth"))
                response = page.request.get(Fixture.origin + "/neoscorpion/reports/fuel?format=pdf")
                self.assertEqual(response.status, 200)
                pdf_bytes = response.body()
                (Fixture.evidence / "sort-fuel-report.pdf").write_bytes(pdf_bytes)
                pdf = PdfReader(BytesIO(pdf_bytes))
                text = "".join(p.extract_text() for p in pdf.pages)
                for value in ("TOTAL EST FUEL: 6,000 GAL", "TOTAL T/F: 3,000 GAL", "TOTAL REQUIRED FUEL: 64.2 K LBS"):
                    self.assertIn(value, text)
                page.screenshot(path=str(Fixture.evidence / "sort-fuel-report-mobile.png"), full_page=True)
        finally:
            if browser:
                browser.close()
            Fixture.tearDownClass()
