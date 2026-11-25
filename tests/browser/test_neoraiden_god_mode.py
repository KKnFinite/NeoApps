import json
import unittest
from pathlib import Path

from playwright.sync_api import expect

from app.extensions import db
from app.models import Gateway, SortDateMission, SortDateParkingAssignment
from tests.browser import test_mobile_drawer as shell_tests
from tests.neoraiden_fixtures import busy_sort, record_attendance, record_departure_milestones


RESOLUTIONS = ((1280, 720), (1366, 768), (1920, 1080), (3840, 2160))
RAIDEN_BLUE = "rgb(159, 201, 249)"
HEADINGS = (
    ["Wave", "Flight", "Tail", "Origin", "POS", "Time", "+/−", "Status"],
    ["Flight", "Tail", "Dest", "POS", "Time", "Status", "Neo Fuel",
     "Pull", "R-LC", "C-LC", "Block", "+/−", "No Return"],
)


class GodModeBrowser(unittest.TestCase):
    def set_theme(self, page, theme):
        board = page.locator("[data-raiden]")
        if board.get_attribute("data-theme") != theme:
            board.locator("[data-theme-toggle]").click()
        expect(board.locator(".rd-wordmark")).to_have_css("color", RAIDEN_BLUE)
        expect(board.locator("[data-refresh], [data-countdown], [data-pause], [data-interval], .rd-settings")).to_have_count(0)
        expect(board.locator(".rd-uld-foot")).not_to_contain_text("Plan")
        expect(board.locator("#rd-departures [data-mission] [data-field='time'] small")).to_have_count(0)

    def check_overview(self, page):
        overview = page.locator(".rd-overview")
        shift = overview.locator('[data-metric="shift-staffing"]')
        ramp = overview.locator('[data-metric="ramp-staffing"]')
        expect(shift.locator("dt")).to_have_text("Shift Staffing")
        expect(shift.locator("dd")).to_have_text("1")
        expect(ramp.locator("dt")).to_have_text("Ramp Staffing")
        expect(ramp.locator("dd")).to_have_text("—")
        self.assertAlmostEqual(shift.bounding_box()["y"], ramp.bounding_box()["y"], delta=1)
        self.assertLess(shift.bounding_box()["x"], ramp.bounding_box()["x"])
        misaligned = overview.evaluate("""el => {
            function textRect(element) {
                const range = document.createRange();
                range.selectNodeContents(element);
                return range.getBoundingClientRect();
            }
            const center = rect => rect.left + rect.width / 2;
            const centered = (a, b) => Math.abs(center(a) - center(b)) <= 1;
            const misaligned = [];
            const heading = el.querySelector('.rd-uld-title h2');
            if (!centered(textRect(heading), el.querySelector('.rd-uld').getBoundingClientRect())) {
                misaligned.push(heading.textContent);
            }
            const labels = [...el.querySelectorAll('.rd-uld thead th')];
            const waves = [...el.querySelectorAll('.rd-uld tbody tr')];
            const pairs = waves.flatMap(row => [...row.cells].map((cell, i) => [labels[i], cell]));
            pairs.push(...[...el.querySelectorAll('.rd-metric')].map(metric =>
                [metric.querySelector('dt'), metric.querySelector('dd')]));
            for (const [label, value] of pairs) {
                const a = textRect(label), b = textRect(value);
                const overlap = label.getBoundingClientRect().bottom - value.getBoundingClientRect().top;
                if (!centered(a, b) || overlap > 1) {
                    misaligned.push([label.textContent, value.textContent, center(a) - center(b), overlap]);
                }
            }
            return misaligned;
        }""")
        self.assertEqual(misaligned, [])

    def check_full_board(self, page, width, height, *, tv=False):
        page.evaluate("() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)))")
        state = page.evaluate("""() => {
            const boxes = [...document.querySelectorAll('.rd-mission-group')];
            const rows = [...document.querySelectorAll('[data-mission]')];
            const fields = rows.flatMap(row => [...row.cells]);
            const heights = rows.map(row => row.getBoundingClientRect().height);
            const left = boxes[0].getBoundingClientRect(), right = boxes[1].getBoundingClientRect();
            const bounds = document.querySelector('[data-raiden]').getBoundingClientRect();
            const values = [...fields,...document.querySelectorAll('.rd-value,.rd-totals dd')].filter(el => el.getClientRects().length);
            return {
                counts: boxes.map(box => box.querySelectorAll('[data-mission]').length),
                sideBySide: left.right <= right.left && left.top === right.top && left.width < right.width,
                columnWidths: boxes.map(box => [box.querySelector('table').getBoundingClientRect().width, box.clientWidth]),
                fitsColumns: boxes.every(box => box.querySelector('table').getBoundingClientRect().width <= box.clientWidth + 1),
                fitsAll: rows.every(row => row.getBoundingClientRect().bottom <= innerHeight + 1),
                noClipping: fields.every(cell => {
                    const rect = cell.getBoundingClientRect();
                    return rect.width > 0 && rect.right <= bounds.right + 1 && cell.scrollWidth <= cell.clientWidth + 1;
                }),
                oneLine: rows.every(row => row.getBoundingClientRect().height <=
                    parseFloat(getComputedStyle(row).lineHeight) + 4 &&
                    [...row.cells].every(cell => getComputedStyle(cell).whiteSpace === 'nowrap')),
                headers: boxes.map(box => [...box.querySelectorAll('thead th')].map(cell => cell.textContent)),
                cellsMatchHeaders: boxes.every(box => {
                    const keys = [...box.querySelectorAll('thead th')].map(cell => cell.dataset.field).join();
                    return [...box.querySelectorAll('tbody tr')].every(row =>
                        [...row.cells].map(cell => cell.dataset.field).join() === keys);
                }),
                fonts: [...new Set(values.map(cell => parseFloat(getComputedStyle(cell).fontSize)))],
                cellPadding: parseFloat(getComputedStyle(fields[0]).paddingInlineStart),
                canFont: parseFloat(getComputedStyle(document.querySelector('.rd-uld tbody td')).fontSize),
                scrollboxes: boxes.filter(box => /auto|scroll/.test(getComputedStyle(box).overflow)).length,
                width:document.documentElement.scrollWidth, height:document.documentElement.scrollHeight,
                rowMin:Math.min(...heights), rowMax:Math.max(...heights)
            };
        }""")
        self.assertEqual(state["counts"], [29, 31], state)
        self.assertEqual(state["headers"], list(HEADINGS), state)
        for field in ("sideBySide", "fitsColumns", "noClipping", "oneLine", "cellsMatchHeaders"):
            self.assertTrue(state[field], state)
        self.assertEqual(len(state["fonts"]), 1, state)
        self.assertGreaterEqual(state["fonts"][0], 10, state)
        self.assertLessEqual(state["fonts"][0], 24 if tv else 12, state)
        self.assertGreaterEqual(state["cellPadding"], 2, state)
        self.assertGreater(state["canFont"], state["fonts"][0], state)
        self.assertLessEqual(state["canFont"], 30 if tv else 20, state)
        self.assertEqual(state["scrollboxes"], 0, state)
        self.assertLessEqual(state["width"], width + 1, state)
        self.assertTrue(state["fitsAll"], state)
        if tv:
            self.assertLessEqual(state["height"], height + 1, state)
        return state

    def test_full_sort_workspaces(self):
        fixture = shell_tests.MobileDrawerBrowserTest
        fixture.setUpClass()
        self.addCleanup(fixture.tearDownClass)
        with fixture.app.app_context():
            operation = busy_sort(Gateway.query.one(), extra_arrivals=29, extra_departures=29)
            record_attendance(operation)
            record_departure_milestones(operation)
            for tail, position, lane in (("N431UP", "D05", 1), ("N402UP", "D06", 2)):
                db.session.add(SortDateParkingAssignment(sort_date_operation_id=operation.id,
                    tail_number=tail, ramp_code="D", position_code=position, lane_number=lane))
            departures = SortDateMission.query.filter_by(sort_date_operation_id=operation.id)
            departures.filter_by(flight_number="UPS0702").one().departure_status = "crew_load_complete"
            departures.filter_by(flight_number="UPS0403").one().wave = "2nd Wave"
            db.session.commit()
            op_id = operation.id

        output = Path("instance/browser-evidence/issue85/after")
        output.mkdir(parents=True, exist_ok=True)
        for engine in ("chromium", "webkit"):
            with self.subTest(engine=engine), getattr(fixture.pw, engine).launch() as browser:
                page = browser.new_page(viewport={"width":390, "height":844}, service_workers="block")
                fixture().login(page)
                fixture().ready(page, f"/neoraiden?gateway=RFD&operation_id={op_id}")
                expect(page.locator(".operational-mobile-header .node-word")).to_have_css("color", RAIDEN_BLUE)
                page.locator(".rd-staff summary").click()
                expect(page.locator(".rd-staff")).to_have_attribute("open", "")
                page.locator(".rd-staff summary").click()
                mobile_fonts = {}
                for width in (390, 430):
                    page.set_viewport_size({"width":width, "height":844})
                    for theme in ("dark", "light"):
                        self.set_theme(page, theme)
                        self.check_overview(page)
                        for direction, flight, count in (("Arrivals", "UPS0403", 29), ("Departures", "FX101", 31)):
                            page.get_by_role("button", name=direction, exact=True).click()
                            expect(page.locator("[data-mission]:visible")).to_have_count(count)
                            table = page.get_by_role("table", name=direction, exact=True)
                            visible_headers = table.locator("thead th:visible")
                            fonts = table.locator("tbody tr :is(th,td,.rd-value,.rd-flight-toggle):visible").evaluate_all(
                                "cells => [...new Set(cells.map(cell => parseFloat(getComputedStyle(cell).fontSize)))]")
                            self.assertEqual(len(fonts), 1, fonts)
                            self.assertTrue(10 <= fonts[0] <= 11.5, fonts)
                            mobile_fonts[width] = fonts[0]
                            if direction == "Arrivals":
                                expect(visible_headers).to_have_text(HEADINGS[0])
                                row = table.locator("[data-mission]").filter(has_text=flight)
                                expect(row.locator('[data-field="wave"] .rd-value')).to_have_text("2")
                                expect(row.locator("td:visible, th:visible").first).to_have_attribute("data-field", "wave")
                                expect(row.locator("td:visible, th:visible").last).to_have_attribute("data-field", "status")
                                expect(row.locator("[data-field='time'] small")).to_have_text("ACT")
                            else:
                                expect(visible_headers).to_have_text(HEADINGS[1][:6])
                                toggle = page.get_by_role("button", name=f"Details for {flight}", exact=True)
                                toggle.click()
                                row = toggle.locator("xpath=ancestor::tr")
                                expect(row.locator(".rd-detail-label:visible")).to_have_text(HEADINGS[1][6:])
                                expect(row.locator("[data-field='time'] time")).to_have_text("01:00")
                                for field, value in (("variance","+9"), ("fuel","45.6"), ("pull","00:57"),
                                                     ("ramp","00:59"), ("crew","01:02"), ("block","01:09"), ("no-return","Yes")):
                                    expect(row.locator(f'[data-field="{field}"] .rd-value')).to_have_text(value)
                            self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), width + 1)
                            self.assertTrue(table.locator("tbody td:visible, tbody th:visible").evaluate_all(
                                "cells => cells.every(cell => cell.scrollWidth <= cell.clientWidth + 1)"))
                            page.screenshot(path=output/f"mobile-{direction.lower()}-{engine}-{width}-{theme}.png")
                            if direction == "Departures":
                                toggle.click()
                (output/f"mobile-font-{engine}.json").write_text(json.dumps(mobile_fonts, indent=2))

                stats = []
                for mode in ("desktop", "board", "tv"):
                    for width, height in RESOLUTIONS:
                        page.set_viewport_size({"width":width, "height":height})
                        if mode == "board":
                            page.locator(".operational-sidebar-utilities [data-operational-board-toggle]").click()
                        if mode == "tv":
                            page.get_by_role("button", name="TV Mode", exact=True).click()
                            expect(page.get_by_role("button", name="Exit TV Mode")).to_be_visible()
                            self.assertTrue(page.evaluate("document.fullscreenElement === document.documentElement"))
                        else:
                            expect(page.locator(".operational-node-identity .node-word")).to_have_css("color", RAIDEN_BLUE)
                        for theme in ("dark", "light"):
                            self.set_theme(page, theme)
                            self.check_overview(page)
                            stats.append([mode, width, height, theme, self.check_full_board(page, width, height, tv=mode == "tv")])
                            page.screenshot(path=output/f"{mode}-{engine}-{height}-{theme}.png")
                        if mode == "tv":
                            page.keyboard.press("Escape")
                            page.wait_for_function("document.fullscreenElement === null")
                        if mode == "board":
                            page.get_by_role("button", name="Exit Board View").click()
                page.set_viewport_size({"width":640, "height":400})
                page.get_by_role("button", name="TV Mode", exact=True).click()
                expect(page.locator("[data-tv-notice]")).to_contain_text("cannot fit every flight legibly")
                expect(page.locator("[data-mission]")).to_have_count(60)

                page.set_viewport_size({"width":1440, "height":900})
                fixture().ready(page, "/motherbrain/system-settings/node-refresh-timings")
                expect(page.locator("#refresh-group-raiden .node-word")).to_have_css("color", RAIDEN_BLUE)
                switcher = page.locator("[data-operational-topbar] [data-character-switcher]")
                switcher.locator("summary").click()
                expect(switcher.locator("a.node-raiden .node-word")).to_have_css("color", RAIDEN_BLUE)
                (output/f"geometry-{engine}.json").write_text(json.dumps(stats, indent=2))
