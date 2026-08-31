import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from sqlalchemy import inspect

from app import create_app
from app.extensions import db
from app.models import (
    Gateway,
    NeoSubZeroDepartureDeiceEvent,
    NeoSubZeroSprayRecord,
    NeoSubZeroUccAssignment,
    NeoSubZeroUccTruckAssignment,
    SortDateMission,
    SortDateOperation,
    SortDateParkingAssignment,
    StaffingPerson,
    StaffingUnit,
    StaffingWorkAssignment,
    User,
)
from app.neonodes.neosubzero import routes as _neosubzero_routes  # noqa: F401
from app.services.access_control import backfill_default_gateway_node_roles
from app.services.live_collaboration import entity_version
from app.services.neosubzero_departure_deice import neosubzero_fluid_settings
from app.services.neosubzero_spray import (
    NeoSubZeroSprayError,
    neosubzero_deice_log,
    set_neosubzero_spray_gallons,
    set_neosubzero_ucc_truck,
)
from app.services.neosubzero_staffing import (
    DEICE_QUALIFICATION_KEY,
    set_staffing_person_qualification,
)
from app.services.password_policy import set_user_password
from app.services.neosubzero_schema import NEOSUBZERO_TABLES
from app.services.permission_rules import DEFAULT_PERMISSION_RULES, ensure_default_permission_rules


class NeoSubZeroSprayOperationsTest(unittest.TestCase):
    def setUp(self):
        config = type(
            "TestConfig",
            (),
            {
                "SECRET_KEY": "test",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
                "DEFAULT_GATEWAY_CODE": "RFD",
            },
        )
        self.app = create_app(config)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        ensure_default_permission_rules()
        self.gateway = Gateway(code="RFD", name="RFD", is_active=True)
        db.session.add(self.gateway)
        db.session.flush()
        self.operation = SortDateOperation(
            sort_date=date(2026, 8, 31),
            gateway_id=self.gateway.id,
            gateway_code="RFD",
            sort_name="night",
        )
        db.session.add(self.operation)
        db.session.flush()
        self.mission = SortDateMission(
            sort_date=self.operation.sort_date,
            gateway_code="RFD",
            sort_name="night",
            sort_date_operation_id=self.operation.id,
            mission_type="departure",
            mission_source="master",
            flight_number="5X101",
            origin="RFD",
            destination="SDF",
            timezone="America/Chicago",
            planned_datetime_local=datetime(2026, 8, 31, 21, 0),
            planned_datetime_utc=datetime(2026, 9, 1, 2, 0),
            assigned_tail_number="N101",
            departure_status="scheduled",
        )
        db.session.add(self.mission)
        db.session.flush()
        db.session.add(
            SortDateParkingAssignment(
                sort_date_operation_id=self.operation.id,
                tail_number=" n101 ",
                ramp_code="A",
                position_code="A03",
            )
        )
        self.event = NeoSubZeroDepartureDeiceEvent(
            sort_date_operation_id=self.operation.id,
            sort_date_mission_id=self.mission.id,
            tail_number="N101",
            status="finished",
            treatment_plan="type_i_type_iv",
            pass1_surface_area="wings_only",
            pass1_started_at_utc=datetime(2026, 9, 1, 1, 10),
            pass1_ended_at_utc=datetime(2026, 9, 1, 1, 20),
            pass2_surface_area="entire_aircraft",
            pass2_started_at_utc=datetime(2026, 9, 1, 1, 21),
            pass2_ended_at_utc=datetime(2026, 9, 1, 1, 31),
        )
        db.session.add(self.event)
        night = self._unit("sort", "Night")
        aviation = self._unit("operation", "Aviation Services", night)
        self.deice = self._unit("work_area", "Deice", aviation)
        self.driver = self._person("D100", "Drive", "One", self.deice)
        self.flyer = self._person("D200", "Fly", "Two", self.deice)
        for person in (self.driver, self.flyer):
            set_staffing_person_qualification(person, DEICE_QUALIFICATION_KEY, True)
        db.session.add_all(
            [
                NeoSubZeroUccAssignment(
                    sort_date_operation_id=self.operation.id,
                    ramp="Alpha",
                    position_number=1,
                    team_role="driver",
                    person_id=self.driver.id,
                ),
                NeoSubZeroUccAssignment(
                    sort_date_operation_id=self.operation.id,
                    ramp="Alpha",
                    position_number=1,
                    team_role="flyer",
                    person_id=self.flyer.id,
                ),
            ]
        )
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_truck_persists_for_sort_and_spray_snapshots_do_not_follow_changes(self):
        truck = set_neosubzero_ucc_truck(self.operation, "Alpha", 1, "T-17")
        db.session.flush()
        record = self._set_gallons(1, 1, "42.5")
        db.session.flush()
        self.assertEqual(record.truck_number_snapshot, "T-17")
        self.assertEqual(record.driver_name_snapshot, "Drive One")
        self.assertEqual(record.flyer_name_snapshot, "Fly Two")

        set_neosubzero_ucc_truck(
            self.operation, "Alpha", 1, "T-99", assignment=truck
        )
        replacement = self._person("D300", "New", "Driver", self.deice)
        set_staffing_person_qualification(replacement, DEICE_QUALIFICATION_KEY, True)
        driver_slot = NeoSubZeroUccAssignment.query.filter_by(
            sort_date_operation_id=self.operation.id,
            ramp="Alpha",
            position_number=1,
            team_role="driver",
        ).one()
        driver_slot.person_id = replacement.id
        db.session.flush()

        self.assertEqual(record.truck_number_snapshot, "T-17")
        self.assertEqual(record.driver_name_snapshot, "Drive One")
        self.assertEqual(truck.truck_number, "T-99")

    def test_gallons_require_positive_nonzero_and_only_create_for_spraying_position(self):
        set_neosubzero_ucc_truck(self.operation, "Alpha", 1, "12")
        db.session.flush()
        with self.assertRaises(NeoSubZeroSprayError):
            self._set_gallons(1, 1, "0")
        with self.assertRaises(NeoSubZeroSprayError):
            self._set_gallons(1, 1, "-2")
        self.assertEqual(NeoSubZeroSprayRecord.query.count(), 0)

        record = self._set_gallons(1, 1, "8.25")
        db.session.flush()
        self.assertEqual(record.gallons, Decimal("8.25"))
        self.assertEqual(record.pass_type, "type_i")
        self.assertEqual(record.surface_area, "wings_only")
        self.assertEqual(NeoSubZeroSprayRecord.query.count(), 1)
        self.assertIsNone(
            NeoSubZeroSprayRecord.query.filter_by(position_number=2).one_or_none()
        )

    def test_context_and_fluid_values_are_snapshotted_once(self):
        set_neosubzero_ucc_truck(self.operation, "Alpha", 1, "44")
        db.session.flush()
        record = self._set_gallons(
            2,
            1,
            "19",
            context={
                "reason_for_application": "Frost",
                "active_precipitation": "Snow",
                "ambient_temperature": "21 F",
                "dew_point": "18 F",
                "notes": "First application\nverified",
            },
        )
        db.session.flush()
        record = self._set_gallons(
            2,
            1,
            "20",
            record=record,
            context={"reason_for_application": "Changed later"},
        )
        self.assertEqual(record.reason_for_application, "Frost")
        self.assertEqual(record.active_precipitation, "Snow")
        self.assertEqual(record.notes, "First application\nverified")
        self.assertEqual(record.pass_type, "type_iv")
        self.assertEqual(record.concentration_percent_snapshot, 100)
        self.assertEqual(record.gallons, Decimal("20"))

    def test_deice_log_groups_type_i_and_iv_by_truck_and_event(self):
        set_neosubzero_ucc_truck(self.operation, "Alpha", 1, "7")
        db.session.flush()
        self._set_gallons(1, 1, "11")
        self._set_gallons(2, 1, "13")
        db.session.flush()
        groups = neosubzero_deice_log(self.operation)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["truck"], "7")
        self.assertEqual(groups[0]["tail"], "N101")
        self.assertEqual(
            [row["type"] for row in groups[0]["applications"]],
            ["Type I", "Type IV"],
        )
        self.assertEqual(groups[0]["applications"][0]["duration"], 10)

    def test_deicer_mobile_requires_current_assignment_and_limits_slot_mutation(self):
        assigned = self._user("assigned_deicer", "operator", self.driver.employee_id)
        client = self.app.test_client()
        self._login(client, assigned)
        with patch(
            "app.neonodes.neosubzero.routes.current_neosubzero_operation",
            return_value=self.operation,
        ):
            response = client.get("/neosubzero/deicer-mobile")
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"POSITION 1", response.data)
            allowed = client.post(
                "/neosubzero/truck",
                data={
                    "board": "deicer",
                    "ramp": "Alpha",
                    "position_number": 1,
                    "truck_number": "55",
                    "expected_version": "",
                    "mission_id": self.mission.id,
                },
            )
            denied = client.post(
                "/neosubzero/truck",
                data={
                    "board": "deicer",
                    "ramp": "Alpha",
                    "position_number": 2,
                    "truck_number": "66",
                    "expected_version": "",
                    "mission_id": self.mission.id,
                },
            )
            own_gallons = client.post(
                "/neosubzero/spray-gallons",
                data={
                    "board": "deicer",
                    "mission_id": self.mission.id,
                    "ramp": "Alpha",
                    "pass_number": 1,
                    "position_number": 1,
                    "gallons": "12",
                    "expected_version": "",
                },
            )
            other_gallons = client.post(
                "/neosubzero/spray-gallons",
                data={
                    "board": "deicer",
                    "mission_id": self.mission.id,
                    "ramp": "Alpha",
                    "pass_number": 1,
                    "position_number": 2,
                    "gallons": "12",
                    "expected_version": "",
                },
            )
        self.assertEqual(allowed.status_code, 302)
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(own_gallons.status_code, 302)
        self.assertEqual(other_gallons.status_code, 403)
        self.assertEqual(
            NeoSubZeroUccTruckAssignment.query.filter_by(position_number=1).one().truck_number,
            "55",
        )
        self.assertEqual(
            NeoSubZeroSprayRecord.query.filter_by(position_number=1).one().gallons,
            Decimal("12"),
        )

        driver_assignment = NeoSubZeroUccAssignment.query.filter_by(
            sort_date_operation_id=self.operation.id,
            person_id=self.driver.id,
        ).one()
        db.session.delete(driver_assignment)
        db.session.commit()
        with patch(
            "app.neonodes.neosubzero.routes.current_neosubzero_operation",
            return_value=self.operation,
        ):
            response = client.get("/neosubzero/deicer-mobile")
            revision = client.get("/neosubzero/deicer-mobile/revision")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(revision.status_code, 403)

    def test_deice_log_page_is_read_only_and_uses_snapshot_grouping(self):
        viewer = self._user("log_viewer", "watcher", "LOG-1")
        set_neosubzero_ucc_truck(self.operation, "Alpha", 1, "31")
        db.session.flush()
        self._set_gallons(1, 1, "9")
        db.session.commit()
        client = self.app.test_client()
        self._login(client, viewer)
        with patch(
            "app.neonodes.neosubzero.routes.current_neosubzero_operation",
            return_value=self.operation,
        ):
            response = client.get("/neosubzero/deice-log")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"DEICE LOG", response.data)
        self.assertIn(b">TRUCK<", response.data)
        self.assertIn(b">31<", response.data)
        self.assertNotIn(b"SAVE", response.data)

    def test_schema_and_permission_foundation_is_registered(self):
        tables = set(inspect(db.engine).get_table_names())
        self.assertIn("neosubzero_ucc_truck_assignments", tables)
        self.assertIn("neosubzero_spray_records", tables)
        self.assertIn(NeoSubZeroUccTruckAssignment.__table__, NEOSUBZERO_TABLES)
        self.assertIn(NeoSubZeroSprayRecord.__table__, NEOSUBZERO_TABLES)
        defaults = {key: role for key, role, _description in DEFAULT_PERMISSION_RULES}
        self.assertEqual(defaults["neosubzero.deicer_mobile.view"], "operator")
        self.assertEqual(defaults["neosubzero.deice_log.view"], "watcher")

    def test_truck_and_gallon_routes_reject_stale_versions_without_overwrite(self):
        editor = self._user("spray_editor", "simulator", "SPRAY-EDITOR")
        truck = set_neosubzero_ucc_truck(self.operation, "Alpha", 1, "1")
        db.session.commit()
        stale_truck_version = entity_version(truck)
        truck.truck_number = "2"
        db.session.commit()
        record = self._set_gallons(1, 1, "10")
        db.session.commit()
        stale_record_version = entity_version(record)
        record.gallons = Decimal("11")
        db.session.commit()

        client = self.app.test_client()
        self._login(client, editor)
        with patch(
            "app.neonodes.neosubzero.routes.current_neosubzero_operation",
            return_value=self.operation,
        ):
            truck_response = client.post(
                "/neosubzero/truck",
                data={
                    "board": "outbound",
                    "ramp": "Alpha",
                    "position_number": 1,
                    "truck_number": "3",
                    "expected_version": stale_truck_version,
                },
                follow_redirects=True,
            )
            gallons_response = client.post(
                "/neosubzero/spray-gallons",
                data={
                    "board": "outbound",
                    "mission_id": self.mission.id,
                    "ramp": "Alpha",
                    "pass_number": 1,
                    "position_number": 1,
                    "gallons": "12",
                    "expected_version": stale_record_version,
                },
                follow_redirects=True,
            )
        self.assertIn(b"Truck assignment changed while you were editing", truck_response.data)
        self.assertIn(b"Gallons changed while you were editing", gallons_response.data)
        db.session.refresh(truck)
        db.session.refresh(record)
        self.assertEqual(truck.truck_number, "2")
        self.assertEqual(record.gallons, Decimal("11"))

    def _set_gallons(self, pass_number, position, gallons, *, record=None, context=None):
        return set_neosubzero_spray_gallons(
            self.operation,
            self.mission,
            self.event,
            pass_number,
            position,
            gallons,
            fluid_settings=neosubzero_fluid_settings(self.gateway),
            application_context=context or {},
            record=record,
        )

    def _unit(self, unit_type, name, parent=None):
        unit = StaffingUnit(unit_type=unit_type, name=name, parent=parent, active=True)
        db.session.add(unit)
        db.session.flush()
        return unit

    def _person(self, employee_id, first_name, last_name, work_area):
        person = StaffingPerson(
            employee_id=employee_id,
            first_name=first_name,
            last_name=last_name,
            seniority_date=date(2020, 1, 1),
            classification="part_time",
            employee_status="active",
            active=True,
        )
        db.session.add(person)
        db.session.flush()
        db.session.add(
            StaffingWorkAssignment(
                person_id=person.id,
                work_area_unit_id=work_area.id,
                active=True,
            )
        )
        db.session.flush()
        return person

    def _user(self, username, role, employee_id):
        user = User(
            username=username,
            email=f"{username}@example.com",
            employee_id=employee_id,
            first_name="SubZero",
            last_name="User",
            full_name="SubZero User",
            role=role,
            is_active=True,
            email_verified_at=datetime.utcnow(),
        )
        set_user_password(user, "TestPassword123!")
        db.session.add(user)
        db.session.flush()
        backfill_default_gateway_node_roles(user, role=role)
        db.session.commit()
        return user

    def _login(self, client, user):
        return client.post(
            "/login",
            data={"username": user.username, "password": "TestPassword123!"},
        )


if __name__ == "__main__":
    unittest.main()
