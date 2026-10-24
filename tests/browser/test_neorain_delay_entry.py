"""Real, CSRF-protected Delay Info forms under a gateway Rain MASTER role."""
from datetime import datetime
from pathlib import Path
import unittest

from playwright.sync_api import expect

from tests.browser import test_neorain_compact_overrides as fixtures
from app.extensions import db
from app.models import User, NeoRainDelayInfo, PortalAppAccess, PermissionRule
from app.services.access_control import backfill_default_gateway_node_roles
from app.services.password_policy import set_user_password


class RainDelayEntryBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rain = fixtures.CompactRainBrowserTest
        cls.rain.setUpClass()
        cls.fixture = cls.rain.fixture
        cls.evidence = Path('instance/browser-evidence/rain-delay-entry')
        cls.evidence.mkdir(parents=True, exist_ok=True)
        with cls.fixture.app.app_context():
            user = User(username='rain-master', email='rain-master@example.test',
                        role='watcher', email_verified_at=datetime.now())
            set_user_password(user, 'BrowserFixture123!')
            db.session.add(user)
            db.session.flush()
            backfill_default_gateway_node_roles(user, role='master')
            # Account/app WATCHER must not override the explicit RFD Rain MASTER.
            PortalAppAccess.query.filter_by(user_id=user.id, app_code='neogateway').one().role = 'watcher'
            db.session.commit()

    @classmethod
    def tearDownClass(cls):
        cls.rain.tearDownClass()

    def open_panel(self, page, board, width):
        if width > 900:
            button = page.locator('[data-neorain-delay-toggle]:visible').first
            if button.get_attribute('aria-expanded') != 'true':
                button.click()
            return page.locator('#' + button.get_attribute('aria-controls'))
        button = page.locator('[data-neorain-mobile-row-open]:visible').first
        if button.get_attribute('aria-expanded') != 'true':
            button.click()
        panel = page.locator('[data-neorain-mobile-delay]:visible').first
        if panel.get_attribute('open') is None:
            panel.locator('summary').click()
        return panel

    def add_form(self, panel):
        return panel.locator('form').filter(has=panel.page.locator('input[name="action"][value="add"]')).first

    def test_live_refresh_preserves_master_delay_draft(self):
        browser = self.fixture.pw.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={'width':1920, 'height':1000})
            self.fixture().login(page, 'rain-master')
            self.fixture().ready(page, '/neorain/inbound')
            panel = self.open_panel(page, 'inbound', 1920)
            form = self.add_form(panel)
            form.locator('[name="minutes"]').fill('10')
            form.locator('[name="code"]').fill('RS')
            form.locator('[name="notes"]').fill('Master draft must survive import')
            page.evaluate('window.rainDelayDocument = true')
            fixtures.CompactRainBrowserTest().upstream('arrival', 'block_in', datetime(2026,10,16,8,25))
            root = page.locator('[data-neorain-inbound-live]')
            expect(root).to_have_attribute('data-neorain-refresh-deferred', 'true', timeout=20000)
            self.assertTrue(page.evaluate('window.rainDelayDocument === true'))
            expect(form.locator('[name="notes"]')).to_have_value('Master draft must survive import')
        finally:
            browser.close()

    def exercise(self, engine, width):
        browser = getattr(self.fixture.pw, engine).launch(headless=True)
        try:
            page = browser.new_page(viewport={'width':width,'height':950})
            page.set_default_timeout(7000)
            page.set_default_navigation_timeout(15000)
            page.on('pageerror', lambda error: print('DELAY PAGE ERROR:', str(error), flush=True))
            self.fixture().login(page, 'rain-master')
            for board, kind in (('inbound','arrival'), ('outbound','departure')):
                with self.subTest(engine=engine, board=board):
                    with self.fixture.app.app_context():
                        NeoRainDelayInfo.query.delete()
                        db.session.commit()
                    self.fixture().ready(page, '/neorain/' + board)
                    panel = self.open_panel(page, board, width)
                    add = self.add_form(panel)
                    add.locator('[name="minutes"]').fill('10')
                    add.locator('[name="code"]').fill('RS')
                    add.locator('[name="notes"]').fill('Master saved entry')
                    with page.expect_response(lambda r:'/delay-info' in r.url and r.request.method == 'POST') as response:
                        add.locator('button[type="submit"]').click()
                    self.assertIn(response.value.status, (200,302))
                    if response.value.status == 200:
                        self.assertTrue(response.value.json().get('ok'), response.value.body())
                    if response.value.status == 302:
                        page.wait_for_load_state('domcontentloaded')
                        panel = self.open_panel(page, board, width)
                    update = panel.locator('form').filter(has=page.locator('[name="action"][value="update"]')).first
                    expect(update.locator('button[type="submit"]')).to_be_visible()
                    update.locator('[name="notes"]').fill('Corrected complete notes')
                    with page.expect_response(lambda r:'/delay-info' in r.url and r.request.method == 'POST'):
                        update.locator('button[type="submit"]').click()
                    expect(panel.locator('[data-neorain-delay-feedback]')).to_have_text('DELAY INFO SAVED.')
                    page.reload()
                    panel = self.open_panel(page, board, width)
                    expect(panel.locator('[name="notes"][value="Corrected complete notes"]')).to_be_visible()
                    self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'))
                    # Saving a second delay must not replace another entry's draft.
                    first_notes = panel.locator('[name="notes"][value="Corrected complete notes"]')
                    first_notes.fill('Independent unsaved correction')
                    add = self.add_form(panel)
                    add.locator('[name="minutes"]').fill('3')
                    add.locator('[name="code"]').fill('GL')
                    add.locator('[name="notes"]').fill('Second independent entry')
                    with page.expect_response(lambda r:'/delay-info' in r.url and r.request.method == 'POST') as second:
                        add.locator('button').click()
                    self.assertEqual(second.value.status, 200)
                    expect(panel.locator('[data-neorain-delay-feedback]')).to_have_text('DELAY INFO SAVED.')
                    expect(first_notes).to_have_value('Independent unsaved correction')
                    expect(panel.locator('[name="notes"][value="Second independent entry"]')).to_be_visible()
                    page.screenshot(path=str(self.evidence / f'{engine}-{board}-{width}.png'), full_page=True)
                    for remaining in (1,0):
                        delete = panel.locator('form').filter(has=page.locator('[name="action"][value="delete"]')).first
                        with page.expect_response(lambda r:'/delay-info' in r.url and r.request.method == 'POST'):
                            delete.locator('button[type="submit"]').click()
                        expect(panel.locator('.neorain-delay-existing-row')).to_have_count(remaining)
                    with self.fixture.app.app_context():
                        self.assertEqual(NeoRainDelayInfo.query.count(), 0)
        finally:
            browser.close()

    def test_chromium_master_desktop_crud(self):
        self.exercise('chromium', 1920)

    def test_webkit_master_mobile_crud(self):
        self.exercise('webkit', 390)

    def test_failed_saves_preserve_drafts_and_recover(self):
        for engine, width in [('chromium',1920), ('webkit',390)]:
            browser = getattr(self.fixture.pw, engine).launch(headless=True)
            try:
                page = browser.new_page(viewport={'width':width,'height':950})
                self.fixture().login(page, 'rain-master')
                for board in ('inbound','outbound'):
                    self.fixture().ready(page, '/neorain/' + board)
                    panel = self.open_panel(page, board, width)
                    form = self.add_form(panel)
                    form.locator('[name="minutes"]').fill('-1')
                    form.locator('[name="code"]').fill('RS')
                    form.locator('[name="notes"]').fill('Retained after rejected save')
                    with page.expect_response(lambda r:'/delay-info' in r.url and r.request.method == 'POST') as rejected:
                        form.locator('button').click()
                    self.assertEqual(rejected.value.status, 400)
                    expect(panel.locator('[data-neorain-delay-feedback]')).to_contain_text('positive whole number')
                    expect(form.locator('[name="notes"]')).to_have_value('Retained after rejected save')
                    expect(form.locator('button')).to_be_enabled()
                    form.locator('[name="minutes"]').fill('10')
                    with self.fixture.app.app_context():
                        rule = PermissionRule.query.filter_by(permission_key=f'neorain.{board}.edit').one()
                        rule.minimum_role = 'grandmaster'; db.session.commit()
                    try:
                        with page.expect_response(lambda r:'/delay-info' in r.url and r.request.method == 'POST') as denied:
                            form.locator('button').click()
                        self.assertEqual(denied.value.status, 403)
                        expect(panel.locator('[data-neorain-delay-feedback]')).to_contain_text('does not permit')
                        expect(form.locator('[name="notes"]')).to_have_value('Retained after rejected save')
                    finally:
                        with self.fixture.app.app_context():
                            PermissionRule.query.filter_by(permission_key=f'neorain.{board}.edit').one().minimum_role = 'simulator'
                            db.session.commit()
                    with page.expect_response(lambda r:'/delay-info' in r.url and r.request.method == 'POST') as saved:
                        form.locator('button').click()
                    self.assertEqual(saved.value.status, 200)
                    expect(panel.locator('[data-neorain-delay-feedback]')).to_have_text('DELAY INFO SAVED.')
            finally:
                browser.close()
