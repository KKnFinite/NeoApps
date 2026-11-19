"""Real desktop/mobile registration, review, reapplication and app preferences."""
from pathlib import Path
from threading import Thread
from uuid import uuid4
from unittest import TestCase
from unittest.mock import patch
import logging
import tempfile

from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server

from app import create_app
from app.extensions import db
from app.models import AccountAccessDecision, AccountAccessRequest, AccountScopedRole, PortalAppAccess, User
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.account_foundation_schema import ensure_account_history_guards
from app.services.permission_rules import ensure_default_permission_rules
from tests.test_account_security_phase1 import fixture_user, PASSWORD


class AccountsWorkspaceBrowserTest(TestCase):
    def test_chromium_and_webkit_desktop_mobile_workflows(self):
        evidence = Path("instance/browser-evidence/accounts-phase3").resolve()
        evidence.mkdir(parents=True, exist_ok=True)
        # Local temporary storage avoids network-drive SQLite fsync costs.
        # Preserve the fixture and record its path; never clean up prior runs.
        fixture = Path(tempfile.mkdtemp(prefix="neoapps-accounts-phase3-")) / "fixture.db"
        (evidence / "latest-fixture-path.txt").write_text(str(fixture), encoding="utf-8")
        app = create_app(type("Config", (), dict(SECRET_KEY="phase3-browser", TESTING=True,
            AUTO_BOOTSTRAP_DATABASE=False, CSRF_PROTECT_TESTING=True, SESSION_COOKIE_SECURE=False,
            AUTH_RATE_LIMIT_ENABLED=False, SQLALCHEMY_DATABASE_URI="sqlite:///"+fixture.as_posix(),
            SQLALCHEMY_TRACK_MODIFICATIONS=False, FLIGHT_API_ENABLED=False,
            GOOGLE_MOTHERBRAIN_IMPORT_ENABLED=False, GOOGLE_MOTHERBRAIN_READER_ENABLED=False,
            LIVE_SCREEN_REFRESH_INTERVAL_MS=0)))
        with app.app_context():
            db.create_all()
            ensure_account_history_guards(db.session.connection())
            ensure_default_gateway_and_nodes()
            ensure_default_permission_rules()
            fixture_user("browser-owner", platform="grandmaster")
            db.session.commit()
        server = make_server("127.0.0.1", 0, app, threaded=True)
        worker = Thread(target=server.serve_forever, daemon=True)
        logging.getLogger("werkzeug").setLevel(logging.ERROR)
        worker.start()
        origin = f"http://127.0.0.1:{server.server_port}"
        verification = {}
        def capture(user, token):
            verification[user.email] = token
            return {"sent": False}
        try:
            with patch("requests.sessions.Session.request", side_effect=AssertionError("External provider forbidden")), patch("app.auth.routes.email_service.send_email_verification", side_effect=capture), patch("app.auth.routes.email_service.send_access_approved", return_value={"sent": False}), sync_playwright() as pw:
                for engine in ("chromium", "webkit"):
                    browser = getattr(pw, engine).launch()
                    try:
                        for width in (1280, 390):
                            with self.subTest(engine=engine, width=width):
                                context = browser.new_context(viewport={"width": width, "height": 900 if width == 1280 else 844})
                                page = context.new_page()
                                errors = []
                                page.on("pageerror", lambda error: errors.append(str(error)))
                                prefix = f"{engine}-{width}"
                                email = prefix+"@example.test"
                                page.goto(origin+"/create-account")
                                employee_id = f'{(1000000 if engine == "chromium" else 2000000) + width:07d}'
                                for key,value in dict(first_name=prefix, last_name="Applicant", email=email, employee_id=employee_id,
                                    supervisor_name="Fixture manager", work_area="Ramp", access_reason="Staffing and gateway responsibilities", password=PASSWORD, confirm_password=PASSWORD).items():
                                    page.locator(f'[name="{key}"]').fill(value)
                                page.locator('[name="app_codes"][value="neostaffing"]').check()
                                self.assertEqual(page.locator('[name="app_codes"][value="neobid"]').count(), 0)
                                self.assertFalse(page.evaluate("document.documentElement.scrollWidth > innerWidth + 1"))
                                if page.locator('.aa-platform-header').count():
                                    self.assertEqual(page.locator('.mobile-topbar:visible').count(),0)
                                    self.assertEqual(page.locator('.aa-platform-header').count(),1)
                                page.screenshot(path=str(evidence/f"registration-{prefix}.png"), full_page=True)
                                page.get_by_role("button", name="Create account & request access").click()
                                expect(page.get_by_role("heading", name="Verify Your Email", exact=True)).to_be_visible()
                                self.assertEqual(page.goto(origin+"/verify-email/"+verification[email]).status, 200)
                                with app.app_context():
                                    target = User.query.filter_by(email=email).one().id
                                self.login(page, origin, "browser-owner@example.test")
                                page.goto(origin+"/accounts/requests")
                                expect(page.locator('.aa-request', has_text=email).first).to_be_visible()
                                page.goto(origin+f"/accounts/people/{target}?tab=access")
                                gateway = page.locator('form[action$="/application"]').filter(has=page.locator('[name="app_code"][value="neogateway"]'))
                                gateway.locator('[name="reason"]').fill("Additional justification required")
                                with page.expect_navigation(wait_until="domcontentloaded"):
                                    gateway.get_by_role("button", name="Deny", exact=True).click()
                                page.wait_for_url(lambda url:"tab=access" in url)
                                staffing = page.locator('form[action$="/application"]').filter(has=page.locator('[name="app_code"][value="neostaffing"]'))
                                staffing.locator('[name="reason"]').fill("Verified staffing duties")
                                with page.expect_navigation(wait_until="domcontentloaded"):
                                    staffing.get_by_role("button", name="Approve / save role", exact=True).click()
                                page.wait_for_url(lambda url:"tab=access" in url)
                                self.assertFalse(page.evaluate("document.documentElement.scrollWidth > innerWidth + 1"))
                                page.screenshot(path=str(evidence/f"profile-{prefix}.png"), full_page=True)
                                self.login(page, origin, email)
                                page.goto(origin+"/my-account")
                                request_form = page.locator('form[action$="/app-access"]').filter(has=page.locator('[name="app_code"][value="neogateway"]'))
                                request_form.locator('[name="reason"]').fill("New gateway responsibilities confirmed by manager")
                                with page.expect_navigation(wait_until="domcontentloaded"):
                                    request_form.get_by_role("button", name="Reapply", exact=True).click()
                                expect(page.get_by_text("Pending review", exact=True).first).to_be_visible()
                                self.login(page, origin, "browser-owner@example.test")
                                page.goto(origin+f"/accounts/people/{target}?tab=access")
                                gateway = page.locator('form[action$="/application"]').filter(has=page.locator('[name="app_code"][value="neogateway"]'))
                                gateway.locator('[name="reason"]').fill("Verified gateway responsibilities")
                                with page.expect_navigation(wait_until="domcontentloaded"):
                                    gateway.get_by_role("button", name="Approve / save role", exact=True).click()
                                page.wait_for_url(lambda url:"tab=access" in url)
                                self.login(page, origin, email)
                                page.goto(origin+"/my-account")
                                page.locator('[name="preferred_app"]').select_option("neostaffing")
                                page.locator('[name="show_cryocheck"]').check()
                                with page.expect_navigation(wait_until="domcontentloaded"):
                                    page.get_by_role("button", name="Save preferences", exact=True).click()
                                expect(page.get_by_text("Preferences saved.", exact=True)).to_be_visible()
                                self.assertFalse(page.evaluate("document.documentElement.scrollWidth > innerWidth + 1"))
                                page.screenshot(path=str(evidence/f"my-account-{prefix}.png"), full_page=True)
                                page.goto(origin+"/portal")
                                expect(page.get_by_role("link", name="CryoCheck ↗ · External")).to_be_visible()
                                self.login(page, origin, email)
                                self.assertTrue(page.url.endswith("/neostaffing"))
                                with app.app_context():
                                    self.assertEqual(AccountAccessRequest.query.filter_by(user_id=target).count(), 3)
                                    ids = [r.id for r in AccountAccessRequest.query.filter_by(user_id=target)]
                                    self.assertEqual(AccountAccessDecision.query.filter(AccountAccessDecision.request_id.in_(ids)).count(), 3)
                                    self.assertEqual(AccountScopedRole.query.count(), 0)
                                self.login(page, origin, "browser-owner@example.test")
                                page.goto(origin+f"/accounts/people/{target}?tab=security")
                                page.get_by_text("Change account type", exact=True).click()
                                page.once("dialog", lambda dialog: dialog.accept())
                                with page.expect_navigation(wait_until="domcontentloaded"):
                                    page.get_by_role("button", name="Convert to Fuel Truck Driver", exact=True).click()
                                page.wait_for_url(lambda url:f"/accounts/people/{target}" in url)
                                page.goto(origin+f"/accounts/people/{target}?tab=security")
                                page.get_by_text("Change account type", exact=True).click()
                                page.once("dialog", lambda dialog: dialog.accept())
                                with page.expect_navigation(wait_until="domcontentloaded"):
                                    page.get_by_role("button", name="Convert to full NeoApps account", exact=True).click()
                                page.wait_for_url(lambda url:f"/accounts/people/{target}" in url)
                                with app.app_context():
                                    self.assertIsNone(db.session.get(User,target).scorpion_vendor)
                                    self.assertEqual(db.session.get(User,target).email,email)
                                    self.assertEqual(PortalAppAccess.query.filter_by(user_id=target,app_code="neogateway").one().status,"pending")
                                self.assertEqual(errors, [])
                                context.close()
                    finally:
                        browser.close()
        finally:
            server.shutdown()
            worker.join(5)
            with app.app_context():
                db.session.remove()
                db.engine.dispose()

    @staticmethod
    def login(page, origin, email):
        # Use the real CSRF-protected logout, without a session-cookie shortcut.
        if "/create-account" not in page.url and "/verify-email/" not in page.url:
            page.goto(origin+"/my-account")
            logout = page.locator('form[action="/logout"] button').first
            if logout.count():
                logout.click()
                page.wait_for_url("**/login")
        page.goto(origin+"/login")
        page.locator('[name="email"]').fill(email)
        page.locator('[name="password"]').fill(PASSWORD)
        page.locator('form button[type="submit"]').first.click()
        page.wait_for_url(lambda url:"/login" not in url)
