"""Single focused Live Counts pilot view; reuse the isolated shell fixture."""
import unittest
from pathlib import Path
from tests.browser import test_mobile_drawer as existing
from app.extensions import db
from app.models import NeoSektorOperationalSetting
from app.services.access_control import ensure_default_gateway_and_nodes


class LiveCountsFontTest(unittest.TestCase):
    def test_mobile_pilot(self):
        fixture_type = existing.MobileDrawerBrowserTest
        fixture_type.setUpClass()
        with fixture_type.app.app_context():
            gateway = ensure_default_gateway_and_nodes()
            db.session.add(NeoSektorOperationalSetting(gateway_id=gateway.id, gateway_code=gateway.code, integration_mode='neo_only'))
            db.session.commit()
        fixture = fixture_type()
        browser = fixture.pw.chromium.launch()
        try:
            page = browser.new_page(viewport={'width': 390, 'height': 844})
            loaded = []
            page.on('response', lambda response: loaded.append(response.url) if response.status == 200 else None)
            fixture.login(page)
            fixture.ready(page, '/neosektor/live-counts')
            self.assertTrue(page.url.endswith('/neosektor/live-counts'))
            page.evaluate('async()=>{await document.fonts.ready;}')
            evidence = Path('instance/browser-evidence/live-counts-plain')
            evidence.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(evidence/'chromium-390x844.png'))
            for style in ('Regular', 'SemiBold'):
                self.assertTrue(any(f'NeoFontPlain-{style}.woff2' in url for url in loaded), style)
            self.assertTrue(page.evaluate("document.fonts.check('400 14px NeoFontPlain') && document.fonts.check('600 14px NeoFontPlain')"))
            for selector in ('.live-update-status', '.wave-card h2', '.wave-metric-label', '.readonly-count', '.bay-heading strong'):
                self.assertIn('NeoFontPlain', page.locator(selector).first.evaluate('e=>getComputedStyle(e).fontFamily'))
            self.assertIn('NeoFont', page.locator('.operational-mobile-identity strong').evaluate('e=>getComputedStyle(e).fontFamily'))
            self.assertIn('NeoFontLite', page.locator('.operational-mobile-identity small').evaluate('e=>getComputedStyle(e).fontFamily'))
            self.assertFalse(page.evaluate('document.documentElement.scrollWidth > innerWidth + 1'))
            gap = page.locator('.neosektor-live-wave-row').evaluate('e=>e.getBoundingClientRect().top-e.previousElementSibling.getBoundingClientRect().bottom')
            self.assertLessEqual(gap, 12)
            self.assertGreaterEqual(gap, 0)
            dock = page.locator('.neo-mobile-bottom').bounding_box()
            self.assertAlmostEqual(dock['y']+dock['height'], 844, delta=1)
            # Text must fit its visible card; labels remain single-line.
            self.assertTrue(page.locator('.wave-metric-label, .bay-heading span, .bay-heading strong').evaluate_all('es=>es.every(e=>{const r=document.createRange();r.selectNodeContents(e);const t=r.getBoundingClientRect(),b=e.closest("article").getBoundingClientRect();return t.left>=b.left && t.right<=b.right && r.getClientRects().length===1;})'))
            print(f'Live Counts: gap={gap:.1f}px; dock={dock}; both font files loaded')
            fixture.ready(page, '/neosektor')
            self.assertEqual(page.locator('link[href*="neosektor_live_counts.css"]').count(), 0)
        finally:
            browser.close()
            fixture_type.tearDownClass()
