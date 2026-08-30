from datetime import date, datetime, time
import unittest
from unittest.mock import patch

from app import create_app
from app.extensions import db
from app.models import (
    Gateway,
    MasterFlightSchedule,
    SortDateMission,
    SortDateOperation,
    StaffingPerson,
    StaffingUnit,
    StaffingWorkAssignment,
    User,
)
from app.services.access_control import backfill_default_gateway_node_roles
from app.services.live_collaboration import entity_version
from app.services.password_policy import set_user_password
from app.services.permission_rules import ensure_default_permission_rules


class NeoRainLoadPlannerRoutesTest(unittest.TestCase):
    def setUp(self):
        config = type(
            "NeoRainLoadPlannerRouteTestConfig",
            (),
            {
                "SECRET_KEY": "test",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            },
        )
        self.app = create_app(config)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        ensure_default_permission_rules()
        self.gateway = Gateway(code="RFD", name="RFD")
        db.session.add(self.gateway)
        self._create_staffing_hierarchy()
        self.eligible = self._person("LP-ELIGIBLE", self.load_planners)
        self.ineligible = self._person("LP-INELIGIBLE", self.other_area)
        self.master = self._master("5X100")
        self.operation = self._operation()
        self.manual = self._mission("5X200", source="manual")
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_master_section_renders_and_saves_without_a_current_sort(self):
        editor = self._user("load_planner_editor", "operator")
        self._login(editor)
        with self._current_operation(None):
            initial = self.client.get("/neorain/load-planner-lineup")
            response = self._post(
                assignment_scope="master",
                departure_id=self.master.id,
                planner_person_id=self.eligible.id,
                expected_version=entity_version(self.master),
            )

        self.assertEqual(initial.status_code, 200)
        self.assertIn(b"MASTER DEPARTURES", initial.data)
        self.assertIn(b"5X100", initial.data)
        self.assertIn(b"CURRENT SORT ONLY", initial.data)
        self.assertEqual(response.status_code, 302)
        db.session.expire_all()
        self.assertEqual(
            db.session.get(MasterFlightSchedule, self.master.id).load_planner_person_id,
            self.eligible.id,
        )

    def test_current_sort_only_section_saves_temporary_assignment_and_unassigned(self):
        editor = self._user("load_planner_current_editor", "operator")
        self._login(editor)
        with self._current_operation(self.operation):
            initial = self.client.get("/neorain/load-planner-lineup")
            assign = self._post(
                assignment_scope="current_sort",
                departure_id=self.manual.id,
                planner_person_id=self.eligible.id,
                expected_version=entity_version(self.manual),
            )

        self.assertEqual(initial.status_code, 200)
        self.assertIn(b"5X200", initial.data)
        self.assertEqual(assign.status_code, 302)
        db.session.expire_all()
        manual = db.session.get(SortDateMission, self.manual.id)
        self.assertEqual(manual.load_planner_person_id, self.eligible.id)

        with self._current_operation(self.operation):
            clear = self._post(
                assignment_scope="current_sort",
                departure_id=manual.id,
                planner_person_id="",
                expected_version=entity_version(manual),
            )
        self.assertEqual(clear.status_code, 302)
        db.session.expire_all()
        self.assertIsNone(db.session.get(SortDateMission, self.manual.id).load_planner_person_id)

    def test_viewer_is_read_only_and_cannot_save(self):
        viewer = self._user("load_planner_viewer", "watcher")
        self._login(viewer)
        with self._current_operation(self.operation):
            page = self.client.get("/neorain/load-planner-lineup")
            denied = self._post(
                assignment_scope="current_sort",
                departure_id=self.manual.id,
                planner_person_id=self.eligible.id,
                expected_version=entity_version(self.manual),
            )

        self.assertEqual(page.status_code, 200)
        self.assertIn(b"UNASSIGNED", page.data)
        self.assertNotIn(b"neorain-load-planner-form", page.data)
        self.assertEqual(denied.status_code, 403)
        db.session.expire_all()
        self.assertIsNone(db.session.get(SortDateMission, self.manual.id).load_planner_person_id)

    def test_stale_or_ineligible_assignment_is_rejected_without_overwrite(self):
        editor = self._user("load_planner_conflict_editor", "operator")
        self._login(editor)
        expected_version = entity_version(self.master)
        self.master.destination = "ONT"
        db.session.commit()

        with self._current_operation(None):
            stale = self._post(
                assignment_scope="master",
                departure_id=self.master.id,
                planner_person_id=self.eligible.id,
                expected_version=expected_version,
            )
            ineligible = self._post(
                assignment_scope="master",
                departure_id=self.master.id,
                planner_person_id=self.ineligible.id,
                expected_version=entity_version(self.master),
            )

        self.assertEqual(stale.status_code, 409)
        self.assertIn(b"changed while you were editing", stale.data)
        self.assertEqual(ineligible.status_code, 400)
        db.session.expire_all()
        self.assertIsNone(db.session.get(MasterFlightSchedule, self.master.id).load_planner_person_id)

    def _post(self, **data):
        return self.client.post(
            "/neorain/load-planner-lineup",
            data=data,
            follow_redirects=False,
        )

    def _current_operation(self, operation):
        return patch(
            "app.neonodes.neorain.routes.current_neorain_outbound_operation",
            return_value=operation,
        )

    def _create_staffing_hierarchy(self):
        staffing_sort = StaffingUnit(unit_type="sort", name="Night", active=True)
        ramp = StaffingUnit(unit_type="operation", name="Ramp", parent=staffing_sort, active=True)
        load_planning = StaffingUnit(
            unit_type="department", name="Load Planning", parent=ramp, active=True
        )
        self.load_planners = StaffingUnit(
            unit_type="work_area", name="Load Planners", parent=load_planning, active=True
        )
        self.other_area = StaffingUnit(
            unit_type="work_area", name="Other", parent=load_planning, active=True
        )
        db.session.add_all(
            [staffing_sort, ramp, load_planning, self.load_planners, self.other_area]
        )
        db.session.flush()

    def _person(self, employee_id, work_area):
        person = StaffingPerson(
            employee_id=employee_id,
            first_name="Load",
            last_name=employee_id,
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
        return person

    def _master(self, flight_number):
        master = MasterFlightSchedule(
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
            sort_name="night",
            mission_type="departure",
            flight_number=flight_number,
            origin="RFD",
            destination="SDF",
            active=True,
            active_days="monday",
            planned_time_local=time(1, 30),
        )
        db.session.add(master)
        db.session.flush()
        return master

    def _operation(self):
        operation = SortDateOperation(
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
            sort_name="night",
            sort_date=date(2026, 9, 1),
        )
        db.session.add(operation)
        db.session.flush()
        return operation

    def _mission(self, flight_number, *, source):
        mission = SortDateMission(
            sort_date_operation_id=self.operation.id,
            sort_date=self.operation.sort_date,
            gateway_code=self.gateway.code,
            sort_name="night",
            mission_type="departure",
            mission_source=source,
            flight_number=flight_number,
            origin="RFD",
            destination="SDF",
            planned_datetime_local=datetime(2026, 9, 1, 1, 30),
            planned_datetime_utc=datetime(2026, 9, 1, 1, 30),
            planned_source="manual",
        )
        db.session.add(mission)
        db.session.flush()
        return mission

    def _user(self, username, role):
        user = User(
            username=username,
            email=f"{username}@example.test",
            first_name="Rain",
            last_name="User",
            full_name="Rain User",
            employee_id=f"EMP-{username}",
            email_verified_at=datetime.utcnow(),
            role=role,
            is_active=True,
        )
        set_user_password(user, "TestPassword123!")
        db.session.add(user)
        db.session.flush()
        backfill_default_gateway_node_roles(user, role=role)
        db.session.commit()
        return user

    def _login(self, user):
        return self.client.post(
            "/login",
            data={"username": user.username, "password": "TestPassword123!"},
            follow_redirects=False,
        )


if __name__ == "__main__":
    unittest.main()
