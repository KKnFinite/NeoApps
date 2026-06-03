from datetime import date, datetime, time
import unittest

from app import create_app
from app.extensions import db
from app.models import (
    MasterFlightSchedule,
    SortDateCrewAssignment,
    SortDateMission,
    SortDateOperation,
    SortDateTailState,
    User,
)
from app.services.access_control import backfill_default_gateway_node_roles


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

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"NeoMotherBrain", response.data)

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
            "/motherbrain/operations",
            "/motherbrain/operations/new",
            "/motherbrain/master-schedule",
            "/motherbrain/master-schedule/new",
            f"/motherbrain/operations/{operation.id}",
            f"/motherbrain/operations/{operation.id}/arrivals",
            f"/motherbrain/operations/{operation.id}/departures",
            f"/motherbrain/operations/{operation.id}/missions/new",
            f"/motherbrain/operations/{operation.id}/missions/{mission.id}",
            f"/motherbrain/operations/{operation.id}/missions/{mission.id}/edit",
        )

        for path in get_paths:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

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

    def test_logged_in_user_can_view_master_schedule_list(self):
        self._add_master(flight_number="DEP001")
        db.session.commit()

        response = self.client.get("/motherbrain/master-schedule")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Master Flight Schedule", response.data)
        self.assertIn(b"DEP001", response.data)

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
                flight_number="ARRMAN",
                origin="SDF",
                destination="RFD",
                assigned_tail_number="N123UP",
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
                flight_number="DEPMAN",
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
        self.assertEqual(mission.pure_pull_time_local, time(1, 20))
        self.assertEqual(mission.first_mix_pull_time_local, time(1, 40))
        self.assertEqual(mission.final_mix_pull_time_local, time(1, 55))
        self.assertEqual(mission.pull_time_source, "manual")
        self.assertEqual(mission.departure_status, "loading")

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
        db.session.add(self._mission(operation, "arrival", "ARRBOARD"))
        db.session.add(self._mission(operation, "departure", "DEPBOARD"))
        db.session.commit()

        response = self.client.get(f"/motherbrain/operations/{operation.id}/arrivals")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"ARRBOARD", response.data)
        self.assertNotIn(b"DEPBOARD", response.data)

    def test_manual_departure_appears_on_departure_board(self):
        operation = self._operation()
        db.session.add(operation)
        db.session.add(self._mission(operation, "arrival", "ARRBOARD"))
        db.session.add(self._mission(operation, "departure", "DEPBOARD"))
        db.session.commit()

        response = self.client.get(f"/motherbrain/operations/{operation.id}/departures")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"DEPBOARD", response.data)
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
        self.assertIn(b"02:10", response.data)
        self.assertIn(b"02:30", response.data)
        self.assertIn(b"02:15", response.data)

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
        self.assertIn(b"02:10", response.data)
        self.assertIn(b"02:30", response.data)
        self.assertIn(b"01:20", response.data)
        self.assertIn(b"01:40", response.data)
        self.assertIn(b"02:15", response.data)

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
            "flight_number": flight_number,
            "origin": "SDF" if mission_type == "arrival" else "RFD",
            "destination": "RFD" if mission_type == "arrival" else "SDF",
            "planned_datetime_local": datetime(2026, 6, 1, 2, 10),
            "planned_datetime_utc": datetime(2026, 6, 1, 7, 10),
        }
        values.update(overrides)
        return SortDateMission(**values)


if __name__ == "__main__":
    unittest.main()
