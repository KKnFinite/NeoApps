from datetime import date, datetime, time
import unittest

from app import create_app
from app.extensions import db
from app.models import MasterFlightSchedule, SortDateMission, SortDateOperation, User


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

        user = User(username="route_test", role="grandmaster")
        user.set_password("TestPassword123!")
        db.session.add(user)
        db.session.commit()

        self.client = self.app.test_client()
        self.client.post(
            "/login",
            data={"username": "route_test", "password": "TestPassword123!"},
        )

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_logged_in_user_can_access_motherbrain_home(self):
        response = self.client.get("/motherbrain")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Operations Core", response.data)

    def test_master_schedule_requires_login(self):
        self.client.get("/logout")

        response = self.client.get("/motherbrain/master-schedule")

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
