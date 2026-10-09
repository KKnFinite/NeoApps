"""Real authenticated checkbox saves, live alerts and 16-column browser layout."""
from datetime import date, datetime, timedelta
import json
import unittest
from unittest.mock import patch

from playwright.sync_api import expect

from app.extensions import db
from app.models import (NeoScorpionDispatcherCheck, NeoScorpionFuelAssignment,
    NeoScorpionFuelCycleHistory, NeoScorpionFuelTankState, NeoScorpionFuelWorkState,
    NeoScorpionSortAssetState, SortDateMission, SortDateOperation, SortDateTailState, User)
from app.services.access_control import backfill_default_gateway_node_roles, ensure_default_gateway_and_nodes
from app.services.neoscorpion_assets import record_nightly_operational_change
from app.services.password_policy import set_user_password
from tests.browser.test_mobile_drawer import MobileDrawerBrowserTest as Fixture


class DispatchChecksExcessBrowserTest(unittest.TestCase):
    def test_personal_persistence_live_alerts_failures_and_responsive_alignment(self):
        Fixture.setUpClass()
        Fixture.app.config["LIVE_SCREEN_REFRESH_INTERVAL_MS"] = 5000
        browser = None
        try:
            with Fixture.app.app_context():
                gateway = ensure_default_gateway_and_nodes()
                admin = User.query.filter_by(username="drawer-admin").one()
                other = User(username="personal-other", email="personal-other@example.test",
                             role="grandmaster", email_verified_at=datetime.now())
                set_user_password(other, "BrowserFixture123!")
                db.session.add(other)
                db.session.flush()
                backfill_default_gateway_node_roles(other, role="grandmaster")
                operation = SortDateOperation(generated_by_user_id=admin.id, gateway_id=gateway.id,
                    gateway_code=gateway.code, sort_date=date.today(), sort_name="night", window_minutes=60)
                db.session.add(operation)
                db.session.flush()
                db.session.add(NeoScorpionSortAssetState(sort_date_operation_id=operation.id, revision=1))
                mission_ids, work_ids = [], []
                for index in range(2):
                    tail = f"N49{index}UP"
                    mission = SortDateMission(sort_date_operation_id=operation.id, sort_date=operation.sort_date,
                        gateway_code=gateway.code, sort_name="night", mission_type="departure",
                        mission_source="manual", flight_number="UPS901", origin=gateway.code, destination="SDF",
                        assigned_tail_number=tail, tail_source="manual", timezone="America/Chicago",
                        planned_fuel_load=25_400 + 2 * index, planned_datetime_utc=datetime.utcnow() + timedelta(hours=2),
                        planned_source="manual", departure_status="loading", fuel_status="waiting")
                    db.session.add(mission)
                    db.session.add(SortDateTailState(sort_date=operation.sort_date, gateway_code=gateway.code,
                        sort_name="night", tail_number=tail, aircraft_type="757", aircraft_type_source="derived"))
                    db.session.flush()
                    assignment = NeoScorpionFuelAssignment(sort_date_operation_id=operation.id,
                        sort_date_mission_id=mission.id, confirmed_tail_number=tail, current_cycle_number=2 if index == 0 else 1)
                    db.session.add(assignment)
                    db.session.flush()
                    work = NeoScorpionFuelWorkState(fuel_assignment_id=assignment.id, tail_number=tail,
                        apu_running=True, apu_allowance_lbs=0, automatic_apu_allowance_lbs=0,
                        apu_source_tank_code="left")
                    db.session.add(work)
                    db.session.flush()
                    for code, lbs in (("left", 15_000), ("ctr", 0), ("right", 13_750 + index)):
                        db.session.add(NeoScorpionFuelTankState(fuel_work_state_id=work.id, tank_code=code,
                                                             actual_lbs=lbs, remaining_lbs=6000))
                    if index == 0:
                        db.session.add(NeoScorpionFuelCycleHistory(sort_date_operation_id=operation.id,
                            fuel_assignment_id=assignment.id, mission_id=mission.id, cycle_number=1,
                            label="COMPLETED", snapshot={"tail_number": tail, "flight_number": "UPS901",
                                "neo_fuel_display": "28.8", "neo_fuel_excess_alert": True, "tank_rows": []}))
                    mission_ids.append(mission.id)
                    work_ids.append(work.id)
                db.session.commit()
                operation_id, admin_id = operation.id, admin.id

            with patch("app.services.neoscorpion.current_existing_operational_sort_operations",
                    side_effect=lambda gateway, now=None: [db.session.get(SortDateOperation, operation_id)]):
                browser = Fixture.pw.chromium.launch()
                context = browser.new_context(viewport={"width": 1440, "height": 900})
                page = context.new_page()
                Fixture().login(page)
                page.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1', 'seen')")
                requests, errors = [], []
                page.on("request", lambda request: requests.append((request.method, request.url)))
                page.on("pageerror", lambda error: errors.append(str(error)))
                Fixture().ready(page, "/neoscorpion/fuel-dispatch")
                rows = [page.locator(f'[data-dispatch-mission-id="{mid}"]') for mid in mission_ids]
                checks = [row.locator("[data-dispatch-check]") for row in rows]
                neo = rows[0].locator("[data-dispatch-neo-fuel]")
                expect(neo).to_have_text("28.8")
                expect(neo).to_have_class("neoscorpion-dispatch-size-large is-neo-fuel-excess")
                expect(rows[1].locator("[data-dispatch-neo-fuel]")).to_have_text("28.8")
                self.assertFalse(rows[1].locator("[data-dispatch-neo-fuel]").evaluate("el => el.classList.contains('is-neo-fuel-excess')"))
                self.assertEqual(neo.evaluate("el => getComputedStyle(el).color"), "rgb(255, 98, 107)")
                self.assertEqual(neo.evaluate("el => getComputedStyle(el).fontWeight"), "900")
                expect(page.locator("[data-cycle-history] input[type=checkbox]")).to_have_count(0)
                expect(page.locator("[data-cycle-history] .is-neo-fuel-excess")).to_have_count(0)
                checks[0].check()
                expect(checks[0]).to_have_attribute("data-saved-checked", "1")
                expect(checks[0]).to_be_enabled()
                with Fixture.app.app_context():
                    record = NeoScorpionDispatcherCheck.query.one()
                    self.assertEqual(record.user_id, admin_id)
                    self.assertEqual(NeoScorpionSortAssetState.query.one().revision, 1)
                    self.assertEqual(db.session.get(SortDateMission, mission_ids[0]).planned_fuel_load, 25_400)
                page.reload(wait_until="domcontentloaded")
                expect(checks[0]).to_be_checked()
                for width in (1440, 1024, 390, 375):
                    page.set_viewport_size({"width": width, "height": 900})
                    self.assertTrue(page.locator(".neoscorpion-dispatch-table").evaluate("""table => {
                        const header = [...table.tHead.rows[0].cells];
                        const rows = [...table.tBodies[0].rows].filter(row => !row.classList.contains('neoscorpion-dispatch-detail-row'));
                        return header.length === 16 && header[0].getBoundingClientRect().width <= 28 &&
                            rows.every(row => row.cells.length === 16 && [...row.cells].every((cell, index) =>
                                Math.abs(cell.getBoundingClientRect().left - header[index].getBoundingClientRect().left) < 1)) &&
                            [...table.querySelectorAll('.neoscorpion-dispatch-detail-row td')].every(cell => cell.colSpan === 16);
                    }"""), f"Active/history/header columns must align at {width}px")
                    self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), width)
                    if width in (1440, 390):
                        page.screenshot(path=str(Fixture.evidence / f"dispatch-check-excess-{width}.png"), full_page=True)
                # Failure feedback restores the previous persisted state without navigation.
                page.route("**/fuel-dispatch/check", lambda route: route.fulfill(status=500,
                    content_type="application/json", body=json.dumps({"ok": False, "error": "Fixture save failure"})))
                checks[0].uncheck()
                expect(checks[0]).to_be_checked()
                expect(checks[0]).to_have_attribute("aria-invalid", "true")
                expect(checks[0]).to_have_attribute("title", "Save Failed: Fixture save failure")
                page.unroute("**/fuel-dispatch/check")
                settings = context.new_page()
                Fixture().ready(settings, "/neoscorpion/settings")
                form = settings.locator('form:has(input[name="action"][value="save_settings"])')
                field = form.locator('[name="neo_fuel_excess_alert_gallons"]')
                expect(field).to_have_value("500")
                field.fill("600")
                with settings.expect_navigation(wait_until="domcontentloaded"):
                    form.get_by_role("button", name="Save Settings", exact=True).click()
                page.bring_to_front()
                expect(neo).not_to_have_class("neoscorpion-dispatch-size-large is-neo-fuel-excess", timeout=20000)
                expect(checks[0]).to_be_checked()
                page.set_viewport_size({"width": 1440, "height": 900})
                before_edits = sum(url == Fixture.origin + "/neoscorpion/fuel-dispatch" for _, url in requests)
                required = rows[0].locator('[data-autosave-field="required_fuel"]')
                required.fill("24.0")
                required.press("Tab")
                expect(required).to_have_attribute("data-saved-value", "24.0")
                expect(neo).to_have_class("neoscorpion-dispatch-size-large is-neo-fuel-excess")
                apu = rows[0].locator("[data-dispatch-apu-editor]")
                apu.locator("summary").click()
                apu.locator("[data-dispatch-apu-override-value]").fill("1.0")
                apu.get_by_role("button", name="SAVE", exact=True).click()
                expect(neo).to_have_text("27.8")
                expect(neo).not_to_have_class("neoscorpion-dispatch-size-large is-neo-fuel-excess")
                self.assertEqual(sum(url == Fixture.origin + "/neoscorpion/fuel-dispatch" for _, url in requests), before_edits)
                settings.bring_to_front()
                field.fill("500")
                form.locator('[name="fuel_density_lbs_per_gallon"]').fill("7")
                with settings.expect_navigation(wait_until="domcontentloaded"):
                    form.get_by_role("button", name="Save Settings", exact=True).click()
                page.bring_to_front()
                expect(neo).to_have_class("neoscorpion-dispatch-size-large is-neo-fuel-excess", timeout=20000)
                with Fixture.app.app_context():
                    tank = NeoScorpionFuelTankState.query.filter_by(fuel_work_state_id=work_ids[0], tank_code="left").one()
                    tank.actual_lbs = 14_600
                    state = NeoScorpionSortAssetState.query.filter_by(sort_date_operation_id=operation_id).one()
                    record_nightly_operational_change(state, operation_id)
                    db.session.commit()
                expect(neo).to_have_text("27.4", timeout=20000)
                expect(neo).not_to_have_class("neoscorpion-dispatch-size-large is-neo-fuel-excess")
                expect(checks[0]).to_be_checked()
                # A second real dispatcher gets separate checks for identical flights.
                other_context = browser.new_context(viewport={"width": 1440, "height": 900})
                other_page = other_context.new_page()
                Fixture().login(other_page, "personal-other")
                other_page.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1', 'seen')")
                Fixture().ready(other_page, "/neoscorpion/fuel-dispatch")
                expect(other_page.locator(f'[data-dispatch-mission-id="{mission_ids[0]}"] [data-dispatch-check]')).not_to_be_checked()
                other_check = other_page.locator(f'[data-dispatch-mission-id="{mission_ids[1]}"] [data-dispatch-check]')
                other_check.check()
                expect(other_check).to_have_attribute("data-saved-checked", "1")
                page.reload(wait_until="domcontentloaded")
                expect(checks[0]).to_be_checked()
                expect(checks[1]).not_to_be_checked()
                self.assertFalse(errors)
                self.assertTrue(any("live-panel" in url for _, url in requests))
        finally:
            if browser:
                browser.close()
            Fixture.tearDownClass()
