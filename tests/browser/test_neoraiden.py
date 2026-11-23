import unittest
from datetime import timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

from playwright.sync_api import expect
from app.extensions import db
from app.models import Gateway, SortDateMission
from tests.browser import test_mobile_drawer as fixtures
from tests.test_neoraiden import seed_sort
from tests.test_neorain_sort_refresh import configure_sort_window


class CommandCenterBrowser(unittest.TestCase):
    def test_authorized_navigation_refresh_and_board(self):
        fixture = fixtures.MobileDrawerBrowserTest
        fixture.setUpClass()
        self.addCleanup(fixture.tearDownClass)
        with fixture.app.app_context():
            gateway = Gateway.query.one()
            op = seed_sort(gateway)
            op_id = op.id
            _, start = configure_sort_window(gateway, op.sort_date)
            fixture.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = start
        evidence = Path("instance/browser-evidence/gateway-shell")
        evidence.mkdir(parents=True,exist_ok=True)
        for engine in ("chromium","webkit"):
            with self.subTest(engine=engine):
                browser = getattr(fixture.pw,engine).launch()
                try:
                    page = browser.new_page(viewport=dict(width=1366,height=768), service_workers="block")
                    page.clock.install(time=start.replace(tzinfo=ZoneInfo("America/Chicago")))
                    fixture().login(page,username="drawer-watcher")
                    fixture().ready(page,"/rfd")
                    for width, height in ((390,844),(1366,768)):
                        page.set_viewport_size(dict(width=width,height=height))
                        launcher = page.get_by_role("link",name="Open NeoRaiden")
                        expect(launcher).to_be_visible()
                        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), width+1)
                        page.screenshot(path=evidence / f"launcher-{engine}-{width}.png")
                    page.get_by_role("link",name="Open NeoRaiden").click()
                    expect(page.locator("[data-raiden]")).to_be_visible()
                    page.locator("[data-sort-choice]").select_option(index=1)
                    expect(page.locator('[data-mission]')).to_have_count(2)
                    page.get_by_label("Refresh settings").click()
                    page.locator("[data-interval]").select_option("15")
                    page.get_by_label("Refresh settings").click()
                    expect(page.locator("[data-countdown]")).to_have_count(0)
                    expect(page.locator("[data-refresh-status]")).to_be_empty()
                    with page.expect_response(lambda r:"/neoraiden/state" in r.url) as first:
                        page.locator("[data-refresh]").click()
                    self.assertEqual(first.value.status,304)
                    with fixture.app.app_context():
                        m=SortDateMission.query.filter_by(sort_date_operation_id=op_id, flight_number="FX202").one()
                        m.assigned_tail_number="N" + engine.upper()
                        db.session.commit()
                    with page.expect_response(lambda r:"/neoraiden/state" in r.url, timeout=22000) as updated:
                        pass
                    self.assertEqual(updated.value.status,200)
                    expect(page.locator("[data-board]")).to_contain_text("N"+engine.upper())
                    for w,h in ((1366,768),(1920,1080),(1024,768),(768,1024),(390,844)):
                        page.set_viewport_size(dict(width=w,height=h))
                        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), w+1)
                        if w>900:
                            page.locator(".operational-sidebar-utilities [data-operational-board-toggle]").click()
                            box=page.locator("[data-raiden]").bounding_box()
                            self.assertGreaterEqual(box["width"],w-24)
                            page.screenshot(path=evidence / f"raiden-{engine}-{w}-board.png")
                            page.locator(".operational-board-exit").click()
                        page.screenshot(path=evidence / f"raiden-{engine}-{w}.png")
                    page.route("**/neoraiden/state*",lambda route:route.fulfill(status=503,body=""))
                    automatic_requests = []
                    page.on("request", lambda req: automatic_requests.append(req.url)
                            if "/neoraiden/state" in req.url else None)
                    page.clock.set_system_time((start + timedelta(hours=8)).replace(tzinfo=ZoneInfo("America/Chicago")))
                    page.clock.fast_forward(61000)
                    page.evaluate("window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted:true}))")
                    expect(page.locator("[data-refresh-status]")).to_have_text("Outside Sort hours")
                    self.assertEqual(automatic_requests, [])
                    with page.expect_response(lambda r:"/neoraiden/state" in r.url and r.status == 503):
                        page.locator("[data-refresh]").click()
                    expect(page.locator("[data-stale]")).to_contain_text("last successful update")
                    self.assertEqual(page.locator("[data-mission]").count(),2)
                    page.unroute("**/neoraiden/state*")
                    page.route("**/neoraiden/state*",lambda route:route.fulfill(status=403,body=""))
                    page.locator("[data-refresh]").click()
                    expect(page.locator("[data-board]")).to_be_empty()
                    expect(page.locator("[data-refresh]")).to_be_disabled()
                finally:
                    browser.close()
