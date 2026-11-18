"""Scoped administration using real CSRF forms on synthetic desktop/mobile data."""
from pathlib import Path
from threading import Thread
from unittest import TestCase
from unittest.mock import patch
import tempfile
import logging
from playwright.sync_api import sync_playwright,expect
from werkzeug.serving import make_server
from app import create_app
from app.extensions import db
from app.models import User,PortalAppAccess,PermissionRule,AccountAuthorizationState
from app.services.account_foundation_schema import ensure_account_history_guards
from tests.test_scoped_authorization import prepare_scope_fixture
from tests.test_account_security_phase1 import fixture_user,PASSWORD
from tests.browser import test_accounts_workspace as workspace


class ScopedAuthorizationBrowserTest(TestCase):
    def test_owned_administration_chromium_webkit_desktop_mobile(self):
        evidence=Path('instance/browser-evidence/accounts-phase5').resolve();evidence.mkdir(parents=True,exist_ok=True)
        fixture=Path(tempfile.mkdtemp(prefix='neoapps-scopes-phase5-'))/'fixture.db'
        (evidence/'latest-fixture-path.txt').write_text(str(fixture),encoding='utf-8')
        app=create_app(type('Config',(),dict(SECRET_KEY='phase5-browser',TESTING=True,AUTO_BOOTSTRAP_DATABASE=False,
            CSRF_PROTECT_TESTING=True,SESSION_COOKIE_SECURE=False,AUTH_RATE_LIMIT_ENABLED=False,
            SQLALCHEMY_DATABASE_URI='sqlite:///'+fixture.as_posix(),SQLALCHEMY_TRACK_MODIFICATIONS=False,
            FLIGHT_API_ENABLED=False,GOOGLE_MOTHERBRAIN_IMPORT_ENABLED=False,GOOGLE_MOTHERBRAIN_READER_ENABLED=False,
            LIVE_SCREEN_REFRESH_INTERVAL_MS=0)))
        with app.app_context():
            db.create_all();ensure_account_history_guards(db.session.connection())
            prepare_scope_fixture()
            admin=fixture_user('scope-app-admin',platform='master')
            db.session.add(PortalAppAccess(user_id=admin.id,app_code='neostaffing',role='grandmaster',status='approved',is_active=True))
            subjects={}
            for engine in ('chromium','webkit'):
                for width in (1280,390):
                    subject=fixture_user(f'scope-{engine}-{width}')
                    db.session.add(PortalAppAccess(user_id=subject.id,app_code='neostaffing',role='watcher',status='pending',is_active=True))
                    subjects[(engine,width)]=subject.id
            db.session.commit()
            rule_id=PermissionRule.query.filter_by(permission_key='neoscorpion.fuel_dispatch.edit').one().id
        server=make_server('127.0.0.1',0,app,threaded=True);worker=Thread(target=server.serve_forever,daemon=True)
        logging.getLogger('werkzeug').setLevel(logging.ERROR);worker.start();origin=f'http://127.0.0.1:{server.server_port}'
        try:
            with patch('requests.sessions.Session.request',side_effect=AssertionError('External provider forbidden')),patch('app.auth.routes.email_service.send_access_approved',return_value={'sent':False}),sync_playwright() as pw:
                for engine in ('chromium','webkit'):
                    browser=getattr(pw,engine).launch()
                    try:
                        for width in (1280,390):
                            with self.subTest(engine=engine,width=width):
                                context=browser.new_context(viewport={'width':width,'height':900 if width==1280 else 844})
                                page=context.new_page();page.set_default_timeout(15000);errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                                workspace.AccountsWorkspaceBrowserTest.login(page,origin,'scope-root@example.test')
                                page.goto(origin+'/accounts/security/scoped-cutover')
                                if engine=='chromium' and width==1280:
                                    expect(page.get_by_role('heading',name='Complete migration review')).to_be_visible()
                                    page.screenshot(path=str(evidence/'reviewed-cutover.png'),full_page=True)
                                    page.locator('[name=current_password]').fill(PASSWORD)
                                    page.locator('[name=confirmation]').fill('ACTIVATE SCOPED PERMISSIONS')
                                    with page.expect_navigation(wait_until='domcontentloaded'):page.get_by_role('button',name='Activate complete scoped authorization').click()
                                expect(page.get_by_role('heading',name='Scoped authorization ACTIVE')).to_be_visible()
                                workspace.AccountsWorkspaceBrowserTest.login(page,origin,'scope-master@example.test')
                                self.assertEqual(page.goto(origin+'/neoscorpion/roles').status,200)
                                subject=subjects[(engine,width)]
                                form=page.locator('form').filter(has=page.locator(f'[name=user_id][value="{subject}"]'))
                                self.assertEqual(form.locator('option[value=master],option[value=grandmaster]').count(),0)
                                stale=form.evaluate('(f)=>Object.fromEntries(new FormData(f))')
                                form.locator('[name=role]').select_option('simulator');form.locator('[name=reason]').fill('Synthetic delegated operational duties')
                                with page.expect_navigation(wait_until='domcontentloaded'):form.get_by_role('button',name='Save role',exact=True).click()
                                stale['role']='operator';stale['reason']='Stale replay must reject'
                                self.assertEqual(page.request.post(origin+'/neoscorpion/roles',form=stale).status,409)
                                self.assertFalse(page.evaluate('document.documentElement.scrollWidth>innerWidth+1'))
                                page.screenshot(path=str(evidence/f'node-roles-{engine}-{width}.png'),full_page=True)
                                page.goto(origin+'/neoscorpion/permissions')
                                self.assertEqual(page.locator('[name^=minimum_role_]').filter(has_text='Grandmaster').count(),0)
                                page.locator(f'[name=minimum_role_{rule_id}]').select_option('master' if width==1280 else 'simulator')
                                with page.expect_navigation(wait_until='domcontentloaded'):page.get_by_role('button',name='Save permitted thresholds').click()
                                self.assertFalse(page.evaluate('document.documentElement.scrollWidth>innerWidth+1'))
                                page.screenshot(path=str(evidence/f'node-permissions-{engine}-{width}.png'),full_page=True)
                                self.assertEqual(page.request.get(origin+'/neoapps/roles').status,403)
                                self.assertEqual(page.request.get(origin+'/neostaffing/accounts').status,403)
                                workspace.AccountsWorkspaceBrowserTest.login(page,origin,'scope-app-admin@example.test')
                                self.assertEqual(page.goto(origin+'/neostaffing/accounts').status,200)
                                approve=page.locator('form').filter(has=page.locator(f'[name=user_id][value="{subject}"]'))
                                approve.locator('[name=role]').select_option('operator');approve.locator('[name=reason]').fill('Synthetic Staffing request reviewed')
                                with page.expect_navigation(wait_until='domcontentloaded'):approve.get_by_role('button',name='Approve / save',exact=True).click()
                                self.assertFalse(page.evaluate('document.documentElement.scrollWidth>innerWidth+1'))
                                page.screenshot(path=str(evidence/f'app-access-{engine}-{width}.png'),full_page=True)
                                self.assertEqual(page.request.get(origin+'/neogateway/accounts').status,403)
                                self.assertEqual(page.request.post(origin+'/neostaffing/accounts',form={'user_id':str(subject)}).status,400)
                                self.assertEqual(errors,[])
                                context.close()
                    finally:browser.close()
            with app.app_context():
                self.assertTrue(db.session.get(AccountAuthorizationState,1).active)
                self.assertEqual(User.query.filter_by(role='grandmaster').count(),2)
                for subject in subjects.values():self.assertEqual(PortalAppAccess.query.filter_by(user_id=subject,app_code='neostaffing').one().status,'approved')
        finally:
            server.shutdown();worker.join(5)
            with app.app_context():db.session.remove();db.engine.dispose()
