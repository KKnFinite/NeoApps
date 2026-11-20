import json
import unittest
from pathlib import Path
from playwright.sync_api import expect

from app.extensions import db
from app.models import Gateway, SortDateMission
from tests.browser import test_mobile_drawer as shell_tests
from tests.neoraiden_fixtures import busy_sort


class GodModeBrowser(unittest.TestCase):
    def test_full_sort_workspaces(self):
        ShellFixture = shell_tests.MobileDrawerBrowserTest
        ShellFixture.setUpClass()
        self.addCleanup(ShellFixture.tearDownClass)
        with ShellFixture.app.app_context():
            op_id = busy_sort(Gateway.query.one()).id
        output = Path("instance/browser-evidence/god-mode")
        output.mkdir(parents=True, exist_ok=True)
        for engine in ("chromium", "webkit"):
            with self.subTest(engine=engine):
                browser = getattr(ShellFixture.pw, engine).launch()
                try:
                    page = browser.new_page(viewport={"width":390,"height":844})
                    ShellFixture().login(page)
                    path = "/neoraiden?gateway=RFD&operation_id=" + str(op_id)
                    ShellFixture().ready(page,path)
                    self.assertEqual(page.title(),"NeoRaiden — God Mode")
                    expect(page.locator("[data-mission]")).to_have_count(52)
                    stats = []
                    def check_uld_first():
                        bounds = page.evaluate("""() => {
                            const rect = sel => document.querySelector(sel).getBoundingClientRect();
                            return [rect('.rd-header').bottom,rect('.rd-uld').top,rect('.rd-uld').bottom,
                                    rect('.rd-context').top,rect('.rd-workspace').top];
                        }""")
                        self.assertLessEqual(bounds[0],bounds[1]+1)
                        self.assertLessEqual(bounds[2],bounds[3]+1)
                        self.assertLessEqual(bounds[2],bounds[4]+1)
                    for width in (320,375,390,430):
                        page.set_viewport_size(dict(width=width,height=844))
                        check_uld_first()
                        for side in ("arrivals","departures"):
                            page.locator(f'[data-list-choice="{side}"]').click()
                            expect(page.locator("[data-panel]:visible")).to_have_count(1)
                            geometry = page.evaluate("""() => {
                                const bounds = [...document.querySelectorAll('details[data-mission]')]
                                    .map(el => el.getBoundingClientRect()).filter(r=>r.width);
                                const dock=document.querySelector('.operational-mobile-bottom-nav');
                                const bottom = dock.getBoundingClientRect().top;
                                return {heights:[...new Set(bounds.map(r=>Math.round(r.height)))],
                                    fullyVisible:bounds.filter(r=>r.top>=0 && r.bottom<=bottom).length,
                                    first:Math.round(bounds[0].top), width:document.documentElement.scrollWidth};
                            }""")
                            stats.append([width,side,geometry])
                            self.assertLessEqual(geometry["width"],width+1)
                            self.assertEqual(len(page.locator("[data-mission]:visible").all()),26)
                            self.assertTrue(all(48 <= h <= 68 for h in geometry["heights"]),geometry)
                            self.assertGreaterEqual(geometry["fullyVisible"],7 if width==390 else 6,geometry)
                        page.screenshot(path=output/f"phone-{engine}-{width}.png",full_page=True)
                        if width==390:
                            page.screenshot(path=output/f"phone-{engine}-390-viewport.png")
                    page.get_by_role("searchbox").fill("FX101")
                    expect(page.locator("[data-mission]:visible")).to_have_count(1)
                    item = page.locator("[data-mission]:visible")
                    item.locator("summary").focus()
                    item.locator("summary").press("Enter")
                    expect(item.locator(".rd-detail")).to_contain_text("D13")
                    expect(item.locator(".rd-detail")).to_contain_text("Configured",ignore_case=True)
                    page.locator("[data-theme-toggle]").click()
                    expect(page.locator("[data-raiden]")).to_have_attribute("data-theme","light")
                    expect(item.locator(".rd-detail")).to_be_visible()
                    with ShellFixture.app.app_context():
                        flight = SortDateMission.query.filter_by(sort_date_operation_id=op_id, flight_number="FX101").one()
                        fuel = 27777 if engine == "chromium" else 27778
                        flight.planned_fuel_load = fuel
                        db.session.commit()
                    with page.expect_response(lambda response: "/neoraiden/state" in response.url and response.status == 200):
                        page.locator("[data-refresh]").click()
                    expect(page.get_by_role("searchbox")).to_have_value("FX101")
                    expect(item.locator(".rd-detail")).to_be_visible()
                    expect(item.locator(".rd-detail")).to_contain_text(str(fuel))
                    page.get_by_role("searchbox").fill("")
                    page.reload()
                    expect(page.locator("[data-raiden]")).to_have_attribute("data-list","departures")
                    expect(page.locator("[data-raiden]")).to_have_attribute("data-theme","light")
                    page.screenshot(path=output/f"phone-{engine}-light.png")
                    for width, height in ((1366,768),(1920,1080)):
                        page.set_viewport_size(dict(width=width,height=height))
                        desktop=page.evaluate("""() => {
                            const [a,d]=document.querySelectorAll('.rd-panel');
                            const box=a.getBoundingClientRect(), next=d.getBoundingClientRect();
                            const scroll=a.querySelector('.rd-scroll');
                            return {left:Math.round(box.left),top:Math.round(box.top),panel:Math.round(box.width),
                                second:Math.round(next.left),secondTop:Math.round(next.top),scroll:scroll.clientHeight,
                                content:scroll.scrollHeight,doc:document.documentElement.scrollWidth,
                                sticky:getComputedStyle(a.querySelector('.rd-columns')).position};
                        }""")
                        stats.append([width,"desktop",desktop])
                        check_uld_first()
                        self.assertEqual(desktop["top"],desktop["secondTop"])
                        self.assertGreater(desktop["second"],desktop["left"]+desktop["panel"]-1)
                        self.assertLess(desktop["top"],height*.62)
                        self.assertGreater(desktop["content"],desktop["scroll"])
                        self.assertEqual(desktop["sticky"],"sticky")
                        self.assertLessEqual(desktop["doc"],width+1)
                        page.screenshot(path=output/f"desktop-{engine}-{width}-light.png",full_page=True)
                        page.locator("[data-theme-toggle]").click()
                        page.screenshot(path=output/f"desktop-{engine}-{width}-dark.png",full_page=True)
                        page.locator("[data-theme-toggle]").click()
                    page.locator(".operational-sidebar-utilities [data-operational-board-toggle]").click()
                    page.get_by_role("button",name="TV Mode",exact=True).click()
                    expect(page.get_by_role("button",name="Exit TV Mode")).to_be_visible()
                    expect(page.locator("[data-page]")).to_contain_text("Page 2",timeout=13000)
                    page.locator("[data-pause]").click()
                    for width,height in ((1280,720),(1366,768),(1920,1080),(3840,2160)):
                        page.set_viewport_size(dict(width=width,height=height))
                        page.wait_for_function("document.querySelector('.rd-tv').clientHeight===innerHeight")
                        page.evaluate("() => new Promise(ok=>requestAnimationFrame(()=>requestAnimationFrame(ok)))")
                        check_uld_first()
                        num_pages=int(page.locator("[data-page]").inner_text().split("/")[-1])
                        seen=set()
                        for _ in range(num_pages):
                            state=page.evaluate("""() => {
                                const bottom=document.querySelector('.rd-pager').getBoundingClientRect();
                                const cards=[...document.querySelectorAll('.rd-mission:not([hidden])')];
                                return {ids:cards.map(el=>el.dataset.mission),
                                    clipped:cards.some(el=>el.getBoundingClientRect().bottom >
                                        el.closest('.rd-scroll').getBoundingClientRect().bottom+1),
                                    footer:Math.ceil(bottom.bottom),w:document.documentElement.scrollWidth,
                                    h:document.documentElement.scrollHeight,
                                    font:parseFloat(getComputedStyle(cards[0].querySelector('strong')).fontSize)};
                            }""")
                            self.assertFalse(state["clipped"],state)
                            self.assertLessEqual(state["footer"],height+1)
                            self.assertLessEqual(state["w"],width+1)
                            self.assertLessEqual(state["h"],height+1)
                            self.assertGreaterEqual(state["font"],15)
                            seen.update(state["ids"])
                            if num_pages>1: page.locator("[data-next]").click()
                        self.assertEqual(len(seen),52)
                        stats.append([width,"tv",height,num_pages,state["font"],state["footer"]])
                        for theme in ("dark","light"):
                            if page.locator("[data-raiden]").get_attribute("data-theme")!=theme:
                                page.locator("[data-theme-toggle]").click()
                            page.screenshot(path=output/f"tv-{engine}-{height}-{theme}.png")
                    page.set_viewport_size(dict(width=1366,height=768))
                    page.locator("[data-next]").click()
                    current=page.locator("[data-page]").inner_text()
                    with ShellFixture.app.app_context():
                        m=SortDateMission.query.filter_by(sort_date_operation_id=op_id,flight_number="FX101").one()
                        m.planned_fuel_load = 30001 if engine=="chromium" else 30002
                        db.session.commit()
                    with page.expect_response(lambda r:'/neoraiden/state' in r.url and r.status==200):
                        page.locator("[data-refresh]").click()
                    expect(page.locator("[data-page]")).to_have_text(current)
                    expect(page.locator("[data-pause]")).to_have_text("Resume")
                    page.locator("[data-prev]").click()
                    page.get_by_role("button",name="Exit TV Mode").click()
                    expect(page.locator(".operational-board-exit")).to_be_visible()
                    page.locator(".operational-board-exit").click()
                    expect(page.locator(".operational-sidebar-utilities")).to_be_visible()
                    (output/f"geometry-{engine}.json").write_text(json.dumps(stats,indent=2))
                finally:
                    browser.close()
