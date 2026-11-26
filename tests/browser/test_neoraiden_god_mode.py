import json
import os
import unittest
from datetime import datetime, time, timedelta
from pathlib import Path

from playwright.sync_api import expect

from app.extensions import db
from app.models import Gateway, NeoSubZeroDepartureDeiceEvent, NeoSubZeroPretreatState, SortDateMission, SortDateParkingAssignment
from tests.browser import test_mobile_drawer as shell_tests
from tests.neoraiden_fixtures import busy_sort, record_attendance, record_departure_milestones, record_pull_roster


RESOLUTIONS = ((1280, 720), (1366, 768), (1920, 1080), (3840, 2160))
RAIDEN_BLUE = "rgb(159, 201, 249)"
HEADINGS = (
    ["Wave", "Flight", "Tail", "Origin", "POS", "Time", "+/−", "Status"],
    ["Flight", "Tail", "Dest", "POS", "Time", "Status", "Neo Fuel",
     "PURE", "MIX", "R-LC", "C-LC", "Block", "+/−", "DEICE", "No Return"],
)


class GodModeBrowser(unittest.TestCase):
    def set_theme(self, page, theme):
        board = page.locator("[data-raiden]")
        if board.get_attribute("data-theme") != theme:
            board.locator("[data-theme-toggle]").click()
        expect(board.locator(".rd-wordmark")).to_have_css("color", RAIDEN_BLUE)
        expect(board.locator("[data-refresh], [data-countdown], [data-pause], [data-interval], .rd-settings")).to_have_count(0)
        expect(board.locator("h1")).to_have_text("NeoRaiden")
        expect(page).to_have_title("NeoRaiden")
        expect(board.locator(".rd-overview")).not_to_contain_text("Plan")
        expect(board.locator(".rd-overview")).not_to_contain_text("On ballmats")
        expect(board.locator("#rd-departures [data-mission] [data-field='time'] small")).to_have_count(0)

    def check_overview(self, page):
        overview = page.locator(".rd-overview")
        shift = overview.locator('[data-metric="shift-staffing"]')
        ramp = overview.locator('[data-metric="ramp-staffing"]')
        expect(shift.locator("dt")).to_have_text("Shift Staffing")
        expect(shift.locator("dd")).to_have_text("1")
        expect(ramp.locator("dt")).to_have_text("Ramp Staffing")
        expect(ramp.locator("dd")).to_have_text("—")
        expect(overview.get_by_role("table", name="CAN / ULD counts").locator("thead th")).to_have_text(
            ["Wave", "East", "West", "Left to arrive", "Left to unload"])
        expect(overview.locator(".rd-uld tbody th")).to_have_text(["1st", "2nd", "Openings"])
        expect(overview.locator("tr[data-openings] td")).to_have_text(["2", "3", "", ""])
        expect(overview.locator(".rd-averages")).to_have_count(0)
        expect(overview.locator(".rd-section-heading")).to_have_text(["Inbound ULD Counts", "Flight Data", "Staffing"])
        expect(overview.locator(".rd-mission-count")).to_have_text(["29", "31"])
        expect(overview.locator(".rd-performance tbody th")).to_have_text(["1st Wave", "2nd Wave", "Total"])
        expect(overview.locator('[data-performance-wave="combined"] td')).to_have_text(
            ["0", "0", "0", "1", "9", "9"])
        expect(page.locator("[data-raiden] details, [data-mission-details]")).to_have_count(0)
        metrics = overview.evaluate("""el => {
            const ctx = document.createElement('canvas').getContext('2d', {willReadFrequently: true});
            function luminance(color) {
                ctx.fillStyle = color;
                ctx.fillRect(0, 0, 1, 1);
                const [r,g,b] = [...ctx.getImageData(0, 0, 1, 1).data].slice(0, 3).map(value => {
                    const c = value / 255;
                    return c <= .04045 ? c / 12.92 : ((c + .055) / 1.055) ** 2.4;
                });
                return .2126 * r + .7152 * g + .0722 * b;
            }
            const background = luminance(getComputedStyle(el.querySelector('.rd-staffing')).backgroundColor);
            const ordinary = el.querySelector('.rd-staffing dt');
            const muted = luminance(getComputedStyle(el).getPropertyValue('--muted').trim());
            const contrast = l => (Math.max(l, background) + .05) / (Math.min(l, background) + .05);
            return {
                labelContrast: contrast(luminance(getComputedStyle(ordinary).color)),
                mutedContrast: contrast(muted),
                boxWidths: [...el.children].map(box => box.getBoundingClientRect().width),
                frames: [...el.children].map(box => [getComputedStyle(box).borderTopWidth,
                    getComputedStyle(box.querySelector('h2')).backgroundColor]),
                sectionHeights: [...el.children].map(box => box.getBoundingClientRect().height),
                clipped: [...el.querySelectorAll('th,td,dt,dd,h2,.rd-overview-box')].filter(cell =>
                    cell.scrollWidth > cell.clientWidth + 1).map(cell => cell.textContent),
            };
        }""")
        self.assertGreater(metrics["labelContrast"], metrics["mutedContrast"], metrics)
        self.assertGreaterEqual(metrics["labelContrast"], 4.5, metrics)
        self.assertEqual(metrics["clipped"], [], metrics)
        self.assertTrue(all(border == "1px" and fill != "rgba(0, 0, 0, 0)" for border, fill in metrics["frames"]))
        misaligned = overview.evaluate("""el => {
            function textRect(element) {
                const range = document.createRange();
                range.selectNodeContents(element);
                return range.getBoundingClientRect();
            }
            const center = rect => rect.left + rect.width / 2;
            const centered = (a, b) => Math.abs(center(a) - center(b)) <= 1;
            const misaligned = [];
            const labels = [...el.querySelectorAll('.rd-uld thead th')];
            const waves = [...el.querySelectorAll('.rd-uld tbody tr')];
            const pairs = waves.flatMap(row => [...row.cells].flatMap((cell, i) =>
                cell.textContent ? [[labels[i], cell]] : []));
            pairs.push(...[...el.querySelectorAll('.rd-totals .rd-metric')].map(metric =>
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
        return metrics

    def check_full_board(self, page, width, height, *, tv=False):
        page.evaluate("() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)))")
        state = page.evaluate("""() => {
            const boxes = [...document.querySelectorAll('.rd-mission-group')];
            const rows = [...document.querySelectorAll('[data-mission]')];
            const fields = rows.flatMap(row => [...row.cells]);
            const heights = rows.map(row => row.getBoundingClientRect().height);
            const left = boxes[0].getBoundingClientRect(), right = boxes[1].getBoundingClientRect();
            const bounds = document.querySelector('[data-raiden]').getBoundingClientRect();
            const values = [...fields,...document.querySelectorAll('.rd-value')].filter(el => el.getClientRects().length);
            const toolbar = [...document.querySelector('.rd-header').children].filter(el => el.clientHeight > 0);
            const verticalCenters = toolbar.map(el => el.getBoundingClientRect().top + el.clientHeight / 2);
            return {
                counts: boxes.map(box => box.querySelectorAll('[data-mission]').length),
                sideBySide: left.right <= right.left && left.top === right.top && left.width < right.width,
                columnWidths: boxes.map(box => [box.querySelector('table').getBoundingClientRect().width, box.clientWidth]),
                fitsColumns: boxes.every(box => box.querySelector('table').getBoundingClientRect().width <= box.clientWidth + 1),
                fitsAll: rows.every(row => row.getBoundingClientRect().bottom <= innerHeight + 1),
                lastMissionBottom: Math.max(...rows.map(row => row.getBoundingClientRect().bottom)),
                noClipping: fields.every(cell => {
                    const rect = cell.getBoundingClientRect();
                    return rect.width > 0 && rect.right <= bounds.right + 1 && cell.scrollWidth <= cell.clientWidth + 1;
                }),
                overflowingCells: fields.filter(cell => cell.scrollWidth > cell.clientWidth + 1)
                    .map(cell => [cell.dataset.field, cell.innerText, cell.clientWidth, cell.scrollWidth]),
                oneLine: rows.every(row => [...row.cells].every(cell => {
                    const style = getComputedStyle(cell);
                    return style.whiteSpace === 'nowrap' && cell.getBoundingClientRect().height <=
                        parseFloat(style.lineHeight) + parseFloat(style.paddingTop) + parseFloat(style.paddingBottom) + 1.25;
                })),
                headers: boxes.map(box => [...box.querySelectorAll('table:not(.rd-paired) thead th')].map(cell => cell.textContent)),
                cellsMatchHeaders: boxes.every(box => {
                    const keys = [...box.querySelectorAll('table:not(.rd-paired) thead th')].map(cell => cell.dataset.field).join();
                    return [...box.querySelectorAll('table:not(.rd-paired) tbody tr')].every(row =>
                        [...row.cells].map(cell => cell.dataset.field).join() === keys);
                }),
                fonts: [...new Set(values.map(cell => parseFloat(getComputedStyle(cell).fontSize)))],
                cellPadding: parseFloat(getComputedStyle(fields[0]).paddingInlineStart),
                overviewFonts: [...new Set([...document.querySelectorAll('.rd-overview :is(tbody td,dd,.rd-mission-count)')]
                    .map(cell => parseFloat(getComputedStyle(cell).fontSize)))],
                overviewLabels: [...new Set([...document.querySelectorAll('.rd-overview :is(th,dt,.rd-count-label)')]
                    .map(cell => parseFloat(getComputedStyle(cell).fontSize)))],
                overviewHeight: document.querySelector('.rd-overview').getBoundingClientRect().height,
                toolbarSpread: Math.max(...verticalCenters) - Math.min(...verticalCenters),
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
        self.assertGreaterEqual(state["cellPadding"], 1.5, state)
        expected = (32, 22) if tv and height >= 2160 else (24, 16) if tv and height >= 1080 else (18, 14)
        self.assertEqual(state["overviewFonts"], [expected[0]], state)
        self.assertEqual(state["overviewLabels"], [expected[1]], state)
        self.assertLessEqual(state["toolbarSpread"], 2, state)
        self.assertEqual(state["scrollboxes"], 0, state)
        self.assertLessEqual(state["width"], width + 1, state)
        if tv:
            self.assertTrue(state["fitsAll"], state)
            self.assertLessEqual(state["height"], height + 1, state)
        return state

    def check_outline(self, board):
        expect(board).to_have_css("outline-width", "1px")
        expect(board).to_have_css("outline-offset", "-1px")
        expect(board).to_have_css("border-top-left-radius", "5px")
        self.assertTrue(board.evaluate("""el => getComputedStyle(el).outlineColor ===
            getComputedStyle(el.closest('[data-raiden]').querySelector('.rd-overview-box')).borderTopColor"""))
        expect(board).to_have_css("padding", "0px")

    def check_pulse(self, page, active):
        expect(active).to_have_css("animation-name", "rd-deice-breathe")
        expect(active).to_have_css("animation-duration", "3s")
        expect(active.locator("xpath=ancestor::tr")).to_have_css("animation-name", "none")
        for state in ("fin", "pretreated"):
            expect(page.locator(f'[data-deice-state="{state}"]:visible')).to_have_css("animation-name", "none")
        page.emulate_media(reduced_motion="reduce")
        expect(active).to_have_css("animation-name", "none")
        page.emulate_media(reduced_motion="no-preference")

    def test_full_sort_workspaces(self):
        fixture = shell_tests.MobileDrawerBrowserTest
        fixture.setUpClass()
        self.addCleanup(fixture.tearDownClass)
        with fixture.app.app_context():
            operation = busy_sort(Gateway.query.one(), extra_arrivals=29, extra_departures=29)
            fixture.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = datetime.combine(operation.sort_date, time(21))
            record_attendance(operation)
            record_departure_milestones(operation)
            record_pull_roster(operation)
            event = NeoSubZeroDepartureDeiceEvent.query.filter_by(sort_date_operation_id=operation.id).one()
            start = datetime(2026, 4, 13, 4, 45)
            event.tail_number, event.status, event.radio_state = "N431UP", "cleared", "pending"
            event.clear_outcome, event.treatment_plan = "sprayed", "one_type_i"
            event.pass1_started_at_utc, event.pass1_ended_at_utc = start, start + timedelta(minutes=30)
            active = SortDateMission.query.filter_by(sort_date_operation_id=operation.id, flight_number="UPS0712").one()
            db.session.add(NeoSubZeroDepartureDeiceEvent(sort_date_operation_id=operation.id, sort_date_mission_id=active.id,
                tail_number=active.assigned_tail_number, status="configured", treatment_plan="one_type_i",
                configured_at_utc=start, pass1_started_at_utc=start + timedelta(minutes=3)))
            db.session.add(NeoSubZeroPretreatState(sort_date_operation_id=operation.id, tail_number="N432UP",
                pass1_started_at_utc=start, pass1_ended_at_utc=start + timedelta(minutes=7),
                pass2_started_at_utc=start + timedelta(minutes=10), pass2_ended_at_utc=start + timedelta(minutes=17)))
            for tail, position, lane in (("N431UP", "D05", 1), ("N402UP", "D06", 2)):
                db.session.add(SortDateParkingAssignment(sort_date_operation_id=operation.id,
                    tail_number=tail, ramp_code="D", position_code=position, lane_number=lane))
            departures = SortDateMission.query.filter_by(sort_date_operation_id=operation.id)
            departures.filter_by(flight_number="UPS0702").one().departure_status = "crew_load_complete"
            departures.filter_by(flight_number="UPS0403").one().wave = "2nd Wave"
            db.session.commit()
            op_id = operation.id

        output = Path(os.environ.get("RAIDEN_SPACING_EVIDENCE", "instance/browser-evidence/issue91-spacing/after"))
        output.mkdir(parents=True, exist_ok=True)
        for engine in ("chromium", "webkit"):
            with self.subTest(engine=engine), getattr(fixture.pw, engine).launch() as browser:
                page = browser.new_page(viewport={"width":390, "height":844}, service_workers="block")
                fixture().login(page)
                fixture().ready(page, f"/neoraiden?gateway=RFD&operation_id={op_id}")
                expect(page.locator(".operational-mobile-header .node-word")).to_have_css("color", RAIDEN_BLUE)
                mobile_fonts = {}
                mobile_metrics = {}
                for width in (390, 430):
                    page.set_viewport_size({"width":width, "height":844})
                    for theme in ("dark", "light"):
                        self.set_theme(page, theme)
                        overview_metrics = self.check_overview(page)
                        mobile_metrics[f"{width}-{theme}"] = page.locator(".rd-overview").evaluate("""el => ({
                            overviewHeight: el.getBoundingClientRect().height,
                            overviewFont: parseFloat(getComputedStyle(el.querySelector('.rd-staffing dd')).fontSize),
                            overviewLabel: parseFloat(getComputedStyle(el.querySelector('.rd-staffing dt')).fontSize)
                        })""")
                        metrics = mobile_metrics[f"{width}-{theme}"]
                        metrics.update(overview_metrics)
                        self.assertEqual((metrics["overviewFont"], metrics["overviewLabel"]), (16, 13), metrics)
                        page.locator(".rd-overview").screenshot(path=output/f"overview-mobile-{engine}-{width}-{theme}.png")
                        for direction, flight, count in (("Arrivals", "UPS0403", 29), ("Departures", "FX101", 31)):
                            page.get_by_role("button", name=direction, exact=True).click()
                            expect(page.locator(":is([data-mission],[data-paired-mission]):visible")).to_have_count(count)
                            table = page.get_by_role("table", name=direction, exact=True)
                            self.check_outline(page.locator(f"#rd-{direction.lower()}"))
                            visible_headers = table.locator("thead th:visible")
                            fonts = table.locator("tbody tr :is(th,td,.rd-value):visible").evaluate_all(
                                "cells => [...new Set(cells.map(cell => parseFloat(getComputedStyle(cell).fontSize)))]")
                            self.assertEqual(len(fonts), 1, fonts)
                            self.assertTrue(10 <= fonts[0] <= 11.5, fonts)
                            mobile_fonts[width] = fonts[0]
                            mobile_metrics[f"{width}-{theme}"][direction] = {
                                "font": fonts[0],
                                "rowHeight": table.locator("tbody tr").first.bounding_box()["height"],
                            }
                            if direction == "Arrivals":
                                expect(visible_headers).to_have_text(HEADINGS[0])
                                row = table.locator("[data-mission]").filter(has_text=flight)
                                expect(row.locator('[data-field="wave"] .rd-value')).to_have_text("2")
                                expect(row.locator("td:visible, th:visible").first).to_have_attribute("data-field", "wave")
                                expect(row.locator("td:visible, th:visible").last).to_have_attribute("data-field", "status")
                                expect(row.locator("[data-field='time'] small")).to_have_text("ACT")
                            else:
                                self.assertEqual(visible_headers.evaluate_all("cells => cells.map(el => el.dataset.pair)"),
                                    ["flight", "tail", "pure", "fuel", "ramp", "time", "no-return"])
                                row = table.locator("[data-paired-mission]").filter(has_text=flight)
                                expect(row.locator('[data-pair="time"] [data-field="block"]')).to_have_attribute(
                                    "aria-label", "Block 01:09")
                                for field, value in (("variance","+9"), ("fuel","45.6"), ("pure","00:02"), ("mix","00:57"),
                                                     ("ramp","00:59"), ("crew","01:02"), ("block","01:09"), ("no-return","✓")):
                                    expect(row.locator(f'[data-field="{field}"] .rd-value')).to_have_text(value)
                                expect(row.locator(".rd-deice-short")).to_have_text("FIN 00:15")
                                expect(row.locator(".rd-deice")).to_have_attribute("title", "Completed 00:15")
                                warning = "rgb(246, 227, 122)" if theme == "dark" else "rgb(100, 84, 9)"
                                for field in ("ramp", "crew"):
                                    expect(row.locator(f'[data-field="{field}"] .rd-value')).to_have_css("color", warning)
                                expect(row.locator('[data-field="flight"] .rd-value')).to_have_attribute("data-value-color", "late")
                                ready = "rgb(137, 230, 180)" if theme == "dark" else "rgb(23, 99, 63)"
                                expect(row.get_by_role("img", name="No Return confirmed")).to_have_css("color", ready)
                                late = "rgb(242, 163, 170)" if theme == "dark" else "rgb(147, 46, 41)"
                                expect(row.locator('[data-field="variance"] .rd-value')).to_have_css("color", late)
                                expect(table.locator('[data-deice-state="pretreated"] .rd-deice-short')).to_have_text("Pre 23:55")
                                self.check_pulse(page, table.locator('[data-deice-state="deicing"]'))
                                page.evaluate("window.scrollTo(0, 0)")
                            self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), width + 1)
                            self.assertTrue(table.locator("tbody td:visible, tbody th:visible").evaluate_all(
                                "cells => cells.every(cell => cell.scrollWidth <= cell.clientWidth + 1)"))
                            page.screenshot(path=output/f"mobile-{direction.lower()}-{engine}-{width}-{theme}.png")
                (output/f"mobile-font-{engine}.json").write_text(json.dumps(mobile_fonts, indent=2))
                (output/f"mobile-metrics-{engine}.json").write_text(json.dumps(mobile_metrics, indent=2))

                page.set_viewport_size({"width":1280, "height":720})
                page.get_by_label("Gateway", exact=True).focus()
                for selector in ("[data-sort-choice]", "[data-search]", "[data-tv]", "[data-theme-toggle]"):
                    page.keyboard.press("Tab")
                    expect(page.locator(selector)).to_be_focused()
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
                            overview_metrics = self.check_overview(page)
                            self.check_outline(page.locator("#rd-arrivals"))
                            self.check_outline(page.locator("#rd-departures"))
                            self.check_pulse(page, page.locator('.rd-wide-departures [data-deice-state="deicing"]'))
                            expect(page.locator('.rd-wide-departures [data-deice-state="pretreated"] .rd-deice-short')).to_have_text("Pretreated 23:55")
                            self.assertGreater(overview_metrics["boxWidths"][1], max(overview_metrics["boxWidths"][0], overview_metrics["boxWidths"][2]))
                            expect(page.locator('.rd-wide-departures [data-value-color="no-return"]').first).to_have_text("✓")
                            page.screenshot(path=output/f"{mode}-{engine}-{height}-{theme}.png")
                            geometry = self.check_full_board(page, width, height, tv=mode == "tv")
                            if not geometry["fitsAll"]:
                                last = page.locator("#rd-departures [data-mission]").last
                                last.scroll_into_view_if_needed()
                                bounds = last.bounding_box()
                                self.assertLessEqual(bounds["y"] + bounds["height"], height + 1)
                                page.evaluate("window.scrollTo(0, 0)")
                            stats.append([mode, width, height, theme, {**geometry, **overview_metrics}])
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
