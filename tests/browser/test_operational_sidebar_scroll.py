"""One Chromium check: primary scroll ownership, pinned utilities and mobile."""
import json
import unittest
from playwright.sync_api import expect
from tests.browser import test_mobile_drawer as fixtures


class SidebarScrollBrowserTest(unittest.TestCase):
    def test_primary_scroll_keeps_utilities_fixed_and_links_reachable(self):
        F=fixtures.MobileDrawerBrowserTest;F.setUpClass();browser=None;measurements=[]
        try:
            browser=F.pw.chromium.launch()
            page=browser.new_page(viewport={'width':1280,'height':768})
            errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            F().login(page)
            page.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1','seen')")
            for path in ('/neoscorpion/fuel-dispatch','/neostaffing/people','/motherbrain/manage-sort'):
                F().ready(page,path)
                sidebar=page.locator('[data-operational-sidebar]')
                primary=sidebar.locator('.operational-sidebar-primary')
                menu=primary.locator('.motherbrain-desktop-side-menu')
                footer=sidebar.locator('.operational-sidebar-utilities')
                expect(primary).to_have_count(1)
                links=menu.locator('a');hrefs=links.evaluate_all('(nodes)=>nodes.map(n=>n.getAttribute("href"))')
                self.assertTrue(hrefs)
                for height in (480,600,768,900):
                    page.set_viewport_size({'width':1280,'height':height})
                    for collapsed in (False,True):
                        if collapsed:sidebar.locator('[data-operational-sidebar-toggle]').click()
                        before=footer.bounding_box()
                        self.assertLessEqual(before['y']+before['height'],height+1)
                        self.assertGreater(before['y'],84)
                        self.assertEqual(sidebar.evaluate('e=>getComputedStyle(e).overflowY'),'hidden')
                        self.assertEqual(menu.evaluate('e=>getComputedStyle(e).overflowY'),'visible')
                        self.assertEqual(primary.evaluate('e=>getComputedStyle(e).overflowY'),'auto')
                        for link in links.all():
                            link.scroll_into_view_if_needed()
                            visible=link.evaluate('''e=>{
                                const b=e.getBoundingClientRect(),p=e.closest('.operational-sidebar-primary').getBoundingClientRect();
                                const hit=document.elementFromPoint(b.x+b.width/2,b.y+b.height/2);
                                return {ok:b.top>=p.top-1 && b.bottom<=p.bottom+1 && e.contains(hit),
                                    rect:b.toJSON(),primary:p.toJSON(),hit:hit?.className,link:e.textContent.trim()};
                            }''')
                            if not visible['ok']:page.screenshot(path=str(F.evidence/'sidebar-scroll-failure.png'))
                            self.assertTrue(visible['ok'],dict(path=path,height=height,collapsed=collapsed,**visible))
                        self.assertEqual(links.evaluate_all('(nodes)=>nodes.map(n=>n.getAttribute("href"))'),hrefs)
                        after=footer.bounding_box()
                        self.assertAlmostEqual(before['y'],after['y'],delta=.1)
                        self.assertAlmostEqual(before['height'],after['height'],delta=.1)
                        self.assertEqual(sidebar.evaluate('e=>e.scrollTop'),0)
                        self.assertEqual(menu.evaluate('e=>e.scrollTop'),0)
                        if height==480:self.assertGreater(primary.evaluate('e=>e.scrollTop'),0)
                        for control in footer.locator('a,button').all():
                            expect(control).to_be_visible()
                            self.assertTrue(control.evaluate('''e=>{const b=e.getBoundingClientRect();return b.bottom<=innerHeight && e.contains(document.elementFromPoint(b.x+b.width/2,b.y+b.height/2));}'''))
                        measurements.append(dict(path=path,height=height,collapsed=collapsed,
                            footerY=after['y'],footerHeight=after['height'],
                            primaryHeight=primary.bounding_box()['height'],scrollTop=primary.evaluate('e=>e.scrollTop')))
                        sidebar.locator('[data-operational-sidebar-toggle]').scroll_into_view_if_needed()
                        if collapsed:sidebar.locator('[data-operational-sidebar-toggle]').click()
                    if path=='/neoscorpion/fuel-dispatch' and height in (480,900):
                        page.screenshot(path=str(F.evidence/f'sidebar-pinned-{height}.png'))
            page.set_viewport_size({'width':390,'height':844})
            expect(sidebar).to_be_hidden()
            page.locator('[data-drawer-toggle]').click()
            expect(page.locator('[data-mobile-drawer]')).to_be_visible()
            page.locator('[data-drawer-close]').click()
            expect(page.locator('[data-mobile-drawer]')).to_be_hidden()
            self.assertEqual(errors,[])
            (F.evidence/'sidebar-scroll-measurements.json').write_text(json.dumps(measurements,indent=2))
        finally:
            if browser:browser.close()
            F.tearDownClass()
