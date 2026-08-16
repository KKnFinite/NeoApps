from datetime import date, datetime
import re
import unittest

from flask import g
from sqlalchemy import event

from app import create_app
from app.extensions import db
from app.models import (
    PermissionRule,
    PortalAppAccess,
    StaffingChangeRequest,
    StaffingChangeRequestItem,
    StaffingLeadershipAssignment,
    StaffingPerson,
    StaffingReportingRelationship,
    StaffingUnit,
    StaffingWorkAssignment,
    User,
)
from app.services import neostaffing_bulk_change as bulk_service
from app.services import neostaffing_change_requests as request_service
from app.services.password_policy import set_user_password
from app.services.permission_rules import ensure_default_permission_rules


class NeoStaffingBulkChangeTest(unittest.TestCase):
    def setUp(self):
        config = type(
            "NeoStaffingBulkChangeConfig",
            (),
            {
                "SECRET_KEY": "bulk-change-test",
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

        self.sort = self._unit("sort", "Night")
        self.operation_one = self._unit("operation", "East Operation", self.sort)
        self.operation_two = self._unit("operation", "West Operation", self.sort)
        self.department_one = self._unit("department", "East Department", self.operation_one)
        self.department_two = self._unit("department", "West Department", self.operation_two)
        self.area_one = self._unit("work_area", "East Work Area", self.department_one)
        self.area_two = self._unit("work_area", "West Work Area", self.department_two)

        self.division_manager = self._person("BC-DM", "division_manager", "Dana", "Division")
        self.manager_one = self._person("BC-M1", "manager", "Morgan", "East")
        self.manager_two = self._person("BC-M2", "manager", "Morgan", "West")
        self.ft_one = self._person("BC-FT1", "full_time_supervisor", "Frank", "East")
        self.ft_two = self._person("BC-FT2", "full_time_supervisor", "Frank", "West")
        self.pt_operator = self._person("BC-PT1", "part_time_supervisor", "Pat", "Operator")
        self.pt_simulator = self._person("BC-PT2", "part_time_supervisor", "Pat", "Simulator")
        self.employee_one = self._person("BC-E1", "part_time", "Employee", "One")
        self.employee_two = self._person("BC-E2", "full_time_combo", "Employee", "Two")

        self._lead(self.division_manager, self.sort)
        self._lead(self.manager_one, self.operation_one)
        self._lead(self.manager_two, self.operation_two)
        self._lead(self.ft_one, self.department_one)
        self._lead(self.ft_two, self.department_two)
        self._lead(self.pt_operator, self.area_one)
        self._lead(self.pt_simulator, self.area_one)
        self._work(self.employee_one, self.area_one)
        self._work(self.employee_two, self.area_two)

        self._reports(self.manager_one, self.division_manager)
        self._reports(self.manager_two, self.division_manager)
        self._reports(self.ft_one, self.manager_one)
        self._reports(self.ft_two, self.manager_two)
        self._reports(self.pt_operator, self.ft_one)
        self._reports(self.pt_simulator, self.ft_one)

        self.pt_operator_user = self._user("bulk_pt_operator", "operator", self.pt_operator)
        self.pt_simulator_user = self._user("bulk_pt_simulator", "simulator", self.pt_simulator)
        self.ft_editor_user = self._user("bulk_ft_editor", "master", self.ft_one)
        self.dm_editor_user = self._user("bulk_dm_editor", "master", self.division_manager)
        self.grandmaster_user = self._user("bulk_gm", "grandmaster")
        self.watcher_user = self._user("bulk_watcher", "watcher")
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_workspace_page_is_read_only_and_permission_threshold_is_dynamic(self):
        self._login(self.ft_editor_user)
        statements = []

        def capture(_connection, _cursor, statement, _parameters, _context, _many):
            statements.append(statement.lstrip().upper())

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            response = self.client.get("/neostaffing/bulk-change")
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"SESSION WORKSPACE", response.data)
        self.assertFalse(any(row.startswith(("INSERT", "UPDATE", "DELETE")) for row in statements))

        token = re.search(
            rb'name="workspace_token" value="([^"]+)"',
            response.data,
        ).group(1).decode()
        staged = self.client.post(
            "/neostaffing/bulk-change",
            data={
                "workspace_token": token,
                "action": "stage_person",
                "person_id": str(self.employee_one.id),
                "change_first_name": "1",
                "first_name": "Route Staged",
            },
        )
        self.assertEqual(staged.status_code, 200)
        self.assertIn(b"Route Staged", staged.data)
        self.assertEqual(db.session.get(StaffingPerson, self.employee_one.id).first_name, "Employee")

        self._login(self.pt_operator_user)
        self.assertEqual(self.client.get("/neostaffing/bulk-change").status_code, 200)
        rule = PermissionRule.query.filter_by(
            permission_key=bulk_service.BULK_CHANGE_PERMISSION
        ).one()
        rule.minimum_role = "simulator"
        db.session.commit()
        self.assertEqual(
            self.client.get("/neostaffing/bulk-change", follow_redirects=False).status_code,
            302,
        )
        self._login(self.pt_simulator_user)
        self.assertEqual(self.client.get("/neostaffing/bulk-change").status_code, 200)
        self._login(self.watcher_user)
        self.assertEqual(
            self.client.get("/neostaffing/bulk-change", follow_redirects=False).status_code,
            302,
        )

    def test_operator_normal_scope_and_simulator_cross_area(self):
        workspace = bulk_service.new_workspace(self.pt_operator_user)
        self._stage_person_name(workspace, self.employee_one, "Allowed", self.pt_operator_user)
        with self.assertRaisesRegex(ValueError, "outside your normal staffing scope"):
            self._stage_person_name(workspace, self.employee_two, "Blocked", self.pt_operator_user)

        cross_area = bulk_service.new_workspace(self.pt_simulator_user)
        self._stage_person_name(cross_area, self.employee_two, "Allowed", self.pt_simulator_user)
        self.assertEqual(
            cross_area["people"][f"p:{self.employee_two.id}"]["changes"]["first_name"],
            "Allowed",
        )

    def test_direct_apply_is_atomic_and_can_create_unlinked_person(self):
        workspace = bulk_service.new_workspace(self.ft_editor_user)
        self._stage_person_name(workspace, self.employee_one, "Updated", self.ft_editor_user)
        self._service_call(
            bulk_service.stage_workspace_change,
            workspace,
            "stage_new_person",
            {
                "employee_id": "BC-NEW",
                "first_name": "New",
                "last_name": "Employee",
                "seniority_date": "2024-01-02",
                "classification": "part_time",
                "employee_status": "active",
                "active": "1",
                "work_area_unit_id": str(self.area_two.id),
            },
            self.ft_editor_user,
        )
        result = self._service_call(bulk_service.apply_workspace, workspace, self.ft_editor_user)
        db.session.commit()
        self.assertEqual(result["people"], 2)
        self.assertEqual(db.session.get(StaffingPerson, self.employee_one.id).first_name, "Updated")
        new_person = StaffingPerson.query.filter_by(employee_id="BC-NEW").one()
        self.assertIsNone(User.query.filter_by(employee_id="BC-NEW").first())
        self.assertEqual(new_person.work_assignment.work_area_unit_id, self.area_two.id)

    def test_reorganization_uses_consolidated_review_and_can_keep_intentional_mismatch(self):
        second_ft = self._person("BC-FT3", "full_time_supervisor", "Second", "East")
        self._lead(second_ft, self.department_one)
        self._reports(second_ft, self.manager_one)
        db.session.commit()

        workspace = bulk_service.new_workspace(self.dm_editor_user)
        self._service_call(
            bulk_service.stage_workspace_change,
            workspace,
            "stage_unit",
            {"unit_id": str(self.department_one.id), "parent_id": str(self.operation_two.id)},
            self.dm_editor_user,
        )
        context = self._service_call(
            bulk_service.bulk_change_context,
            workspace,
            self.dm_editor_user,
        )
        affected = {
            row["person"]["ref"] for row in context["relationship_reviews"]
        }
        self.assertEqual(
            affected,
            {f"p:{self.ft_one.id}", f"p:{second_ft.id}"},
        )
        for person in (self.ft_one, second_ft):
            self._service_call(
                bulk_service.stage_workspace_change,
                workspace,
                "stage_reporting",
                {
                    "person_ref": f"p:{person.id}",
                    "relationship_action": "keep",
                },
                self.dm_editor_user,
            )
        result = self._service_call(
            bulk_service.apply_workspace,
            workspace,
            self.dm_editor_user,
        )
        db.session.commit()
        self.assertEqual(result["unit_changes"], 1)
        self.assertEqual(db.session.get(StaffingUnit, self.department_one.id).parent_id, self.operation_two.id)
        current = StaffingReportingRelationship.query.filter_by(
            person_id=self.ft_one.id,
            active=True,
        ).one()
        self.assertEqual(current.reports_to_person_id, self.manager_one.id)

    def test_management_assignment_and_reports_to_change_apply_together(self):
        workspace = bulk_service.new_workspace(self.dm_editor_user)
        assignment = StaffingLeadershipAssignment.query.filter_by(
            person_id=self.ft_one.id,
            unit_id=self.department_one.id,
            active=True,
        ).one()
        self._service_call(
            bulk_service.stage_workspace_change,
            workspace,
            "stage_leadership_remove",
            {"assignment_id": str(assignment.id)},
            self.dm_editor_user,
        )
        self._service_call(
            bulk_service.stage_workspace_change,
            workspace,
            "stage_leadership_add",
            {
                "person_ref": f"p:{self.ft_one.id}",
                "unit_id": str(self.department_two.id),
                "leadership_level": "department",
            },
            self.dm_editor_user,
        )
        context = self._service_call(bulk_service.bulk_change_context, workspace, self.dm_editor_user)
        row = next(
            row
            for row in context["relationship_reviews"]
            if row["person"]["ref"] == f"p:{self.ft_one.id}"
        )
        self.assertEqual([candidate["ref"] for candidate in row["suggested"]], [f"p:{self.manager_two.id}"])
        for review in context["relationship_reviews"]:
            person_ref = review["person"]["ref"]
            values = {
                "person_ref": person_ref,
                "relationship_action": "keep",
            }
            if person_ref == f"p:{self.ft_one.id}":
                values = {
                    "person_ref": person_ref,
                    "relationship_action": "change",
                    "reports_to_ref": f"p:{self.manager_two.id}",
                }
            self._service_call(
                bulk_service.stage_workspace_change,
                workspace,
                "stage_reporting",
                values,
                self.dm_editor_user,
            )
        self._service_call(bulk_service.apply_workspace, workspace, self.dm_editor_user)
        db.session.commit()
        active_assignments = StaffingLeadershipAssignment.query.filter_by(
            person_id=self.ft_one.id,
            active=True,
        ).all()
        self.assertEqual([row.unit_id for row in active_assignments], [self.department_two.id])
        relationship = StaffingReportingRelationship.query.filter_by(
            person_id=self.ft_one.id,
            active=True,
        ).one()
        self.assertEqual(relationship.reports_to_person_id, self.manager_two.id)
        self.assertEqual(
            StaffingReportingRelationship.query.filter_by(
                person_id=self.ft_one.id,
                active=False,
            ).count(),
            1,
        )

    def test_two_supervisors_can_swap_departments_in_one_package(self):
        workspace = bulk_service.new_workspace(self.dm_editor_user)
        first_assignment = StaffingLeadershipAssignment.query.filter_by(
            person_id=self.ft_one.id,
            unit_id=self.department_one.id,
            active=True,
        ).one()
        second_assignment = StaffingLeadershipAssignment.query.filter_by(
            person_id=self.ft_two.id,
            unit_id=self.department_two.id,
            active=True,
        ).one()
        for assignment in (first_assignment, second_assignment):
            self._service_call(
                bulk_service.stage_workspace_change,
                workspace,
                "stage_leadership_remove",
                {"assignment_id": str(assignment.id)},
                self.dm_editor_user,
            )
        for person, department in (
            (self.ft_one, self.department_two),
            (self.ft_two, self.department_one),
        ):
            self._service_call(
                bulk_service.stage_workspace_change,
                workspace,
                "stage_leadership_add",
                {
                    "person_ref": f"p:{person.id}",
                    "unit_id": str(department.id),
                    "leadership_level": "department",
                },
                self.dm_editor_user,
            )
        for person, manager in (
            (self.ft_one, self.manager_two),
            (self.ft_two, self.manager_one),
        ):
            self._service_call(
                bulk_service.stage_workspace_change,
                workspace,
                "stage_reporting",
                {
                    "person_ref": f"p:{person.id}",
                    "relationship_action": "change",
                    "reports_to_ref": f"p:{manager.id}",
                },
                self.dm_editor_user,
            )
        for person in (self.pt_operator, self.pt_simulator):
            self._service_call(
                bulk_service.stage_workspace_change,
                workspace,
                "stage_reporting",
                {
                    "person_ref": f"p:{person.id}",
                    "relationship_action": "change",
                    "reports_to_ref": f"p:{self.ft_two.id}",
                },
                self.dm_editor_user,
            )

        self._service_call(
            bulk_service.apply_workspace,
            workspace,
            self.dm_editor_user,
        )
        db.session.commit()

        assignments = StaffingLeadershipAssignment.query.filter_by(active=True).all()
        assignment_keys = {
            (row.person_id, row.unit_id) for row in assignments
        }
        self.assertIn((self.ft_one.id, self.department_two.id), assignment_keys)
        self.assertIn((self.ft_two.id, self.department_one.id), assignment_keys)
        relationships = {
            row.person_id: row.reports_to_person_id
            for row in StaffingReportingRelationship.query.filter_by(active=True).all()
        }
        self.assertEqual(relationships[self.ft_one.id], self.manager_two.id)
        self.assertEqual(relationships[self.ft_two.id], self.manager_one.id)

    def test_unchanged_reporting_suggestion_does_not_prompt_or_block(self):
        second_area = self._unit("work_area", "Second East Work Area", self.department_one)
        unrelated = self._person(
            "BC-UNASSIGNED-FT",
            "full_time_supervisor",
            "Unassigned",
            "Supervisor",
        )
        db.session.commit()

        workspace = bulk_service.new_workspace(self.dm_editor_user)
        self._service_call(
            bulk_service.stage_workspace_change,
            workspace,
            "stage_leadership_add",
            {
                "person_ref": f"p:{self.pt_operator.id}",
                "unit_id": str(second_area.id),
                "leadership_level": "work_area",
            },
            self.dm_editor_user,
        )
        context = self._service_call(
            bulk_service.bulk_change_context,
            workspace,
            self.dm_editor_user,
        )

        reviewed_refs = {
            row["person"]["ref"] for row in context["relationship_reviews"]
        }
        self.assertNotIn(f"p:{self.pt_operator.id}", reviewed_refs)
        self.assertNotIn(f"p:{unrelated.id}", reviewed_refs)
        self.assertEqual(context["blocking_errors"], [])

        self._service_call(
            bulk_service.apply_workspace,
            workspace,
            self.dm_editor_user,
        )
        db.session.commit()
        assignments = StaffingLeadershipAssignment.query.filter_by(
            person_id=self.pt_operator.id,
            active=True,
        ).all()
        self.assertEqual(
            {row.unit_id for row in assignments},
            {self.area_one.id, second_area.id},
        )
        self.assertIsNone(
            StaffingReportingRelationship.query.filter_by(
                person_id=unrelated.id,
                active=True,
            ).first()
        )

    def test_new_management_person_requires_valid_reports_to_but_operational_assignment_may_be_blank(self):
        workspace = bulk_service.new_workspace(self.dm_editor_user)
        self._service_call(
            bulk_service.stage_workspace_change,
            workspace,
            "stage_new_person",
            {
                "employee_id": "BC-NEW-MGR",
                "first_name": "New",
                "last_name": "Manager",
                "seniority_date": "2024-02-03",
                "classification": "manager",
                "employee_status": "active",
                "active": "1",
                "work_area_unit_id": "",
            },
            self.dm_editor_user,
        )
        with self.assertRaisesRegex(ValueError, "Choose a Reports To decision"):
            self._service_call(bulk_service.apply_workspace, workspace, self.dm_editor_user)
        new_ref = next(ref for ref in workspace["people"] if ref.startswith("n:"))
        self._service_call(
            bulk_service.stage_workspace_change,
            workspace,
            "stage_reporting",
            {
                "person_ref": new_ref,
                "relationship_action": "change",
                "reports_to_ref": f"p:{self.division_manager.id}",
            },
            self.dm_editor_user,
        )
        self._service_call(bulk_service.apply_workspace, workspace, self.dm_editor_user)
        db.session.commit()
        person = StaffingPerson.query.filter_by(employee_id="BC-NEW-MGR").one()
        self.assertEqual(person.leadership_assignments, [])
        relationship = StaffingReportingRelationship.query.filter_by(
            person_id=person.id,
            active=True,
        ).one()
        self.assertEqual(relationship.reports_to_person_id, self.division_manager.id)

    def test_existing_person_promotion_applies_classification_assignment_and_reports_to_together(self):
        workspace = bulk_service.new_workspace(self.dm_editor_user)
        self._service_call(
            bulk_service.stage_workspace_change,
            workspace,
            "stage_person",
            {
                "person_id": str(self.employee_one.id),
                "change_classification": "1",
                "classification": "part_time_supervisor",
            },
            self.dm_editor_user,
        )
        self._service_call(
            bulk_service.stage_workspace_change,
            workspace,
            "stage_leadership_add",
            {
                "person_ref": f"p:{self.employee_one.id}",
                "unit_id": str(self.area_one.id),
                "leadership_level": "work_area",
            },
            self.dm_editor_user,
        )
        self._service_call(
            bulk_service.stage_workspace_change,
            workspace,
            "stage_reporting",
            {
                "person_ref": f"p:{self.employee_one.id}",
                "relationship_action": "change",
                "reports_to_ref": f"p:{self.ft_one.id}",
            },
            self.dm_editor_user,
        )
        self._service_call(bulk_service.apply_workspace, workspace, self.dm_editor_user)
        db.session.commit()
        person = db.session.get(StaffingPerson, self.employee_one.id)
        self.assertEqual(person.classification, "part_time_supervisor")
        self.assertFalse(person.work_assignment.active)
        self.assertEqual(
            [row.unit_id for row in person.leadership_assignments if row.active],
            [self.area_one.id],
        )
        relationship = StaffingReportingRelationship.query.filter_by(
            person_id=person.id,
            active=True,
        ).one()
        self.assertEqual(relationship.reports_to_person_id, self.ft_one.id)

    def test_stale_state_rejects_complete_package_without_partial_write(self):
        workspace = bulk_service.new_workspace(self.ft_editor_user)
        self._stage_person_name(workspace, self.employee_one, "Package One", self.ft_editor_user)
        self._stage_person_name(workspace, self.employee_two, "Package Two", self.ft_editor_user)
        self.employee_one.last_name = "Live Change"
        db.session.commit()
        with self.assertRaisesRegex(ValueError, bulk_service.LIVE_DATA_CHANGED_MESSAGE):
            self._service_call(bulk_service.apply_workspace, workspace, self.ft_editor_user)
        db.session.rollback()
        db.session.expire_all()
        self.assertEqual(db.session.get(StaffingPerson, self.employee_one.id).first_name, "Employee")
        self.assertEqual(db.session.get(StaffingPerson, self.employee_two.id).first_name, "Employee")

    def test_pt_supervisor_submits_valid_fields_and_keeps_conflicts_or_unsupported_items_staged(self):
        self._service_call(
            request_service.submit_change_request,
            {
                "person_id": str(self.employee_one.id),
                "requested_first_name": "Already Pending",
            },
            self.pt_operator_user,
        )
        db.session.commit()

        workspace = bulk_service.new_workspace(self.pt_operator_user)
        self._service_call(
            bulk_service.stage_workspace_change,
            workspace,
            "stage_person",
            {
                "person_id": str(self.employee_one.id),
                "change_first_name": "1",
                "first_name": "Conflicting",
                "change_last_name": "1",
                "last_name": "Valid Request",
            },
            self.pt_operator_user,
        )
        self._service_call(
            bulk_service.stage_workspace_change,
            workspace,
            "stage_new_person",
            {
                "employee_id": "BC-UNSUPPORTED-NEW",
                "first_name": "Unsupported",
                "last_name": "Person",
                "seniority_date": "2024-01-01",
                "classification": "part_time",
                "employee_status": "active",
                "active": "1",
                "work_area_unit_id": str(self.area_one.id),
            },
            self.pt_operator_user,
        )
        result = self._service_call(
            bulk_service.submit_workspace,
            workspace,
            self.pt_operator_user,
        )
        db.session.commit()
        self.assertEqual(len(result["requests"]), 1)
        self.assertTrue(any(row["field"] == "first_name" for row in result["blocked"]))
        self.assertTrue(any(row["field"] == "new_person" for row in result["unsupported"]))
        pending = StaffingChangeRequestItem.query.filter_by(
            person_id=self.employee_one.id,
            status="pending",
        ).all()
        self.assertEqual({row.field_name for row in pending}, {"first_name", "last_name"})
        self.assertEqual(db.session.get(StaffingPerson, self.employee_one.id).last_name, "One")
        self.assertIn("first_name", workspace["people"][f"p:{self.employee_one.id}"]["changes"])
        self.assertNotIn("last_name", workspace["people"][f"p:{self.employee_one.id}"]["changes"])

    def test_pt_supervisor_management_items_are_unsupported_and_never_apply(self):
        workspace = bulk_service.new_workspace(self.pt_simulator_user)
        self._service_call(
            bulk_service.stage_workspace_change,
            workspace,
            "stage_reporting",
            {
                "person_ref": f"p:{self.pt_simulator.id}",
                "relationship_action": "change",
                "reports_to_ref": f"p:{self.ft_two.id}",
            },
            self.pt_simulator_user,
        )
        result = self._service_call(
            bulk_service.submit_workspace,
            workspace,
            self.pt_simulator_user,
        )
        self.assertEqual(result["requests"], [])
        self.assertTrue(any(row["field"] == "reporting" for row in result["unsupported"]))
        current = StaffingReportingRelationship.query.filter_by(
            person_id=self.pt_simulator.id,
            active=True,
        ).one()
        self.assertEqual(current.reports_to_person_id, self.ft_one.id)

    def test_master_role_without_management_authority_cannot_apply_management_package(self):
        master = self._user("bulk_plain_master", "master")
        db.session.commit()
        workspace = bulk_service.new_workspace(master)
        self._service_call(
            bulk_service.stage_workspace_change,
            workspace,
            "stage_reporting",
            {
                "person_ref": f"p:{self.ft_one.id}",
                "relationship_action": "keep",
            },
            master,
        )
        with self.assertRaisesRegex(ValueError, "eligible FT Supervisor"):
            self._service_call(bulk_service.apply_workspace, workspace, master)

    def test_context_and_final_apply_queries_remain_bounded_at_1500_people(self):
        people = []
        assignments = []
        for index in range(1492):
            person = StaffingPerson(
                employee_id=f"BC-SCALE-{index:04d}",
                first_name="Scale",
                last_name=f"Person {index:04d}",
                seniority_date=date(2020, 1, 1),
                classification="part_time",
                employee_status="active",
                active=True,
            )
            people.append(person)
            assignments.append(
                StaffingWorkAssignment(
                    person=person,
                    work_area=self.area_one,
                    active=True,
                )
            )
        db.session.add_all(people + assignments)
        db.session.commit()
        workspace = bulk_service.new_workspace(self.ft_editor_user)
        bundle = bulk_service.BulkChangeDataBundle()
        workspace["base_revision"] = bundle.revision
        for person in people[:100]:
            workspace["people"][f"p:{person.id}"] = {
                "kind": "existing",
                "person_id": person.id,
                "changes": {"first_name": "Updated"},
                "request_note": None,
            }
        db.session.expire_all()
        select_count = 0

        def count_selects(_connection, _cursor, statement, _parameters, _context, _many):
            nonlocal select_count
            if statement.lstrip().upper().startswith("SELECT"):
                select_count += 1

        event.listen(db.engine, "before_cursor_execute", count_selects)
        try:
            self._service_call(bulk_service.bulk_change_context, workspace, self.ft_editor_user)
        finally:
            event.remove(db.engine, "before_cursor_execute", count_selects)
        self.assertLessEqual(select_count, 12)

        select_count = 0
        event.listen(db.engine, "before_cursor_execute", count_selects)
        try:
            self._service_call(bulk_service.apply_workspace, workspace, self.ft_editor_user)
        finally:
            event.remove(db.engine, "before_cursor_execute", count_selects)
        self.assertLessEqual(select_count, 20)
        db.session.rollback()

    def _stage_person_name(self, workspace, person, first_name, user):
        return self._service_call(
            bulk_service.stage_workspace_change,
            workspace,
            "stage_person",
            {
                "person_id": str(person.id),
                "change_first_name": "1",
                "first_name": first_name,
            },
            user,
        )

    def _service_call(self, callback, *args):
        with self.app.test_request_context("/neostaffing/bulk-change"):
            return callback(*args)

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

    def _person(self, employee_id, classification, first_name, last_name):
        person = StaffingPerson(
            employee_id=employee_id,
            first_name=first_name,
            last_name=last_name,
            seniority_date=date(2020, 1, 1),
            classification=classification,
            employee_status="active",
            active=True,
        )
        db.session.add(person)
        db.session.flush()
        return person

    def _lead(self, person, unit):
        assignment = StaffingLeadershipAssignment(
            person=person,
            unit=unit,
            leadership_level=unit.unit_type,
            active=True,
        )
        db.session.add(assignment)
        db.session.flush()
        return assignment

    def _work(self, person, area):
        assignment = StaffingWorkAssignment(
            person=person,
            work_area=area,
            active=True,
        )
        db.session.add(assignment)
        db.session.flush()
        return assignment

    def _reports(self, person, target):
        relationship = StaffingReportingRelationship(
            person=person,
            reports_to_person=target,
            active=True,
            effective_start=date.today(),
        )
        db.session.add(relationship)
        db.session.flush()
        return relationship

    def _user(self, username, app_role, person=None):
        user = User(
            username=username,
            email=f"{username}@example.com",
            employee_id=person.employee_id if person else f"USER-{username}",
            role="watcher",
            is_active=True,
            email_verified_at=datetime.utcnow(),
        )
        set_user_password(user, "TestPassword123!")
        db.session.add(user)
        db.session.flush()
        db.session.add(
            PortalAppAccess(
                user_id=user.id,
                app_code="neostaffing",
                status="approved",
                role=app_role,
                is_active=True,
                approved_at=datetime.utcnow(),
            )
        )
        db.session.flush()
        return user

    def _login(self, user):
        g.pop("_login_user", None)
        return self.client.post(
            "/login",
            data={"username": user.username, "password": "TestPassword123!"},
            follow_redirects=False,
        )


if __name__ == "__main__":
    unittest.main()
