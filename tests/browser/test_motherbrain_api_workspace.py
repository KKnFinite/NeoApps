"""Manage API and queue presentation on isolated, provider-free Sort data."""
import json
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from playwright.sync_api import expect

from app.extensions import db
from app.models import FlightApiReviewItem, Gateway, SortDateOperation, User
from app.services.flight_api import build_flight_api_last_poll_snapshot
from app.services.sort_timeline import ensure_sort_timeline_settings
from tests.browser import test_mobile_drawer as fixture


class ApiWorkspaceTest(unittest.TestCase):
    def test_responsive_api_and_queue(self):
        env = fixture.MobileDrawerBrowserTest
        env.setUpClass()
        self.addCleanup(env.tearDownClass)
        now = datetime(2026, 11, 17, 22)
        env.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = now
        with env.app.app_context():
            gateway = Gateway.query.filter_by(code="RFD").one()
            operation = SortDateOperation(gateway_id=gateway.id, gateway_code=gateway.code,
                sort_date=now.date(), sort_name="night", window_minutes=0,
                generated_by_user_id=User.query.filter_by(username="drawer-admin").one().id)
            db.session.add(operation)
            db.session.flush()
            gateway_id = gateway.id
            rows = []
            for kind, flight, origin, dest, when in (
                ("arrival", "5X110", "SDF", "RFD", "22:10"),
                ("departure", "5X220", "RFD", "EWR", "01:20"),
                ("departure", "5X221", "RFD", "EWR", "02:20"),
            ):
                call_sign = flight.replace("5X", "UPS")
                tail = flight.replace("5X", "N") + "UP"
                db.session.add(FlightApiReviewItem(sort_date_operation_id=operation.id,
                    gateway_id=gateway.id, gateway_code=gateway.code, sort_date=now.date(),
                    sort_name="night", mission_type=kind, flight_number=flight, call_sign=call_sign,
                    origin=origin, destination=dest, review_status="pending", review_key=flight,
                    tail_number=tail, aircraft_model="A300",
                    revised_time_utc=datetime.fromisoformat(("2026-11-17T" if kind == "arrival" else "2026-11-18T") + when) + timedelta(hours=6), api_status="Expected"))
                rows.append(dict(mission_type=kind, flight_number=flight, call_sign=call_sign,
                    origin=origin, destination=dest, tail_number=tail, provider_time_local=when,
                    api_status="Expected", match_context="No matching mission"))
            operation.flight_api_last_attempted_poll_at_utc = datetime(2026, 11, 18, 3, 50)
            operation.flight_api_last_successful_poll_at_utc = operation.flight_api_last_attempted_poll_at_utc
            operation.flight_api_last_poll_snapshot_json = json.dumps(build_flight_api_last_poll_snapshot(
                gateway, operation, rows, datetime(2026, 11, 18, 3, 50)))
            db.session.commit()
            operation_id = operation.id

        with patch("app.services.flight_api.urlopen", side_effect=AssertionError("External provider disabled")) as provider:
            for engine, width, height in (("chromium", 1440, 900), ("webkit", 390, 844), ("webkit", 820, 1180)):
                with self.subTest(width=width):
                    browser = getattr(env.pw, engine).launch()
                    try:
                        page = browser.new_page(viewport={"width": width, "height": height},
                            is_mobile=engine == "webkit", has_touch=engine == "webkit")
                        page.set_default_timeout(6000)
                        env.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = now
                        with env.app.app_context():
                            settings = ensure_sort_timeline_settings(db.session.get(Gateway, gateway_id))
                            settings.provider_enabled = False
                            db.session.commit()
                        env().login(page)
                        page.clock.install()
                        for name, path in (("api", "flight-api-test"), ("queue", "flight-api-review")):
                            response = page.goto(f"{env.origin}/motherbrain/{path}?operation_id={operation_id}")
                            self.assertEqual(response.status, 200)
                            expect(page.get_by_text("5X221", exact=True).first).to_be_attached()
                            page.evaluate("document.fonts.ready")
                            page.screenshot(path=str(env.evidence / f"{name}-{width}.png"))
                            self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), width)
                            if name == "queue":
                                row = page.locator(".api-queue tbody tr").filter(has_text="5X221")
                                row.locator("summary").click()
                                expect(row.get_by_text("N221UP")).to_be_visible()
                                bounds = row.get_by_role("button", name="ADD TO CURRENT SORT").bounding_box()
                                self.assertLessEqual(bounds["x"] + bounds["width"], width)
                            else:
                                panel = page.locator("[data-auto-poll-result]")
                                expect(panel).to_be_hidden()
                                page.locator(".api-diagnostics summary").first.click()
                                expect(page.get_by_text("CURRENT ELIGIBILITY STATUS", exact=True)).to_be_visible()
                                page.locator(".api-diagnostics summary").first.click()
                                page.get_by_role("button", name="Check auto-poll once").click()
                                expect(panel).to_be_visible()
                                page.locator(".api-replay summary").click()
                                payload = '{"arrivals":[],"departures":[]}'
                                page.locator("[name=replay_payload]").fill(payload)
                                page.get_by_role("button", name="RUN REPLAY").click()
                                expect(page.locator("[name=replay_payload]")).to_have_value(payload)
                                expect(page.locator("#flight-api-last-poll-title")).to_contain_text("REPLAY PREVIEW")

                        env.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = now.replace(hour=11)
                        with env.app.app_context():
                            settings = ensure_sort_timeline_settings(db.session.get(Gateway, gateway_id))
                            settings.provider_enabled = True
                            db.session.commit()
                        for path in ("flight-api-test", "flight-api-review"):
                            page.goto(f"{env.origin}/motherbrain/{path}")
                            requests = []
                            def observed(request):
                                if request.resource_type in ("xhr", "fetch"):
                                    requests.append(request.url)
                            page.on("request", observed)
                            expect(page.locator("[data-flight-api-auto-poll-timer]")).to_have_count(0)
                            if path == "flight-api-test":
                                page.locator(".api-diagnostics summary").first.click()
                            page.clock.fast_forward(16 * 60 * 1000)
                            page.evaluate("new Promise(resolve => requestAnimationFrame(resolve))")
                            page.remove_listener("request", observed)
                            self.assertEqual(requests, [])
                    finally:
                        browser.close()
            provider.assert_not_called()
