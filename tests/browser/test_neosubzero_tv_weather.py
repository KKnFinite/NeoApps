"""The DUCC wallboard preserves weather, physical geometry and draft-safe refresh."""
import copy
from datetime import datetime
import json
import re
import unittest
from pathlib import Path

from playwright.sync_api import expect
from app.extensions import db
from app.models import (NeoSubZeroDepartureDeiceEvent, NeoSubZeroSprayRecord, NeoSubZeroTruck, SortDateOperation,
                        SortDateParkingAssignment, StaffingDailyAttendance, StaffingPerson, StaffingUnit)
from app.services.live_screen_refresh import save_live_screen_refresh_override
from app.services.neosubzero_spray import set_neosubzero_ucc_truck
from app.services.neosubzero_ucc import set_neosubzero_ucc_assignment
from tests.browser import test_neosubzero_ucc as fixtures

EVIDENCE = Path('instance/ducc-weather/browser')
ICON_COLORS = {
    'planned': '#876000', 'configured': '#176a44', 'deicing': '#12657d',
    'fin': '#285da8', 'radio-pending': '#b31977', 'negative': '#ab3045', 'gone': '#536472',
}


def contrast(foreground, background):
    def luminance(color):
        channels = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
        linear = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4 for value in channels]
        return sum(value * weight for value, weight in zip(linear, (.2126, .7152, .0722)))
    dark, light = sorted(map(luminance, (foreground, background)))
    return (light + .05) / (dark + .05)


def rgb(hex_color):
    return 'rgb(' + ', '.join(str(int(hex_color[i:i + 2], 16)) for i in (1, 3, 5)) + ')'


class DuccTvWeatherTest(unittest.TestCase):
    def seed_wallboard(self, case):
        with case.app.app_context():
            operation = db.session.get(SortDateOperation, case.operation_id)
            save_live_screen_refresh_override(operation.gateway, "neosubzero.ucc_tv", "5")
            crew, present, unmarked = [db.session.get(StaffingPerson, person_id) for person_id in case.people]
            crew.last_name, present.last_name, unmarked.last_name = "De la Cruz", "O'Neil", "Smith-Jones"
            db.session.flush()
            for ramp, number in (('Remote', '12'), ('Early Out', 'UCC-831')):
                set_neosubzero_ucc_truck(operation, ramp, 1, number)
            set_neosubzero_ucc_assignment(operation, 'Early Out', 1, 'driver', crew)
            set_neosubzero_ucc_assignment(operation, 'Early Out', 1, 'flyer', present)
            set_neosubzero_ucc_assignment(operation, 'Remote', 1, 'driver', unmarked)
            NeoSubZeroTruck.query.filter_by(gateway_id=operation.gateway_id, number='UCC-831').one().active = False
            night = StaffingUnit.query.filter_by(unit_type='sort', name='Night').one()
            db.session.add(StaffingDailyAttendance(person_id=crew.id, sort_unit_id=night.id,
                sort_date_operation_id=operation.id, attendance_date=operation.sort_date,
                work_area_unit_id=crew.work_assignments[0].work_area_unit_id, status='call_in'))
            db.session.add(StaffingDailyAttendance(person_id=present.id, sort_unit_id=night.id,
                sort_date_operation_id=operation.id, attendance_date=operation.sort_date, status='here'))
            fixture = fixtures.NeoSubZeroUccTest()
            fixture.operation = operation
            remote = fixture._departure('UPSREMOTE', 'NREMOTE', 'SDF', 3)
            db.session.add(SortDateParkingAssignment(sort_date_operation_id=operation.id,
                tail_number=remote.assigned_tail_number, ramp_code='R', position_code='R01'))
            event = NeoSubZeroDepartureDeiceEvent.query.filter_by(sort_date_mission_id=case.missions[0]).one()
            db.session.add(NeoSubZeroSprayRecord(sort_date_operation_id=operation.id, sort_date_mission_id=case.missions[0],
                departure_deice_event_id=event.id, tail_number_snapshot=event.tail_number,
                pass_number=1, position_number=1, truck_number_snapshot='UCC-831', pass_type='type_i',
                fluid_name_snapshot='Type I', concentration_percent_snapshot=50, started_at_utc=datetime(2026, 10, 23, 5, 10)))
            db.session.commit()

    def check_light_palette(self, board):
        backgrounds = {'.ucc-tv-ramp': '#ffffff', '.ucc-tv-weather': '#ffffff',
                       '.ucc-tv-weather article': '#f3f7fa', '.ucc-tv-plane:not(.is-in-throat)': '#e7eff5'}
        for selector, expected in backgrounds.items():
            expect(board.locator(selector).first).to_have_css('background-color', rgb(expected))
        expect(board).to_have_css('background-color', rgb('#f3f7fa'))
        expect(board).to_have_css('color', rgb('#122333'))
        text_colors = [
            ('.ucc-tv-ramp h2', '#12657d', '#ffffff'),
            ('.ucc-tv-temperature > strong', '#122333', '#ffffff'),
            ('.ucc-tv-observation dd', '#122333', '#ffffff'),
            ('.ucc-tv-weather .ucc-tv-source', '#405567', '#ffffff'),
            ('.ucc-tv-weather article .ucc-tv-muted', '#405567', '#f3f7fa'),
            ('.ucc-tv-plane:not(.is-in-throat) .ucc-tv-plane-route', '#405567', '#e7eff5'),
        ]
        for selector, expected, background in text_colors:
            expect(board.locator(selector).first).to_have_css('color', rgb(expected))
            self.assertGreaterEqual(contrast(expected, background), 4.5, selector)
        for foreground in ('#12657d', '#405567', '#a12a3c'):
            self.assertGreaterEqual(contrast(foreground, '#f3f7fa'), 4.5)
        icons = board.evaluate('''(wall, states) => {
            const probe = document.createElement('div');
            probe.style.position = 'absolute';
            probe.style.visibility = 'hidden';
            wall.append(probe);
            const colors = states.map(state => {
                const plane = document.createElement('div');
                plane.className = `ucc-tv-plane state-${state}`;
                const icon = document.createElement('span');
                icon.className = 'ucc-tv-plane-icon';
                plane.append(icon);
                probe.append(plane);
                return getComputedStyle(icon).backgroundColor;
            });
            probe.remove();
            return colors;
        }''', list(ICON_COLORS))
        self.assertEqual(icons, list(map(rgb, ICON_COLORS.values())))
        for color in ICON_COLORS.values():
            self.assertGreaterEqual(contrast(color, '#e7eff5'), 3)
        expect(board.locator('.ucc-tv-remote .ucc-tv-plane-icon')).to_have_css('background-color', rgb('#405567'))
        self.assertGreaterEqual(contrast('#405567', '#e7eff5'), 3)
        expect(board.locator('.ucc-tv-pad .state-deicing')).to_have_css('background-color', 'rgba(0, 0, 0, 0)')
        expect(board.locator('.ucc-tv-pad .state-deicing .ucc-tv-plane-icon')).to_have_css('background-color', rgb('#12657d'))

    def check_crew_colors(self, board, *, light):
        blue, green, red = ('#125f9d', '#1d6b3c', '#a12a3c') if light else ('#79c6ff', '#92dfa6', '#ff9c9c')
        remote = board.locator('.ucc-tv-remote')
        early = board.locator('.ucc-tv-early')
        unmarked = remote.locator('[data-ucc-tv-crew-field="driver"]')
        here = early.locator('[data-ucc-tv-crew-field="flyer"]')
        absent = early.locator('[data-ucc-tv-crew-field="driver"]')
        for field, text, color in (
            (remote.locator('[data-ucc-tv-crew-field="truck"]'), '1 · TRUCK 12', blue),
            (early.locator('[data-ucc-tv-crew-field="truck"]'), '1 · TRUCK UCC-831', red),
            (unmarked, 'D · T. Smith-Jones', green),
            (here, "F · T. O'Neil", green),
            (absent, 'D · C. De la Cruz', red),
        ):
            expect(field).to_have_text(text)
            expect(field).to_have_css('color', rgb(color))
            self.assertGreaterEqual(contrast(color, '#f3f7fa' if light else '#07121e'), 4.5)
        expect(absent).to_have_attribute('title', 'Christopher De la Cruz')
        expect(here).to_have_attribute('title', "Taylor O'Neil")
        sizes = early.locator('.ucc-tv-crew').evaluate("""crew => Array.from(crew.children, field => {
            const css = getComputedStyle(field);
            return [parseFloat(css.fontSize), Number(css.fontWeight), css.whiteSpace,
                    field.scrollWidth <= field.clientWidth + 1];
        })""")
        self.assertTrue(all(weight >= 700 and nowrap == 'nowrap' and fits for _, weight, nowrap, fits in sizes))
        self.assertTrue(all(size > sizes[0][0] for size, *_ in sizes[1:]))
        expect(board.locator('.ucc-tv-ramp').nth(1).locator('.ucc-tv-crew')).to_have_count(0)
        expect(remote.locator('[data-ucc-tv-crew-field="flyer"]')).to_have_count(0)

    def exercise(self, engine):
        case = fixtures.UccBrowserTest(); case.setUpClass(); browser = None
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        measurements = []
        try:
            self.seed_wallboard(case)
            sample = case.weather_data['future_forecast'][0]
            case.weather_data['future_forecast'] = tuple(
                dict(copy.deepcopy(sample), date_label=day)
                for day in ('TUE NOV 10', 'WED NOV 11', 'THU NOV 12'))
            case.weather_data['tv_forecast_extra'] = (dict(sample, date_label='MON NOV 16', hours=()),)
            browser = getattr(case.pw, engine).launch()
            page = browser.new_page(viewport={'width':1920, 'height':1080})
            errors = []; page.on('pageerror', lambda error: errors.append(str(error)))
            case.login(page); case.ready(page, '/neosubzero/ucc')
            expect(page.locator('[data-ucc-upcoming] article:visible')).to_have_count(4)
            expect(page.get_by_text('MON NOV 16', exact=True)).to_be_visible()
            expect(page.locator('.neosubzero-weather-hour-secondary > span:first-child').first).to_be_visible()
            page.evaluate('document.documentElement.requestFullscreen=undefined')
            draft = page.locator('[data-ucc-editor="Bravo-2"] select[name="person_id"]').first
            draft.evaluate('(field, person) => { field.value = person; field.dispatchEvent(new Event("input", {bubbles:true})); }', str(case.people[2]))
            page.locator('[data-ucc-tv-enter]').click()
            board = page.locator('.ucc-tv-wallboard')
            theme = board.get_by_role('button', name='Light appearance')
            exit_tv = board.get_by_role('button', name='Exit TV Mode')
            expect(board).to_be_visible()
            expect(board).to_have_attribute('data-ucc-tv-theme', 'dark')
            expect(theme).to_have_attribute('aria-pressed', 'false')
            expect(page.locator('[data-subzero-ucc]')).to_be_hidden()
            expect(board.locator('.ucc-tv-ramp > h2')).to_have_text(['Alpha', 'Bravo', 'Charlie', 'Delta', 'Echo'])
            expect(board.locator('.ucc-tv-hours article')).to_have_count(12)
            expect(board.locator('.ucc-tv-header > :visible')).to_have_text(['RFD DUCC · TV MODE', 'Light / Dark', 'Exit TV Mode'])
            expect(board.locator('.ucc-tv-remote .ucc-tv-plane-icon')).to_have_class('ucc-tv-plane-icon facing-up')
            expect(board.locator('.ucc-tv-early .ucc-person-unavailable')).to_have_text('D · C. De la Cruz')
            expect(board.locator('.ucc-tv-pad .state-deicing .ucc-tv-plane-icon')).to_have_class('ucc-tv-plane-icon facing-up')
            expect(board.locator('.ucc-tv-stands .ucc-tv-plane-icon.facing-right').first).to_be_visible()
            expect(board.locator('.ucc-tv-stands .ucc-tv-plane-icon.facing-left').first).to_be_visible()
            expect(board.locator('.ucc-tv-stands .ucc-tv-position').filter(has_text='Middle')).to_have_count(0)
            cards = board.locator('.ucc-tv-windows article')
            expect(cards).to_have_count(4)
            expect(cards.last).to_contain_text('MON NOV 16')
            expect(cards.last).to_contain_text('Forecast unavailable')
            for width, height in ((1920,1080), (1366,768), (1280,720), (3840,2160)):
                page.set_viewport_size({'width':width, 'height':height})
                measure = '''wall => {
                    const rect = node => node.getBoundingClientRect();
                    const ramps = [...wall.querySelectorAll('.ucc-tv-ramp')];
                    return {
                        width: wall.scrollWidth, height: wall.scrollHeight,
                        ramps: ramps.map(ramp => [rect(ramp).width, rect(ramp).height]),
                        sections: [...wall.querySelectorAll('.ucc-tv-weather, .ucc-tv-ramp, .ucc-tv-pad, .ucc-tv-footer')].map(node => {
                            const box = rect(node);
                            return [box.x, box.y, box.width, box.height];
                        }),
                        labels: ramps.map(ramp => {
                            const heading = ramp.querySelector("h2");
                            const range = document.createRange();
                            range.selectNodeContents(heading);
                            const label = range.getBoundingClientRect();
                            const column = rect(ramp);
                            const pad = rect(ramp.querySelector(".ucc-tv-pad"));
                            return {y:label.y, centered:Math.abs(label.x + label.width / 2
                                - column.x - column.width / 2) <= 1, above:label.bottom <= pad.top};
                        }),
                        ordered: ramps.every(ramp => {
                            const [left, pad, right] = ramp.querySelector('.ucc-tv-staging').children;
                            const stands = rect(ramp.querySelector('.ucc-tv-stands'));
                            return rect(left).right <= rect(pad).left && rect(pad).right <= rect(right).left
                                && Math.max(rect(left).bottom, rect(pad).bottom, rect(right).bottom) <= stands.top;
                        })
                    };
                }'''
                geometry = board.evaluate(measure)
                self.assertLessEqual(geometry['width'], width)
                self.assertLessEqual(geometry['height'], height)
                self.assertTrue(geometry['ordered'])
                self.assertTrue(all(label['centered'] and label['above'] for label in geometry['labels']))
                self.assertLessEqual(max(label['y'] for label in geometry['labels'])
                                     - min(label['y'] for label in geometry['labels']), 1)
                self.check_crew_colors(board, light=False)
                self.assertEqual(len({(round(w), round(h)) for w, h in geometry['ramps']}), 1)
                expect(board).to_have_css('background-color', rgb('#07121e'))
                expect(board.locator('.ucc-tv-ramp').first).to_have_css('background-color', rgb('#0a1927'))
                expect(board.locator('.ucc-tv-plane:not(.is-in-throat)').first).to_have_css('background-color', rgb('#0e2133'))
                page.screenshot(path=str(EVIDENCE/f'{engine}-{width}-dark.png'))
                theme.click()
                expect(board).to_have_attribute('data-ucc-tv-theme', 'light')
                expect(theme).to_have_attribute('aria-pressed', 'true')
                self.assertEqual(board.evaluate(measure), geometry)
                self.check_light_palette(board)
                self.check_crew_colors(board, light=True)
                page.screenshot(path=str(EVIDENCE/f'{engine}-{width}-light.png'))
                theme.click()
                expect(board).to_have_attribute('data-ucc-tv-theme', 'dark')
                for card in cards.all()[:3]:
                    expect(card).to_be_visible()
                    for key in ('temperature_range', 'precipitation_probability', 'conditions', 'wind'):
                        expect(card).to_contain_text(sample[key])
                measurements.append(dict(engine=engine, **geometry))
            theme.click()
            expect(board).to_have_attribute('data-ucc-tv-theme', 'light')
            case.weather_data['tv_forecast_extra'] = (dict(sample, date_label='MON NOV 16'),)
            with case.app.app_context():
                StaffingDailyAttendance.query.filter_by(person_id=case.people[0]).one().status = 'here'
                set_neosubzero_ucc_assignment(db.session.get(SortDateOperation, case.operation_id), 'Bravo', 2,
                                             'driver', db.session.get(StaffingPerson, case.people[1]))
                db.session.commit()
            expect(board.locator('.ucc-tv-ramp').filter(has=page.get_by_text('Bravo', exact=True))).to_contain_text("T. O'Neil", timeout=10000)
            expect(board.locator('.ucc-tv-early [data-ucc-tv-crew-field="driver"]')).to_have_css('color', rgb('#1d6b3c'))
            expect(cards.last).to_contain_text(sample['temperature_range'])
            expect(board).to_have_attribute('data-ucc-tv-theme', 'light')
            expect(draft).to_have_value(str(case.people[2]))
            exit_tv.click()
            expect(theme).to_have_count(0)
            page.locator('[data-ucc-tv-enter]').click()
            expect(board).to_have_attribute('data-ucc-tv-theme', 'light')
            page.reload()
            expect(board).to_have_attribute('data-ucc-tv-theme', 'light')
            page.evaluate('document.documentElement.requestFullscreen=undefined')
            exit_tv.click()
            expect(page.locator('[data-subzero-ucc]')).to_be_visible()
            page.set_viewport_size({'width':390, 'height':844})
            page.locator('[data-ucc-view="ramp"]').click()
            expect(theme).to_have_count(0)
            expect(page.locator('[data-subzero-ucc]')).not_to_have_attribute('data-ucc-tv-theme', re.compile('.+'))

            page.add_init_script('''(() => {
                const getItem = Storage.prototype.getItem;
                const setItem = Storage.prototype.setItem;
                Storage.prototype.getItem = function (key) {
                    if (key === 'neoapps.neosubzero.ucc.tv-theme') throw new Error('Storage unavailable');
                    return getItem.call(this, key);
                };
                Storage.prototype.setItem = function (key, value) {
                    if (key === 'neoapps.neosubzero.ucc.tv-theme') throw new Error('Storage unavailable');
                    return setItem.call(this, key, value);
                };
                Element.prototype.requestFullscreen = undefined;
            })();''')
            page.set_viewport_size({'width':1280, 'height':720})
            page.reload()
            page.locator('[data-ucc-tv-enter]').click()
            expect(board).to_have_attribute('data-ucc-tv-theme', 'dark')
            theme.click()
            expect(board).to_have_attribute('data-ucc-tv-theme', 'light')
            exit_tv.click()
            page.locator('[data-ucc-tv-enter]').click()
            expect(board).to_have_attribute('data-ucc-tv-theme', 'light')
            self.assertFalse(errors, errors)
        finally:
            (EVIDENCE/f'{engine}-measurements.json').write_text(json.dumps(measurements,indent=2))
            if browser: browser.close()
            case.tearDownClass()

    def test_chromium_upcoming_weather_remains_visible(self): self.exercise('chromium')
    def test_webkit_upcoming_weather_remains_visible(self): self.exercise('webkit')
