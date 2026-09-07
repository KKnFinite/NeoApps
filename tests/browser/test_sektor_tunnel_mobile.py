"""Focused Tunnel/mobile viewport regression, real pages and isolated SQLite."""
import json
import unittest
from pathlib import Path
from tests.browser import test_mobile_drawer as existing
from app.extensions import db
from app.models import NeoSektorOperationalSetting, User
from app.services.access_control import ensure_default_gateway_and_nodes


class SektorTunnelMobileTest(unittest.TestCase):
    def test_tunnel_and_all_sektor_docks(self):
        kind = existing.MobileDrawerBrowserTest
        kind.setUpClass()
        evidence = Path('instance/browser-evidence/sektor-tunnel-mobile')
        evidence.mkdir(parents=True, exist_ok=True)
        results = []
        try:
            with kind.app.app_context():
                gateway = ensure_default_gateway_and_nodes()
                db.session.add(NeoSektorOperationalSetting(gateway_id=gateway.id,gateway_code=gateway.code,integration_mode='neo_only'))
                db.session.commit()
            paths = ['/neosektor'+suffix for suffix in ('','/live-counts','/tunnel-conductor','/ebm','/wbm','/driver-routing','/discharge','/settings')]
            for engine in ('chromium','webkit'):
                browser = getattr(kind.pw, engine).launch()
                try:
                    page = browser.new_page(viewport={'width':390,'height':844})
                    kind().login(page)
                    for path in paths:
                        kind().ready(page,path)
                        for safe in (0,34):
                            page.evaluate('(n)=>document.documentElement.style.setProperty("--neo-safe-bottom",n+"px")',safe)
                            result = page.evaluate('''()=>{
                                const d=document.querySelector('.neo-mobile-bottom'),r=d.getBoundingClientRect(),s=getComputedStyle(d);
                                return {bottom:r.bottom,height:r.height,position:s.position,transform:s.transform,translate:s.translate,margin:s.marginBottom,
                                  overflow:document.documentElement.scrollWidth>innerWidth+1,
                                  owners:['html','body','.shell','.content'].map(q=>{const s=getComputedStyle(document.querySelector(q));return {selector:q,overflow:s.overflowY,maxHeight:s.maxHeight};})};
                            }''')
                            results.append(dict(engine=engine,path=path,safe=safe,**result))
                            self.assertAlmostEqual(result['bottom'],844,delta=1,msg=path)
                            self.assertAlmostEqual(result['height'],54+safe,delta=1)
                            self.assertFalse(result['overflow'],(engine,path))
                            self.assertEqual((result['position'],result['transform'],result['translate'],result['margin']),('fixed','none','none','0px'))
                            for owner in result['owners']:
                                self.assertNotEqual(owner['overflow'],'hidden',(path,owner))
                                self.assertEqual(owner['maxHeight'],'none',(path,owner))
                            page.locator('[data-drawer-toggle]').click()
                            self.assertAlmostEqual(page.locator('.neo-mobile-bottom').evaluate('e=>e.getBoundingClientRect().bottom'),844,delta=1)
                            if path == '/neosektor':
                                gap = page.locator('.sektor-command-tile--live-counts').evaluate('e=>document.querySelector(".neo-mobile-bottom").getBoundingClientRect().top-e.getBoundingClientRect().bottom')
                                self.assertGreaterEqual(gap,20)
                                self.assertLessEqual(gap,40)
                            page.locator('[data-drawer-toggle]').click()
                            self.assertAlmostEqual(page.locator('.neo-mobile-bottom').evaluate('e=>e.getBoundingClientRect().bottom'),844,delta=1)
                        if path == '/neosektor/tunnel-conductor':
                            page.evaluate('document.documentElement.style.setProperty("--neo-safe-bottom","0px")')
                            page.screenshot(path=str(evidence/f'{engine}-390x844.png'))
                            self.assertTrue(page.locator('[data-live-update-status]').is_visible())
                            self.assertEqual(page.locator('#neosektor-tunnel-panel input').evaluate_all('''es=>{
                                const rs=es.map(e=>({label:e.getAttribute('aria-label')||e.dataset.neosektorEditKey,r:e.getBoundingClientRect()}));
                                return rs.flatMap((a,i)=>rs.slice(i+1).filter(b=>Math.min(a.r.right,b.r.right)>Math.max(a.r.left,b.r.left)+1&&Math.min(a.r.bottom,b.r.bottom)>Math.max(a.r.top,b.r.top)+1).map(b=>[a.label,b.label]));
                            }'''),[])
                            for label in ('Ballmat Counts','Bay Status','Unload Modifiers','Driver Route Offset'):
                                title = page.locator('#neosektor-tunnel-panel h2',has_text=label).filter(visible=True)
                                self.assertEqual(title.count(),1,label)
                                self.assertEqual(title.evaluate('e=>getComputedStyle(e).textAlign'),'center')
                            self.assertEqual(page.locator('[data-tunnel-wave-input]').count(),2)
                            self.assertEqual(page.locator('[data-wave-count]').count(),4)
                            self.assertEqual(page.locator('[data-open-bays]').count(),2)
                            self.assertEqual(page.locator('[data-tunnel-route-override-input]').count(),2)
                            self.assertEqual(page.locator('[data-tunnel-bay-priority-enabled]').count(),5)
                            for key,label in (('first_modifier','1ST WAVE BAYS'),('second_modifier','2ND WAVE BAYS')):
                                self.assertEqual(page.locator(f'[data-tunnel-setting="{key}"]').locator('..').locator('span').inner_text(),label)
                            offset = page.locator('.tunnel-offset-panel')
                            page.evaluate('window.scrollTo(0,document.documentElement.scrollHeight)')
                            self.assertLessEqual(offset.bounding_box()['y']+offset.bounding_box()['height'],page.locator('.neo-mobile-bottom').bounding_box()['y'])
                            self.assertGreaterEqual(offset.bounding_box()['y'],0)
                            page.screenshot(path=str(evidence/f'{engine}-390x844-bottom.png'))
                    # The permission-gated management surface is another Sektor
                    # shell consumer, even though it reuses an attendance template.
                    with kind.app.app_context():
                        manager = User.query.filter_by(username='drawer-admin').one()
                        previous = manager.management_level
                        manager.management_level = 'manager'
                        db.session.commit()
                    for area in ('dis','ebm','wbm'):
                        path = '/neosektor/manage-employees?area='+area
                        kind().ready(page,path)
                        self.assertTrue(page.locator('[data-operational-manage-employees]').count())
                        for safe in (0,34):
                            page.evaluate('(n)=>document.documentElement.style.setProperty("--neo-safe-bottom",n+"px")',safe)
                            dock = page.locator('.neo-mobile-bottom').bounding_box()
                            self.assertAlmostEqual(dock['y']+dock['height'],844,delta=1)
                            self.assertFalse(page.evaluate('document.documentElement.scrollWidth>innerWidth+1'))
                            results.append(dict(engine=engine,path=path,safe=safe,bottom=dock['y']+dock['height'],height=dock['height']))
                    with kind.app.app_context():
                        User.query.filter_by(username='drawer-admin').one().management_level = previous
                        db.session.commit()
                    if engine == 'chromium':
                        page.set_viewport_size({'width':1920,'height':1080})
                        kind().ready(page,'/neosektor/tunnel-conductor')
                        self.assertFalse(page.locator('.tunnel-polish-mobile-only').first.is_visible())
                        self.assertFalse(page.evaluate('document.documentElement.scrollWidth>innerWidth+1'))
                        page.screenshot(path=str(evidence/'desktop.png'))
                finally:
                    browser.close()
            print(f'{len(results)} Sektor engine/page/safe-area checks passed')
        finally:
            (evidence/'dock-results.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
            kind.tearDownClass()
