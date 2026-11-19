"""Actual synthetic desktop/mobile confirmation and persistent search flow."""
from pathlib import Path
from unittest import TestCase

from playwright.sync_api import expect

from app.extensions import db
from app.models import AccountSecurityEvent,User
from app.services.account_foundation_schema import ensure_account_history_guards
from tests.browser import test_mobile_drawer as fixtures
from tests.browser.test_security_headers import enforced_csp
from tests.test_account_security_phase1 import fixture_user


class AccountDeletionSearchBrowserTest(TestCase):
    def test_desktop_chromium_mobile_webkit_deletion_and_shared_search(self):
        fixture=fixtures.MobileDrawerBrowserTest
        evidence=Path('instance/browser-evidence/account-deletion-search');evidence.mkdir(parents=True,exist_ok=True)
        with enforced_csp():
            fixture.setUpClass()
            try:
                fixture.app.config['AUTH_RATE_LIMIT_ENABLED']=False
                with fixture.app.app_context():
                    ensure_account_history_guards(db.session.connection())
                    targets={engine:fixture_user('delete-'+engine).id for engine in ('chromium','webkit')}
                    survivor=fixture_user('search-survivor')
                    survivor.employee_id='0123456789';survivor_id=survivor.id
                    db.session.commit()
                for engine,width in (('chromium',1280),('webkit',390)):
                    with self.subTest(engine=engine,width=width):
                        browser=getattr(fixture.pw,engine).launch(headless=True)
                        try:
                            page=browser.new_page(viewport={'width':width,'height':900 if width>500 else 844},service_workers='block')
                            page.goto(fixture.origin+'/login')
                            page.locator('[name=email]').fill('drawer-admin@example.test')
                            page.locator('[name=password]').fill('BrowserFixture123!')
                            with page.expect_navigation():page.locator('button[type=submit]').first.click()
                            target=targets[engine]
                            page.goto(fixture.origin+f'/accounts/people/{target}?tab=security')
                            search=page.get_by_role('search',name='People search')
                            expect(search).to_be_visible()
                            self.assertEqual(search.count(),1)
                            self.assertTrue(page.evaluate("document.querySelector('form[role=search]').getBoundingClientRect().top >= document.querySelector('nav[aria-label=\"Accounts & Access\"]').getBoundingClientRect().bottom"))
                            self.assertFalse(page.evaluate('document.documentElement.scrollWidth > innerWidth + 1'))
                            page.screenshot(path=str(evidence/f'{engine}-security.png'),full_page=True)
                            if engine=='chromium':
                                page.set_viewport_size({'width':390,'height':844})
                                self.assertFalse(page.evaluate('document.documentElement.scrollWidth > innerWidth + 1'))
                                page.screenshot(path=str(evidence/'chromium-mobile-security.png'),full_page=True)
                                page.set_viewport_size({'width':width,'height':900})
                            form=page.locator('form').filter(has=page.locator('[name=confirm_delete]'))
                            form.locator('[name=confirmed_user_id]').fill(str(target))
                            form.locator('[name=confirm_delete]').check()
                            posts=[]
                            page.on('request',lambda r:posts.append(r.url) if r.method=='POST' and '/delete' in r.url else None)
                            page.once('dialog',lambda dialog:dialog.dismiss())
                            form.get_by_role('button',name='DELETE ACCOUNT',exact=True).click()
                            self.assertEqual(posts,[])
                            with fixture.app.app_context():self.assertTrue(db.session.get(User,target).is_active)
                            page.once('dialog',lambda dialog:dialog.accept())
                            with page.expect_navigation():form.get_by_role('button',name='DELETE ACCOUNT',exact=True).click()
                            self.assertEqual(page.url,fixture.origin+'/accounts')
                            expect(page.get_by_text(f'Account {target} deleted.',exact=False)).to_be_visible()
                            self.assertEqual(page.locator(f'.aa-person[href="/accounts/people/{target}"]').count(),0)
                            with fixture.app.app_context():
                                self.assertFalse(db.session.get(User,target).is_active)
                                self.assertEqual(AccountSecurityEvent.query.filter_by(subject_user_id=target,action='account_deleted').count(),1)
                            page.goto(fixture.origin+'/accounts/requests')
                            search=page.get_by_role('search',name='People search')
                            search.locator('[name=q]').fill('0123456789')
                            with page.expect_navigation():search.get_by_role('button',name='Search',exact=True).click()
                            expect(page.locator(f'.aa-person[href="/accounts/people/{survivor_id}"]')).to_be_visible()
                            self.assertFalse(page.evaluate('document.documentElement.scrollWidth > innerWidth + 1'))
                            page.screenshot(path=str(evidence/f'{engine}-people-search.png'),full_page=True)
                        finally:browser.close()
            finally:fixture.tearDownClass()
