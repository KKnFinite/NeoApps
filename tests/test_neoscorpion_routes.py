import unittest
from datetime import date, datetime
from decimal import Decimal

from app import create_app
from app.extensions import db
from app.models import (
    GatewayMembership,
    GatewayNodeRole,
    NeoNode,
    NeoScorpionFuelAssignment,
    NeoScorpionFuelTruck,
    NeoScorpionSettings,
    NeoScorpionTailFuelState,
    SortDateMission,
    SortDateOperation,
    SortDateParkingAssignment,
    SortDateTailState,
    User,
)
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.neoscorpion import (
    CALCULATION_NOT_CONFIGURED_MESSAGE,
    display_thousands_to_lbs,
    gallons_to_lbs,
    lbs_to_display_thousands,
    lbs_to_gallons,
)
from app.services.parking_plan import set_tail_hot
from app.services.permission_rules import ensure_default_permission_rules
from app.services.password_policy import set_user_password


class NeoScorpionRoutesTest(unittest.TestCase):
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

    def test_fuel_unit_conversions(self):
        self.assertEqual(display_thousands_to_lbs("50.5"), 50500)
        self.assertEqual(display_thousands_to_lbs("13.6"), 13600)
        self.assertEqual(lbs_to_display_thousands(50500), Decimal("50.5"))
        self.assertEqual(gallons_to_lbs(100, 6.7), 670)
        self.assertEqual(lbs_to_gallons(670, 6.7), 100)

    def test_unauthenticated_users_cannot_access_neoscorpion_pages(self):
        for path in (
            "/neoscorpion",
            "/neoscorpion/fuel-dispatch",
            "/neoscorpion/fueler",
            "/neoscorpion/truck-manager",
            "/neoscorpion/settings",
            "/neoscorpion/history",
        ):
            with self.subTest(path=path):
                response = self.client.get(path, follow_redirects=False)
                self.assertEqual(response.status_code, 302)
                self.assertIn("/login", response.location)

    def test_dashboard_and_rfd_launch_render_neoscorpion_links(self):
        self._login_approved_user(role="master")

        dashboard = self.client.get("/neoscorpion")
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn(b"neoscorpion-dashboard-shell", dashboard.data)
        self.assertIn(b"node-desktop-nav-page", dashboard.data)
        self.assertIn(b"data-node-desktop-side-nav", dashboard.data)
        self.assertIn(b'data-node-desktop-shell="scorpion"', dashboard.data)
        desktop_sidebar = dashboard.data.split(b"data-node-desktop-side-nav", 1)[1].split(b"</aside>", 1)[0]
        self.assertIn(b"neoscorpion-256x256.png", desktop_sidebar)
        self.assertNotIn(b"neoscorpion-128x128.png", desktop_sidebar)
        self.assertIn(b'<span class="neo-page-title motherbrain-desktop-top-title-text">DASHBOARD</span>', dashboard.data)
        self.assertIn(b"neo-brand-title__node--scorpion", dashboard.data)
        self.assertIn(b'src="/static/images/icons/neoscorpion/inapp/neoscorpion-256x256.png"', dashboard.data)
        self.assertIn(b"data-node-desktop-dashboard", dashboard.data)
        self.assertIn(b'data-node-dashboard="scorpion"', dashboard.data)
        self.assertIn(b'data-node-dashboard-tile="dispatch"', dashboard.data)
        self.assertIn(b'data-node-dashboard-tile="fueler"', dashboard.data)
        self.assertIn(b'data-node-dashboard-tile="trucks"', dashboard.data)
        self.assertIn(b'data-node-dashboard-tile="settings"', dashboard.data)
        self.assertIn(b'data-node-dashboard-tile="history"', dashboard.data)
        self.assertIn(b'href="/neoscorpion/fuel-dispatch"', dashboard.data)
        self.assertIn(b"Fuel Dispatch", dashboard.data)
        self.assertIn(b"Fueler", dashboard.data)
        self.assertIn(b"Truck Manager", dashboard.data)
        self.assertIn(b"Settings", dashboard.data)
        self.assertIn(b"Fuel History", dashboard.data)

    def test_mobile_topbar_uses_complete_short_labels_without_ellipsis(self):
        self._login_approved_user(role="master")

        expected_labels = {
            "/neoscorpion/fuel-dispatch": "DISPATCH",
            "/neoscorpion/fueler": "FUELER",
            "/neoscorpion/truck-manager": "TRUCKS",
            "/neoscorpion/settings": "SETTINGS",
            "/neoscorpion/history": "HISTORY",
        }

        for path, label in expected_labels.items():
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(
                    f'<span class="mobile-topbar-page-name neo-page-title">{label}</span>'.encode(),
                    response.data,
                )

        hub = self.client.get("/rfd")
        self.assertEqual(hub.status_code, 200)
        self.assertIn(b'href="/neoscorpion"', hub.data)
        self.assertIn(b'src="/static/images/icons/neoscorpion/inapp/neoscorpion-128x128.png"', hub.data)

    def test_fuel_dispatch_assembles_owned_data(self):
        user = self._login_approved_user(role="simulator")
        operation, mission = self._add_current_departure(
            flight_number="UPS901",
            tail_number="N123UP",
            destination="ONT",
            planned_fuel_load=50500,
        )
        self._add_current_departure(
            flight_number="UPS902",
            tail_number="N456UP",
            destination="EWR",
        )
        self._add_current_arrival(
            operation,
            flight_number="UPS801",
            tail_number="N123UP",
            origin="PHL",
            eta_datetime_utc=datetime(2026, 6, 26, 3, 10),
            arrival_status="en_route",
        )
        truck = NeoScorpionFuelTruck(
            gateway_id=self.gateway.id,
            truck_number="TRUCK 7",
            remaining_fuel_gallons=3400,
            vendor_driver_name="Vendor Driver",
        )
        db.session.add(truck)
        db.session.flush()
        db.session.add(
            NeoScorpionFuelAssignment(
                sort_date_operation_id=operation.id,
                sort_date_mission_id=mission.id,
                assigned_fueler_user_id=user.id,
                assigned_truck_id=truck.id,
                review_status="assigned",
            )
        )
        db.session.add(
            NeoScorpionTailFuelState(
                sort_date_operation_id=operation.id,
                tail_number="N123UP",
                inbound_fuel_lbs=13600,
                fob_lbs=14100,
                apu_lbs=300,
            )
        )
        db.session.add(
            SortDateParkingAssignment(
                sort_date_operation_id=operation.id,
                tail_number="N123UP",
                ramp_code="D",
                position_code="07",
                lane_number=1,
            )
        )
        db.session.commit()

        response = self.client.get("/neoscorpion/fuel-dispatch")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"UPS901", response.data)
        self.assertIn(b"UPS902", response.data)
        self.assertIn(b"N123UP", response.data)
        self.assertIn(b"N456UP", response.data)
        self.assertIn(b"A300", response.data)
        self.assertIn(b"ONT", response.data)
        self.assertIn(b"EWR", response.data)
        self.assertIn(b"22:10", response.data)
        self.assertIn(b"En Route", response.data)
        self.assertIn(b"neoscorpion-dispatch-divider-cell", response.data)
        self.assertIn(b"D07", response.data)
        self.assertIn(b'value="50.5"', response.data)
        self.assertIn(b'value="13.6"', response.data)
        self.assertIn(b"14.1", response.data)
        self.assertIn(b"TRUCK 7", response.data)
        self.assertIn(b"3400 gal", response.data)
        self.assertIn(b"INOP", response.data)
        self.assertNotIn(CALCULATION_NOT_CONFIGURED_MESSAGE.encode(), response.data)

        header = response.data.split(b"<thead>", 1)[1].split(b"</thead>", 1)[0]
        for earlier, later in (
            (b"Tail", b"Arrival ETA"),
            (b"Arrival ETA", b"Arrival Status"),
            (b"Arrival Status", b"Departure Flight"),
            (b"Departure Flight", b"Dest"),
            (b"Dest", b"Parking"),
            (b"Parking", b"ETD"),
        ):
            self.assertLess(header.index(earlier), header.index(later))

    def test_fuel_dispatch_includes_hot_departure_and_preserves_std(self):
        self._login_approved_user(role="simulator")
        operation, mission = self._add_current_departure(
            flight_number="UPS901",
            tail_number="N123UP",
            destination="ONT",
        )
        expected_std = mission.planned_datetime_local

        set_tail_hot(operation, "N123UP", True)
        db.session.commit()
        marked = self.client.get("/neoscorpion/fuel-dispatch")
        set_tail_hot(operation, "N123UP", False)
        db.session.commit()
        restored = self.client.get("/neoscorpion/fuel-dispatch")
        db.session.refresh(mission)

        self.assertEqual(mission.planned_datetime_local, expected_std)
        self.assertNotEqual(mission.departure_status, "cancelled")
        self.assertIn(b"UPS901", marked.data)
        self.assertIn(b"23:30", marked.data)
        self.assertIn(b"UPS901", restored.data)
        self.assertIn(b"23:30", restored.data)

    def test_fuel_dispatch_excludes_standalone_spare_tail(self):
        self._login_approved_user(role="simulator")
        operation, _mission = self._add_current_departure(
            flight_number="UPS901",
            tail_number="N123UP",
            destination="ONT",
        )
        db.session.add(
            SortDateTailState(
                sort_date=operation.sort_date,
                gateway_code=operation.gateway_code,
                sort_name=operation.sort_name,
                tail_number="N555UP",
                aircraft_type="767",
                aircraft_type_source="manual",
                operational_status="spare",
            )
        )
        db.session.add(
            SortDateParkingAssignment(
                sort_date_operation_id=operation.id,
                tail_number="N555UP",
                ramp_code="A",
                position_code="A01",
                lane_number=1,
            )
        )
        db.session.commit()

        response = self.client.get("/neoscorpion/fuel-dispatch")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"UPS901", response.data)
        self.assertNotIn(b"N555UP", response.data)

    def test_fueler_sees_only_assigned_missions_and_a300_center_fuel(self):
        user = self._login_approved_user(role="operator")
        operation, first = self._add_current_departure("UPS301", "N123UP", "SDF")
        _operation, second = self._add_current_departure("UPS302", "N456UP", "EWR")
        other = User(username="other_fueler", email="other@example.test", role="watcher")
        set_user_password(other, "TestPassword123!")
        db.session.add(other)
        db.session.flush()
        db.session.add_all(
            [
                NeoScorpionFuelAssignment(
                    sort_date_operation_id=operation.id,
                    sort_date_mission_id=first.id,
                    assigned_fueler_user_id=user.id,
                ),
                NeoScorpionFuelAssignment(
                    sort_date_operation_id=operation.id,
                    sort_date_mission_id=second.id,
                    assigned_fueler_user_id=other.id,
                ),
            ]
        )
        db.session.commit()

        response = self.client.get("/neoscorpion/fueler")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"UPS301", response.data)
        self.assertNotIn(b"UPS302", response.data)
        self.assertIn(b"Center Fuel", response.data)
        self.assertIn(CALCULATION_NOT_CONFIGURED_MESSAGE.encode(), response.data)

    def test_truck_manager_can_add_vendor_driver_truck(self):
        self._login_approved_user(role="simulator")

        response = self.client.post(
            "/neoscorpion/truck-manager",
            data={
                "action": "save_truck",
                "truck_number": "truck 12",
                "capacity_gallons": "8000",
                "remaining_fuel_gallons": "6200",
                "vendor_driver_name": "Casey Vendor",
                "description": "North pad",
                "is_active": "1",
            },
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        truck = NeoScorpionFuelTruck.query.filter_by(truck_number="TRUCK 12").one()
        self.assertEqual(truck.remaining_fuel_gallons, 6200)
        self.assertEqual(truck.vendor_driver_name, "Casey Vendor")
        self.assertIn(b"TRUCK 12", response.data)
        self.assertIn(b"Casey Vendor", response.data)

    def test_settings_shell_saves_thresholds(self):
        self._login_approved_user(role="master")

        response = self.client.post(
            "/neoscorpion/settings",
            data={
                "fuel_density_lbs_per_gallon": "6.8",
                "fob_difference_threshold_lbs": "500",
                "tf_vs_estimated_threshold_lbs": "750",
            },
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        settings = NeoScorpionSettings.query.filter_by(gateway_id=self.gateway.id).one()
        self.assertEqual(settings.fuel_density_lbs_per_gallon, 6.8)
        self.assertEqual(settings.fob_difference_threshold_lbs, 500)
        self.assertEqual(settings.tf_vs_estimated_threshold_lbs, 750)
        self.assertIn(b"Detailed aircraft-specific fuel calculations are not configured yet.", response.data)

    def test_history_placeholder_is_permission_protected(self):
        self._login_approved_user(role="operator")

        response = self.client.get("/neoscorpion/history")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Completed fuel history is ready", response.data)

    def _login_approved_user(self, role="watcher"):
        user = User(
            username=f"neoscorpion_{role}_user",
            email=f"neoscorpion_{role}@example.test",
            role="watcher",
        )
        set_user_password(user, "TestPassword123!")
        db.session.add(user)
        db.session.flush()

        membership = GatewayMembership(
            user_id=user.id,
            gateway_id=self.gateway.id,
            status="approved",
            is_active=True,
        )
        db.session.add(membership)
        db.session.flush()

        scorpion = NeoNode.query.filter_by(code="scorpion").one()
        db.session.add(
            GatewayNodeRole(
                gateway_membership_id=membership.id,
                node_id=scorpion.id,
                role=role,
                is_active=True,
            )
        )
        db.session.commit()

        self.client.post(
            "/login",
            data={"email": user.email, "password": "TestPassword123!"},
            follow_redirects=False,
        )
        return user

    def _add_current_departure(
        self,
        flight_number="UPS900",
        tail_number="N123UP",
        destination="SDF",
        planned_fuel_load=None,
    ):
        operation = SortDateOperation.query.filter_by(
            gateway_code=self.gateway.code,
            sort_name="night",
        ).first()
        if not operation:
            operation = SortDateOperation(
                gateway_id=self.gateway.id,
                sort_date=date(2026, 6, 25),
                gateway_code=self.gateway.code,
                sort_name="night",
                window_minutes=360,
            )
            db.session.add(operation)
            db.session.flush()

        mission = SortDateMission(
            sort_date=operation.sort_date,
            gateway_code=operation.gateway_code,
            sort_name=operation.sort_name,
            sort_date_operation_id=operation.id,
            mission_type="departure",
            mission_source="manual",
            flight_number=flight_number,
            origin=operation.gateway_code,
            destination=destination,
            timezone="America/Chicago",
            planned_datetime_local=datetime(2026, 6, 25, 23, 30),
            planned_datetime_utc=datetime(2026, 6, 26, 4, 30),
            planned_source="manual",
            assigned_tail_number=tail_number,
            tail_source="manual",
            planned_fuel_load=planned_fuel_load,
            fuel_status="waiting",
            departure_status="loading",
        )
        db.session.add(mission)
        db.session.add(
            SortDateTailState(
                sort_date=operation.sort_date,
                gateway_code=operation.gateway_code,
                sort_name=operation.sort_name,
                tail_number=tail_number,
                aircraft_type="A300" if tail_number == "N123UP" else "757",
                aircraft_type_source="derived",
            )
        )
        db.session.commit()
        return operation, mission

    def _add_current_arrival(
        self,
        operation,
        flight_number="UPS800",
        tail_number="N123UP",
        origin="PHL",
        eta_datetime_utc=None,
        arrival_status="scheduled",
    ):
        mission = SortDateMission(
            sort_date=operation.sort_date,
            gateway_code=operation.gateway_code,
            sort_name=operation.sort_name,
            sort_date_operation_id=operation.id,
            mission_type="arrival",
            mission_source="manual",
            flight_number=flight_number,
            origin=origin,
            destination=operation.gateway_code,
            timezone="America/Chicago",
            planned_datetime_local=datetime(2026, 6, 25, 21, 30),
            planned_datetime_utc=datetime(2026, 6, 26, 2, 30),
            planned_source="manual",
            eta_datetime_utc=eta_datetime_utc,
            eta_source="manual" if eta_datetime_utc else "unknown",
            assigned_tail_number=tail_number,
            tail_source="manual",
            fuel_status="waiting",
            arrival_status=arrival_status,
        )
        db.session.add(mission)
        db.session.commit()
        return mission


if __name__ == "__main__":
    unittest.main()
