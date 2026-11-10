"""The DUCC wallboard preserves weather, physical geometry and draft-safe refresh."""
import copy
import json
import unittest
from pathlib import Path

from playwright.sync_api import expect
from app.extensions import db
from app.models import SortDateOperation, StaffingPerson
from app.services.neosubzero_ucc import set_neosubzero_ucc_assignment
from tests.browser import test_neosubzero_ucc as fixtures

EVIDENCE = Path('instance/ducc-weather/browser')


class DuccTvWeatherTest(unittest.TestCase):
    def exercise(self, engine):
        case = fixtures.UccBrowserTest(); case.setUpClass(); browser = None
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        measurements = []
        try:
            sample = case.weather_data['future_forecast'][0]
            case.weather_data['future_forecast'] = tuple(
                dict(copy.deepcopy(sample), date_label=day)
                for day in ('TUE NOV 10', 'WED NOV 11', 'THU NOV 12'))
            case.weather_data['tv_forecast_extra'] = (dict(sample, date_label='MON NOV 16', hours=()),)
            browser = getattr(case.pw, engine).launch()
            page = browser.new_page(viewport={'width':1920, 'height':1080})
            errors = []; page.on('pageerror', lambda error: errors.append(str(error)))
            case.login(page); case.ready(page, '/neosubzero/ucc')
            expect(page.locator('[data-ucc-upcoming] article')).to_have_count(3)
            expect(page.get_by_text('MON NOV 16', exact=True)).to_have_count(0)
            page.evaluate('document.documentElement.requestFullscreen=undefined')
            draft = page.locator('[data-ucc-editor="Bravo-2"] select[name="person_id"]').first
            draft.evaluate('(field, person) => { field.value = person; field.dispatchEvent(new Event("input", {bubbles:true})); }', str(case.people[2]))
            page.locator('[data-ucc-tv-enter]').click()
            board = page.locator('.ucc-tv-wallboard')
            expect(board).to_be_visible()
            expect(page.locator('[data-subzero-ucc]')).to_be_hidden()
            expect(board.locator('.ucc-tv-ramp > h2')).to_have_text(['Alpha', 'Bravo', 'Charlie', 'Delta', 'Echo'])
            expect(board.locator('.ucc-tv-hours article')).to_have_count(12)
            expect(board.locator('button')).to_have_count(1)
            expect(board.locator('.ucc-tv-stands .ucc-tv-position').filter(has_text='Middle')).to_have_count(0)
            cards = board.locator('.ucc-tv-windows article')
            expect(cards).to_have_count(4)
            expect(cards.last).to_contain_text('MON NOV 16')
            expect(cards.last).to_contain_text('Forecast unavailable')
            for width, height in ((1920,1080), (1280,720), (3840,2160)):
                page.set_viewport_size({'width':width, 'height':height})
                geometry = board.evaluate('''wall => {
                    const rect = node => node.getBoundingClientRect();
                    const ramps = [...wall.querySelectorAll('.ucc-tv-ramp')];
                    return {
                        width: wall.scrollWidth, height: wall.scrollHeight,
                        ramps: ramps.map(ramp => [rect(ramp).width, rect(ramp).height]),
                        ordered: ramps.every(ramp => {
                            const [left, pad, right] = ramp.querySelector('.ucc-tv-staging').children;
                            const stands = rect(ramp.querySelector('.ucc-tv-stands'));
                            return rect(left).right <= rect(pad).left && rect(pad).right <= rect(right).left
                                && Math.max(rect(left).bottom, rect(pad).bottom, rect(right).bottom) <= stands.top;
                        })
                    };
                }''')
                self.assertLessEqual(geometry['width'], width)
                self.assertLessEqual(geometry['height'], height)
                self.assertTrue(geometry['ordered'])
                self.assertEqual(len({(round(w), round(h)) for w, h in geometry['ramps']}), 1)
                page.screenshot(path=str(EVIDENCE/f'{engine}-{width}.png'))
                for card in cards.all()[:3]:
                    expect(card).to_be_visible()
                    for key in ('temperature_range', 'precipitation_probability', 'conditions', 'wind'):
                        expect(card).to_contain_text(sample[key])
                measurements.append(dict(engine=engine, **geometry))
            case.weather_data['tv_forecast_extra'] = (dict(sample, date_label='MON NOV 16'),)
            with case.app.app_context():
                set_neosubzero_ucc_assignment(db.session.get(SortDateOperation, case.operation_id), 'Bravo', 2,
                                             'driver', db.session.get(StaffingPerson, case.people[1]))
                db.session.commit()
            expect(board.locator('.ucc-tv-ramp').filter(has=page.get_by_text('Bravo', exact=True))).to_contain_text('Longname-Deicer-1', timeout=10000)
            expect(cards.last).to_contain_text(sample['temperature_range'])
            expect(draft).to_have_value(str(case.people[2]))
            board.get_by_role('button', name='Exit TV Mode').click()
            expect(page.locator('[data-subzero-ucc]')).to_be_visible()
            self.assertFalse(errors, errors)
        finally:
            (EVIDENCE/f'{engine}-measurements.json').write_text(json.dumps(measurements,indent=2))
            if browser: browser.close()
            case.tearDownClass()

    def test_chromium_upcoming_weather_remains_visible(self): self.exercise('chromium')
    def test_webkit_upcoming_weather_remains_visible(self): self.exercise('webkit')
