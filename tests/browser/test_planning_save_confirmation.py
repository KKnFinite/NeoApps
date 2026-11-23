"""Planning actions must distinguish confirmed writes from a sign-in response."""
import unittest
from datetime import datetime, timedelta

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
            if (request.method == "POST" and request.path.endswith("/tail-swap")
                    and request.form.get("replacement_tail") == "N999UP"):
                return "<title>Sign in</title>", 200, {"Content-Type": "text/html"}
        for engine, flight in (("chromium", "FX101"), ("webkit", "FX202")):
            with self.subTest(engine=engine):
                browser = getattr(env.pw, engine).launch()
                try:
                    page = browser.new_page(viewport={"width": 1440, "height": 900})
                    env().login(page)
                    page.goto(f"{env.origin}/motherbrain/operations/{operation_id}/alp/departure")
                    self.check_initial_picker(env, browser, page, operation_id, flight)
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
                    page.set_viewport_size({"width": 390, "height": 844})
                    mobile = page.locator("[data-live-rows=mobile-missions] [data-live-row]").filter(has_text=flight)
                    mobile.get_by_role("button", name="Swap tail").click()
                    choices = mobile.locator("select[name=replacement_tail]")
                    self.assertEqual(choices.locator("option").all_text_contents(), ["TAIL", "N101", "N202"])
                    choices.select_option("N202")
                    expect(choices).to_have_value("N202")
                finally:
                    browser.close()

    def check_initial_picker(self, env, browser, page, operation_id, flight):
        mobile_rows = "[data-live-rows=mobile-missions]"
        expected = ["", "N101", "N202"]
        page.set_viewport_size({"width": 390, "height": 844})
        self.assertEqual(page.locator(f"{mobile_rows} select option").count(), 0)
        row = page.locator(f"{mobile_rows} article").filter(has_text=flight)
        toggle = row.get_by_role("button", name="Swap tail")
        page.context.set_offline(True)
        try:
            toggle.focus()
            toggle.press("Enter")
            picker = row.get_by_role("combobox", name=f"Replacement tail for {flight}")
            expect(picker).to_be_visible()
            self.assertEqual(picker.locator("option").evaluate_all("opts => opts.map(o => o.value)"), expected)
            toggle.press("Tab")
            expect(picker).to_be_focused()
            picker.select_option("N202")
            toggle.press("Enter")
            toggle.press("Enter")
            expect(picker).to_have_value("N202")
            self.assertEqual(page.locator(f"{mobile_rows} select option").count(), len(expected))
        finally:
            page.context.set_offline(False)
        with browser.new_context(java_script_enabled=False, storage_state=page.context.storage_state(),
                                 viewport={"width": 390, "height": 844}) as context:
            native = context.new_page()
            self.assertEqual(native.goto(page.url).status, 200)
            fallback = native.locator(f"{mobile_rows} article").filter(has_text=flight).locator("select")
            self.assertEqual(fallback.locator("option").evaluate_all("opts => opts.map(o => o.value)"), expected)
            self.assertEqual(fallback.get_attribute("aria-label"), f"Replacement tail for {flight}")
            self.assertIsNotNone(fallback.get_attribute("required"))
        page.set_viewport_size({"width": 1440, "height": 900})
        with env.app.app_context():
            mission = SortDateMission.query.filter_by(sort_date_operation_id=operation_id, flight_number=flight).one()
            original_tail = mission.assigned_tail_number
            mission.planned_datetime_utc += timedelta(minutes=1)
            db.session.commit()
        other_flight = "FX202" if flight == "FX101" else "FX101"
        other = page.locator("[data-live-rows=missions] [data-live-row]").filter(has_text=other_flight)
        other.get_by_role("button", name="CANCEL", exact=True).click()
        expect(other.get_by_role("button", name="RESTORE")).to_be_visible()
        other.get_by_role("button", name="RESTORE").click()
        expect(other.get_by_role("button", name="CANCEL", exact=True)).to_be_visible()
        page.set_viewport_size({"width": 390, "height": 844})
        expect(row.get_by_role("combobox")).to_have_value("N202")
        row.get_by_role("button", name="Apply").click()
        expect(row.get_by_role("button", name="USE LATEST")).to_be_visible()
        with env.app.app_context():
            mission = SortDateMission.query.filter_by(sort_date_operation_id=operation_id, flight_number=flight).one()
            self.assertEqual(mission.assigned_tail_number, original_tail)
        page.set_viewport_size({"width": 1440, "height": 900})
        page.reload()
