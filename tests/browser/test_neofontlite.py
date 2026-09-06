"""NeoFontLite V2 on real Sektor UI, isolated SQLite and no integrations."""
import unittest
from pathlib import Path
from tests.browser import test_mobile_drawer as existing


class NeoFontLiteBrowserTest(unittest.TestCase):
    def test_mobile_vector_font(self):
        fixture_type=existing.MobileDrawerBrowserTest
        fixture_type.setUpClass()
        fixture=fixture_type()
        evidence=Path('instance/neofontlite-v2/evidence')
        evidence.mkdir(parents=True,exist_ok=True)
        try:
            for engine in ['chromium','webkit']:
                with self.subTest(engine=engine):
                    browser=getattr(fixture.pw,engine).launch()
                    try:
                        page=browser.new_page(viewport={'width':390,'height':760})
                        fixture.login(page)
                        fixture.ready(page,'/neosektor')
                        page.evaluate("async()=>{await document.fonts.load('300 16px NeoFontLite');await document.fonts.ready;}")
                        self.assertTrue(page.evaluate("[...document.fonts].some(f=>f.family==='NeoFontLite' && f.status==='loaded')"))
                        self.assertFalse(page.evaluate('document.documentElement.scrollWidth>innerWidth+1'))
                        self.assertEqual(page.locator('.sektor-command-copy strong').all_text_contents(),['EBM','WBM','Tunnel Conductor','Driver Routing','Discharge','Settings','Live Counts'])
                        self.assertTrue(page.locator('.sektor-command-copy strong').evaluate_all('''es=>es.every(e=>{
                            const r=document.createRange();r.selectNodeContents(e);
                            const t=r.getBoundingClientRect(),b=e.closest('a').getBoundingClientRect();
                            return getComputedStyle(e).fontFamily.includes('NeoFontLite') && t.left>=b.left && t.right<=b.right && t.top>=b.top && t.bottom<=b.bottom;
                        })'''))
                        page.screenshot(path=str(evidence/f'{engine}-sektor-390-760.png'),full_page=True)
                        page.goto(Path('app/static/fonts/neofontlite/preview.html').resolve().as_uri())
                        page.set_viewport_size({'width':1200,'height':1000})
                        page.evaluate("async()=>{await document.fonts.load('300 16px NeoFontLite');await document.fonts.load('16px NeoFont');await document.fonts.ready;}")
                        self.assertEqual(page.locator('.alphabet span').all_text_contents(),list('ABCDEFGHIJKLMNOPQRSTUVWXYZ'))
                        page.screenshot(path=str(evidence/f'{engine}-font-preview.png'),full_page=True)
                    finally:
                        browser.close()
        finally:
            fixture_type.tearDownClass()
