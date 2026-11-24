"""Block-In changes on one flight while another flight has an unfinished edit."""
from datetime import datetime
from unittest import TestCase

from playwright.sync_api import expect

from app.extensions import db
from app.models import SortDateMission
from tests.browser import test_neorain_compact_overrides as fixtures


class LiveArrivalTest(TestCase):
    def test_local_and_remote_block_in(self):
        RainFixture = fixtures.CompactRainBrowserTest
        RainFixture.setUpClass()
        self.addCleanup(RainFixture.tearDownClass)
        rain = RainFixture()
        env = rain.fixture()
        mid = rain.ids["arrival"]
        with env.app.app_context():
            arrival = db.session.get(SortDateMission, mid)
            arrival.eta_datetime_utc = datetime(2026, 10, 16, 6, 10)
            other = SortDateMission(sort_date_operation_id=arrival.sort_date_operation_id,
                sort_date=arrival.sort_date, gateway_code=arrival.gateway_code,
                sort_name=arrival.sort_name, mission_type="arrival", mission_source="manual",
                flight_number="UPS234", origin="SDF", destination="RFD", timezone=arrival.timezone)
            db.session.add(other)
            db.session.commit()
            other_id = other.id

        for engine, width in (("chromium", 1920), ("webkit", 390)):
            with self.subTest(engine=engine), getattr(env.pw, engine).launch() as browser:
                page = browser.new_page(viewport={"width": width, "height": 844})
                rain.login_for_sort(page)
                env.ready(page, "/neorain/inbound")
                row = page.locator(f'[data-neorain-inbound-row][data-neorain-mission-id="{mid}"]:visible')
                clock = row.locator(".neorain-eta" if width > 900 else "[data-arrival-mobile-clock]")
                block = row.locator('[data-rain-override-field="block_in"] input:visible')
                page.evaluate("window.testArrivalRow=document.querySelector('[data-neorain-inbound-row]')")
                expect(clock).to_have_text("01:10")
                with page.expect_response(lambda r: r.url.endswith("/override")):
                    block.fill("0125")
                expect(block).to_have_value("01:25")
                if width > 900:
                    expect(clock).to_be_hidden()
                else:
                    expect(clock).to_have_text("01:00")
                with page.expect_response(lambda r: r.url.endswith("/override")):
                    block.fill("")
                    block.press("Tab")
                expect(clock).to_be_visible()
                expect(clock).to_have_text("01:10")
                # Release the native override before exercising the upstream projection.
                block.focus()
                with page.expect_response(lambda r: r.url.endswith("/override")):
                    page.locator("[data-rain-reset-selected]").click()
                if width > 900:
                    row.locator("[data-neorain-delay-toggle]").click()
                    panel = page.locator(f"#delay-info-{mid}")
                else:
                    row.locator("[data-neorain-mobile-row-open]").click()
                    panel = row.locator("[data-neorain-mobile-inline-detail]")
                draft = page.locator(f'[data-neorain-mission-id="{other_id}"]:visible [data-rain-override-field="block_in"] input:visible')
                draft.fill("12")
                rain.upstream("arrival", "block_in", datetime(2026, 10, 16, 6, 35))
                expect(block).to_have_value("01:35", timeout=15000)
                if width > 900:
                    expect(clock).to_be_hidden()
                else:
                    expect(clock).to_have_text("01:00")
                rain.upstream("arrival", "block_in", None)
                expect(clock).to_be_visible(timeout=15000)
                expect(clock).to_have_text("01:10", timeout=15000)
                expect(draft).to_have_value("12")
                expect(draft).to_be_focused()
                expect(panel).to_be_visible()
                self.assertTrue(page.evaluate("window.testArrivalRow===document.querySelector('[data-neorain-inbound-row]')"))

                env.ready(page, f"/neorain/rainrock?operation_id={rain.operation_id}")
                page.evaluate("window.originalReport=document.querySelector('[data-rainrock-report]')")
                volume = page.locator('[data-rainrock-field][name="actual_volume"]')
                volume.fill("12450")
                expect(page.locator('[data-rainrock-value="actual_volume"]')).to_have_text("12,450")
                expect(volume).to_be_focused()
                self.assertTrue(page.evaluate("window.originalReport===document.querySelector('[data-rainrock-report]')"))
