import json
import os
import re
import unittest
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import expect

from app.extensions import db
from app.models import Gateway, NeoSubZeroDepartureDeiceEvent, NeoSubZeroOperationContext, NeoSubZeroPretreatState, SortDateMission, SortDateParkingAssignment
from app.services import neosubzero_weather
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

    def check_overview(self, page, *, comparison=False):
        overview = page.locator(".rd-overview")
        shift = overview.locator('[data-metric="shift-staffing"]')
        ramp = overview.locator('[data-metric="ramp-staffing"]')
        expect(shift.locator("dt")).to_have_text("Shift Staffing")
        expect(shift.locator("dd")).to_have_text("1")
        expect(ramp.locator("dt")).to_have_text("Ramp Staffing")
        expect(ramp.locator("dd")).to_be_empty()
        expect(overview.get_by_role("table", name="CAN / ULD counts").locator("thead th")).to_have_text(
            ["Wave", "East", "West", "Left to arrive", "Left to unload"])
        expect(overview.locator(".rd-uld tbody th")).to_have_text(["1st", "2nd", "Openings"])
        expect(overview.locator("tr[data-openings] td")).to_have_text(["2", "3", "", ""])
        averages = overview.locator(".rd-averages dd")
        if comparison:
            expect(averages).to_have_text(["5.6", "10.0"])
        else:
            expect(averages).to_have_text(["5.0", "0.0"])
        expect(overview.locator(".rd-section-heading")).to_have_text(["Time & Weather", "Inbound ULD Counts", "Flight Data", "Staffing"])
        weather = overview.locator(".rd-weather")
        clock = weather.locator("[data-gateway-clock]")
        expect(clock).to_have_text(re.compile(r"^\d{2}:\d{2}$"))
        expect(weather.locator('[data-metric="temperature"] dd')).to_have_text("0°F")
        expect(weather.locator('[data-metric="precipitation"] dd')).to_have_text("Light snow")
        expect(weather.locator('[data-metric="dewpoint"] dd')).to_have_text("-4°F")
        expect(weather.locator('[data-metric="spread"] dd')).to_have_text("4°F")
        expect(weather.locator('[data-metric="relative_humidity"] dd')).to_have_text(re.compile(r"^\d+%$"))
        expect(weather.locator('[data-metric="wind"] dd')).to_have_text("300° 12 kt G20 kt")
        expect(weather.locator('[data-metric="visibility"] dd')).to_have_text("0 SM")
        expect(weather.locator(".rd-weather-meta")).to_contain_text("Mist · KRFD METAR")
        self.assertEqual(clock.evaluate("el => getComputedStyle(el).fontSize"),
                         weather.locator('[data-metric="temperature"] dd').evaluate("el => getComputedStyle(el).fontSize"))
        expect(overview.locator(".rd-mission-count")).to_have_text(["29", "31"])
        expect(overview.locator(".rd-count-line")).to_have_text(["29 Arrivals", "31 Departures"])
        for group in overview.locator("[data-performance]").all():
            direction, count = (group.locator(selector).bounding_box()
                                for selector in (".rd-direction", ".rd-count-line"))
            self.assertLessEqual(direction["y"] + direction["height"], count["y"] + .5)
            self.assertAlmostEqual(direction["x"] + direction["width"] / 2,
                                   count["x"] + count["width"] / 2, delta=.5)
            expect(group.locator(".rd-count-line")).to_have_css("text-align", "center")
        expect(overview.get_by_role("rowheader", name="1st Wave", exact=True)).to_be_visible()
        expect(overview.get_by_role("rowheader", name="2nd Wave", exact=True)).to_be_visible()
        expect(overview.locator('[data-performance-wave="first_wave"] td')).to_have_text(["", "", "", "1", "9", "9"])
        expect(overview.locator('[data-performance-wave="combined"] td')).to_have_text(
            ["0", "0", "0", "1", "9", "9"])
        expect(page.locator("[data-raiden] details, [data-mission-details]")).to_have_count(0)
        audit = page.locator("[data-raiden]").evaluate("""root => {
            const visible = el => el.getBoundingClientRect().width > 1 && !el.closest('[hidden]');
            const bold = [...root.querySelectorAll('*')].filter(el => visible(el) &&
                (el.matches('input,button,select') ||
                 [...el.childNodes].some(child => child.nodeType === 3 && child.textContent.trim())) &&
                getComputedStyle(el).fontWeight !== '400').map(el => [el.tagName, el.className]);
            const placeholders = [...root.querySelectorAll('td,dd,.rd-pair-value')].filter(el =>
                visible(el) && /^[-–—]$/.test(el.textContent.trim())).map(el => el.outerHTML);
            return {bold, placeholders};
        }""")
        self.assertEqual(audit, {"bold": [], "placeholders": []})
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
                sectionHeights: [...el.children].map(box => box.getBoundingClientRect().height),
                clipped: [...el.querySelectorAll('th,td,dt,dd,h2,.rd-overview-box')].filter(cell =>
                    cell.scrollWidth > cell.clientWidth + 1).map(cell => cell.textContent),
            };
        }""")
        self.assertGreater(metrics["labelContrast"], metrics["mutedContrast"], metrics)
        self.assertGreaterEqual(metrics["labelContrast"], 4.5, metrics)
        self.assertEqual(metrics["clipped"], [], metrics)
        metrics.update(self.check_sections(overview))
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
                const a = textRect(label), b = value.textContent.trim() ? textRect(value) : value.getBoundingClientRect();
                const overlap = label.getBoundingClientRect().bottom - value.getBoundingClientRect().top;
                if (!centered(a, b) || overlap > 1) {
                    misaligned.push([label.textContent, value.textContent, center(a) - center(b), overlap]);
                }
            }
            return misaligned;
        }""")
        self.assertEqual(misaligned, [])
        return metrics

    def check_sections(self, overview):
        metrics = overview.evaluate("""el => {
            const root = el.closest('[data-raiden]');
            const style = node => getComputedStyle(node);
            const rect = node => node.getBoundingClientRect();
            const boxes = [...el.children];
            const boards = [...root.querySelectorAll('.rd-mission-group')].filter(box => rect(box).width > 0);
            const titles = [...boxes, ...boards].map(box => box.querySelector('.rd-section-heading'))
                .filter(title => rect(title).width > 1);
            const workspace = root.querySelector('.rd-workspace');
            const pairedTabs = workspace.querySelector('.rd-mobile-views');
            const observed = [rect(workspace).top - rect(el).bottom];
            for (let i = 1; i < boxes.length; i++) {
                const previous = rect(boxes[i - 1]), current = rect(boxes[i]);
                observed.push(Math.abs(current.top - previous.top) < 1
                    ? current.left - previous.right : current.top - previous.bottom);
            }
            if (boards.length === 2) observed.push(rect(boards[1]).left - rect(boards[0]).right);
            if (rect(pairedTabs).height) {
                observed.push(rect(boards[0]).top - rect(pairedTabs).bottom);
                observed.push(rect(pairedTabs.children[1]).left - rect(pairedTabs.children[0]).right);
            }
            const surface = style(boxes[0]).backgroundColor;
            const tableLayers = el.querySelectorAll('table,thead,tbody,tfoot,tr,th,td');
            return {
                gaps: observed,
                sectionGap: parseFloat(style(el).gap),
                titleOffsets: titles.map(title => {
                    const text = document.createRange();
                    text.selectNodeContents(title);
                    const line = text.getBoundingClientRect(), bar = rect(title);
                    return Math.abs(line.left + line.width / 2 - bar.left - bar.width / 2);
                }),
                titleSizes: titles.map(title => style(title).fontSize),
                titleHeights: titles.map(title => rect(title).height),
                titleStyles: [...new Set(titles.map(title => [style(title).backgroundColor, style(title).color,
                    style(title).padding, style(title).textAlign].join('|')))],
                frames: [...new Set([...boxes, ...boards].map(box => [style(box).outlineWidth,
                    style(box).outlineOffset, style(box).outlineColor, style(box).borderRadius].join('|')))],
                uniformSurface: boxes.every(box => style(box).backgroundColor === surface) &&
                    [...tableLayers].every(node => ['rgba(0, 0, 0, 0)', surface].includes(style(node).backgroundColor)),
                missionStripes: boards.every(box => {
                    const rows = box.querySelector('table:not(.rd-paired)').tBodies[0].rows;
                    return style(rows[0]).backgroundColor !== style(rows[1]).backgroundColor;
                }),
            };
        }""")
        for gap in metrics["gaps"]:
            self.assertAlmostEqual(gap, metrics["sectionGap"], delta=.15, msg=metrics)
        width = overview.page.viewport_size["width"]
        self.assertEqual(metrics["sectionGap"], 16 if width == 3840 else 8)
        self.assertTrue(all(offset < 1 for offset in metrics["titleOffsets"]), metrics)
        self.assertLess(max(metrics["titleHeights"]) - min(metrics["titleHeights"]), .15, metrics)
        self.assertEqual(len(metrics["titleStyles"]), 1, metrics)
        self.assertEqual(len(metrics["frames"]), 1, metrics)
        self.assertTrue(metrics["frames"][0].startswith("1px|-1px|"), metrics)
        self.assertTrue(metrics["frames"][0].endswith("|5px"), metrics)
        self.assertTrue(metrics["uniformSurface"], metrics)
        self.assertTrue(metrics["missionStripes"], metrics)
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
        expected = (31, 22) if tv and height >= 2160 else (23, 16) if tv and height >= 1080 else (17, 14)
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
            getComputedStyle(el.closest('[data-raiden]').querySelector('.rd-overview-box')).outlineColor"""))
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
        observed = datetime.now(timezone.utc) - timedelta(minutes=10)
        cache = {"krfd_metar": dict(value=[dict(icaoId="KRFD", reportTime=observed.isoformat(),
                 temp=-17.78, dewp=-20, wdir=300, wspd=12, wgst=20, visib=0, wxString="Mist")],
                 fetched_at=datetime.now(timezone.utc).timestamp())}
        self.enterContext(patch.object(neosubzero_weather, "_CACHE", cache))
        self.enterContext(patch.object(neosubzero_weather, "_request_json", side_effect=AssertionError("Weather I/O")))
        fixture.app.config["NEOSUBZERO_WEATHER_ENABLED"] = True
        with fixture.app.app_context():
            operation = busy_sort(Gateway.query.one(), extra_arrivals=29, extra_departures=29)
            fixture.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = datetime.combine(operation.sort_date, time(21))
            db.session.add(NeoSubZeroOperationContext(sort_date_operation_id=operation.id, active_precipitation="Light snow"))
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
                        self.assertEqual((metrics["overviewFont"], metrics["overviewLabel"]), (13, 11), metrics)
                        self.assertLess(metrics["overviewHeight"], 415, metrics)
                        expect(page.locator(".rd-performance thead .rd-short-label:visible")).to_have_text(
                            ["Late", "Min", "Avg", "Late", "Min", "Avg"])
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
                            self.assertGreater(overview_metrics["boxWidths"][2], max(overview_metrics["boxWidths"][:2] + overview_metrics["boxWidths"][3:]))
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
                # Force a nonredundant comparison using an explicitly included mission outside waves 1 and 2.
                with fixture.app.app_context():
                    outlier = SortDateMission.query.filter_by(sort_date_operation_id=op_id, flight_number="UPS0404").one()
                    original = outlier.actual_block_in_datetime_utc, outlier.late_metrics_included_override
                    outlier.actual_block_in_datetime_utc = outlier.planned_datetime_utc + timedelta(minutes=10)
                    outlier.late_metrics_included_override = True
                    db.session.commit()
                try:
                    page.set_viewport_size({"width":1280, "height":720})
                    fixture().ready(page, f"/neoraiden?gateway=RFD&operation_id={op_id}")
                    page.get_by_role("button", name="TV Mode", exact=True).click()
                    expect(page.get_by_role("button", name="Exit TV Mode")).to_be_visible()
                    for theme in ("dark", "light"):
                        self.set_theme(page, theme)
                        overview_metrics = self.check_overview(page, comparison=True)
                        geometry = self.check_full_board(page, 1280, 720, tv=True)
                        stats.append(["tv-averages", 1280, 720, theme, {**geometry, **overview_metrics}])
                        page.screenshot(path=output/f"tv-averages-{engine}-720-{theme}.png")
                    page.keyboard.press("Escape")
                    page.wait_for_function("document.fullscreenElement === null")
                finally:
                    with fixture.app.app_context():
                        outlier = SortDateMission.query.filter_by(sort_date_operation_id=op_id, flight_number="UPS0404").one()
                        outlier.actual_block_in_datetime_utc, outlier.late_metrics_included_override = original
                        db.session.commit()
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
