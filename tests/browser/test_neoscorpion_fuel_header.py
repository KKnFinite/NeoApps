"""Shared Fueler header geometry on real, isolated Fueler/Dispatch pages."""
from datetime import timedelta
import json
import os
from pathlib import Path
from threading import Thread
import unittest

from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

from app.extensions import db
from tests import test_neoscorpion_direct_entry as fixtures


EVIDENCE = Path('instance/fuel-header')
WIDTHS = (320, 375, 390, 430, 768, 1024, 1440)


class FuelHeaderBrowserTest(unittest.TestCase):
    def exercise(self, engine):
        case = fixtures.DirectEntryTest()
        case.setUp()
        app = case.client.application
        app.config.update(SESSION_COOKIE_SECURE=False, LIVE_SCREEN_REFRESH_INTERVAL_MS=0,
                          GOOGLE_MOTHERBRAIN_IMPORT_ENABLED=False,
                          GOOGLE_MOTHERBRAIN_READER_ENABLED=False,
                          FLIGHT_API_ENABLED=False)
        case.mission.flight_number = 'UPS0910'
        case.mission.origin = 'SDF'  # Must never become the displayed gateway origin.
        case.mission.destination = 'LAX'
        case.prepare()
        case.save(case.start + timedelta(minutes=1), **case.final_data())
        aid = case.assignment.id
        tail = case.mission.assigned_tail_number
        fresh = case.fixture._mission('UPS0911', 'N492UP', 25000, 1)
        fresh.destination = 'LAX'
        fresh.origin = 'SDF'
        fresh_id = fresh.id
        db.session.commit()
        server = make_server('127.0.0.1', 0, app, threaded=True)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = f'http://127.0.0.1:{server.server_port}'
        phase = os.environ.get('FUEL_HEADER_PHASE', 'after')
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        metrics, errors = [], []
        try:
            with sync_playwright() as pw:
                browser = getattr(pw, engine).launch()
                try:
                    page = browser.new_page(viewport={'width':1440, 'height':1000}, service_workers='block')
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    def login(user):
                        page.context.clear_cookies()
                        page.goto(origin + '/login')
                        page.locator('[name=email]').fill(user.email)
                        page.locator('[name=password]').fill('TestPassword123!')
                        page.locator('button[type=submit]').click()
                        page.wait_for_url(lambda url: '/login' not in url)
                        page.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1','seen')")

                    def check(mode, expected_tail, flight):
                        card = page.locator(f'[data-screen-mode={mode}]').first
                        expect(card).to_be_visible()
                        for width in ((390, 1440) if phase == 'before' else WIDTHS):
                            page.set_viewport_size({'width':width, 'height':1000 if width > 700 else 844})
                            page.evaluate('document.fonts.ready')
                            if phase == 'before':
                                page.screenshot(path=str(EVIDENCE / f'{engine}-{mode}-before-{width}.png'))
                                continue
                            line = card.locator('[data-fuel-header-line]')
                            expect(line.locator('[data-fuel-header-value]')).to_have_text([expected_tail, flight, 'RFD', 'LAX'])
                            labels = card.locator('.scorpion-fueler-identity dt').all_text_contents()
                            self.assertEqual(labels, ['ETD','Parking','Aircraft','Required Fuel','Est. Gal'] if mode == 'dispatcher_direct_entry'
                                             else ['Fueler','ETD','Parking','Truck','ON','Cycle','Aircraft','Required Fuel','Est. Gal'])
                            self.assertEqual(card.locator('.scorpion-fueler-etd,.scorpion-fueler-flight').count(), 0)
                            self.assertFalse(card.evaluate('e=>e.scrollWidth>e.clientWidth+1'))
                            page.wait_for_function("""() => [...document.querySelectorAll('[data-fuel-header]')].filter(e=>e.clientWidth).every(e=>
                                e.querySelector('[data-fuel-header-line]').getBoundingClientRect().width <= e.clientWidth + 1)""")
                            def measure(kind):
                                result = line.evaluate("""e=>{const r=e.getBoundingClientRect(), values=[...e.querySelectorAll('[data-fuel-header-value]')].map(v=>{
                                    const b=v.getBoundingClientRect();return {x:b.x,y:b.y,right:b.right,bottom:b.bottom,height:b.height};});
                                    return {width:r.width,height:r.height,font:parseFloat(getComputedStyle(e).fontSize),values,
                                    available:e.parentElement.clientWidth,pageWidth:document.documentElement.scrollWidth};}""")
                                self.assertLessEqual(result['width'], result['available'] + 1)
                                self.assertLessEqual(result['pageWidth'], width + 1)
                                self.assertLess(max(v['y'] for v in result['values']) - min(v['y'] for v in result['values']), 1)
                                self.assertTrue(all(a['right'] < b['x'] for a,b in zip(result['values'], result['values'][1:])))
                                metrics.append(dict(engine=engine,mode=mode,viewport=width,kind=kind,**result))
                            measure('normal')
                            if width in (390, 1440):
                                page.screenshot(path=str(EVIDENCE / f'{engine}-{mode}-after-{width}.png'))
                            # Layout stress: complete, realistic longer identifiers, with no clipping or ellipsis.
                            line.locator('[data-fuel-header-value]').evaluate_all("""els=>els.forEach((e,i)=>e.textContent=['RA-12345','UPS12345','RJAA','ZBAA'][i])""")
                            page.wait_for_function("""() => [...document.querySelectorAll('[data-fuel-header-line]')].filter(e=>e.clientWidth).every(e=>
                                e.getBoundingClientRect().width <= e.parentElement.clientWidth + 1)""")
                            measure('long')
                            line.locator('[data-fuel-header-value]').evaluate_all("""(els,values)=>els.forEach((e,i)=>e.textContent=values[i])""", [expected_tail,flight,'RFD','LAX'])

                    login(case.fueler)
                    page.goto(origin + '/neoscorpion/fueler')
                    check('fueler', tail, 'UPS0910')
                    # Resizing/refitting must preserve unsaved operational drafts and selection.
                    if phase == 'after':
                        notes = page.locator('[name=notes]').first
                        notes.fill('Operator draft retained')
                        page.set_viewport_size({'width':375,'height':844})
                        expect(notes).to_have_value('Operator draft retained')
                    login(case.dispatcher)
                    page.goto(origin + '/neoscorpion/fuel-dispatch')
                    def open_card(url):
                        with page.expect_response(lambda response: response.url == origin + url) as received:
                            page.evaluate("""url=>{const b=document.createElement('button');b.dataset.fuelDataOpen=url;
                                document.body.append(b);b.click();b.remove();}""", url)
                        self.assertEqual(received.value.status, 200)
                        expect(page.locator('[data-fuel-data-dialog]')).to_be_visible()
                        expect(page.locator('[data-fuel-data-dialog] [data-fuel-data-card]')).to_be_visible()
                    open_card(f'/neoscorpion/fuel-dispatch/fueler-data/{aid}')
                    check('dispatcher_open', tail, 'UPS0910')
                    page.locator('[data-fuel-data-close]').click()
                    page.goto(origin + '/neoscorpion/fuel-dispatch')
                    open_card(f'/neoscorpion/fuel-dispatch/missions/{fresh_id}/direct-entry')
                    check('dispatcher_direct_entry', 'N492UP', 'UPS0911')
                    if phase == 'after':
                        card = page.locator('[data-screen-mode=dispatcher_direct_entry]')
                        card.locator('[name=actual_left]').fill('14')
                        card.locator('[name=actual_ctr]').fill('0')
                        card.locator('[name=actual_right]').fill('14')
                        expect(card.locator('[data-actual-total]')).to_have_text('28.0')
                        expect(card.locator('[data-direct-entry-complete]')).to_be_enabled()
                        # Dynamic replacement, as used when reopening a shared dialog.
                        page.locator('[data-fuel-data-close]').click()
                        page.goto(origin + '/neoscorpion/fuel-dispatch')
                        open_card(f'/neoscorpion/fuel-dispatch/fueler-data/{aid}')
                        check('dispatcher_open', tail, 'UPS0910')
                    self.assertFalse(errors, errors)
                finally:
                    browser.close()
        finally:
            (EVIDENCE / f'{engine}-{phase}-metrics.json').write_text(json.dumps(metrics,indent=2))
            server.shutdown()
            thread.join(timeout=5)
            case.doCleanups()

    def test_chromium(self): self.exercise('chromium')
    def test_webkit(self): self.exercise('webkit')
