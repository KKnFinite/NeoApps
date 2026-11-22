"""Count signed-in requests from a tab spanning a configured Night Sort."""
from datetime import datetime, timedelta
from unittest import TestCase
from zoneinfo import ZoneInfo

from app.models import Gateway
from tests.browser import test_mobile_drawer as fixtures
from tests.test_neorain_sort_refresh import configure_sort_window


class PhysicalWindowBrowserTest(TestCase):
    def test_idle_operational_screens_make_no_off_day_requests(self):
        fixture = fixtures.MobileDrawerBrowserTest()
        fixture.setUpClass()
        self.addCleanup(fixture.tearDownClass)
        saturday = datetime(2026, 11, 21, 22, tzinfo=ZoneInfo("America/Chicago"))
        with fixture.app.app_context():
            gateway = Gateway.query.filter_by(code="RFD").one()
            configure_sort_window(gateway, (saturday + timedelta(days=2)).date())
            fixture.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = saturday
        paths = ("/neoscorpion/fuel-dispatch", "/neoermac/door-view", "/neosektor/driver-routing",
                 "/neosubzero/operations", "/neosubzero/ucc", "/neorain/inbound", "/neorain/outbound",
                 "/neorain/rainrock", "/neorain/daily-briefing", "/neostaffing/attendance", "/neoraiden")
        for engine, width in (("chromium", 1440), ("webkit", 390)):
            with getattr(fixture.pw, engine).launch() as browser:
                page = browser.new_page(viewport={"width": width, "height": 844}, service_workers="block")
                fixture.login(page)
                page.clock.install(time=saturday - timedelta(minutes=1))
                page.clock.pause_at(saturday)
                requests = []
                page.on("request", lambda req: requests.append((req.resource_type, req.url)))
                for path in paths:
                    with self.subTest(engine=engine, path=path):
                        response = page.goto(fixture.origin + path, wait_until="networkidle")
                        self.assertEqual(response.status, 200)
                        self.assertNotIn("/login", page.url)
                        requests.clear()
                        page.evaluate("""() => {
                          document.dispatchEvent(new Event('visibilitychange'));
                          window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted:true}));
                        }""")
                        page.clock.fast_forward(90000)
                        self.assertEqual(requests, [])
                        if path == "/neoraiden":
                            page.get_by_role("button", name="TV Mode", exact=True).click()
                            page.wait_for_load_state("networkidle")
                            requests.clear()
                            page.clock.fast_forward(90000)
                            self.assertEqual(requests, [])

    def test_restricted_workspace_crosses_both_boundaries(self):
        fixture = fixtures.MobileDrawerBrowserTest()
        fixture.setUpClass()
        self.addCleanup(fixture.tearDownClass)
        opening = datetime(2026, 11, 23, 21, tzinfo=ZoneInfo("America/Chicago"))
        closing = opening + timedelta(hours=8)
        with fixture.app.app_context():
            configure_sort_window(Gateway.query.filter_by(code="RFD").one(), opening.date())
        for engine, width in (("chromium", 1440), ("webkit", 390)):
            with self.subTest(engine=engine), getattr(fixture.pw, engine).launch() as browser:
                page = browser.new_page(viewport={"width": width, "height": 844}, service_workers="block")
                fixture.login(page)
                before = opening - timedelta(minutes=3)
                fixture.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = before
                page.clock.install(time=before - timedelta(minutes=1))
                page.clock.pause_at(before)
                url = fixture.origin + "/accounts/view-as/vendor-supervisor"
                self.assertEqual(page.goto(url, wait_until="networkidle").status, 200)
                self.assertEqual(page.locator("[data-vendor-refresh]").count(), 1)
                traffic = []
                page.on("request", lambda request: traffic.append((request.resource_type, request.url)))

                def resume_events():
                    page.evaluate("""() => {
                        document.dispatchEvent(new Event('visibilitychange'));
                        window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted:true}));
                    }""")

                resume_events()
                page.clock.run_for(120000)
                self.assertEqual(traffic, [])
                for instant in (opening, closing - timedelta(minutes=1)):
                    fixture.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = instant
                    page.clock.set_system_time(instant)
                    with page.expect_response(lambda response: "/live/revision" in response.url) as received:
                        resume_events()
                    self.assertEqual(received.value.status, 200)
                    received.value.finished()
                    page.wait_for_load_state("networkidle")
                traffic.clear()
                for instant in (closing, closing + timedelta(hours=12)):
                    fixture.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = instant
                    page.clock.set_system_time(instant)
                    resume_events()
                    page.clock.run_for(65000)
                    self.assertEqual(traffic, [])
