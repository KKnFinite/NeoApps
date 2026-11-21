"""Signed-in desktop and mobile node entry using isolated synthetic accounts."""
import re
import unittest
from datetime import datetime
from urllib.parse import urlsplit

from playwright.sync_api import expect

from app.extensions import db
from app.models import Gateway, NeoSektorOperationalSetting
from tests.browser import test_mobile_drawer as fixture


PRIMARY = {
    "neoermac": ["View Outbound", "Upcoming Pulls", "Door View", "Building Lineup", "Settings"],
    "neoscorpion": ["Fuel Dispatch", "Fueling Board", "Fueler", "Fuel Truck Driver", "Settings"],
    "neosektor": ["Tunnel Conductor", "Driver Routing", "East Ballmat", "West Ballmat", "Live Counts",
                  "Discharge", "Employees", "Stats", "Settings"],
}
HOME = {"neoermac": "view-outbound", "neoscorpion": "fuel-dispatch", "neosektor": "tunnel-conductor",
        "neorain": "inbound", "neosubzero": "operations"}


class DesktopNodeNavigationTest(unittest.TestCase):
    def test_responsive_entry_and_navigation(self):
        env = fixture.MobileDrawerBrowserTest
        env.setUpClass()
        self.addCleanup(env.tearDownClass)
        env.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = datetime(2026, 11, 23, 12)
        with env.app.app_context():
            gateway = Gateway.query.filter_by(code="RFD").one()
            db.session.add(NeoSektorOperationalSetting(gateway_id=gateway.id, gateway_code="RFD",
                                                      integration_mode="neo_only"))
            db.session.commit()

        for engine, width, height in (("chromium", 1440, 900), ("webkit", 390, 844)):
            with self.subTest(engine=engine):
                browser = getattr(env.pw, engine).launch()
                try:
                    page = browser.new_page(viewport={"width": width, "height": height},
                                            is_mobile=width < 901, has_touch=width < 901)
                    page.set_default_timeout(7500)
                    page.add_init_script("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1', 'seen')")
                    env().login(page)
                    page.clock.install()
                    for node in ("neoermac", "neoscorpion", "neosektor", "neosubzero", "neorain", "neostaffing"):
                        with self.subTest(node=node):
                            desktop = width > 900
                            if node in ("neorain", "neosubzero"):
                                remembered = "outbound" if node == "neorain" else "deice-log"
                                env().ready(page, f"/{node}/{remembered}")
                            else:
                                remembered = ""
                            target = HOME.get(node, "") if desktop else ""
                            path = "/" + node + ("/" + target if target else "")
                            expected = re.compile(re.escape(env.origin + path) + r"/?(?:\?.*)?$")
                            page.goto(env.origin + "/" + node, wait_until="commit")
                            expect(page).to_have_url(expected)
                            page.evaluate("document.fonts.ready")
                            page.wait_for_load_state("load")
                            page.screenshot(path=str(env.evidence / f"{node}-{width}.png"))
                            requests = []
                            def record(req):
                                if req.resource_type in ("xhr", "fetch"):
                                    requests.append(urlsplit(req.url).path)
                            page.on("request", record)
                            page.clock.fast_forward(65_000)
                            page.evaluate("new Promise(resolve => requestAnimationFrame(resolve))")
                            page.remove_listener("request", record)
                            self.assertEqual([path for path in requests if path.startswith("/" + node + "/")], [])
                            if node in PRIMARY:
                                if desktop:
                                    self.check_sidebar(page, node)
                                else:
                                    expect(page.locator("[data-node-desktop-dashboard]")).to_be_visible()
                            if node == "neostaffing":
                                if desktop:
                                    tools = page.locator(".v11-sidebar .v11-more")
                                    expect(tools.locator("summary")).to_have_text("UNDER CONSTRUCTION")
                                    self.assertFalse(tools.evaluate("e => e.open"))
                                else:
                                    env().ready(page, "/neostaffing/menu")
                                    expect(page.locator(".v11-page .v11-more summary")).to_have_text("More Tools")
                                continue
                            # Launch from the actual gateway and return with one Back.
                            if node in ("neorain", "neosubzero"):
                                env().ready(page, f"/{node}/{remembered}")
                            env().ready(page, "/rfd")
                            page.locator(".gateway-node-" + node[3:] + ":visible").click()
                            expect(page).to_have_url(expected)
                            page.go_back(wait_until="domcontentloaded")
                            expect(page).to_have_url(env.origin + "/rfd")
                            # A direct URL remains available irrespective of entry defaults.
                            if node in PRIMARY and desktop:
                                direct = {"neoermac":"door-view", "neoscorpion":"quick-fuel", "neosektor":"wbm"}[node]
                                env().ready(page, f"/{node}/{direct}")
                                page.locator(".operational-sidebar-logo").click()
                                expect(page).to_have_url(expected)
                            page.goto(env.origin + "/" + node + "/", wait_until="commit")
                            expect(page).to_have_url(expected)
                finally:
                    browser.close()

    def check_sidebar(self, page, node):
        menu = page.locator("[data-operational-sidebar] .node-desktop-side-menu")
        expect(menu).to_be_visible()
        self.assertEqual(menu.locator(":scope > a .operational-side-link-label").all_text_contents(), PRIMARY[node])
        expect(menu.locator("a[aria-current=page] .operational-side-link-label")).to_have_text(PRIMARY[node][0])
        if node == "neosektor":
            self.assertTrue(menu.get_by_role("link", name="East Ballmat").get_attribute("href").endswith("/ebm"))
            self.assertTrue(menu.get_by_role("link", name="West Ballmat").get_attribute("href").endswith("/wbm"))
            return
        group = menu.locator("details")
        self.assertFalse(group.evaluate("e => e.open"))
        group.locator("summary").click()
        names = (["Tug Assignments"] if node == "neoermac"
                 else ["Truck Manager", "Quick Fuel", "Reports", "Fuel History", "Hanzo"])
        self.assertEqual(group.locator("a .operational-side-link-label").all_text_contents(), names)
        group.get_by_role("link", name=names[0]).click()
        expect(group.locator("a[aria-current=page] .operational-side-link-label")).to_have_text(names[0])
        self.assertTrue(group.evaluate("e => e.open"))
        if node == "neoscorpion":
            page.goto(page.url.split("/neoscorpion/")[0] + "/neoscorpion/reports/fuel")
            expect(group.locator("a[aria-current=page] .operational-side-link-label")).to_have_text("Reports")
