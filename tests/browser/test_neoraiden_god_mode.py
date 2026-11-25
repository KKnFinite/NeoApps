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
                        expect(page.get_by_role("table",name="Missions")).to_have_count(1)
                        expect(page.locator("[data-mission]:visible")).to_have_count(59)
                        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"),width+1)
                        page.screenshot(path=output/f"phone-{engine}-{width}.png",full_page=True)
                        if width==390:
                            page.screenshot(path=output/f"phone-{engine}-390-viewport.png")
                    page.get_by_role("searchbox").fill("FX101")
                    expect(page.locator("[data-mission]:visible")).to_have_count(1)
                    item = page.locator("[data-mission]:visible")
                    expect(item.locator("td,th")).to_have_count(7)
                    expect(item.locator("details,summary")).to_have_count(0)
                    page.locator("[data-theme-toggle]").click()
                    expect(page.locator("[data-raiden]")).to_have_attribute("data-theme","light")
                    with ShellFixture.app.app_context():
                        flight = SortDateMission.query.filter_by(sort_date_operation_id=op_id, flight_number="FX101").one()
                        tail = "N" + engine.upper()
                        flight.assigned_tail_number = tail
                        bays = NeoSektorOpenBayState.query.filter_by(side="EAST").one()
                        bays.open_count = 4 if engine == "chromium" else 3
                        east = bays.open_count
                        StaffingDailyAttendance.query.filter_by(sort_date_operation_id=op_id, status="call_in").one().status = "here"
                        db.session.commit()
                    with page.expect_response(lambda response: "/neoraiden/state" in response.url and response.status == 200):
                        page.locator("[data-refresh]").click()
                    expect(page.get_by_role("searchbox")).to_have_value("FX101")
                    expect(item).to_contain_text(tail)
                    expect(page.locator(".rd-uld-foot")).to_contain_text(f"E {east} · W 3")
                    expect(page.locator(".rd-staff summary")).to_contain_text("2 HERE")
                    page.get_by_role("searchbox").fill("")
                    page.reload()
                    expect(page.locator("[data-raiden]")).to_have_attribute("data-theme","light")
                    page.screenshot(path=output/f"phone-{engine}-light.png")
                    for width, height in ((1366,768),(1920,1080)):
                        page.set_viewport_size(dict(width=width,height=height))
                        rows = page.locator("[data-mission]")
                        expect(rows).to_have_count(59)
                        heights = rows.evaluate_all("rows => rows.map(row => row.getBoundingClientRect().height)")
                        self.assertLess(max(heights), 26)
                        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), width + 1)
                        expect(page.locator(".rd-staff summary")).to_be_visible()
                        check_uld_first()
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
                        self.assertTrue(native)
                        expect(page.locator(".operational-desktop-sidebar")).to_be_hidden()
                        expect(page.locator("[data-page], [data-next], [data-prev], [data-pause]")).to_have_count(0)
                        page.wait_for_function("document.querySelector('.rd-tv')?.clientHeight===innerHeight")
                        page.evaluate("() => new Promise(ok=>requestAnimationFrame(()=>requestAnimationFrame(ok)))")
                        check_uld_first()
                        expect(page.locator(".rd-tv-here b")).to_have_text("2")
                        state = page.evaluate("""() => {
                            const rows=[...document.querySelectorAll('.rd-mission')];
                            const fields=[...document.querySelectorAll('.rd-mission :is(th,td)')];
                            const visible=el=>{
                                const r=el.getBoundingClientRect();
                                return r.width>0 && r.height>0 && r.top>=0 && r.left>=0
                                    && r.bottom<=innerHeight+1 && r.right<=innerWidth+1;
                            };
                            return {arrivals:rows.filter(el=>el.cells[0].textContent==='ARR').length,
                                departures:rows.filter(el=>el.cells[0].textContent==='DEP').length,
                                visible:rows.filter(visible).length,
                                fieldsFit:fields.every(el=>visible(el) && el.scrollWidth<=el.clientWidth+1),
                                oneColumn:rows.every((row,i)=>!i || row.offsetTop>rows[i-1].offsetTop),
                                scrollboxes:[...document.querySelectorAll('.rd-tv *')].filter(el=>
                                    /auto|scroll/.test(getComputedStyle(el).overflowY) && el.scrollHeight>el.clientHeight+1).length,
                                w:document.documentElement.scrollWidth,h:document.documentElement.scrollHeight,
                                font:parseFloat(getComputedStyle(rows[0].cells[1]).fontSize)};
                        }""")
                        self.assertEqual((state["arrivals"], state["departures"], state["visible"]), (29, 30, 59), state)
                        self.assertTrue(state["fieldsFit"], state)
                        self.assertLessEqual(state["w"],width+1)
                        self.assertLessEqual(state["h"],height+1)
                        self.assertGreaterEqual(state["font"],10)
                        self.assertTrue(state["oneColumn"])
                        self.assertEqual(state["scrollboxes"],0)
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
                        m.assigned_tail_number = 'NBOARD'
                        NeoSektorOpenBayState.query.filter_by(side="EAST").one().open_count = 0
                        StaffingDailyAttendance.query.filter_by(sort_date_operation_id=op_id).order_by(StaffingDailyAttendance.id.desc()).first().status = "call_in"
                        db.session.commit()
                    with page.expect_response(lambda r:'/neoraiden/state' in r.url and r.status==200):
                        page.locator("[data-refresh]").click()
                    expect(page.locator("[data-mission]:visible")).to_have_count(59)
                    self.assertEqual(page.evaluate("document.fullscreenElement === document.documentElement"), native)
                    expect(page.locator(".rd-missions")).to_contain_text("NBOARD")
                    expect(page.locator(".rd-uld-foot")).to_contain_text("E 0 · W 3")
                    expect(page.locator(".rd-tv-here b")).to_have_text("1")
                    page.get_by_role("button",name="Exit TV Mode").click()
                    page.wait_for_function("document.fullscreenElement === null")
                    expect(page.locator(".operational-board-exit")).to_be_visible()
                    page.locator(".operational-board-exit").click()
                    expect(page.locator(".operational-sidebar-utilities")).to_be_visible()
                    page.get_by_role("button",name="TV Mode",exact=True).click()
                    expect(page.locator(".rd-tv")).to_be_visible()
                    page.evaluate("document.exitFullscreen()")
                    expect(page.locator(".rd-tv")).to_have_count(0)
                    expect(page.locator(".operational-sidebar-utilities")).to_be_visible()
                    page.set_viewport_size(dict(width=640,height=400))
                    page.get_by_role("button",name="TV Mode",exact=True).click()
                    expect(page.locator("[data-tv-notice]")).to_contain_text("cannot fit every flight legibly")
                    expect(page.locator(".rd-tv")).to_have_count(0)
                    expect(page.locator("[data-mission]")).to_have_count(59)
                    page.evaluate("() => { document.documentElement.requestFullscreen = async () => { throw new Error(); }; }")
                    page.get_by_role("button",name="TV Mode",exact=True).click()
                    expect(page.locator("[data-tv-notice]")).to_contain_text("requires browser fullscreen")
                    expect(page.locator(".rd-tv")).to_have_count(0)
                    (output/f"geometry-{engine}.json").write_text(json.dumps(stats,indent=2))
                finally:
                    browser.close()
