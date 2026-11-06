"""TV fitting must retain upcoming-sort forecast values, not only window labels."""
import copy
import json
import unittest
from pathlib import Path

from playwright.sync_api import expect
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
                for day in ('MON NOV 09', 'TUE NOV 10', 'WED NOV 11'))
            browser = getattr(case.pw, engine).launch()
            page = browser.new_page(viewport={'width':1920, 'height':1080})
            errors = []; page.on('pageerror', lambda error: errors.append(str(error)))
            case.login(page); case.ready(page, '/neosubzero/ucc')
            page.evaluate('document.documentElement.requestFullscreen=undefined')
            page.locator('[data-ucc-tv-enter]').click()
            for width, height in ((1920,1080), (1366,768), (2560,1440)):
                page.set_viewport_size({'width':width, 'height':height})
                page.wait_for_function('(()=>{const r=document.querySelector("[data-subzero-ucc]").getBoundingClientRect();return r.right<=innerWidth+1&&r.bottom<=innerHeight+1})()')
                cards = page.locator('[data-ucc-upcoming] article')
                expect(cards).to_have_count(3)
                page.screenshot(path=str(EVIDENCE/f'{engine}-{width}.png'))
                for i in range(3):
                    card = cards.nth(i)
                    for selector, value in (('span:nth-child(2)', sample['temperature_range']),
                                            ('span:nth-child(5)', sample['precipitation_probability']),
                                            ('em', sample['conditions'])):
                        expect(card.locator(':scope > '+selector)).to_be_visible()
                        expect(card.locator(':scope > '+selector)).to_contain_text(value)
                    expect(card.locator(':scope > em')).to_contain_text(sample['wind'])
                measurements.append(dict(engine=engine,width=width,height=height,
                    density=page.locator('[data-subzero-ucc]').get_attribute('data-ucc-weather-density')))
            # A density change or a refreshed forecast must not hide its values.
            for density in ('normal','compact','minimal'):
                page.evaluate('(value)=>{document.querySelector("[data-subzero-ucc]").dataset.uccWeatherDensity=value}',density)
                for selector in ('span:nth-child(2)','span:nth-child(5)','em'):
                    expect(cards.first.locator(':scope > '+selector)).to_be_visible()
            self.assertFalse(errors, errors)
        finally:
            (EVIDENCE/f'{engine}-measurements.json').write_text(json.dumps(measurements,indent=2))
            if browser: browser.close()
            case.tearDownClass()

    def test_chromium_upcoming_weather_remains_visible(self): self.exercise('chromium')
    def test_webkit_upcoming_weather_remains_visible(self): self.exercise('webkit')
