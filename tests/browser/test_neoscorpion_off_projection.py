"""Actual Dispatch column geometry, countdown and live OFF transition."""
from datetime import date, datetime, timedelta, timezone
import re
import unittest
from unittest.mock import patch

from playwright.sync_api import expect

from app.extensions import db
from app.models import (NeoScorpionFuelAssignment, NeoScorpionFuelTruck, NeoScorpionFuelWorkState,
    NeoScorpionSettings, NeoScorpionSortAssetState, NeoScorpionTailFuelState,
    SortDateMission, SortDateOperation, SortDateParkingAssignment, SortDateTailState, User)
from app.services.neoscorpion_assets import record_nightly_operational_change
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.time_display import format_local_hhmm
from tests.browser import test_mobile_drawer as fixture_module


class OffProjectionBrowserTest(unittest.TestCase):
    def test_desktop_mobile_countdown_geometry_and_live_off(self):
        fixture = fixture_module.MobileDrawerBrowserTest
        fixture.setUpClass()
        fixture.app.config["LIVE_SCREEN_REFRESH_INTERVAL_MS"] = 5000
        browser = None
        try:
            now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
            with fixture.app.app_context():
                gateway = ensure_default_gateway_and_nodes()
                admin = User.query.filter_by(username="drawer-admin").one()
                operation = SortDateOperation(generated_by_user_id=admin.id, gateway_id=gateway.id,
                    gateway_code=gateway.code, sort_date=date.today(), sort_name="night", window_minutes=60)
                truck = NeoScorpionFuelTruck(gateway_id=gateway.id, truck_number="20")
                db.session.add_all([operation, truck]); db.session.flush()
                db.session.add_all([NeoScorpionSettings(gateway_id=gateway.id),
                    NeoScorpionSortAssetState(sort_date_operation_id=operation.id, revision=1)])
                ids, work_ids = [], []
                for i, tail in enumerate(("N491UP", "N492UP")):
                    mission = SortDateMission(sort_date_operation_id=operation.id, sort_date=operation.sort_date,
                        gateway_code=gateway.code, sort_name="night", mission_type="departure", mission_source="manual",
                        flight_number=f"UPS90{i}", origin=gateway.code, destination="SDF", timezone="America/Chicago",
                        assigned_tail_number=tail, planned_fuel_load=52000 if i == 0 else None,
                        planned_datetime_utc=now+timedelta(hours=2), fuel_status="assigned")
                    arrival = SortDateMission(sort_date_operation_id=operation.id, sort_date=operation.sort_date,
                        gateway_code=gateway.code, sort_name="night", mission_type="arrival", mission_source="manual",
                        flight_number=f"UPS80{i}", origin="SDF", destination=gateway.code,
                        assigned_tail_number=tail, actual_block_in_datetime_utc=now-timedelta(minutes=10))
                    db.session.add_all([mission, arrival]); db.session.flush()
                    assignment = NeoScorpionFuelAssignment(sort_date_operation_id=operation.id,
                        sort_date_mission_id=mission.id, assigned_fueler_user_id=admin.id,
                        assigned_truck_id=truck.id, confirmed_tail_number=tail)
                    db.session.add(assignment); db.session.flush()
                    work = NeoScorpionFuelWorkState(fuel_assignment_id=assignment.id, tail_number=tail,
                        on_at_utc=now-timedelta(minutes=2), apu_running=False, apu_allowance_lbs=0)
                    db.session.add_all([work,
                        NeoScorpionTailFuelState(sort_date_operation_id=operation.id, tail_number=tail, inbound_fuel_lbs=12000),
                        SortDateTailState(sort_date=operation.sort_date, gateway_code=gateway.code, sort_name="night",
                            tail_number=tail, aircraft_type="757", aircraft_type_source="derived"),
                        SortDateParkingAssignment(sort_date_operation_id=operation.id, tail_number=tail,
                            ramp_code="B", position_code=f"B0{6+i}")])
                    db.session.flush(); ids.append(mission.id); work_ids.append(work.id)
                db.session.commit(); operation_id = operation.id

            with patch("app.services.neoscorpion.current_existing_operational_sort_operations",
                       side_effect=lambda gateway, now=None: [db.session.get(SortDateOperation, operation_id)]):
                browser = fixture.pw.chromium.launch()
                page = browser.new_page(viewport={"width":1440,"height":950})
                fixture().login(page)
                page.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1','seen')")
                errors = []; page.on("pageerror", lambda error: errors.append(str(error)))
                page.clock.install(time=now.replace(tzinfo=timezone.utc))
                fixture().ready(page, "/neoscorpion/fuel-dispatch")
                row = page.locator(f'tr[data-dispatch-mission-id="{ids[0]}"]')
                unknown = page.locator(f'tr[data-dispatch-mission-id="{ids[1]}"]')
                estimate = row.locator("[data-fueler-off-estimate]")
                expect(estimate).to_have_text(re.compile(r"^EST OFF \d{2}:\d{2} · ~\d+ MIN$"))
                expect(unknown.locator("[data-fueler-off-estimate]")).to_have_text("TIMING UNKNOWN")
                self.assertEqual(row.locator("[data-fuel-status-secondary]").count(), 1)
                for width in (1440, 390):
                    page.set_viewport_size({"width":width,"height":950})
                    metrics = row.evaluate("""row => {
                        const cell = row.querySelector('.neoscorpion-dispatch-status-cell');
                        const pill = cell.querySelector('.neoscorpion-fuel-status-chip');
                        const line = cell.querySelector('[data-fueler-off-estimate]');
                        const cr=cell.getBoundingClientRect(), pr=pill.getBoundingClientRect();
                        return {columns:row.querySelectorAll('td').length,
                            visible:[...row.querySelectorAll('td')].filter(el=>getComputedStyle(el).display!=='none').length,
                            width:cr.width, height:cr.height, pillWidth:pr.width, pillHeight:pr.height,
                            centered:Math.abs((pr.left+pr.right)-(cr.left+cr.right))<2,
                            lineWidth:line.clientWidth, lineContent:line.scrollWidth,
                            font:parseFloat(getComputedStyle(line).fontSize), nowrap:getComputedStyle(line).whiteSpace};
                    }""")
                    print("OFF estimate geometry", width, metrics)
                    self.assertEqual(metrics["columns"], 16)
                    self.assertEqual(metrics["visible"], 16 if width == 1440 else 5)
                    self.assertEqual(metrics["pillHeight"], 25)
                    self.assertTrue(metrics["centered"])
                    self.assertEqual(metrics["nowrap"], "nowrap")
                    self.assertLessEqual(metrics["lineContent"], metrics["lineWidth"]+1)
                    self.assertFalse(page.evaluate("document.documentElement.scrollWidth > innerWidth + 1"))
                    row.screenshot(path=str(fixture.evidence/f"off-estimate-{width}.png"))
                before = estimate.inner_text()
                before_height = row.bounding_box()["height"]
                page.clock.run_for(60001)
                expect(estimate).not_to_have_text(before)
                self.assertEqual(row.bounding_box()["height"], before_height)
                finish = estimate.get_attribute("data-fueler-off-estimate")
                page.clock.set_system_time(datetime.fromisoformat(finish.replace("Z", "+00:00"))+timedelta(seconds=1))
                page.clock.run_for(1000)
                expect(estimate).to_have_text("EST OFF OVERDUE")
                with fixture.app.app_context():
                    work = db.session.get(NeoScorpionFuelWorkState, work_ids[0]); work.off_at_utc = now
                    state = NeoScorpionSortAssetState.query.filter_by(sort_date_operation_id=operation_id).one()
                    record_nightly_operational_change(state, operation_id); db.session.commit()
                page.clock.run_for(6000)
                expect(row.locator("[data-fuel-status]")).to_have_attribute("data-fuel-status", "off", timeout=15000)
                expect(row.locator("[data-fueler-off-estimate]")).to_have_count(0)
                expect(row.locator("[data-fuel-status-secondary]")).to_have_text("OFF "+format_local_hhmm(now, "America/Chicago"))
                self.assertFalse(errors, errors)
        finally:
            if browser:
                browser.close()
            fixture.tearDownClass()
