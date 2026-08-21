from datetime import date
import unittest

from app import create_app
from app.extensions import db
from app.models import StaffingPerson, StaffingShiftFlowPlan, StaffingUnit
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
        self.non_shift = StaffingUnit(unit_type="work_area", name="Door Outside", parent=self.ramp)
        db.session.add_all([self.night, self.ramp, self.shift, self.door, self.ballmat, self.discharge, self.other, self.non_shift])
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
        self.assertEqual({area.id for area in staffing_service.shift_flow_area_options(self.door)}, {self.door.id, self.ballmat.id, self.discharge.id, self.other.id})
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
