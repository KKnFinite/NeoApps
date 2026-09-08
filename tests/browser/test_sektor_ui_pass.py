"""Focused current Sektor layout/state smoke checks with isolated synthetic data."""
import json
import subprocess
import unittest
from pathlib import Path
from tests.browser.test_mobile_drawer import MobileDrawerBrowserTest as Fixture
from app.extensions import db
from app.models import NeoSektorOperationalSetting, NeoErmacUldRequest
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.neosektor_live_counts import apply_standalone_compat_values
from tests.test_neosektor_integration_modes import _complete_sheet_values


class SektorUIPassTest(unittest.TestCase):
    def test_driver_text_and_priority_visibility(self):
        """Check painted text bounds, not merely a scroll-free document."""
        Fixture.setUpClass()
        evidence = Path('instance/browser-evidence/sektor-driver-correction').resolve()
        evidence.mkdir(parents=True, exist_ok=True)
        try:
            with Fixture.app.app_context():
                gateway = ensure_default_gateway_and_nodes()
                db.session.add(NeoSektorOperationalSetting(gateway_id=gateway.id,
                    gateway_code=gateway.code, integration_mode='neo_only'))
                apply_standalone_compat_values(gateway, _complete_sheet_values())
                db.session.commit()
            browser = Fixture.pw.chromium.launch()
            page = browser.new_page(viewport={'width':390, 'height':844})
            Fixture().login(page)
            for width, height, tv in [(390,844,False),(1920,1080,True),(390,844,True)]:
                with self.subTest(width=width, tv=tv):
                    page.set_viewport_size({'width':width, 'height':height})
                    Fixture().ready(page, '/neosektor/driver-routing' + ('?tv=1' if tv else ''))
                    name = f'driver-{"tv" if tv else "normal"}-{width}x{height}'
                    page.screenshot(path=str(evidence / f'{name}.png'), full_page=True)
                    self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), width+1)
                    if tv:
                        self.assertLessEqual(page.evaluate('document.documentElement.scrollHeight'), height+1)
                        self.assertEqual(page.locator('.app-header:visible,.operational-mobile-header:visible,.topbar:visible,.live-update-controls:visible,.neo-mobile-bottom:visible').count(),0)
                        self.assertTrue(page.locator('.sektor-tv-exit').is_visible())
                    else:
                        self.assertFalse(page.locator('[data-driver-routing] .header-title').is_visible())
                        self.assertTrue(page.locator('.neosektor-driver-back').is_visible())
                    cards = page.locator('[data-driver-priority-index]:visible')
                    self.assertEqual(cards.count(), 3)
                    for index in range(cards.count()):
                        card = cards.nth(index)
                        self.assertEqual(card.locator('[data-driver-rank]').inner_text(), ['1ST','2ND','3RD'][index])
                        number = card.locator('[data-driver-bay-name]')
                        self.assertRegex(number.inner_text(), r'^[1-5]$')
                        self.assertEqual(number.evaluate('e=>getComputedStyle(e).color'), 'rgb(240, 246, 250)')
                        self.assertEqual(number.evaluate('e=>getComputedStyle(e).webkitTextFillColor'), 'rgb(240, 246, 250)')
                    # Range rectangles detect text overflowing even when an ancestor clips it.
                    bounds = page.locator('[data-driver-bay-name],.driver-priority-rank,.driver-target-node,.driver-instruction,.driver-wave-message').evaluate_all('''es => es.filter(e=>e.getClientRects().length).map(e=>{
                        const range=document.createRange(); range.selectNodeContents(e);
                        const r=range.getBoundingClientRect();
                        const p=e.closest('[data-driver-wave],[data-driver-priority-index]').getBoundingClientRect();
                        const s=getComputedStyle(e);
                        return {text:e.textContent.trim(), visible:s.visibility==='visible' && +s.opacity>0,
                            fits:r.width>0 && r.height>0 && r.left>=p.left && r.right<=p.right && r.top>=p.top && r.bottom<=p.bottom};
                    })''')
                    self.assertTrue(bounds)
                    for result in bounds:
                        self.assertTrue(result['visible'] and result['fits'], result)
            page.set_viewport_size({'width':390, 'height':844})
            Fixture().ready(page, '/neosektor/live-counts')
            colors = page.locator('#neosektor-live-counts-panel .readonly-count').evaluate_all('es=>es.map(e=>getComputedStyle(e).color)')
            self.assertEqual(colors, ['rgb(239, 53, 71)'] * 6)
            browser.close()
        finally:
            Fixture.tearDownClass()

    def test_focused_screens(self):
        Fixture.setUpClass()
        evidence = Path('instance/browser-evidence/sektor-ui-pass').resolve()
        evidence.mkdir(parents=True, exist_ok=True)
        results = []
        try:
            with Fixture.app.app_context():
                gateway = ensure_default_gateway_and_nodes()
                db.session.add(NeoSektorOperationalSetting(gateway_id=gateway.id,
                    gateway_code=gateway.code, integration_mode='neo_only'))
                apply_standalone_compat_values(gateway, _complete_sheet_values())
                db.session.add(NeoErmacUldRequest(gateway_id=gateway.id, door='D34',
                    a2_count=3, a1_count=2, amp_count=1, setup_needed=True))
                db.session.commit()
            browser = Fixture.pw.chromium.launch()
            page = browser.new_page(viewport={'width':390,'height':844})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            Fixture().login(page)

            def capture(name, no_scroll=False):
                scripts = '\n'.join(page.locator('script:not([src])').all_text_contents())
                syntax = subprocess.run(['node', '--check'], input=scripts, text=True, capture_output=True)
                self.assertEqual(syntax.returncode, 0, syntax.stderr)
                result = page.evaluate('''() => ({height:document.documentElement.scrollHeight,
                    width:document.documentElement.scrollWidth, vh:innerHeight, vw:innerWidth,
                    dock:document.querySelector('.neo-mobile-bottom')?.getBoundingClientRect().bottom,
                    statusTop:document.querySelector('.live-update-controls')?.getBoundingClientRect().top,
                    headerBottom:document.querySelector('.operational-mobile-header')?.getBoundingClientRect().bottom})''')
                results.append(dict(name=name, **result))
                page.screenshot(path=str(evidence / f'{name}.png'), full_page=True)
                (evidence / 'geometry.json').write_text(json.dumps(results, indent=2))
                self.assertLessEqual(result['width'], result['vw']+1, result)
                if no_scroll:
                    self.assertLessEqual(result['height'], result['vh']+1, result)
                    self.assertAlmostEqual(result['dock'], result['vh'], delta=1)

            for side in ['ebm','wbm']:
                Fixture().ready(page, '/neosektor/'+side)
                for mode in ([1,2] if side=='ebm' else [1]):
                    if mode == 2:
                        page.locator('[data-bm-mode="2"]').click()
                        page.wait_for_function('document.querySelector("[data-ballmat-mobile]").dataset.mode === "2"')
                    alignment = page.evaluate('''() => {
                        const y=e=>{const r=e.getBoundingClientRect();return r.y+r.height/2;};
                        return {local:[...document.querySelectorAll('.bm-local h2,.bm-count-row')].map(y),
                            other:[...document.querySelectorAll('.bm-other h2,.bm-other > div')].map(y)};
                    }''')
                    for a,b in zip(alignment['local'], alignment['other']):
                        self.assertAlmostEqual(a,b,delta=1,msg=alignment)
                    capture(f'{side}-{mode}-390x844', True)

            Fixture().ready(page, '/neosektor/live-counts')
            capture('live-counts-390x844', True)
            self.assertGreaterEqual(results[-1]['statusTop'], results[-1]['headerBottom'])
            for index in range(3):
                east=page.locator('[data-live-side="east"] .counter-card').nth(index).bounding_box()
                west=page.locator('[data-live-side="west"] .counter-card').nth(index).bounding_box()
                self.assertAlmostEqual(east['y'],west['y'],delta=1)
            self.assertEqual(page.locator('[data-live-bay]').evaluate_all('es=>es.map(e=>e.dataset.liveBay)'),['Bay 1','Bay 2','Bay 3','Bay 4','Bay 5'])
            self.assertEqual(page.locator('[data-live-route]').count(),2)
            for text in page.locator('[data-live-route]').all_text_contents():
                self.assertIn(text, ['-', 'NOT ARRIVED', 'ROUTING EAST', 'ROUTING WEST'])
            self.assertEqual(page.locator('#neosektor-live-counts-panel input').count(),0)

            Fixture().ready(page, '/neosektor/discharge')
            capture('discharge-390x844')
            self.assertGreaterEqual(results[-1]['statusTop'], results[-1]['headerBottom'])
            self.assertEqual(page.locator('.neosektor-discharge-row').count(),1)
            page.locator('.neosektor-discharge-row').click()
            page.locator('button[type="submit"]').filter(has_text='SEND ON THE WAY').wait_for()
            self.assertEqual(page.locator('.neosektor-discharge-send-panel input[type="number"]').count(),3)
            capture('discharge-detail-390x844')
            page.set_viewport_size({'width':1920,'height':1080})
            capture('discharge-desktop')
            Fixture().ready(page, '/neosektor/driver-routing')
            self.assertTrue(page.locator('.sektor-tv-entry').is_visible())
            self.assertEqual(page.locator('[data-driver-wave]').count(),2)
            capture('driver-normal-desktop')
            page.locator('.sektor-tv-entry').click()
            page.wait_for_url('**tv=1')
            page.reload()
            page.locator('.sektor-tv-exit').wait_for()
            capture('driver-tv-1920x1080')
            self.assertLessEqual(page.evaluate('document.documentElement.scrollHeight'),1081)
            self.assertEqual(page.locator('.app-header:visible,.operational-mobile-header:visible,.topbar:visible,.live-update-controls:visible,.neo-mobile-bottom:visible').count(),0)
            self.assertEqual(page.locator('[data-driver-wave]:visible').count(),2)
            board = page.locator('.neosektor-driver-board').bounding_box()
            self.assertGreater(board['height'], 1000)
            self.assertGreater(page.locator('[data-driver-wave="second"]').bounding_box()['y'], 650)
            self.assertTrue(page.locator('.sektor-tv-exit').is_visible())
            self.assertEqual(errors, [])
            browser.close()
        finally:
            Fixture.tearDownClass()
