"""Local responsive smoke check for the two new NeoRain report surfaces."""

from datetime import date, datetime
from tempfile import TemporaryDirectory
from threading import Thread
from unittest import TestCase

from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

from app import create_app
from app.extensions import db
from app.models import Gateway, NeoRainDelayInfo, SortDateMission, SortDateOperation, User
from app.services.access_control import backfill_default_gateway_node_roles
from app.services.password_policy import set_user_password
from app.services.permission_rules import ensure_default_permission_rules


class NeoRainReportBrowserTest(TestCase):
    def test_desktop_and_phone_widths_and_copy(self):
        with TemporaryDirectory() as directory:
            config = type("BrowserConfig", (), {
                "SECRET_KEY": "test", "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///" + directory.replace("\\", "/") + "/rain-report.db",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            })
            app = create_app(config)
            with app.app_context():
                db.create_all()
                ensure_default_permission_rules()
                gateway = Gateway(code="RFD", name="RFD")
                db.session.add(gateway)
                db.session.flush()
                operation = SortDateOperation(gateway_id=gateway.id, gateway_code="RFD",
                                              sort_date=date(2026, 10, 10), sort_name="Night")
                db.session.add(operation)
                db.session.flush()
                departure = SortDateMission(
                    sort_date_operation_id=operation.id, sort_date=operation.sort_date,
                    gateway_code="RFD", sort_name="Night", mission_type="departure",
                    mission_source="master", wave="1", flight_number="UPS07831",
                    origin="RFD", destination="SDF", timezone="UTC",
                    planned_datetime_utc=datetime(2026, 10, 11, 1, 0),
                    actual_block_out_datetime_utc=datetime(2026, 10, 11, 1, 11),
                    departure_status="departed", assigned_tail_number="N7831UP",
                )
                db.session.add(departure)
                db.session.flush()
                db.session.add(NeoRainDelayInfo(
                    sort_date_mission_id=departure.id, minutes=11, code="RS",
                    notes="MULTIPLE LATE INBOUND AIRCRAFT AND EXTENDED WEATHER RESTRICTION",
                ))
                user = User(username="rain_browser", email="rain_browser@example.test",
                            first_name="Rain", last_name="Browser", full_name="Rain Browser",
                            employee_id="RAIN-BROWSER", email_verified_at=datetime.utcnow(),
                            role="operator", is_active=True)
                set_user_password(user, "TestPassword123!")
                db.session.add(user)
                db.session.flush()
                backfill_default_gateway_node_roles(user, role="operator")
                db.session.commit()
                operation_id = operation.id
            server = make_server("127.0.0.1", 0, app, threaded=True)
            worker = Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(headless=True)
                    try:
                        for width, height in ((1440, 900), (390, 844), (375, 568)):
                            context = browser.new_context(viewport={"width": width, "height": height}, permissions=["clipboard-read", "clipboard-write"])
                            page = context.new_page()
                            base = f"http://127.0.0.1:{server.server_port}"
                            page.goto(base + "/login")
                            page.locator('[name="email"]').fill("rain_browser@example.test")
                            page.locator('[name="password"]').fill("TestPassword123!")
                            page.locator('button[type="submit"]').click()
                            page.goto(base + f"/neorain/rainrock?operation_id={operation_id}")
                            self.assertEqual(page.locator("h1").first.inner_text(), "RAINROCK")
                            self.assertTrue(page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth"))
                            self.assertTrue(page.evaluate("""[...document.querySelectorAll('.rain-report input, .rain-report button, .rain-report select')].every(el => { const r = el.getBoundingClientRect(); return r.left >= -1 && r.right <= innerWidth + 1; })"""))
                            if width < 700:
                                self.assertEqual(page.locator(".rain-report-table tr").first.evaluate("el => getComputedStyle(el).display"), "block")
                            page.goto(base + f"/neorain/daily-briefing?operation_id={operation_id}")
                            self.assertEqual(page.locator("h1").first.inner_text(), "DAILY BRIEFING")
                            self.assertTrue(page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth"))
                            self.assertTrue(page.evaluate("""[...document.querySelectorAll('.rain-report button, .rain-report select')].every(el => { const r = el.getBoundingClientRect(); return r.left >= -1 && r.right <= innerWidth + 1; })"""))
                            page.locator("#copy-briefing").click()
                            expect(page.locator("#copy-briefing-status")).to_have_text("Copied.")
                            self.assertEqual(page.evaluate("navigator.clipboard.readText()").replace("\r\n", "\n"),
                                             page.locator("#briefing-text").inner_text())
                            context.close()
                    finally:
                        browser.close()
            finally:
                server.shutdown()
                worker.join(timeout=5)
                with app.app_context():
                    db.session.remove()
                    db.engine.dispose()
