"""Focused approved Sektor dashboard checks using the shared isolated fixture."""
import unittest
from pathlib import Path
from tests.browser import test_mobile_drawer as existing


class SektorDashboardBrowserTest(unittest.TestCase):
    def test_dashboard_views(self):
        fixture_type = existing.MobileDrawerBrowserTest
        fixture_type.setUpClass()
        fixture = fixture_type()
        evidence = Path('instance/browser-evidence/sektor-dashboard')
        evidence.mkdir(parents=True, exist_ok=True)
        try:
            for engine, width, height in [('chromium',390,844),('webkit',390,760),('chromium',1920,1080)]:
                with self.subTest(engine=engine, width=width, height=height):
                    browser = getattr(fixture.pw, engine).launch()
                    try:
                        page = browser.new_page(viewport={'width':width,'height':height})
                        fixture.login(page)
                        fixture.ready(page, '/neosektor')
                        page.evaluate("async()=>{await document.fonts.load('300 16px NeoFontLite');await document.fonts.ready;}")
                        self.assertEqual(page.locator('[data-node-dashboard-tile]').evaluate_all("es=>es.map(e=>e.dataset.nodeDashboardTile)"),
                                         ['ebm','wbm','tunnel','driver-routing','discharge','settings','live-counts'])
                        self.assertEqual(page.locator('[data-operational-sidebar]').count(),0)
                        if width>900:
                            self.assertTrue(page.locator('.operational-node-topbar').evaluate('e=>{const b=e.getBoundingClientRect();return b.left===0 && b.top===0 && b.width===innerWidth && [...e.children].every(c=>{const r=c.getBoundingClientRect();return r.top>=b.top && r.bottom<=b.bottom+1;});}'))
                        self.assertFalse(page.evaluate('document.documentElement.scrollWidth > innerWidth + 1'))
                        self.assertTrue(page.locator('.sektor-command-copy strong').evaluate_all('es=>es.every(e=>{const r=document.createRange();r.selectNodeContents(e);const t=r.getBoundingClientRect(),b=e.closest("a").getBoundingClientRect();return t.left>=b.left && t.right<=b.right && t.top>=b.top && t.bottom<=b.bottom;})'))
                        self.assertIn('NeoFontLite',page.locator('.sektor-command-copy strong').first.evaluate('e=>getComputedStyle(e).fontFamily'))
                        art = page.locator('.sektor-command-art img').evaluate('e=>e.currentSrc')
                        self.assertIn('dashboard_mobile.png' if width<901 else 'dashboard_desktop.png',art)
                        self.assertTrue(page.locator('.sektor-command-tile--live-counts').evaluate("e=>Math.abs(e.getBoundingClientRect().width-e.parentElement.getBoundingClientRect().width)<2"))
                        page.screenshot(path=str(evidence/f'{engine}-{width}-{height}.png'),full_page=True)
                        if width<901:
                            page.locator('.sektor-command-tile--live-counts').scroll_into_view_if_needed()
                            page.evaluate('window.scrollTo(0,document.documentElement.scrollHeight)')
                            self.assertTrue(page.locator('.sektor-command-tile--live-counts').evaluate("e=>e.getBoundingClientRect().bottom <= document.querySelector('.neo-mobile-bottom').getBoundingClientRect().top"))
                            page.screenshot(path=str(evidence/f'{engine}-{width}-{height}-scrolled.png'))
                    finally:
                        browser.close()
        finally:
            fixture_type.tearDownClass()
