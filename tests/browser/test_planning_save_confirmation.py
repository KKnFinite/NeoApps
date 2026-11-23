"""Planning actions must distinguish confirmed writes from a sign-in response."""
import unittest
from datetime import datetime

from flask import request
from playwright.sync_api import expect

from app.extensions import db
from app.models import Gateway, SortDateMission, SortDateParkingAssignment
from tests.browser import test_mobile_drawer as fixture
from tests.test_neoraiden import seed_sort


class PlanningSaveConfirmationTest(unittest.TestCase):
    def test_unconfirmed_draft_and_manual_reconciliation(self):
        env = fixture.MobileDrawerBrowserTest
        env.setUpClass()
        self.addCleanup(env.tearDownClass)
        with env.app.app_context():
            operation = seed_sort(Gateway.query.one())
            operation_id = operation.id
        env.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = datetime(2026, 1, 10, 12)
        @env.app.before_request
        def interrupted_action():
            if request.method == "POST" and request.path.endswith("/tail-swap"):
                return "<title>Sign in</title>", 200, {"Content-Type": "text/html"}
        for engine, flight in (("chromium", "FX101"), ("webkit", "FX202")):
            with self.subTest(engine=engine):
                browser = getattr(env.pw, engine).launch()
                try:
                    page = browser.new_page(viewport={"width": 1440, "height": 900})
                    env().login(page)
                    page.goto(f"{env.origin}/motherbrain/operations/{operation_id}/alp/departure")
                    row = page.locator("[data-live-rows=missions] [data-live-row]").filter(has_text=flight)
                    form = row.locator(".planning-tail-swap-form")
                    tail = form.locator("[name=replacement_tail]")
                    tail.fill("N999UP")
                    form.locator("[name=confirm_tail_swap]").check()
                    form.get_by_role("button").click()
                    with self.subTest(engine=engine, action="unconfirmed draft"):
                        expect(row.locator(".live-conflict-panel.is-error")).to_be_visible(timeout=2500)
                        expect(tail).to_have_value("N999UP")
                        expect(row).to_have_attribute("data-live-dirty", "true")
                    with env.app.app_context():
                        mission = SortDateMission.query.filter_by(sort_date_operation_id=operation_id, flight_number=flight).one()
                        self.assertNotEqual(mission.assigned_tail_number, "N999UP")
                    # The operator discards the draft and takes an explicit historical action.
                    page.reload()
                    with env.app.app_context():
                        parking = SortDateParkingAssignment.query.filter_by(sort_date_operation_id=operation_id).one()
                        parking.position_code = "B04" if engine == "chromium" else "B05"
                        db.session.commit()
                    row.get_by_role("button", name="CANCEL", exact=True).click()
                    expect(row.get_by_role("button", name="RESTORE")).to_be_visible(timeout=3500)
                    with self.subTest(engine=engine, action="parking-only change"):
                        other = page.locator("[data-live-rows=missions] [data-live-row]").filter(has_text="FX202")
                        expect(other).to_contain_text("B04" if engine == "chromium" else "B05", timeout=1500)
                    row.get_by_role("button", name="RESTORE").click()
                    expect(row.get_by_role("button", name="CANCEL", exact=True)).to_be_visible()
                finally:
                    browser.close()
