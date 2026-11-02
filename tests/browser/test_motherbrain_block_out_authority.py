"""R07 real editor -> Google import -> MotherBrain/Rain presentation."""
from datetime import date, datetime, timedelta
import unittest
from unittest.mock import patch

from playwright.sync_api import expect

from app.extensions import db
from app.models import Gateway, SortDateMission, SortDateOperation
from app.services.google_rain_live_milestones import apply_google_rain_departure_milestones
from tests.browser import test_mobile_drawer as fixtures


class BlockOutAuthorityBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = fixtures.MobileDrawerBrowserTest
        cls.fixture.setUpClass()
        cls.fixture.app.config["SECURITY_HEADERS_CSP_ALLOW_NON_PRODUCTION"] = True

    @classmethod
    def tearDownClass(cls):
        cls.fixture.tearDownClass()

    def run_engine(self, engine):
        day = date(2026, 8, 10) + timedelta(days=engine == "webkit")
        actual = datetime.combine(day + timedelta(days=1), datetime.min.time()).replace(hour=7)
        with self.fixture.app.app_context():
            gateway = Gateway.query.filter_by(code="RFD").one()
            operation = SortDateOperation(gateway_id=gateway.id, gateway_code="RFD",
                sort_date=day, sort_name="night")
            db.session.add(operation)
            db.session.flush()
            mission = SortDateMission(sort_date_operation_id=operation.id,
                sort_date=day, sort_name="night", gateway_code="RFD", mission_type="departure",
                mission_source="manual", wave="1", flight_number="UPS0910", origin="RFD", destination="LAX",
                assigned_tail_number="N123UP", timezone="America/Chicago",
                planned_datetime_local=actual.replace(hour=2, minute=24),
                planned_datetime_utc=actual.replace(minute=24), planned_source="manual",
                departure_status="scheduled")
            db.session.add(mission)
            db.session.commit()
            operation_id, mission_id = operation.id, mission.id

        def import_rain():
            with self.fixture.app.app_context():
                operation = db.session.get(SortDateOperation, operation_id)
                result = apply_google_rain_departure_milestones(operation,
                    rows=[dict(sheet_row=4, flight_number="UPS0910", block="2:45")])
                db.session.commit()
                return result

        browser = getattr(self.fixture.pw, engine).launch(headless=True)
        context = browser.new_context(viewport={"width":1920 if engine == "chromium" else 390, "height":900})
        try:
            page = context.new_page()
            page.set_default_timeout(10000)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            self.fixture().login(page)
            edit_url = f"/motherbrain/operations/{operation_id}/missions/{mission_id}/edit"
            self.fixture().ready(page, edit_url)
            page.locator('[name="actual_block_out_datetime_utc_date"]').fill(actual.strftime("%Y-%m-%d"))
            page.locator('[name="actual_block_out_datetime_utc_hour"]').fill("07")
            page.locator('[name="actual_block_out_datetime_utc_minute"]').fill("00")
            page.get_by_role("button", name="SAVE", exact=True).click()
            page.wait_for_url(f"**/motherbrain/operations/{operation_id}/missions/{mission_id}")
            self.assertEqual(import_rain()["applied_count"], 1)
            self.fixture().ready(page, edit_url)
            expect(page.locator('[name="actual_block_out_datetime_utc_date"]')).to_have_value(actual.strftime("%Y-%m-%d"))
            expect(page.locator('[name="actual_block_out_datetime_utc_hour"]')).to_have_value("07")
            expect(page.locator('[name="actual_block_out_datetime_utc_minute"]')).to_have_value("00")
            with self.fixture.app.app_context():
                current = db.session.get(SortDateMission, mission_id)
                self.assertEqual(current.actual_block_out_datetime_utc, actual)
                self.assertEqual(current.actual_block_out_source, "manual")
            with patch('app.neonodes.neorain.routes.current_neorain_outbound_operation',
                       side_effect=lambda gateway: db.session.get(SortDateOperation, operation_id)):
                self.fixture().ready(page, "/neorain/outbound")
                control = page.locator(f'.rain-override[data-rain-override-mission="{mission_id}"][data-rain-override-field="official_block_out"] [data-rain-override-time]:visible')
                if not control.count():
                    page.locator('[data-neorain-mobile-row-open]:visible').first.click()
                expect(control).to_have_value("02:00")
                with page.expect_response(lambda r: "/neorain/override" in r.url and r.request.method == "POST") as response:
                    control.fill("0240")
                self.assertEqual(response.value.status, 200)
                import_rain()
                page.reload()
                if not control.count():
                    page.locator('[data-neorain-mobile-row-open]:visible').first.click()
                expect(control).to_have_value("02:40")
            with self.fixture.app.app_context():
                current = db.session.get(SortDateMission, mission_id)
                self.assertEqual(current.actual_block_out_datetime_utc, actual.replace(minute=40))
                self.assertEqual(current.actual_block_out_source, "neorain")
            self.assertEqual(errors, [])
        finally:
            context.close()
            browser.close()

    def test_chromium(self):
        self.run_engine("chromium")

    def test_webkit(self):
        self.run_engine("webkit")
