"""Focused DUCC TV fit, physical truck order and normal/mobile preservation."""
import json
import re
import unittest
from pathlib import Path

from playwright.sync_api import expect
from app.extensions import db
from app.models import SortDateMission, NeoSubZeroDepartureDeiceEvent
from app.services.neosubzero_evidence import evidence_values
from tests.browser import test_neosubzero_ucc as fixtures


EVIDENCE = Path('instance/ducc-tv-legibility-20261105')


class DuccTvLegibilityTest(unittest.TestCase):
    def truck_order(self, page):
        for ramp in page.locator('[data-ucc-ramp]:visible').all():
            positions = ramp.locator('.ucc-team > summary').evaluate_all(r'''els => Object.fromEntries(els.map(e => {
                const r=e.getBoundingClientRect();return [e.parentElement.className.match(/position-(\d)/)[1], {x:r.x,y:r.y}];
            }))''')
            self.assertLess(positions['1']['x'], positions['4']['x'])
            self.assertLess(positions['2']['x'], positions['3']['x'])
            self.assertAlmostEqual(positions['1']['y'], positions['4']['y'], delta=1)
            self.assertAlmostEqual(positions['2']['y'], positions['3']['y'], delta=1)
            self.assertLess(positions['1']['y'], positions['2']['y'])

    def exercise(self, engine):
        case = fixtures.UccBrowserTest()
        case.setUpClass()
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        browser = None
        measurements = []
        try:
            with case.app.app_context():
                before = [evidence_values(e) for e in NeoSubZeroDepartureDeiceEvent.query.order_by(NeoSubZeroDepartureDeiceEvent.id)]
            browser = getattr(case.pw, engine).launch()
            page = browser.new_page(viewport={'width':1920,'height':1080}, service_workers='block')
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            case.login(page)
            case.ready(page, '/neosubzero/ucc')
            self.truck_order(page)
            expect(page.locator('[data-ucc-ramp="Remote"]')).to_be_visible()
            for width, height in ((1920,1080),(1280,720),(1440,900),(2560,1440),(3840,2160)):
                page.set_viewport_size({'width':width,'height':height})
                page.locator('[data-ucc-tv-enter]').click()
                expect(page.locator('body')).to_have_class(re.compile('neosubzero-ucc-tv-mode'))
                page.wait_for_function('''() => {
                    const root=document.querySelector('[data-subzero-ucc]').getBoundingClientRect();
                    const ramps=[...document.querySelectorAll('[data-ucc-ramp]:not([data-ucc-ramp="Remote"])')];
                    const bottom=Math.max(...ramps.map(e=>e.getBoundingClientRect().bottom));
                    return root.width>=innerWidth*.98 && root.right<=innerWidth && root.bottom<=innerHeight && bottom>=innerHeight-16;
                }''')
                expect(page.locator('[data-ucc-ramp]:visible')).to_have_count(5)
                expect(page.locator('[data-ucc-ramp="Remote"]')).to_be_hidden()
                self.truck_order(page)
                geometry = page.evaluate('''() => {
                    const root=document.querySelector('[data-subzero-ucc]'),box=root.getBoundingClientRect(),scale=box.width/root.offsetWidth;
                    const rect=e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,bottom:r.bottom};};
                    const font=s=>parseFloat(getComputedStyle(root.querySelector(s)).fontSize)*scale;
                    return {width:innerWidth,height:innerHeight,root:rect(root),
                        ramps:[...root.querySelectorAll('[data-ucc-ramp]:not([data-ucc-ramp="Remote"])')].map(e=>({rect:rect(e),border:getComputedStyle(e).borderLeftWidth})),
                        fonts:{temperature:font('.neosubzero-weather-primary strong'),values:font('.neosubzero-weather-current dd'),hour:font('.neosubzero-weather-hour-priority b'),upcoming:font('[data-ucc-upcoming] b')},
                        overflowX:document.documentElement.scrollWidth-innerWidth,overflowY:document.documentElement.scrollHeight-innerHeight};
                }''')
                measurements.append(geometry)
                self.assertLessEqual(geometry['overflowX'],1)
                self.assertLessEqual(geometry['overflowY'],1)
                for dimension in ('y','width','height'):
                    values = [r['rect'][dimension] for r in geometry['ramps']]
                    self.assertLessEqual(max(values)-min(values),1)
                self.assertTrue(all(float(r['border'][:-2])>=2 for r in geometry['ramps']))
                self.assertGreaterEqual(geometry['fonts']['temperature'],34)
                self.assertGreaterEqual(geometry['fonts']['values'],13)
                self.assertGreaterEqual(geometry['fonts']['hour'],12)
                self.assertGreaterEqual(geometry['fonts']['upcoming'],10)
                for hour in page.locator('.neosubzero-weather-current-sort > article').all():
                    self.assertTrue(hour.evaluate('e=>e.scrollWidth<=e.clientWidth+1'))
                for plane in page.locator('.ucc-ramp:visible .ucc-plane').all():
                    expect(plane).to_have_css('background-color','rgb(9, 23, 33)')
                    expect(plane.locator('.ucc-plane-status')).to_be_hidden()
                page.screenshot(path=str(EVIDENCE/f'{engine}-tv-{width}.png'))
                page.locator('[data-ucc-tv-exit]').click()
                page.wait_for_function('!document.fullscreenElement')
            # Existing live refresh re-fits the same five ramps, without reviving
            # Remote or consuming a staffing draft in the background.
            page.set_viewport_size({'width':1920,'height':1080})
            expect(page.locator('[data-ucc-ramp="Remote"]')).to_be_visible()
            editor = page.locator('[data-ucc-editor="Alpha-1"]')
            editor.locator(':scope > summary').click()
            draft = editor.locator('[name="truck_number"]')
            draft.fill('PRESERVED TV DRAFT')
            editor.locator(':scope > summary').click()
            page.locator('[data-ucc-tv-enter]').click()
            with case.app.app_context():
                mission = db.session.get(SortDateMission,case.missions[0])
                mission.flight_number = 'UPS-TV-LIVE'
                db.session.commit()
            expect(page.locator('[data-ucc-aircraft]').filter(has_text='UPS-TV-LIVE')).to_be_visible(timeout=15000)
            expect(draft).to_have_value('PRESERVED TV DRAFT')
            expect(page.locator('[data-ucc-ramp]:visible')).to_have_count(5)
            page.locator('[data-ucc-tv-exit]').click()
            expect(page.locator('[data-operational-topbar]')).to_be_visible()
            expect(page.locator('[data-ucc-ramp="Remote"]')).to_be_visible()
            for width in (320,390,430):
                page.set_viewport_size({'width':width,'height':844})
                self.truck_order(page)
                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth-innerWidth'),1)
            page.locator('[data-ucc-view="ramp"]').click()
            page.locator('[data-ucc-ramp-select="Remote"]').click()
            expect(page.locator('[data-ucc-ramp="Remote"]')).to_be_visible()
            expect(page.locator('.neosubzero-weather')).to_be_hidden()
            with case.app.app_context():
                self.assertEqual(before,[evidence_values(e) for e in NeoSubZeroDepartureDeiceEvent.query.order_by(NeoSubZeroDepartureDeiceEvent.id)])
            self.assertFalse(errors,errors)
        finally:
            (EVIDENCE/f'{engine}-measurements.json').write_text(json.dumps(measurements,indent=2))
            if browser:
                browser.close()
            case.tearDownClass()

    def test_chromium_tv_legibility(self):
        self.exercise('chromium')

    def test_webkit_tv_legibility(self):
        self.exercise('webkit')
