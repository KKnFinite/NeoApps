import unittest
from datetime import date, datetime, timedelta
from unittest.mock import patch

from app import create_app
from app.extensions import db
from app.models import (
    Gateway,
    LiveScreenRefreshSetting,
    NeoSubZeroCalloutAssignment,
    NeoSubZeroPretreatState,
    NeoSubZeroUccAssignment,
    SortDateMission,
    SortDateOperation,
    SortDateParkingAssignment,
    SortDateTailState,
    StaffingPerson,
    StaffingUnit,
    StaffingWorkAssignment,
    User,
)
from app.services.access_control import backfill_default_gateway_node_roles
from app.services.live_collaboration import entity_version
from app.services.neosubzero_staffing import (
    DEICE_QUALIFICATION_KEY,
    deactivate_neosubzero_callouts_for_attendance,
    set_neosubzero_callout_membership,
    set_staffing_person_qualification,
)
from app.services.neosubzero_ucc import (
    UCC_REFRESH_KEY,
    NeoSubZeroUccError,
    neosubzero_ucc_context,
    neosubzero_ucc_revision,
    set_neosubzero_ucc_assignment,
)
from app.services.password_policy import set_user_password
from app.services.permission_rules import (
    DEFAULT_PERMISSION_RULES,
    ensure_default_permission_rules,
)


class NeoSubZeroUccTest(unittest.TestCase):
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
        self.remote = self._departure("5X100", "N100", "SDF", 1)
        self.alpha = self._departure("5X200", "N200", "ONT", 2)
        db.session.add_all(
            [
                SortDateParkingAssignment(
                    sort_date_operation_id=self.operation.id,
                    tail_number="N100",
                    ramp_code="R",
                    position_code="R09",
                ),
                SortDateParkingAssignment(
                    sort_date_operation_id=self.operation.id,
                    tail_number="N200",
                    ramp_code="A",
                    position_code="A03",
                ),
            ]
        )
        night = self._unit("sort", "Night")
        aviation = self._unit("operation", "Aviation Services", night)
        deice = self._unit("work_area", "Deice", aviation)
        hub = self._unit("operation", "Hub", night)
        unload = self._unit("work_area", "Unload", hub)
        self.permanent = self._person("D100", "Permanent", "Deicer", deice)
        self.callout = self._person("D200", "Callout", "Deicer", unload)
        for person in (self.permanent, self.callout):
            set_staffing_person_qualification(
                person,
                DEICE_QUALIFICATION_KEY,
                True,
            )
        self.callout_row = set_neosubzero_callout_membership(
            self.operation,
            self.callout,
            True,
        )
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_context_uses_canonical_ramps_parking_and_subzero_states(self):
        db.session.add(
            NeoSubZeroPretreatState(
                sort_date_operation_id=self.operation.id,
                tail_number="N200",
                pretreat_planned=True,
            )
        )
        db.session.add(
            SortDateTailState(
                sort_date=self.operation.sort_date,
                gateway_code="RFD",
                sort_name="night",
                tail_number="N100",
                deice_status="negative",
            )
        )
        db.session.commit()
        context = neosubzero_ucc_context(self.gateway, self.operation)
        self.assertEqual([ramp["name"] for ramp in context["ramps"]], ["Remote", "Alpha"])
        self.assertEqual(context["ramps"][0]["throat"]["flight"], "5X100")
        self.assertEqual(context["ramps"][0]["aircraft"][0]["parking"], "R09")
        self.assertEqual(context["ramps"][0]["aircraft"][0]["visual_state"], "negative")
        self.assertEqual(context["ramps"][1]["aircraft"][0]["visual_state"], "pretreat-planned")
        self.assertEqual(
            {row["person"].id for row in context["staffing_pool"]},
            {self.permanent.id, self.callout.id},
        )

    def test_driver_flyer_assignment_and_duplicate_prevention(self):
        driver = set_neosubzero_ucc_assignment(
            self.operation, "Remote", 1, "driver", self.permanent
        )
        flyer = set_neosubzero_ucc_assignment(
            self.operation, "Remote", 1, "flyer", self.callout
        )
        db.session.commit()
        self.assertEqual(driver.person_id, self.permanent.id)
        self.assertEqual(flyer.person_id, self.callout.id)
        with self.assertRaises(NeoSubZeroUccError):
            set_neosubzero_ucc_assignment(
                self.operation, "Alpha", 2, "driver", self.permanent
            )
        self.assertEqual(NeoSubZeroUccAssignment.query.count(), 2)

    def test_not_here_callout_clears_ucc_and_here_restores_pool_membership(self):
        slot = set_neosubzero_ucc_assignment(
            self.operation, "Alpha", 2, "flyer", self.callout
        )
        db.session.commit()
        deactivate_neosubzero_callouts_for_attendance(
            self.operation, {self.callout.id: "call_in"}
        )
        db.session.commit()
        self.assertFalse(self.callout_row.active)
        self.assertEqual(self.callout_row.removal_reason, "attendance")
        self.assertIsNone(slot.person_id)

        deactivate_neosubzero_callouts_for_attendance(
            self.operation, {self.callout.id: "here"}
        )
        db.session.commit()
        self.assertTrue(self.callout_row.active)
        self.assertIsNone(slot.person_id)

    def test_manual_callout_removal_and_qualification_revoke_clear_slots(self):
        slot = set_neosubzero_ucc_assignment(
            self.operation, "Alpha", 1, "driver", self.callout
        )
        set_neosubzero_callout_membership(
            self.operation,
            self.callout,
            False,
            assignment=self.callout_row,
        )
        db.session.commit()
        self.assertIsNone(slot.person_id)
        deactivate_neosubzero_callouts_for_attendance(
            self.operation, {self.callout.id: "here"}
        )
        self.assertFalse(self.callout_row.active)

        permanent_slot = set_neosubzero_ucc_assignment(
            self.operation, "Remote", 4, "flyer", self.permanent
        )
        qualification = next(
            row
            for row in self.permanent.qualifications
            if row.qualification_key == DEICE_QUALIFICATION_KEY
        )
        set_staffing_person_qualification(
            self.permanent,
            DEICE_QUALIFICATION_KEY,
            False,
            qualification=qualification,
        )
        db.session.commit()
        self.assertIsNone(permanent_slot.person_id)

    def test_ucc_permissions_route_refresh_and_settings_contract(self):
        defaults = {key: role for key, role, _ in DEFAULT_PERMISSION_RULES}
        self.assertEqual(defaults["neosubzero.ucc.view"], "watcher")
        self.assertEqual(defaults["neosubzero.ucc.edit"], "simulator")
        self.assertIn("neosubzero_ucc_assignments", db.metadata.tables)

        watcher = self._user("ucc_watcher", "watcher")
        client = self.app.test_client()
        self._login(client, watcher)
        with patch(
            "app.neonodes.neosubzero.routes.current_neosubzero_operation",
            return_value=self.operation,
        ):
            page = client.get("/neosubzero/ucc")
            denied = client.post(
                "/neosubzero/ucc",
                data={
                    "ramp": "Remote",
                    "position_number": 1,
                    "team_role": "driver",
                    "person_id": self.permanent.id,
                    "expected_version": "",
                },
            )
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"THROAT", page.data)
        self.assertEqual(denied.status_code, 403)

        simulator = self._user("ucc_simulator", "simulator")
        self._login(client, simulator)
        before = neosubzero_ucc_revision(self.gateway, self.operation)
        with patch(
            "app.neonodes.neosubzero.routes.current_neosubzero_operation",
            return_value=self.operation,
        ):
            saved = client.post(
                "/neosubzero/ucc",
                data={
                    "ramp": "Remote",
                    "position_number": 1,
                    "team_role": "driver",
                    "person_id": self.permanent.id,
                    "expected_version": "",
                },
            )
            revision = client.get("/neosubzero/ucc/revision?revision=old")
        self.assertEqual(saved.status_code, 302)
        self.assertEqual(revision.status_code, 200)
        self.assertNotEqual(before, neosubzero_ucc_revision(self.gateway, self.operation))
        self.assertEqual(UCC_REFRESH_KEY, "neosubzero.ucc")

        master = self._user("ucc_master", "master")
        self._login(client, master)
        settings = client.get("/neosubzero/settings")
        self.assertIn(b"UCC", settings.data)
        self.assertIn(b"neosubzero.ucc", settings.data)
        saved_refresh = client.post(
            "/neosubzero/settings",
            data={
                "action": "save_refresh",
                "screen_key": UCC_REFRESH_KEY,
                "refresh_interval_seconds": "10",
            },
        )
        self.assertEqual(saved_refresh.status_code, 302)
        self.assertEqual(
            LiveScreenRefreshSetting.query.filter_by(
                gateway_id=self.gateway.id,
                screen_key=UCC_REFRESH_KEY,
            ).one().interval_seconds,
            10,
        )

    def test_ucc_route_rejects_stale_slot_version(self):
        simulator = self._user("ucc_stale_simulator", "simulator")
        slot = set_neosubzero_ucc_assignment(
            self.operation, "Remote", 2, "driver", self.permanent
        )
        db.session.commit()
        original_version = entity_version(slot)
        slot.person_id = self.callout.id
        db.session.commit()
        client = self.app.test_client()
        self._login(client, simulator)
        with patch(
            "app.neonodes.neosubzero.routes.current_neosubzero_operation",
            return_value=self.operation,
        ):
            response = client.post(
                "/neosubzero/ucc",
                data={
                    "ramp": "Remote",
                    "position_number": 2,
                    "team_role": "driver",
                    "person_id": self.permanent.id,
                    "expected_version": original_version,
                },
                follow_redirects=True,
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"changed while you were editing", response.data)
        db.session.refresh(slot)
        self.assertEqual(slot.person_id, self.callout.id)

    def _departure(self, flight, tail, destination, hour):
        mission = SortDateMission(
            sort_date=self.operation.sort_date,
            gateway_code="RFD",
            sort_name="night",
            sort_date_operation_id=self.operation.id,
            mission_type="departure",
            mission_source="master",
            flight_number=flight,
            origin="RFD",
            destination=destination,
            timezone="America/Chicago",
            planned_datetime_local=datetime(2026, 8, 31, hour),
            planned_datetime_utc=datetime(2026, 8, 31, hour),
            assigned_tail_number=tail,
            departure_status="scheduled",
        )
        db.session.add(mission)
        db.session.flush()
        return mission

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

    def _user(self, username, role):
        user = User(
            username=username,
            email=f"{username}@example.com",
            employee_id=f"EMP-{username}",
            first_name="UCC",
            last_name="User",
            full_name="UCC User",
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
