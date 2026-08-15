from datetime import date, datetime, timedelta
import json
import unittest
from unittest.mock import patch

from flask import g
from sqlalchemy import event

from app import create_app
from app.extensions import db
from app.models import (
    PermissionRule,
    PortalAppAccess,
    StaffingChangeRequest,
    StaffingChangeRequestEvent,
    StaffingChangeRequestItem,
    StaffingLeadershipAssignment,
    StaffingPerson,
    StaffingReportingRelationship,
    StaffingUnit,
    StaffingWorkAssignment,
    User,
)
from app.services import neostaffing_change_requests as request_service
from app.services.password_policy import set_user_password
from app.services.permission_rules import ensure_default_permission_rules


class NeoStaffingChangeRequestsTest(unittest.TestCase):
    def setUp(self):
        config = type(
            "NeoStaffingChangeRequestsConfig",
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
        self.client = self.app.test_client()

        self.sort = self._unit("sort", "Night")
        self.operation = self._unit("operation", "Unload", self.sort)
        self.source_department = self._unit(
            "department", "Source Department", self.operation
        )
        self.destination_department = self._unit(
            "department", "Destination Department", self.operation
        )
        self.source_area = self._unit(
            "work_area", "Source Area", self.source_department
        )
        self.destination_area = self._unit(
            "work_area", "Destination Area", self.destination_department
        )

        self.submitter = self._person("PT-100", "part_time_supervisor", "Pat", "Submitter")
        self.source_approver = self._person(
            "FT-100", "full_time_supervisor", "Fran", "Source"
        )
        self.destination_approver = self._person(
            "FT-200", "full_time_supervisor", "Drew", "Destination"
        )
        self.manager = self._person("MGR-100", "manager", "Morgan", "Manager")
        self.division_manager = self._person(
            "DIV-100", "division_manager", "Dana", "Division"
        )
        self.target = self._person("EMP-100", "part_time", "Taylor", "Employee")
        db.session.add_all(
            [
                StaffingWorkAssignment(
                    person=self.target,
                    work_area=self.source_area,
                    active=True,
                ),
                StaffingLeadershipAssignment(
                    person=self.submitter,
                    unit=self.source_area,
                    leadership_level="work_area",
                    active=True,
                ),
                StaffingLeadershipAssignment(
                    person=self.source_approver,
                    unit=self.source_department,
                    leadership_level="department",
                    active=True,
                ),
                StaffingLeadershipAssignment(
                    person=self.destination_approver,
                    unit=self.destination_department,
                    leadership_level="department",
                    active=True,
                ),
                StaffingLeadershipAssignment(
                    person=self.manager,
                    unit=self.operation,
                    leadership_level="operation",
                    active=True,
                ),
                StaffingLeadershipAssignment(
                    person=self.division_manager,
                    unit=self.sort,
                    leadership_level="sort",
                    active=True,
                ),
                StaffingReportingRelationship(
                    person=self.submitter,
                    reports_to_person=self.source_approver,
                    active=True,
                ),
            ]
        )
        self.submitter_user = self._user(
            "pt_submitter", "operator", self.submitter
        )
        self.source_approver_user = self._user(
            "ft_approver", "operator", self.source_approver
        )
        self.manager_user = self._user("manager_approver", "operator", self.manager)
        self.grandmaster_user = self._user("staffing_gm", "grandmaster")
        self.grandmaster_user.role = "grandmaster"
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_submission_captures_multiple_immutable_fields_and_source_destination_routing(self):
        change_request = self._submit(
            self.submitter_user,
            requested_first_name="Updated",
            requested_work_area_unit_id=str(self.destination_area.id),
            request_note="Move after sort.",
        )
        db.session.commit()

        self.assertEqual(change_request.status, "pending")
        self.assertEqual(change_request.source_work_area_unit_id, self.source_area.id)
        self.assertEqual(
            change_request.destination_work_area_unit_id,
            self.destination_area.id,
        )
        self.assertEqual(
            json.loads(change_request.routed_approver_person_ids_json),
            sorted([self.source_approver.id, self.destination_approver.id]),
        )
        items = StaffingChangeRequestItem.query.filter_by(
            request_id=change_request.id
        ).order_by(StaffingChangeRequestItem.field_name).all()
        self.assertEqual(
            {item.field_name for item in items},
            {"first_name", "work_area_unit_id"},
        )
        first_name = next(item for item in items if item.field_name == "first_name")
        self.assertEqual(json.loads(first_name.original_value_json), "Taylor")
        self.assertEqual(json.loads(first_name.requested_value_json), "Updated")
        self.assertEqual(StaffingChangeRequestEvent.query.count(), 2)
        self.assertEqual(self.target.first_name, "Taylor")

    def test_only_one_pending_item_per_employee_and_field(self):
        self._submit(self.submitter_user, requested_first_name="First")
        db.session.commit()

        with self.assertRaisesRegex(ValueError, "Pending request already exists"):
            self._submit(self.submitter_user, requested_first_name="Second")
        db.session.rollback()
        self.assertEqual(
            StaffingChangeRequestItem.query.filter_by(
                person_id=self.target.id,
                field_name="first_name",
                status="pending",
            ).count(),
            1,
        )

    def test_operator_scope_and_simulator_cross_area_submission(self):
        other_target = self._person("EMP-200", "full_time_combo", "Other", "Area")
        db.session.add(
            StaffingWorkAssignment(
                person=other_target,
                work_area=self.destination_area,
                active=True,
            )
        )
        simulator_person = self._person(
            "PT-200", "part_time_supervisor", "Sim", "Supervisor"
        )
        simulator = self._user("pt_simulator", "simulator", simulator_person)
        db.session.commit()

        with self.assertRaisesRegex(ValueError, "outside your"):
            self._submit(
                self.submitter_user,
                person_id=other_target.id,
                requested_last_name="Changed",
            )
        db.session.rollback()
        request_row = self._submit(
            simulator,
            person_id=other_target.id,
            requested_last_name="Changed",
        )
        db.session.commit()
        self.assertEqual(request_row.status, "pending")

    def test_master_pt_supervisor_still_requires_approval_and_grandmaster_bypasses(self):
        master_person = self._person(
            "PT-300", "part_time_supervisor", "Master", "Supervisor"
        )
        master = self._user("pt_master", "master", master_person)
        db.session.commit()

        pending = self._submit(master, requested_last_name="Pending")
        db.session.commit()
        self.assertEqual(pending.status, "pending")
        self.assertEqual(self.target.last_name, "Employee")

        self._decide_all(pending.id, "deny", "Clear field", self.source_approver_user)
        db.session.commit()
        applied = self._submit(
            self.grandmaster_user,
            requested_last_name="Applied",
        )
        db.session.commit()
        self.assertEqual(applied.status, "completed")
        self.assertEqual(self.target.last_name, "Applied")
        self.assertEqual(applied.items[0].status, "approved")

    def test_routing_falls_back_to_reports_to_then_unassigned(self):
        for assignment in StaffingLeadershipAssignment.query.filter(
            StaffingLeadershipAssignment.person_id.in_(
                {self.source_approver.id, self.destination_approver.id}
            )
        ):
            assignment.active = False
        db.session.commit()

        fallback = self._submit(self.submitter_user, requested_first_name="Fallback")
        db.session.commit()
        self.assertEqual(
            json.loads(fallback.routed_approver_person_ids_json),
            [self.source_approver.id],
        )
        self._decide_all(fallback.id, "deny", "Reset", self.source_approver_user)
        StaffingReportingRelationship.query.filter_by(
            person_id=self.submitter.id,
            active=True,
        ).update({"active": False})
        db.session.commit()

        unassigned = self._submit(self.submitter_user, requested_first_name="Unassigned")
        db.session.commit()
        self.assertTrue(unassigned.unassigned_approval)
        self.assertEqual(json.loads(unassigned.routed_approver_person_ids_json), [])

    def test_one_of_multiple_ft_supervisors_can_approve_and_apply_immediately(self):
        request_row = self._submit(
            self.submitter_user,
            requested_first_name="Approved",
            requested_work_area_unit_id=str(self.destination_area.id),
        )
        db.session.commit()
        item = next(row for row in request_row.items if row.field_name == "first_name")
        revision = request_service.change_request_item_revision(item)

        rows = self._service_call(
            request_service.decide_change_request_item,
            item.id,
            "approve",
            None,
            self.source_approver_user,
            revision,
        )
        db.session.commit()

        self.assertEqual(rows[0].status, "approved")
        self.assertEqual(self.target.first_name, "Approved")
        self.assertEqual(request_row.status, "pending")

        work_area_item = next(
            row for row in request_row.items if row.field_name == "work_area_unit_id"
        )
        self._service_call(
            request_service.decide_change_request_item,
            work_area_item.id,
            "approve",
            None,
            self.source_approver_user,
            request_service.change_request_item_revision(work_area_item),
        )
        db.session.commit()
        assignment = StaffingWorkAssignment.query.filter_by(
            person_id=self.target.id,
            active=True,
        ).one()
        self.assertEqual(assignment.work_area_unit_id, self.destination_area.id)
        self.assertEqual(request_row.status, "completed")

    def test_all_supported_person_fields_apply_but_employee_id_phone_and_management_do_not(self):
        request_row = self._submit(
            self.submitter_user,
            requested_first_name="First",
            requested_last_name="Last",
            requested_seniority_date="2019-05-06",
            requested_employee_status="fmla",
            requested_classification="full_time_combo",
            requested_employee_id="FORGED-ID",
            requested_phone_number="555-9999",
        )
        db.session.commit()
        self._decide_all(
            request_row.id,
            "approve",
            None,
            self.source_approver_user,
        )
        db.session.commit()

        self.assertEqual(self.target.employee_id, "EMP-100")
        self.assertIsNone(self.target.phone_number)
        self.assertEqual(self.target.first_name, "First")
        self.assertEqual(self.target.last_name, "Last")
        self.assertEqual(self.target.seniority_date, date(2019, 5, 6))
        self.assertEqual(self.target.employee_status, "fmla")
        self.assertEqual(self.target.classification, "full_time_combo")
        self.assertEqual(
            {item.field_name for item in request_row.items},
            {
                "first_name",
                "last_name",
                "seniority_date",
                "employee_status",
                "classification",
            },
        )

        with self.assertRaisesRegex(ValueError, "non-management"):
            self._submit(
                self.submitter_user,
                requested_classification="manager",
            )
        db.session.rollback()
        management_target = self._person(
            "MGR-200", "manager", "Management", "Target"
        )
        db.session.commit()
        with self.assertRaisesRegex(ValueError, "non-management employee"):
            self._submit(
                self.submitter_user,
                person_id=management_target.id,
                requested_first_name="Blocked",
            )

    def test_field_and_bulk_denial_require_reason_and_withdrawal_is_submitter_only(self):
        request_row = self._submit(
            self.submitter_user,
            requested_first_name="One",
            requested_last_name="Two",
        )
        db.session.commit()
        item = request_row.items[0]
        with self.assertRaisesRegex(ValueError, "denial reason"):
            self._service_call(
                request_service.decide_change_request_item,
                item.id,
                "deny",
                "",
                self.source_approver_user,
                request_service.change_request_item_revision(item),
            )
        db.session.rollback()

        rows = self._decide_all(
            request_row.id,
            "deny",
            "Not supported",
            self.source_approver_user,
        )
        db.session.commit()
        self.assertEqual({row.status for row in rows}, {"denied"})
        self.assertEqual(request_row.status, "completed")

        withdrawal = self._submit(
            self.submitter_user,
            requested_first_name="Withdraw",
            requested_last_name="Remaining",
        )
        db.session.commit()
        first = withdrawal.items[0]
        with self.assertRaisesRegex(ValueError, "Only the submitter"):
            self._service_call(
                request_service.withdraw_change_request_item,
                first.id,
                None,
                self.source_approver_user,
                request_service.change_request_item_revision(first),
            )
        db.session.rollback()
        self._service_call(
            request_service.withdraw_change_request_item,
            first.id,
            "Duplicate",
            self.submitter_user,
            request_service.change_request_item_revision(first),
        )
        count = self._service_call(
            request_service.withdraw_change_request_remaining,
            withdrawal.id,
            None,
            self.submitter_user,
        )
        db.session.commit()
        self.assertEqual(count, 1)
        self.assertEqual(withdrawal.status, "completed")

    def test_approval_supersedes_when_original_value_changed_and_stale_action_loses(self):
        request_row = self._submit(self.submitter_user, requested_first_name="Requested")
        db.session.commit()
        item = request_row.items[0]
        old_revision = request_service.change_request_item_revision(item)
        self.target.first_name = "Newer Native Value"
        db.session.commit()

        decided = self._service_call(
            request_service.decide_change_request_item,
            item.id,
            "approve",
            None,
            self.source_approver_user,
            old_revision,
        )
        db.session.commit()
        self.assertEqual(decided[0].status, "superseded")
        self.assertEqual(self.target.first_name, "Newer Native Value")

        another = self._submit(self.submitter_user, requested_last_name="Denied")
        db.session.commit()
        another_item = another.items[0]
        stale_revision = request_service.change_request_item_revision(another_item)
        self._service_call(
            request_service.decide_change_request_item,
            another_item.id,
            "deny",
            "First decision",
            self.source_approver_user,
            stale_revision,
        )
        db.session.commit()
        with self.assertRaisesRegex(ValueError, "changed|already"):
            self._service_call(
                request_service.decide_change_request_item,
                another_item.id,
                "approve",
                None,
                self.manager_user,
                stale_revision,
            )

    def test_approved_and_denied_reversal_rules_preserve_newer_values(self):
        request_row = self._submit(self.submitter_user, requested_first_name="Applied")
        db.session.commit()
        item = request_row.items[0]
        self._service_call(
            request_service.decide_change_request_item,
            item.id,
            "approve",
            None,
            self.source_approver_user,
            request_service.change_request_item_revision(item),
        )
        db.session.commit()
        self._service_call(
            request_service.reverse_change_request_item,
            item.id,
            "Approved in error",
            self.source_approver_user,
            request_service.change_request_item_revision(item),
        )
        db.session.commit()
        self.assertEqual(item.status, "pending")
        self.assertEqual(self.target.first_name, "Taylor")

        self._service_call(
            request_service.decide_change_request_item,
            item.id,
            "approve",
            None,
            self.source_approver_user,
            request_service.change_request_item_revision(item),
        )
        db.session.commit()
        self.target.first_name = "Later Edit"
        db.session.commit()
        with self.assertRaisesRegex(ValueError, "changed after approval"):
            self._service_call(
                request_service.reverse_change_request_item,
                item.id,
                "Try reversal",
                self.source_approver_user,
                request_service.change_request_item_revision(item),
            )
        db.session.rollback()
        self.assertEqual(self.target.first_name, "Later Edit")

        denied = self._submit(self.submitter_user, requested_last_name="Denied")
        db.session.commit()
        denied_item = denied.items[0]
        self._service_call(
            request_service.decide_change_request_item,
            denied_item.id,
            "deny",
            "No",
            self.source_approver_user,
            request_service.change_request_item_revision(denied_item),
        )
        db.session.commit()
        self._service_call(
            request_service.reverse_change_request_item,
            denied_item.id,
            "Reconsider",
            self.manager_user,
            request_service.change_request_item_revision(denied_item),
        )
        db.session.commit()
        self.assertEqual(denied_item.status, "pending")

    def test_management_classification_and_configurable_permissions_both_apply(self):
        with self.app.test_request_context("/neostaffing/requests"):
            self.assertFalse(
                request_service.can_approve_change_requests(self.submitter_user)
            )
            self.assertTrue(
                request_service.can_approve_change_requests(self.source_approver_user)
            )

        submit_rule = PermissionRule.query.filter_by(
            permission_key="neostaffing.change_requests.submit"
        ).one()
        submit_rule.minimum_role = "simulator"
        simulator_person = self._person(
            "PT-400", "part_time_supervisor", "Cross", "Area"
        )
        simulator = self._user("threshold_simulator", "simulator", simulator_person)
        db.session.commit()
        with self.app.test_request_context("/neostaffing/requests"):
            self.assertFalse(request_service.can_submit_change_requests(self.submitter_user))
            self.assertTrue(request_service.can_submit_change_requests(simulator))

        watcher_person = self._person(
            "FT-400", "full_time_supervisor", "Watch", "Only"
        )
        watcher = self._user("ft_watcher", "watcher", watcher_person)
        approve_rule = PermissionRule.query.filter_by(
            permission_key="neostaffing.change_requests.approve"
        ).one()
        approve_rule.minimum_role = "watcher"
        db.session.commit()
        with self.app.test_request_context("/neostaffing/requests"):
            self.assertFalse(request_service.can_approve_change_requests(watcher))

    def test_retention_expires_unresolved_purges_completed_and_orders_overdue_first(self):
        old_request = self._submit(self.submitter_user, requested_first_name="Old")
        db.session.commit()
        old_request.submitted_at = datetime.utcnow() - timedelta(days=31)

        completed = StaffingChangeRequest(
            person_id=self.target.id,
            submitted_by_user_id=self.submitter_user.id,
            status="completed",
            submitted_at=datetime.utcnow() - timedelta(days=20),
            completed_at=datetime.utcnow() - timedelta(days=15),
            routed_approver_person_ids_json="[]",
        )
        db.session.add(completed)
        db.session.flush()
        completed_id = completed.id
        db.session.add(
            StaffingChangeRequestItem(
                request_id=completed.id,
                person_id=self.target.id,
                field_name="last_name",
                original_value_json=json.dumps("Employee"),
                requested_value_json=json.dumps("Old"),
                status="denied",
                decided_at=datetime.utcnow() - timedelta(days=15),
            )
        )
        db.session.commit()

        result = self._service_call(
            request_service.cleanup_change_request_retention,
            datetime.utcnow(),
        )
        db.session.commit()
        self.assertEqual(result["expired"], 1)
        self.assertEqual(result["purged"], 1)
        self.assertEqual(old_request.status, "completed")
        self.assertEqual(old_request.items[0].status, "superseded")
        self.assertIsNone(db.session.get(StaffingChangeRequest, completed_id))

        overdue = self._submit(self.submitter_user, requested_last_name="Overdue")
        db.session.commit()
        overdue.submitted_at = datetime.utcnow() - timedelta(hours=49)
        fresh_target = self._person("EMP-300", "part_time", "Fresh", "Employee")
        db.session.add(
            StaffingWorkAssignment(
                person=fresh_target,
                work_area=self.source_area,
                active=True,
            )
        )
        db.session.commit()
        fresh = self._submit(
            self.submitter_user,
            person_id=fresh_target.id,
            requested_first_name="New",
        )
        db.session.commit()
        context = self._service_call(
            request_service.change_request_context,
            {"view": "active", "queue": "all"},
            self.submitter_user,
        )
        self.assertEqual(context["rows"][0]["request"].id, overdue.id)
        self.assertTrue(context["rows"][0]["overdue"])
        self.assertEqual(context["rows"][1]["request"].id, fresh.id)

    def test_default_queues_follow_routing_and_manager_operational_purview(self):
        request_row = self._submit(self.submitter_user, requested_first_name="Queue")
        db.session.commit()
        ft_context = self._service_call(
            request_service.change_request_context,
            {"view": "active"},
            self.source_approver_user,
        )
        manager_context = self._service_call(
            request_service.change_request_context,
            {"view": "active"},
            self.manager_user,
        )
        division_user = self._user(
            "division_approver",
            "operator",
            self.division_manager,
        )
        db.session.commit()
        division_all = self._service_call(
            request_service.change_request_context,
            {"view": "active", "queue": "all"},
            division_user,
        )
        self.assertEqual(ft_context["filters"]["queue"], "routed")
        self.assertEqual(ft_context["rows"][0]["request"].id, request_row.id)
        self.assertEqual(manager_context["filters"]["queue"], "purview")
        self.assertEqual(manager_context["rows"][0]["request"].id, request_row.id)
        self.assertTrue(division_all["can_approve"])
        self.assertEqual(division_all["filters"]["queue"], "all")
        self.assertEqual(division_all["rows"][0]["request"].id, request_row.id)

    def test_requests_get_is_read_only_without_retention_work_and_renders_actions(self):
        request_row = self._submit(self.submitter_user, requested_first_name="Render")
        db.session.commit()
        self._login(self.source_approver_user)
        dml = []

        def capture_sql(_connection, _cursor, statement, _parameters, _context, _many):
            if statement.lstrip().split(None, 1)[0].upper() in {"INSERT", "UPDATE", "DELETE"}:
                dml.append(statement)

        event.listen(db.engine, "before_cursor_execute", capture_sql)
        try:
            with patch("app.neostaffing.routes.db.session.commit") as commit:
                response = self.client.get("/neostaffing/requests?queue=routed")
        finally:
            event.remove(db.engine, "before_cursor_execute", capture_sql)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"ACTIVE", response.data)
        self.assertIn(b"HISTORY", response.data)
        self.assertIn(b"ALL REQUESTS", response.data)
        self.assertIn(b"APPROVE FIELD", response.data)
        self.assertIn(str(request_row.id).encode(), response.data)
        self.assertEqual(dml, [])
        commit.assert_not_called()

    def test_request_routes_enforce_dynamic_access_and_apply_valid_decision(self):
        watcher = self._user("request_watcher", "watcher")
        db.session.commit()
        self._login(watcher)
        read_only = self.client.get("/neostaffing/requests?queue=all")
        self.assertEqual(read_only.status_code, 200)
        self.assertNotIn(b"SUBMIT REQUEST", read_only.data)
        self.assertNotIn(b"APPROVE FIELD", read_only.data)

        self._login(self.submitter_user)
        submit_page = self.client.get(
            f"/neostaffing/requests?queue=all&person_id={self.target.id}"
        )
        self.assertIn(b"SUBMIT REQUEST", submit_page.data)
        submitted = self.client.post(
            "/neostaffing/requests/submit",
            data={
                "person_id": str(self.target.id),
                "requested_first_name": "Route Applied",
                "view": "active",
                "queue": "all",
            },
            follow_redirects=False,
        )
        self.assertEqual(submitted.status_code, 302)
        item = StaffingChangeRequestItem.query.filter_by(status="pending").one()

        self._login(self.source_approver_user)
        approved = self.client.post(
            f"/neostaffing/requests/items/{item.id}/decision",
            data={
                "action": "approve",
                "expected_revision": request_service.change_request_item_revision(item),
                "view": "active",
                "queue": "routed",
            },
            follow_redirects=False,
        )
        self.assertEqual(approved.status_code, 302)
        db.session.expire_all()
        self.assertEqual(db.session.get(StaffingPerson, self.target.id).first_name, "Route Applied")

    def test_context_and_submission_queries_remain_bounded_at_1500_people(self):
        people = []
        assignments = []
        for index in range(1499):
            person = StaffingPerson(
                employee_id=f"SCALE-{index:04d}",
                first_name="Scale",
                last_name=f"Person {index:04d}",
                seniority_date=date(2020, 1, 1),
                classification="part_time" if index % 2 else "full_time_combo",
                employee_status="active",
                active=True,
            )
            people.append(person)
            assignments.append(
                StaffingWorkAssignment(
                    person=person,
                    work_area=self.source_area if index % 2 else self.destination_area,
                    active=True,
                )
            )
        db.session.add_all(people + assignments)
        db.session.commit()
        submitter_user_id = self.submitter_user.id
        scale_requests = []
        for index, person in enumerate(people[:100]):
            request_row = StaffingChangeRequest(
                person_id=person.id,
                submitted_by_user_id=submitter_user_id,
                submitted_by_person_id=self.submitter.id,
                source_work_area_unit_id=(
                    self.source_area.id if index % 2 else self.destination_area.id
                ),
                routed_approver_person_ids_json="[]",
                unassigned_approval=True,
                status="pending",
            )
            db.session.add(request_row)
            db.session.flush()
            db.session.add(
                StaffingChangeRequestItem(
                    request_id=request_row.id,
                    person_id=person.id,
                    field_name="first_name",
                    original_value_json=json.dumps("Scale"),
                    requested_value_json=json.dumps(f"Scale {index}"),
                    status="pending",
                )
            )
            scale_requests.append(request_row)
        db.session.commit()
        db.session.expunge_all()

        select_count = 0

        def count_selects(_connection, _cursor, statement, _parameters, _context, _many):
            nonlocal select_count
            if statement.lstrip().upper().startswith("SELECT"):
                select_count += 1

        event.listen(db.engine, "before_cursor_execute", count_selects)
        try:
            context = self._service_call(
                request_service.change_request_context,
                {"view": "active", "queue": "all"},
                db.session.get(User, submitter_user_id),
            )
        finally:
            event.remove(db.engine, "before_cursor_execute", count_selects)
        self.assertGreaterEqual(len(context["candidates"]), 700)
        self.assertEqual(len(context["rows"]), 100)
        self.assertLessEqual(select_count, 16)

    def _submit(self, user, person_id=None, **changes):
        values = {"person_id": str(person_id or self.target.id), **changes}
        return self._service_call(request_service.submit_change_request, values, user)

    def _decide_all(self, request_id, action, reason, user):
        return self._service_call(
            request_service.decide_change_request_remaining,
            request_id,
            action,
            reason,
            user,
        )

    def _service_call(self, callback, *args):
        with self.app.test_request_context("/neostaffing/requests"):
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
        )


if __name__ == "__main__":
    unittest.main()
