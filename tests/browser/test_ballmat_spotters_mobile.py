"""Four operator layouts, real login/CSRF and a desktop smoke check."""
import json
import unittest
from pathlib import Path
from tests.browser import test_mobile_drawer as existing
from app.extensions import db
from app.models import NeoSektorOperationalSetting
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.neosektor_live_counts import apply_standalone_compat_values
from tests.test_neosektor_integration_modes import _complete_sheet_values


class BallmatMobileTest(unittest.TestCase):
    def test_four_modes(self):
        kind = existing.MobileDrawerBrowserTest
        kind.setUpClass()
        evidence = Path('instance/browser-evidence/ballmat-spotters').resolve()
        evidence.mkdir(parents=True,exist_ok=True)
        results = []
        try:
            with kind.app.app_context():
                gateway = ensure_default_gateway_and_nodes()
                db.session.add(NeoSektorOperationalSetting(gateway_id=gateway.id,gateway_code=gateway.code,integration_mode='neo_only'))
                apply_standalone_compat_values(gateway,_complete_sheet_values())
                db.session.commit()
            browser = kind.pw.chromium.launch()
            page = browser.new_page(viewport={'width':390,'height':844},device_scale_factor=1)
            errors=[]
            page.on('pageerror',lambda error: errors.append(str(error)))
            kind().login(page)
            for side,path,bays in [('east','/neosektor/ebm',['Bay 1','Bay 2','Bay 3']),('west','/neosektor/wbm',['Bay 4','Bay 5'])]:
                kind().ready(page,path)
                self.assertTrue(page.locator('#ballmat-operator [data-live-update-status]').is_visible(),
                    page.locator('[data-live-update-status]').evaluate_all('es=>es.map(e=>({html:e.outerHTML,parent:e.parentElement.outerHTML.slice(0,400),display:getComputedStyle(e).display}))'))
                for mode in (1,2):
                    if mode==2:
                        with page.expect_response(lambda r:'/ballmat/update' in r.url and r.request.method=='POST') as mutation:
                            page.locator('[data-bm-mode="2"]').click()
                        self.assertEqual(mutation.value.status,200)
                        page.wait_for_function('document.querySelector("[data-ballmat-mobile]").dataset.mode==="2"')
                    page.screenshot(path=str(evidence/f'{side}-{mode}-390x844.png'))
                    result=page.evaluate('''() => {
                        const dock=document.querySelector('.neo-mobile-bottom').getBoundingClientRect();
                        const panel=document.querySelector('[data-ballmat-mobile]');
                        return {width:document.documentElement.scrollWidth,height:document.documentElement.scrollHeight,
                            dockBottom:dock.bottom,dockTop:dock.top,panelBottom:panel.getBoundingClientRect().bottom,
                            font:getComputedStyle(panel).fontFamily,
                            geometry: ['.content','#ballmat-operator','.counts-wrap','.live-update-controls','.operational-mobile-header'].map(s=>{
                                const e=document.querySelector(s),r=e.getBoundingClientRect();
                                return {s,top:r.top,bottom:r.bottom,height:r.height,position:getComputedStyle(e).position};})};
                    }''')
                    results.append(dict(side=side,mode=mode,**result))
                    (evidence/'geometry.json').write_text(json.dumps(results,indent=2))
                    self.assertLessEqual(result['height'],845,result)
                    self.assertLessEqual(result['width'],391,result)
                    self.assertAlmostEqual(result['dockBottom'],844,delta=1)
                    self.assertLessEqual(result['panelBottom'],result['dockTop']-10)
                    self.assertIn('NeoFontPlain',result['font'])
                    status = page.locator('#ballmat-operator .live-update-controls').bounding_box()
                    header = page.locator('.operational-mobile-header').bounding_box()
                    self.assertGreaterEqual(status['y'],header['y']+header['height'])
                    self.assertTrue(page.locator('#ballmat-operator [data-live-monitor-mode]').is_visible())
                    self.assertEqual(page.locator('.bm-published:visible').count(),3 if mode==2 else 0)
                    self.assertEqual(page.locator('.bm-step:visible').count(),6 if mode==2 else 3)
                    self.assertLessEqual(page.locator('.bm-routing').bounding_box()['y']+page.locator('.bm-routing').bounding_box()['height'],result['dockTop']-10)
                    # Detect clipped contents, not just document overflow.
                    self.assertEqual(page.locator('.ballmat-mobile button,.ballmat-mobile output').evaluate_all('''es=>es.filter(e=>e.getBoundingClientRect().width>0 && e.scrollWidth>e.clientWidth+1).map(e=>e.outerHTML)'''),[])
                    self.assertEqual(page.locator('.bm-waves button,.bm-waves input').count(),0)
                    self.assertEqual(page.locator('[data-bm-bay]').evaluate_all('es=>es.map(e=>e.dataset.bmBay)'),bays)
                    self.assertEqual(page.locator('[data-bm-other]:visible').count(),3)
                    if mode==2:
                        for row in page.locator('.bm-count-row').all():
                            left=row.locator('.bm-left').bounding_box()
                            total=row.locator('.bm-published').bounding_box()
                            right=row.locator('.bm-right').bounding_box()
                            self.assertLessEqual(left['x']+left['width'],total['x'])
                            self.assertLessEqual(total['x']+total['width'],right['x'])
                        with page.expect_response(lambda r:'/ballmat/update' in r.url and r.request.method=='POST') as mutation:
                            page.locator('[data-metric="first"][data-position="right"][data-bm-step="1"]').click()
                        self.assertEqual(mutation.value.status,200)
                        page.wait_for_function('document.querySelector("[data-ballmat-mobile]").getAttribute("aria-busy")===null')
                        self.assertEqual(page.locator('.bm-right [data-bm-value="first:right"]').inner_text(),'1')
                    page.locator('[data-drawer-toggle]').click()
                    self.assertAlmostEqual(page.locator('.neo-mobile-bottom').bounding_box()['y']+page.locator('.neo-mobile-bottom').bounding_box()['height'],844,delta=1)
                    page.locator('[data-drawer-toggle]').click()
            page.evaluate('document.documentElement.style.setProperty("--neo-safe-bottom","34px"); document.documentElement.style.setProperty("--neo-safe-top","44px")')
            self.assertLessEqual(page.evaluate('document.documentElement.scrollHeight'),845)
            self.assertAlmostEqual(page.locator('.neo-mobile-bottom').evaluate('e=>e.getBoundingClientRect().bottom'),844,delta=1)
            self.assertLessEqual(page.locator('.bm-routing').evaluate('e=>e.getBoundingClientRect().bottom'),page.locator('.neo-mobile-bottom').evaluate('e=>e.getBoundingClientRect().top')-10)
            results.append({'safeTop':44,'safeBottom':34,'dockBottom':844,'noScroll':True})
            (evidence/'geometry.json').write_text(json.dumps(results,indent=2))
            page.evaluate('document.documentElement.style.removeProperty("--neo-safe-bottom");document.documentElement.style.removeProperty("--neo-safe-top")')
            page.set_viewport_size({'width':1920,'height':1080})
            kind().ready(page,'/neosektor/ebm')
            self.assertFalse(page.locator('[data-ballmat-mobile]').is_visible())
            self.assertTrue(page.locator('.ops-grid').is_visible())
            self.assertFalse(page.evaluate('document.documentElement.scrollWidth>innerWidth+1'))
            page.screenshot(path=str(evidence/'desktop-1920x1080.png'))
            self.assertEqual(errors,[])
            browser.close()
        finally:
            kind.tearDownClass()
