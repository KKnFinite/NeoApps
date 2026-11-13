"""Canonical PRETREAT reaches open DUCC views through their normal revision poll."""
import unittest
from pathlib import Path

from playwright.sync_api import expect

from app.extensions import db
from app.models import NeoSubZeroPretreatState, SortDateMission, SortDateOperation, SortDateParkingAssignment
from app.neonodes.neosubzero.services import mutate_pretreat
from app.services.live_screen_refresh import save_live_screen_refresh_override
from tests.browser import test_neosubzero_ucc as fixtures


class DuccPretreatBrowserTest(unittest.TestCase):
    def exercise(self, engine):
        case = fixtures.UccBrowserTest()
        case.setUpClass()
        self.addCleanup(case.tearDownClass)
        stands = {"Alpha": "A01", "Bravo": "B01", "Charlie": "C01", "Delta": "D01", "Echo": "E01", "Remote": "R01"}
        mission_ids = {}
        with case.app.app_context():
            builder = fixtures.NeoSubZeroUccTest()
            builder.operation = db.session.get(SortDateOperation, case.operation_id)
            save_live_screen_refresh_override(builder.operation.gateway, "neosubzero.ucc_tv", "5")
            for index, (ramp, stand) in enumerate(stands.items(), 1):
                mission = builder._departure(f"UPS81{index}", f"N81{index}UP", "SDF", 4)
                if ramp == "Remote":
                    builder._deice_event(mission, "deice_planned")
                db.session.add(SortDateParkingAssignment(sort_date_operation_id=case.operation_id,
                    tail_number=mission.assigned_tail_number, ramp_code=stand[0], position_code=stand))
                mission_ids[ramp] = mission.id
            db.session.commit()

        browser = getattr(case.pw, engine).launch()
        self.addCleanup(browser.close)
        work = browser.new_page(viewport={"width": 1366, "height": 900})
        tv = browser.new_page(viewport={"width": 1366, "height": 900})
        mobile = browser.new_page(viewport={"width": 390, "height": 844})
        navigations = []
        for page in (work, tv, mobile):
            case.login(page)
            page.clock.install()
            if page == tv:
                page.add_init_script("localStorage.setItem('neoapps.neosubzero.ucc.tv-mode','1')")
            with page.expect_response(lambda response: "/ucc/revision" in response.url):
                case.ready(page, "/neosubzero/ucc?view=ramp&ramp=Alpha" if page == mobile else "/neosubzero/ucc")
            page.on("request", lambda request: navigations.append(request.url) if request.is_navigation_request() else None)

        def card(page, ramp):
            key = mission_ids[ramp]
            if page == work:
                return page.locator(f'[data-ucc-parking="{stands[ramp]}"] [data-ucc-aircraft="{key}"]')
            root = ".ucc-tv-wallboard" if page == tv else ".ucc-mobile-projection"
            return page.locator(f'{root} .ucc-tv-stand:has(.ucc-tv-position:text-is("{stands[ramp]}")) [data-ucc-projected-aircraft="{key}"]')

        def apply(action, values, ramps=("Alpha",)):
            with case.app.app_context():
                op = db.session.get(SortDateOperation, case.operation_id)
                for ramp in ramps:
                    mission = db.session.get(SortDateMission, mission_ids[ramp])
                    state = NeoSubZeroPretreatState.query.filter_by(sort_date_operation_id=op.id,
                                                                  tail_number=mission.assigned_tail_number).one_or_none()
                    mutate_pretreat(op, mission, action, values, state)
                db.session.commit()
            for page in (work, tv, mobile):
                page.clock.fast_forward(6000)

        def expect_state(page, ramp, state, color, label=None):
            aircraft = card(page, ramp)
            expect(aircraft).to_be_visible()
            expect(aircraft).to_have_class(f"ucc-plane state-{state}" if page == work else f"ucc-tv-plane is-stacked state-{state}")
            if page == work:
                expect(aircraft.locator(".ucc-plane-icon")).to_have_css("color", color)
            else:
                expect(aircraft.locator(".ucc-tv-plane-icon")).to_have_css("background-color", color)
            if label:
                expect(aircraft.locator(".ucc-plane-pretreat")).to_have_text(label)

        for page in (work, tv, mobile):
            expect(card(page, "Alpha")).to_have_class(
                "ucc-plane state-normal" if page == work else "ucc-tv-plane is-stacked state-normal")
        apply("toggle_planned", {"planned": "1"})
        for page in (work, tv, mobile):
            expect_state(page, "Alpha", "pretreat-planned", "rgb(212, 165, 54)")
        apply("toggle_configured", {})
        for page in (work, tv, mobile):
            expect_state(page, "Alpha", "pretreat-configured", "rgb(39, 135, 86)")
        values = {}
        for field in ("pass1_start", "pass1_end", "pass2_start"):
            values[field] = "22:15"
            apply("save_treatment", values)
            for page in (work, tv, mobile):
                expect_state(page, "Alpha", "pretreat-in-progress", "rgb(39, 135, 86)", "PRETREAT IN PROGRESS")
        values["pass2_end"] = "22:15"
        apply("save_treatment", values, stands)

        evidence = Path("instance/browser-evidence/subzero-ducc-pretreat")
        evidence.mkdir(parents=True, exist_ok=True)
        blue = "rgb(40, 93, 168)"
        work.locator("[data-ucc-weather-toggle]").click()
        for width in (1366, 1920):
            for page in (work, tv):
                page.set_viewport_size({"width": width, "height": 1080})
                for ramp in stands:
                    expect_state(page, ramp, "pretreated", blue, "PRETREATED")
                    self.assertTrue(card(page, ramp).locator(".ucc-plane-pretreat").evaluate(
                        "label => label.getBoundingClientRect().width <= label.parentElement.clientWidth + 1"
                    ))
                self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth-innerWidth"), 1)
            card(work, "Alpha").scroll_into_view_if_needed()
            work.screenshot(path=str(evidence / f"{engine}-working-{width}.png"))
            tv.screenshot(path=str(evidence / f"{engine}-tv-dark-{width}.png"))
            tv.locator(".ucc-tv-theme").click()
            expect(tv.locator(".ucc-tv-wallboard")).to_have_attribute("data-ucc-tv-theme", "light")
            expect_state(tv, "Alpha", "pretreated", blue, "PRETREATED")
            expect(card(tv, "Alpha").locator(".ucc-plane-pretreat")).to_have_css("color", "rgb(35, 79, 140)")
            tv.screenshot(path=str(evidence / f"{engine}-tv-light-{width}.png"))
            tv.locator(".ucc-tv-theme").click()
        for width in (375, 390, 430):
            mobile.set_viewport_size({"width": width, "height": 844})
            for ramp in stands:
                mobile.locator(f'[data-ucc-ramp-select="{ramp}"]').click()
                expect_state(mobile, ramp, "pretreated", blue, "PRETREATED")
                expect(mobile.locator("[data-subzero-ucc]")).to_have_attribute("data-ucc-selected-ramp", ramp)
                self.assertLessEqual(mobile.evaluate("document.documentElement.scrollWidth-innerWidth"), 1)
                if ramp == "Alpha":
                    mobile.screenshot(path=str(evidence / f"{engine}-mobile-alpha-{width}.png"))
            mobile.screenshot(path=str(evidence / f"{engine}-mobile-remote-{width}.png"))
        self.assertEqual(navigations, [])

    def test_chromium(self):
        self.exercise("chromium")

    def test_webkit(self):
        self.exercise("webkit")
