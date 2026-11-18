"""Portal-only responsive navigation; real CSRF login, synthetic retained DB."""
from contextlib import contextmanager
from pathlib import Path
from threading import Thread
from unittest import TestCase
from unittest.mock import patch
import logging
import tempfile

from playwright.sync_api import sync_playwright,expect
from werkzeug.serving import make_server
from app import create_app
from app.extensions import db
from app.models import PortalAppAccess,AccountAuthorizationState,AccountScopedRole,AccountSecurityEvent
from app.models.account_preference import AccountPreference
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.permission_rules import ensure_default_permission_rules
from tests.test_account_security_phase1 import fixture_user,PASSWORD


SIZES=((375,667),(390,844),(430,932),(960,800),(1024,768),(1280,900),(1440,900))


@contextmanager
def portal_fixture():
    fixture=Path(tempfile.mkdtemp(prefix='neoapps-portal-nav-'))/'fixture.db'
    app=create_app(type('Config',(),dict(SECRET_KEY='portal-navigation-fixture',TESTING=True,
        AUTO_BOOTSTRAP_DATABASE=False,CSRF_PROTECT_TESTING=True,SESSION_COOKIE_SECURE=False,
        AUTH_RATE_LIMIT_ENABLED=False,SQLALCHEMY_DATABASE_URI='sqlite:///'+fixture.as_posix(),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,FLIGHT_API_ENABLED=False,
        GOOGLE_MOTHERBRAIN_IMPORT_ENABLED=False,GOOGLE_MOTHERBRAIN_READER_ENABLED=False,
        LIVE_SCREEN_REFRESH_INTERVAL_MS=0)))
    with app.app_context():
        db.create_all();ensure_default_gateway_and_nodes();ensure_default_permission_rules()
        for name,role in (('portal-owner','grandmaster'),('portal-operator','watcher')):
            user=fixture_user(name,platform=role)
            user.first_name='Alexandria';user.last_name='Long Operational Coordinator Name'
            db.session.add(PortalAppAccess(user_id=user.id,app_code='neostaffing',role='operator',status='approved',is_active=True))
            if name=='portal-owner':db.session.add(AccountPreference(user_id=user.id,show_cryocheck=True))
        db.session.commit()
    server=make_server('127.0.0.1',0,app,threaded=True);thread=Thread(target=server.serve_forever,daemon=True)
    logging.getLogger('werkzeug').setLevel(logging.ERROR);thread.start()
    try:
        with patch('requests.sessions.Session.request',side_effect=AssertionError('External providers forbidden')):
            yield app,f'http://127.0.0.1:{server.server_port}',fixture
    finally:
        server.shutdown();thread.join(5)
        with app.app_context():db.session.remove();db.engine.dispose()


def login(page,origin,name):
    page.goto(origin+'/login')
    page.get_by_label('Email',exact=True).fill(name+'@example.test')
    page.get_by_label('Password',exact=True).fill(PASSWORD)
    with page.expect_navigation(wait_until='domcontentloaded'):
        page.locator('form button[type=submit]').first.click()
    page.goto(origin+'/portal')


def capture_baseline():
    evidence=Path('instance/browser-evidence/portal-navigation/before').resolve();evidence.mkdir(parents=True,exist_ok=True)
    with portal_fixture() as (_app,origin,fixture),sync_playwright() as pw:
        (evidence/'fixture-path.txt').write_text(str(fixture),encoding='utf-8')
        for engine in ('chromium','webkit'):
            browser=getattr(pw,engine).launch()
            try:
                for width,height in SIZES:
                    context=browser.new_context(viewport={'width':width,'height':height},device_scale_factor=1)
                    page=context.new_page();login(page,origin,'portal-owner')
                    page.screenshot(path=str(evidence/f'portal-{engine}-{width}.png'),full_page=True)
                    context.close()
            finally:browser.close()


class PortalNavigationBrowserTest(TestCase):
    def test_chromium_webkit_responsive_menu_header_and_permissions(self):
        evidence=Path('instance/browser-evidence/portal-navigation/after').resolve();evidence.mkdir(parents=True,exist_ok=True)
        with portal_fixture() as (app,origin,fixture),sync_playwright() as pw:
            (evidence/'fixture-path.txt').write_text(str(fixture),encoding='utf-8')
            for engine in ('chromium','webkit'):
                browser=getattr(pw,engine).launch()
                try:
                    for width,height in SIZES:
                        with self.subTest(engine=engine,width=width):
                            context=browser.new_context(viewport={'width':width,'height':height},device_scale_factor=1)
                            page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                            login(page,origin,'portal-owner')
                            expect(page.locator('[data-portal-app=neogateway]')).to_be_visible()
                            expect(page.locator('[data-portal-app=neostaffing]')).to_be_visible()
                            self.assertEqual(page.locator('.aa-apps-extra').count(),0)
                            self.assertEqual(page.get_by_role('menuitem',name='App switcher',exact=True).count(),0)
                            self.assertFalse(page.evaluate('document.documentElement.scrollWidth>innerWidth+1'))
                            cards=page.locator('[data-portal-app]').evaluate_all('(els)=>els.map(e=>({x:e.getBoundingClientRect().x,y:e.getBoundingClientRect().y,right:e.getBoundingClientRect().right,bottom:e.getBoundingClientRect().bottom}))')
                            for card in cards:self.assertTrue(card['x']>=0 and card['right']<=width+1,card)
                            if width<=900:
                                dock=page.locator('.neo-mobile-bottom').bounding_box()
                                self.assertLessEqual(max(c['bottom'] for c in cards),dock['y']+1)
                                self.assertLessEqual(cards[0]['bottom'],cards[1]['y'])
                                self.assertFalse(page.get_by_role('link',name='My Account',exact=True).is_visible())
                                page.screenshot(path=str(evidence/f'portal-{engine}-{width}.png'),full_page=True)
                                page.locator('[data-drawer-toggle]').click()
                                drawer=page.locator('[data-mobile-drawer]')
                                expect(drawer.get_by_role('link',name='My Account',exact=True)).to_be_visible()
                                expect(drawer.get_by_role('link',name='Accounts & Access',exact=True)).to_be_visible()
                                expect(drawer.get_by_role('link',name='CryoCheck ↗ · External',exact=True)).to_be_visible()
                                self.assertEqual(drawer.get_by_role('link',name='App Access',exact=True).count(),0)
                                page.screenshot(path=str(evidence/f'menu-{engine}-{width}.png'),full_page=True)
                                drawer.get_by_role('link',name='My Account',exact=True).click()
                                expect(page.get_by_role('heading',name='App Access',exact=True)).to_be_visible()
                            else:
                                header=page.locator('.portal-topbar')
                                expect(header.get_by_role('link',name='Accounts & Access',exact=True)).to_be_visible()
                                self.assertEqual(header.get_by_role('menuitem',name='My Account',exact=True).count(),1)
                                controls=header.locator('.portal-header-button,.aa-my-account-link,.character-switcher-trigger,.motherbrain-alert-button').evaluate_all('(els)=>els.map(e=>({left:e.getBoundingClientRect().left,right:e.getBoundingClientRect().right,top:e.getBoundingClientRect().top,bottom:e.getBoundingClientRect().bottom,height:e.getBoundingClientRect().height}))')
                                self.assertTrue(all(39<=c['height']<=41 for c in controls),controls)
                                fonts=header.locator('.portal-header-button,.aa-my-account-link,.character-switcher-trigger,.motherbrain-alert-button').evaluate_all('(els)=>els.map(e=>({family:getComputedStyle(e).fontFamily,size:getComputedStyle(e).fontSize,padding:getComputedStyle(e).padding,border:getComputedStyle(e).borderRadius}))')
                                self.assertTrue(all(font==fonts[0] for font in fonts),fonts)
                                for i,a in enumerate(controls):
                                    self.assertTrue(a['left']>=0 and a['right']<=width+1,a)
                                    for b in controls[i+1:]:self.assertFalse(a['left']<b['right']-1 and a['right']>b['left']+1 and a['top']<b['bottom']-1 and a['bottom']>b['top']+1,(a,b))
                                page.screenshot(path=str(evidence/f'portal-{engine}-{width}.png'),full_page=True)
                                header.locator('.character-switcher-trigger').click()
                                expect(header.locator('.character-switcher-panel')).to_be_visible()
                                header.locator('.character-switcher-trigger').click()
                                header.locator('.motherbrain-alert-button').click()
                                expect(header.locator('.motherbrain-alert-panel')).to_be_visible()
                                panel=header.locator('.motherbrain-alert-panel').bounding_box()
                                self.assertTrue(panel['x']>=0 and panel['x']+panel['width']<=width+1,panel)
                                header.locator('.motherbrain-alert-button').click()
                                header.get_by_role('menuitem',name='My Account',exact=True).click()
                                expect(page.get_by_role('heading',name='My Account',exact=True)).to_be_visible()
                            self.assertEqual(errors,[])
                            context.close()
                    # The ordinary account gets no administration link or optional shortcut.
                    for width,height in ((390,844),(1280,900)):
                        context=browser.new_context(viewport={'width':width,'height':height},reduced_motion='reduce');page=context.new_page()
                        login(page,origin,'portal-operator')
                        self.assertEqual(page.get_by_role('link',name='Accounts & Access',exact=True).count(),0)
                        self.assertEqual(page.get_by_role('link',name='CryoCheck ↗ · External',exact=True).count(),0)
                        if width>900:self.assertEqual(page.locator('.portal-topbar .aa-my-account-link').evaluate('(e)=>getComputedStyle(e).transitionDuration'),'0s')
                        context.close()
                finally:browser.close()
            with app.app_context():
                self.assertEqual(AccountAuthorizationState.query.count(),0)
                self.assertEqual(AccountScopedRole.query.count(),0)
                self.assertEqual(AccountSecurityEvent.query.count(),0)
