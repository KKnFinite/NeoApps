from datetime import date, datetime, timedelta
import unittest
from unittest.mock import patch

from flask import g
from sqlalchemy import event

from app import create_app
from app.extensions import db
from app.models import (
    PortalAppAccess,
    StaffingLeadershipAssignment,
    StaffingPerson,
    StaffingReportingRelationship,
    StaffingUnit,
    User,
)
from app.services import neostaffing as staffing_service
from app.services.password_policy import set_user_password


class NeoStaffingReportingTest(unittest.TestCase):
    def setUp(self):
        config = type(
            "NeoStaffingReportingConfig",
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
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_reporting_tiers_and_division_manager_exception_are_hard_validated(self):
        part_time = self._person("R100", "part_time")
        part_time_supervisor = self._person("R101", "part_time_supervisor")
        specialist = self._person("R102", "full_time_specialist")
        full_time_supervisor = self._person("R103", "full_time_supervisor")
        manager = self._person("R104", "manager")
        division_manager = self._person("R105", "division_manager")

        for person, reports_to_person in (
            (part_time_supervisor, full_time_supervisor),
            (specialist, full_time_supervisor),
            (full_time_supervisor, manager),
            (manager, division_manager),
        ):
            with self.subTest(person=person.classification):
                self.assertTrue(
                    staffing_service.validate_reporting_relationship(
                        person,
                        reports_to_person,
                    )
                )

        with self.assertRaisesRegex(ValueError, "must report to"):
            staffing_service.validate_reporting_relationship(
                part_time_supervisor,
                manager,
            )
        with self.assertRaisesRegex(ValueError, "does not have"):
            staffing_service.validate_reporting_relationship(part_time, manager)
        with self.assertRaisesRegex(ValueError, "do not have"):
            staffing_service.validate_reporting_relationship(
                division_manager,
                manager,
            )

    def test_relationship_change_preserves_history_and_rejects_stale_revision(self):
        manager = self._person("R200", "manager")
        first_division_manager = self._person("R201", "division_manager")
        second_division_manager = self._person("R202", "division_manager")
        first_date = date(2026, 8, 1)
        second_date = date(2026, 8, 10)

        first_result = staffing_service.update_reporting_relationship(
            manager.id,
            first_division_manager.id,
            "none",
            effective_date=first_date,
        )
        db.session.commit()
        first_revision = staffing_service.reporting_relationship_revision(
            first_result["relationship"]
        )
        second_result = staffing_service.update_reporting_relationship(
            manager.id,
            second_division_manager.id,
            first_revision,
            effective_date=second_date,
        )
        db.session.commit()

        rows = StaffingReportingRelationship.query.filter_by(
            person_id=manager.id
        ).order_by(StaffingReportingRelationship.id).all()
        self.assertEqual(len(rows), 2)
        self.assertFalse(rows[0].active)
        self.assertEqual(rows[0].effective_end, second_date)
        self.assertTrue(rows[1].active)
        self.assertEqual(rows[1].reports_to_person_id, second_division_manager.id)
        self.assertEqual(
            StaffingReportingRelationship.query.filter_by(
                person_id=manager.id,
                active=True,
            ).count(),
            1,
        )

        with self.assertRaisesRegex(ValueError, "changed while you were editing"):
            staffing_service.update_reporting_relationship(
                manager.id,
                first_division_manager.id,
                first_revision,
            )
        db.session.rollback()
        self.assertEqual(second_result["relationship"].reports_to_person_id, second_division_manager.id)

    def test_mutation_purges_only_history_older_than_thirty_days(self):
        manager = self._person("R300", "manager")
        division_manager = self._person("R301", "division_manager")
        as_of = date(2026, 8, 15)
        retained = StaffingReportingRelationship(
            person=manager,
            reports_to_person=division_manager,
            active=False,
            effective_start=as_of - timedelta(days=60),
            effective_end=as_of - timedelta(days=30),
        )
        expired = StaffingReportingRelationship(
            person=manager,
            reports_to_person=division_manager,
            active=False,
            effective_start=as_of - timedelta(days=70),
            effective_end=as_of - timedelta(days=31),
        )
        db.session.add_all([retained, expired])
        db.session.commit()

        result = staffing_service.update_reporting_relationship(
            manager.id,
            division_manager.id,
            "none",
            effective_date=as_of,
        )
        db.session.commit()

        self.assertEqual(result["purged"], 1)
        self.assertIsNotNone(db.session.get(StaffingReportingRelationship, retained.id))
        self.assertEqual(
            StaffingReportingRelationship.query.filter_by(
                active=False,
                effective_end=as_of - timedelta(days=31),
            ).count(),
            0,
        )

    def test_management_context_uses_reports_to_and_allows_operational_mismatch(self):
        first_sort, first_operation, _first_department, _first_area = self._hierarchy(
            "First"
        )
        second_sort, second_operation, second_department, _second_area = self._hierarchy(
            "Second"
        )
        division_manager = self._person("R400", "division_manager", "Dana", "Division")
        manager = self._person("R401", "manager", "Morgan", "Manager")
        full_time_supervisor = self._person(
            "R402",
            "full_time_supervisor",
            "Frankie",
            "Supervisor",
        )
        specialist = self._person(
            "R403",
            "full_time_specialist",
            "Sam",
            "Specialist",
        )
        no_assignment_manager = self._person("R404", "manager", "No", "Assignment")
        db.session.add_all(
            [
                StaffingLeadershipAssignment(
                    person=division_manager,
                    unit=first_sort,
                    leadership_level="sort",
                ),
                StaffingLeadershipAssignment(
                    person=manager,
                    unit=second_operation,
                    leadership_level="operation",
                ),
                StaffingLeadershipAssignment(
                    person=full_time_supervisor,
                    unit=second_department,
                    leadership_level="department",
                ),
                StaffingReportingRelationship(
                    person=manager,
                    reports_to_person=division_manager,
                ),
                StaffingReportingRelationship(
                    person=full_time_supervisor,
                    reports_to_person=manager,
                ),
                StaffingReportingRelationship(
                    person=no_assignment_manager,
                    reports_to_person=division_manager,
                ),
            ]
        )
        db.session.commit()

        context = staffing_service.management_org_chart_context(manager.id)
        root = next(
            row for row in context["tree"] if row["person"].id == division_manager.id
        )
        manager_node = next(
            row for row in root["children"] if row["person"].id == manager.id
        )
        self.assertTrue(manager_node["mismatch"])
        self.assertEqual(
            [row["person"].id for row in manager_node["children"]],
            [full_time_supervisor.id],
        )
        self.assertTrue(context["selected_detail"]["mismatch"])
        self.assertEqual(
            context["selected_detail"]["operational_assignments"][0]["unit"].id,
            second_operation.id,
        )
        self.assertIn(
            specialist.id,
            [row["person"].id for row in context["unassigned_tree"]],
        )

        no_assignment_context = staffing_service.management_org_chart_context(
            no_assignment_manager.id
        )
        self.assertFalse(no_assignment_context["selected_detail"]["mismatch"])
        self.assertFalse(
            no_assignment_context["selected_detail"]["comparison_available"]
        )
        self.assertEqual(
            no_assignment_context["selected_detail"]["operational_assignments"],
            [],
        )
        self.assertNotEqual(first_operation.id, second_operation.id)
        self.assertNotEqual(first_sort.id, second_sort.id)

    def test_multiple_operational_assignments_are_equal_and_can_suggest_reports_to(self):
        _first_sort, first_operation, first_department, _first_area = self._hierarchy(
            "First"
        )
        _second_sort, second_operation, second_department, _second_area = self._hierarchy(
            "Second"
        )
        manager = self._person("R500", "manager")
        supervisor = self._person("R501", "full_time_supervisor")
        db.session.add_all(
            [
                StaffingLeadershipAssignment(
                    person=manager,
                    unit=first_operation,
                    leadership_level="operation",
                ),
                StaffingLeadershipAssignment(
                    person=manager,
                    unit=second_operation,
                    leadership_level="operation",
                ),
                StaffingLeadershipAssignment(
                    person=supervisor,
                    unit=first_department,
                    leadership_level="department",
                ),
                StaffingLeadershipAssignment(
                    person=supervisor,
                    unit=second_department,
                    leadership_level="department",
                ),
            ]
        )
        db.session.commit()

        context = staffing_service.management_org_chart_context(supervisor.id)
        candidate = next(
            row
            for row in context["selected_detail"]["candidates"]
            if row["person"].id == manager.id
        )
        self.assertTrue(candidate["suggested"])
        self.assertEqual(
            len(staffing_service.management_org_chart_context(manager.id)["selected_detail"]["operational_assignments"]),
            2,
        )

    def test_management_context_query_count_is_bounded_for_1500_people(self):
        people = [
            StaffingPerson(
                employee_id=f"LARGE-{index:04d}",
                first_name="Large",
                last_name=f"Person {index:04d}",
                seniority_date=date(2020, 1, 1),
                classification="part_time_supervisor",
                employee_status="active",
                active=True,
            )
            for index in range(1500)
        ]
        db.session.add_all(people)
        db.session.commit()
        db.session.expunge_all()

        select_count = 0

        def count_sql(_connection, _cursor, statement, _parameters, _context, _many):
            nonlocal select_count
            if statement.lstrip().upper().startswith("SELECT"):
                select_count += 1

        event.listen(db.engine, "before_cursor_execute", count_sql)
        try:
            context = staffing_service.management_org_chart_context()
        finally:
            event.remove(db.engine, "before_cursor_execute", count_sql)

        self.assertEqual(context["people_count"], 1500)
        self.assertEqual(context["unassigned_count"], 1500)
        self.assertLessEqual(select_count, 5)

    def test_management_view_is_read_only_and_operational_view_remains_default(self):
        viewer = self._user("reporting_viewer", "watcher")
        self._person("R600", "division_manager")
        db.session.commit()
        self._login(viewer.username)

        dml = []

        def capture_sql(_connection, _cursor, statement, _parameters, _context, _many):
            verb = statement.lstrip().split(None, 1)[0].upper()
            if verb in {"INSERT", "UPDATE", "DELETE"}:
                dml.append(statement)

        event.listen(db.engine, "before_cursor_execute", capture_sql)
        try:
            with patch("app.neostaffing.routes.db.session.commit") as commit:
                management = self.client.get("/neostaffing/org-chart?view=management")
        finally:
            event.remove(db.engine, "before_cursor_execute", capture_sql)
        operational = self.client.get("/neostaffing/org-chart")

        self.assertEqual(management.status_code, 200)
        self.assertIn(b"MANAGEMENT TREE", management.data)
        self.assertIn(b"UNASSIGNED MANAGEMENT", management.data)
        self.assertNotIn(b"SAVE REPORTS TO", management.data)
        self.assertEqual(dml, [])
        commit.assert_not_called()
        self.assertEqual(operational.status_code, 200)
        self.assertIn(b"FULL TREE", operational.data)
        self.assertIn(b"data-org-chart-tree", operational.data)

    def test_management_view_displays_allowed_operational_mismatch(self):
        first_sort, _first_operation, _first_department, _first_area = self._hierarchy(
            "First"
        )
        _second_sort, second_operation, _second_department, _second_area = self._hierarchy(
            "Second"
        )
        division_manager = self._person("R650", "division_manager")
        manager = self._person("R651", "manager")
        db.session.add_all(
            [
                StaffingLeadershipAssignment(
                    person=division_manager,
                    unit=first_sort,
                    leadership_level="sort",
                ),
                StaffingLeadershipAssignment(
                    person=manager,
                    unit=second_operation,
                    leadership_level="operation",
                ),
                StaffingReportingRelationship(
                    person=manager,
                    reports_to_person=division_manager,
                ),
            ]
        )
        viewer = self._user("reporting_mismatch_viewer", "watcher")
        db.session.commit()
        self._login(viewer.username)

        response = self.client.get(
            f"/neostaffing/org-chart?view=management&person_id={manager.id}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            b"Operational assignments and Reports To differ. This is allowed.",
            response.data,
        )
        self.assertIn(b"STRUCTURE MISMATCH", response.data)

    def test_direct_edit_rules_block_watcher_and_pt_supervisor_but_allow_ft_supervisor(self):
        subject = self._person("R700", "manager")
        target = self._person("R701", "division_manager")
        watcher_person = self._person("R702", "full_time_supervisor")
        part_time_supervisor = self._person("R703", "part_time_supervisor")
        editor_person = self._person("R704", "full_time_supervisor")
        watcher = self._user("reporting_watcher", "watcher", watcher_person.employee_id)
        pt_editor = self._user(
            "reporting_pt_editor",
            "simulator",
            part_time_supervisor.employee_id,
        )
        ft_editor = self._user(
            "reporting_ft_editor",
            "simulator",
            editor_person.employee_id,
        )
        db.session.commit()

        watcher_client = self._logged_in_client(watcher.username)
        watcher_page = watcher_client.get(
            f"/neostaffing/org-chart?view=management&person_id={subject.id}"
        )
        self.assertNotIn(b"SAVE REPORTS TO", watcher_page.data)

        pt_client = self._logged_in_client(pt_editor.username)
        pt_response = pt_client.post(
            f"/neostaffing/app-management/reporting/{subject.id}/update",
            data={
                "reports_to_person_id": str(target.id),
                "expected_revision": "none",
            },
            follow_redirects=True,
        )
        self.assertIn(b"Direct Reports To changes require", pt_response.data)
        self.assertEqual(StaffingReportingRelationship.query.count(), 0)

        ft_client = self._logged_in_client(ft_editor.username)
        ft_response = ft_client.post(
            f"/neostaffing/app-management/reporting/{subject.id}/update",
            data={
                "reports_to_person_id": str(target.id),
                "expected_revision": "none",
            },
            follow_redirects=True,
        )
        self.assertEqual(ft_response.status_code, 200)
        self.assertIn(b"Reports To updated.", ft_response.data)
        self.assertEqual(
            StaffingReportingRelationship.query.filter_by(
                person_id=subject.id,
                reports_to_person_id=target.id,
                active=True,
            ).count(),
            1,
        )

    def test_grandmaster_can_edit_without_linked_staffing_person(self):
        subject = self._person("R800", "full_time_supervisor")
        target = self._person("R801", "manager")
        grandmaster = self._user("reporting_grandmaster", "grandmaster")
        grandmaster.role = "grandmaster"
        db.session.commit()
        self._login(grandmaster.username)

        response = self.client.post(
            f"/neostaffing/app-management/reporting/{subject.id}/update",
            data={
                "reports_to_person_id": str(target.id),
                "expected_revision": "none",
            },
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Reports To updated.", response.data)
        self.assertEqual(StaffingReportingRelationship.query.count(), 1)

    def _person(
        self,
        employee_id,
        classification,
        first_name="Test",
        last_name="Person",
    ):
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

    def _hierarchy(self, prefix):
        sort = StaffingUnit(unit_type="sort", name=f"{prefix} Sort")
        operation = StaffingUnit(
            unit_type="operation",
            name=f"{prefix} Operation",
            parent=sort,
        )
        department = StaffingUnit(
            unit_type="department",
            name=f"{prefix} Department",
            parent=operation,
        )
        work_area = StaffingUnit(
            unit_type="work_area",
            name=f"{prefix} Work Area",
            parent=department,
        )
        db.session.add_all([sort, operation, department, work_area])
        db.session.flush()
        return sort, operation, department, work_area

    def _user(self, username, app_role, employee_id=None):
        user = User(
            username=username,
            email=f"{username}@example.com",
            employee_id=employee_id or f"USER-{username}",
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

    def _login(self, username, client=None):
        client = client or self.client
        g.pop("_login_user", None)
        return client.post(
            "/login",
            data={"username": username, "password": "TestPassword123!"},
            follow_redirects=False,
        )

    def _logged_in_client(self, username):
        client = self.app.test_client()
        self._login(username, client)
        return client


if __name__ == "__main__":
    unittest.main()
