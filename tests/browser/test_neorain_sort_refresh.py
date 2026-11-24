"""Observe real signed-in network traffic across the overnight Sort boundary."""
from datetime import timedelta
from threading import Thread
from unittest import TestCase
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

from app.extensions import db
from app.models import SortDateOperation
from app.services.gateway_matrix import save_gateway_matrix
from app.services.google_motherbrain_live_polling import set_google_motherbrain_live_polling_enabled
from tests import test_neorain_sort_refresh as fixtures


class NeoRainSortRefreshBrowserTest(TestCase):
    def test_desktop_chromium(self):
        self.exercise("chromium", 1440)

    def test_mobile_webkit(self):
        self.exercise("webkit", 390)

    def exercise(self, engine, width):
        case = fixtures.NeoRainSortRefreshTest()
        case.setUp()
        self.addCleanup(case.doCleanups)
        app = case.app
        app.config.update(SESSION_COOKIE_SECURE=False, LIVE_SCREEN_REFRESH_INTERVAL_MS=5000, RAINROCK_REFRESH_INTERVAL_MS=1000,
                          NEORAIN_BRIEFING_REFRESH_MS=1000)
        old = SortDateOperation(gateway_id=case.case.gateway.id, gateway_code="RFD",
                                sort_date=case.operation.sort_date - timedelta(days=1), sort_name="night")
        db.session.add(old)
        gateway = case.case.gateway
        save_gateway_matrix(gateway, [(case.operation.sort_date.strftime("%A"), "night")])
        set_google_motherbrain_live_polling_enabled(gateway, "night", True)
        db.session.commit()
        operation_id, old_id = case.operation.id, old.id
        server = make_server("127.0.0.1", 0, app, threaded=True)
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(worker.join, 5)
        self.addCleanup(server.shutdown)
        origin = f"http://127.0.0.1:{server.server_port}"
        zone = ZoneInfo("America/Chicago")
        endpoints = {"inbound": "revision", "outbound": "revision", "rainrock": "state",
                     "daily-briefing": "state"}

        with sync_playwright() as pw, getattr(pw, engine).launch() as browser:
            context = browser.new_context(viewport={"width": width, "height": 820}, service_workers="block")
            context.route("**/motherbrain/google-live-poll/execute",
                          lambda route: route.fulfill(json={"continue_heartbeat": True}))
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(origin + "/login")
            page.locator("[name=email]").fill(case.case.actor.email)
            page.locator("[name=password]").fill("TestPassword123!")
            page.locator("button[type=submit]").click()
            page.wait_for_url(lambda url: "/login" not in url)
            page.clock.install(time=(case.start - timedelta(minutes=2)).replace(tzinfo=zone))
            # Freeze timers so requests before and after close are checked deterministically.
            page.clock.pause_at((case.start - timedelta(minutes=1)).replace(tzinfo=zone))
            requests = []
            page.on("request", lambda request: requests.append(request.url) if
                    "automatic=1" in request.url or "/google-live-poll/execute" in request.url else None)
            # A non-Rain shell also has to stop locally when left open across close.
            close = case.start + timedelta(hours=8)
            before_close = close - timedelta(seconds=10)
            app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = before_close
            page.clock.set_system_time(before_close.replace(tzinfo=zone))
            with page.expect_response(lambda response: "/google-live-poll/execute" in response.url):
                self.assertEqual(page.goto(origin + "/rfd").status, 200)
            requests.clear()
            app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = close
            page.clock.run_for(70000)
            self.assertEqual(requests, [])
            for screen, endpoint in endpoints.items():
                with self.subTest(screen=screen):
                    base = f"{origin}/neorain/{screen}"
                    report = endpoint == "state"
                    url = base + (f"?operation_id={operation_id}" if report else "")
                    app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = case.start - timedelta(minutes=1)
                    page.clock.set_system_time((case.start - timedelta(minutes=1)).replace(tzinfo=zone))
                    self.assertEqual(page.goto(url).status, 200)
                    if report:
                        asset = f"neorain_{screen.replace('-', '_')}.js?v="
                        self.assertEqual(page.locator(f'script[src*="{asset}"]').count(), 1)
                    requests.clear()
                    page.clock.run_for(59000)
                    self.assertEqual(requests, [])

                    at = case.start + timedelta(hours=7, minutes=58)
                    app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = at
                    page.clock.set_system_time(at.replace(tzinfo=zone))
                    with page.expect_response(lambda response: f"/{screen}/{endpoint}" in response.url) as poll:
                        self.assertEqual(page.goto(url).status, 200)
                        page.clock.run_for(6000)
                    self.assertTrue(poll.value.json()["refresh"]["auto_refresh_enabled"])
                    self.assertTrue(any(f"/{screen}/{endpoint}" in request for request in requests))

                    if screen == "outbound":
                        self.assertTrue(any("/google-live-poll/execute" in request for request in requests))

                    if report:
                        old_url = f"{base}?operation_id={old_id}"
                        self.assertEqual(page.goto(old_url).status, 200)
                        requests.clear()
                        page.clock.run_for(70000)
                        self.assertEqual(requests, [])
                        self.assertEqual(page.goto(url).status, 200)

                    end = case.start + timedelta(hours=8)
                    app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = end
                    page.clock.set_system_time(end.replace(tzinfo=zone))
                    requests.clear()
                    page.clock.run_for(70000)
                    self.assertEqual(requests, [])
                    self.assertEqual(page.goto(url).status, 200)
                    page.clock.run_for(70000)
                    self.assertEqual(requests, [])
            self.assertEqual(errors, [])
