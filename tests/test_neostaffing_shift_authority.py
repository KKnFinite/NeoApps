"""Canonical multi-sort assignment and complete-route contract."""
from datetime import date
import unittest

from sqlalchemy import text

from app import create_app
from app.extensions import db
from app.models import StaffingPerson, StaffingUnit, StaffingWorkAssignment, StaffingShiftFlowPlan
from app.services import neostaffing as staffing
from app.services.neostaffing_assignment_schema import sync_staffing_assignment_schema


class ShiftAuthorityTest(unittest.TestCase):
    def setUp(self):
        self.app = create_app(type("Config", (), {"TESTING": True, "SECRET_KEY": "test",
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", "SQLALCHEMY_TRACK_MODIFICATIONS": False}))
        self.context = self.app.app_context(); self.context.push(); db.create_all()
        self.night = StaffingUnit(unit_type="sort", name="Night")
        self.twilight = StaffingUnit(unit_type="sort", name="Twilight")
        self.ramp = StaffingUnit(unit_type="operation", name="Ramp", parent=self.night)
        self.shift = StaffingUnit(unit_type="department", name="Shift", parent=self.ramp)
        self.other = StaffingUnit(unit_type="work_area", name="Other", parent=self.twilight)
        self.outside = StaffingUnit(unit_type="work_area", name="Outside", parent=self.ramp)
        self.areas = {name: StaffingUnit(unit_type="work_area", name=name, parent=self.shift)
                      for name in ["East Ballmat", "West Ballmat", "Discharge"] +
                      [f"Door {n}" for n in (1,4,6,9,13,17,21,24,26,29,32,34)]}
        db.session.add_all([self.night, self.twilight, self.ramp, self.shift, self.other,
                            self.outside, *self.areas.values()]); db.session.commit()
        sync_staffing_assignment_schema(); db.session.commit()

    def tearDown(self):
        db.session.remove(); db.drop_all(); self.context.pop()

    def person(self, classification="part_time"):
        person = StaffingPerson(employee_id=str(StaffingPerson.query.count()+1), first_name="Test",
            last_name="Worker", classification=classification, seniority_date=date(2020,1,1), active=True)
        db.session.add(person); db.session.commit()
        return person

    def revision(self, person):
        home = staffing.assignment_service.shift_home(person)
        return staffing.shift_flow_revision(person, person.shift_flow_plan, home)

    def test_ft_sorts_and_non_ft_limit(self):
        person = self.person("full_time_combo")
        first = staffing.assign_work_area(person, self.areas["Door 6"])
        second = staffing.assign_work_area(person, self.other)
        db.session.commit()
        self.assertEqual(len(person.work_assignments), 2)
        staffing.assign_work_area(person, self.areas["Door 9"]); db.session.commit()
        self.assertEqual(first.work_area_unit_id, self.areas["Door 9"].id)
        self.assertEqual(second.work_area_unit_id, self.other.id)
        self.assertEqual(staffing.people_context({})["counts"]["total"], 1)
        self.assertEqual(staffing.dashboard_context({})["summary"]["total_employees"], 1)
        for sort in (self.night, self.twilight):
            self.assertEqual(staffing.people_context({"sort_id": str(sort.id)})["counts"]["total"], 1)
        pt = self.person()
        staffing.assign_work_area(pt, self.areas["Door 6"]); db.session.commit()
        db.session.add(StaffingWorkAssignment(person=pt, work_area=self.other, active=True))
        with self.assertRaisesRegex(ValueError, "Only FT Combo"):
            db.session.flush()
        db.session.rollback()

    def test_home_lifecycle_and_complete_route_conflict(self):
        person = self.person("full_time_combo")
        staffing.assign_work_area(person, self.other)
        staffing.assign_work_area(person, self.areas["Door 6"]); db.session.commit()
        self.assertIsNone(person.shift_flow_plan)
        old = self.revision(person)
        result = staffing.move_shift_flow_final_composite(person, self.areas["Door 24"].id,
            "bm2", self.areas["Door 6"], old)
        db.session.commit()
        self.assertTrue(result["changed"])
        self.assertEqual(staffing.assignment_service.shift_home(person).work_area.name, "West Ballmat")
        self.assertEqual(person.shift_flow_plan.sort_start_work_area.name, "West Ballmat")
        self.assertEqual(person.shift_flow_plan.final_door_work_area.name, "Door 24")
        self.assertIsNone(person.shift_flow_plan.setup_work_area)
        stale = staffing.move_shift_flow_final_composite(person, self.areas["Door 9"].id,
            "at_door", self.areas["West Ballmat"], old)
        self.assertIn("conflict", stale); db.session.rollback()
        staffing.assign_work_area(person, self.outside); db.session.commit()
        self.assertIsNone(StaffingShiftFlowPlan.query.filter_by(staffing_person_id=person.id).first())
        staffing.assign_work_area(person, self.areas["Door 6"]); db.session.commit()
        db.session.expire_all()
        self.assertIsNone(person.shift_flow_plan)
        self.assertEqual({a.work_area_unit_id for a in person.work_assignments if a.active}, {self.other.id, self.areas["Door 6"].id})

    def test_database_rejects_duplicate_same_sort_and_reparent_collision(self):
        person = self.person("full_time_combo")
        staffing.assign_work_area(person, self.areas["Door 6"]); db.session.commit()
        with self.assertRaises(Exception):
            db.session.execute(text("INSERT INTO staffing_work_assignments(person_id,work_area_unit_id,active,created_at,updated_at) VALUES (:p,:a,true,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"),
                               {"p": person.id, "a": self.areas["Door 9"].id})
        db.session.rollback()
        staffing.assign_work_area(person, self.other); db.session.commit()
        with self.assertRaises(Exception):
            db.session.execute(text("UPDATE staffing_units SET parent_id=:parent WHERE id=:area"),
                               {"parent": self.shift.id, "area": self.other.id})
        db.session.rollback()
        self.assertEqual(StaffingWorkAssignment.query.filter_by(person_id=person.id, active=True).count(), 2)

    def test_manual_home_custom_and_standard_replacement(self):
        person = self.person("domiciled_full_time_combo")
        staffing.assign_work_area(person, self.other)
        staffing.assign_work_area(person, self.areas["Door 6"]); db.session.commit()
        stale_home = self.revision(person)
        staffing.save_shift_flow_plan(person, {
            "expected_version": stale_home,
            "shift_flow_sort_start_work_area_id": self.areas["West Ballmat"].id,
            "shift_flow_setup_work_area_id": self.areas["Door 6"].id,
            "shift_flow_final_door_work_area_id": self.areas["Door 24"].id,
            "shift_flow_ballmat_transition": "2"}, self.areas["Door 6"])
        db.session.commit(); db.session.expire_all()
        home = staffing.assignment_service.shift_home(person)
        plan = person.shift_flow_plan
        self.assertEqual(home.work_area_unit_id, plan.sort_start_work_area_id)
        projection = staffing._shift_flow_map([{"person": person, "assignment": home, "plan": plan}], list(self.areas.values()))
        members = [r for phase in projection["phases"] for location in phase["locations"] for r in location["rows"]]
        self.assertTrue(all(r["custom"] for r in members))
        self.assertEqual(projection["count"], 1)  # Ghosts are projections, not employees.
        with self.assertRaisesRegex(ValueError, "changed while"):
            staffing.save_shift_flow_plan(person, {"expected_version": stale_home}, home.work_area)
        db.session.rollback()
        # A Home-only manual change preserves valid unsubmitted Setup/Final.
        staffing.save_shift_flow_plan(person, {"expected_version": self.revision(person),
            "shift_flow_sort_start_work_area_id": self.areas["East Ballmat"].id}, home.work_area)
        db.session.commit()
        self.assertEqual(plan.setup_work_area_id, self.areas["Door 6"].id)
        self.assertEqual(plan.final_door_work_area_id, self.areas["Door 24"].id)
        self.assertEqual(plan.ballmat_transition, 2)
        result = staffing.move_shift_flow_final_composite(person, self.areas["Door 24"].id,
            "bm1", self.areas["East Ballmat"], self.revision(person), complete_route=True)
        db.session.commit(); db.session.expire_all()
        self.assertTrue(result["changed"])
        self.assertIsNone(person.shift_flow_plan.setup_work_area_id)
        self.assertEqual(person.shift_flow_plan.ballmat_transition, 1)
        self.assertEqual(staffing.assignment_service.shift_home(person).work_area.name, "West Ballmat")
        self.assertIn(self.other.id, [a.work_area_unit_id for a in person.work_assignments])
        self.assertEqual(staffing._attendance_work_area_for_person(person).id, self.areas["West Ballmat"].id)

    def test_home_only_change_invalidates_board_and_bulk_lifecycle(self):
        person = self.person()
        staffing.assign_work_area(person, self.areas["Door 6"]); db.session.commit()
        old = self.revision(person)
        staffing.assign_work_area(person, self.areas["Door 9"]); db.session.commit()
        result = staffing.move_shift_flow_final_composite(person, self.areas["Door 24"].id,
            "bm1", self.areas["Door 9"], old, complete_route=True)
        self.assertIn("conflict", result); db.session.rollback()
        staffing.move_shift_flow_final_composite(person, self.areas["Door 24"].id,
            "bm1", self.areas["Door 9"], self.revision(person), complete_route=True)
        db.session.commit()
        staffing.bulk_update_work_area_assignments([person.id], "move", self.outside); db.session.commit()
        self.assertIsNone(StaffingShiftFlowPlan.query.filter_by(staffing_person_id=person.id).first())
        staffing.bulk_update_work_area_assignments([person.id], "move", self.areas["Door 6"]); db.session.commit()
        self.assertIsNone(StaffingShiftFlowPlan.query.filter_by(staffing_person_id=person.id).first())

    def test_physical_sides_and_database_classification_guard(self):
        configurations = staffing._shift_flow_map([], list(self.areas.values()))["configurations"]
        self.assertEqual({d.name for d in configurations["west"]["doors"]},
                         {f"Door {n}" for n in (21,24,26,29,32,34)})
        self.assertEqual({d.name for d in configurations["east"]["doors"]},
                         {f"Door {n}" for n in (1,4,6,9,13,17)})
        person = self.person("non_domiciled_full_time_combo")
        staffing.assign_work_area(person, self.areas["Door 6"])
        staffing.assign_work_area(person, self.other); db.session.commit()
        with self.assertRaises(Exception):
            db.session.execute(text("UPDATE staffing_people SET classification='part_time' WHERE id=:p"), {"p": person.id})
        db.session.rollback()
        self.assertEqual(person.classification, "non_domiciled_full_time_combo")

    def test_complete_setup_route_and_passive_projection(self):
        from sqlalchemy import event
        person = self.person()
        staffing.assign_work_area(person, self.areas["Door 6"]); db.session.commit()
        staffing.move_shift_flow_final_composite(person, self.areas["Door 24"].id,
            "bm3", self.areas["Door 6"], self.revision(person), complete_route=True, setup_mode="door")
        db.session.commit()
        self.assertEqual(person.shift_flow_plan.setup_work_area_id, self.areas["Door 24"].id)
        statements = []
        def capture(_c, _cursor, sql, *_args):
            statements.append(sql.lstrip().lower())
        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            context = staffing.shift_flow_context()
            db.session.flush()
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)
        self.assertEqual(context["flow_map"]["count"], 1)
        self.assertFalse(context["rows"][0]["custom"])
        self.assertFalse(any(sql.startswith(("insert", "update", "delete")) for sql in statements))
        self.assertEqual(sum(sql.startswith("select") for sql in statements), 2)

    def test_partial_manual_flow_is_preserved_as_custom_not_fabricated(self):
        person = self.person()
        staffing.assign_work_area(person, self.areas['West Ballmat']); db.session.commit()
        staffing.save_shift_flow_plan(person, {'expected_version':self.revision(person),
            'shift_flow_setup_work_area_id':self.areas['Door 6'].id}, self.areas['West Ballmat'])
        db.session.commit()
        row = staffing.shift_flow_context()['rows'][0]
        self.assertTrue(row['custom'])
        self.assertIsNone(row['plan'].final_door_work_area_id)
        self.assertIsNone(row['plan'].ballmat_transition)

    def test_flow_map_presentation_keeps_separate_mobile_and_desktop_contracts(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        css = (root/'app/static/css/neostaffing_shift_map.css').read_text()
        template = (root/'app/templates/neostaffing/_shift_flow_map.html').read_text()
        self.assertIn('@media(max-width:900px)', css)
        self.assertIn('grid-template-columns:repeat(7,minmax(0,1fr))', css)
        self.assertIn('grid-template-columns:repeat(2,minmax(0,1fr))', css)
        self.assertIn('min-height:44px', css)
        self.assertIn("phase.key == 'sort_start' and can_edit_shift_flow", template)
        self.assertIn("phase.key != 'sort_start'", template)
        self.assertIn('data-route-picker', template)
        self.assertIn('data-flow-lines', template)

    def test_bootstrap_preserves_ids_and_reconciles_legacy_home_idempotently(self):
        person = self.person()
        assignment = staffing.assign_work_area(person, self.areas["Door 6"])
        staffing.move_shift_flow_final_composite(person, self.areas["Door 9"].id,
            "at_door", self.areas["Door 6"], self.revision(person), complete_route=True)
        db.session.commit()
        assignment_id, plan_id = assignment.id, person.shift_flow_plan.id
        # Simulate the old independent Sort Start using raw SQL before bootstrap.
        if db.engine.dialect.name == "postgresql":
            db.session.execute(text("DROP TRIGGER staffing_flow_home_validate ON staffing_shift_flow_plans"))
            db.session.execute(text("ALTER TABLE staffing_work_assignments ADD CONSTRAINT uq_staffing_work_assignments_person UNIQUE(person_id)"))
            db.session.execute(text("ALTER TABLE staffing_shift_flow_plans ALTER COLUMN final_door_work_area_id SET NOT NULL"))
        db.session.execute(text("UPDATE staffing_shift_flow_plans SET sort_start_work_area_id=:a WHERE id=:p"),
                           {"a": self.areas["Door 24"].id, "p": plan_id})
        db.session.commit()
        sync_staffing_assignment_schema(); db.session.commit()
        sync_staffing_assignment_schema(); db.session.commit(); db.session.expire_all()
        self.assertEqual(person.shift_flow_plan.id, plan_id)
        self.assertEqual(person.shift_flow_plan.sort_start_work_area_id, assignment.work_area_unit_id)
        self.assertEqual(person.shift_flow_plan.final_door_work_area_id, self.areas["Door 9"].id)
        self.assertEqual(person.work_assignments[0].id, assignment_id)
        self.assertEqual(StaffingWorkAssignment.query.count(), 1)

    def test_sqlite_legacy_table_rebuild_preserves_populated_assignments(self):
        if db.engine.dialect.name != "sqlite":
            self.skipTest("PostgreSQL uses ALTER; covered by bootstrap test")
        from sqlalchemy import MetaData, UniqueConstraint, inspect
        person = self.person()
        staffing.assign_work_area(person, self.areas["Door 6"])
        staffing.move_shift_flow_final_composite(person, self.areas["Door 9"].id,
            "at_door", self.areas["Door 6"], self.revision(person), complete_route=True)
        db.session.commit()
        # Recreate only these synthetic tables in their production legacy shape.
        connection = db.session.connection()
        metadata = MetaData()
        for table in db.metadata.sorted_tables:
            table.to_metadata(metadata)
        metadata.tables['staffing_work_assignments'].append_constraint(
            UniqueConstraint('person_id', name='uq_staffing_work_assignments_person'))
        metadata.tables['staffing_shift_flow_plans'].c.final_door_work_area_id.nullable = False
        snapshots = {}
        for model in (StaffingWorkAssignment, StaffingShiftFlowPlan):
            table = model.__table__
            snapshots[table.name] = [dict(row) for row in connection.execute(table.select()).mappings()]
            table.drop(connection)
            metadata.tables[table.name].create(connection)
            connection.execute(metadata.tables[table.name].insert(), snapshots[table.name])
        db.session.commit()
        sync_staffing_assignment_schema(); db.session.commit()
        sync_staffing_assignment_schema(); db.session.commit()
        for model in (StaffingWorkAssignment, StaffingShiftFlowPlan):
            rows = [dict(row) for row in db.session.execute(model.__table__.select()).mappings()]
            self.assertEqual(rows, snapshots[model.__tablename__])
        self.assertFalse(inspect(db.engine).get_unique_constraints('staffing_work_assignments'))
        self.assertEqual(db.session.execute(text('PRAGMA foreign_key_check')).all(), [])
