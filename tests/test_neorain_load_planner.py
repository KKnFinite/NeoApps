from datetime import date, datetime, time
import unittest

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
)
from app.neonodes.neorain.services import (
    LoadPlannerAssignmentError,
    assign_current_sort_only_departure_load_planner,
    assign_master_departure_load_planner,
    effective_neorain_load_planner,
    eligible_neorain_load_planners,
    neorain_load_planner_lineup,
)


class NeoRainLoadPlannerTest(unittest.TestCase):
    def setUp(self):
        config = type(
            "NeoRainLoadPlannerTestConfig",
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
        self.gateway = Gateway(code="RFD", name="RFD")
        db.session.add(self.gateway)
        self._create_staffing_hierarchy()
        self.eligible = self._person("LP-ELIGIBLE", self.load_planners)
        self.ineligible = self._person("LP-INELIGIBLE", self.other_area)
        self.inactive = self._person("LP-INACTIVE", self.load_planners, active=False)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_eligible_roster_uses_only_active_canonical_load_planner_assignments(self):
        self.assertEqual(
            [person.employee_id for person in eligible_neorain_load_planners()],
            [self.eligible.employee_id],
        )

    def test_master_assignment_persists_and_master_linked_mission_resolves_it(self):
        master = self._master("5X100")
        operation = self._operation()
        mission = self._mission(operation, "5X100", master=master)
        db.session.flush()

        assign_master_departure_load_planner(master, self.eligible)
        db.session.flush()
        db.session.expire(mission, ["master_flight_schedule"])

        self.assertEqual(master.load_planner_person_id, self.eligible.id)
        self.assertEqual(effective_neorain_load_planner(mission), self.eligible)

        assign_master_departure_load_planner(master)
        self.assertIsNone(master.load_planner_person_id)

    def test_current_sort_only_assignment_is_temporary_and_master_linked_mission_is_rejected(self):
        operation = self._operation()
        manual = self._mission(operation, "5X200", source="manual")
        master = self._master("5X201")
        linked = self._mission(operation, "5X201", master=master)
        db.session.flush()

        assign_current_sort_only_departure_load_planner(manual, self.eligible)

        self.assertEqual(manual.load_planner_person_id, self.eligible.id)
        self.assertEqual(effective_neorain_load_planner(manual), self.eligible)
        with self.assertRaisesRegex(LoadPlannerAssignmentError, "Master-linked"):
            assign_current_sort_only_departure_load_planner(linked, self.eligible)
        self.assertIsNone(linked.load_planner_person_id)

    def test_ineligible_or_inactive_planners_are_rejected_and_resolve_unassigned(self):
        master = self._master("5X300")
        operation = self._operation()
        manual = self._mission(operation, "5X300", source="manual")
        db.session.flush()

        with self.assertRaisesRegex(LoadPlannerAssignmentError, "eligible"):
            assign_master_departure_load_planner(master, self.ineligible)
        with self.assertRaisesRegex(LoadPlannerAssignmentError, "eligible"):
            assign_current_sort_only_departure_load_planner(manual, self.inactive)

        manual.load_planner_person_id = self.eligible.id
        self.eligible.active = False
        db.session.flush()
        self.assertIsNone(effective_neorain_load_planner(manual))

    def test_lineup_separates_persistent_master_and_current_sort_only_departures(self):
        operation = self._operation()
        master = self._master("5X400")
        assign_master_departure_load_planner(master, self.eligible)
        self._mission(operation, "5X400", master=master)
        manual = self._mission(operation, "5X401", source="manual")
        assign_current_sort_only_departure_load_planner(manual, self.eligible)
        db.session.commit()

        lineup = neorain_load_planner_lineup(self.gateway, operation)

        self.assertEqual(
            [(row["departure"].flight_number, row["planner"]) for row in lineup["master_departures"]],
            [("5X400", self.eligible)],
        )
        self.assertEqual(
            [row["departure"].flight_number for row in lineup["current_sort_only_departures"]],
            ["5X401"],
        )
        self.assertEqual(lineup["current_sort_only_departures"][0]["planner"], self.eligible)
        self.assertEqual(
            neorain_load_planner_lineup(self.gateway, None),
            {"master_departures": (), "current_sort_only_departures": ()},
        )

    def test_only_departures_can_receive_load_planner_assignments(self):
        arrival_master = self._master("5X500", mission_type="arrival")
        operation = self._operation()
        arrival_mission = self._mission(
            operation, "5X500", mission_type="arrival", source="manual"
        )

        with self.assertRaisesRegex(LoadPlannerAssignmentError, "departure"):
            assign_master_departure_load_planner(arrival_master, self.eligible)
        with self.assertRaisesRegex(LoadPlannerAssignmentError, "departure"):
            assign_current_sort_only_departure_load_planner(arrival_mission, self.eligible)

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

    def _person(self, employee_id, work_area, *, active=True):
        person = StaffingPerson(
            employee_id=employee_id,
            first_name="Load",
            last_name=employee_id,
            seniority_date=date(2020, 1, 1),
            classification="part_time",
            employee_status="active",
            active=active,
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

    def _master(self, flight_number, *, mission_type="departure"):
        master = MasterFlightSchedule(
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
            sort_name="night",
            mission_type=mission_type,
            flight_number=flight_number,
            origin="RFD" if mission_type == "departure" else "SDF",
            destination="SDF" if mission_type == "departure" else "RFD",
            active=True,
            active_days="monday,tuesday",
            planned_time_local=time(1, 30),
        )
        db.session.add(master)
        db.session.flush()
        return master

    def _mission(self, operation, flight_number, *, master=None, source="master", mission_type="departure"):
        mission = SortDateMission(
            sort_date_operation_id=operation.id,
            sort_date=operation.sort_date,
            gateway_code=self.gateway.code,
            sort_name="night",
            mission_type=mission_type,
            mission_source=source,
            master_flight_schedule_id=master.id if master else None,
            flight_number=flight_number,
            origin="RFD" if mission_type == "departure" else "SDF",
            destination="SDF" if mission_type == "departure" else "RFD",
            planned_datetime_local=datetime(2026, 9, 1, 1, 30),
            planned_datetime_utc=datetime(2026, 9, 1, 1, 30),
            planned_source="master",
        )
        db.session.add(mission)
        db.session.flush()
        return mission


if __name__ == "__main__":
    unittest.main()
