"""Measure the workspace itself on both sides of the desktop breakpoint."""
import unittest
from pathlib import Path

from app.extensions import db
from app.models import Gateway, NeoSektorOperationalSetting
from tests.browser import test_mobile_drawer as fixtures


class BoardGeometry(unittest.TestCase):
    def test_enter_restore_across_operational_nodes(self):
        fixture = fixtures.MobileDrawerBrowserTest
        fixture.setUpClass()
        self.addCleanup(fixture.tearDownClass)
        with fixture.app.app_context():
            gateway = Gateway.query.one()
            db.session.add(NeoSektorOperationalSetting(gateway_id=gateway.id,
                           gateway_code=gateway.code, integration_mode="neo_only"))
            db.session.commit()
        for engine in ("chromium", "webkit"):
            browser = getattr(fixture.pw, engine).launch()
            try:
                page = browser.new_page()
                fixture().login(page)
                page.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1','seen')")
                for path, board in (
                    ("/motherbrain/manage-sort", ".manage-sort-page"),
                    ("/neoermac/door-view", ".neoermac-door-shell"),
                    ("/neoscorpion/fuel-dispatch", ".neoscorpion-screen"),
                    ("/neorain/rainrock", ".rainrock"),
                    ("/neosektor/live-counts", "#sektor-live-display"),
                    ("/neosubzero/ucc", ".ucc-board-viewport"),
                ):
                    for width, height in ((1366,768),(1920,1080),(1024,768),(390,844)):
                        with self.subTest(engine=engine,path=path,width=width):
                            page.set_viewport_size(dict(width=width,height=height))
                            fixture().ready(page,path)
                            footer=page.locator(".operational-sidebar-utilities")
                            if width < 901:
                                self.assertFalse(footer.is_visible())
                                self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"),width+1)
                                continue
                            entry=footer.locator(":scope > :last-child")
                            self.assertEqual(entry.inner_text().splitlines()[-1].lower(),"board view")
                            entry.click()
                            page.locator(".operational-board-exit").wait_for()
                            bounds=page.locator(board).first.bounding_box()
                            self.assertLessEqual(bounds["x"],12)
                            self.assertGreaterEqual(bounds["width"],width-24)
                            self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"),width+1)
                            page.locator(".operational-board-exit").click()
                            self.assertTrue(footer.is_visible())
                page.set_viewport_size(dict(width=1366, height=768))
                fixture().ready(page, "/neosubzero/ucc")
                page.locator("[data-ucc-tv-enter]").click()
                tv_exit = page.locator(".ucc-tv-wallboard .ucc-tv-exit")
                tv_exit.wait_for()
                evidence = Path("instance/browser-evidence/gateway-shell")
                evidence.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=evidence / f"ducc-{engine}-tv.png")
                self.assertEqual(page.locator(".ucc-tv-ramp:visible").count(), 5)
                self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 1367)
                tv_exit.click()
                self.assertTrue(page.locator("[data-ucc-tv-enter]").is_visible())
                fixture().ready(page, "/neosektor/driver-routing?tv=1")
                self.assertTrue(page.locator(".sektor-tv-exit").is_visible())
                self.assertFalse(page.locator("[data-operational-sidebar]").is_visible())
                self.assertLessEqual(page.evaluate("document.documentElement.scrollHeight"), 769)
                page.locator(".sektor-tv-exit").click()
                page.locator(".sektor-tv-entry").wait_for()
                self.assertEqual(page.locator("[data-driver-wave]:visible").count(), 2)
            finally:
                browser.close()
