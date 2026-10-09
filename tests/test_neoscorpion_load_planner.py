"""Canonical Load Planner projection into Dispatch; no fuel behavior changes."""
from datetime import date, datetime, time
import re
import unittest

from sqlalchemy import event

from app.extensions import db
from app.models import (
    MasterFlightSchedule, NeoScorpionFuelTankState, NeoScorpionFuelWorkState,
    StaffingPerson, StaffingUnit, StaffingWorkAssignment,
)
from app.services.neoscorpion import (
    _attach_dispatch_load_planners, _departure_missions, fuel_dispatch_context,
)
from tests import test_neoscorpion_dispatch_planning as dispatch_fixture


class DispatchLoadPlannerTest(unittest.TestCase):
    def setUp(self):
        self.fixture = dispatch_fixture.NeoScorpionDispatchPlanningTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.gateway, self.operation = self.fixture.gateway, self.fixture.operation
        parent = None
        for unit_type, name in (("sort", "Night"), ("operation", "Ramp"),
                                ("department", "Load Planning"), ("work_area", "Load Planners")):
            parent = StaffingUnit(unit_type=unit_type, name=name, parent=parent, active=True)
            db.session.add(parent)
        db.session.flush()
        self.work_area = parent
        self.alex = self._person("LP-A", "Alex", "Planner")
        self.blair = self._person("LP-B", "Blair", "Planner")
        db.session.commit()

    def _person(self, employee_id, first, last):
        person = StaffingPerson(employee_id=employee_id, first_name=first, last_name=last,
                                seniority_date=date(2020, 1, 1), classification="part_time", active=True)
        db.session.add(person)
        db.session.flush()
        db.session.add(StaffingWorkAssignment(person_id=person.id,
                                             work_area_unit_id=self.work_area.id, active=True))
        return person

    def _master(self, planner):
        master = MasterFlightSchedule(gateway_id=self.gateway.id, gateway_code=self.gateway.code,
            sort_name="night", mission_type="departure", flight_number="UPS901", origin=self.gateway.code,
            destination="SDF", active_days="monday", planned_time_local=time(23, 0),
            load_planner_person_id=planner.id if planner else None)
        db.session.add(master)
        db.session.flush()
        return master

    def _mission(self, index, *, master=None, planner=None):
        mission = self.fixture._mission("UPS901", f"N4LP{index}", 25_000, index)
        mission.master_flight_schedule_id = master.id if master else None
        mission.mission_source = "master" if master else "manual"
        mission.load_planner_person_id = planner.id if planner else None
        return mission

    def test_assigned_and_unassigned_both_types_resolve_only_canonical_identity(self):
        assigned_master = self._master(self.alex)
        unassigned_master = self._master(None)
        # A stale mission-level value must never override a linked Master row.
        master_assigned = self._mission(1, master=assigned_master, planner=self.blair)
        master_unassigned = self._mission(2, master=unassigned_master, planner=self.blair)
        temporary_assigned = self._mission(3, planner=self.blair)
        temporary_assigned.mission_source = "api"
        temporary_assigned.api_added_current_sort_only = True
        temporary_unassigned = self._mission(4)
        for mission in (master_assigned, master_unassigned, temporary_assigned, temporary_unassigned):
            mission.assigned_tail_number = "N4LP1"
        db.session.commit()
        rows = {row["mission"].id: row for row in fuel_dispatch_context(self.gateway)["rows"]}
        for mission, expected in ((master_assigned, self.alex.full_name),
                                  (master_unassigned, "UNASSIGNED"),
                                  (temporary_assigned, self.blair.full_name),
                                  (temporary_unassigned, "UNASSIGNED")):
            with self.subTest(mission=mission.id):
                self.assertEqual(rows[mission.id]["load_planner_name"], expected)

    def test_planner_projection_queries_do_not_grow_with_departure_count(self):
        self._mission(1, master=self._master(self.alex))
        db.session.commit()

        def project():
            db.session.expire_all()
            statements = []
            def record(_connection, _cursor, statement, _parameters, _context, _many):
                statements.append(statement)
            operation_id = self.operation.id
            event.listen(db.engine, "before_cursor_execute", record)
            try:
                missions = _departure_missions(type("Scope", (), {"id": operation_id})(),
                                                include_load_planners=True)
                rows = [{"mission": mission} for mission in missions]
                _attach_dispatch_load_planners(rows)
                self.assertTrue(all(row["load_planner_name"] != "UNASSIGNED" for row in rows))
                return len(statements)
            finally:
                event.remove(db.engine, "before_cursor_execute", record)
        initial_count = project()
        for index in range(2, 31):
            person = self._person(f"LP-{index}", "Planner", str(index))
            self._mission(index, master=self._master(person) if index % 2 else None,
                          planner=person if not index % 2 else None)
        db.session.commit()
        self.assertEqual(project(), initial_count)
        self.assertEqual(initial_count, 3, "One joined mission query plus Rain's two eligibility queries")

    def test_name_renders_after_unchanged_copy_and_with_incomplete_calculation(self):
        assigned = self._mission(1, master=self._master(self.alex))
        self._mission(2)
        assignment = self.fixture._assignment(assigned)
        work = NeoScorpionFuelWorkState(fuel_assignment_id=assignment.id, tail_number="N4LP1",
                                       apu_running=False, apu_allowance_lbs=0,
                                       off_at_utc=datetime(2026, 8, 19, 3, 0))
        db.session.add(work)
        db.session.flush()
        for code, lbs in (("left", 8000), ("ctr", 9000), ("right", 8000)):
            db.session.add(NeoScorpionFuelTankState(fuel_work_state_id=work.id, tank_code=code,
                                                    remaining_lbs=lbs, actual_lbs=lbs))
        db.session.commit()
        self.fixture._login_user("planner_viewer", "simulator")
        before = self.fixture._row_counts()
        response = self.fixture.client.get("/neoscorpion/fuel-dispatch")
        self.assertEqual(response.status_code, 200)
        sections = re.findall(r'<section><strong>LOAD PLANNING</strong>(.*?)</section>',
                              response.data.decode(), flags=re.S)
        self.assertEqual(len(sections), 2)
        assigned_section = next(section for section in sections if self.alex.full_name in section)
        self.assertIn('data-copy-value="UPS901 SDF N4LP1 NEO &gt; 25.0"', assigned_section)
        self.assertLess(assigned_section.index('>COPY</button>'),
                        assigned_section.index('Load Planner: Alex Planner'))
        self.assertTrue(any('Load Planner: UNASSIGNED' in section for section in sections))
        self.assertTrue(all('<input' not in section and '<select' not in section for section in sections))
        self.assertEqual(self.fixture._row_counts(), before)
