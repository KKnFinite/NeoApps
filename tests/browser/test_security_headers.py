"""Enforced CSP with isolated authenticated pages and existing operational saves.

Run sequentially: python -m unittest tests.browser.test_security_headers -v
No production users, browser sessions, integration calls or operational data.
"""
from contextlib import contextmanager
import unittest
from unittest.mock import patch
from weakref import WeakSet

from flask import render_template_string
from flask_login import login_required
from playwright.sync_api import Page, expect

from tests.browser import test_mobile_drawer as fixtures


@contextmanager
def enforced_csp():
    original_create = fixtures.create_app
    original_goto = Page.goto
    original_screenshot = Page.screenshot
    failures = []
    observed = WeakSet()

    def create(*args, **kwargs):
        app = original_create(*args, **kwargs)
        app.config['SECURITY_HEADERS_CSP_ALLOW_NON_PRODUCTION'] = True
        return app

    def goto(page, *args, **kwargs):
        if page not in observed:
            observed.add(page)
            page.on('pageerror', lambda error: failures.append(str(error)))
            page.on('console', lambda message: failures.append(message.text)
                    if message.type == 'error' and any(value in message.text.lower()
                        for value in ['content security policy', 'content-security-policy', 'refused to execute', 'violates the following']) else None)
        response = original_goto(page, *args, **kwargs)
        if response and response.url.startswith(fixtures.MobileDrawerBrowserTest.origin):
            assert 'Content-Security-Policy'.lower() in response.headers, response.url
        return response

    def screenshot(page, *args, **kwargs):
        # Playwright's WebKit screenshot synchronization injects <style>body
        # {}</style> (inPagePrepareForScreenshots). Skip those optional evidence
        # images, retaining every real DOM/action assertion and console check.
        # Never bypass CSP or allowlist the test runner's inline stylesheet.
        if page.context.browser.browser_type.name == 'webkit':
            return b''
        return original_screenshot(page, *args, **kwargs)

    with patch.object(fixtures, 'create_app', create), patch.object(Page, 'goto', goto), \
            patch.object(Page, 'screenshot', screenshot):
        yield failures
    assert not failures, failures


class SecurityHeadersBrowserTest(unittest.TestCase):
    def test_untrusted_scripts_and_inline_handlers_blocked_in_both_engines(self):
        fixture = fixtures.MobileDrawerBrowserTest
        fixture.setUpClass()
        fixture.app.config['SECURITY_HEADERS_CSP_ALLOW_NON_PRODUCTION'] = True

        @fixture.app.get('/_csp-probe')
        @login_required
        def probe():
            return render_template_string('''{% extends "base.html" %}{% block content %}
                <button id="trusted">Trusted action</button>
                {{ markup|safe }}
                <script nonce="{{ csp_nonce() }}">
                    window.trustedRan=true;
                    document.querySelector('#trusted').addEventListener('click',()=>window.trustedClicked=true);
                </script>{% endblock %}''', markup='''
                <script id="injected">window.injectedRan=true;</script>
                <button id="injected-handler" onclick="window.injectedHandler=true">Untrusted action</button>''')
        try:
            for engine in ('chromium', 'webkit'):
                with self.subTest(engine=engine):
                    browser = getattr(fixture.pw, engine).launch(headless=True)
                    try:
                        page = browser.new_page(viewport={'width': 1920, 'height': 900}, service_workers='block')
                        fixture().login(page)
                        messages = []
                        page.on('console', lambda message: messages.append(message.text))
                        response = page.goto(fixture.origin + '/_csp-probe')
                        self.assertIn('content-security-policy', response.headers)
                        self.assertNotIn('content-security-policy-report-only', response.headers)
                        self.assertTrue(page.evaluate('window.trustedRan === true'))
                        self.assertFalse(page.evaluate('window.injectedRan === true'))
                        self.assertEqual(page.locator('#injected').get_attribute('nonce'), None)
                        page.locator('#trusted').click()
                        self.assertTrue(page.evaluate('window.trustedClicked === true'))
                        page.locator('#injected-handler').click()
                        self.assertFalse(page.evaluate('window.injectedHandler === true'))
                        self.assertTrue(any('inline' in message.lower() for message in messages), messages)
                    finally:
                        browser.close()
        finally:
            fixture.tearDownClass()

    def test_authenticated_nodes_navigation_fonts_images_and_scripts(self):
        with enforced_csp():
            fixture = fixtures.MobileDrawerBrowserTest
            fixture.setUpClass()
            try:
                paths = ['/portal', '/rfd', '/admin/users', '/motherbrain',
                         '/neosektor', '/neosektor/live-counts', '/neosektor/tunnel-conductor',
                         '/neoermac', '/neorain', '/neorain/inbound', '/neorain/outbound',
                         '/neoscorpion', '/neoscorpion/fuel-dispatch', '/neoscorpion/fueler',
                         '/neoscorpion/hanzo', '/neosubzero/operations', '/neosubzero/ucc',
                         '/neosubzero/frost-vault', '/neostaffing', '/neostaffing/people']
                for engine in ('chromium', 'webkit'):
                    browser = getattr(fixture.pw, engine).launch(headless=True)
                    try:
                        page = browser.new_page(viewport={'width': 1920, 'height': 900}, service_workers='block')
                        fixture().login(page)
                        for path in paths:
                            with self.subTest(engine=engine, path=path):
                                response = page.goto(fixture.origin + path)
                                self.assertEqual(response.status, 200, path)
                                self.assertIn('content-security-policy', response.headers)
                                self.assertTrue(page.evaluate('!!window.NeoAppsCsrf'))
                                if page.locator('script[src*="/js/live_updates.js"]').count():
                                    self.assertTrue(page.evaluate('!!window.NeoLiveUpdates'))
                                self.assertTrue(page.locator('script[src*="/js/csrf.js"]').count())
                        page.set_viewport_size({'width': 390, 'height': 844})
                        fixture().ready(page, '/rfd')
                        page.locator('[data-drawer-toggle]').click()
                        expect(page.locator('[data-mobile-drawer]')).to_be_visible()
                        page.keyboard.press('Escape')
                        expect(page.locator('[data-mobile-drawer]')).to_be_hidden()
                    finally:
                        browser.close()
            finally:
                fixture.tearDownClass()

    def test_csrf_expired_native_ajax_and_dynamic_forms(self):
        from tests.browser import test_csrf_renewal as csrf
        with enforced_csp():
            case = csrf.CsrfRenewalBrowserTest
            case.setUpClass()
            try:
                case().test_native_expired_submit_preserves_button_and_exactly_one_action()
                case().test_overnight_ajax_and_dynamic_native_forms()
                case().test_renewal_failure_retains_draft_and_sends_nothing()
                case().test_two_tabs_renew_without_invalidating_each_other()
            finally:
                case.tearDownClass()

    def test_real_dispatch_fueler_and_staffing_saves_after_expiry(self):
        from tests.browser import test_csrf_shift_operations as shifts
        with enforced_csp():
            shifts.OperationalShiftCsrfBrowserTest().test_existing_chromium_webkit_fueler_and_dispatch_workflows_after_expiry()
            shifts.OperationalShiftCsrfBrowserTest().test_existing_staffing_editor_validation_and_saves_after_expiry()

    def test_real_rain_inbound_outbound_saves_after_expiry(self):
        from tests.browser import test_csrf_shift_rain as rain
        with enforced_csp():
            case = rain.RainShiftCsrfBrowserTest
            case.setUpClass()
            try:
                case().test_chromium_aged_master_inbound_outbound_actual_crud()
                case().test_webkit_aged_master_inbound_outbound_actual_crud()
            finally:
                case.tearDownClass()

    def test_real_ermac_missions_pulls_and_live_refresh(self):
        from tests.browser import test_neoermac_flight_identity as ermac
        with enforced_csp():
            ermac.FlightIdentityBrowserTest().test_native_editor_door_pulls_and_live_refresh_desktop_mobile()

    def test_real_subzero_operations_and_ucc(self):
        from tests.browser import test_neosubzero_operations as operations
        with enforced_csp():
            case = operations.OperationsBrowserTest
            case.setUpClass()
            try:
                case().test_layouts_preferences_forms_live_drafts_and_tail_identity()
            finally:
                case.tearDownClass()
            case = operations.fixtures.UccBrowserTest
            case.setUpClass()
            try:
                case().test_ucc_physical_board_mobile_staffing_live_and_tv()
            finally:
                case.tearDownClass()

    def test_real_sektor_controls_and_live_screens(self):
        from app.extensions import db
        from app.models import NeoSektorOperationalSetting
        from app.services.access_control import ensure_default_gateway_and_nodes
        from app.services.neosektor_live_counts import apply_standalone_compat_values
        from tests.test_neosektor_integration_modes import _complete_sheet_values
        with enforced_csp():
            fixture = fixtures.MobileDrawerBrowserTest
            fixture.setUpClass()
            try:
                with fixture.app.app_context():
                    gateway = ensure_default_gateway_and_nodes()
                    db.session.add(NeoSektorOperationalSetting(
                        gateway_id=gateway.id, gateway_code=gateway.code,
                        integration_mode='neo_only'))
                    apply_standalone_compat_values(gateway, _complete_sheet_values())
                    db.session.commit()
                for engine, side in (('chromium', 'ebm'), ('webkit', 'wbm')):
                    browser = getattr(fixture.pw, engine).launch(headless=True)
                    try:
                        page = browser.new_page(viewport={'width': 390, 'height': 844})
                        fixture().login(page)
                        fixture().ready(page, '/neosektor/' + side)
                        root = page.locator('[data-ballmat-operations]')
                        count = page.locator('input[data-bm-value="first:total"]')
                        before = int(count.input_value())
                        update_url = fixture.origin + root.get_attribute('data-update-url')
                        request_url = fixture.origin + root.get_attribute('data-mode-request-url')
                        with page.expect_response(lambda r: r.request.method == 'POST'
                                and r.url == update_url) as saved:
                            page.locator('[data-bm-step="1"][data-metric="first"][data-position="total"]').click()
                        self.assertEqual(saved.value.status, 200)
                        expect(count).to_have_value(str(before + 1))
                        with page.expect_response(lambda r: r.request.method == 'POST'
                                and r.url == request_url) as requested:
                            page.locator('[data-bm-mode="2"]').click()
                        self.assertEqual(requested.value.status, 200)
                        # Current policy requests approval; it does not switch
                        # mode immediately as the older UI fixture assumed.
                        expect(page.locator('[data-bm-mode-status]')).to_contain_text('REQUEST 2 PENDING')
                        expect(page.locator('[data-ballmat-mobile]')).to_have_attribute('data-mode', '1')
                    finally:
                        browser.close()
            finally:
                fixture.tearDownClass()
