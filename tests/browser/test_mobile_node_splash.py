"""Responsive NeoSubZero and NeoRain entry using signed-in synthetic accounts."""
import re
import unittest
from datetime import date, datetime

from playwright.sync_api import expect

from app.extensions import db
from app.models import Gateway, GatewayMembership, GatewayNodeRole, NeoNode, SortDateOperation, User
from app.services.neosubzero_preferences import set_neosubzero_operations_preferences
from tests.browser import test_mobile_drawer as fixture


SUBZERO = "neosubzero"
RAIN = "neorain"
MENUS = {
    SUBZERO: {
        "DUCC": "/neosubzero/ucc",
        "Deice Dispatch": "/neosubzero/operations",
        "Deicer": "/neosubzero/deicer-mobile",
        "Deice Log": "/neosubzero/deice-log",
        "Deicer Management": "/neosubzero/deicer-management",
        "Truck Management": "/neosubzero/truck-management",
        "CryoCheck": "https://cryocheck.onrender.com",
        "Settings": "/neosubzero/settings",
    },
    RAIN: {
        "Inbound": "/neorain/inbound",
        "Outbound": "/neorain/outbound",
        "Reports": "/neorain/reports",
        "Load Planner Lineup": "/neorain/load-planner-lineup",
        "Settings": "/neorain/settings",
    },
}
DESKTOP = {SUBZERO: "Deice Dispatch", RAIN: "Inbound"}
ALTERNATE = {SUBZERO: "Deice Log", RAIN: "Outbound"}


class MobileNodeSplashTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.MobileDrawerBrowserTest.setUpClass()
        cls.addClassCleanup(fixture.MobileDrawerBrowserTest.tearDownClass)
        cls.env = fixture.MobileDrawerBrowserTest()
        cls.env.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = datetime(2026, 11, 23, 12)
        with cls.env.app.app_context():
            gateway = Gateway.query.filter_by(code="RFD").one()
            user = User.query.filter_by(username="first-login").one()
            membership = GatewayMembership.query.filter_by(user_id=user.id, gateway_id=gateway.id).one()
            for node in NeoNode.query.filter(NeoNode.code.in_(("subzero", "rain"))).all():
                grant = GatewayNodeRole.query.filter_by(gateway_membership_id=membership.id, node_id=node.id).one()
                grant.is_active = False
            historical = SortDateOperation(gateway_id=gateway.id, gateway_code=gateway.code,
                                           sort_date=date(2026, 11, 20), sort_name="night", window_minutes=60)
            db.session.add(historical)
            db.session.commit()
            cls.historical_id = historical.id

    def test_signed_in_node_entry_on_phone_tablet_and_desktop(self):
        for engine, width, height in (("webkit", 390, 844), ("webkit", 820, 1180), ("chromium", 1440, 900)):
            with self.subTest(engine=engine, width=width), getattr(self.env.pw, engine).launch() as browser:
                mobile = width < 901
                page = browser.new_page(viewport={"width": width, "height": height},
                                        is_mobile=mobile, has_touch=mobile)
                page.set_default_timeout(7500)
                self.env.login(page)
                page.clock.install()
                self.env.ready(page, "/portal")
                page.locator("a[href='/rfd']:visible").first.click()
                expect(page).to_have_url(self.env.origin + "/rfd")
                for node, links in MENUS.items():
                    with self.subTest(node=node):
                        self.env.ready(page, "/rfd")
                        root = self.env.origin + "/" + node
                        expected = re.compile(re.escape(root) + r"/?$") if mobile else self.env.origin + links[DESKTOP[node]]
                        page.locator(".gateway-node-" + ("subzero" if node == SUBZERO else "rain") + ":visible").click()
                        expect(page).to_have_url(expected)
                        page.wait_for_load_state("load")
                        page.evaluate("document.fonts.ready")
                        page.screenshot(path=str(self.env.evidence / f"{node}-{width}.png"), full_page=mobile)
                        if mobile:
                            menu = page.locator("[data-node-splash-menu]")
                            expect(menu.locator("a span:first-child")).to_have_text(list(links))
                            for label, href in links.items():
                                expect(menu.get_by_role("link", name=label, exact=True)).to_have_attribute("href", href)
                            if node == SUBZERO:
                                expect(menu.get_by_role("link", name="CryoCheck")).to_have_attribute("target", "_blank")
                            self.assertTrue(menu.locator("a").evaluate_all(
                                "tiles => tiles.every(a => a.getBoundingClientRect().height >= 44)"))
                            self.assertFalse(page.evaluate("document.documentElement.scrollWidth > innerWidth"))
                            self.assert_idle_splash(page)
                            menu.get_by_role("link", name=ALTERNATE[node], exact=True).click()
                            expect(page).to_have_url(self.env.origin + links[ALTERNATE[node]])
                            page.go_back(wait_until="domcontentloaded")
                            expect(menu).to_be_visible()
                        else:
                            active = page.locator(".node-desktop-side-menu .is-active .operational-side-link-label")
                            expect(active).to_have_text(DESKTOP[node])
                            expect(page.locator("[data-node-splash-menu]")).to_have_count(0)
                        page.go_back(wait_until="domcontentloaded")
                        expect(page).to_have_url(self.env.origin + "/rfd")

                        route = links[ALTERNATE[node]]
                        self.env.ready(page, route)
                        home = ".operational-mobile-identity" if mobile else ".operational-sidebar-logo"
                        page.locator(home).click()
                        expect(page).to_have_url(expected)
                        for suffix in ("", "/"):
                            page.goto(root + suffix, wait_until="commit")
                            expect(page).to_have_url(expected)

    def assert_idle_splash(self, page):
        requests = []

        def record(request):
            if request.resource_type in ("fetch", "xhr", "websocket"):
                requests.append(request.url)

        page.on("request", record)
        page.clock.fast_forward(120_000)
        page.evaluate("new Promise(resolve => requestAnimationFrame(resolve))")
        page.remove_listener("request", record)
        self.assertEqual(requests, [])

    def test_tile_navigation_preserves_selected_sort_and_dispatch_mode(self):
        with self.env.pw.webkit.launch() as browser:
            page = browser.new_page(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
            self.env.login(page)
            menu = page.locator("[data-node-splash-menu]")
            for node, label in ((SUBZERO, "Deice Log"), (RAIN, "Reports")):
                with self.subTest(node=node):
                    query = f"?operation_id={self.historical_id}"
                    self.env.ready(page, "/" + node + query)
                    menu.get_by_role("link", name=label, exact=True).click()
                    expect(page).to_have_url(self.env.origin + MENUS[node][label] + query)
                    report = page.locator("[data-evidence-root]" if node == SUBZERO else "[data-neorain-reports]")
                    attribute = "data-operation-id" if node == SUBZERO else "data-operation"
                    expect(report).to_have_attribute(attribute, str(self.historical_id))
            with self.env.app.app_context():
                admin = User.query.filter_by(username="drawer-admin").one()
                set_neosubzero_operations_preferences(admin, None, {"mode": "pretreat"})
                db.session.commit()
            self.env.ready(page, "/neosubzero")
            menu.get_by_role("link", name="Deice Dispatch", exact=True).click()
            expect(page.locator('[data-ops-workspace][data-mode="pretreat"]')).to_be_visible()

    def test_readonly_tiles_follow_permissions_and_direct_route_guards(self):
        with self.env.pw.webkit.launch() as browser:
            page = browser.new_page(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
            self.env.login(page, "drawer-watcher")
            for node in (SUBZERO, RAIN):
                self.env.ready(page, "/" + node)
                menu = page.locator("[data-node-splash-menu]")
                menu.get_by_role("link", name="Settings", exact=True).click()
                expect(page).to_have_url(self.env.origin + MENUS[node]["Settings"])
                expect(page.get_by_role("button", name=re.compile(r"^Save", re.I))).to_have_count(0)
            self.env.ready(page, "/neosubzero/operations?mode=pretreat")
            expect(page.locator('[data-ops-workspace][data-mode="pretreat"]')).to_be_visible()

    def test_inactive_node_grants_block_root_and_direct_urls(self):
        with self.env.pw.webkit.launch() as browser:
            page = browser.new_page(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
            self.env.login(page, "first-login")
            for node in (SUBZERO, RAIN):
                for path in ("/" + node, MENUS[node][DESKTOP[node]]):
                    with self.subTest(path=path):
                        response = page.goto(self.env.origin + path)
                        self.assertNotEqual(response.url, self.env.origin + path)
                        expect(page.locator("[data-node-splash-menu]")).to_have_count(0)
