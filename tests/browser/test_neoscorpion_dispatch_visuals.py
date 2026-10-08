"""Focused painted Dispatch table checks using an isolated local sort."""
import unittest
from datetime import date, datetime
from unittest.mock import patch

from tests.browser.test_mobile_drawer import MobileDrawerBrowserTest as Fixture
from app.extensions import db
from app.models import (
    NeoScorpionFuelAssignment, NeoScorpionFuelCycleHistory, NeoScorpionFuelTankState, NeoScorpionFuelWorkState,
    NeoScorpionSortAssetState,
    NeoScorpionTailFuelState, SortDateMission, SortDateOperation,
    SortDateParkingAssignment, SortDateTailState, User,
)


class NeoScorpionDispatchVisualsBrowserTest(unittest.TestCase):
    def test_outer_row_pulse_and_fuel_alignment_at_desktop_and_mobile_widths(self):
        Fixture.setUpClass()
        Fixture.app.config["LIVE_SCREEN_REFRESH_INTERVAL_MS"] = 5000
        browser = None
        try:
            with Fixture.app.app_context():
                from app.services.access_control import ensure_default_gateway_and_nodes
                gateway = ensure_default_gateway_and_nodes()
                admin = User.query.filter_by(username="drawer-admin").one()
                operation = SortDateOperation(
                    generated_by_user_id=admin.id, gateway_id=gateway.id,
                    gateway_code=gateway.code, sort_date=date.today(),
                    sort_name="night", window_minutes=60,
                )
                db.session.add(operation)
                db.session.flush()
                for index, has_fob in ((1, False), (2, True)):
                    tail = f"N4VIS{index}UP"
                    departure = SortDateMission(
                        sort_date=operation.sort_date, gateway_code=gateway.code,
                        sort_name="night", sort_date_operation_id=operation.id,
                        mission_type="departure", mission_source="manual",
                        flight_number=f"UPS90{index}", origin=gateway.code,
                        destination="SDF", timezone="America/Chicago",
                        planned_datetime_local=datetime(2026, 10, 7, 23, 30),
                        planned_datetime_utc=datetime(2026, 10, 8, 4, 30),
                        planned_source="manual", planned_fuel_load=50_000,
                        assigned_tail_number=tail, tail_source="manual",
                        fuel_status="assigned", departure_status="loading",
                    )
                    db.session.add(departure)
                    db.session.flush()
                    assignment = NeoScorpionFuelAssignment(
                        sort_date_operation_id=operation.id,
                        sort_date_mission_id=departure.id, confirmed_tail_number=tail,
                        review_status="pending", current_cycle_number=3 if index == 1 else 1,
                    )
                    db.session.add_all((assignment,
                        NeoScorpionTailFuelState(sort_date_operation_id=operation.id,
                            tail_number=tail, inbound_fuel_lbs=12_000),
                        SortDateParkingAssignment(sort_date_operation_id=operation.id,
                            tail_number=tail, ramp_code="E", position_code=f"E0{index}", lane_number=1),
                        SortDateTailState(sort_date=operation.sort_date,
                            gateway_code=gateway.code, sort_name="night",
                            tail_number=tail, aircraft_type="757", aircraft_type_source="derived"),
                        SortDateMission(sort_date=operation.sort_date,
                            gateway_code=gateway.code, sort_name="night",
                            sort_date_operation_id=operation.id, mission_type="arrival",
                            mission_source="manual", flight_number=f"UPS80{index}",
                            origin="SDF", destination=gateway.code,
                            assigned_tail_number=tail, arrival_status="on_ground",
                            fuel_status="waiting", departure_status="scheduled")))
                    db.session.flush()
                    if index == 1:
                        for cycle_number, label in ((1, "FUEL"), (2, "UPLIFT")):
                            db.session.add(NeoScorpionFuelCycleHistory(
                                sort_date_operation_id=operation.id,
                                fuel_assignment_id=assignment.id, mission_id=departure.id,
                                cycle_number=cycle_number, label=label,
                                snapshot={
                                    "tail_number": tail, "arrival_eta": "23:00",
                                    "destination": "SDF", "flight_number": departure.flight_number,
                                    "departure_time": "23:30", "parking_position": "E01",
                                    "inbound_fuel_display": "12.0", "required_fuel_display": "50.0",
                                    "actual_total_display": "52.0", "apu_allowance_display": "0.0",
                                    "neo_fuel_display": "52.0", "fueler": "Alexandria Dispatcher",
                                    "truck": "T-01", "transfer_fuel_gallons": 450,
                                    "dispatch_status_label": "Complete", "tank_rows": [],
                                },
                            ))
                    if has_fob:
                        work = NeoScorpionFuelWorkState(
                            fuel_assignment_id=assignment.id, tail_number=tail)
                        db.session.add(work)
                        db.session.flush()
                        db.session.add_all(NeoScorpionFuelTankState(
                            fuel_work_state_id=work.id, tank_code=code,
                            remaining_lbs=10_000,
                        ) for code in ("left", "ctr", "right"))
                db.session.commit()
                operation_id = operation.id

            # The browser fixture is isolated from the real sort clock.
            with patch("app.services.neoscorpion.current_existing_operational_sort_operations",
                       side_effect=lambda gateway, now=None: [db.session.get(SortDateOperation, operation_id)]):
                browser = Fixture.pw.chromium.launch()
                page = browser.new_page()
                Fixture().login(page)
                page.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1', 'seen')")
                for width, height in ((1440, 900), (390, 844), (375, 667)):
                    with self.subTest(width=width):
                        page.set_viewport_size({"width": width, "height": height})
                        Fixture().ready(page, "/neoscorpion/fuel-dispatch")
                        rows = page.locator(".neoscorpion-dispatch-primary-row.is-ready-to-assign")
                        self.assertEqual(rows.count(), 2)
                        history_rows = page.locator(".neoscorpion-dispatch-primary-row[data-cycle-history]")
                        self.assertEqual(history_rows.count(), 2)
                        fonts = page.evaluate("""() => ({
                            history: [...document.querySelectorAll('[data-cycle-history]')].map(row =>
                                [...row.cells].map(cell => getComputedStyle(cell).fontSize)),
                            details: [...document.querySelectorAll('[data-cycle-history] .neoscorpion-dispatch-details-toggle')]
                                .map(button => getComputedStyle(button).fontSize),
                            strong: [...document.querySelectorAll('[data-cycle-history] td strong')]
                                .map(node => getComputedStyle(node).fontSize),
                            small: [...document.querySelectorAll('[data-cycle-history] td small')]
                                .map(node => getComputedStyle(node).fontSize),
                            active: getComputedStyle(document.querySelector(
                                '.neoscorpion-dispatch-primary-row:not([data-cycle-history]) > td')).fontSize,
                        })""")
                        self.assertTrue(all(len(set(row_fonts)) == 1 for row_fonts in fonts["history"]), fonts)
                        self.assertTrue(all(size == fonts["history"][0][0] for size in fonts["strong"]), fonts)
                        self.assertTrue(all(float(size[:-2]) < float(fonts["history"][0][0][:-2])
                                            for size in fonts["small"]), fonts)
                        self.assertLess(float(fonts["history"][0][0][:-2]), float(fonts["active"][:-2]))
                        self.assertTrue(all(float(size[:-2]) < float(fonts["history"][0][0][:-2])
                                            for size in fonts["details"]), fonts)
                        geometry = rows.evaluate_all("""rows => rows.map(row => {
                            const cells = [...row.cells];
                            const inbound = row.querySelector('.neoscorpion-dispatch-inbound');
                            const required = row.querySelector('.neoscorpion-dispatch-required');
                            const first = inbound.querySelector('input,strong').getBoundingClientRect();
                            const second = required.querySelector('input,span').getBoundingClientRect();
                            const fob = inbound.querySelector('.neoscorpion-dispatch-secondary');
                            return {aligned: Math.abs(first.top - second.top) <= 1,
                                reserved: fob.getBoundingClientRect().height > 0,
                                empty: fob.classList.contains('is-empty'),
                                animations: [cells[0], cells[1], cells.at(-1)].map(
                                    cell => getComputedStyle(cell).animationName),
                                rowWidth: row.getBoundingClientRect().width,
                                cellsWidth: cells.reduce((sum, cell) => sum + cell.getBoundingClientRect().width, 0)};
                        })""")
                        self.assertEqual([item["empty"] for item in geometry], [True, False])
                        for item in geometry:
                            self.assertTrue(item["aligned"] and item["reserved"], item)
                            self.assertAlmostEqual(item["rowWidth"], item["cellsWidth"], delta=2)
                            self.assertEqual(item["animations"], [
                                "neoscorpion-ready-assign-left",
                                "neoscorpion-ready-assign-horizontal",
                                "neoscorpion-ready-assign-right",
                            ])
                        page.emulate_media(reduced_motion="reduce")
                        self.assertEqual(rows.first.locator("td").nth(1).evaluate(
                            "cell => getComputedStyle(cell).animationName"), "none")
                        page.emulate_media(reduced_motion="no-preference")
                        if width in (1440, 390):
                            page.screenshot(path=str(Fixture.evidence / f"dispatch-history-{width}.png"))
                        if width == 1440:
                            page.evaluate("window.dispatchRowBeforeRefresh = document.querySelectorAll('.neoscorpion-dispatch-primary-row:not([data-cycle-history])')[1]")
                            with Fixture.app.app_context():
                                tail_state = NeoScorpionTailFuelState.query.filter_by(tail_number="N4VIS2UP").one()
                                tail_state.inbound_fuel_lbs = 13_000
                                asset = NeoScorpionSortAssetState.query.filter_by(sort_date_operation_id=operation_id).first()
                                if asset is None:
                                    asset = NeoScorpionSortAssetState(sort_date_operation_id=operation_id, revision=0)
                                    db.session.add(asset)
                                asset.revision += 1
                                db.session.commit()
                            page.wait_for_function("""() => {
                                const row = document.querySelectorAll('.neoscorpion-dispatch-primary-row:not([data-cycle-history])')[1];
                                const input = row?.querySelector('input[name="inbound_fuel"]');
                                return row !== window.dispatchRowBeforeRefresh && input?.value === '13.0';
                            }""", timeout=15000)
                            self.assertEqual(page.locator('.neoscorpion-dispatch-primary-row.is-ready-to-assign').count(), 2)
                            self.assertEqual(page.locator('[data-cycle-history]').count(), 2)
                            self.assertEqual(history_rows.first.locator('td').first.evaluate(
                                'cell => getComputedStyle(cell).fontSize'), fonts['history'][0][0])
        finally:
            if browser:
                browser.close()
            Fixture.tearDownClass()
