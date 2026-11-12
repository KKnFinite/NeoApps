"""Echo pad projection reaches existing Working DUCC and TV sessions."""
import unittest
from datetime import datetime
from urllib.parse import urlsplit

from playwright.sync_api import expect

from app.extensions import db
from app.models import NeoSubZeroDepartureDeiceEvent, SortDateMission, SortDateOperation, SortDateParkingAssignment, SortDateTailState, User
from app.services.live_screen_refresh import save_live_screen_refresh_override
from tests.browser import test_neosubzero_ucc as fixtures


class DuccEchoPadBrowserTest(unittest.TestCase):
    def exercise(self, engine):
        case = fixtures.UccBrowserTest()
        case.setUpClass()
        self.addCleanup(case.tearDownClass)
        with case.app.app_context():
            builder = fixtures.NeoSubZeroUccTest()
            builder.operation = db.session.get(SortDateOperation, case.operation_id)
            save_live_screen_refresh_override(builder.operation.gateway, "neosubzero.ucc_tv", "5")
            ready = builder._departure("UPS701", "N701UP", "SDF", 4)
            stale = builder._departure("UPS702", "N702UP", "DEN", 4)
            no_deice = builder._departure("UPS703", "N703UP", "ONT", 4)
            for mission, code in ((ready, "E04"), (stale, "E05"), (no_deice, "E06")):
                db.session.add(SortDateParkingAssignment(sort_date_operation_id=case.operation_id,
                    tail_number=mission.assigned_tail_number, ramp_code="E", position_code=code))
            db.session.add(SortDateTailState(sort_date=builder.operation.sort_date, gateway_code="RFD",
                sort_name="night", tail_number="N701UP", deice_status="required"))
            unsupported_fin = builder._deice_event(stale, "finished")
            stale.actual_block_out_datetime_utc = datetime(2026, 10, 23, 4)
            db.session.commit()
            ready_id, no_deice_id, occupant_id = ready.id, no_deice.id, unsupported_fin.id

        browser = getattr(case.pw, engine).launch()
        self.addCleanup(browser.close)
        width, height = (1366, 768) if engine == "webkit" else (1920, 1080)
        work = browser.new_page(viewport={"width": width, "height": height})
        tv = browser.new_page(viewport={"width": width, "height": height})
        navigations = []
        for page in (work, tv):
            case.login(page)
            page.clock.install()
            if page == tv:
                page.add_init_script("localStorage.setItem('neoapps.neosubzero.ucc.tv-mode','1')")
            with page.expect_response(lambda response: urlsplit(response.url).path.endswith("/ucc/revision")):
                case.ready(page, "/neosubzero/ucc")
            page.on("framenavigated", lambda frame, tab=page: navigations.append(frame.url) if frame == tab.main_frame else None)
        echo = work.locator('[data-ucc-ramp="Echo"]')
        tv_echo = tv.locator(".ucc-tv-ramp").filter(has=tv.get_by_role("heading", name="Echo", exact=True))
        work_pad, tv_pad = echo.locator("[data-ucc-deice-pad]"), tv_echo.locator(".ucc-tv-pad")
        wait10 = echo.locator('[data-ucc-queue-position="1"]')
        expect(echo.locator('[data-ucc-parking="E04"]')).to_contain_text("N701UP")
        expect(work_pad).not_to_contain_text("N702UP")
        expect(tv_pad).not_to_contain_text("N702UP")
        expect(echo.locator(".ucc-queue-exception").filter(has_text="FIN EVIDENCE REVIEW")).to_contain_text("N702UP")

        def tick():
            for page in (work, tv):
                with page.expect_response(lambda response: urlsplit(response.url).path.endswith("/ucc/revision")):
                    page.clock.fast_forward(5000)

        with case.app.app_context():
            db.session.get(SortDateMission, ready_id).actual_block_out_datetime_utc = datetime(2026, 10, 23, 4, 5)
            db.session.get(SortDateMission, no_deice_id).actual_block_out_datetime_utc = datetime(2026, 10, 23, 4, 6)
            db.session.commit()
        tick()
        for pad in (work_pad, tv_pad):
            expect(pad).to_contain_text("N701UP")
        expect(wait10).not_to_contain_text("N701UP")
        expect(tv_echo.locator(".ucc-tv-stands")).not_to_contain_text("N701UP")
        expect(echo.locator(".ucc-plane").filter(has_text="N701UP")).to_have_count(1)
        expect(tv_echo.locator(".ucc-tv-plane").filter(has_text="N701UP")).to_have_count(1)
        expect(echo.locator(".ucc-plane").filter(has_text="N703UP")).to_have_count(0)
        expect(tv_echo.locator(".ucc-tv-plane").filter(has_text="N703UP")).to_have_count(0)
        with case.app.app_context():
            self.assertIsNone(NeoSubZeroDepartureDeiceEvent.query.filter_by(sort_date_mission_id=ready_id).first())
            verified = db.session.get(NeoSubZeroDepartureDeiceEvent, occupant_id)
            verified.status = "configured"
            verified.pass1_started_at_utc = datetime(2026, 10, 23, 4, 8)
            db.session.commit()
        tick()
        for pad in (work_pad, tv_pad):
            expect(pad).to_contain_text("N702UP")
        expect(wait10).to_contain_text("N701UP")
        expect(tv_echo.locator(".ucc-tv-stands")).to_contain_text("N701UP")

        for status, radio in (("finished", None), ("cleared", "pending"), ("cleared", "complete")):
            with case.app.app_context():
                verified = db.session.get(NeoSubZeroDepartureDeiceEvent, occupant_id)
                verified.status = status
                verified.pass1_ended_at_utc = datetime(2026, 10, 23, 4, 10)
                verified.radio_state = radio
                if radio == "complete":
                    verified.radio_completed_at_utc = datetime(2026, 10, 23, 4, 12)
                    verified.radio_completed_by_user_id = User.query.filter_by(username="drawer-admin").one().id
                db.session.commit()
            tick()
            for pad in (work_pad, tv_pad):
                expect(pad).to_contain_text("N701UP" if radio == "complete" else "N702UP")
        for width in (1366, 1920):
            for page, pad in ((work, work_pad), (tv, tv_pad)):
                page.set_viewport_size({"width": width, "height": 1080})
                expect(pad).to_be_visible()
                expect(pad).to_contain_text("N701UP")
                self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth - innerWidth"), 1)
        expect(wait10).to_have_text("")
        expect(tv_echo.locator(".ucc-tv-stands")).not_to_contain_text("N701UP")
        with case.app.app_context():
            record = db.session.get(NeoSubZeroDepartureDeiceEvent, occupant_id)
            self.assertEqual(record.pass1_started_at_utc, datetime(2026, 10, 23, 4, 8))
            self.assertEqual(record.pass1_ended_at_utc, datetime(2026, 10, 23, 4, 10))
        self.assertEqual(navigations, [])

    def test_chromium(self):
        self.exercise("chromium")

    def test_webkit(self):
        self.exercise("webkit")
