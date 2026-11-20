"""The main board saves one master row and updates its current mission."""
import unittest
from datetime import date, datetime, time

from playwright.sync_api import expect
from app.extensions import db
from app.models import MasterFlightSchedule, SortDateMission, SortDateOperation
from app.services.access_control import ensure_default_gateway_and_nodes
from tests.browser import test_mobile_drawer as fixture


class MasterControlsBrowserTest(unittest.TestCase):
    def test_weekdays_and_active_control(self):
        env = fixture.MobileDrawerBrowserTest
        for engine, width in (("chromium", 1440), ("webkit", 390)):
            with self.subTest(engine=engine):
                env.setUpClass()
                try:
                    env.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = datetime(2026, 11, 23, 22)
                    with env.app.app_context():
                        gateway = ensure_default_gateway_and_nodes()
                        op = SortDateOperation(gateway_id=gateway.id, gateway_code="RFD", sort_date=date(2026, 11, 23), sort_name="night")
                        master = MasterFlightSchedule(gateway_id=gateway.id, gateway_code="RFD", sort_name="night",
                            mission_type="departure", flight_number="UPS901", aircraft_type="757", origin="RFD",
                            destination="EWR", active_days="tuesday", active=True, planned_time_local=time(1))
                        db.session.add_all([op, master])
                        db.session.commit()
                    browser = getattr(env.pw, engine).launch()
                    try:
                        page = browser.new_page(viewport={"width": width, "height": 850})
                        env().login(page)
                        env().ready(page, "/motherbrain/master-schedule")
                        row = page.locator("#master-departures tbody tr").first
                        monday = row.get_by_role("checkbox", name="Monday")
                        tuesday = row.get_by_role("checkbox", name="Tuesday")
                        expect(tuesday).to_be_checked()
                        expect(monday).not_to_be_checked()
                        with page.expect_response(lambda r: r.request.method == "POST" and "/master-schedule" in r.url) as saved:
                            monday.check()
                        self.assertEqual(saved.value.status, 200)
                        with env.app.app_context():
                            mission_id = SortDateMission.query.filter_by(flight_number="UPS901").one().id
                        toggle = row.locator(".master-active-control")
                        for active in (False, True):
                            with page.expect_response(lambda r: "/toggle-active" in r.url) as changed:
                                toggle.click()
                            self.assertEqual(changed.value.status, 200)
                            expect(toggle).to_have_text("Active" if active else "Inactive")
                            with env.app.app_context():
                                mission = db.session.get(SortDateMission, mission_id)
                                self.assertEqual(mission.departure_status, "scheduled" if active else "cancelled")
                        page.reload()
                        expect(monday).to_be_checked()
                        expect(tuesday).to_be_checked()
                        bounds = row.evaluate("""el => [...el.querySelectorAll('.master-weekdays label, .master-active-control, .master-cell-actions button')]
                            .map(c => c.getBoundingClientRect()).every(r => r.width > 0 && r.left >= 0 && r.right <= innerWidth + 1)""")
                        self.assertTrue(bounds, "Every weekday, Active and Delete must fit on screen")
                        page.screenshot(path=str(env.evidence / f"master-weekdays-{engine}.png"))
                    finally:
                        browser.close()
                finally:
                    env.tearDownClass()
