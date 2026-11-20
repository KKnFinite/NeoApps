"""Operational planning layouts using the authenticated local browser fixture."""
import unittest
from datetime import date, datetime, time, timedelta

from playwright.sync_api import expect

from app.extensions import db
from app.models import SortDateMission, SortDateOperation
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.sort_timeline import ensure_sort_timeline_settings
from tests.browser import test_mobile_drawer as fixture


class MissionPlanningBrowserTest(unittest.TestCase):
    def test_planning_workspaces(self):
        env = fixture.MobileDrawerBrowserTest
        env.setUpClass()
        self.addCleanup(env.tearDownClass)
        with env.app.app_context():
            gateway = ensure_default_gateway_and_nodes()
            operation = SortDateOperation(gateway_id=gateway.id, gateway_code="RFD",
                                         sort_name="night", sort_date=date(2026, 6, 24))
            db.session.add(operation)
            for direction in ("arrival", "departure"):
                inbound = direction == "arrival"
                for index, airport in enumerate(("EWR", "SDF", "PHL", "OMA", "EWR", "DEN")):
                    local = datetime(2026, 6, 24, 22) if inbound else datetime(2026, 6, 25, 2)
                    local += timedelta(minutes=index * 10)
                    db.session.add(SortDateMission(
                        sort_date_operation=operation, sort_date=operation.sort_date, gateway_code="RFD",
                        sort_name="night", mission_type=direction, mission_source="manual",
                        wave="1" if index < 3 else "2", flight_number=f"UPS{(610 if inbound else 750) + index}",
                        origin=airport if inbound else "RFD", destination="RFD" if inbound else airport,
                        assigned_tail_number=f"N{610 + index + (0 if inbound else 2)}UP",
                        planned_datetime_local=local, planned_datetime_utc=local + timedelta(hours=5)))
            db.session.commit()
            base = f"/motherbrain/operations/{operation.id}/alp"

        for engine, width, height in (("chromium", 1440, 900), ("webkit", 390, 844), ("webkit", 820, 1180)):
            with self.subTest(engine=engine, width=width):
                browser = getattr(env.pw, engine).launch()
                try:
                    page = browser.new_page(viewport={"width": width, "height": height},
                                            is_mobile=engine == "webkit", has_touch=engine == "webkit")
                    env().login(page)
                    for direction in ("arrival", "departure"):
                        response = page.goto(env.origin + f"{base}/{direction}", wait_until="domcontentloaded")
                        self.assertEqual(response.status, 200)
                        expect(page.locator(".mb-heading h1")).to_be_visible()
                        page.evaluate("document.fonts.ready")
                        page.screenshot(path=str(env.evidence / f"{direction}-{width}.png"))
                        rows = page.locator("[data-live-rows=missions] [data-live-id]")
                        self.assertEqual(rows.count(), 6)
                        mobile = width < 761
                        visible = page.locator("[data-live-rows=mobile-missions] [data-live-id]") if mobile else rows
                        expect(visible.first).to_be_in_viewport()
                        self.assertTrue(page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"))
                        expect(page.locator(".planning-review-section")).to_be_visible()
                        if width == 820:
                            self.assertTrue(visible.first.evaluate("""r => {
                                const wrap = r.closest('.table-wrap').getBoundingClientRect();
                                return [...r.querySelectorAll('input:not([type=hidden]), a, button')].every(c =>
                                    c.getBoundingClientRect().right <= wrap.right + 1);
                            }"""), "Routine mission controls must fit the tablet workspace")
                        if direction == "departure":
                            for flight, dest in (("UPS750", "1st EWR"), ("UPS754", "2nd EWR")):
                                row = visible.filter(has_text=flight)
                                expect(row).to_contain_text(dest, ignore_case=True)
                                self.assertIn(row.get_attribute("data-live-id").split(":")[1],
                                              row.get_by_role("link").first.get_attribute("href"))
                            if mobile:
                                last = visible.last
                                last.get_by_role("button", name="Swap tail").click()
                                expect(last.locator("select")).to_be_visible()
                                self.assertTrue(last.evaluate("""r => r.querySelector('select')
                                    .getBoundingClientRect().bottom <= r.getBoundingClientRect().bottom + 1"""))
                            page.get_by_role("link", name="Spares", exact=True).click()
                            expect(page.locator(".planning-spare-create-form")).to_be_visible()
                        if not mobile:
                            page.get_by_role("link", name="ALP import").click()
                            expect(page.locator("[name=paste_text]")).to_be_visible()
                            if direction == "arrival":
                                paste = "25-JUN-2026\tUPS610\tEWR\tN610UP\tA01\tScheduled\t03:00 (S)"
                                unmatched = paste.replace("UPS610", "UPS999").replace("N610UP", "N999UP")
                                page.locator("[name=paste_text]").fill("\n".join((paste, paste, unmatched, "BROKEN ROW")))
                                with page.expect_navigation():
                                    page.get_by_role("button", name="PREVIEW", exact=True).click()
                                expect(page.get_by_text("Expected 7 ALP columns.")).to_be_visible()
                                expect(page.get_by_role("button", name="APPLY PREVIEW")).to_be_visible()
                                expect(page.locator(".mb-review-flag")).to_be_visible()
                                expect(page.locator("[data-live-rows=review]")).to_contain_text("UPS0999")
                                expect(page.locator("#alp-import")).to_contain_text("DUPLICATE ROWS")
                                page.locator("#alp-import").scroll_into_view_if_needed()
                                page.screenshot(path=str(env.evidence / f"preview-{width}.png"))
                                with page.expect_navigation():
                                    page.get_by_role("button", name="CLEAR PREVIEW").click()
                                expect(page.get_by_role("button", name="APPLY PREVIEW")).to_have_count(0)
                finally:
                    browser.close()
        # Exercise a real poll response after an independent synthetic mission change.
        env.app.config.update(CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE=datetime(2026, 6, 24, 22),
                              LIVE_SCREEN_REFRESH_INTERVAL_MS=5000)
        with env.app.app_context():
            gateway = ensure_default_gateway_and_nodes()
            night = next(s for s in ensure_sort_timeline_settings(gateway).sort_settings if s.sort_name == "night")
            night.sort_window_start_local, night.sort_window_end_local = time(20), time(5)
            night.ops_window_start_local, night.ops_window_end_local = time(20), time(5)
            db.session.commit()
        browser = env.pw.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            env().login(page)
            page.goto(env.origin + base + "/departure")
            page.get_by_role("link", name="ALP import").click()
            draft = page.locator("[name=paste_text]")
            draft.fill("Unsubmitted ALP draft")
            with env.app.app_context():
                flight = SortDateMission.query.filter_by(flight_number="UPS754").one()
                flight.assigned_tail_number = "N824UP"
                db.session.commit()
            rows = page.locator("[data-live-rows=missions] tr")
            expect(rows.filter(has_text="UPS754")).to_contain_text("N824UP", timeout=35000)
            expect(rows.filter(has_text="UPS750")).to_contain_text("N612UP")
            expect(draft).to_have_value("Unsubmitted ALP draft")
        finally:
            browser.close()
