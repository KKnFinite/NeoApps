"""Signed-in planning pages at desktop, tablet and phone dimensions."""
import unittest
from datetime import date, datetime, time

from app.extensions import db
from app.models import GatewaySortMatrix, MasterFlightSchedule, SortDateOperation
from app.services.sort_timeline import ensure_sort_timeline_settings
from app.services.access_control import ensure_default_gateway_and_nodes
from tests.browser import test_mobile_drawer as fixture


class PlanningBodiesBrowserTest(unittest.TestCase):
    def test_signed_in_layouts(self):
        env = fixture.MobileDrawerBrowserTest
        env.setUpClass()
        self.addCleanup(env.tearDownClass)
        env.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = datetime(2026, 11, 23, 22)
        with env.app.app_context():
            gateway = ensure_default_gateway_and_nodes()
            op = SortDateOperation(gateway_id=gateway.id, gateway_code="RFD",
                sort_date=date(2026, 11, 23), sort_name="night", first_wave_window_minutes=20,
                second_wave_window_minutes=15)
            db.session.add(op)
            db.session.add(GatewaySortMatrix(gateway_id=gateway.id, gateway_code="RFD",
                day_of_week="monday", sort_name="night", is_active=True))
            night = next(s for s in ensure_sort_timeline_settings(gateway).sort_settings if s.sort_name == "night")
            night.sort_window_start_local, night.sort_window_end_local = time(21), time(5)
            for direction in ("arrival", "departure"):
                inbound = direction == "arrival"
                for index, airport in enumerate(("EWR", "SDF", "PHL", "EWR")):
                    db.session.add(MasterFlightSchedule(
                        gateway_id=gateway.id, gateway_code="RFD", sort_name="night", mission_type=direction,
                        flight_number=f"UPS{(610 if inbound else 720) + index}", aircraft_type="757",
                        origin=airport if inbound else "RFD", destination="RFD" if inbound else airport,
                        active_days="monday,tuesday,wednesday,thursday", active=index != 2,
                        planned_time_local=time(21 if inbound else 2, index * 10),
                        pure_pull_time_local=None if inbound else time(1, index * 10),
                        mix_pull_time_local=None if inbound else time(1, index * 10 + 5)))
            db.session.commit()
            detail_url = f"/motherbrain/operations/{op.id}"

        for engine, width, height in (("chromium", 1440, 900), ("webkit", 390, 844), ("webkit", 820, 1180)):
            with self.subTest(engine=engine, width=width):
                browser = getattr(env.pw, engine).launch()
                try:
                    page = browser.new_page(viewport={"width": width, "height": height},
                                            is_mobile=engine == "webkit", has_touch=engine == "webkit")
                    env().login(page)
                    for name, path in (("schedule", "/motherbrain/master-schedule"),
                                       ("sort", "/motherbrain/manage-sort"), ("operation", detail_url)):
                        env().ready(page, path)
                        page.screenshot(path=str(env.evidence / f"{name}-{width}.png"))
                        body = page.locator("main .mb-planning")
                        self.assertTrue(body.is_visible())
                        self.assertTrue(page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"))
                        if name == "schedule":
                            for table in page.locator(".master-board-table").all():
                                self.assertEqual(table.locator("tbody tr").count(), 4)
                                row = table.locator("tbody tr").first
                                self.assertTrue(row.evaluate("""row => [...row.querySelectorAll(
                                    'input[type=text], select, button, .master-weekdays label'
                                )].every(control => {
                                    const r = control.getBoundingClientRect();
                                    const bounds = row.closest('.table-wrap').getBoundingClientRect();
                                    return r.width > 0 && r.left >= Math.max(0, bounds.left) - 1
                                        && r.right <= Math.min(innerWidth, bounds.right) + 1;
                                })"""))
                                headings = table.locator("thead th")
                                self.assertEqual(headings.count(), row.locator("td").count())
                        elif name == "sort":
                            self.assertTrue(page.get_by_role("link", name="Operation settings").is_visible())
                            self.assertIn("20 MIN", body.inner_text())
                            page.locator(".mb-disclosure summary").click()
                            self.assertTrue(page.get_by_text("Sort Timeline holds", exact=False).is_visible())
                        else:
                            window = page.locator("[data-global-window-input]")
                            window.fill("18")
                            self.assertTrue(page.locator("[data-wave-window-input]").first.is_disabled())
                            with page.expect_navigation(wait_until="load"):
                                page.get_by_role("button", name="UPDATE WINDOW").click()
                            self.assertEqual(window.input_value(), "18")
                            window.fill("")
                            with page.expect_navigation(wait_until="load"):
                                page.get_by_role("button", name="UPDATE WINDOW").click()
                            self.assertEqual(window.input_value(), "")
                            self.assertFalse(page.locator("[data-wave-window-input]").first.is_disabled())
                            with env.app.app_context():
                                saved = db.session.get(SortDateOperation, int(detail_url.rsplit("/", 1)[1]))
                                self.assertIsNone(saved.window_minutes)
                                self.assertEqual(saved.first_wave_window_minutes, 20)
                finally:
                    browser.close()
