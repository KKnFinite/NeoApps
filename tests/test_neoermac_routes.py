import unittest
from datetime import time

from app import create_app
from app.extensions import db
from app.models import (
    GatewayMembership,
    GatewayNodeRole,
    MasterFlightSchedule,
    NeoErmacBuildingLineup,
    NeoNode,
    User,
)
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.permission_rules import ensure_default_permission_rules


class NeoErmacRoutesTest(unittest.TestCase):
    def setUp(self):
        TestConfig = type(
            "TestConfig",
            (),
            {
                "SECRET_KEY": "test",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            },
        )
        self.app = create_app(TestConfig)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.gateway = ensure_default_gateway_and_nodes()
        ensure_default_permission_rules()
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_unauthenticated_users_cannot_access_neoermac_pages(self):
        for path in self._neoermac_paths():
            with self.subTest(path=path):
                response = self.client.get(path, follow_redirects=False)

                self.assertEqual(response.status_code, 302)
                self.assertIn("/login", response.location)

    def test_authenticated_user_with_neoermac_access_can_access_menu(self):
        self._login_approved_user()

        response = self.client.get("/neoermac")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"NeoErmac", response.data)
        self.assertIn(b'src="/static/images/neoermac_logo1_large.png"', response.data)
        self.assertIn(b'srcset="/static/images/neoermac_logo1_medium.png"', response.data)
        self.assertIn(b'srcset="/static/images/neoermac_logo1_small.png"', response.data)
        self.assertIn(b"OPERATIONAL OVERVIEW", response.data)
        self.assertIn(b"ACTIVE GATEWAY", response.data)
        self.assertIn(b"BUILDING LINEUP", response.data)
        self.assertIn(b"VIEW OUTBOUND", response.data)
        self.assertIn(b"DOOR VIEW", response.data)
        self.assertIn(b"TUG ASSIGNMENTS", response.data)
        self.assertIn(b"BACK TO", response.data)
        self.assertIn(b'class="brand-inline-name neo-node-name node-gateway"', response.data)
        self.assertNotIn(b"RFD NEONODE", response.data)
        self.assertNotIn(b'<nav class="neoermac-menu"', response.data)

    def test_neoermac_menu_links_work(self):
        self._login_approved_user()

        response = self.client.get("/neoermac")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'href="/neoermac/building-lineup"', response.data)
        self.assertIn(b'href="/neoermac/outbound"', response.data)
        self.assertIn(b'href="/neoermac/door-view"', response.data)
        self.assertIn(b'href="/neoermac/tug-assignments"', response.data)
        self.assertIn(b'href="/rfd"', response.data)

    def test_placeholder_pages_render(self):
        self._login_approved_user()
        expected_pages = {
            "/neoermac/outbound": b"VIEW OUTBOUND",
            "/neoermac/door-view": b"DOOR VIEW",
            "/neoermac/tug-assignments": b"TUG ASSIGNMENTS",
        }

        for path, title in expected_pages.items():
            with self.subTest(path=path):
                response = self.client.get(path)

                self.assertEqual(response.status_code, 200)
                self.assertIn(title, response.data)
                self.assertIn(b'aria-label="BACK TO NeoErmac"', response.data)
                self.assertIn(b"OPERATIONAL LOGIC WILL BE ADDED IN A LATER PASS.", response.data)

    def test_building_lineup_page_renders_belt_map(self):
        self._login_approved_user(role="operator")

        response = self.client.get("/neoermac/building-lineup")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"BUILDING LINEUP", response.data)
        self.assertIn(b"RFD BELT DESTINATION CONTROL", response.data)
        self.assertIn(b"D1 - D4", response.data)
        self.assertIn(b"D34 - D37", response.data)
        self.assertIn(b"WHT/BLU", response.data)
        self.assertIn(b"BLU/BLK", response.data)
        self.assertEqual(response.data.count(b"neoermac-belt-group"), 12)
        self.assertIn(b"neoermac-belt-row", response.data)
        self.assertIn(b"neoermac-side-label", response.data)
        self.assertIn(b"D1", response.data)
        self.assertIn(b"D37", response.data)
        self.assertIn(b"WHT/BLU", response.data)
        self.assertIn(b"ORG", response.data)
        self.assertNotIn(b"Green Runout", response.data)
        self.assertNotIn(b"Runout 1", response.data)
        self.assertNotIn(b"RUNOUT DESTINATION CONTROL", response.data)
        self.assertNotIn(b"EAST SIDE DESTINATIONS", response.data)
        self.assertNotIn(b"WEST SIDE DESTINATIONS", response.data)
        self.assertIn(b"View Only", response.data)
        self.assertNotIn(b"SAVE BUILDING LINEUP", response.data)

    def test_building_lineup_destination_options_come_from_master_departures(self):
        self._add_master_departure("UPS101", "sdf")
        self._add_master_departure("UPS102", "ont")
        self._add_master_arrival("UPS201", "dfw")
        db.session.commit()
        self._login_approved_user(role="operator")

        response = self.client.get("/neoermac/building-lineup")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'<option value="SDF"', response.data)
        self.assertIn(b'<option value="ONT"', response.data)
        self.assertNotIn(b'<option value="DFW"', response.data)

    def test_user_with_building_lineup_edit_can_save_destinations(self):
        self._add_master_departure("UPS301", "sdf")
        self._add_master_departure("UPS302", "ont")
        db.session.commit()
        self._login_approved_user(role="simulator")

        response = self.client.post(
            "/neoermac/building-lineup",
            data={
                "lineup_green_runout_east_destination_1": "sdf",
                "lineup_green_runout_east_destination_2": "",
                "lineup_green_runout_west_destination_1": "ont",
                "lineup_green_runout_west_destination_2": "",
            },
            follow_redirects=False,
        )

        saved = NeoErmacBuildingLineup.query.filter_by(
            gateway_id=self.gateway.id,
            runout_key="green_runout",
        ).one()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(saved.east_destination_1, "SDF")
        self.assertEqual(saved.west_destination_1, "ONT")

        reload_response = self.client.get("/neoermac/building-lineup")
        self.assertIn(b'<option value="SDF" selected', reload_response.data)
        self.assertIn(b'<option value="ONT" selected', reload_response.data)

    def test_building_lineup_save_allows_blank_slots_and_clears_destinations(self):
        self._add_master_departure("UPS351", "SDF")
        db.session.commit()
        self._login_approved_user(role="simulator")

        self.client.post(
            "/neoermac/building-lineup",
            data={
                "lineup_green_runout_east_destination_1": "SDF",
                "lineup_green_runout_east_destination_2": "",
                "lineup_green_runout_west_destination_1": "",
                "lineup_green_runout_west_destination_2": "",
            },
        )
        response = self.client.post(
            "/neoermac/building-lineup",
            data={
                "lineup_green_runout_east_destination_1": "",
                "lineup_green_runout_east_destination_2": "",
                "lineup_green_runout_west_destination_1": "",
                "lineup_green_runout_west_destination_2": "",
            },
            follow_redirects=False,
        )

        saved = NeoErmacBuildingLineup.query.filter_by(
            gateway_id=self.gateway.id,
            runout_key="green_runout",
        ).one()
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(saved.east_destination_1)
        self.assertIsNone(saved.east_destination_2)
        self.assertIsNone(saved.west_destination_1)
        self.assertIsNone(saved.west_destination_2)

    def test_user_with_building_lineup_view_can_open_read_only(self):
        self._add_master_departure("UPS401", "SDF")
        db.session.commit()
        self._login_approved_user(role="operator")

        read_only_response = self.client.get("/neoermac/building-lineup")
        self.assertEqual(read_only_response.status_code, 200)
        self.assertIn(b"View Only", read_only_response.data)
        self.assertIn(b"disabled", read_only_response.data)
        self.assertNotIn(b"SAVE BUILDING LINEUP", read_only_response.data)

    def test_view_only_user_cannot_post_building_lineup(self):
        self._add_master_departure("UPS402", "SDF")
        db.session.commit()
        self._login_approved_user(role="operator")

        self.client.get("/neoermac/building-lineup")

        save_response = self.client.post(
            "/neoermac/building-lineup",
            data={"lineup_green_runout_east_destination_1": "SDF"},
            follow_redirects=False,
        )

        saved = NeoErmacBuildingLineup.query.filter_by(
            gateway_id=self.gateway.id,
            runout_key="green_runout",
        ).one()
        self.assertEqual(save_response.status_code, 403)
        self.assertIsNone(saved.east_destination_1)

    def test_user_without_building_lineup_view_cannot_open_page(self):
        self._login_approved_user(role="watcher")

        response = self.client.get("/neoermac/building-lineup", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertIn("/neoermac", response.location)

    def test_ermac_route_is_not_used(self):
        self._login_approved_user()

        menu = self.client.get("/neoermac")
        response = self.client.get("/ermac")

        self.assertEqual(response.status_code, 404)
        self.assertNotIn(b'href="/ermac"', menu.data)

    def _login_approved_user(self, role="watcher"):
        user = User(
            username=f"neoermac_{role}_user",
            email=f"neoermac_{role}@example.test",
            role="watcher",
        )
        user.set_password("TestPassword123!")
        db.session.add(user)
        db.session.flush()

        db.session.add(
            GatewayMembership(
                user_id=user.id,
                gateway_id=self.gateway.id,
                status="approved",
                is_active=True,
            )
        )
        db.session.flush()

        if role != "watcher":
            ermac = NeoNode.query.filter_by(code="ermac").one()
            db.session.add(
                GatewayNodeRole(
                    gateway_membership_id=user.gateway_memberships[0].id,
                    node_id=ermac.id,
                    role=role,
                    is_active=True,
                )
            )
        db.session.commit()

        return self.client.post(
            "/login",
            data={"email": user.email, "password": "TestPassword123!"},
            follow_redirects=False,
        )

    def _add_master_departure(self, flight_number, destination):
        db.session.add(
            MasterFlightSchedule(
                gateway_id=self.gateway.id,
                gateway_code=self.gateway.code,
                sort_name="night",
                mission_type="departure",
                flight_number=flight_number,
                origin=self.gateway.code,
                destination=destination,
                active=True,
                active_days="monday,tuesday,wednesday,thursday,friday,saturday,sunday",
                planned_time_local=time(23, 0),
                timezone="America/Chicago",
            )
        )

    def _add_master_arrival(self, flight_number, origin):
        db.session.add(
            MasterFlightSchedule(
                gateway_id=self.gateway.id,
                gateway_code=self.gateway.code,
                sort_name="night",
                mission_type="arrival",
                flight_number=flight_number,
                origin=origin,
                destination=self.gateway.code,
                active=True,
                active_days="monday,tuesday,wednesday,thursday,friday,saturday,sunday",
                planned_time_local=time(22, 0),
                timezone="America/Chicago",
            )
        )

    def _neoermac_paths(self):
        return (
            "/neoermac",
            "/neoermac/building-lineup",
            "/neoermac/outbound",
            "/neoermac/door-view",
            "/neoermac/tug-assignments",
        )


if __name__ == "__main__":
    unittest.main()
