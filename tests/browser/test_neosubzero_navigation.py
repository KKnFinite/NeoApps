"""Actual shared navigation at desktop/mobile widths; isolated authenticated data."""
import unittest
import re
from pathlib import Path
from playwright.sync_api import expect
from app.extensions import db
from app.models import User, StaffingPerson
from tests.browser import test_neosubzero_ucc as fixtures
from tests.test_neosubzero_navigation import HREFS, INTERNAL_LINKS, LABELS


class SubZeroNavigationBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.UccBrowserTest.setUpClass.__func__(cls)
        with cls.app.app_context():
            User.query.filter_by(username='drawer-admin').one().employee_id = db.session.get(StaffingPerson, cls.people[0]).employee_id
            db.session.commit()
    tearDownClass = classmethod(fixtures.UccBrowserTest.tearDownClass.__func__)
    login = fixtures.UccBrowserTest.login
    ready = fixtures.UccBrowserTest.ready

    def test_desktop_mobile_collapsed_links_and_direct_routes(self):
        evidence = Path('instance/browser-evidence/subzero-navigation').resolve()
        evidence.mkdir(parents=True, exist_ok=True)
        for engine in ('chromium', 'webkit'):
            browser = getattr(self.pw, engine).launch()
            context = browser.new_context(viewport={'width':1440, 'height':1000})
            context.route('https://cryocheck.onrender.com/**', lambda route: route.fulfill(body='CryoCheck fixture'))
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            self.login(page)
            self.ready(page, '/neosubzero/ucc')
            for width in (1440, 1024, 768, 430, 375, 320):
                page.set_viewport_size({'width':width, 'height':1000})
                page.wait_for_function('(w)=>document.documentElement.clientWidth===w', arg=width)
                if width > 900:
                    menu = page.locator('.node-desktop-side-menu')
                else:
                    page.locator('[data-drawer-toggle]').click()
                    page.locator('[data-mobile-drawer]').evaluate('async el => { await Promise.all(el.getAnimations().map(animation => animation.finished)); }')
                    menu = page.locator('[data-drawer-view="menu"] nav[aria-label="NeoSub-Zero screens"]')
                expect(menu).to_be_visible()
                self.assertEqual(menu.locator('a').evaluate_all('(els)=>els.map(el=>el.getAttribute("aria-label"))'), LABELS)
                self.assertEqual(menu.locator('a').evaluate_all('(els)=>els.map(el=>el.getAttribute("href"))'), HREFS)
                self.assertEqual(menu.locator(':scope > *').evaluate_all('(els)=>els.map(e=>e.getAttribute("aria-label"))'), LABELS)
                expect(menu.locator('[aria-current="page"]')).to_have_attribute('aria-label', 'DUCC')
                external = menu.get_by_role('link', name='CryoCheck')
                expect(external).to_have_attribute('target', '_blank')
                expect(external).to_have_attribute('rel', 'noopener noreferrer')
                if width <= 900:
                    self.assertNotEqual(menu.locator('a[aria-current="page"]').evaluate('e=>getComputedStyle(e).borderColor'), menu.locator('a').nth(1).evaluate('e=>getComputedStyle(e).borderColor'))
                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth-innerWidth'), 1)
                bounds = menu.bounding_box()
                self.assertGreaterEqual(bounds['x'], 0)
                self.assertLessEqual(bounds['x'] + bounds['width'], width)
                page.screenshot(path=str(evidence/f'{engine}-{width}.png'))
                if width in (1440, 375):
                    with context.expect_page() as popup:
                        external.click()
                    popup.value.wait_for_url('https://cryocheck.onrender.com/')
                    self.assertTrue(popup.value.evaluate('window.opener === null'))
                    popup.value.close()
                if width <= 900 and width != 375:
                    page.locator('[data-drawer-close]').click()
            page.set_viewport_size({'width':1440, 'height':1000})
            page.locator('[data-operational-sidebar-toggle]').click()
            expect(page.locator('body')).to_have_class(re.compile('operational-sidebar-collapsed'))
            self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth-innerWidth'), 1)
            page.screenshot(path=str(evidence/f'{engine}-collapsed.png'))
            for label, path in INTERNAL_LINKS:
                self.ready(page, '/neosubzero/' + path)
                expect(page.locator('.node-desktop-side-menu [aria-current="page"]')).to_have_attribute('aria-label', label)
                expect(page.locator('.node-desktop-side-menu a')).to_have_count(len(LABELS))
                self.assertEqual(page.locator('nav[aria-label="NeoSub-Zero navigation"] a').evaluate_all(
                    '(links) => links.map(link => link.getAttribute("href"))'), HREFS)
            self.ready(page, '/neosubzero/pretreat')
            expect(page.locator('.node-desktop-side-menu [aria-current="page"]')).to_have_attribute(
                'aria-label', 'Deice Dispatch')
            self.assertFalse(errors, errors)
            context.close()
            browser.close()
