import json
import unittest
from pathlib import Path
from playwright.sync_api import expect

from app.extensions import db
from app.models import Gateway, NeoSektorOpenBayState, SortDateMission, SortDateParkingAssignment, StaffingDailyAttendance
from tests.browser import test_mobile_drawer as shell_tests
from tests.neoraiden_fixtures import busy_sort, record_attendance


class GodModeBrowser(unittest.TestCase):
    def test_full_sort_workspaces(self):
        ShellFixture = shell_tests.MobileDrawerBrowserTest
        ShellFixture.setUpClass()
        self.addCleanup(ShellFixture.tearDownClass)
        with ShellFixture.app.app_context():
            operation = busy_sort(Gateway.query.one(), extra_arrivals=29, extra_departures=28)
            record_attendance(operation)
            for tail, position, lane in (("N401UP", "D05", 1), ("N402UP", "D06", 2)):
                db.session.add(SortDateParkingAssignment(sort_date_operation_id=operation.id,
                    tail_number=tail, ramp_code="D", position_code=position, lane_number=lane))
            db.session.commit()
            op_id = operation.id
        output = Path("instance/browser-evidence/issue66")
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
                    expect(page.locator("[data-mission]")).to_have_count(59)
                    stats = []
                    expect(page.locator(".rd-staff summary")).to_contain_text("1 HERE")
                    expect(page.locator(".rd-tv-here")).to_be_hidden()
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
                            self.assertEqual(len(page.locator("[data-mission]:visible").all()),29 if side == "arrivals" else 30)
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
                        bays = NeoSektorOpenBayState.query.filter_by(side="EAST").one()
                        bays.open_count = 4 if engine == "chromium" else 3
                        east = bays.open_count
                        StaffingDailyAttendance.query.filter_by(sort_date_operation_id=op_id, status="call_in").one().status = "here"
                        db.session.commit()
                    with page.expect_response(lambda response: "/neoraiden/state" in response.url and response.status == 200):
                        page.locator("[data-refresh]").click()
                    expect(page.get_by_role("searchbox")).to_have_value("FX101")
                    expect(item.locator(".rd-detail")).to_be_visible()
                    expect(item.locator(".rd-detail")).to_contain_text(str(fuel))
                    expect(page.locator(".rd-uld-foot")).to_contain_text(f"E {east} · W 3")
                    expect(page.locator(".rd-staff summary")).to_contain_text("2 HERE")
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
                                content:scroll.scrollHeight,
                                gutter:document.querySelector('[data-raiden]').getBoundingClientRect().left -
                                    document.querySelector('.operational-desktop-sidebar').getBoundingClientRect().right,
                                doc:document.documentElement.scrollWidth,
                                sticky:getComputedStyle(a.querySelector('.rd-columns')).position};
                        }""")
                        stats.append([width,"desktop",desktop])
                        check_uld_first()
                        self.assertGreaterEqual(desktop["gutter"], 0)
                        self.assertLessEqual(desktop["gutter"], 12)
                        self.assertEqual(desktop["top"],desktop["secondTop"])
                        expect(page.locator(".rd-staff summary")).to_be_visible()
                        expect(page.locator(".rd-tv-here")).to_be_hidden()
                        self.assertGreater(desktop["second"],desktop["left"]+desktop["panel"]-1)
                        self.assertLess(desktop["top"],height*.62)
                        self.assertGreater(desktop["content"],desktop["scroll"])
                        self.assertEqual(desktop["sticky"],"sticky")
                        self.assertLessEqual(desktop["doc"],width+1)
                        page.screenshot(path=output/f"desktop-{engine}-{width}-light.png",full_page=True)
                        page.locator("[data-theme-toggle]").click()
                        page.screenshot(path=output/f"desktop-{engine}-{width}-dark.png",full_page=True)
                        page.locator("[data-theme-toggle]").click()
                    page.locator(".operational-sidebar-collapse").click()
                    gutter = page.evaluate("""() => document.querySelector('[data-raiden]').getBoundingClientRect().left -
                        document.querySelector('.operational-desktop-sidebar').getBoundingClientRect().right""")
                    self.assertGreaterEqual(gutter, 0)
                    self.assertLessEqual(gutter, 12)
                    page.locator(".operational-sidebar-collapse").click()
                    expect(page.locator("[data-board]")).to_contain_text("D05")
                    expect(page.locator("[data-board]")).not_to_contain_text("Lane 1")
                    expect(page.locator("[data-board]")).to_contain_text("D06 \u00b7 Lane 2")
                    page.locator(".operational-sidebar-utilities [data-operational-board-toggle]").click()
                    for width, height in ((1280,720),(1366,768),(1920,1080),(3840,2160)):
                        page.set_viewport_size(dict(width=width,height=height))
                        page.get_by_role("button",name="TV Mode",exact=True).click()
                        expect(page.get_by_role("button",name="Exit TV Mode")).to_be_visible()
                        native = page.evaluate("document.fullscreenElement === document.documentElement")
                        if engine == "chromium":
                            self.assertTrue(native)
                        if not native:
                            expect(page.locator("[data-tv-notice]")).to_contain_text("Browser fullscreen is unavailable")
                        expect(page.locator(".operational-desktop-sidebar")).to_be_hidden()
                        expect(page.locator("[data-page], [data-next], [data-prev], [data-pause]")).to_have_count(0)
                        page.wait_for_function("document.querySelector('.rd-tv')?.clientHeight===innerHeight")
                        page.evaluate("() => new Promise(ok=>requestAnimationFrame(()=>requestAnimationFrame(ok)))")
                        check_uld_first()
                        expect(page.locator(".rd-tv-here b")).to_have_text("2")
                        state = page.evaluate("""() => {
                            const rows=[...document.querySelectorAll('.rd-mission')];
                            const fields=[...document.querySelectorAll('.rd-mission summary :is(strong,small,.rd-status)')];
                            const visible=el=>{
                                const r=el.getBoundingClientRect();
                                return r.width>0 && r.height>0 && r.top>=0 && r.left>=0
                                    && r.bottom<=innerHeight+1 && r.right<=innerWidth+1;
                            };
                            return {arrivals:rows.filter(el=>el.closest('[data-panel=arrivals]')).length,
                                departures:rows.filter(el=>el.closest('[data-panel=departures]')).length,
                                visible:rows.filter(visible).length,
                                fieldsFit:fields.every(visible),
                                w:document.documentElement.scrollWidth,h:document.documentElement.scrollHeight,
                                font:parseFloat(getComputedStyle(fields[0]).fontSize)};
                        }""")
                        self.assertEqual((state["arrivals"], state["departures"], state["visible"]), (29, 30, 59), state)
                        self.assertTrue(state["fieldsFit"], state)
                        self.assertLessEqual(state["w"],width+1)
                        self.assertLessEqual(state["h"],height+1)
                        self.assertGreaterEqual(state["font"],14)
                        stats.append([width,"tv",height,state])
                        for theme in ("dark","light"):
                            if page.locator("[data-raiden]").get_attribute("data-theme")!=theme:
                                page.locator("[data-theme-toggle]").click()
                            page.screenshot(path=output/f"tv-{engine}-{height}-{theme}.png")
                        if width != 3840:
                            page.get_by_role("button",name="Exit TV Mode").click()
                            page.wait_for_function("document.fullscreenElement === null")
                    with ShellFixture.app.app_context():
                        m=SortDateMission.query.filter_by(sort_date_operation_id=op_id,flight_number="FX101").one()
                        m.planned_fuel_load = 30001 if engine=="chromium" else 30002
                        NeoSektorOpenBayState.query.filter_by(side="EAST").one().open_count = 0
                        StaffingDailyAttendance.query.filter_by(sort_date_operation_id=op_id).order_by(StaffingDailyAttendance.id.desc()).first().status = "call_in"
                        db.session.commit()
                    with page.expect_response(lambda r:'/neoraiden/state' in r.url and r.status==200):
                        page.locator("[data-refresh]").click()
                    expect(page.locator("[data-mission]:visible")).to_have_count(59)
                    self.assertEqual(page.evaluate("document.fullscreenElement === document.documentElement"), native)
                    expect(page.locator(".rd-uld-foot")).to_contain_text("E 0 · W 3")
                    expect(page.locator(".rd-tv-here b")).to_have_text("1")
                    page.get_by_role("button",name="Exit TV Mode").click()
                    page.wait_for_function("document.fullscreenElement === null")
                    expect(page.locator(".operational-board-exit")).to_be_visible()
                    page.locator(".operational-board-exit").click()
                    expect(page.locator(".operational-sidebar-utilities")).to_be_visible()
                    page.get_by_role("button",name="TV Mode",exact=True).click()
                    expect(page.locator(".rd-tv")).to_be_visible()
                    if native:
                        # In headless browsers, the platform Escape key is unavailable.
                        # Exercise the same native fullscreenchange event.
                        page.evaluate("document.exitFullscreen()")
                    else:
                        page.keyboard.press("Escape")
                    expect(page.locator(".rd-tv")).to_have_count(0)
                    expect(page.locator(".operational-sidebar-utilities")).to_be_visible()
                    page.set_viewport_size(dict(width=640,height=400))
                    page.get_by_role("button",name="TV Mode",exact=True).click()
                    expect(page.locator("[data-tv-notice]")).to_contain_text("cannot fit every flight legibly")
                    expect(page.locator(".rd-tv")).to_have_count(0)
                    expect(page.locator("[data-mission]")).to_have_count(59)
                    (output/f"geometry-{engine}.json").write_text(json.dumps(stats,indent=2))
                finally:
                    browser.close()
