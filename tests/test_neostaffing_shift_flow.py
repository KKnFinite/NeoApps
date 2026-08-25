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

    def _configure_final_composite(self):
        by_name = {area.name: area for area in [self.door, self.empty_door, self.ballmat, self.empty_ballmat, self.discharge, self.other]}
        for name in ("Door 34", "Door 32", "Door 29", "Door 26", "Door 24", "Door 21", "Door 17", "Door 13", "Door 9", "Door 6", "Door 4"):
            area = StaffingUnit(unit_type="work_area", name=name, parent=self.shift)
            db.session.add(area)
            by_name[name] = area
        east_ballmat = StaffingUnit(unit_type="work_area", name="East Ballmat", parent=self.shift)
        west_ballmat = StaffingUnit(unit_type="work_area", name="West Ballmat", parent=self.shift)
        db.session.add_all([east_ballmat, west_ballmat])
        db.session.commit()
        by_name["East Ballmat"] = east_ballmat
        by_name["West Ballmat"] = west_ballmat
        return by_name

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
        self.assertIn('data-shift-flow-drag-board', template)
        self.assertIn('data-shift-flow-destination-id', template)
        self.assertIn('draggable="true"', template)
        self.assertIn('neostaffing_shift_flow_drag.js', template)
        self.assertEqual(
            staffing_service.SHIFT_FLOW_PHASES,
            (("setup", "SETUP"), ("sort_start", "SORT START"),
             ("after_w1", "1ST WAVE"), ("after_w2", "2ND WAVE"),
             ("final_door", "FINAL DOOR")),
        )
        self.assertNotIn("AFTER CLEANUP", template)

    def test_phase_lane_backbone_is_complete_and_stably_ordered(self):
        expected = {
            "final_door": ["Door 1", "Door 2", "FLOW NOT SET"],
            "setup": ["NO SETUP", "Door 1", "Door 2", "Ballmat A", "Ballmat B", "FLOW NOT SET"],
            "sort_start": ["Door 1", "Door 2", "Ballmat A", "Ballmat B", "Discharge", "FLOW NOT SET"],
            "after_w1": ["Door 1", "Door 2", "Ballmat A", "Ballmat B", "Discharge", "FLOW NOT SET"],
            "after_w2": ["Door 1", "Door 2", "Ballmat A", "Ballmat B", "Discharge", "FLOW NOT SET"],
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

    def test_setup_drag_moves_to_ballmat_and_no_setup_without_touching_other_fields(self):
        person = self._person("100004")
        plan = staffing_service.create_shift_flow_plan(
            person, self._values(self.ballmat, "2", self.door, self.empty_door), self.door
        )
        db.session.commit()
        version = plan.updated_at.isoformat(timespec="microseconds")
        moved = staffing_service.move_shift_flow_phase_lane(
            person, "setup", self.empty_ballmat.id, self.door, version
        )
        db.session.commit()
        self.assertTrue(moved["changed"])
        self.assertEqual(plan.setup_work_area_id, self.empty_ballmat.id)
        self.assertEqual(plan.sort_start_work_area_id, self.ballmat.id)
        self.assertEqual(plan.ballmat_transition, 2)
        self.assertEqual(plan.final_door_work_area_id, self.empty_door.id)

        version = plan.updated_at.isoformat(timespec="microseconds")
        cleared = staffing_service.move_shift_flow_phase_lane(
            person, "setup", "NO SETUP", self.door, version
        )
        self.assertTrue(cleared["changed"])
        self.assertIsNone(plan.setup_work_area_id)
        with self.assertRaisesRegex(ValueError, "Setup Assignment"):
            staffing_service.move_shift_flow_phase_lane(person, "setup", self.discharge.id, self.door, cleared["version"])

    def test_sort_start_drag_requires_preserves_and_clears_ballmat_transition(self):
        person = self._person("100005")
        plan = staffing_service.create_shift_flow_plan(
            person, self._values(self.ballmat, "2", self.door, self.door), self.door
        )
        db.session.commit()
        version = plan.updated_at.isoformat(timespec="microseconds")
        preserved = staffing_service.move_shift_flow_phase_lane(
            person, "sort_start", self.empty_ballmat.id, self.door, version
        )
        db.session.commit()
        self.assertTrue(preserved["changed"])
        self.assertEqual(plan.ballmat_transition, 2)

        version = plan.updated_at.isoformat(timespec="microseconds")
        cleared = staffing_service.move_shift_flow_phase_lane(
            person, "sort_start", self.discharge.id, self.door, version
        )
        db.session.commit()
        self.assertIsNone(plan.ballmat_transition)
        self.assertEqual(plan.sort_start_work_area_id, self.discharge.id)

        version = plan.updated_at.isoformat(timespec="microseconds")
        with self.assertRaisesRegex(ValueError, "Ballmat Transition"):
            staffing_service.move_shift_flow_phase_lane(person, "sort_start", self.ballmat.id, self.door, version)
        assigned = staffing_service.move_shift_flow_phase_lane(
            person, "sort_start", self.ballmat.id, self.door, version, "3"
        )
        self.assertEqual(plan.ballmat_transition, 3)
        self.assertTrue(assigned["changed"])

    def test_final_composite_uses_only_configured_east_west_doors_and_places_flow_bands(self):
        areas = self._configure_final_composite()
        specs = (
            ("100006", None, None, areas["Door 34"], ""),
            ("100007", areas["Door 34"], areas["East Ballmat"], areas["Door 32"], "1"),
            ("100008", areas["Door 34"], areas["East Ballmat"], areas["Door 29"], "2"),
            ("100009", None, self.discharge, areas["Door 26"], ""),
            ("100010", areas["Door 34"], areas["East Ballmat"], areas["Door 24"], "3"),
        )
        people = []
        for employee_id, setup, start, final, transition in specs:
            person = self._person(employee_id)
            staffing_service.create_shift_flow_plan(person, self._values(start or final, transition, setup, final), self.door)
            db.session.add(StaffingWorkAssignment(person=person, work_area=self.door, active=True))
            people.append(person)
        db.session.commit()
        board = staffing_service.shift_flow_context("final_door", "east")["final_composite"]
        self.assertEqual([door.name for door in board["doors"]], ["Door 34", "Door 32", "Door 29", "Door 26", "Door 24", "Door 21"])
        west = staffing_service.shift_flow_context("final_door", "west")["final_composite"]
        self.assertEqual([door.name for door in west["doors"]], ["Door 17", "Door 13", "Door 9", "Door 6", "Door 4", "Door 1"])
        self.assertEqual(board["bands"], (("at_door", "AT DOOR"), ("bm1", "BM1"), ("bm2", "BM2"), ("discharge", "DISCHARGE"), ("bm3", "BM3")))
        self.assertEqual(board["ballmat"].name, "East Ballmat")
        self.assertEqual(west["ballmat"].name, "West Ballmat")
        self.assertNotIn("Door 2", [door.name for door in board["doors"]])
        self.assertEqual(len(board["columns"]), 6)
        cells = {(column["door"].name, band["key"]): band for column in board["columns"] for band in column["bands"]}
        self.assertEqual([row["person"].id for row in cells[("Door 34", "at_door")]["rows"]], [people[0].id])
        self.assertEqual([row["person"].id for row in cells[("Door 32", "bm1")]["rows"]], [people[1].id])
        self.assertEqual([row["person"].id for row in cells[("Door 29", "bm2")]["rows"]], [people[2].id])
        self.assertEqual([row["person"].id for row in cells[("Door 26", "discharge")]["rows"]], [people[3].id])
        self.assertEqual([row["person"].id for row in cells[("Door 24", "bm3")]["rows"]], [people[4].id])
        self.assertEqual(cells[("Door 32", "bm1")]["rows"][0]["setup_assignment"], "SET D34")
        self.assertEqual(cells[("Door 34", "at_door")]["rows"][0]["setup_assignment"], "NO SETUP")
        self.assertNotIn("sections", cells[("Door 34", "at_door")])

    def test_final_composite_derives_aligned_dynamic_rows_with_one_empty_trailer(self):
        areas = self._configure_final_composite()
        same_door_people = []
        for employee_id in ("100030", "100031", "100032"):
            person = self._person(employee_id)
            staffing_service.create_shift_flow_plan(
                person,
                self._values(
                    areas["East Ballmat"],
                    "1",
                    areas["Door 24"],
                    areas["Door 34"],
                ),
                self.door,
            )
            db.session.add(
                StaffingWorkAssignment(person=person, work_area=self.door, active=True)
            )
            same_door_people.append(person)
        other_door = self._person("100033")
        staffing_service.create_shift_flow_plan(
            other_door,
            self._values(
                areas["East Ballmat"],
                "1",
                None,
                areas["Door 32"],
            ),
            self.door,
        )
        db.session.add(
            StaffingWorkAssignment(person=other_door, work_area=self.door, active=True)
        )
        db.session.commit()

        board = staffing_service.shift_flow_context("final_door", "east")["final_composite"]
        band = next(item for item in board["display_bands"] if item["key"] == "bm1")

        self.assertEqual(band["occupied_row_count"], 3)
        self.assertEqual(len(band["rows"]), 4)
        self.assertTrue(band["rows"][-1]["trailing"])
        self.assertTrue(all(cell["row"] is None for cell in band["rows"][-1]["cells"]))
        self.assertTrue(all(len(display_row["cells"]) == 6 for display_row in band["rows"]))

        door_34_index = next(
            index
            for index, cell in enumerate(band["rows"][0]["cells"])
            if cell["door"].name == "Door 34"
        )
        door_32_index = next(
            index
            for index, cell in enumerate(band["rows"][0]["cells"])
            if cell["door"].name == "Door 32"
        )
        self.assertEqual(
            {
                band["rows"][index]["cells"][door_34_index]["row"]["person"].id
                for index in range(3)
            },
            {person.id for person in same_door_people},
        )
        self.assertEqual(
            band["rows"][0]["cells"][door_32_index]["row"]["person"].id,
            other_door.id,
        )
        self.assertIsNone(band["rows"][1]["cells"][door_32_index]["row"])

        empty_band = next(
            item for item in board["display_bands"] if item["key"] == "bm2"
        )
        self.assertEqual(empty_band["occupied_row_count"], 0)
        self.assertEqual(len(empty_band["rows"]), 1)
        self.assertTrue(empty_band["rows"][0]["trailing"])

    def test_final_composite_persistently_accounts_for_attention_and_opposite_side_people(self):
        areas = self._configure_final_composite()
        east_person = self._person("100012")
        west_person = self._person("100013")
        unplanned = self._person("100014")
        invalid = self._person("100015")
        staffing_service.create_shift_flow_plan(
            east_person, self._values(areas["Door 34"], "", final=areas["Door 34"]), self.door
        )
        staffing_service.create_shift_flow_plan(
            west_person, self._values(areas["Door 17"], "", final=areas["Door 17"]), self.door
        )
        # This legacy-shaped plan references a real Shift door outside the fixed East/West map.
        db.session.add(
            StaffingShiftFlowPlan(
                person=invalid,
                sort_start_work_area=self.other,
                final_door_work_area=self.empty_door,
            )
        )
        db.session.add_all(
            [
                StaffingWorkAssignment(person=person, work_area=self.door, active=True)
                for person in (east_person, west_person, unplanned, invalid)
            ]
        )
        db.session.commit()

        board = staffing_service.shift_flow_context("final_door", "east")["final_composite"]
        east_ids = {
            row["person"].id
            for column in board["columns"]
            for band in column["bands"]
            for row in band["rows"]
        }
        attention = {row["person"].id: row["attention_reason"] for row in board["needs_attention"]}
        self.assertEqual(east_ids, {east_person.id})
        self.assertNotIn(west_person.id, attention)
        self.assertEqual(board["opposite_side_count"], 1)
        self.assertEqual(attention[unplanned.id], "FLOW NOT SET — plan required.")
        self.assertEqual(attention[invalid.id], "Final Door is not a configured East/West final door.")
        self.assertEqual(board["active_shift_count"], 4)
        self.assertEqual(board["placed_count"], 1)
        self.assertEqual(board["accounted_count"], 4)

    def test_final_composite_attention_handles_invalid_ballmat_transition(self):
        areas = self._configure_final_composite()
        person = self._person("100016")
        plan = StaffingShiftFlowPlan(
            person=person,
            sort_start_work_area=areas["East Ballmat"],
            final_door_work_area=areas["Door 34"],
            ballmat_transition=None,
        )
        db.session.add_all([plan, StaffingWorkAssignment(person=person, work_area=self.door, active=True)])
        db.session.commit()
        board = staffing_service.shift_flow_context("final_door", "east")["final_composite"]
        self.assertEqual(
            board["needs_attention"][0]["attention_reason"],
            "Ballmat Transition must be 1, 2, or 3.",
        )

    def test_exact_setup_assignment_labels_use_real_setup_area(self):
        areas = self._configure_final_composite()
        person = self._person("100026")
        plan = staffing_service.create_shift_flow_plan(
            person, self._values(areas["Door 34"], "", areas["East Ballmat"], areas["Door 34"]), self.door
        )
        db.session.flush()
        self.assertEqual(staffing_service.shift_flow_setup_assignment_label(plan), "SET EBM")
        plan.setup_work_area = areas["West Ballmat"]
        db.session.flush()
        self.assertEqual(staffing_service.shift_flow_setup_assignment_label(plan), "SET WBM")
        plan.setup_work_area = areas["Door 24"]
        db.session.flush()
        self.assertEqual(staffing_service.shift_flow_setup_assignment_label(plan), "SET D24")
        plan.setup_work_area = None
        db.session.flush()
        self.assertEqual(staffing_service.shift_flow_setup_assignment_label(plan), "NO SETUP")

    def test_final_composite_markup_keeps_persistent_attention_column_and_reasons(self):
        template = (Path(__file__).resolve().parents[1] / "app/templates/neostaffing/shift_flow.html").read_text(encoding="utf-8")
        self.assertIn("UNASSIGNED / NEEDS ATTENTION", template)
        self.assertIn("row.attention_reason", template)
        self.assertIn("row.setup_assignment", template)
        self.assertIn("data-shift-flow-needs-attention", template)
        self.assertIn("data-shift-flow-composite-cell", template)
        self.assertIn("composite.opposite_side_count", template)
        self.assertNotIn("composite.unplaced", template)
        self.assertNotIn("data-setup-section", template)
        self.assertNotIn("data-shift-flow-setup-section", template)
        self.assertNotIn("cell.sections", template)
        self.assertNotIn("NON-SETUP", template)

    def test_final_composite_dynamic_row_markup_and_committed_reflow_contract(self):
        root = Path(__file__).resolve().parents[1]
        template = (root / "app/templates/neostaffing/shift_flow.html").read_text(encoding="utf-8")
        javascript = (root / "app/static/js/neostaffing_shift_flow_drag.js").read_text(encoding="utf-8")
        css = (root / "app/static/css/base.css").read_text(encoding="utf-8")

        self.assertIn("composite.display_bands", template)
        self.assertIn("data-shift-flow-display-row", template)
        self.assertIn("data-shift-flow-empty-row", template)
        self.assertIn("data-shift-flow-composite-band", template)
        self.assertIn("normalizeBandRows", javascript)
        self.assertIn("rowIndex <= occupiedRowCount", javascript)
        self.assertIn("container.replaceChildren(fragment)", javascript)
        self.assertIn("normalizeBands(sourceBand, targetBand)", javascript)
        self.assertIn("[data-shift-flow-empty-row]", css)

        commit_check = javascript.index("if (!response.ok || !payload.ok)")
        dom_move = javascript.index('targetCell.querySelector("ul")?.append(row)')
        row_reflow = javascript.index("normalizeBands(sourceBand, targetBand)")
        self.assertLess(commit_check, dom_move)
        self.assertLess(dom_move, row_reflow)

    def test_wave_drag_moves_use_side_ballmat_and_preserve_setup(self):
        areas = self._configure_final_composite()
        cases = (
            ("100017", "after_w1", areas["East Ballmat"], "2", areas["Door 32"], areas["Door 34"], 1, areas["East Ballmat"]),
            ("100018", "after_w1", areas["Door 34"], "", areas["Door 34"], areas["West Ballmat"], 2, areas["West Ballmat"]),
            ("100019", "after_w2", areas["West Ballmat"], "3", areas["Door 9"], areas["Door 13"], 2, areas["West Ballmat"]),
            ("100020", "after_w2", areas["Door 34"], "", areas["Door 34"], areas["East Ballmat"], 3, areas["East Ballmat"]),
        )
        for employee_id, phase, start, transition, final, destination, expected_transition, expected_start in cases:
            person = self._person(employee_id)
            plan = staffing_service.create_shift_flow_plan(
                person, self._values(start, transition, self.ballmat, final), self.door
            )
            db.session.commit()
            version = plan.updated_at.isoformat(timespec="microseconds")
            result = staffing_service.move_shift_flow_phase_lane(
                person, phase, destination.id, self.door, version
            )
            db.session.commit()
            self.assertTrue(result["changed"])
            self.assertEqual(plan.sort_start_work_area_id, expected_start.id)
            self.assertEqual(plan.ballmat_transition, expected_transition)
            self.assertEqual(plan.setup_work_area_id, self.ballmat.id)
            if staffing_service.shift_work_area_type(destination) == "Door":
                self.assertEqual(plan.final_door_work_area_id, destination.id)
            else:
                self.assertEqual(plan.final_door_work_area_id, final.id)

    def test_wave_drag_rejects_door_to_door_discharge_and_stale_moves(self):
        areas = self._configure_final_composite()
        door_person = self._person("100021")
        door_plan = staffing_service.create_shift_flow_plan(
            door_person, self._values(areas["Door 34"], "", final=areas["Door 34"]), self.door
        )
        discharge_person = self._person("100022")
        discharge_plan = staffing_service.create_shift_flow_plan(
            discharge_person, self._values(self.discharge, "", final=areas["Door 34"]), self.door
        )
        db.session.commit()
        version = door_plan.updated_at.isoformat(timespec="microseconds")
        with self.assertRaisesRegex(ValueError, "Door-to-Door"):
            staffing_service.move_shift_flow_phase_lane(
                door_person, "after_w1", areas["Door 32"].id, self.door, version
            )
        with self.assertRaisesRegex(ValueError, "Discharge"):
            staffing_service.move_shift_flow_phase_lane(
                discharge_person, "after_w2", areas["Door 34"].id, self.door,
                discharge_plan.updated_at.isoformat(timespec="microseconds"),
            )
        door_plan.setup_work_area = self.ballmat
        db.session.commit()
        stale = staffing_service.move_shift_flow_phase_lane(
            door_person, "after_w1", areas["East Ballmat"].id, self.door, version
        )
        self.assertEqual(stale["conflict"]["type"], "stale_version")

    def test_final_composite_drag_updates_derived_fields_and_preserves_setup_atomically(self):
        areas = self._configure_final_composite()
        person = self._person("100011")
        plan = staffing_service.create_shift_flow_plan(
            person, self._values(self.door, "", self.ballmat, self.door), self.door
        )
        db.session.commit()
        version = plan.updated_at.isoformat(timespec="microseconds")
        east = staffing_service.move_shift_flow_final_composite(
            person, areas["Door 24"].id, "bm2", self.door, version
        )
        db.session.commit()
        self.assertTrue(east["changed"])
        self.assertEqual(plan.final_door_work_area_id, areas["Door 24"].id)
        self.assertEqual(plan.sort_start_work_area_id, areas["East Ballmat"].id)
        self.assertEqual(plan.ballmat_transition, 2)
        self.assertEqual(plan.setup_work_area_id, self.ballmat.id)

        west = staffing_service.move_shift_flow_final_composite(
            person, areas["Door 9"].id, "bm1", self.door, east["version"]
        )
        self.assertEqual(plan.final_door_work_area_id, areas["Door 9"].id)
        self.assertEqual(plan.sort_start_work_area_id, areas["West Ballmat"].id)
        self.assertEqual(plan.ballmat_transition, 1)
        self.assertEqual(plan.setup_work_area_id, self.ballmat.id)
        with self.assertRaisesRegex(ValueError, "configured East or West doors"):
            staffing_service.move_shift_flow_final_composite(
                person, self.empty_door.id, "at_door", self.door, west["version"]
            )

    def test_attention_drop_creates_missing_plan_and_repairs_invalid_plan(self):
        areas = self._configure_final_composite()
        unplanned = self._person("100023")
        db.session.add(StaffingWorkAssignment(person=unplanned, work_area=self.door, active=True))
        db.session.commit()

        created = staffing_service.move_shift_flow_final_composite(
            unplanned, areas["Door 24"].id, "bm1", self.door, ""
        )
        db.session.commit()
        created_plan = unplanned.shift_flow_plan
        self.assertTrue(created["created"])
        self.assertEqual(created_plan.final_door_work_area_id, areas["Door 24"].id)
        self.assertEqual(created_plan.sort_start_work_area_id, areas["East Ballmat"].id)
        self.assertEqual(created_plan.ballmat_transition, 1)
        self.assertIsNone(created_plan.setup_work_area_id)

        invalid = self._person("100024")
        invalid_plan = StaffingShiftFlowPlan(
            person=invalid,
            setup_work_area=self.non_shift,
            sort_start_work_area=self.other,
            final_door_work_area=self.empty_door,
        )
        db.session.add_all([
            invalid_plan,
            StaffingWorkAssignment(person=invalid, work_area=self.door, active=True),
        ])
        db.session.commit()
        repaired = staffing_service.move_shift_flow_final_composite(
            invalid, areas["Door 9"].id, "discharge", self.door,
            invalid_plan.updated_at.isoformat(timespec="microseconds"),
        )
        db.session.commit()
        self.assertTrue(repaired["changed"])
        self.assertFalse(repaired["created"])
        self.assertEqual(invalid_plan.final_door_work_area_id, areas["Door 9"].id)
        self.assertEqual(invalid_plan.sort_start_work_area_id, self.discharge.id)
        self.assertIsNone(invalid_plan.ballmat_transition)
        self.assertIsNone(invalid_plan.setup_work_area_id)

        board = staffing_service.shift_flow_context("final_door", "west")["final_composite"]
        attention_ids = {row["person"].id for row in board["needs_attention"]}
        placed_ids = {
            row["person"].id
            for column in board["columns"]
            for band in column["bands"]
            for row in band["rows"]
        }
        self.assertNotIn(invalid.id, attention_ids)
        self.assertIn(invalid.id, placed_ids)
        self.assertEqual(board["active_shift_count"], 2)
        self.assertEqual(board["accounted_count"], 2)

    def test_composite_stale_drop_changes_nothing(self):
        areas = self._configure_final_composite()
        person = self._person("100025")
        plan = staffing_service.create_shift_flow_plan(
            person, self._values(areas["Door 34"], "", self.ballmat, areas["Door 34"]), self.door
        )
        db.session.commit()
        stale_version = plan.updated_at.isoformat(timespec="microseconds")
        plan.sort_start_work_area = areas["East Ballmat"]
        plan.ballmat_transition = 3
        db.session.commit()

        conflict = staffing_service.move_shift_flow_final_composite(
            person, areas["Door 24"].id, "bm1", self.door, stale_version
        )
        self.assertEqual(conflict["conflict"]["type"], "stale_version")
        self.assertEqual(plan.final_door_work_area_id, areas["Door 34"].id)
        self.assertEqual(plan.sort_start_work_area_id, areas["East Ballmat"].id)
        self.assertEqual(plan.ballmat_transition, 3)
        self.assertEqual(plan.setup_work_area_id, self.ballmat.id)

    def test_context_uses_bounded_collection_queries(self):
        statements = []

        def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        db.session.expire_all()
        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            staffing_service.shift_flow_context("final_door")
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)
        self.assertLessEqual(len(statements), 2)
