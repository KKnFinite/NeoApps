from datetime import date, datetime, time
import unittest

from app import create_app
from app.extensions import db
from app.models import (
    Gateway,
    GatewayMembership,
    GatewaySortMatrix,
    MasterFlightSchedule,
    SortDateCrewAssignment,
    SortDateMission,
    SortDateOperation,
    SortDateTailState,
    User,
)
from app.services.access_control import backfill_default_gateway_node_roles
from app.services.gateway_matrix import current_gateway_local_date


class MotherBrainRoutesTest(unittest.TestCase):
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

        user = User(username="Kessler", role="grandmaster")
        user.set_password("TestPassword123!")
        db.session.add(user)
        db.session.flush()
        backfill_default_gateway_node_roles(user, role="grandmaster")
        self.rfd_gateway = Gateway.query.filter_by(code="RFD").first()
        db.session.commit()

        self.client = self.app.test_client()
        self.client.post(
            "/login",
            data={"username": "Kessler", "password": "TestPassword123!"},
        )

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_logged_in_user_can_access_motherbrain_home(self):
        response = self.client.get("/motherbrain")
        html = response.data.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'src="/static/images/motherbrain_logo1.png"', response.data)
        self.assertIn(b"blueprint-neomotherbrain", response.data)
        self.assertIn(b"motherbrain-fixed-header", response.data)
        self.assertIn(b"motherbrain-home-page", response.data)
        self.assertIn(b'class="motherbrain-header-logo-link"', response.data)
        self.assertIn(b'class="motherbrain-header-logo"', response.data)
        self.assertIn(b"motherbrain-screen-logo", response.data)
        self.assertNotIn(b"NeoMotherBrain", response.data)
        self.assertNotIn(b"NEOMOTHERBRAIN", response.data)
        self.assertNotIn(b">Command<", response.data)
        self.assertNotIn(b"Command Console", response.data)
        self.assertNotIn(b"NeoRFD Command", response.data)
        self.assertNotIn(b"NeoRFD command", response.data)
        self.assertNotIn(b"NEORFD COMMAND", response.data)
        self.assertIn(b'aria-label="Primary"', response.data)
        self.assertNotIn(b'aria-label="MotherBrain menu"', response.data)
        self.assertNotIn(b'class="panel motherbrain-landing"', response.data)
        self.assertNotIn(b"action-button-secondary", response.data)
        self.assertNotIn(b'class="metric-grid"', response.data)
        self.assertNotIn(b"Master Schedule Rows", response.data)
        self.assertIn(b"User Management", response.data)
        self.assertIn(b"Back to NeoGateway", response.data)
        self.assertIn(b"Gateway Matrix", response.data)
        self.assertIn(b"Master Schedule", response.data)
        self.assertIn(b"Manage Sort", response.data)
        self.assertIn(b"MotherBrain Dashboard", response.data)
        self.assertIn(b"Manage current sorts", response.data)
        self.assertIn(b"Manage master flight schedule", response.data)
        self.assertIn(b"Assign active sorts", response.data)
        self.assertIn(b"User and access controls", response.data)
        self.assertNotIn(b"Gateway Matris", response.data)
        dashboard_html = html.split('class="motherbrain-dashboard-grid"', 1)[1]
        self.assertLess(dashboard_html.index("Manage Sort"), dashboard_html.index("Master Schedule"))
        self.assertLess(dashboard_html.index("Master Schedule"), dashboard_html.index("Gateway Matrix"))
        self.assertLess(dashboard_html.index("Gateway Matrix"), dashboard_html.index("User Management"))
        self.assertIn(b'href="/rfd"', response.data)
        self.assertIn(b"Logout", response.data)
        self.assertIn(b'data-motherbrain-menu-button', response.data)
        self.assertIn(b'aria-expanded="false"', response.data)
        self.assertIn(b'aria-controls="motherbrain-mobile-menu"', response.data)
        self.assertIn(b'id="motherbrain-mobile-menu"', response.data)
        self.assertIn(b'href="/admin/users"', response.data)
        self.assertIn(b'href="/motherbrain/gateway-matrix"', response.data)
        self.assertIn(b'href="/motherbrain/master-schedule"', response.data)
        self.assertIn(b'href="/motherbrain/manage-sort"', response.data)
        self.assertIn(b'href="/logout"', response.data)
        self.assertNotIn(b"Access Requests", response.data)
        self.assertNotIn(b"Generate Nightly Operation", response.data)

    def test_motherbrain_header_navigation_routes_work(self):
        routes = {
            "/admin/users": b'href="/admin/users" aria-current="page"',
            "/motherbrain/gateway-matrix": b'href="/motherbrain/gateway-matrix" aria-current="page"',
            "/motherbrain/master-schedule": b'href="/motherbrain/master-schedule" aria-current="page"',
            "/motherbrain/manage-sort": b'href="/motherbrain/manage-sort" aria-current="page"',
        }

        for path, active_link in routes.items():
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(b"motherbrain-fixed-header", response.data)
                self.assertIn(b'class="motherbrain-header-logo-link"', response.data)
                self.assertIn(b'class="motherbrain-header-logo"', response.data)
                self.assertNotIn(b"NeoMotherBrain", response.data)
                self.assertNotIn(b"NEOMOTHERBRAIN", response.data)
                self.assertNotIn(b">Command<", response.data)
                self.assertNotIn(b"Command Console", response.data)
                self.assertNotIn(b"NeoRFD Command", response.data)
                self.assertNotIn(b"NeoRFD command", response.data)
                self.assertNotIn(b"NEORFD COMMAND", response.data)
                self.assertNotIn(b"motherbrain-screen-logo", response.data)
                self.assertIn(b"Back to NeoGateway", response.data)
                self.assertIn(b"User Management", response.data)
                self.assertIn(b"Gateway Matrix", response.data)
                self.assertIn(b"Master Schedule", response.data)
                self.assertIn(b"Manage Sort", response.data)
                self.assertIn(b'href="/rfd"', response.data)
                self.assertIn(b'href="/logout"', response.data)
                self.assertIn(b'data-motherbrain-menu-button', response.data)
                self.assertIn(b'aria-controls="motherbrain-mobile-menu"', response.data)
                self.assertIn(b'id="motherbrain-mobile-menu"', response.data)
                self.assertIn(active_link, response.data)
                self.assertIn(b'aria-current="page"', response.data)

        rfd_response = self.client.get("/rfd")
        self.assertEqual(rfd_response.status_code, 200)
        self.assertIn(b"NeoGateway", rfd_response.data)

        still_authenticated = self.client.get("/motherbrain")
        self.assertEqual(still_authenticated.status_code, 200)

        logout_response = self.client.get("/logout", follow_redirects=False)
        self.assertEqual(logout_response.status_code, 302)

    def test_gateway_matrix_displays_dynamic_gateway_and_sort_order(self):
        response = self.client.get("/motherbrain/gateway-matrix")
        html = response.data.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn("centered-command-page", html)
        self.assertIn("gateway-matrix-heading-block", html)
        self.assertIn("Set active sorts for RFD", html)
        self.assertLess(
            html.index('name="monday_sunrise"'),
            html.index('name="monday_day"'),
        )
        self.assertLess(
            html.index('name="monday_day"'),
            html.index('name="monday_twilight"'),
        )
        self.assertLess(
            html.index('name="monday_twilight"'),
            html.index('name="monday_night"'),
        )

    def test_gateway_matrix_saves_current_gateway_sort_toggles(self):
        response = self.client.post(
            "/motherbrain/gateway-matrix",
            data={
                "monday_night": "1",
                "monday_day": "1",
                "tuesday_twilight": "1",
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        active_rows = GatewaySortMatrix.query.filter_by(
            gateway_id=self.rfd_gateway.id,
            is_active=True,
        ).all()
        self.assertEqual(
            {(row.day_of_week, row.sort_name) for row in active_rows},
            {
                ("monday", "night"),
                ("monday", "day"),
                ("tuesday", "twilight"),
            },
        )
        monday_day = GatewaySortMatrix.query.filter_by(
            gateway_id=self.rfd_gateway.id,
            day_of_week="monday",
            sort_name="day",
        ).one()
        self.assertEqual(monday_day.gateway_code, "RFD")
        self.assertTrue(monday_day.is_active)

    def test_motherbrain_auto_generates_today_active_matrix_sorts(self):
        sort_date = current_gateway_local_date(self.rfd_gateway)
        day = sort_date.strftime("%A").lower()
        self._add_matrix_cell(day, "night")
        self._add_master(
            flight_number="AUTO01",
            active_days=day,
            sort_name="night",
        )
        db.session.commit()

        response = self.client.get("/motherbrain")

        operation = SortDateOperation.query.filter_by(
            gateway_code="RFD",
            sort_date=sort_date,
            sort_name="night",
        ).first()
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(operation)
        self.assertEqual(len(operation.missions), 1)
        self.assertEqual(operation.missions[0].flight_number, "AUTO01")

    def test_manage_sort_creates_missing_operations_without_duplicates(self):
        sort_date = current_gateway_local_date(self.rfd_gateway)
        day = sort_date.strftime("%A").lower()
        self._add_matrix_cell(day, "night")
        self._add_master(
            flight_number="SORT01",
            active_days=day,
            sort_name="night",
        )
        db.session.commit()

        first_response = self.client.get("/motherbrain/manage-sort")
        second_response = self.client.get("/motherbrain/manage-sort")

        operations = SortDateOperation.query.filter_by(
            gateway_code="RFD",
            sort_date=sort_date,
            sort_name="night",
        ).all()
        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(len(operations), 1)
        self.assertIn(b"Manage Sort", first_response.data)
        self.assertIn(b"Night", first_response.data)
        self.assertIn(b"Add Special Flight", first_response.data)
        html = first_response.data.decode()
        main_html = html.split('<main class="content">', 1)[1].split("</main>", 1)[0]
        self.assertNotIn('href="/motherbrain/gateway-matrix"', main_html)
        self.assertNotIn('href="/motherbrain/master-schedule"', main_html)
        self.assertNotIn('href="/motherbrain"', main_html)

    def test_manage_sort_syncs_new_master_rows_into_existing_operation(self):
        sort_date = current_gateway_local_date(self.rfd_gateway)
        day = sort_date.strftime("%A").lower()
        operation = self._operation(sort_date=sort_date)
        db.session.add(operation)
        master = self._add_master(
            flight_number="SYNCIN",
            active_days=day,
        )
        db.session.commit()

        response = self.client.get("/motherbrain/manage-sort")

        mission = SortDateMission.query.filter_by(flight_number="SYNCIN").one()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(mission.sort_date_operation_id, operation.id)
        self.assertEqual(mission.master_flight_schedule_id, master.id)
        self.assertEqual(mission.mission_source, "master")

    def test_operation_detail_syncs_newer_master_template_fields(self):
        master = self._add_master(
            flight_number="SYNCUP",
            active_days="monday",
            destination="SDF",
        )
        db.session.flush()
        operation = self._operation(
            sort_date=date(2026, 6, 1),
            generated_at_utc=datetime(2026, 1, 1, 0, 0),
        )
        db.session.add(operation)
        db.session.flush()
        mission = self._mission(
            operation=operation,
            mission_type="departure",
            flight_number="SYNCUP",
            mission_source="master",
            master_flight_schedule_id=master.id,
            destination="OLD",
        )
        db.session.add(mission)
        db.session.flush()
        master.destination = "ONT"
        master.planned_time_local = time(3, 20)
        master.updated_at = datetime(2026, 1, 2, 0, 0)
        db.session.commit()

        response = self.client.get(f"/motherbrain/operations/{operation.id}")

        updated_mission = db.session.get(SortDateMission, mission.id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(updated_mission.destination, "ONT")
        self.assertEqual(updated_mission.planned_datetime_local, datetime(2026, 6, 1, 3, 20))

    def test_manage_sort_empty_state_is_simple_centered_message(self):
        response = self.client.get("/motherbrain/manage-sort")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"No active sorts today.", response.data)
        self.assertIn(b"centered-empty-message", response.data)
        self.assertNotIn(b"No Active Sorts Today", response.data)
        self.assertNotIn(b"Open Gateway Matrix", response.data)
        self.assertNotIn(b"Enable today", response.data)

    def test_kessler_grandmaster_can_access_motherbrain_pages(self):
        operation = self._operation()
        db.session.add(operation)
        mission = self._mission(
            operation=operation,
            mission_type="departure",
            flight_number="DEPACCESS",
        )
        db.session.add(mission)
        db.session.commit()

        get_paths = (
            "/motherbrain",
            "/motherbrain/gateway-matrix",
            "/motherbrain/manage-sort",
            "/motherbrain/operations",
            "/motherbrain/operations/new",
            "/motherbrain/master-schedule",
            "/motherbrain/master-schedule/new",
            "/motherbrain/master-schedule/bulk-edit",
            f"/motherbrain/operations/{operation.id}",
            f"/motherbrain/operations/{operation.id}/arrivals",
            f"/motherbrain/operations/{operation.id}/departures",
            f"/motherbrain/operations/{operation.id}/missions/new",
            f"/motherbrain/operations/{operation.id}/missions/{mission.id}",
            f"/motherbrain/operations/{operation.id}/missions/{mission.id}/edit",
        )

        for path in get_paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(b'src="/static/images/motherbrain_logo1.png"', response.data)
                self.assertIn(b'class="motherbrain-header-logo-link"', response.data)
                if path == "/motherbrain":
                    self.assertIn(b"motherbrain-home-page", response.data)
                    self.assertIn(b"motherbrain-screen-logo", response.data)
                else:
                    self.assertNotIn(b"motherbrain-home-page", response.data)
                    self.assertNotIn(b"motherbrain-screen-logo", response.data)
                self.assertNotIn(b"NeoMotherBrain", response.data)
                self.assertNotIn(b"NEOMOTHERBRAIN", response.data)
                self.assertNotIn(b">Command<", response.data)
                self.assertNotIn(b"Command Console", response.data)
                self.assertNotIn(b"NeoRFD Command", response.data)
                self.assertNotIn(b"NeoRFD command", response.data)
                self.assertNotIn(b"NEORFD COMMAND", response.data)
                self.assertNotIn(b"<p class=\"eyebrow\">NeoMotherBrain</p>", response.data)

        response = self.client.post(
            f"/motherbrain/operations/{operation.id}/window",
            data={"window_minutes": "10"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)

    def test_master_schedule_requires_login(self):
        operation = self._operation()
        db.session.add(operation)
        db.session.commit()
        self.client.get("/logout")

        protected_paths = (
            "/motherbrain/master-schedule",
            "/motherbrain/operations",
            f"/motherbrain/operations/{operation.id}/missions/new",
        )
        for path in protected_paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 302)
                self.assertIn("/login", response.location)

    def test_user_without_rfd_access_cannot_enter_motherbrain(self):
        dfw_gateway = self._gateway("DFW", "NeoDFW")
        user = User(username="dfw_only", role="grandmaster")
        user.set_password("TestPassword123!")
        db.session.add(user)
        db.session.flush()
        db.session.add(
            GatewayMembership(
                user_id=user.id,
                gateway_id=dfw_gateway.id,
                status="approved",
                is_active=True,
            )
        )
        db.session.commit()

        client = self.app.test_client()
        client.post(
            "/login",
            data={"username": "dfw_only", "password": "TestPassword123!"},
        )
        response = client.get("/motherbrain", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/access-pending")

    def test_motherbrain_routes_do_not_leak_other_gateway_records(self):
        dfw_gateway = self._gateway("DFW", "NeoDFW")
        rfd_master = self._add_master(flight_number="RFD001", gateway_id=self.rfd_gateway.id)
        dfw_master = self._add_master(
            flight_number="DFW001",
            gateway_id=dfw_gateway.id,
            gateway_code="DFW",
            origin="DFW",
            destination="ONT",
        )
        rfd_operation = self._operation(gateway_id=self.rfd_gateway.id, gateway_code="RFD")
        dfw_operation = self._operation(gateway_id=dfw_gateway.id, gateway_code="DFW")
        db.session.add_all((rfd_operation, dfw_operation))
        db.session.flush()
        dfw_mission = self._mission(
            dfw_operation,
            "departure",
            "DFWDEP",
            gateway_code="DFW",
            origin="DFW",
            destination="ONT",
        )
        db.session.add(dfw_mission)
        db.session.commit()

        master_list = self.client.get("/motherbrain/master-schedule")
        operations_list = self.client.get("/motherbrain/operations")
        dfw_master_detail = self.client.get(f"/motherbrain/master-schedule/{dfw_master.id}")
        dfw_master_edit = self.client.get(f"/motherbrain/master-schedule/{dfw_master.id}/edit")
        dfw_operation_detail = self.client.get(f"/motherbrain/operations/{dfw_operation.id}")
        dfw_arrivals = self.client.get(f"/motherbrain/operations/{dfw_operation.id}/arrivals")
        dfw_departures = self.client.get(f"/motherbrain/operations/{dfw_operation.id}/departures")
        dfw_mission_detail = self.client.get(
            f"/motherbrain/operations/{dfw_operation.id}/missions/{dfw_mission.id}"
        )

        self.assertEqual(master_list.status_code, 200)
        self.assertIn(rfd_master.flight_number.encode(), master_list.data)
        self.assertNotIn(b"DFW001", master_list.data)
        self.assertEqual(operations_list.status_code, 200)
        self.assertIn(str(rfd_operation.sort_date).encode(), operations_list.data)
        self.assertNotIn(b"DFW", operations_list.data)
        for response in (
            dfw_master_detail,
            dfw_master_edit,
            dfw_operation_detail,
            dfw_arrivals,
            dfw_departures,
            dfw_mission_detail,
        ):
            self.assertEqual(response.status_code, 404)

    def test_logged_in_user_can_view_master_schedule_list(self):
        self._add_master(
            flight_number="ARR001",
            mission_type="arrival",
            origin="SDF",
            destination="RFD",
        )
        self._add_master(
            flight_number="DEP001",
            pure_pull_time_local=time(1, 10),
            first_mix_pull_time_local=time(1, 25),
            final_mix_pull_time_local=time(1, 40),
        )
        db.session.commit()

        response = self.client.get("/motherbrain/master-schedule")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Master Flight Schedule", response.data)
        self.assertIn(b"centered-command-page", response.data)
        self.assertIn(b"Master Arrivals", response.data)
        self.assertIn(b"Master Departures", response.data)
        self.assertIn(b"class=\"master-board-form\"", response.data)
        self.assertNotIn(b"Add Master Flights", response.data)
        self.assertNotIn(b"Add Arrival", response.data)
        self.assertNotIn(b"Add Departure", response.data)
        self.assertNotIn(b"Edit Multiple Rows", response.data)
        self.assertIn(b"<th>Origin</th>", response.data)
        self.assertIn(b"<th>AC Type</th>", response.data)
        self.assertIn(b"<th>STA</th>", response.data)
        self.assertIn(b"<th>Destination</th>", response.data)
        self.assertIn(b"<th>ETD/STD</th>", response.data)
        self.assertIn(b"<th>Pure Pull</th>", response.data)
        self.assertIn(b"<th>1st Mix</th>", response.data)
        self.assertIn(b"<th>2nd Mix</th>", response.data)
        self.assertIn(b"data-label=\"ETD/STD\"", response.data)
        self.assertIn(b"data-label=\"AC Type\"", response.data)
        self.assertIn(b"data-label=\"2nd Mix\"", response.data)
        self.assertIn(b"ARR001", response.data)
        self.assertIn(b"DEP001", response.data)
        self.assertIn(b"SDF", response.data)
        self.assertIn(b'name="row_arrival_0_planned_time_local_hour"', response.data)
        self.assertIn(b'name="row_arrival_0_aircraft_type"', response.data)
        self.assertIn(b'name="row_departure_0_aircraft_type"', response.data)
        self.assertIn(b'name="row_departure_0_pure_pull_time_local_hour"', response.data)
        self.assertIn(b'name="row_departure_0_first_mix_pull_time_local_hour"', response.data)
        self.assertIn(b'name="row_departure_0_final_mix_pull_time_local_hour"', response.data)
        self.assertIn(b'name="row_arrival_new_flight_number"', response.data)
        self.assertIn(b'name="row_departure_new_flight_number"', response.data)
        self.assertNotIn(b"Save Master Arrivals", response.data)
        self.assertNotIn(b"Save Master Departures", response.data)
        for aircraft_type in (b"A300", b"747", b"757", b"767", b"Other"):
            self.assertIn(b'<option value="' + aircraft_type + b'"', response.data)
        self.assertNotIn(b">Edit</a>", response.data)
        self.assertIn(b">Delete</button>", response.data)
        self.assertNotIn(b"<th>Tail</th>", response.data)
        self.assertNotIn(b"<th>Parking</th>", response.data)
        self.assertNotIn(b"<th>ETA</th>", response.data)
        self.assertNotIn(b"<th>ETD</th>", response.data)
        self.assertNotIn(b"<th>STD</th>", response.data)
        self.assertNotIn(b"<th>Final Mix</th>", response.data)
        self.assertNotIn(b"data-label=\"Tail\"", response.data)
        self.assertNotIn(b"data-label=\"Parking\"", response.data)
        self.assertNotIn(b"data-label=\"ETA\"", response.data)
        self.assertNotIn(b"<th>Mission</th>", response.data)
        self.assertNotIn(b"Origin/Destination", response.data)
        self.assertNotIn(b"<th>Sort</th>", response.data)
        self.assertNotIn(b"<th>Active Days</th>", response.data)
        self.assertNotIn(b"<td>night</td>", response.data)
        self.assertNotIn(b"<td>departure</td>", response.data)

    def test_master_schedule_board_save_updates_existing_and_creates_new_row(self):
        existing = self._add_master(
            flight_number="DEPOLD",
            destination="SDF",
            active_days="monday,tuesday",
        )
        db.session.commit()

        response = self.client.post(
            "/motherbrain/master-schedule",
            data={
                "board_mission_type": "departure",
                "row_indexes": ["departure_0", "departure_new"],
                "row_departure_0_id": str(existing.id),
                "row_departure_0_mission_type": "departure",
                "row_departure_0_sort_name": "night",
                "row_departure_0_active": "1",
                "row_departure_0_active_days": ["monday", "tuesday"],
                "row_departure_0_flight_number": "depold",
                "row_departure_0_aircraft_type": "757",
                "row_departure_0_origin": "RFD",
                "row_departure_0_destination": "ont",
                "row_departure_0_planned_time_local": "03:15",
                "row_departure_0_pure_pull_time_local": "01:10",
                "row_departure_0_first_mix_pull_time_local": "01:25",
                "row_departure_0_final_mix_pull_time_local": "01:40",
                "row_departure_new_id": "",
                "row_departure_new_mission_type": "departure",
                "row_departure_new_sort_name": "night",
                "row_departure_new_active": "1",
                "row_departure_new_active_days": ["monday", "tuesday"],
                "row_departure_new_flight_number": "depnew",
                "row_departure_new_aircraft_type": "747",
                "row_departure_new_origin": "RFD",
                "row_departure_new_destination": "sdf",
                "row_departure_new_planned_time_local": "04:20",
                "row_departure_new_pure_pull_time_local": "02:10",
                "row_departure_new_first_mix_pull_time_local": "02:25",
                "row_departure_new_final_mix_pull_time_local": "02:40",
            },
            follow_redirects=False,
        )

        updated = db.session.get(MasterFlightSchedule, existing.id)
        created = MasterFlightSchedule.query.filter_by(flight_number="DEPNEW").first()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(updated.flight_number, "DEPOLD")
        self.assertEqual(updated.destination, "ONT")
        self.assertEqual(updated.aircraft_type, "757")
        self.assertEqual(updated.planned_time_local, time(3, 15))
        self.assertEqual(updated.pure_pull_time_local, time(1, 10))
        self.assertIsNotNone(created)
        self.assertEqual(created.origin, "RFD")
        self.assertEqual(created.destination, "SDF")
        self.assertEqual(created.aircraft_type, "747")
        self.assertEqual(created.planned_time_local, time(4, 20))
        self.assertEqual(created.final_mix_pull_time_local, time(2, 40))

    def test_master_schedule_board_autosave_json_updates_and_skips_incomplete_add_row(self):
        existing = self._add_master(
            flight_number="ARROLD",
            mission_type="arrival",
            origin="SDF",
            destination="RFD",
            active_days="monday,tuesday",
        )
        db.session.commit()

        response = self.client.post(
            "/motherbrain/master-schedule",
            data={
                "board_mission_type": "arrival",
                "row_indexes": ["arrival_0", "arrival_new"],
                "row_arrival_0_id": str(existing.id),
                "row_arrival_0_mission_type": "arrival",
                "row_arrival_0_sort_name": "night",
                "row_arrival_0_active": "1",
                "row_arrival_0_active_days": ["monday", "tuesday"],
                "row_arrival_0_flight_number": "arrold",
                "row_arrival_0_aircraft_type": "A300",
                "row_arrival_0_origin": "ont",
                "row_arrival_0_destination": "RFD",
                "row_arrival_0_planned_time_local": "03:15",
                "row_arrival_new_id": "",
                "row_arrival_new_mission_type": "arrival",
                "row_arrival_new_sort_name": "night",
                "row_arrival_new_active": "1",
                "row_arrival_new_active_days": ["monday", "tuesday"],
                "row_arrival_new_flight_number": "partial",
                "row_arrival_new_aircraft_type": "767",
                "row_arrival_new_origin": "",
                "row_arrival_new_destination": "RFD",
                "row_arrival_new_planned_time_local": "04:20",
            },
            headers={"Accept": "application/json", "X-Requested-With": "fetch"},
        )

        updated = db.session.get(MasterFlightSchedule, existing.id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["updated"], 1)
        self.assertEqual(response.get_json()["created"], 0)
        self.assertEqual(updated.origin, "ONT")
        self.assertEqual(updated.aircraft_type, "A300")
        self.assertIsNone(MasterFlightSchedule.query.filter_by(flight_number="PARTIAL").first())

    def test_master_schedule_form_uses_limited_sort_dropdown_and_capitalized_missions(self):
        response = self.client.get("/motherbrain/master-schedule/new")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'<form class="master-schedule-single-form"', response.data)
        self.assertIn(b'data-master-mode="arrival"', response.data)
        self.assertIn(b'data-master-mode="departure"', response.data)
        self.assertIn(b'<select name="row_0_sort_name">', response.data)
        self.assertIn(b'<select name="row_0_aircraft_type">', response.data)
        self.assertNotIn(b'name="sort_name" value=', response.data)
        for value, label in (
            (b"night", b"Night"),
            (b"twilight", b"Twilight"),
            (b"day", b"Day"),
            (b"sunrise", b"Sunrise"),
        ):
            self.assertIn(b'<option value="' + value + b'"', response.data)
            self.assertIn(b">" + label + b"</option>", response.data)
        self.assertIn(b">Arrival</a>", response.data)
        self.assertIn(b">Departure</a>", response.data)
        for aircraft_type in (b"A300", b"747", b"757", b"767", b"Other"):
            self.assertIn(b'<option value="' + aircraft_type + b'"', response.data)
        self.assertNotIn(b">arrival</option>", response.data)
        self.assertNotIn(b">departure</option>", response.data)

    def test_add_master_schedule_arrival_mode_does_not_render_pull_time_fields(self):
        response = self.client.get("/motherbrain/master-schedule/new?mission_type=arrival")
        html = response.data.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn('data-master-mode="arrival" aria-selected="true"', html)
        self.assertIn(">STA</span>", html)
        self.assertIn(b"Save Arrival", response.data)
        self.assertIn('<select name="row_0_sort_name">', html)
        self.assertIn(b">Sort</span>", response.data)
        self.assertIn(b">Origin</span>", response.data)
        self.assertNotIn(b">Destination</span>", response.data)
        self.assertNotIn(b">STD</span>", response.data)
        self.assertNotIn(b"Pure Pull", response.data)
        self.assertNotIn(b"First Mix Pull", response.data)
        self.assertNotIn(b"Final Mix Pull", response.data)
        self.assertNotIn(b"pure_pull_time_local_hour", response.data)
        self.assertNotIn(b"first_mix_pull_time_local_hour", response.data)
        self.assertNotIn(b"final_mix_pull_time_local_hour", response.data)

    def test_master_schedule_arrival_mode_hides_pull_time_fields(self):
        master = self._add_master(
            mission_type="arrival",
            flight_number="ARRMODE",
            origin="SDF",
            destination="RFD",
        )
        db.session.commit()

        response = self.client.get(f"/motherbrain/master-schedule/{master.id}/edit")
        html = response.data.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn(">STA</span>", html)
        self.assertIn(b"Save Arrival", response.data)
        self.assertIn('type="hidden" name="row_0_mission_type" value="arrival"', html)
        self.assertIn('type="hidden" name="row_0_sort_name" value="night"', html)
        self.assertNotIn('data-master-mode="arrival"', html)
        self.assertNotIn('data-master-mode="departure"', html)
        self.assertNotIn('<select name="row_0_sort_name">', html)
        self.assertNotIn(b">Sort</span>", response.data)
        self.assertIn(b">Origin</span>", response.data)
        self.assertNotIn(b">Destination</span>", response.data)
        self.assertNotIn(b">STD</span>", response.data)
        self.assertNotIn(b"Pure Pull", response.data)
        self.assertNotIn(b"First Mix Pull", response.data)
        self.assertNotIn(b"Final Mix Pull", response.data)
        self.assertNotIn(b"pure_pull_time_local_hour", response.data)
        self.assertNotIn(b"first_mix_pull_time_local_hour", response.data)
        self.assertNotIn(b"final_mix_pull_time_local_hour", response.data)

    def test_master_schedule_departure_mode_shows_pull_time_fields(self):
        response = self.client.get("/motherbrain/master-schedule/new")
        html = response.data.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn('data-master-mode="departure" aria-selected="true"', html)
        self.assertIn(">STD</span>", html)
        self.assertIn(b"Save Departure", response.data)
        self.assertIn(b">Destination</span>", response.data)
        self.assertNotIn(b">Origin</span>", response.data)
        self.assertNotIn(b">STA</span>", response.data)
        self.assertEqual(html.count('class="master-pull-field" data-departure-only'), 3)
        self.assertNotIn("data-departure-only hidden", html)
        self.assertIn(b"Pure Pull", response.data)
        self.assertIn(b"First Mix Pull", response.data)
        self.assertIn(b"Final Mix Pull", response.data)

    def test_rfd_master_schedule_airport_defaults_use_current_gateway(self):
        arrival = self.client.get("/motherbrain/master-schedule/new?mission_type=arrival")
        departure = self.client.get("/motherbrain/master-schedule/new?mission_type=departure")
        arrival_html = arrival.data.decode()
        departure_html = departure.data.decode()

        self.assertEqual(arrival.status_code, 200)
        self.assertIn('type="hidden" name="row_0_destination"', arrival_html)
        self.assertIn('value="RFD"', arrival_html)
        self.assertIn('name="row_0_origin"', arrival_html)
        self.assertNotIn('readonly aria-readonly="true"', arrival_html)
        self.assertEqual(departure.status_code, 200)
        self.assertIn('type="hidden" name="row_0_origin"', departure_html)
        self.assertIn('value="RFD"', departure_html)
        self.assertIn('name="row_0_destination"', departure_html)
        self.assertNotIn('readonly aria-readonly="true"', departure_html)

    def test_master_schedule_create_defaults_home_airport_from_current_gateway(self):
        arrival = self.client.post(
            "/motherbrain/master-schedule/new",
            data=self._master_schedule_form_data(
                mission_type="arrival",
                flight_number="ARRDEF",
                origin="SDF",
                destination="",
            ),
            follow_redirects=False,
        )
        departure = self.client.post(
            "/motherbrain/master-schedule/new",
            data=self._master_schedule_form_data(
                flight_number="DEPDEF",
                origin="",
                destination="SDF",
            ),
            follow_redirects=False,
        )

        arrival_master = MasterFlightSchedule.query.filter_by(flight_number="ARRDEF").first()
        departure_master = MasterFlightSchedule.query.filter_by(flight_number="DEPDEF").first()
        self.assertEqual(arrival.status_code, 302)
        self.assertEqual(departure.status_code, 302)
        self.assertEqual(arrival_master.destination, "RFD")
        self.assertEqual(departure_master.origin, "RFD")
        self.assertEqual(arrival_master.gateway_id, self.rfd_gateway.id)
        self.assertEqual(departure_master.gateway_id, self.rfd_gateway.id)

    def test_bulk_create_master_schedule_rows(self):
        response = self.client.post(
            "/motherbrain/master-schedule/new",
            data=self._bulk_master_schedule_form_data(
                {
                    "flight_number": "BULK001",
                    "origin": "rfd",
                    "destination": "sdf",
                    "planned_time_local": "01:10",
                    "pure_pull_time_local": "00:40",
                },
                {
                    "mission_type": "arrival",
                    "flight_number": "BULK002",
                    "origin": "sdf",
                    "destination": "rfd",
                    "planned_time_local": "03:20",
                    "pure_pull_time_local": "01:10",
                },
            ),
            follow_redirects=False,
        )

        departure = MasterFlightSchedule.query.filter_by(flight_number="BULK001").first()
        arrival = MasterFlightSchedule.query.filter_by(flight_number="BULK002").first()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/motherbrain/master-schedule")
        self.assertEqual(departure.origin, "RFD")
        self.assertEqual(departure.destination, "SDF")
        self.assertEqual(departure.pure_pull_time_local, time(0, 40))
        self.assertEqual(arrival.mission_type, "arrival")
        self.assertIsNone(arrival.pure_pull_time_local)

    def test_bulk_edit_master_schedule_rows(self):
        first = self._add_master(flight_number="EDITA1", active=True)
        second = self._add_master(
            flight_number="EDITA2",
            mission_type="arrival",
            origin="SDF",
            destination="RFD",
            active=True,
        )
        db.session.commit()

        response = self.client.post(
            "/motherbrain/master-schedule/bulk-edit",
            data=self._bulk_master_schedule_form_data(
                {
                    "id": str(first.id),
                    "flight_number": "EDITA1",
                    "origin": "RFD",
                    "destination": "ONT",
                    "planned_time_local": "04:15",
                    "active_days": ["monday", "friday"],
                },
                {
                    "id": str(second.id),
                    "mission_type": "departure",
                    "flight_number": "EDITA2",
                    "origin": "RFD",
                    "destination": "SDF",
                    "planned_time_local": "05:20",
                    "first_mix_pull_time_local": "04:45",
                },
            ),
            follow_redirects=False,
        )

        updated_first = db.session.get(MasterFlightSchedule, first.id)
        updated_second = db.session.get(MasterFlightSchedule, second.id)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(updated_first.destination, "ONT")
        self.assertEqual(updated_first.active_days, "monday,friday")
        self.assertEqual(updated_second.mission_type, "departure")
        self.assertEqual(updated_second.first_mix_pull_time_local, time(4, 45))

    def test_master_schedule_rejects_unknown_sort_name(self):
        response = self.client.post(
            "/motherbrain/master-schedule/new",
            data=self._master_schedule_form_data(sort_name="midnight"),
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(b"Sort name must be Night, Twilight, Day, or Sunrise.", response.data)
        self.assertIsNone(MasterFlightSchedule.query.filter_by(sort_name="midnight").first())

    def test_master_schedule_rejects_flight_number_over_8_characters(self):
        response = self.client.post(
            "/motherbrain/master-schedule/new",
            data=self._master_schedule_form_data(flight_number="FLIGHT999"),
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(b"Flight number must be 8 characters or fewer.", response.data)
        self.assertIsNone(MasterFlightSchedule.query.filter_by(flight_number="FLIGHT999").first())

    def test_master_schedule_origin_destination_are_three_letters_and_save_uppercase(self):
        invalid = self.client.post(
            "/motherbrain/master-schedule/new",
            data=self._master_schedule_form_data(destination="SD1"),
        )
        valid = self.client.post(
            "/motherbrain/master-schedule/new",
            data=self._master_schedule_form_data(
                flight_number="UP123",
                origin="rfd",
                destination="sdf",
            ),
            follow_redirects=False,
        )

        master = MasterFlightSchedule.query.filter_by(flight_number="UP123").first()
        self.assertEqual(invalid.status_code, 400)
        self.assertIn(b"Destination must be exactly 3 letters.", invalid.data)
        self.assertEqual(valid.status_code, 302)
        self.assertEqual(master.flight_number, "UP123")
        self.assertEqual(master.origin, "RFD")
        self.assertEqual(master.destination, "SDF")

    def test_master_schedule_aircraft_type_saves_and_rejects_unknown(self):
        valid = self.client.post(
            "/motherbrain/master-schedule/new",
            data=self._master_schedule_form_data(
                flight_number="ACTYPE1",
                aircraft_type="Other",
            ),
            follow_redirects=False,
        )
        invalid = self.client.post(
            "/motherbrain/master-schedule/new",
            data=self._master_schedule_form_data(
                flight_number="ACTYPE2",
                aircraft_type="A330",
            ),
        )

        master = MasterFlightSchedule.query.filter_by(flight_number="ACTYPE1").first()
        self.assertEqual(valid.status_code, 302)
        self.assertEqual(master.aircraft_type, "Other")
        self.assertEqual(invalid.status_code, 400)
        self.assertIn(b"AC Type must be A300, 747, 757, 767, or Other.", invalid.data)
        self.assertIsNone(MasterFlightSchedule.query.filter_by(flight_number="ACTYPE2").first())

    def test_master_schedule_flight_number_saves_uppercase(self):
        response = self.client.post(
            "/motherbrain/master-schedule/new",
            data=self._master_schedule_form_data(flight_number="up789"),
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertIsNotNone(MasterFlightSchedule.query.filter_by(flight_number="UP789").first())

    def test_master_schedule_time_fields_use_24_hour_format_and_save(self):
        response = self.client.post(
            "/motherbrain/master-schedule/new",
            data=self._master_schedule_form_data(
                flight_number="TIME01",
                planned_time_local="23:45",
                pure_pull_time_local="20:10",
            ),
            follow_redirects=False,
        )
        form_response = self.client.get("/motherbrain/master-schedule/new")

        master = MasterFlightSchedule.query.filter_by(flight_number="TIME01").first()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(master.planned_time_local, time(23, 45))
        self.assertEqual(master.pure_pull_time_local, time(20, 10))
        self.assertIn(b'class="military-time-select"', form_response.data)
        self.assertIn(b'name="row_0_planned_time_local_hour"', form_response.data)
        self.assertIn(b'name="row_0_planned_time_local_minute"', form_response.data)
        self.assertNotIn(b'type="time"', form_response.data)

    def test_master_schedule_rejects_non_military_time(self):
        response = self.client.post(
            "/motherbrain/master-schedule/new",
            data=self._master_schedule_form_data(
                flight_number="BADTIME",
                planned_time_local="9:30",
            ),
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(b"Planned time must use HH:MM military format.", response.data)
        self.assertIsNone(MasterFlightSchedule.query.filter_by(flight_number="BADTIME").first())

    def test_master_schedule_timezone_is_not_selectable_and_uses_gateway_timezone(self):
        response = self.client.post(
            "/motherbrain/master-schedule/new",
            data=self._master_schedule_form_data(
                flight_number="TZ001",
                timezone="America/New_York",
            ),
            follow_redirects=False,
        )
        form_response = self.client.get("/motherbrain/master-schedule/new")

        master = MasterFlightSchedule.query.filter_by(flight_number="TZ001").first()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(master.timezone, "America/Chicago")
        self.assertNotIn(b'name="timezone"', form_response.data)

    def test_master_schedule_list_does_not_show_parking_when_applicable(self):
        master = self._add_master(flight_number="PARK01", preferred_parking="A1")
        db.session.commit()

        form_response = self.client.get(f"/motherbrain/master-schedule/{master.id}/edit")
        list_response = self.client.get("/motherbrain/master-schedule")
        detail_response = self.client.get(f"/motherbrain/master-schedule/{master.id}")

        self.assertEqual(list_response.status_code, 200)
        self.assertNotIn(b"Parking", list_response.data)
        self.assertNotIn(b"A1", list_response.data)

        for label, response in (("form", form_response), ("detail", detail_response)):
            with self.subTest(page=label):
                self.assertEqual(response.status_code, 200)
                self.assertNotIn(b"Preferred Parking", response.data)
                self.assertNotIn(b"Parking", response.data)
                self.assertNotIn(b"A1", response.data)
        self.assertNotIn(b">Edit</a>", detail_response.data)

    def test_delete_master_schedule_removes_row_and_preserves_generated_mission(self):
        master = self._add_master(flight_number="DELMS")
        operation = self._operation()
        db.session.add(operation)
        db.session.flush()
        mission = self._mission(
            operation=operation,
            mission_type="departure",
            flight_number="DELMS",
            master_flight_schedule_id=master.id,
        )
        db.session.add(mission)
        db.session.commit()

        response = self.client.post(
            f"/motherbrain/master-schedule/{master.id}/delete",
            follow_redirects=False,
        )

        updated_mission = db.session.get(SortDateMission, mission.id)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/motherbrain/master-schedule")
        self.assertIsNone(db.session.get(MasterFlightSchedule, master.id))
        self.assertIsNotNone(updated_mission)
        self.assertIsNone(updated_mission.master_flight_schedule_id)

    def test_create_departure_master_row_with_pull_times(self):
        response = self.client.post(
            "/motherbrain/master-schedule/new",
            data=self._master_schedule_form_data(
                flight_number="DEP100",
                pure_pull_time_local="01:20",
                first_mix_pull_time_local="01:40",
                final_mix_pull_time_local="01:55",
                active_days=["monday", "wednesday"],
            ),
            follow_redirects=False,
        )

        master = MasterFlightSchedule.query.filter_by(flight_number="DEP100").first()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(master.mission_type, "departure")
        self.assertEqual(master.active_days, "monday,wednesday")
        self.assertEqual(master.timezone, "America/Chicago")
        self.assertEqual(master.pure_pull_time_local, time(1, 20))
        self.assertEqual(master.first_mix_pull_time_local, time(1, 40))
        self.assertEqual(master.final_mix_pull_time_local, time(1, 55))

    def test_create_arrival_master_row_clears_pull_times(self):
        response = self.client.post(
            "/motherbrain/master-schedule/new",
            data=self._master_schedule_form_data(
                mission_type="arrival",
                flight_number="ARR100",
                origin="SDF",
                destination="RFD",
                pure_pull_time_local="01:20",
                first_mix_pull_time_local="01:40",
                final_mix_pull_time_local="01:55",
            ),
            follow_redirects=False,
        )

        master = MasterFlightSchedule.query.filter_by(flight_number="ARR100").first()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(master.mission_type, "arrival")
        self.assertIsNone(master.pure_pull_time_local)
        self.assertIsNone(master.first_mix_pull_time_local)
        self.assertIsNone(master.final_mix_pull_time_local)

    def test_edit_arrival_clears_pull_times_after_type_change(self):
        master = self._add_master(
            flight_number="DEP200",
            pure_pull_time_local=time(1, 20),
            first_mix_pull_time_local=time(1, 40),
            final_mix_pull_time_local=time(1, 55),
        )
        db.session.commit()

        response = self.client.post(
            f"/motherbrain/master-schedule/{master.id}/edit",
            data=self._master_schedule_form_data(
                mission_type="arrival",
                flight_number="DEP200",
                origin="SDF",
                destination="RFD",
                pure_pull_time_local="01:20",
                first_mix_pull_time_local="01:40",
                final_mix_pull_time_local="01:55",
            ),
            follow_redirects=False,
        )

        updated = db.session.get(MasterFlightSchedule, master.id)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(updated.mission_type, "arrival")
        self.assertIsNone(updated.pure_pull_time_local)
        self.assertIsNone(updated.first_mix_pull_time_local)
        self.assertIsNone(updated.final_mix_pull_time_local)

    def test_duplicate_active_master_row_is_rejected(self):
        self._add_master(flight_number="DEP300", active=True)
        db.session.commit()

        response = self.client.post(
            "/motherbrain/master-schedule/new",
            data=self._master_schedule_form_data(flight_number="DEP300"),
        )

        active_duplicates = MasterFlightSchedule.query.filter_by(
            gateway_code="RFD",
            sort_name="night",
            mission_type="departure",
            flight_number="DEP300",
            active=True,
        ).count()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(active_duplicates, 1)
        self.assertIn(b"already exists", response.data)

    def test_inactive_master_row_does_not_generate_operation_mission(self):
        self.client.post(
            "/motherbrain/master-schedule/new",
            data=self._master_schedule_form_data(
                flight_number="DEP400",
                active=False,
            ),
        )

        response = self.client.post(
            "/motherbrain/operations/new",
            data={
                "sort_date": "2026-06-01",
                "gateway_code": "RFD",
                "sort_name": "night",
            },
            follow_redirects=False,
        )

        operation = SortDateOperation.query.first()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(operation.missions), 0)

    def test_active_days_saved_from_form_work_with_generation(self):
        self.client.post(
            "/motherbrain/master-schedule/new",
            data=self._master_schedule_form_data(
                flight_number="DEP500",
                active_days=["monday"],
            ),
        )

        self.client.post(
            "/motherbrain/operations/new",
            data={
                "sort_date": "2026-06-01",
                "gateway_code": "RFD",
                "sort_name": "night",
            },
        )

        operation = SortDateOperation.query.first()
        self.assertEqual(operation.missions[0].flight_number, "DEP500")

    def test_operation_generation_uses_only_current_gateway_master_schedules(self):
        dfw_gateway = self._gateway("DFW", "NeoDFW")
        self._add_master(flight_number="RFDGEN", gateway_id=self.rfd_gateway.id)
        self._add_master(
            flight_number="DFWGEN",
            gateway_id=dfw_gateway.id,
            gateway_code="DFW",
            origin="DFW",
            destination="ONT",
        )
        db.session.commit()

        response = self.client.post(
            "/motherbrain/operations/new",
            data={
                "sort_date": "2026-06-01",
                "gateway_code": "DFW",
                "sort_name": "night",
            },
            follow_redirects=False,
        )

        operation = SortDateOperation.query.filter_by(gateway_code="RFD").first()
        self.assertEqual(response.status_code, 302)
        self.assertIsNotNone(operation)
        self.assertEqual(operation.gateway_id, self.rfd_gateway.id)
        self.assertEqual([mission.flight_number for mission in operation.missions], ["RFDGEN"])
        self.assertEqual(SortDateOperation.query.filter_by(gateway_code="DFW").count(), 0)

    def test_toggle_active_changes_active_state(self):
        master = self._add_master(flight_number="DEP600", active=True)
        db.session.commit()

        response = self.client.post(
            f"/motherbrain/master-schedule/{master.id}/toggle-active",
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(db.session.get(MasterFlightSchedule, master.id).active)

        self.client.post(
            f"/motherbrain/master-schedule/{master.id}/toggle-active",
            follow_redirects=False,
        )
        self.assertTrue(db.session.get(MasterFlightSchedule, master.id).active)

    def test_create_manual_arrival_mission_clears_pull_times(self):
        operation = self._operation()
        db.session.add(operation)
        db.session.commit()

        response = self.client.post(
            f"/motherbrain/operations/{operation.id}/missions/new",
            data=self._mission_form_data(
                mission_type="arrival",
                flight_number="arrman",
                origin="sdf",
                destination="rfd",
                assigned_tail_number="n123up",
                arrival_status="en_route",
                pure_pull_time_local="01:20",
                first_mix_pull_time_local="01:40",
                final_mix_pull_time_local="01:55",
            ),
            follow_redirects=False,
        )

        mission = SortDateMission.query.filter_by(flight_number="ARRMAN").first()
        tail_state = SortDateTailState.query.filter_by(tail_number="N123UP").first()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(mission.mission_source, "manual")
        self.assertEqual(mission.mission_type, "arrival")
        self.assertEqual(mission.flight_number, "ARRMAN")
        self.assertEqual(mission.origin, "SDF")
        self.assertEqual(mission.destination, "RFD")
        self.assertEqual(mission.assigned_tail_number, "N123UP")
        self.assertEqual(mission.arrival_status, "en_route")
        self.assertIsNone(mission.pure_pull_time_local)
        self.assertIsNone(mission.first_mix_pull_time_local)
        self.assertIsNone(mission.final_mix_pull_time_local)
        self.assertIsNone(mission.pull_time_source)
        self.assertEqual(tail_state.aircraft_type, "A300")
        self.assertEqual(tail_state.aircraft_type_source, "derived")

    def test_create_manual_departure_mission_with_pull_times(self):
        operation = self._operation()
        db.session.add(operation)
        db.session.commit()

        response = self.client.post(
            f"/motherbrain/operations/{operation.id}/missions/new",
            data=self._mission_form_data(
                flight_number="depman",
                origin="rfd",
                destination="sdf",
                pure_pull_time_local="01:20",
                first_mix_pull_time_local="01:40",
                final_mix_pull_time_local="01:55",
                departure_status="loading",
            ),
            follow_redirects=False,
        )

        mission = SortDateMission.query.filter_by(flight_number="DEPMAN").first()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(mission.mission_source, "manual")
        self.assertEqual(mission.mission_type, "departure")
        self.assertEqual(mission.flight_number, "DEPMAN")
        self.assertEqual(mission.origin, "RFD")
        self.assertEqual(mission.destination, "SDF")
        self.assertEqual(mission.pure_pull_time_local, time(1, 20))
        self.assertEqual(mission.first_mix_pull_time_local, time(1, 40))
        self.assertEqual(mission.final_mix_pull_time_local, time(1, 55))
        self.assertEqual(mission.pull_time_source, "manual")
        self.assertEqual(mission.departure_status, "loading")

    def test_arrival_mission_form_does_not_render_pull_time_fields(self):
        operation = self._operation()
        db.session.add(operation)
        mission = self._mission(
            operation=operation,
            mission_type="arrival",
            flight_number="ARRFORM",
            origin="SDF",
            destination="RFD",
        )
        db.session.add(mission)
        db.session.commit()

        response = self.client.get(
            f"/motherbrain/operations/{operation.id}/missions/{mission.id}/edit"
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"Pure Pull", response.data)
        self.assertNotIn(b"First Mix Pull", response.data)
        self.assertNotIn(b"Final Mix Pull", response.data)
        self.assertNotIn(b"pure_pull_time_local_hour", response.data)

    def test_departure_mission_form_renders_pull_time_fields(self):
        operation = self._operation()
        db.session.add(operation)
        db.session.commit()

        response = self.client.get(f"/motherbrain/operations/{operation.id}/missions/new")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Pure Pull", response.data)
        self.assertIn(b"First Mix Pull", response.data)
        self.assertIn(b"Final Mix Pull", response.data)
        self.assertIn(b"pure_pull_time_local_hour", response.data)

    def test_duplicate_mission_flight_number_is_rejected_inside_operation(self):
        operation = self._operation()
        db.session.add(operation)
        db.session.add(self._mission(operation, "departure", "DUP001"))
        db.session.commit()

        response = self.client.post(
            f"/motherbrain/operations/{operation.id}/missions/new",
            data=self._mission_form_data(flight_number="DUP001"),
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(b"already exists", response.data)
        self.assertEqual(
            SortDateMission.query.filter_by(
                sort_date_operation_id=operation.id,
                flight_number="DUP001",
            ).count(),
            1,
        )

    def test_edit_departure_mission_into_arrival_clears_pull_times(self):
        operation = self._operation()
        db.session.add(operation)
        mission = self._mission(
            operation=operation,
            mission_type="departure",
            flight_number="EDIT001",
            pure_pull_time_local=time(1, 20),
            first_mix_pull_time_local=time(1, 40),
            final_mix_pull_time_local=time(1, 55),
            pull_time_source="manual",
        )
        db.session.add(mission)
        db.session.commit()

        response = self.client.post(
            f"/motherbrain/operations/{operation.id}/missions/{mission.id}/edit",
            data=self._mission_form_data(
                mission_type="arrival",
                flight_number="EDIT001",
                origin="SDF",
                destination="RFD",
                pure_pull_time_local="01:20",
                first_mix_pull_time_local="01:40",
                final_mix_pull_time_local="01:55",
            ),
            follow_redirects=False,
        )

        updated = db.session.get(SortDateMission, mission.id)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(updated.mission_type, "arrival")
        self.assertIsNone(updated.pure_pull_time_local)
        self.assertIsNone(updated.first_mix_pull_time_local)
        self.assertIsNone(updated.final_mix_pull_time_local)
        self.assertIsNone(updated.pull_time_source)

    def test_manual_arrival_appears_on_arrival_board(self):
        operation = self._operation()
        db.session.add(operation)
        db.session.add(self._mission(operation, "arrival", "arrboard", arrival_status="unloaded"))
        db.session.add(self._mission(operation, "departure", "DEPBOARD"))
        db.session.commit()

        response = self.client.get(f"/motherbrain/operations/{operation.id}/arrivals")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"ARRBOARD", response.data)
        self.assertIn(b"Unloaded", response.data)
        self.assertIn(b">Status<", response.data)
        self.assertNotIn(b"DEPBOARD", response.data)

    def test_manual_departure_appears_on_departure_board(self):
        operation = self._operation()
        db.session.add(operation)
        db.session.add(self._mission(operation, "arrival", "ARRBOARD"))
        db.session.add(self._mission(operation, "departure", "depboard", departure_status="crew_load_complete"))
        db.session.commit()

        response = self.client.get(f"/motherbrain/operations/{operation.id}/departures")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"DEPBOARD", response.data)
        self.assertIn(b"Crew Load Complete", response.data)
        self.assertIn(b">Status<", response.data)
        self.assertNotIn(b"ARRBOARD", response.data)

    def test_manual_departure_window_adjusted_times_still_display(self):
        operation = self._operation(window_minutes=20)
        db.session.add(operation)
        db.session.add(
            self._mission(
                operation=operation,
                mission_type="departure",
                flight_number="WINMAN",
                planned_datetime_local=datetime(2026, 6, 1, 2, 10),
                pure_pull_time_local=time(1, 20),
                first_mix_pull_time_local=time(1, 40),
                final_mix_pull_time_local=time(1, 55),
            )
        )
        db.session.commit()

        response = self.client.get(f"/motherbrain/operations/{operation.id}/departures")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"02:30", response.data)
        self.assertNotIn(b"02:10", response.data)
        self.assertNotIn(b"02:15", response.data)

    def test_delete_mission_removes_mission_and_crew_assignments(self):
        operation = self._operation()
        db.session.add(operation)
        mission = self._mission(operation, "departure", "DEL001")
        db.session.add(mission)
        db.session.flush()
        db.session.add(
            SortDateCrewAssignment(
                sort_date_mission_id=mission.id,
                aircraft_section="topside",
                required=True,
            )
        )
        db.session.commit()

        response = self.client.post(
            f"/motherbrain/operations/{operation.id}/missions/{mission.id}/delete",
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertIsNone(db.session.get(SortDateMission, mission.id))
        self.assertEqual(
            SortDateCrewAssignment.query.filter_by(
                sort_date_mission_id=mission.id,
            ).count(),
            0,
        )

    def test_tail_state_manual_aircraft_type_is_preserved_on_mission_save(self):
        operation = self._operation()
        db.session.add(operation)
        db.session.add(
            SortDateTailState(
                sort_date=operation.sort_date,
                gateway_code=operation.gateway_code,
                sort_name=operation.sort_name,
                tail_number="N123UP",
                aircraft_type="A330",
                aircraft_type_source="manual",
            )
        )
        db.session.commit()

        response = self.client.post(
            f"/motherbrain/operations/{operation.id}/missions/new",
            data=self._mission_form_data(
                flight_number="TAILMAN",
                assigned_tail_number="N123UP",
            ),
            follow_redirects=False,
        )

        tail_state = SortDateTailState.query.filter_by(tail_number="N123UP").first()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(tail_state.aircraft_type, "A330")
        self.assertEqual(tail_state.aircraft_type_source, "manual")

    def test_tail_state_lookup_is_scoped_by_gateway(self):
        dfw_gateway = self._gateway("DFW", "NeoDFW")
        rfd_operation = self._operation(gateway_id=self.rfd_gateway.id, gateway_code="RFD")
        dfw_operation = self._operation(gateway_id=dfw_gateway.id, gateway_code="DFW")
        db.session.add_all((rfd_operation, dfw_operation))
        db.session.flush()
        db.session.add(
            SortDateTailState(
                sort_date=dfw_operation.sort_date,
                gateway_code="DFW",
                sort_name=dfw_operation.sort_name,
                tail_number="N123UP",
                aircraft_type="A330",
                aircraft_type_source="manual",
            )
        )
        rfd_mission = self._mission(
            rfd_operation,
            "departure",
            "RFDTAL",
            assigned_tail_number="N123UP",
        )
        db.session.add(rfd_mission)
        db.session.flush()

        response = self.client.post(
            f"/motherbrain/operations/{rfd_operation.id}/missions/{rfd_mission.id}/edit",
            data=self._mission_form_data(
                flight_number="RFDTAL",
                assigned_tail_number="N123UP",
            ),
            follow_redirects=False,
        )

        rfd_tail = SortDateTailState.query.filter_by(
            gateway_code="RFD",
            tail_number="N123UP",
        ).first()
        dfw_tail = SortDateTailState.query.filter_by(
            gateway_code="DFW",
            tail_number="N123UP",
        ).first()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(rfd_tail.aircraft_type, "A300")
        self.assertEqual(rfd_tail.aircraft_type_source, "derived")
        self.assertEqual(dfw_tail.aircraft_type, "A330")
        self.assertEqual(dfw_tail.aircraft_type_source, "manual")

    def test_tail_swap_rebuilds_crew_slots_using_existing_rules(self):
        operation = self._operation()
        db.session.add(operation)
        mission = self._mission(
            operation=operation,
            mission_type="departure",
            flight_number="SWAP001",
            assigned_tail_number="N123UP",
        )
        db.session.add(mission)
        db.session.flush()
        for section in ("topside", "front_p", "rear_p", "ab"):
            db.session.add(
                SortDateCrewAssignment(
                    sort_date_mission_id=mission.id,
                    aircraft_section=section,
                    required=True,
                )
            )
        db.session.commit()

        response = self.client.post(
            f"/motherbrain/operations/{operation.id}/missions/{mission.id}/edit",
            data=self._mission_form_data(
                flight_number="SWAP001",
                assigned_tail_number="N456UP",
            ),
            follow_redirects=False,
        )

        sections = sorted(
            assignment.aircraft_section
            for assignment in SortDateCrewAssignment.query.filter_by(
                sort_date_mission_id=mission.id,
            ).all()
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(sections, ["belly_31", "belly_34", "topside"])

    def test_operation_generation_route_creates_operation(self):
        self._add_master(flight_number="ARR001", mission_type="arrival")
        self._add_master(flight_number="DEP001", mission_type="departure")
        db.session.commit()

        response = self.client.post(
            "/motherbrain/operations/new",
            data={
                "sort_date": "2026-06-01",
                "gateway_code": "rfd",
                "sort_name": "night",
            },
            follow_redirects=False,
        )

        operation = SortDateOperation.query.first()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(operation.gateway_code, "RFD")
        self.assertEqual(operation.sort_name, "night")
        self.assertEqual(len(operation.missions), 2)

    def test_duplicate_generation_redirects_to_existing_operation(self):
        self._add_master(flight_number="DEP001")
        db.session.commit()
        self.client.post(
            "/motherbrain/operations/new",
            data={
                "sort_date": "2026-06-01",
                "gateway_code": "RFD",
                "sort_name": "night",
            },
        )

        response = self.client.post(
            "/motherbrain/operations/new",
            data={
                "sort_date": "2026-06-01",
                "gateway_code": "RFD",
                "sort_name": "night",
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn(
            f"/motherbrain/operations/{SortDateOperation.query.first().id}".encode(),
            response.location.encode(),
        )
        self.assertEqual(SortDateOperation.query.count(), 1)

    def test_arrival_board_shows_only_arrival_missions(self):
        operation = self._operation_with_missions()
        db.session.commit()

        response = self.client.get(f"/motherbrain/operations/{operation.id}/arrivals")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"ARR001", response.data)
        self.assertNotIn(b"DEP999", response.data)

    def test_departure_board_shows_only_departure_missions(self):
        operation = self._operation_with_missions()
        db.session.commit()

        response = self.client.get(f"/motherbrain/operations/{operation.id}/departures")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"DEP999", response.data)
        self.assertNotIn(b"ARR001", response.data)

    def test_departure_board_uses_adjusted_window_display_fields(self):
        operation = SortDateOperation(
            sort_date=date(2026, 6, 1),
            gateway_code="RFD",
            sort_name="night",
            window_minutes=20,
        )
        db.session.add(operation)
        db.session.add(
            self._mission(
                operation=operation,
                mission_type="departure",
                flight_number="DEP999",
                planned_datetime_local=datetime(2026, 6, 1, 2, 10),
                pure_pull_time_local=time(1, 20),
                first_mix_pull_time_local=time(1, 40),
                final_mix_pull_time_local=time(1, 55),
            )
        )
        db.session.commit()

        response = self.client.get(f"/motherbrain/operations/{operation.id}/departures")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"02:30", response.data)
        self.assertNotIn(b"02:10", response.data)
        self.assertNotIn(b"01:20", response.data)
        self.assertNotIn(b"01:40", response.data)
        self.assertNotIn(b"02:15", response.data)

    def test_window_update_rejects_negative_values(self):
        operation = self._operation()
        db.session.add(operation)
        db.session.commit()

        response = self.client.post(
            f"/motherbrain/operations/{operation.id}/window",
            data={"window_minutes": "-1"},
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Window minutes must be 0 or higher.", response.data)
        self.assertEqual(db.session.get(SortDateOperation, operation.id).window_minutes, 0)

    def test_window_update_accepts_zero_or_positive_values(self):
        operation = self._operation()
        db.session.add(operation)
        db.session.commit()

        response = self.client.post(
            f"/motherbrain/operations/{operation.id}/window",
            data={"window_minutes": "25"},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(db.session.get(SortDateOperation, operation.id).window_minutes, 25)

        self.client.post(
            f"/motherbrain/operations/{operation.id}/window",
            data={"window_minutes": "0"},
        )
        self.assertEqual(db.session.get(SortDateOperation, operation.id).window_minutes, 0)

    def _add_master(self, **overrides):
        values = {
            "gateway_code": "RFD",
            "sort_name": "night",
            "mission_type": "departure",
            "flight_number": "DEP001",
            "aircraft_type": "",
            "origin": "RFD",
            "destination": "SDF",
            "active": True,
            "active_days": "monday,tuesday",
            "planned_time_local": time(2, 10),
        }
        if overrides.get("mission_type") == "arrival":
            values["origin"] = "SDF"
            values["destination"] = "RFD"
        values.update(overrides)
        master = MasterFlightSchedule(**values)
        db.session.add(master)
        return master

    def _gateway(self, code, name):
        gateway = Gateway.query.filter_by(code=code).first()
        if gateway:
            return gateway

        gateway = Gateway(code=code, name=name, is_active=True)
        db.session.add(gateway)
        db.session.flush()
        return gateway

    def _add_matrix_cell(self, day_of_week, sort_name, gateway=None, is_active=True):
        gateway = gateway or self.rfd_gateway
        matrix_cell = GatewaySortMatrix(
            gateway_id=gateway.id,
            gateway_code=gateway.code,
            day_of_week=day_of_week,
            sort_name=sort_name,
            is_active=is_active,
        )
        db.session.add(matrix_cell)
        return matrix_cell

    def _master_schedule_form_data(self, **overrides):
        values = {
            "gateway_code": "RFD",
            "sort_name": "night",
            "mission_type": "departure",
            "flight_number": "DEP001",
            "origin": "RFD",
            "destination": "SDF",
            "active_days": ["monday", "tuesday"],
            "planned_time_local": "02:10",
            "timezone": "America/Chicago",
            "preferred_parking": "",
            "pure_pull_time_local": "",
            "first_mix_pull_time_local": "",
            "final_mix_pull_time_local": "",
            "active": True,
        }
        values.update(overrides)
        active = values.pop("active")
        if active:
            values["active"] = "1"
        return values

    def _bulk_master_schedule_form_data(self, *rows):
        data = {"row_indexes": [str(index) for index in range(len(rows))]}
        for index, overrides in enumerate(rows):
            values = self._master_schedule_form_data(**overrides)
            row_id = overrides.get("id", "")
            active_days = values.pop("active_days", [])
            active = values.pop("active", None)
            values.pop("gateway_code", None)
            values.pop("timezone", None)
            values.pop("preferred_parking", None)

            prefix = f"row_{index}_"
            data[f"{prefix}id"] = row_id
            for key, value in values.items():
                data[f"{prefix}{key}"] = value
            data[f"{prefix}active_days"] = active_days
            if active:
                data[f"{prefix}active"] = "1"
        return data

    def _mission_form_data(self, **overrides):
        values = {
            "mission_type": "departure",
            "flight_number": "DEP001",
            "origin": "RFD",
            "destination": "SDF",
            "assigned_tail_number": "",
            "planned_time_local": "02:10",
            "timezone": "America/Chicago",
            "eta_datetime_utc": "",
            "actual_block_in_datetime_utc": "",
            "actual_block_out_datetime_utc": "",
            "planned_fuel_load": "",
            "fuel_status": "",
            "departure_status": "",
            "pure_pull_time_local": "",
            "first_mix_pull_time_local": "",
            "final_mix_pull_time_local": "",
        }
        values.update(overrides)
        return values

    def _operation(self, **overrides):
        values = {
            "sort_date": date(2026, 6, 1),
            "gateway_code": "RFD",
            "sort_name": "night",
        }
        values.update(overrides)
        return SortDateOperation(**values)

    def _operation_with_missions(self):
        operation = self._operation()
        db.session.add(operation)
        db.session.add(
            self._mission(
                operation=operation,
                mission_type="arrival",
                flight_number="ARR001",
                origin="SDF",
                destination="RFD",
            )
        )
        db.session.add(
            self._mission(
                operation=operation,
                mission_type="departure",
                flight_number="DEP999",
                origin="RFD",
                destination="SDF",
            )
        )
        return operation

    def _mission(self, operation, mission_type, flight_number, **overrides):
        values = {
            "sort_date_operation": operation,
            "sort_date": operation.sort_date,
            "gateway_code": operation.gateway_code,
            "sort_name": operation.sort_name,
            "mission_type": mission_type,
            "mission_source": "manual",
            "flight_number": flight_number.upper(),
            "origin": "SDF" if mission_type == "arrival" else "RFD",
            "destination": "RFD" if mission_type == "arrival" else "SDF",
            "planned_datetime_local": datetime(2026, 6, 1, 2, 10),
            "planned_datetime_utc": datetime(2026, 6, 1, 7, 10),
        }
        values.update(overrides)
        return SortDateMission(**values)


if __name__ == "__main__":
    unittest.main()
