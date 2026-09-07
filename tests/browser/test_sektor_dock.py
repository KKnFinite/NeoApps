"""Sektor shell/content/dock parity with Ermac, using the isolated fixture."""
import json
import unittest
from pathlib import Path
from tests.browser import test_mobile_drawer as existing


class SektorDockBrowserTest(unittest.TestCase):
    def test_mobile_shell_matches_ermac(self):
        kind=existing.MobileDrawerBrowserTest
        kind.setUpClass()
        fixture=kind()
        evidence=Path('instance/browser-evidence/sektor-hero-menu-v3')
        evidence.mkdir(parents=True,exist_ok=True)
        results=[]
        try:
            for engine in ('chromium','webkit'):
                browser=getattr(fixture.pw,engine).launch()
                try:
                    for width,height in ((390,844),(430,932)):
                        page=browser.new_page(viewport={'width':width,'height':height})
                        fixture.login(page)
                        for safe in (0,34):
                            measured={}
                            for label,path in (('ermac','/neoermac'),('sektor','/neosektor')):
                                fixture.ready(page,path)
                                # Desktop engines do not expose real iPhone safe areas.
                                # Exercise the shared CSS contract separately with a synthetic inset.
                                page.evaluate('(safe)=>document.documentElement.style.setProperty("--neo-safe-bottom",safe+"px")',safe)
                                page.evaluate("async()=>{await document.fonts.load('300 16px NeoFontLite');await document.fonts.ready;}")
                                measured[label]=page.locator('.neo-mobile-bottom').evaluate('''e=>{
                                    const b=e.getBoundingClientRect(),s=getComputedStyle(e);
                                    return {top:b.top,bottom:b.bottom,height:b.height,paddingBottom:s.paddingBottom,
                                    position:s.position,transform:s.transform,translate:s.translate,
                                    shell:['.shell','.shell > .content'].map(selector=>{
                                      const s=getComputedStyle(document.querySelector(selector));
                                      return Object.fromEntries(['width','maxWidth','minHeight','maxHeight','overflowX','overflowY','paddingTop','paddingRight','paddingBottom','paddingLeft','marginTop','marginBottom','display','position'].map(k=>[k,s[k]]));
                                    }),
                                    scrollHeight:document.documentElement.scrollHeight,viewport:innerHeight,
                                    controls:[...e.children].map(c=>{let r=c.getBoundingClientRect();return [r.top,r.bottom];})};
                                }''')
                                if safe==34:
                                    page.screenshot(path=str(evidence/f'{engine}-{width}-{height}-{label}-safe34.png'))
                            results.append(dict(engine=engine,width=width,height=height,safe=safe,**measured))
                            for key in ('top','bottom','height'):
                                self.assertAlmostEqual(measured['ermac'][key],measured['sektor'][key],delta=1)
                            self.assertEqual(measured['ermac']['paddingBottom'],measured['sektor']['paddingBottom'])
                            self.assertEqual(measured['ermac']['controls'],measured['sektor']['controls'])
                            self.assertEqual(measured['ermac']['shell'],measured['sektor']['shell'])
                            self.assertIn('sektor=20260907-hero-menu-v3',page.locator('link[href*="neosektor_dashboard.css"]').get_attribute('href'))
                            self.assertIn('sektor=20260907-shared-geometry-v2',page.locator('link[href*="17-shared.css"]').get_attribute('href'))
                            self.assertEqual(measured['sektor']['position'],'fixed')
                            self.assertEqual(measured['sektor']['transform'],'none')
                            self.assertEqual(measured['sektor']['translate'],'none')
                            self.assertEqual(measured['sektor']['bottom'],height)
                            self.assertEqual(page.locator('.sektor-command h1, .sektor-command-heading').count(),0)
                            gap=page.locator('.sektor-command-tile--live-counts').evaluate('e=>document.querySelector(".neo-mobile-bottom").getBoundingClientRect().top-e.getBoundingClientRect().bottom')
                            self.assertGreaterEqual(gap,20)
                            self.assertLessEqual(gap,40)
                            page.evaluate('window.scrollTo(0,document.documentElement.scrollHeight)')
                            self.assertTrue(page.locator('.sektor-command-tile--live-counts').evaluate('e=>e.getBoundingClientRect().bottom <= document.querySelector(".neo-mobile-bottom").getBoundingClientRect().top'))
                        page.close()
                finally:
                    browser.close()
        finally:
            (evidence/'measurements.json').write_text(json.dumps(results,indent=2))
            kind.tearDownClass()
