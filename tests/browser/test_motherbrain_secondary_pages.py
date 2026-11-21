"""Exercise the secondary MotherBrain pages with synthetic missions and schedule rows."""
import unittest
from datetime import date, datetime, time

from app.extensions import db
from app.models import Gateway, GatewayMembership, GatewayNodeRole, MasterFlightSchedule, NeoNode, SortDateMission, SortDateOperation, User
from tests.browser import test_mobile_drawer as fixture


class SecondaryPagesTest(unittest.TestCase):
    def test_signed_in_secondary_pages(self):
        env = fixture.MobileDrawerBrowserTest
        env.setUpClass()
        self.addCleanup(env.tearDownClass)
        env.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = datetime(2026, 11, 23, 12)
        with env.app.app_context():
            gateway = Gateway.query.filter_by(code="RFD").one()
            viewer = User.query.filter_by(username="drawer-watcher").one()
            membership = GatewayMembership.query.filter_by(user_id=viewer.id, gateway_id=gateway.id).one()
            node = NeoNode.query.filter_by(code="motherbrain").one()
            GatewayNodeRole.query.filter_by(gateway_membership_id=membership.id, node_id=node.id).one().role = "operator"
            op = SortDateOperation(gateway_id=gateway.id, gateway_code="RFD",
                                   sort_date=date(2026, 11, 23), sort_name="night")
            db.session.add(op)
            master = MasterFlightSchedule(gateway_id=gateway.id, gateway_code="RFD", sort_name="night",
                mission_type="departure", flight_number="UPS720", origin="RFD", destination="EWR",
                aircraft_type="757", active_days="monday", planned_time_local=time(2), active=True)
            db.session.add(master)
            for i, (direction, origin, destination) in enumerate((
                ("arrival", "SDF", "RFD"), ("departure", "RFD", "EWR"), ("departure", "RFD", "EWR")
            )):
                mission = SortDateMission(sort_date_operation=op, sort_date=op.sort_date,
                    gateway_code="RFD", sort_name="night", mission_type=direction,
                    mission_source="manual", wave="1", flight_number=f"UPS{719 + i}", origin=origin,
                    destination=destination, assigned_tail_number=f"N{719 + i}UP",
                    planned_datetime_local=datetime(2026, 11, 23, 22, i * 10))
                db.session.add(mission)
            db.session.commit()
            operation = f"/motherbrain/operations/{op.id}"
            flight = f"{operation}/missions/{mission.id}"
            schedule = "/motherbrain/master-schedule"
            master_path = f"{schedule}/{master.id}"

        pages = {
            "operations": "/motherbrain/operations", "generate": "/motherbrain/operations/new",
            "arrival-board": operation + "/arrivals", "departure-board": operation + "/departures",
            "mission": flight, "mission-add": operation + "/missions/new", "mission-edit": flight + "/edit",
            "master": master_path, "master-add": schedule + "/new", "master-edit": master_path + "/edit",
            "master-bulk": schedule + "/bulk-edit",
        }
        for engine, width, height in (("chromium", 1440, 900), ("webkit", 390, 844), ("webkit", 820, 1180)):
            with self.subTest(width=width):
                browser = getattr(env.pw, engine).launch()
                try:
                    page = browser.new_page(viewport={"width": width, "height": height},
                                            is_mobile=engine == "webkit", has_touch=engine == "webkit")
                    env().login(page)
                    for name, path in pages.items():
                        with self.subTest(page=name):
                            env().ready(page, path)
                            self.assertEqual(page.url, env.origin + path)
                            if name in ("generate", "master-add", "master-edit", "master-bulk", "master",
                                        "mission-add", "mission-edit", "mission"):
                                self.assertGreater(page.locator("main form button[type=submit]:enabled").count(), 0)
                            if name.endswith("-board"):
                                self.assertIn(name.split("-")[0].title() + " Board", page.locator("main h1").inner_text())
                            if name == "departure-board":
                                for label in ("1st EWR", "2nd EWR"):
                                    self.assertIn(label, page.locator("main table").inner_text())
                            if name == "operations" and width == 390:
                                for link in page.locator("main .table-actions a").all():
                                    box = link.bounding_box()
                                    self.assertGreaterEqual(box["x"], 0)
                                    self.assertLessEqual(box["x"] + box["width"], width)
                            if name in ("master-edit", "master-add"):
                                for field in page.locator(".master-schedule-entry-grid input:visible, .master-schedule-entry-grid select:visible").all():
                                    box = field.bounding_box()
                                    self.assertLessEqual(box["x"] + box["width"], width + 1)
                            page.screenshot(path=str(env.evidence / f"{name}-{width}.png"))
                            self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), width + 1)
                    viewer = browser.new_page(viewport={"width": width, "height": height})
                    env().login(viewer, "drawer-watcher")
                    for name in ("generate", "mission-add", "mission-edit", "master-add", "master-edit", "master-bulk",
                                 "mission", "master"):
                        env().ready(viewer, pages[name])
                        self.assertEqual(viewer.url, env.origin + pages[name])
                        self.assertEqual(viewer.locator("main form button[type=submit]:enabled").count(), 0, name)
                        if name not in ("mission", "master"):
                            self.assertTrue(viewer.locator("main fieldset[disabled] legend").is_visible(), name)
                        else:
                            self.assertEqual(viewer.locator("main form[method=post]").count(), 0, name)
                finally:
                    browser.close()
