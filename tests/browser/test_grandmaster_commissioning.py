"""Isolated desktop/mobile protected commissioning; no production requests."""
from pathlib import Path
from threading import Thread
from uuid import uuid4
import logging
import unittest
from unittest.mock import patch

from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server

from app import create_app
from app.extensions import db
from app.models import AccountSecurityEvent, User
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.account_security_history import ensure_account_security_history
from app.services.grandmaster_commissioning import scoped_grant_fingerprint
from app.services.permission_rules import ensure_default_permission_rules
from tests.test_account_security_phase1 import fixture_user, PASSWORD


class GrandmasterCommissioningBrowserTest(unittest.TestCase):
    def test_desktop_mobile_review_reauthentication_and_explicit_submission(self):
        evidence=Path("instance/browser-evidence/commissioning").resolve()
        evidence.mkdir(parents=True,exist_ok=True)
        database=evidence/("fixture-"+uuid4().hex+".db")
        app=create_app(type("Config",(),dict(SECRET_KEY="commissioning-browser-fixture",TESTING=True,
            AUTO_BOOTSTRAP_DATABASE=False,CSRF_PROTECT_TESTING=True,SESSION_COOKIE_SECURE=False,
            AUTH_RATE_LIMIT_ENABLED=False,SQLALCHEMY_DATABASE_URI="sqlite:///"+database.as_posix(),
            SQLALCHEMY_TRACK_MODIFICATIONS=False,FLIGHT_API_ENABLED=False,
            GOOGLE_MOTHERBRAIN_IMPORT_ENABLED=False,GOOGLE_MOTHERBRAIN_READER_ENABLED=False,
            LIVE_SCREEN_REFRESH_INTERVAL_MS=0)))
        with app.app_context():
            db.create_all()
            ensure_account_security_history(db.session.connection())
            ensure_default_gateway_and_nodes()
            ensure_default_permission_rules()
            fixture_user("browser-owner",platform="grandmaster")
            ids=[fixture_user("browser-backup-"+str(index)).id for index in range(2)]
            db.session.commit()
        server=make_server("127.0.0.1",0,app,threaded=True)
        worker=Thread(target=server.serve_forever,daemon=True)
        logging.getLogger("werkzeug").setLevel(logging.ERROR)
        worker.start()
        origin=f"http://127.0.0.1:{server.server_port}"
        try:
            with patch("requests.sessions.Session.request",side_effect=AssertionError("No external providers")),sync_playwright() as pw:
                browser=pw.chromium.launch()
                try:
                    for index,viewport in enumerate(({"width":1280,"height":900},{"width":390,"height":844})):
                        context=browser.new_context(viewport=viewport)
                        page=context.new_page()
                        target=ids[index]
                        with app.app_context():
                            grants=scoped_grant_fingerprint(target)
                        page.goto(origin+"/login")
                        page.locator('input[name="email"]').fill("browser-owner@example.test")
                        page.locator('input[name="password"]').fill(PASSWORD)
                        page.locator('button[type="submit"]').first.click()
                        page.wait_for_url(lambda url:"/login" not in url)
                        self.assertEqual(page.goto(origin+f"/admin/users/{target}/commission-grandmaster").status,200)
                        page.locator('[name="confirmed_target_id"]').fill(str(target))
                        page.locator('[name="reason"]').fill("Commission synthetic verified backup")
                        page.locator('[name="confirm_platform_authority"]').check()
                        page.locator('[name="current_password"]').fill("WrongBrowserPassword!")
                        with page.expect_navigation(wait_until="domcontentloaded") as rejected:
                            page.get_by_role("button",name="PROMOTE TO NEOAPPS GRANDMASTER",exact=True).click()
                        self.assertEqual(rejected.value.status,400)
                        expect(page.get_by_text("Current password could not be verified.",exact=False)).to_be_visible()
                        self.assertEqual(page.locator('[name="current_password"]').input_value(),"")
                        page.locator('[name="confirmed_target_id"]').fill(str(target))
                        page.locator('[name="reason"]').fill("Commission synthetic verified backup")
                        page.locator('[name="confirm_platform_authority"]').check()
                        self.assertFalse(page.evaluate("document.documentElement.scrollWidth > innerWidth+1"))
                        page.screenshot(path=str(evidence/f'review-{viewport["width"]}.png'),full_page=True)
                        page.locator('[name="current_password"]').fill(PASSWORD)
                        page.get_by_role("button",name="PROMOTE TO NEOAPPS GRANDMASTER",exact=True).click()
                        page.wait_for_url(lambda url:f"/accounts/people/{target}" in url and "tab=security" in url)
                        self.assertTrue(page.get_by_text("NeoApps platform Grandmaster commissioned.",exact=False).is_visible())
                        self.assertEqual(page.locator('dt',has_text="NEOAPPS PLATFORM ROLE").locator('..').locator('dd').inner_text(),"Grandmaster")
                        with app.app_context():
                            self.assertEqual(db.session.get(User,target).role,"grandmaster")
                            self.assertEqual(scoped_grant_fingerprint(target),grants)
                            self.assertEqual(AccountSecurityEvent.query.filter_by(subject_user_id=target,action="platform_grandmaster_commissioned").count(),1)
                        page.screenshot(path=str(evidence/f'commissioned-{viewport["width"]}.png'),full_page=True)
                        context.close()
                finally:
                    browser.close()
        finally:
            server.shutdown()
            worker.join(timeout=5)
            with app.app_context():
                db.session.remove()
                db.engine.dispose()
            # Preserve synthetic database and screenshots; no cleanup of files.
