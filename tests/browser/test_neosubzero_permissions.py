"""Rendered NeoSubZero role controls against isolated node assignments."""
import unittest

from playwright.sync_api import expect

from app.extensions import db
from app.models import StaffingUnit, User
from app.services.access_control import backfill_default_gateway_node_roles
from app.services.neosubzero_staffing import DEICE_QUALIFICATION_KEY, set_staffing_person_qualification
from app.services.password_policy import set_user_password
from tests.browser import test_neosubzero_ucc as fixtures
from tests.test_neosubzero_permissions import FLEET, MANAGEMENT, ROLES, UCC, set_subzero_role


class SubZeroRoleBrowserTest(unittest.TestCase):
    def test_node_role_controls_in_chromium_and_webkit(self):
        fixture = fixtures.UccBrowserTest()
        fixture.setUpClass()
        self.addCleanup(fixture.tearDownClass)
        with fixture.app.app_context():
            builder = fixtures.NeoSubZeroUccTest()
            night = StaffingUnit.query.filter_by(unit_type="sort", name="Night").one()
            ramp = builder._unit("operation", "Ramp", night)
            department = builder._unit("department", "Shift", ramp)
            door = builder._unit("work_area", "Door 1", department)
            candidate = builder._person("BROWSER-CALL", "Browser", "Candidate", door)
            set_staffing_person_qualification(candidate, DEICE_QUALIFICATION_KEY, True)
            for role in ROLES:
                user = User(username=f"role-{role}", email=f"role-{role}@example.test", role="watcher",
                            email_verified_at=User.query.filter_by(username="drawer-admin").one().email_verified_at)
                set_user_password(user, "BrowserFixture123!")
                db.session.add(user)
                db.session.flush()
                backfill_default_gateway_node_roles(user, role="watcher")
                set_subzero_role(user, role)
            db.session.commit()

        for engine in ("chromium", "webkit"):
            browser = getattr(fixture.pw, engine).launch()
            self.addCleanup(browser.close)
            for role in ROLES:
                with self.subTest(engine=engine, role=role):
                    self.check_role(fixture, browser, role)

    def check_role(self, fixture, browser, role):
        context = browser.new_context(viewport={"width": 1366, "height": 900}, service_workers="block")
        try:
            page = context.new_page()
            fixture.login(page, f"role-{role}")
            editable = role in ROLES[2:]
            for path, selector in (
                (MANAGEMENT + "?tab=callouts", 'button[value="add"]'),
                (MANAGEMENT + "?tab=eligibility", 'button[value="unqualify"]'),
                (FLEET, 'button:has-text("ADD TRUCK")'),
            ):
                fixture.ready(page, path)
                controls = page.locator(selector)
                if editable:
                    expect(controls.first).to_be_visible()
                else:
                    expect(controls).to_have_count(0)
            expect(page.get_by_role("link", name="Truck Management", exact=True).first).to_be_visible()
            fixture.ready(page, "/neosubzero/settings")
            fluid = page.locator('[name="type_i_fluid_name"]')
            if role in ROLES[3:]:
                expect(fluid).to_be_editable()
            else:
                expect(fluid).not_to_be_editable()

            fixture.ready(page, UCC)
            empty = page.locator('[data-ucc-editor="Echo-4"] [data-ucc-crew-form="truck"] select')
            pencil = page.locator('[data-ucc-editor="Alpha-1"] [data-ucc-crew-edit="driver"]')
            if editable:
                expect(empty).to_be_visible()
                expect(pencil).to_be_visible()
                pencil.click()
                expect(page.locator('.ucc-team-controls:popover-open')).to_be_visible()
                page.keyboard.press("Escape")
            else:
                expect(empty).to_have_count(0)
                expect(pencil).to_have_count(0)
            page.evaluate("document.documentElement.requestFullscreen = undefined")
            page.locator("[data-ucc-tv-enter]").click()
            tv = page.locator(".ucc-tv-wallboard")
            expect(tv).to_be_visible()
            expect(tv.locator("form, select, [data-ucc-crew-edit]")).to_have_count(0)
            expect(page.locator("[data-ucc-crew-edit]:visible")).to_have_count(0)
        finally:
            context.close()
