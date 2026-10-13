"""Focused real-browser checks for read-only Hanzo and Dispatch presentation."""
from datetime import date, datetime, timedelta
import unittest
from unittest.mock import patch

from playwright.sync_api import expect

from app.extensions import db
from app.models import (
    NeoScorpionFuelAssignment, NeoScorpionFuelerNickname, NeoScorpionFuelTankState,
    NeoScorpionFuelTruck, NeoScorpionFuelWorkState, NeoScorpionSortTruck,
    SortDateMission, SortDateOperation, SortDateParkingAssignment, SortDateTailState, User,
)
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.neoscorpion import fuel_dispatch_context as canonical_dispatch_context
from tests.browser import test_mobile_drawer as fixture_module


class NeoScorpionUiBatchBrowserTest(unittest.TestCase):
    def test_hanzo_and_dispatch_desktop_mobile_and_live_fragment(self):
        fixture = fixture_module.MobileDrawerBrowserTest
        fixture.setUpClass()
        browser = None
        try:
            with fixture.app.app_context():
                gateway = ensure_default_gateway_and_nodes()
                admin = User.query.filter_by(username="drawer-admin").one()
                operation = SortDateOperation(
                    generated_by_user_id=admin.id, gateway_id=gateway.id,
                    gateway_code=gateway.code, sort_date=date.today(), sort_name="night",
                    window_minutes=60,
                )
                db.session.add(operation)
                db.session.flush()
                truck = NeoScorpionFuelTruck(
                    gateway_id=gateway.id, truck_number="422808", capacity_gallons=20000,
                )
                db.session.add(truck)
                db.session.flush()
                mission = SortDateMission(
                    sort_date_operation_id=operation.id, sort_date=operation.sort_date,
                    gateway_code=gateway.code, sort_name="night", mission_type="departure",
                    mission_source="manual", flight_number="UPS550", origin=gateway.code,
                    destination="SDF", assigned_tail_number="N412UP",
                    timezone="America/Chicago", planned_source="manual",
                    planned_datetime_utc=datetime.utcnow() + timedelta(hours=2),
                    planned_fuel_load=50000, fuel_status="waiting", departure_status="loading",
                )
                db.session.add(mission)
                db.session.flush()
                assignment = NeoScorpionFuelAssignment(
                    sort_date_operation_id=operation.id, sort_date_mission_id=mission.id,
                    assigned_fueler_user_id=admin.id, assigned_truck_id=truck.id,
                    confirmed_tail_number="N412UP",
                )
                db.session.add(assignment)
                db.session.flush()
                work = NeoScorpionFuelWorkState(
                    fuel_assignment_id=assignment.id, tail_number="N412UP",
                    on_at_utc=datetime(2026, 8, 18, 7, 15),
                    off_at_utc=datetime(2026, 8, 18, 7, 40),
                    apu_running=False, apu_allowance_lbs=0,
                )
                db.session.add(work)
                db.session.flush()
                for code, amount in (("left", 10000), ("ctr", 29000), ("right", 10000)):
                    db.session.add(NeoScorpionFuelTankState(
                        fuel_work_state_id=work.id, tank_code=code,
                        remaining_lbs=amount, actual_lbs=amount,
                    ))
                db.session.add_all((
                    NeoScorpionSortTruck(
                        sort_date_operation_id=operation.id, fuel_truck_id=truck.id,
                        status="available", starting_gallons=10000, current_gallons=10000,
                    ),
                    NeoScorpionFuelerNickname(
                        gateway_id=gateway.id, user_id=admin.id,
                        nickname="JAKE", nickname_key="jake",
                    ),
                    SortDateTailState(
                        sort_date=operation.sort_date, gateway_code=gateway.code,
                        sort_name="night", tail_number="N412UP", aircraft_type="757",
                        aircraft_type_source="derived",
                    ),
                    SortDateParkingAssignment(
                        sort_date_operation_id=operation.id, tail_number="N412UP",
                        ramp_code="A", position_code="A4", lane_number=1,
                    ),
                ))
                db.session.commit()
                operation_id = operation.id

            def dispatch_view(gateway, **kwargs):
                context = canonical_dispatch_context(gateway, **kwargs)
                for row in context["rows"]:
                    row["normal_completion_ready"] = True  # Show the existing action for geometry only.
                return context

            with patch("app.services.neoscorpion.current_existing_operational_sort_operations",
                       side_effect=lambda gateway, now=None: [db.session.get(SortDateOperation, operation_id)]), \
                 patch("app.neonodes.neoscorpion.routes.fuel_dispatch_context", side_effect=dispatch_view):
                browser = fixture.pw.chromium.launch()
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                fixture().login(page)
                for width, height in ((1440, 900), (390, 844)):
                    page.set_viewport_size({"width": width, "height": height})
                    fixture().ready(page, "/neoscorpion/hanzo")
                    card = page.locator(".neoscorpion-hanzo-card").first
                    expect(card.locator(".neoscorpion-hanzo-fueling")).to_contain_text("JAKE")
                    expect(card.locator(".neoscorpion-hanzo-fueling")).to_contain_text("422808")
                    expect(card.locator(".is-fuel-short")).to_have_count(2)
                    self.assertEqual(card.locator(".is-fuel-short").first.evaluate(
                        "el => getComputedStyle(el).color"), "rgb(255, 102, 102)")
                    self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), width + 1)
                    page.screenshot(path=str(fixture.evidence / f"scorpion-hanzo-batch1-{width}.png"), full_page=True)

                    fixture().ready(page, "/neoscorpion/fuel-dispatch")
                    row = page.locator(".neoscorpion-dispatch-primary-row").first
                    buttons = row.locator(".neoscorpion-dispatch-action-cell .action-button")
                    complete = buttons.filter(has_text="COMPLETE").first
                    update = buttons.filter(has_text="UPDATE").first
                    if width > 760:
                        expect(complete).to_be_visible()
                        measures = complete.evaluate("el => ({client: el.clientWidth, scroll: el.scrollWidth, width: el.getBoundingClientRect().width, height: el.getBoundingClientRect().height, font: getComputedStyle(el).fontSize})")
                        other = update.evaluate("el => ({width: el.getBoundingClientRect().width, height: el.getBoundingClientRect().height, font: getComputedStyle(el).fontSize})")
                        self.assertLessEqual(measures["scroll"], measures["client"] + 1, measures)
                        self.assertAlmostEqual(measures["width"], other["width"], delta=.1)
                        self.assertEqual(measures["height"], other["height"])
                        self.assertEqual(measures["font"], other["font"])
                    self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), width + 1)
                    page.screenshot(path=str(fixture.evidence / f"scorpion-dispatch-batch1-{width}.png"), full_page=True)
                    fragment = page.request.get(fixture.origin + "/neoscorpion/fuel-dispatch/live-panel")
                    self.assertEqual(fragment.status, 200)
                    self.assertIn("neoscorpion-truck-activity", fragment.json()["html"])
                with fixture.app.app_context():
                    selection = NeoScorpionSortTruck.query.filter_by(
                        sort_date_operation_id=operation_id,
                    ).one()
                    selection.status = "topping_off"
                    db.session.commit()
                refreshed = page.request.get(fixture.origin + "/neoscorpion/fuel-dispatch/live-panel")
                self.assertEqual(refreshed.status, 200)
                self.assertIn("TOPPING OFF", refreshed.json()["html"])
        finally:
            if browser:
                browser.close()
            fixture.tearDownClass()
