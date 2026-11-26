"""Measure the shared desktop chrome in each actual node shell."""
import json
import os
import unittest
from pathlib import Path

from playwright.sync_api import expect

from tests.browser.test_mobile_drawer import MobileDrawerBrowserTest as fixture


NODES = ("/motherbrain", "/neoermac", "/neosektor/settings", "/neoscorpion/fuel-dispatch",
         "/neosubzero", "/neorain", "/neoraiden", "/neostaffing", "/neostaffing/settings")
TRIGGERS = (
    (".motherbrain-alert-button", ".motherbrain-alert-panel"),
    (".operational-character-switcher > summary", ".character-switcher-panel"),
    (".operational-account-menu > summary", "[role=menu]"),
)


class NeoNodeChromeBrowser(unittest.TestCase):
    def measure_sidebar(self, sidebar):
        return sidebar.evaluate("""el => {
            const image = el.querySelector('.operational-sidebar-logo img,.v11-brand img');
            const style = getComputedStyle(image.parentElement);
            const parent = image.parentElement.getBoundingClientRect();
            const bounds = image.getBoundingClientRect();
            return {
                width: el.getBoundingClientRect().width,
                logo: bounds.width,
                logoHeight: bounds.height,
                naturalRatio: image.naturalWidth / image.naturalHeight,
                innerWidth: parent.width - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight),
                centered: Math.abs(bounds.left + bounds.width / 2 - parent.left - parent.width / 2),
                contentLeft: document.querySelector('.v11-body, main.content').getBoundingClientRect().left,
            };
        }""")

    def test_sidebar_and_topbar(self):
        before = os.environ.get("NEO_CHROME_BASELINE") == "1"
        output = Path(os.environ.get("NEO_CHROME_EVIDENCE", "instance/browser-evidence/issue105/shell"))
        output.mkdir(parents=True, exist_ok=True)
        fixture.setUpClass()
        self.addCleanup(fixture.tearDownClass)
        for engine in ("chromium", "webkit"):
            records = {}
            with getattr(fixture.pw, engine).launch() as browser:
                page = browser.new_page(viewport={"width":1366,"height":768}, service_workers="block")
                page.set_default_timeout(10000)
                fixture().login(page)
                for path in NODES:
                    with self.subTest(engine=engine, path=path):
                        name = path.strip("/").replace("/", "-")
                        page.set_viewport_size({"width":1366,"height":768})
                        fixture().ready(page, path)
                        intro = page.locator("[data-spear-splash-close]")
                        if intro.is_visible():
                            intro.click()
                        sidebar = page.locator("[data-operational-sidebar], .v11-sidebar")
                        toolbar = page.locator("[data-operational-topbar], .v11-top")
                        expect(sidebar).to_be_visible()
                        expect(toolbar).to_be_visible()
                        record = records[name] = {}
                        collapse = sidebar.locator("[data-operational-sidebar-toggle]")
                        standalone = not collapse.count()
                        if not standalone and collapse.get_attribute("aria-expanded") == "false":
                            collapse.click()
                            expect(collapse).to_have_attribute("aria-expanded", "true")
                        for width, height in ((1280,720),(1366,768),(1920,1080)):
                            page.set_viewport_size({"width":width,"height":height})
                            metrics = self.measure_sidebar(sidebar)
                            record[str(width)] = metrics
                            self.assertEqual(metrics["width"], 234 if standalone else 248)
                            self.assertGreater(metrics["naturalRatio"], 0)
                            self.assertAlmostEqual(metrics["logoHeight"] * metrics["naturalRatio"], metrics["logo"], delta=1)
                            self.assertLess(metrics["centered"], 1)
                            if not before:
                                self.assertAlmostEqual(metrics["logo"], metrics["innerWidth"], delta=1)
                        page.set_viewport_size({"width":1366,"height":768})
                        if not standalone:
                            collapse.click()
                            expect(collapse).to_have_attribute("aria-expanded", "false")
                            metrics = record["collapsed"] = self.measure_sidebar(sidebar)
                            self.assertEqual((metrics["width"], metrics["logoHeight"]), (68, 40))
                            self.assertEqual(metrics["logo"], min(40, metrics["innerWidth"]))
                            page.screenshot(path=output / f"{engine}-{name}-collapsed.png")
                            collapse.click()
                            expect(collapse).to_have_attribute("aria-expanded", "true")
                            footer = sidebar.locator(".operational-sidebar-utilities")
                            primary = sidebar.locator(".operational-sidebar-primary")
                            footer_top = footer.bounding_box()["y"]
                            primary.evaluate("node => { node.scrollTop = node.scrollHeight; }")
                            self.assertAlmostEqual(footer.bounding_box()["y"], footer_top, delta=1)
                            primary.evaluate("node => { node.scrollTop = 0; }")
                        for scheme in ("dark", "light"):
                            page.emulate_media(color_scheme=scheme)
                            page.mouse.move(0, 0)
                            page.screenshot(path=output / f"{engine}-{name}-{scheme}.png")
                        for selector, panel_selector in TRIGGERS:
                            trigger = toolbar.locator(selector)
                            if standalone and not trigger.count():
                                continue
                            expect(trigger).to_be_visible()
                            panel = trigger.locator("..").locator(panel_selector)
                            if selector == ".motherbrain-alert-button":
                                expect(trigger.locator(".motherbrain-alert-count")).to_have_text("0")
                            if not before:
                                expect(trigger).to_have_css("border-top-width", "0px")
                                expect(trigger).to_have_css("box-shadow", "none")
                            trigger.hover()
                            if not before:
                                expect(trigger).to_have_css("border-top-width", "0px")
                                expect(trigger).to_have_css("box-shadow", "none")
                                expect(trigger).to_have_css("outline-style", "none")
                            trigger.click()
                            expect(panel).to_be_visible()
                            bounds = panel.bounding_box()
                            self.assertGreaterEqual(bounds["x"], 0)
                            self.assertLessEqual(bounds["x"] + bounds["width"], 1367)
                            if not before:
                                expect(trigger).to_have_css("border-top-width", "0px")
                                expect(trigger).to_have_css("box-shadow", "none")
                            slug = panel_selector.lstrip(".").replace("[role=menu]", "account")
                            page.screenshot(path=output / f"{engine}-{name}-{slug}.png")
                            if "switcher" in selector:
                                page.keyboard.press("Escape")
                                expect(trigger).to_be_focused()
                                expect(panel).to_be_hidden()
                                if not before:
                                    expect(trigger).to_have_css("outline-width", "2px")
                                    expect(trigger).to_have_css("outline-style", "solid")
                                page.screenshot(path=output / f"{engine}-{name}-focus.png")
                            else:
                                trigger.click()
                                expect(panel).to_be_hidden()
                        page.set_viewport_size({"width":390,"height":844})
                        expect(sidebar).to_be_hidden()
                        if not standalone:
                            expect(toolbar).to_be_hidden()
                            image = page.locator("[data-operational-mobile-header] img").first
                            expect(image).to_be_visible()
                            record["mobile"] = image.bounding_box()["width"]
                        else:
                            image = page.locator(".v11-phone-brand img")
                            expect(image).to_be_visible()
                            record["mobile"] = image.bounding_box()["width"]
                (output / f"{engine}-widths.json").write_text(json.dumps(records, indent=2))
