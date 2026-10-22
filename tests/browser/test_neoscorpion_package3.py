"""Real Settings/Dashboard responsive bounds; synthetic authenticated database."""
import json
import os
from pathlib import Path
import unittest
from datetime import datetime

from playwright.sync_api import expect
from app.extensions import db
from app.models import User, PortalAppAccess
from app.services.access_control import backfill_default_gateway_node_roles
from app.services.password_policy import set_user_password
from tests.browser import test_mobile_drawer as fixtures


class ResponsiveAdministrationBrowserTest(unittest.TestCase):
    def test_settings_dashboard_viewport_matrix(self):
        F = fixtures.MobileDrawerBrowserTest
        F.setUpClass()
        phase = os.environ.get('PACKAGE3_CAPTURE', 'after')
        evidence = Path('instance/package3-evidence')
        evidence.mkdir(exist_ok=True)
        results = []
        try:
            for engine in ('chromium', 'webkit'):
                browser = getattr(F.pw, engine).launch()
                try:
                    page = browser.new_page(service_workers='block')
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    F().login(page)
                    for width, height in ((320,812),(375,812),(390,844),(430,932),
                                          (768,1024),(820,1180),(1024,768),
                                          (1440,900),(844,390),(1180,820)):
                        page.set_viewport_size({'width': width, 'height': height})
                        for name, path in (('settings','/neoscorpion/settings'),
                                           ('spear','/neoscorpion/settings/spear'),
                                           ('dashboard','/neoscorpion')):
                            F().ready(page, path)
                            geometry = page.evaluate("""() => {
                                const root=document.querySelector('.neoscorpion-shell');
                                const visible=e=>e.getClientRects().length && getComputedStyle(e).visibility!=='hidden';
                                const outside=[...root.querySelectorAll('input:not([type=hidden]),select,textarea,button,a,.neoscorpion-panel,.neoscorpion-dashboard-hero')]
                                    .filter(visible).map(e=>({tag:e.tagName,name:e.name||e.className,
                                        left:e.getBoundingClientRect().left,right:e.getBoundingClientRect().right}))
                                    .filter(r=>r.left < -1 || r.right > innerWidth+1);
                                return {width:innerWidth,document:document.documentElement.scrollWidth,
                                    bodyOverflow:getComputedStyle(document.body).overflowX,
                                    rootOverflow:getComputedStyle(root).overflowX,outside,
                                    overflowing:[...root.querySelectorAll('*')].filter(visible)
                                        .filter(e=>e.scrollWidth>e.clientWidth+2 && e.clientWidth>0)
                                        .map(e=>({tag:e.tagName,name:e.name||e.className,
                                            client:e.clientWidth,scroll:e.scrollWidth})),
                                    forms:[...root.querySelectorAll('form[data-admin-form]')].map(f=>({
                                        scope:f.dataset.adminForm,fields:[...f.elements].filter(e=>e.name).map(e=>e.name)})),
                                    tiles:[...root.querySelectorAll('[data-neoscorpion-dashboard-tile]')].map(e=>e.textContent.trim())};
                            }""")
                            results.append(dict(engine=engine,screen=name,height=height,**geometry))
                            (evidence/f'{phase}-geometry.json').write_text(json.dumps(results,indent=2),encoding='utf8')
                            if width in (320,375,768,820,1440) or height == 390:
                                page.screenshot(path=str(evidence/f'{phase}-{engine}-{name}-{width}.png'),full_page=True)
                            if phase != 'before':
                                with self.subTest(engine=engine,screen=name,width=width,height=height):
                                    self.assertLessEqual(geometry['document'],width+1,geometry)
                                    self.assertEqual(geometry['outside'],[],geometry)
                                    self.assertEqual(geometry['overflowing'],[],geometry)
                                    # The existing mobile app shell clips decorative chrome.
                                    # Test natural content too, rather than relying on that clip.
                                    natural=page.evaluate("""() => {
                                        const previous=document.body.style.overflowX;
                                        document.body.style.overflowX='visible';
                                        const width=document.documentElement.scrollWidth;
                                        document.body.style.overflowX=previous;
                                        return width;
                                    }""")
                                    self.assertLessEqual(natural,width+1,geometry)
                                    self.assertNotEqual(geometry['rootOverflow'],'hidden')
                                    if name == 'settings':
                                        self.assertEqual(len(geometry['forms']),3)
                                        field=page.locator('[name=fuel_density_lbs_per_gallon]')
                                        field.fill('6.7'); field.focus()
                                        expect(field).to_be_focused()
                                        expect(field).to_have_value('6.7')
                                        row_height=page.locator('[name=mission_row_height_percent]')
                                        for value in ('75','85','100','115','130','150'):
                                            row_height.select_option(value)
                                            self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'),width+1)
                                        for button in ('Save Settings','Save Assignment Planning','Save APU Rates'):
                                            control=page.get_by_role('button',name=button,exact=True)
                                            control.scroll_into_view_if_needed()
                                            expect(control).to_be_in_viewport()
                                            expect(control).to_be_enabled()
                                    elif name == 'spear':
                                        readable=page.locator('label:has([name=use_learned_top_off])').evaluate("""label=>{
                                            const text=[...label.childNodes].find(n=>n.nodeType===3 && n.textContent.trim());
                                            const range=document.createRange();range.selectNodeContents(text);
                                            return {lines:range.getClientRects().length,width:label.clientWidth};
                                        }""")
                                        self.assertLessEqual(readable['lines'],3,readable)
                                        control=page.get_by_role('button',name='SAVE SPEAR SETTINGS',exact=True)
                                        control.scroll_into_view_if_needed(); expect(control).to_be_in_viewport()
                                    else:
                                        self.assertGreater(len(geometry['tiles']),5)
                                        page.locator('[data-neoscorpion-dashboard-tile=settings]').click()
                                        expect(page).to_have_url(F.origin+'/neoscorpion/settings')
                    # The real navigation drawer remains operable at phone and tablet widths.
                    if phase != 'before':
                        for width in (320,820):
                            page.set_viewport_size({'width':width,'height':900})
                            F().ready(page,'/neoscorpion')
                            page.locator('[data-drawer-toggle]').click()
                            expect(page.locator('[data-mobile-drawer]')).to_be_visible()
                            page.locator('[data-drawer-close]').click()
                            expect(page.locator('[data-mobile-drawer]')).to_be_hidden()
                    self.assertEqual(errors,[])
                finally:
                    browser.close()
        finally:
            F.tearDownClass()

    def test_settings_viewer_controls_and_bounds(self):
        F = fixtures.MobileDrawerBrowserTest
        F.setUpClass()
        try:
            with F.app.app_context():
                user=User(username='package3-viewer',email='package3-viewer@example.test',
                          role='simulator',email_verified_at=datetime.now())
                set_user_password(user,'BrowserFixture123!')
                db.session.add(user); db.session.flush()
                backfill_default_gateway_node_roles(user,role='simulator')
                db.session.add(PortalAppAccess(user_id=user.id,app_code='neostaffing',
                    status='approved',role='simulator',is_active=True))
                db.session.commit()
            for engine in ('chromium','webkit'):
                browser=getattr(F.pw,engine).launch()
                try:
                    page=browser.new_page(service_workers='block')
                    F().login(page,'package3-viewer')
                    for width in (320,375,820,1440):
                        page.set_viewport_size({'width':width,'height':900})
                        for path in ('/neoscorpion/settings','/neoscorpion/settings/spear'):
                            F().ready(page,path)
                            self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'),width+1)
                            if path.endswith('/settings'):
                                expect(page.locator('[name=fuel_density_lbs_per_gallon]')).to_have_attribute('readonly','')
                                self.assertEqual(page.get_by_role('button',name='Save Settings',exact=True).count(),0)
                                # Existing simulator APU permission is independent.
                                expect(page.get_by_role('button',name='Save APU Rates',exact=True)).to_be_enabled()
                            else:
                                self.assertEqual(page.get_by_role('button',name='SAVE SPEAR SETTINGS',exact=True).count(),0)
                finally:
                    browser.close()
        finally:
            F.tearDownClass()
