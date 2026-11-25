"""Signed-in checks for shared node chrome and the character switcher."""
from pathlib import Path
import re
import unittest

from playwright.sync_api import expect

from app.extensions import db
from app.models import Gateway, NeoSektorOperationalSetting
from tests.browser import test_mobile_drawer as fixtures


SCREENS = {
    "motherbrain": ("/motherbrain/manage-sort", "207, 106, 110"),
    "ermac": ("/neoermac/settings", "143, 24, 38"),
    "sektor": ("/neosektor/live-counts", "181, 18, 27"),
    "scorpion": ("/neoscorpion/fuel-dispatch", "244, 194, 31"),
    "subzero": ("/neosubzero/pretreat", "77, 183, 255"),
    "rain": ("/neorain/inbound", "127, 77, 255"),
    "raiden": ("/neoraiden", "200, 166, 255"),
    "staffing": ("/neostaffing/attendance", None),
    "staffing-shared": ("/neostaffing/settings", "36, 214, 199"),
}


class NodeShellPaletteBrowserTest(unittest.TestCase):
    def assert_panel_fits(self, switcher):
        panel = switcher.locator(".character-switcher-panel")
        expect(panel).to_be_visible()
        geometry = switcher.evaluate("""el => {
            const trigger = el.querySelector('summary').getBoundingClientRect();
            const panel = el.querySelector('.character-switcher-panel');
            const rect = panel.getBoundingClientRect();
            const bounds = document.documentElement;
            const inside = document.elementFromPoint(rect.left + rect.width / 2, rect.top + 16);
            return {gap: rect.top - trigger.bottom, overlaps: rect.left < trigger.right && rect.right > trigger.left,
                    fits: rect.left >= 11 && rect.right <= bounds.clientWidth - 11 &&
                          rect.bottom <= bounds.clientHeight - 11,
                    horizontalScroll: panel.scrollWidth > panel.clientWidth + 1 ||
                                      bounds.scrollWidth > innerWidth + 1,
                    onTop: panel.contains(inside), radius: getComputedStyle(panel).borderRadius,
                    textFits: [...panel.querySelectorAll('.character-switcher-label')].every(label =>
                        label.firstElementChild.getBoundingClientRect().right <= label.getBoundingClientRect().right + 1)};
        }""")
        self.assertGreaterEqual(geometry["gap"], 5, geometry)
        self.assertLessEqual(geometry["gap"], 10, geometry)
        for property_name in ("overlaps", "fits", "onTop", "textFits"):
            self.assertTrue(geometry[property_name], geometry)
        self.assertFalse(geometry["horizontalScroll"], geometry)
        self.assertEqual(geometry["radius"], "11px", geometry)

    def test_signed_in_node_chrome_and_character_switcher(self):
        fixture = fixtures.MobileDrawerBrowserTest
        fixture.setUpClass()
        self.addCleanup(fixture.tearDownClass)
        fixture.app.config["SECURITY_HEADERS_CSP_ALLOW_NON_PRODUCTION"] = True
        with fixture.app.app_context():
            gateway = Gateway.query.one()
            db.session.add(NeoSektorOperationalSetting(gateway_id=gateway.id, gateway_code=gateway.code,
                                                       integration_mode="neo_only"))
            db.session.commit()
        evidence = Path("instance/browser-evidence/issue81/after")
        evidence.mkdir(parents=True, exist_ok=True)
        for engine in ("chromium", "webkit"):
            with self.subTest(engine=engine), getattr(fixture.pw, engine).launch() as browser:
                page = browser.new_page(viewport={"width":1440, "height":900}, service_workers="block")
                page.add_init_script("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1', 'seen')")
                page.set_default_timeout(6000)
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                fixture().login(page)
                triggers, accents = [], set()
                for node, (path, rgb) in SCREENS.items():
                    with self.subTest(node=node):
                        fixture().ready(page, path)
                        staffing = node == "staffing"
                        header = page.locator(".v11-top" if staffing else ".operational-node-topbar")
                        sidebar = page.locator(".v11-sidebar" if staffing else ".operational-desktop-sidebar")
                        switcher = header.locator("[data-character-switcher]")
                        trigger = switcher.locator("summary")
                        page.mouse.move(450, 2)
                        active = sidebar.locator("nav a[aria-current='page']" if staffing else "nav a.is-active").first
                        expect(active).to_be_visible()
                        top_image = header.evaluate("el => getComputedStyle(el).backgroundImage")
                        rail_image = sidebar.evaluate("el => getComputedStyle(el).backgroundImage")
                        active_style = active.evaluate("""el => {
                            const s = getComputedStyle(el); return [s.backgroundColor, s.boxShadow, s.color];
                        }""")
                        if staffing:
                            self.assertEqual(active_style[2], "rgb(107, 222, 207)")
                            expect(header).to_have_css("background-color", "rgb(13, 26, 34)")
                            expect(sidebar).to_have_css("background-color", "rgb(13, 26, 34)")
                        else:
                            for chrome in (top_image, rail_image):
                                self.assertIn(rgb, chrome)
                            if node == "staffing-shared":
                                self.assertEqual(active_style[0], "rgb(23, 51, 48)")
                            else:
                                self.assertEqual(active_style[0], f"rgba({rgb}, 0.22)")
                            self.assertNotEqual(active_style[1], "none")
                        self.assertNotIn(active_style[0], accents)
                        accents.add(active_style[0])
                        triggers.append(trigger.evaluate("""el => {
                            const s = getComputedStyle(el), caret = getComputedStyle(el, '::after');
                            return [s.fontFamily,s.fontSize,s.fontWeight,s.padding,s.backgroundColor,s.borderRadius,
                                    s.height,caret.borderWidth,caret.transform];
                        }"""))
                        for width in (1280,1440,1920):
                            page.set_viewport_size({"width":width, "height":900 if width < 1920 else 1080})
                            if not staffing:
                                self.assertEqual(header.bounding_box()["height"], 84)
                                self.assertAlmostEqual(sidebar.bounding_box()["y"], 84, delta=1)
                            if width != 1280:
                                page.mouse.move(450, 2)
                                page.screenshot(path=evidence / f"{engine}-{node}-{width}.png")
                            trigger.click()
                            self.assert_panel_fits(switcher)
                            marker = switcher.locator("[aria-current='true']")
                            expect(marker).to_have_class(f"character-switcher-link node-{node.split('-')[0]} is-active")
                            expect(marker).to_contain_text("Current")
                            self.assertEqual(marker.locator("a[href]").count(), 0)
                            self.assertTrue(switcher.locator(".character-switcher-icon").evaluate_all(
                                "async imgs => (await Promise.all(imgs.map(img => img.decode().then(() => img.naturalWidth > 0)))).every(Boolean)"))
                            if node in ("scorpion", "raiden") and width != 1280:
                                page.screenshot(path=evidence / f"{engine}-{node}-{width}-open.png")
                            trigger.click()
                self.assertTrue(all(style == triggers[0] for style in triggers), triggers)
                self.check_interaction(page, fixture)
                self.check_mobile(page, fixture, evidence, engine)
                self.assertEqual(errors, [])

    def check_interaction(self, page, fixture):
        fixture().ready(page, "/neoscorpion/fuel-dispatch")
        switcher = page.locator(".operational-node-topbar [data-character-switcher]")
        trigger = switcher.locator("summary")
        panel = switcher.locator(".character-switcher-panel")
        links = panel.get_by_role("link")
        page.set_viewport_size({"width":1440, "height":900})
        fueling_board = page.locator(".operational-desktop-sidebar").get_by_role("link", name="Fueling Board")
        fueling_board.hover()
        expect(fueling_board).to_have_css("background-color", "rgba(244, 194, 31, 0.15)")
        trigger.focus()
        page.keyboard.press("Enter")
        expect(panel).to_be_visible()
        page.keyboard.press("ArrowDown")
        expect(links.first).to_be_focused()
        expect(links.first).to_have_css("outline-style", "solid")
        page.keyboard.press("End")
        expect(links.last).to_be_focused()
        page.keyboard.press("ArrowDown")
        expect(links.first).to_be_focused()
        page.keyboard.press("Escape")
        expect(panel).to_be_hidden()
        expect(trigger).to_be_focused()
        trigger.click()
        trigger.evaluate("el => el.blur()")
        page.keyboard.press("Escape")
        expect(panel).to_be_hidden()
        expect(trigger).to_be_focused()
        trigger.click()
        page.set_viewport_size({"width":1280,"height":300})
        self.assert_panel_fits(switcher)
        self.assertTrue(panel.evaluate("el => el.scrollHeight > el.clientHeight"))
        account = page.get_by_label("Account menu", exact=True).filter(visible=True)
        account.click()
        expect(panel).to_be_hidden()
        expect(page.locator(".operational-account-menu[open]")).to_be_visible()
        trigger.click()
        expect(panel).to_be_visible()
        expect(page.locator(".operational-account-menu[open]")).to_have_count(0)
        page.mouse.click(600, 72)
        expect(panel).to_be_hidden()
        page.set_viewport_size({"width":1440,"height":900})
        page.locator(".operational-sidebar-collapse").click()
        expect(page.locator("body")).to_have_class(re.compile("operational-sidebar-collapsed"))
        page.locator(".operational-sidebar-utilities [data-operational-board-toggle]").click()
        expect(switcher).to_be_hidden()
        page.get_by_role("button", name="Exit Board View").click()
        expect(switcher).to_be_visible()
        fixture().ready(page, "/neostaffing/attendance")
        v11_switcher = page.locator(".v11-top [data-character-switcher]")
        v11_switcher.locator("summary").click()
        page.locator("[data-scope-picker] > summary").click()
        expect(v11_switcher.locator(".character-switcher-panel")).to_be_hidden()
        expect(page.locator("[data-scope-picker][open]")).to_be_visible()
        for path, header in (("/portal",".portal-topbar"), ("/rfd",".gateway-shell-topbar")):
            fixture().ready(page, path)
            gateway_switcher = page.locator(f"{header} [data-character-switcher]")
            gateway_switcher.locator("summary").click()
            self.assert_panel_fits(gateway_switcher)
            expect(gateway_switcher.locator("[aria-current='true']")).to_have_count(0)
        restricted = page.context.browser.new_page(viewport={"width":1280,"height":900}, service_workers="block")
        try:
            fixture().login(restricted, username="drawer-watcher")
            fixture().ready(restricted, "/neoermac")
            watcher_switcher = restricted.locator(".operational-node-topbar [data-character-switcher]")
            watcher_switcher.locator("summary").click()
            expect(watcher_switcher.locator('a[href^="/motherbrain"], a[href="/neoermac"]')).to_have_count(0)
            watcher_switcher.locator('a[href="/neoraiden"]').click()
            expect(restricted).to_have_url(fixture.origin + "/neoraiden")
        finally:
            restricted.close()

    def check_mobile(self, page, fixture, evidence, engine):
        for width in (390,430):
            page.set_viewport_size({"width":width, "height":844})
            for node in ("scorpion","raiden","staffing"):
                path, rgb = SCREENS[node]
                fixture().ready(page, path)
                expect(page.locator(".operational-character-switcher")).to_be_hidden()
                if node == "staffing":
                    expect(page.locator(".v11-dock")).to_be_visible()
                    continue
                header = page.locator(".operational-mobile-header")
                expect(header).to_have_css("border-bottom-color", f"rgba({rgb}, 0.42)")
                page.locator("[data-drawer-nodes]").click()
                drawer = page.locator("[data-mobile-drawer]")
                expect(drawer).to_be_visible()
                self.assertIn(rgb, drawer.evaluate("el => getComputedStyle(el).backgroundImage"))
                expect(drawer.locator('.neo-drawer-node-link.node-ermac')).to_be_visible()
                self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), width)
                page.screenshot(path=evidence / f"{engine}-{node}-{width}-drawer.png")
                page.locator("[data-drawer-close]").click()
                expect(drawer).to_be_hidden()
                if node == "scorpion":
                    page.locator("[data-drawer-toggle]").click()
                    active = drawer.locator("[aria-current='page']")
                    expect(active).to_have_css("background-color", f"rgba({rgb}, 0.22)")
                    page.locator("[data-drawer-close]").click()
