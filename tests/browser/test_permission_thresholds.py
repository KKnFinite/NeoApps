"""Real permission forms at desktop and phone widths using synthetic policy rows."""
import unittest

from playwright.sync_api import expect

from app.extensions import db
from app.models import AccountAuthorizationState, AccountSecurityEvent, PermissionRule, User
from tests.browser import test_mobile_drawer as fixture


ROLES = ["watcher", "operator", "simulator", "master"]
OPERATIONAL_KEY = "neoscorpion.fueler.edit"


class PermissionThresholdBrowserTest(unittest.TestCase):
    def test_legacy_and_scoped_selectors_use_ordered_choices_and_keep_floors(self):
        env = fixture.MobileDrawerBrowserTest()
        env.setUpClass()
        self.addCleanup(env.tearDownClass)
        with env.app.app_context():
            rules = {r.permission_key: r.id for r in PermissionRule.query.all()}
            db.session.get(PermissionRule, rules[OPERATIONAL_KEY]).minimum_role = "master"
            db.session.commit()

        for engine, width in (("chromium", 1440), ("webkit", 390)):
            with self.subTest(editor="legacy", engine=engine), getattr(env.pw, engine).launch() as browser:
                page = browser.new_page(viewport={"width": width, "height": 844}, is_mobile=width == 390)
                env.login(page)
                env.ready(page, "/accounts/security/permissions")
                editable = self.check_choices(page, rules[OPERATIONAL_KEY], [])
                expect(editable).to_have_value("master")
                self.check_choices(page, rules["neoapps.user_management.edit"], ROLES)
                self.assertFalse(page.evaluate("document.documentElement.scrollWidth > innerWidth + 1"))

        with env.app.app_context():
            actor = User.query.filter_by(username="drawer-admin").one()
            receipt = AccountSecurityEvent(actor_user_id=actor.id, subject_user_id=actor.id,
                action="scoped_authorization_activated",
                details={"policy_floors": {OPERATIONAL_KEY: "operator",
                    "neoscorpion.fuel_dispatch.edit": "simulator"}})
            db.session.add(receipt)
            db.session.flush()
            db.session.add(AccountAuthorizationState(id=1, active=True,
                security_event_id=receipt.id, inventory_fingerprint="synthetic"))
            db.session.commit()

        for engine, width in (("chromium", 1440), ("webkit", 390)):
            with self.subTest(editor="scoped", engine=engine), getattr(env.pw, engine).launch() as browser:
                page = browser.new_page(viewport={"width": width, "height": 844}, is_mobile=width == 390)
                env.login(page)
                env.ready(page, "/neoscorpion/permissions")
                selector = self.check_choices(page, rules[OPERATIONAL_KEY], ["watcher"])
                blocked = self.check_choices(page, rules["neoscorpion.fuel_dispatch.edit"], ROLES[:2])
                expect(page.locator("#" + blocked.get_attribute("aria-describedby"))).to_contain_text("Simulator")
                role = "operator" if engine == "chromium" else "master"
                selector.select_option(role)
                with page.expect_navigation(wait_until="load") as navigation:
                    page.get_by_role("button", name="Save permitted thresholds").click()
                self.assertEqual(navigation.value.status, 200)
                expect(page.get_by_text("Scoped permission rules saved.", exact=True)).to_be_visible()
                expect(selector).to_have_value(role)
                page.reload()
                expect(selector).to_have_value(role)
                self.assertFalse(page.evaluate("document.documentElement.scrollWidth > innerWidth + 1"))
                selector.scroll_into_view_if_needed()
                page.screenshot(path=str(env.evidence / f"scoped-permissions-{engine}-{width}.png"))

                for path, key in (
                    ("/neoapps/permissions", "neoapps.user_management.edit"),
                    ("/neogateway/permissions", "neomotherbrain.manage_api.run"),
                ):
                    env.ready(page, path)
                    self.check_choices(page, rules[key], ROLES)
                    self.assertFalse(page.evaluate("document.documentElement.scrollWidth > innerWidth + 1"))

    def check_choices(self, page, rule_id, disabled):
        selector = page.locator(f"select[name=minimum_role_{rule_id}]")
        expect(selector).to_have_count(1)
        options = selector.locator("option[value]:not([value=''])")
        expect(options).to_have_text([role.title() for role in ROLES])
        actual = options.evaluate_all("options => options.filter(o => o.disabled).map(o => o.value)")
        self.assertEqual(actual, disabled)
        return selector
