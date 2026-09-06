"""One focused mobile Gateway check using the existing isolated app fixture."""
import unittest
from pathlib import Path
from tests.browser import test_mobile_drawer as existing


class NeoFontLiteBrowserTest(unittest.TestCase):
    def test_mobile_gateway_and_specimen(self):
        fixture_type=existing.MobileDrawerBrowserTest
        fixture_type.setUpClass()
        fixture=fixture_type()
        evidence=Path('instance/neofontlite-build/evidence')
        evidence.mkdir(parents=True,exist_ok=True)
        browser=fixture.pw.chromium.launch()
        try:
            page=browser.new_page(viewport={'width':390,'height':760})
            fixture.login(page)
            fixture.ready(page,'/rfd')
            page.evaluate("async()=>{await document.fonts.load('300 16px NeoFontLite');await document.fonts.ready;}")
            result=page.locator('.gateway-mobile-header .neo-mobile-product-name').evaluate('''e=>{
                const style=getComputedStyle(e),r=document.createRange();r.selectNodeContents(e);
                const b=e.getBoundingClientRect(),t=r.getBoundingClientRect();
                return {text:e.textContent.trim(),family:style.fontFamily,weight:style.fontWeight,
                    fits:t.left>=b.left && t.right<=b.right+1 && t.height<=b.height+1,
                    overflow:document.documentElement.scrollWidth>innerWidth+1,
                    loaded:[...document.fonts].some(f=>f.family==='NeoFontLite' && f.status==='loaded')};
            }''')
            self.assertEqual(result['text'],'NeoGateway')
            self.assertIn('NeoFontLite',result['family'])
            self.assertEqual(result['weight'],'300')
            self.assertTrue(result['fits'] and result['loaded'])
            self.assertFalse(result['overflow'])
            self.assertNotIn('NeoFontLite',page.locator('.gateway-mobile-title small').evaluate('e=>getComputedStyle(e).fontFamily'))
            self.assertNotIn('NeoFontLite',page.locator('.neo-mobile-bottom').evaluate('e=>getComputedStyle(e).fontFamily'))
            page.screenshot(path=str(evidence/'gateway-390-760.png'))
            page.goto(Path('app/static/fonts/neofontlite/preview.html').resolve().as_uri())
            page.evaluate("async()=>{await document.fonts.load('300 32px NeoFontLite');await document.fonts.ready;await Promise.all([...document.images].map(i=>i.decode()));}")
            self.assertEqual(page.locator('.alphabet span').all_text_contents(),list('ABCDEFGHIJKLMNOPQRSTUVWXYZ'))
            self.assertEqual(page.locator('.samples p').all_text_contents(),['NEOGATEWAY','PORTAL','HOME','NODES','MENU','SETTINGS','DASHBOARD'])
            page.screenshot(path=str(evidence/'font-preview.png'),full_page=True)
        finally:
            browser.close()
            fixture_type.tearDownClass()
