"""Real NeoRain lineup saves refresh read-only Dispatch details in Chromium."""
from contextlib import ExitStack
from datetime import date, datetime, time, timedelta
import unittest
from unittest.mock import patch

from playwright.sync_api import expect

from app.extensions import db
from app.models import (
    MasterFlightSchedule, NeoScorpionFuelAssignment, NeoScorpionFuelTankState,
    NeoScorpionFuelWorkState, SortDateMission, SortDateOperation, SortDateTailState,
    StaffingPerson, StaffingUnit, StaffingWorkAssignment, User,
)
from app.services.access_control import ensure_default_gateway_and_nodes
from tests.browser.test_mobile_drawer import MobileDrawerBrowserTest as Fixture


class DispatchLoadPlannerBrowserTest(unittest.TestCase):
    def test_lineup_edits_refresh_canonical_names_and_preserve_copy(self):
        Fixture.setUpClass()
        Fixture.app.config["LIVE_SCREEN_REFRESH_INTERVAL_MS"] = 5000
        browser = None
        try:
            with Fixture.app.app_context():
                gateway = ensure_default_gateway_and_nodes()
                admin = User.query.filter_by(username="drawer-admin").one()
                operation = SortDateOperation(generated_by_user_id=admin.id, gateway_id=gateway.id,
                    gateway_code=gateway.code, sort_date=date.today(), sort_name="night", window_minutes=60)
                db.session.add(operation)
                parent = None
                for unit_type, name in (("sort", "Night"), ("operation", "Ramp"),
                                        ("department", "Load Planning"), ("work_area", "Load Planners")):
                    parent = StaffingUnit(unit_type=unit_type, name=name, parent=parent, active=True)
                    db.session.add(parent)
                db.session.flush()
                planners = []
                for first in ("Alex", "Blair"):
                    person = StaffingPerson(employee_id=f"LP-{first}", first_name=first, last_name="Planner",
                        seniority_date=date(2020, 1, 1), classification="part_time", active=True)
                    db.session.add(person)
                    db.session.flush()
                    db.session.add(StaffingWorkAssignment(person_id=person.id,
                                                          work_area_unit_id=parent.id, active=True))
                    planners.append(person)
                missions, masters = [], []
                for index in range(4):
                    master = None
                    if index < 2:
                        master = MasterFlightSchedule(gateway_id=gateway.id, gateway_code=gateway.code,
                            sort_name="night", mission_type="departure", flight_number="UPS901",
                            origin=gateway.code, destination="SDF", active_days="monday", active=True,
                            planned_time_local=time(23, 0),
                            load_planner_person_id=planners[0].id if index == 0 else None)
                        db.session.add(master)
                        db.session.flush()
                        masters.append(master)
                    tail = f"N4LP{index}"
                    mission = SortDateMission(sort_date_operation_id=operation.id, sort_date=operation.sort_date,
                        gateway_code=gateway.code, sort_name="night", mission_type="departure",
                        mission_source="master" if master else "manual", flight_number="UPS901",
                        master_flight_schedule_id=master.id if master else None,
                        load_planner_person_id=planners[0].id if index == 2 else None,
                        origin=gateway.code, destination="SDF", assigned_tail_number=tail,
                        tail_source="manual", timezone="America/Chicago", planned_fuel_load=25_000,
                        planned_datetime_utc=datetime.utcnow() + timedelta(hours=2, minutes=index),
                        planned_source="manual", departure_status="loading", fuel_status="waiting")
                    db.session.add(mission)
                    db.session.add(SortDateTailState(sort_date=operation.sort_date, gateway_code=gateway.code,
                        sort_name="night", tail_number=tail, aircraft_type="757", aircraft_type_source="derived"))
                    db.session.flush()
                    missions.append(mission)
                assignment = NeoScorpionFuelAssignment(sort_date_operation_id=operation.id,
                    sort_date_mission_id=missions[0].id, confirmed_tail_number=missions[0].assigned_tail_number)
                db.session.add(assignment)
                db.session.flush()
                work = NeoScorpionFuelWorkState(fuel_assignment_id=assignment.id,
                    tail_number=missions[0].assigned_tail_number, apu_running=False, apu_allowance_lbs=0,
                    off_at_utc=datetime.utcnow())
                db.session.add(work)
                db.session.flush()
                for code, lbs in (("left", 8000), ("ctr", 9000), ("right", 8000)):
                    db.session.add(NeoScorpionFuelTankState(fuel_work_state_id=work.id, tank_code=code,
                                                           remaining_lbs=lbs, actual_lbs=lbs))
                db.session.commit()
                operation_id = operation.id
                mission_ids, master_ids = [mission.id for mission in missions], [master.id for master in masters]
                blair_id = planners[1].id

            with ExitStack() as stack:
                stack.enter_context(patch("app.services.neoscorpion.current_existing_operational_sort_operations",
                    side_effect=lambda gateway, now=None: [db.session.get(SortDateOperation, operation_id)]))
                stack.enter_context(patch("app.neonodes.neorain.routes.current_neorain_outbound_operation",
                    side_effect=lambda gateway: db.session.get(SortDateOperation, operation_id)))
                stack.enter_context(patch("app.neonodes.neorain.routes.current_existing_operational_sort_operations",
                    side_effect=lambda gateway: [db.session.get(SortDateOperation, operation_id)]))
                browser = Fixture.pw.chromium.launch()
                context = browser.new_context(viewport={"width": 1440, "height": 900},
                                              permissions=["clipboard-read", "clipboard-write"])
                dispatch = context.new_page()
                Fixture().login(dispatch)
                dispatch.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1', 'seen')")
                requests = []
                dispatch.on("request", lambda request: requests.append((request.method, request.url)))
                Fixture().ready(dispatch, "/neoscorpion/fuel-dispatch")
                details = [dispatch.locator(f"#neoscorpion-dispatch-detail-{mid}-1") for mid in mission_ids]
                for mid, detail, name in zip(mission_ids, details,
                                            ("Alex Planner", "UNASSIGNED", "Alex Planner", "UNASSIGNED")):
                    dispatch.locator(f'[data-dispatch-mission-id="{mid}"] [data-neoscorpion-dispatch-details]').click()
                    expect(detail.locator("[data-dispatch-load-planner]")).to_have_text(f"Load Planner: {name}")
                    expect(detail.locator("[data-dispatch-load-planner] input, [data-dispatch-load-planner] select")).to_have_count(0)
                copy = details[0].locator("[data-copy-value]")
                original_copy = copy.get_attribute("data-copy-value")
                self.assertEqual(original_copy, "UPS901 SDF N4LP0 NEO > 25.0")
                copy.click()
                expect(copy).to_have_text("COPIED")
                self.assertEqual(dispatch.evaluate("navigator.clipboard.readText()"), original_copy)
                self.assertTrue(details[0].locator("[data-dispatch-load-planner]").evaluate("""name =>
                    name.getBoundingClientRect().top >= name.parentElement.querySelector('[data-copy-value]')
                        .getBoundingClientRect().bottom"""))

                rain = context.new_page()
                Fixture().ready(rain, "/neorain/load-planner-lineup")
                for scope, departure_id, value, detail, expected in (
                        ("master", master_ids[0], str(blair_id), details[0], "Blair Planner"),
                        ("current_sort", mission_ids[3], str(blair_id), details[3], "Blair Planner"),
                        ("master", master_ids[0], "", details[0], "UNASSIGNED"),
                        ("current_sort", mission_ids[3], "", details[3], "UNASSIGNED")):
                    rain.bring_to_front()
                    form = rain.locator(f'form:has(input[name="assignment_scope"][value="{scope}"])'
                                        f':has(input[name="departure_id"][value="{departure_id}"])')
                    form.locator('select[name="planner_person_id"]').select_option(value)
                    with rain.expect_navigation(wait_until="domcontentloaded"):
                        form.get_by_role("button", name="SAVE", exact=True).click()
                    dispatch.bring_to_front()
                    expect(detail.locator("[data-dispatch-load-planner]")).to_have_text(
                        f"Load Planner: {expected}", timeout=20000)
                    expect(detail).to_be_visible()
                    # Other identical flights stay mapped to their own canonical rows.
                    expect(details[1].locator("[data-dispatch-load-planner]")).to_have_text("Load Planner: UNASSIGNED")
                    expect(details[2].locator("[data-dispatch-load-planner]")).to_have_text("Load Planner: Alex Planner")
                    self.assertEqual(copy.get_attribute("data-copy-value"), original_copy)
                dispatch.screenshot(path=str(Fixture.evidence / "dispatch-load-planner-desktop.png"), full_page=True)
                dispatch.set_viewport_size({"width": 390, "height": 844})
                expect(details[0].locator("[data-dispatch-load-planner]")).to_be_visible()
                dispatch.screenshot(path=str(Fixture.evidence / "dispatch-load-planner-mobile.png"), full_page=True)
                self.assertTrue(any("live-panel" in url for _, url in requests))
                self.assertFalse(any(method == "POST" for method, _ in requests), "Dispatch planner display is read-only")
                self.assertEqual(sum(url == Fixture.origin + "/neoscorpion/fuel-dispatch"
                                     for _, url in requests), 1, "Live changes must not reload Dispatch")
                with Fixture.app.app_context():
                    self.assertEqual(NeoScorpionFuelAssignment.query.count(), 1)
                    self.assertEqual([tank.actual_lbs for tank in NeoScorpionFuelTankState.query.order_by(
                        NeoScorpionFuelTankState.id)], [8000, 9000, 8000])
        finally:
            if browser:
                browser.close()
            Fixture.tearDownClass()

