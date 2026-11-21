"""Authenticated schedule configuration on isolated gateway records."""
import unittest
from datetime import datetime

from playwright.sync_api import expect
from werkzeug.datastructures import MultiDict

from app.extensions import db
from app.models import Gateway
from app.services.gateway_matrix import save_gateway_matrix
from app.services.sort_timeline import save_sort_timeline_from_form
from tests.browser import test_mobile_drawer as fixture


class ScheduleWorkspaceBrowserTest(unittest.TestCase):
    def test_responsive_schedule_workspaces(self):
        env = fixture.MobileDrawerBrowserTest
        env.setUpClass()
        self.addCleanup(env.tearDownClass)
        env.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = datetime(2026, 11, 17, 11)
        env.app.config["LIVE_SCREEN_REFRESH_INTERVAL_MS"] = 15000
        with env.app.app_context():
            gateway = Gateway.query.filter_by(code="RFD").one()
            weekdays = ("monday", "tuesday", "wednesday", "thursday", "friday")
            cells = [(day, "night") for day in weekdays] + [("saturday", "day")]
            save_gateway_matrix(gateway, cells)
            form = MultiDict({
                "month_key": "2026-11", "provider_enabled": "1", "provider_name": "AeroDataBox",
                "monthly_api_units": "6000", "units_per_poll": "5", "minimum_auto_poll_interval_minutes": "10",
                "night_sort_start": "21:00", "night_sort_end": "05:00", "night_planning_start": "15:00",
                "night_ops_start": "21:00", "night_ops_end": "05:00",
                "night_polling_start": "21:30", "night_polling_end": "04:30",
                "night_google_polling_start": "22:00", "night_google_polling_end": "04:00",
                "night_special_poll_time": "01:20", "day_sort_start": "12:00", "day_sort_end": "17:00",
                **{f"api_enabled_{sort}_{day}": "1" for day, sort in cells},
            })
            save_sort_timeline_from_form(gateway, form)
            db.session.commit()
        for engine, width, height in (("chromium", 1440, 900), ("webkit", 390, 844), ("webkit", 820, 1180)):
            with self.subTest(engine=engine, width=width):
                browser = getattr(env.pw, engine).launch()
                try:
                    page = browser.new_page(viewport={"width": width, "height": height},
                                            is_mobile=engine == "webkit", has_touch=engine == "webkit")
                    page.set_default_timeout(8000)
                    env().login(page)
                    page.clock.install()
                    for name, path in (("matrix", "gateway-matrix"), ("timeline", "sort-timeline?month=2026-11")):
                        response = page.goto(f"{env.origin}/motherbrain/{path}")
                        self.assertEqual(response.status, 200)
                        page.evaluate("document.fonts.ready")
                        expect(page.locator("[data-flight-api-auto-poll-timer], [data-google-live-poll-heartbeat]")).to_have_count(0)
                        idle_requests = []
                        def record_idle_request(request):
                            if request.resource_type in ("fetch", "xhr"):
                                idle_requests.append(request.url.split("?")[0])
                        page.on("request", record_idle_request)
                        page.clock.fast_forward(16 * 60 * 1000)
                        page.evaluate("new Promise(resolve => requestAnimationFrame(resolve))")
                        page.remove_listener("request", record_idle_request)
                        self.assertEqual(idle_requests, [], "Idle configuration page initiated a background request")
                        page.screenshot(path=str(env.evidence / f"{name}-{width}.png"))
                        if name == "timeline":
                            page.screenshot(path=str(env.evidence / f"timeline-full-{width}.png"), full_page=True)
                        expect(page.locator("main")).to_be_visible()
                        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), width)
                        if name == "matrix":
                            self.check_matrix(page, width)
                        else:
                            self.check_timeline(page, width)
                finally:
                    browser.close()

    def check_matrix(self, page, width):
        table = page.get_by_role("table", name="SET ACTIVE SORTS FOR RFD")
        expect(table.get_by_role("checkbox")).to_have_count(28)
        sunday = table.get_by_role("checkbox", name="Sunday, Day Sort")
        expect(sunday).not_to_be_checked()
        if width <= 390:
            save_box = page.get_by_role("button", name="SAVE GATEWAY MATRIX").bounding_box()
            self.assertLess(save_box["y"] + save_box["height"], page.viewport_size["height"] - 56)
        sunday.check()
        with page.expect_navigation(wait_until="load"):
            page.get_by_role("button", name="SAVE GATEWAY MATRIX").click()
        expect(page.get_by_text("Gateway Matrix updated.", exact=True)).to_be_visible()
        expect(sunday).to_be_checked()
        expect(table.get_by_role("checkbox", name="Monday, Night Sort")).to_be_checked()
        sunday.uncheck()
        with page.expect_response(lambda response: response.request.method == "POST" and "gateway-matrix" in response.url) as saved:
            with page.expect_navigation(wait_until="load"):
                page.get_by_role("button", name="SAVE GATEWAY MATRIX").click()
        self.assertEqual(saved.value.status, 302)
        expect(page.get_by_text("Gateway Matrix updated.", exact=True)).to_be_visible()
        page.reload()
        expect(sunday).not_to_be_checked()

    def check_timeline(self, page, width):
        status = page.locator("[data-sort-timeline-save-status]")
        clock = page.locator("[name=night_ops_end]")
        expect(clock).to_have_value("05:00")
        expect(page.locator("[name=night_planning_start]")).to_have_value("15:00")
        posts = []
        page.on("request", lambda req: posts.append(req) if req.method == "POST" and "sort-timeline" in req.url else None)
        clock.fill("2760")
        clock.blur()
        expect(status).to_contain_text("INVALID TIME")
        self.assertEqual(posts, [])
        clock.fill("0430")
        clock.blur()
        expect(status).to_have_text("SAVED")
        expect(clock).to_have_value("04:30")
        clock.fill("0500")
        clock.blur()
        expect(status).to_have_text("SAVED")

        if width == 390:
            return
        budget = page.locator("[data-sort-timeline-preview='2026-11']")
        detail = budget.locator("details")
        expect(detail.locator("[data-preview-metric=operating_days]")).to_be_hidden()
        detail.locator("summary").click()
        days = detail.locator("[data-preview-metric=operating_days]")
        previous_days = int(days.inner_text())
        offset = page.locator("[name=month_variance_11]")
        offset.locator("..").get_by_role("button", name="Increase").click()
        expect(days).to_have_text(str(previous_days + 1))
        offset.locator("..").get_by_role("button", name="Decrease").click()
        expect(days).to_have_text(str(previous_days))

        provider = page.locator("[name=provider_enabled]")
        provider.uncheck()
        expect(budget.locator("[data-provider-disabled]")).to_be_visible()
        provider.check()
        expect(budget.locator("[data-provider-disabled]")).to_be_hidden()
        card = page.locator("[data-sort-timeline-card=night]")
        card.locator("[data-add-special-poll]").click()
        field = card.locator("[data-special-new-row] input").last
        field.fill("0315")
        field.blur()
        expect(card.locator("[data-delete-value='03:15']")).to_have_count(1)
        saved = card.locator("[data-special-poll-row]").filter(has=page.locator("[data-delete-value='03:15']"))
        saved.locator("input").fill("0320")
        saved.locator("input").blur()
        expect(card.locator("[data-delete-value='03:20']")).to_have_count(1)
        page.reload()
        card.locator("[data-delete-value='03:20']").click()
        expect(status).to_have_text("SAVED")
        page.reload()
        expect(card.locator("[data-delete-value='03:15'], [data-delete-value='03:20']")).to_have_count(0)
        expect(card.locator("[data-delete-value='01:20']")).to_have_count(1)
        expect(clock).to_have_value("05:00")
