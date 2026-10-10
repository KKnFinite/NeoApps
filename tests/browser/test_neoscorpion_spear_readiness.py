"""Real Dispatch HTML and live-poll regression; isolated DB, no SPEAR mocks."""
from datetime import date, datetime, timedelta
import unittest
from unittest.mock import patch

from playwright.sync_api import expect

from app.extensions import db
from app.models import (
    NeoScorpionFuelAssignment, NeoScorpionFuelTruck, NeoScorpionSettings,
    NeoScorpionSortAssetState, NeoScorpionSortFueler, NeoScorpionSortTruck,
    NeoScorpionSpearAuditEntry, NeoScorpionTailFuelState, SortDateMission,
    SortDateOperation, SortDateParkingAssignment, SortDateTailState, User,
)
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.neoscorpion_assets import record_nightly_operational_change
from tests.browser import test_mobile_drawer as fixture_module


class SpearReadinessBrowserTest(unittest.TestCase):
    def test_rendered_readiness_resource_blockers_and_live_transitions(self):
        Fixture = fixture_module.MobileDrawerBrowserTest
        Fixture.setUpClass()
        Fixture.app.config["LIVE_SCREEN_REFRESH_INTERVAL_MS"] = 5000
        browser = None
        try:
            with Fixture.app.app_context():
                gateway = ensure_default_gateway_and_nodes()
                admin = User.query.filter_by(username="drawer-admin").one()
                operation = SortDateOperation(
                    generated_by_user_id=admin.id, gateway_id=gateway.id,
                    gateway_code=gateway.code, sort_date=date.today(),
                    sort_name="night", window_minutes=60,
                )
                db.session.add(operation)
                db.session.flush()
                truck = NeoScorpionFuelTruck(gateway_id=gateway.id, truck_number="T-SPEAR",
                                              capacity_gallons=6000, is_active=True)
                db.session.add(truck)
                db.session.flush()
                db.session.add_all((
                    NeoScorpionSettings(gateway_id=gateway.id, spear_automation_enabled=False,
                                        spear_live_calibration_mode="observe"),
                    NeoScorpionSortFueler(sort_date_operation_id=operation.id, user_id=admin.id),
                    NeoScorpionSortTruck(sort_date_operation_id=operation.id, fuel_truck_id=truck.id,
                                         status="available", starting_gallons=5000, current_gallons=5000),
                    NeoScorpionSortAssetState(sort_date_operation_id=operation.id, revision=0),
                ))
                mission_ids = []
                arrival_ids = []
                for index, (parking, status) in enumerate(
                        (("B06", "on_ground"), ("D07", "arrived"), ("E03", "scheduled")), start=1):
                    tail = f"N{index}SPEAR"
                    departure = SortDateMission(
                        sort_date=operation.sort_date, gateway_code=gateway.code, sort_name="night",
                        sort_date_operation_id=operation.id, mission_type="departure", mission_source="manual",
                        flight_number=f"UPS90{index}", origin=gateway.code, destination="SDF",
                        timezone="America/Chicago", planned_datetime_utc=datetime.utcnow() + timedelta(hours=2),
                        planned_source="manual", planned_fuel_load=20_000 if index < 3 else None,
                        assigned_tail_number=tail, tail_source="manual", fuel_status="waiting",
                        departure_status="scheduled",
                    )
                    arrival = SortDateMission(
                        sort_date=operation.sort_date, gateway_code=gateway.code, sort_name="night",
                        sort_date_operation_id=operation.id, mission_type="arrival", mission_source="manual",
                        flight_number=f"UPS80{index}", origin="SDF", destination=gateway.code,
                        assigned_tail_number=tail, arrival_status=status, fuel_status="waiting",
                        departure_status="scheduled",
                        # Deliberately no block-in/ETA timestamps: status is arrival evidence.
                    )
                    db.session.add_all((departure, arrival,
                        SortDateTailState(sort_date=operation.sort_date, gateway_code=gateway.code,
                                          sort_name="night", tail_number=tail, aircraft_type="757",
                                          aircraft_type_source="derived"),
                        NeoScorpionTailFuelState(sort_date_operation_id=operation.id,
                                                 tail_number=tail, inbound_fuel_lbs=12_000 if index < 3 else None),
                    ))
                    if index < 3:
                        db.session.add(SortDateParkingAssignment(sort_date_operation_id=operation.id,
                            tail_number=tail, ramp_code=parking[0], position_code=parking, lane_number=1))
                    db.session.flush()
                    mission_ids.append(departure.id)
                    arrival_ids.append(arrival.id)
                db.session.commit()
                operation_id, truck_id = operation.id, truck.id

            with patch("app.services.neoscorpion.current_existing_operational_sort_operations",
                       side_effect=lambda gateway, now=None: [db.session.get(SortDateOperation, operation_id)]):
                browser = Fixture.pw.chromium.launch()
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                Fixture().login(page)
                page.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1', 'seen')")
                requests = []
                page.on("request", lambda request: requests.append((request.method, request.url)))
                Fixture().ready(page, "/neoscorpion/fuel-dispatch")
                css = page.locator('link[href*="26-neoscorpion.css"]')
                self.assertIn("scorpion=20261010-spear-next-v2", css.get_attribute("href"))
                rows = [page.locator(f'.neoscorpion-dispatch-primary-row[data-dispatch-mission-id="{mid}"]')
                        for mid in mission_ids]

                def assert_mission_spear_sizes(row):
                    sizes = row.evaluate("""row => {
                        const table = row.closest('table');
                        const expected = parseFloat(getComputedStyle(table).getPropertyValue('--dispatch-font-small'))
                            * parseFloat(getComputedStyle(document.documentElement).fontSize);
                        const detail = row.nextElementSibling.querySelector('.neoscorpion-dispatch-spear-detail');
                        const elements = [row.querySelector('[data-spear-tail-indicator]'),
                            ...row.querySelectorAll('[data-spear-tail-indicator] .neoscorpion-spear-severity'),
                            ...row.querySelectorAll('.neoscorpion-dispatch-recommendation'),
                            ...detail.querySelectorAll('strong, span, small, summary, li, .neoscorpion-spear-why')];
                        return {expected, actual: elements.map(el => ({text: el.textContent.trim(),
                            size: parseFloat(getComputedStyle(el).fontSize)}))};
                    }""")
                    self.assertTrue(sizes["actual"], sizes)
                    for item in sizes["actual"]:
                        self.assertAlmostEqual(item["size"], sizes["expected"], delta=0.15, msg=str(sizes))

                def assert_mission_spear_fit(row):
                    toggle = row.locator(".neoscorpion-dispatch-details-toggle")
                    if not toggle.is_visible():  # Five-column mobile view omits the details column.
                        return
                    toggle.click()
                    detail = row.locator("xpath=following-sibling::tr[1]").locator(".neoscorpion-dispatch-spear-detail")
                    self.assertTrue(detail.evaluate("""section =>
                        section.scrollWidth <= section.clientWidth + 1 &&
                        [...section.querySelectorAll('strong, span, small, summary')].every(el =>
                            el.scrollWidth <= el.clientWidth + 1)
                    """))
                    toggle.click()

                for row, parking in zip(rows[:2], ("B06", "D07")):
                    expect(row.locator(".is-ready")).to_have_text("SPEAR · READY")
                    expect(row.locator(".is-waiting")).to_have_count(0)
                    expect(row.locator(".neoscorpion-dispatch-etd-parking strong")).to_have_text(parking)
                    expect(row.locator("xpath=following-sibling::tr[1]")).to_contain_text("TIMING UNKNOWN")
                    expect(row.locator(".neoscorpion-dispatch-recommendation").first).to_contain_text("SPEAR")
                    assert_mission_spear_sizes(row)
                assert_mission_spear_fit(rows[0])
                expect(rows[2].locator(".is-waiting")).to_have_text("SPEAR · WAITING")
                page.screenshot(path=str(Fixture.evidence / "spear-waiting-desktop.png"), full_page=True)

                # The upstream data update publishes the same revision used by the live board.
                with Fixture.app.app_context():
                    departure = db.session.get(SortDateMission, mission_ids[2])
                    departure.planned_fuel_load = 20_000
                    db.session.get(SortDateMission, arrival_ids[2]).arrival_status = "on_ground"
                    NeoScorpionTailFuelState.query.filter_by(sort_date_operation_id=operation_id,
                        tail_number="N3SPEAR").one().inbound_fuel_lbs = 12_000
                    db.session.add(SortDateParkingAssignment(sort_date_operation_id=operation_id,
                        tail_number="N3SPEAR", ramp_code="E", position_code="E03", lane_number=1))
                    state = NeoScorpionSortAssetState.query.filter_by(sort_date_operation_id=operation_id).one()
                    record_nightly_operational_change(state, operation_id)
                    db.session.commit()
                expect(rows[2].locator(".is-ready")).to_have_text("SPEAR · READY", timeout=20000)
                expect(rows[2].locator(".is-waiting")).to_have_count(0)
                expect(rows[2].locator(".neoscorpion-dispatch-etd-parking strong")).to_have_text("E03")
                assert_mission_spear_sizes(rows[2])
                expect(page.locator("[data-spear-readiness]")).to_contain_text("3/3 MISSIONS READY")
                page.screenshot(path=str(Fixture.evidence / "spear-ready-desktop.png"), full_page=True)

                with Fixture.app.app_context():
                    selection = NeoScorpionSortTruck.query.filter_by(sort_date_operation_id=operation_id,
                        fuel_truck_id=truck_id).one()
                    selection.status = "unavailable_oos"
                    state = NeoScorpionSortAssetState.query.filter_by(sort_date_operation_id=operation_id).one()
                    record_nightly_operational_change(state, operation_id)
                    db.session.commit()
                for row in rows:
                    expect(row.locator(".is-unplanned")).to_have_text("SPEAR · NO TRUCK", timeout=20000)
                    expect(row.locator(".is-waiting")).to_have_count(0)
                page.set_viewport_size({"width": 390, "height": 844})
                for row in rows:
                    assert_mission_spear_sizes(row)
                assert_mission_spear_fit(rows[0])
                page.screenshot(path=str(Fixture.evidence / "spear-resource-blocked-mobile.png"), full_page=True)
                # Compact blockers stay on one line inside the existing tail cells.
                for row in rows:
                    self.assertTrue(row.locator(".is-unplanned").evaluate("""badge => {
                        const cell = badge.closest('td').getBoundingClientRect();
                        const box = badge.getBoundingClientRect();
                        return box.width <= cell.width && getComputedStyle(badge).whiteSpace === 'nowrap' && badge.scrollWidth <= badge.clientWidth+1;
                    }"""))

                page.set_viewport_size({"width": 375, "height": 667})
                for row in rows:
                    assert_mission_spear_sizes(row)
                    self.assertTrue(row.locator(".is-unplanned").evaluate("""badge =>
                        badge.scrollWidth <= badge.clientWidth + 1"""))

                with Fixture.app.app_context():
                    NeoScorpionSortFueler.query.filter_by(sort_date_operation_id=operation_id).delete()
                    NeoScorpionSortTruck.query.filter_by(sort_date_operation_id=operation_id).one().status = "available"
                    state = NeoScorpionSortAssetState.query.filter_by(sort_date_operation_id=operation_id).one()
                    record_nightly_operational_change(state, operation_id)
                    db.session.commit()
                expect(rows[0].locator(".is-unplanned")).to_have_text("SPEAR · NO FUELER", timeout=20000)
                with Fixture.app.app_context():
                    self.assertFalse(NeoScorpionSettings.query.one().spear_automation_enabled)
                    self.assertEqual(NeoScorpionSettings.query.one().spear_live_calibration_mode, "observe")
                    self.assertEqual(NeoScorpionFuelAssignment.query.count(), 0)
                    self.assertEqual(NeoScorpionSpearAuditEntry.query.count(), 0)
                self.assertTrue(any("live-panel" in url for _, url in requests), requests)
                self.assertFalse(any(method == "POST" for method, _ in requests), requests)
                self.assertEqual(sum("/neoscorpion/fuel-dispatch" == url.split("?")[0].removeprefix(Fixture.origin)
                                     for _, url in requests), 1, "Live refresh must not reload the page")
        finally:
            if browser:
                browser.close()
            Fixture.tearDownClass()


if __name__ == "__main__":
    unittest.main()
