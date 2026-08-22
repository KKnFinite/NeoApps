from datetime import date
from pathlib import Path
import unittest

from sqlalchemy import event

from app import create_app
from app.extensions import db
from app.models import StaffingPerson, StaffingShiftFlowPlan, StaffingUnit, StaffingWorkAssignment
from app.services import neostaffing as staffing_service


class ShiftFlowTest(unittest.TestCase):
    def setUp(self):
        config = type("TestConfig", (), {"SECRET_KEY": "test", "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", "SQLALCHEMY_TRACK_MODIFICATIONS": False})
        self.app = create_app(config)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.night = StaffingUnit(unit_type="sort", name="Night")
        self.ramp = StaffingUnit(unit_type="operation", name="Ramp", parent=self.night)
        self.shift = StaffingUnit(unit_type="department", name="Shift", parent=self.ramp)
        self.door = StaffingUnit(unit_type="work_area", name="Door 1", parent=self.shift)
        self.ballmat = StaffingUnit(unit_type="work_area", name="Ballmat A", parent=self.shift)
        self.discharge = StaffingUnit(unit_type="work_area", name="Discharge", parent=self.shift)
        self.other = StaffingUnit(unit_type="work_area", name="Other", parent=self.shift)
        self.empty_door = StaffingUnit(unit_type="work_area", name="Door 2", parent=self.shift)
        self.empty_ballmat = StaffingUnit(unit_type="work_area", name="Ballmat B", parent=self.shift)
        self.non_shift = StaffingUnit(unit_type="work_area", name="Door Outside", parent=self.ramp)
        db.session.add_all([self.night, self.ramp, self.shift, self.door, self.ballmat, self.discharge, self.other, self.empty_door, self.empty_ballmat, self.non_shift])
        db.session.commit()

    def tearDown(self):
        db.session.remove(); db.drop_all(); self.context.pop()

    def _person(self, employee_id="100001"):
        return staffing_service.create_person({"employee_id": employee_id, "first_name": "ada", "last_name": "SMITH", "seniority_date": "2020-01-01", "classification": "part_time", "employee_status": "active"})

    def _values(self, start=None, transition="", setup=None, final=None):
        return {"shift_flow_setup_work_area_id": str(setup.id) if setup else "", "shift_flow_sort_start_work_area_id": str((start or self.door).id), "shift_flow_ballmat_transition": transition, "shift_flow_final_door_work_area_id": str((final or self.door).id)}

    def test_area_classification_and_bounded_options(self):
        self.assertEqual(staffing_service.shift_work_area_type(self.door), "Door")
        self.assertEqual(staffing_service.shift_work_area_type(self.ballmat), "Ballmat")
        self.assertEqual(staffing_service.shift_work_area_type(self.discharge), "Discharge")
        self.assertEqual(staffing_service.shift_work_area_type(self.other), "Other")
        self.assertEqual(
            {area.id for area in staffing_service.shift_flow_area_options(self.door)},
            {self.door.id, self.empty_door.id, self.ballmat.id, self.empty_ballmat.id, self.discharge.id, self.other.id},
        )
        self.assertEqual(staffing_service.shift_flow_area_options(self.non_shift), [])

    def test_valid_door_ballmat_and_discharge_plans(self):
        for index, (start, transition) in enumerate(((self.door, ""), (self.ballmat, "2"), (self.discharge, "")), 1):
            person = self._person(f"10000{index}")
            plan = staffing_service.create_shift_flow_plan(person, self._values(start, transition, self.door, self.door), self.door)
            self.assertEqual(plan.ballmat_transition, int(transition) if transition else None)
        db.session.commit()
        self.assertEqual(StaffingShiftFlowPlan.query.count(), 3)

    def test_transition_and_area_validation_are_atomic(self):
        person = self._person()
        with self.assertRaisesRegex(ValueError, "Ballmat Transition"):
            staffing_service.create_shift_flow_plan(person, self._values(self.ballmat, ""), self.door)
        with self.assertRaisesRegex(ValueError, "Final Door"):
            staffing_service.create_shift_flow_plan(person, self._values(self.door, "", final=self.discharge), self.door)
        with self.assertRaisesRegex(ValueError, "Shift Work Area"):
            staffing_service.create_shift_flow_plan(person, self._values(self.door, "", setup=self.non_shift), self.door)
        db.session.rollback()
        self.assertEqual(StaffingPerson.query.count(), 0)
        self.assertEqual(StaffingShiftFlowPlan.query.count(), 0)

    def test_optional_plan_is_not_created_until_any_flow_value_is_submitted(self):
        person = self._person()
        self.assertIsNone(staffing_service.create_shift_flow_plan(person, {}, self.door))
        db.session.commit()
        self.assertEqual(StaffingPerson.query.count(), 1)
        self.assertEqual(StaffingShiftFlowPlan.query.count(), 0)

    def test_phase_projection_and_shorthand(self):
        person = self._person()
        plan = staffing_service.create_shift_flow_plan(
            person, self._values(self.ballmat, "2", self.door, self.door), self.door
        )
        self.assertEqual(staffing_service._shift_flow_phase_area(plan, "setup").id, self.door.id)
        self.assertEqual(staffing_service._shift_flow_phase_area(plan, "sort_start").id, self.ballmat.id)
        self.assertEqual(staffing_service._shift_flow_phase_area(plan, "after_w1").id, self.ballmat.id)
        self.assertEqual(staffing_service._shift_flow_phase_area(plan, "after_w2").id, self.door.id)
        self.assertEqual(staffing_service._shift_flow_phase_area(plan, "after_cleanup").id, self.door.id)
        self.assertEqual(staffing_service.shift_flow_shorthand(plan), "d BM2")

    def test_discharge_remains_through_later_phases_and_plan_can_update(self):
        person = self._person()
        plan = staffing_service.create_shift_flow_plan(person, self._values(self.discharge, "", final=self.door), self.door)
        for phase in ("after_w1", "after_w2", "after_cleanup"):
            self.assertEqual(staffing_service._shift_flow_phase_area(plan, phase).id, self.discharge.id)
        updated = staffing_service.save_shift_flow_plan(person, self._values(self.door, "", final=self.door), self.door)
        self.assertEqual(updated.sort_start_work_area.id, self.door.id)

    def test_workspace_markup_keeps_navigation_and_board_as_separate_desktop_surfaces(self):
        root = Path(__file__).resolve().parents[1]
        template = (root / "app/templates/neostaffing/shift_flow.html").read_text(encoding="utf-8")
        css = (root / "app/static/css/base.css").read_text(encoding="utf-8")
        self.assertIn('<main class="neostaffing-shift-flow-workspace">', template)
        self.assertIn('neostaffing-shift-flow-board-scroll', template)
        self.assertIn('--shift-flow-group-count', template)
        self.assertIn('grid-template-columns: clamp(144px, 10vw, 184px) minmax(0, 1fr)', css)
        self.assertIn('repeat(var(--shift-flow-group-count, 1), clamp(220px, 15vw, 300px))', css)
        self.assertIn('width: max-content', css)
        self.assertIn('grid-column: 1 / -1', css)
        self.assertIn('overflow: auto; overscroll-behavior: contain', css)
        self.assertIn('top: 142px', css)
        self.assertIn('data-shift-flow-final-board', template)
        self.assertIn('data-final-door-id', template)
        self.assertIn('draggable="true"', template)
        self.assertIn('neostaffing_shift_flow_drag.js', template)

    def test_phase_lane_backbone_is_complete_and_stably_ordered(self):
        expected = {
            "final_door": ["Door 1", "Door 2", "FLOW NOT SET"],
            "setup": ["NO SETUP", "Door 1", "Door 2", "Ballmat A", "Ballmat B", "FLOW NOT SET"],
            "sort_start": ["Door 1", "Door 2", "Ballmat A", "Ballmat B", "Discharge", "FLOW NOT SET"],
            "after_w1": ["Door 1", "Door 2", "Ballmat A", "Ballmat B", "Discharge", "FLOW NOT SET"],
            "after_w2": ["Door 1", "Door 2", "Ballmat A", "Ballmat B", "Discharge", "FLOW NOT SET"],
            "after_cleanup": ["Door 1", "Door 2", "Discharge", "FLOW NOT SET"],
        }
        areas = [self.door, self.empty_door, self.ballmat, self.empty_ballmat, self.discharge, self.other]
        for phase, names in expected.items():
            lanes = staffing_service.shift_flow_board_lanes(phase, areas)
            self.assertEqual([lane["area"].name for lane in lanes], names)
        self.assertNotIn("Other", expected["sort_start"])

    def test_context_projects_people_into_existing_lanes_without_creating_new_ones(self):
        person = self._person()
        plan = staffing_service.create_shift_flow_plan(
            person, self._values(self.ballmat, "1", final=self.empty_door), self.door
        )
        db.session.add(StaffingWorkAssignment(person=person, work_area=self.door, active=True))
        db.session.commit()
        context = staffing_service.shift_flow_context("after_w1")
        lanes = {lane["area"].name: lane for lane in context["groups"]}
        self.assertEqual([row["person"].id for row in lanes["Door 2"]["rows"]], [person.id])
        self.assertEqual(lanes["Door 1"]["rows"], [])
        self.assertEqual(lanes["Ballmat A"]["rows"], [])
        self.assertEqual(lanes["FLOW NOT SET"]["rows"], [])
        self.assertNotIn("Other", lanes)

    def test_unplanned_employee_uses_the_preexisting_flow_not_set_lane(self):
        person = self._person()
        db.session.add(StaffingWorkAssignment(person=person, work_area=self.door, active=True))
        db.session.commit()
        context = staffing_service.shift_flow_context("final_door")
        flow_lane = next(lane for lane in context["groups"] if lane["area"].name == "FLOW NOT SET")
        self.assertEqual([row["person"].id for row in flow_lane["rows"]], [person.id])
        self.assertEqual([lane["area"].name for lane in context["groups"]], ["Door 1", "Door 2", "FLOW NOT SET"])

    def test_final_door_move_changes_only_final_door_and_regroups_counts(self):
        person = self._person()
        plan = staffing_service.create_shift_flow_plan(
            person, self._values(self.ballmat, "2", self.door, self.door), self.door
        )
        db.session.add(StaffingWorkAssignment(person=person, work_area=self.door, active=True))
        db.session.commit()
        version = plan.updated_at.isoformat(timespec="microseconds")

        result = staffing_service.move_shift_flow_final_door(
            person, self.empty_door.id, self.door, version
        )
        db.session.commit()

        self.assertTrue(result["changed"])
        self.assertEqual(plan.final_door_work_area_id, self.empty_door.id)
        self.assertEqual(plan.setup_work_area_id, self.door.id)
        self.assertEqual(plan.sort_start_work_area_id, self.ballmat.id)
        self.assertEqual(plan.ballmat_transition, 2)
        lanes = {lane["area"].name: lane for lane in staffing_service.shift_flow_context("final_door")["groups"]}
        self.assertEqual(lanes["Door 1"]["rows"], [])
        self.assertEqual([row["person"].id for row in lanes["Door 2"]["rows"]], [person.id])

    def test_final_door_move_rejects_unplanned_invalid_same_and_stale_drops(self):
        unplanned = self._person("100002")
        with self.assertRaisesRegex(ValueError, "FLOW NOT SET"):
            staffing_service.move_shift_flow_final_door(unplanned, self.empty_door.id, self.door, "v")

        person = self._person("100003")
        plan = staffing_service.create_shift_flow_plan(person, self._values(self.door), self.door)
        db.session.commit()
        version = plan.updated_at.isoformat(timespec="microseconds")
        same = staffing_service.move_shift_flow_final_door(person, self.door.id, self.door, version)
        self.assertFalse(same["changed"])
        with self.assertRaisesRegex(ValueError, "Final Door"):
            staffing_service.move_shift_flow_final_door(person, self.ballmat.id, self.door, version)

        plan.final_door_work_area = self.empty_door
        db.session.commit()
        stale = staffing_service.move_shift_flow_final_door(person, self.door.id, self.door, version)
        self.assertEqual(stale["conflict"]["type"], "stale_version")

    def test_context_uses_bounded_collection_queries(self):
        statements = []

        def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        db.session.expire_all()
        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            staffing_service.shift_flow_context("setup")
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)
        self.assertLessEqual(len(statements), 2)
