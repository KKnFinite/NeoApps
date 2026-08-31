import unittest
from datetime import date, datetime
from unittest.mock import patch

from werkzeug.datastructures import MultiDict

from app import create_app
from app.extensions import db
from app.models import (
    Gateway,
    NeoSubZeroCalloutAssignment,
    SortDateOperation,
    StaffingDailyAttendance,
    StaffingPerson,
    StaffingPersonQualification,
    StaffingUnit,
    StaffingWorkAssignment,
    User,
)
from app.services import neostaffing as staffing_service
from app.services.access_control import backfill_default_gateway_node_roles
from app.services.live_collaboration import entity_version
from app.services.neosubzero_staffing import (
    DEICE_QUALIFICATION_KEY,
    NeoSubZeroStaffingError,
    current_subzero_staffing_pool,
    neosubzero_callout_context,
    neosubzero_qualification_people,
    permanent_deice_work_area_ids,
    set_neosubzero_callout_membership,
    set_staffing_person_qualification,
)
from app.services.password_policy import set_user_password
from app.services.permission_rules import (
    DEFAULT_PERMISSION_RULES,
    ensure_default_permission_rules,
)


class NeoSubZeroStaffingTest(unittest.TestCase):
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
        self.night = self._unit("sort", "Night")
        aviation = self._unit("operation", "Aviation Services", self.night)
        self.deice = self._unit("work_area", "Deice", aviation)
        hub = self._unit("operation", "Hub", self.night)
        self.unload = self._unit("work_area", "Unload", hub)
        self.permanent = self._person("P100", "Permanent", "Deicer", self.deice)
        self.callout = self._person("P200", "Qualified", "Callout", self.unload)
        self.other = self._person("P300", "Other", "Employee", self.unload)
        self.recorder = User(
            username="attendance_recorder",
            email="attendance@example.com",
            employee_id="R100",
            first_name="Attendance",
            last_name="Recorder",
            full_name="Attendance Recorder",
            role="master",
            is_active=True,
            email_verified_at=datetime.utcnow(),
        )
        set_user_password(self.recorder, "TestPassword123!")
        db.session.add(self.recorder)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_shared_deice_qualification_is_reversible_and_searchable(self):
        row = set_staffing_person_qualification(
            self.callout,
            DEICE_QUALIFICATION_KEY,
            True,
            user_id=self.recorder.id,
        )
        db.session.commit()
        self.assertTrue(row.active)
        self.assertEqual(row.qualification_key, "deice")
        result = neosubzero_qualification_people("P200")
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]["qualified"])
        self.assertIn("Night / Hub / Unload", result[0]["work_area_path"])

        set_staffing_person_qualification(
            self.callout,
            DEICE_QUALIFICATION_KEY,
            False,
            user_id=self.recorder.id,
            qualification=row,
        )
        db.session.commit()
        self.assertFalse(row.active)
        self.assertIsNotNone(row.revoked_at)

    def test_permanent_detection_callout_add_remove_and_preplanning_pool(self):
        set_staffing_person_qualification(
            self.callout,
            DEICE_QUALIFICATION_KEY,
            True,
            user_id=self.recorder.id,
        )
        db.session.commit()
        self.assertEqual(permanent_deice_work_area_ids(), {self.deice.id})
        context = neosubzero_callout_context(self.operation)
        self.assertEqual(
            [item["person"].id for item in context["permanent"]],
            [self.permanent.id],
        )
        self.assertTrue(context["permanent"][0]["available"])
        self.assertEqual(
            [item["person"].id for item in context["candidates"]],
            [self.callout.id],
        )

        assignment = set_neosubzero_callout_membership(
            self.operation,
            self.callout,
            True,
            user_id=self.recorder.id,
        )
        db.session.commit()
        pool = current_subzero_staffing_pool(self.operation)
        self.assertEqual(
            {(item["person"].id, item["source"]) for item in pool},
            {
                (self.permanent.id, "permanent"),
                (self.callout.id, "callout"),
            },
        )
        self.assertEqual(NeoSubZeroCalloutAssignment.query.count(), 1)

        set_neosubzero_callout_membership(
            self.operation,
            self.callout,
            False,
            user_id=self.recorder.id,
            assignment=assignment,
        )
        db.session.commit()
        self.assertFalse(assignment.active)
        self.assertEqual(
            [item["person"].id for item in current_subzero_staffing_pool(self.operation)],
            [self.permanent.id],
        )

    def test_permanent_and_unqualified_people_cannot_be_callouts(self):
        with self.assertRaises(NeoSubZeroStaffingError):
            set_neosubzero_callout_membership(
                self.operation,
                self.permanent,
                True,
                user_id=self.recorder.id,
            )
        with self.assertRaises(NeoSubZeroStaffingError):
            set_neosubzero_callout_membership(
                self.operation,
                self.other,
                True,
                user_id=self.recorder.id,
            )
        self.assertEqual(NeoSubZeroCalloutAssignment.query.count(), 0)

    def test_revoking_deice_qualification_deactivates_callout(self):
        qualification = set_staffing_person_qualification(
            self.callout,
            DEICE_QUALIFICATION_KEY,
            True,
            user_id=self.recorder.id,
        )
        assignment = set_neosubzero_callout_membership(
            self.operation,
            self.callout,
            True,
            user_id=self.recorder.id,
        )
        db.session.commit()
        set_staffing_person_qualification(
            self.callout,
            DEICE_QUALIFICATION_KEY,
            False,
            user_id=self.recorder.id,
            qualification=qualification,
        )
        db.session.commit()
        self.assertFalse(assignment.active)
        self.assertEqual(assignment.removal_reason, "qualification")
        set_staffing_person_qualification(
            self.callout,
            DEICE_QUALIFICATION_KEY,
            True,
            user_id=self.recorder.id,
            qualification=qualification,
        )
        db.session.commit()
        self.assertFalse(assignment.active)

    def test_not_here_attendance_removes_callout_without_auto_restore(self):
        set_staffing_person_qualification(
            self.callout,
            DEICE_QUALIFICATION_KEY,
            True,
            user_id=self.recorder.id,
        )
        assignment = set_neosubzero_callout_membership(
            self.operation,
            self.callout,
            True,
            user_id=self.recorder.id,
        )
        db.session.commit()
        with patch(
            "app.services.neostaffing.current_night_attendance_operation",
            return_value=self.operation,
        ):
            staffing_service.save_attendance(
                MultiDict(
                    {
                        "sort_date_operation_id": str(self.operation.id),
                        "sort_id": str(self.night.id),
                        f"status_{self.callout.id}": "call_in",
                    }
                ),
                self.recorder,
            )
            db.session.commit()
        self.assertFalse(assignment.active)
        self.assertEqual(assignment.removal_reason, "attendance")
        self.assertEqual(
            [item["person"].id for item in current_subzero_staffing_pool(self.operation)],
            [self.permanent.id],
        )

        with patch(
            "app.services.neostaffing.current_night_attendance_operation",
            return_value=self.operation,
        ):
            staffing_service.save_attendance(
                MultiDict(
                    {
                        "sort_date_operation_id": str(self.operation.id),
                        "sort_id": str(self.night.id),
                        f"status_{self.callout.id}": "here",
                    }
                ),
                self.recorder,
            )
            db.session.commit()
        self.assertFalse(assignment.active)
        self.assertEqual(
            [item["person"].id for item in current_subzero_staffing_pool(self.operation)],
            [self.permanent.id],
        )
        candidates = neosubzero_callout_context(self.operation)["candidates"]
        self.assertTrue(candidates[0]["available"])

    def test_permanent_employee_attendance_controls_pool_without_callout_row(self):
        db.session.add(
            StaffingDailyAttendance(
                attendance_date=self.operation.sort_date,
                sort_unit_id=self.night.id,
                sort_date_operation_id=self.operation.id,
                person_id=self.permanent.id,
                work_area_unit_id=self.deice.id,
                operation_unit_id=self.deice.parent_id,
                status="no_call",
            )
        )
        db.session.commit()
        self.assertEqual(current_subzero_staffing_pool(self.operation), ())
        self.assertEqual(NeoSubZeroCalloutAssignment.query.count(), 0)

    def test_permission_defaults_schema_and_route_enforcement(self):
        defaults = {key: role for key, role, _description in DEFAULT_PERMISSION_RULES}
        self.assertEqual(defaults["neosubzero.qualifications.view"], "watcher")
        self.assertEqual(defaults["neosubzero.qualifications.edit"], "master")
        self.assertEqual(defaults["neosubzero.callouts.view"], "watcher")
        self.assertEqual(defaults["neosubzero.callouts.edit"], "master")
        self.assertIn("staffing_person_qualifications", db.metadata.tables)
        self.assertIn("neosubzero_callout_assignments", db.metadata.tables)

        watcher = self._user("subzero_qualification_watcher", "watcher")
        client = self.app.test_client()
        self._login(client, watcher)
        with patch(
            "app.neonodes.neosubzero.routes.current_neosubzero_operation",
            return_value=self.operation,
        ):
            self.assertEqual(client.get("/neosubzero/qualifications").status_code, 200)
            self.assertEqual(client.get("/neosubzero/callouts").status_code, 200)
            denied = client.post(
                "/neosubzero/qualifications",
                data={
                    "person_id": self.callout.id,
                    "action": "qualify",
                    "expected_version": "",
                },
            )
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(StaffingPersonQualification.query.count(), 0)

        manager = self._user("subzero_qualification_master", "master")
        self._login(client, manager)
        with patch(
            "app.neonodes.neosubzero.routes.current_neosubzero_operation",
            return_value=self.operation,
        ):
            saved = client.post(
                "/neosubzero/qualifications",
                data={
                    "person_id": self.callout.id,
                    "action": "qualify",
                    "expected_version": "",
                },
            )
        self.assertEqual(saved.status_code, 302)
        self.assertTrue(StaffingPersonQualification.query.one().active)

    def test_callout_route_adds_and_removes_for_authorized_manager(self):
        set_staffing_person_qualification(
            self.callout,
            DEICE_QUALIFICATION_KEY,
            True,
            user_id=self.recorder.id,
        )
        manager = self._user("subzero_callout_master", "master")
        client = self.app.test_client()
        self._login(client, manager)
        with patch(
            "app.neonodes.neosubzero.routes.current_neosubzero_operation",
            return_value=self.operation,
        ):
            added = client.post(
                "/neosubzero/callouts",
                data={
                    "person_id": self.callout.id,
                    "action": "add",
                    "expected_version": "",
                },
            )
        self.assertEqual(added.status_code, 302)
        assignment = NeoSubZeroCalloutAssignment.query.one()
        self.assertTrue(assignment.active)

        db.session.refresh(assignment)
        with patch(
            "app.neonodes.neosubzero.routes.current_neosubzero_operation",
            return_value=self.operation,
        ):
            removed = client.post(
                "/neosubzero/callouts",
                data={
                    "person_id": self.callout.id,
                    "action": "remove",
                    "expected_version": entity_version(assignment),
                },
            )
        self.assertEqual(removed.status_code, 302)
        self.assertFalse(assignment.active)

    def _unit(self, unit_type, name, parent=None):
        unit = StaffingUnit(
            unit_type=unit_type,
            name=name,
            parent=parent,
            active=True,
        )
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

    def _user(self, username, role):
        user = User(
            username=username,
            email=f"{username}@example.com",
            employee_id=f"EMP-{username}",
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
            follow_redirects=False,
        )


if __name__ == "__main__":
    unittest.main()
