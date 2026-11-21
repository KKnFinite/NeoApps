"""Actual admin pages with a local Sort and a stubbed, counted Google reader."""
import json
import unittest
from datetime import date, datetime
from unittest.mock import patch

from playwright.sync_api import expect

from app.extensions import db
from app.models import Gateway, SortDateMission, SortDateOperation
from app.services.live_screen_registry import registered_live_screen_keys
from tests.browser import test_mobile_drawer as fixture
from tests.test_google_motherbrain_sheets import FIXTURE_PATH, reader_config


class AdminWorkspaceTest(unittest.TestCase):
    def test_settings_and_explicit_preview(self):
        env = fixture.MobileDrawerBrowserTest
        env.setUpClass()
        self.addCleanup(env.tearDownClass)
        env.app.config.update(reader_config(), CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE=datetime(2026, 8, 5, 11))
        with env.app.app_context():
            gateway = Gateway.query.filter_by(code="RFD").one()
            operation = SortDateOperation(gateway_id=gateway.id, gateway_code="RFD",
                                          sort_date=date(2026, 8, 5), sort_name="night")
            db.session.add(operation)
            db.session.flush()
            db.session.add(SortDateMission(sort_date_operation_id=operation.id, sort_date=operation.sort_date,
                gateway_code="RFD", sort_name="night", mission_type="departure", mission_source="manual",
                flight_number="UPS724", origin="RFD", destination="SDF", assigned_tail_number="N724UP"))
            db.session.commit()
            operation_id = operation.id

        envelope = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        envelope["snapshot"]["inbound"]["alp_rows"] = [
            dict(sheet_row=16, date="2026-08-05", flight_number="UPS1487", origin="DTW",
                 tail_number="N152UP", parking="B01", status="ARR", time="23:09 (A)")]
        envelope["snapshot"]["outbound"]["tail_swaps"] = [
            dict(sheet_row=17, flight_number="UPS724", destination="SDF", new_tail="N725UP")]
        envelope["snapshot"]["parking"]["assignments"] = [dict(tail_number="N724UP", position="A01")]
        with patch("app.neomotherbrain.routes.read_google_motherbrain_envelope", return_value=envelope) as reader:
            for engine, width, height in (("chromium", 1440, 900), ("webkit", 390, 844), ("webkit", 820, 1180)):
                with self.subTest(width=width):
                    browser = getattr(env.pw, engine).launch()
                    try:
                        page = browser.new_page(viewport={"width": width, "height": height},
                                                is_mobile=engine == "webkit", has_touch=engine == "webkit")
                        page.set_default_timeout(6000)
                        env().login(page)
                        page.clock.install()
                        start = reader.call_count
                        for name, suffix in (("settings", ""), ("integrations", "/integrations"),
                                             ("timings", "/node-refresh-timings"), ("preview", None)):
                            if suffix is None:
                                page.goto(f"{env.origin}/motherbrain/operations/{operation_id}")
                                button = page.get_by_role("button", name="READ GOOGLE CURRENT SORT")
                                page.locator("[data-google-current-sort-reader] > summary").click()
                                self.assertEqual(reader.call_count, start)
                                button.click()
                                expect(page.locator("[data-google-current-sort-preview]")).to_be_visible()
                                self.assertEqual(reader.call_count, start + 1)
                            else:
                                response = page.goto(f"{env.origin}/motherbrain/system-settings{suffix}")
                                self.assertEqual(response.status, 200)
                                self.assertEqual(reader.call_count, start)
                            page.evaluate("document.fonts.ready")
                            page.screenshot(path=str(env.evidence / f"{name}-{width}.png"))
                            self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), width)
                            if name == "timings":
                                self.check_interval_save(page, width)
                            elif name == "integrations":
                                self.check_authority_controls(page)
                            elif name == "preview":
                                self.check_comparison(page, width)
                            self.assertEqual(reader.call_count, start + (name == "preview"))
                            idle = []
                            def observe(request):
                                if request.resource_type in ("fetch", "xhr"):
                                    idle.append(request.url)
                            page.on("request", observe)
                            page.clock.fast_forward(16 * 60 * 1000)
                            page.evaluate("new Promise(resolve => requestAnimationFrame(resolve))")
                            page.remove_listener("request", observe)
                            self.assertEqual(idle, [])
                        with page.expect_navigation(wait_until="load"):
                            page.get_by_role("button", name="READ GOOGLE AGAIN").click()
                        expect(page.locator("[data-google-current-sort-preview]")).to_be_visible()
                        self.assertEqual(reader.call_count, start + 2)
                    finally:
                        browser.close()

    def check_interval_save(self, page, width):
        rows = page.locator("[data-refresh-screen]")
        self.assertEqual(set(rows.evaluate_all("rows => rows.map(row => row.dataset.refreshScreen)")),
                         set(registered_live_screen_keys()))
        row = page.locator('[data-refresh-screen="neomotherbrain.arrival_planning"]')
        interval = row.get_by_role("combobox", name="Arrival Planning refresh interval")
        button = row.get_by_role("button", name="Save Arrival Planning interval")
        if width >= 820:
            self.assertLess(abs(button.bounding_box()["y"] - interval.bounding_box()["y"]), 5)
        for value in ("15", "default"):
            interval.select_option(value)
            with page.expect_navigation(wait_until="load"):
                button.click()
            expect(page.get_by_text("Node refresh timing saved.", exact=True)).to_be_visible()
            expect(interval).to_have_value(value)

    def check_authority_controls(self, page):
        mirror = page.locator("[data-neorain-mirror]")
        expect(mirror.locator("[data-rain-mirror-state]")).to_have_text("OFF")
        expect(mirror.get_by_role("button", name="Turn on")).to_be_disabled()
        expect(mirror).to_contain_text("require NEO PRIMARY + GOOGLE MIRROR")
        for form_attribute in ("data-neosektor-mode-form", "data-neorain-mode-form"):
            form = page.locator(f"[{form_attribute}]")
            form.locator("xpath=parent::details").locator("summary").click()
            self.assertEqual(set(form.get_by_role("radio").evaluate_all("inputs => inputs.map(i => i.value)")),
                             {"google_primary", "neo_primary_google_mirror", "neo_only"})
            expect(form.locator("input:checked")).to_have_value("google_primary")
            form.locator("xpath=parent::details").locator("summary").click()

    def check_comparison(self, page, width):
        for key, expected in (("inbound-unmatched", "UPS1487"), ("outbound-missing", "UPS724"),
                              ("tail-swaps-pending", "N725UP"), ("parking-add", "A01")):
            group = page.locator(f'[data-preview-category="{key}"]')
            group.locator(":scope > summary").click()
            expect(group.get_by_role("table")).to_contain_text(expected)
            group.get_by_text("Full record", exact=True).first.click()
            expect(group.locator("pre").first).to_be_visible()
            self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), width)
            group.locator(":scope > summary").click()
