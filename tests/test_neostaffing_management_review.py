from datetime import date, datetime
import re
import unittest

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
from app.services import neostaffing_management_review as review_service
from app.services.password_policy import set_user_password


class NeoStaffingManagementReviewTest(unittest.TestCase):
    def setUp(self):
        config = type(
            "NeoStaffingManagementReviewConfig",
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

    def test_pt_supervisor_work_area_suggestions_cover_one_many_and_fallback(self):
        _sort, _operation, department, work_area = self._hierarchy("Primary")
        subject = self._person("MR100", "part_time_supervisor")
        first = self._person("MR101", "full_time_supervisor", "First", "Owner")
        second = self._person("MR102", "full_time_supervisor", "Second", "Owner")
        fallback = self._person("MR103", "full_time_supervisor", "Fallback", "Owner")
        self._linked_user(subject, "mr_subject")
        self._lead(first, department)
        db.session.commit()

        mutation = review_service.assignment_add_mutation(
            subject.id,
            work_area.id,
            "work_area",
        )
        review = review_service.prepare_management_relationship_review(mutation)
        self.assertEqual(
            [row.id for row in review["rows"][0]["suggested_people"]],
            [first.id],
        )
        self.assertFalse(review["rows"][0]["ambiguous"])

        self._lead(second, department)
        db.session.commit()
        review = review_service.prepare_management_relationship_review(mutation)
        self.assertEqual(
            {row.id for row in review["rows"][0]["suggested_people"]},
            {first.id, second.id},
        )
        self.assertTrue(review["rows"][0]["ambiguous"])

        StaffingLeadershipAssignment.query.update({"active": False})
        db.session.commit()
        review = review_service.prepare_management_relationship_review(mutation)
        self.assertEqual(
            {row.id for row in review["rows"][0]["suggested_people"]},
            {first.id, second.id, fallback.id},
        )

    def test_ft_supervisor_and_manager_suggestions_follow_parent_owners(self):
        sort, operation, department, _work_area = self._hierarchy("Tiered")
        supervisor = self._person("MR200", "full_time_supervisor")
        owning_manager = self._person("MR201", "manager")
        manager = self._person("MR203", "manager")
        division_manager = self._person("MR202", "division_manager")
        self._linked_user(supervisor, "mr_ft")
        self._linked_user(manager, "mr_manager")
        self._lead(owning_manager, operation)
        self._lead(division_manager, sort)
        db.session.commit()

        supervisor_review = review_service.prepare_management_relationship_review(
            review_service.assignment_add_mutation(
                supervisor.id,
                department.id,
                "department",
            )
        )
        self.assertEqual(
            [row.id for row in supervisor_review["rows"][0]["suggested_people"]],
            [owning_manager.id],
        )

        manager_review = review_service.prepare_management_relationship_review(
            review_service.assignment_add_mutation(
                manager.id,
                operation.id,
                "operation",
            )
        )
        self.assertEqual(
            [row.id for row in manager_review["rows"][0]["suggested_people"]],
            [division_manager.id],
        )

    def test_keep_current_applies_operational_assignment_without_changing_reports_to(self):
        _sort, _operation, department, work_area = self._hierarchy("Keep")
        subject = self._person("MR300", "part_time_supervisor")
        current = self._person("MR301", "full_time_supervisor", "Current", "Supervisor")
        suggested = self._person("MR302", "full_time_supervisor", "Suggested", "Supervisor")
        self._linked_user(subject, "mr_keep")
        self._lead(suggested, department)
        relationship = self._relationship(subject, current)
        db.session.commit()

        mutation = review_service.assignment_add_mutation(subject.id, work_area.id, "work_area")
        review = review_service.prepare_management_relationship_review(mutation)
        row = review["rows"][0]
        review_service.apply_management_relationship_review(
            mutation,
            review["revision"],
            {
                subject.id: {
                    "action": "keep",
                    "reports_to_person_id": None,
                    "expected_revision": row["relationship_revision"],
                }
            },
        )
        db.session.commit()

        self.assertEqual(
            StaffingLeadershipAssignment.query.filter_by(
                person_id=subject.id,
                unit_id=work_area.id,
                active=True,
            ).count(),
            1,
        )
        self.assertEqual(
            StaffingReportingRelationship.query.filter_by(
                person_id=subject.id,
                active=True,
            ).one().reports_to_person_id,
            current.id,
        )
        self.assertTrue(relationship.active)

    def test_explicit_suggested_and_different_valid_targets_are_applied(self):
        _sort, _operation, department, first_area = self._hierarchy("Change")
        second_area = StaffingUnit(
            unit_type="work_area",
            name="Second Area",
            parent=department,
        )
        suggested = self._person("MR400", "full_time_supervisor", "Suggested", "Supervisor")
        different = self._person("MR401", "full_time_supervisor", "Different", "Supervisor")
        first_subject = self._person("MR402", "part_time_supervisor", "First", "Subject")
        second_subject = self._person("MR403", "part_time_supervisor", "Second", "Subject")
        db.session.add(second_area)
        self._linked_user(first_subject, "mr_change_first")
        self._linked_user(second_subject, "mr_change_second")
        self._lead(suggested, department)
        db.session.commit()

        for subject, area, target in (
            (first_subject, first_area, suggested),
            (second_subject, second_area, different),
        ):
            mutation = review_service.assignment_add_mutation(subject.id, area.id, "work_area")
            review = review_service.prepare_management_relationship_review(mutation)
            row = review["rows"][0]
            review_service.apply_management_relationship_review(
                mutation,
                review["revision"],
                {
                    subject.id: {
                        "action": "change",
                        "reports_to_person_id": target.id,
                        "expected_revision": row["relationship_revision"],
                    }
                },
            )
            db.session.commit()

        self.assertEqual(
            StaffingReportingRelationship.query.filter_by(
                person_id=first_subject.id,
                active=True,
            ).one().reports_to_person_id,
            suggested.id,
        )
        self.assertEqual(
            StaffingReportingRelationship.query.filter_by(
                person_id=second_subject.id,
                active=True,
            ).one().reports_to_person_id,
            different.id,
        )

    def test_multiple_assignments_show_all_likely_supervisors_without_guessing(self):
        _sort, _operation, first_department, first_area = self._hierarchy("Multi One")
        _sort2, _operation2, second_department, second_area = self._hierarchy("Multi Two")
        subject = self._person("MR500", "part_time_supervisor")
        first_owner = self._person("MR501", "full_time_supervisor", "First", "Owner")
        second_owner = self._person("MR502", "full_time_supervisor", "Second", "Owner")
        mismatch = self._person("MR503", "full_time_supervisor", "Intentional", "Mismatch")
        self._linked_user(subject, "mr_multi")
        self._lead(subject, first_area)
        self._lead(first_owner, first_department)
        self._lead(second_owner, second_department)
        self._relationship(subject, mismatch)
        db.session.commit()

        mutation = review_service.assignment_add_mutation(subject.id, second_area.id, "work_area")
        review = review_service.prepare_management_relationship_review(mutation)
        row = review["rows"][0]
        self.assertTrue(row["ambiguous"])
        self.assertEqual(
            {person.id for person in row["suggested_people"]},
            {first_owner.id, second_owner.id},
        )
        self.assertEqual(row["current_reports_to"].id, mismatch.id)

        review_service.apply_management_relationship_review(
            mutation,
            review["revision"],
            {
                subject.id: {
                    "action": "keep",
                    "expected_revision": row["relationship_revision"],
                }
            },
        )
        db.session.commit()
        self.assertEqual(
            StaffingReportingRelationship.query.filter_by(
                person_id=subject.id,
                active=True,
            ).one().reports_to_person_id,
            mismatch.id,
        )

    def test_unchanged_likely_relationship_does_not_prompt_or_replace_mismatch(self):
        _sort, _operation, department, first_area = self._hierarchy("Stable")
        second_area = StaffingUnit(
            unit_type="work_area",
            name="Stable Second Area",
            parent=department,
        )
        subject = self._person("MR550", "part_time_supervisor")
        likely_owner = self._person("MR551", "full_time_supervisor", "Likely", "Owner")
        intentional = self._person("MR552", "full_time_supervisor", "Intentional", "Reports To")
        db.session.add(second_area)
        self._linked_user(subject, "mr_stable")
        self._lead(subject, first_area)
        self._lead(likely_owner, department)
        self._relationship(subject, intentional)
        db.session.commit()

        mutation = review_service.assignment_add_mutation(subject.id, second_area.id, "work_area")
        review = review_service.prepare_management_relationship_review(mutation)

        self.assertFalse(review["required"])
        staffing_service.create_leadership_assignment(subject, second_area, "work_area")
        db.session.commit()
        self.assertEqual(
            StaffingReportingRelationship.query.filter_by(
                person_id=subject.id,
                active=True,
            ).one().reports_to_person_id,
            intentional.id,
        )

    def test_structural_move_uses_one_consolidated_review(self):
        _sort, operation, source_department, work_area = self._hierarchy("Structure")
        destination_department = StaffingUnit(
            unit_type="department",
            name="Destination Department",
            parent=operation,
        )
        source_owner = self._person("MR600", "full_time_supervisor", "Source", "Owner")
        destination_owner = self._person("MR601", "full_time_supervisor", "Destination", "Owner")
        first_subject = self._person("MR602", "part_time_supervisor", "First", "Subject")
        second_subject = self._person("MR603", "part_time_supervisor", "Second", "Subject")
        db.session.add(destination_department)
        self._lead(source_owner, source_department)
        self._lead(destination_owner, destination_department)
        self._lead(first_subject, work_area)
        self._lead(second_subject, work_area)
        self._relationship(first_subject, source_owner)
        self._relationship(second_subject, source_owner)
        db.session.commit()

        normalized = staffing_service.validated_unit_update_values(
            work_area,
            {
                "unit_type": "work_area",
                "name": work_area.name,
                "parent_id": destination_department.id,
                "display_order": work_area.display_order,
                "active": "1",
                "required_headcount": "",
            },
        )
        mutation = review_service.unit_update_mutation(work_area, normalized)
        review = review_service.prepare_management_relationship_review(mutation)
        self.assertTrue(review["consolidated"])
        self.assertEqual({row["person"].id for row in review["rows"]}, {first_subject.id, second_subject.id})

        rows = {row["person"].id: row for row in review["rows"]}
        review_service.apply_management_relationship_review(
            mutation,
            review["revision"],
            {
                first_subject.id: {
                    "action": "keep",
                    "expected_revision": rows[first_subject.id]["relationship_revision"],
                },
                second_subject.id: {
                    "action": "change",
                    "reports_to_person_id": destination_owner.id,
                    "expected_revision": rows[second_subject.id]["relationship_revision"],
                },
            },
        )
        db.session.commit()

        self.assertEqual(work_area.parent_id, destination_department.id)
        active = {
            row.person_id: row.reports_to_person_id
            for row in StaffingReportingRelationship.query.filter_by(active=True).all()
        }
        self.assertEqual(active[first_subject.id], source_owner.id)
        self.assertEqual(active[second_subject.id], destination_owner.id)

    def test_stale_review_cannot_overwrite_newer_relationship(self):
        _sort, _operation, department, work_area = self._hierarchy("Stale")
        subject = self._person("MR700", "part_time_supervisor")
        first = self._person("MR701", "full_time_supervisor")
        second = self._person("MR702", "full_time_supervisor")
        self._linked_user(subject, "mr_stale")
        self._lead(first, department)
        relationship = self._relationship(subject, first)
        db.session.commit()

        mutation = review_service.assignment_add_mutation(subject.id, work_area.id, "work_area")
        review = review_service.prepare_management_relationship_review(mutation)
        relationship.reports_to_person_id = second.id
        relationship.updated_at = datetime(2026, 8, 15, 12, 0, 0)
        db.session.commit()

        with self.assertRaisesRegex(ValueError, "changed while you were reviewing"):
            review_service.apply_management_relationship_review(
                mutation,
                review["revision"],
                {
                    subject.id: {
                        "action": "keep",
                        "expected_revision": review["rows"][0]["relationship_revision"],
                    }
                },
            )
        db.session.rollback()
        self.assertEqual(
            StaffingLeadershipAssignment.query.filter_by(person_id=subject.id).count(),
            0,
        )
        self.assertEqual(
            StaffingReportingRelationship.query.filter_by(
                person_id=subject.id,
                active=True,
            ).one().reports_to_person_id,
            second.id,
        )

    def test_pt_supervisor_master_cannot_bypass_direct_management_authority(self):
        _sort, _operation, _department, work_area = self._hierarchy("Authority")
        subject = self._person("MR800", "part_time_supervisor")
        editor_person = self._person("MR801", "part_time_supervisor")
        editor = self._linked_user(editor_person, "mr_pt_master", app_role="master")
        self._linked_user(subject, "mr_authority_subject")
        db.session.commit()
        self._login(editor.username)

        response = self.client.post(
            "/neostaffing/app-management/management-assignments",
            data={
                "person_id": subject.id,
                "unit_id": work_area.id,
                "leadership_level": "work_area",
                "return_unit_id": work_area.id,
            },
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Direct management assignment changes require", response.data)
        self.assertEqual(StaffingLeadershipAssignment.query.count(), 0)

    def test_direct_editor_receives_review_and_views_remain_operational(self):
        _sort, _operation, department, work_area = self._hierarchy("Route")
        subject = self._person("MR900", "part_time_supervisor", "Review", "Subject")
        owner = self._person("MR901", "full_time_supervisor", "Review", "Owner")
        editor_person = self._person("MR902", "full_time_supervisor")
        editor = self._linked_user(editor_person, "mr_ft_editor", app_role="master")
        self._linked_user(subject, "mr_route_subject")
        self._lead(owner, department)
        db.session.commit()
        self._login(editor.username)

        response = self.client.post(
            "/neostaffing/app-management/management-assignments",
            data={
                "person_id": subject.id,
                "unit_id": work_area.id,
                "leadership_level": "work_area",
                "return_unit_id": work_area.id,
            },
        )
        operational = self.client.get(f"/neostaffing/org-chart?unit_id={work_area.id}")
        management = self.client.get(
            f"/neostaffing/org-chart?view=management&person_id={subject.id}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Management Relationships Affected", response.data)
        self.assertIn(b"Current Reports To", response.data)
        self.assertIn(b"Suggested Reports To", response.data)
        self.assertIn(b"Keep Current", response.data)
        self.assertIn(b"Change to Suggested", response.data)
        self.assertIn(b"Choose Different Valid Supervisor", response.data)
        self.assertEqual(StaffingLeadershipAssignment.query.filter_by(person_id=subject.id).count(), 0)
        self.assertIn(b"FULL TREE", operational.data)
        self.assertIn(b"MANAGEMENT TREE", management.data)

    def test_structural_move_route_renders_one_review_without_mutating(self):
        _sort, operation, source_department, work_area = self._hierarchy(
            "Route Structure"
        )
        destination_department = StaffingUnit(
            unit_type="department",
            name="Route Destination Department",
            parent=operation,
        )
        source_owner = self._person(
            "MR920", "full_time_supervisor", "Source", "Route Owner"
        )
        destination_owner = self._person(
            "MR921", "full_time_supervisor", "Destination", "Route Owner"
        )
        first_subject = self._person(
            "MR922", "part_time_supervisor", "First", "Route Subject"
        )
        second_subject = self._person(
            "MR923", "part_time_supervisor", "Second", "Route Subject"
        )
        editor_person = self._person("MR924", "full_time_supervisor")
        editor = self._linked_user(
            editor_person,
            "mr_structure_editor",
            app_role="master",
        )
        db.session.add(destination_department)
        self._lead(source_owner, source_department)
        self._lead(destination_owner, destination_department)
        self._lead(first_subject, work_area)
        self._lead(second_subject, work_area)
        self._relationship(first_subject, source_owner)
        self._relationship(second_subject, source_owner)
        db.session.commit()
        self._login(editor.username)

        response = self.client.post(
            f"/neostaffing/app-management/hierarchy/units/{work_area.id}/update",
            data={
                "unit_type": "work_area",
                "name": work_area.name,
                "parent_id": destination_department.id,
                "display_order": work_area.display_order,
                "active": "1",
                "required_headcount": "",
                "return_unit_id": work_area.id,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Management Relationships Affected", response.data)
        self.assertIn(b"CONSOLIDATED REVIEW", response.data)
        self.assertIn(first_subject.full_name.encode(), response.data)
        self.assertIn(second_subject.full_name.encode(), response.data)
        self.assertIn(destination_owner.full_name.encode(), response.data)
        db.session.expire_all()
        self.assertEqual(
            db.session.get(StaffingUnit, work_area.id).parent_id,
            source_department.id,
        )
        active_relationships = {
            row.person_id: row.reports_to_person_id
            for row in StaffingReportingRelationship.query.filter_by(active=True).all()
        }
        self.assertEqual(active_relationships[first_subject.id], source_owner.id)
        self.assertEqual(active_relationships[second_subject.id], source_owner.id)

    def test_review_route_applies_selected_suggestion_after_revalidation(self):
        _sort, _operation, department, work_area = self._hierarchy("Route Apply")
        subject = self._person("MR950", "part_time_supervisor", "Apply", "Subject")
        owner = self._person("MR951", "full_time_supervisor", "Apply", "Owner")
        editor_person = self._person("MR952", "full_time_supervisor")
        editor = self._linked_user(editor_person, "mr_apply_editor", app_role="master")
        self._linked_user(subject, "mr_apply_subject")
        self._lead(owner, department)
        db.session.commit()
        self._login(editor.username)

        preview = self.client.post(
            "/neostaffing/app-management/management-assignments",
            data={
                "person_id": subject.id,
                "unit_id": work_area.id,
                "leadership_level": "work_area",
                "return_unit_id": work_area.id,
            },
        )
        revision = re.search(
            rb'name="review_revision" value="([^"]+)"',
            preview.data,
        ).group(1).decode()
        relationship_revision = re.search(
            rb'name="relationship_revision_' + str(subject.id).encode() + rb'" value="([^"]+)"',
            preview.data,
        ).group(1).decode()

        applied = self.client.post(
            "/neostaffing/app-management/management-review/apply",
            data={
                "kind": "add_assignment",
                "person_id": subject.id,
                "unit_id": work_area.id,
                "leadership_level": "work_area",
                "review_revision": revision,
                "return_endpoint": "neostaffing.org_chart",
                "return_value_unit_id": work_area.id,
                "affected_person_ids": str(subject.id),
                f"relationship_revision_{subject.id}": relationship_revision,
                f"relationship_action_{subject.id}": f"target:{owner.id}",
            },
            follow_redirects=False,
        )

        self.assertEqual(applied.status_code, 302)
        self.assertEqual(applied.location, f"/neostaffing/org-chart?unit_id={work_area.id}")
        self.assertEqual(
            StaffingLeadershipAssignment.query.filter_by(
                person_id=subject.id,
                unit_id=work_area.id,
                active=True,
            ).count(),
            1,
        )
        self.assertEqual(
            StaffingReportingRelationship.query.filter_by(
                person_id=subject.id,
                active=True,
            ).one().reports_to_person_id,
            owner.id,
        )

    def test_review_queries_remain_bounded_with_1500_management_people(self):
        _sort, _operation, _department, work_area = self._hierarchy("Scale")
        subject = self._person("MR-LARGE-SUBJECT", "part_time_supervisor")
        self._linked_user(subject, "mr_large_subject")
        db.session.add_all(
            [
                StaffingPerson(
                    employee_id=f"MR-LARGE-{index:04d}",
                    first_name="Large",
                    last_name=f"Supervisor {index:04d}",
                    seniority_date=date(2020, 1, 1),
                    classification="full_time_supervisor",
                    employee_status="active",
                    active=True,
                )
                for index in range(1500)
            ]
        )
        db.session.commit()
        subject_id = subject.id
        work_area_id = work_area.id
        db.session.expunge_all()

        select_count = 0

        def count_sql(_connection, _cursor, statement, _parameters, _context, _many):
            nonlocal select_count
            if statement.lstrip().upper().startswith("SELECT"):
                select_count += 1

        event.listen(db.engine, "before_cursor_execute", count_sql)
        try:
            review = review_service.prepare_management_relationship_review(
                review_service.assignment_add_mutation(
                    subject_id,
                    work_area_id,
                    "work_area",
                )
            )
        finally:
            event.remove(db.engine, "before_cursor_execute", count_sql)

        self.assertEqual(len(review["rows"][0]["valid_candidates"]), 1500)
        self.assertLessEqual(select_count, 8)

    def test_consolidated_structural_review_queries_do_not_scale_per_person(self):
        _sort, operation, source_department, work_area = self._hierarchy("Bulk Review")
        destination_department = StaffingUnit(
            unit_type="department",
            name="Bulk Destination",
            parent=operation,
        )
        source_owner = self._person("MR-BULK-SOURCE", "full_time_supervisor")
        destination_owner = self._person("MR-BULK-DEST", "full_time_supervisor")
        db.session.add(destination_department)
        self._lead(source_owner, source_department)
        self._lead(destination_owner, destination_department)
        subjects = [
            self._person(f"MR-BULK-{index:04d}", "part_time_supervisor")
            for index in range(300)
        ]
        for subject in subjects:
            self._lead(subject, work_area)
            self._relationship(subject, source_owner)
        db.session.commit()

        normalized = staffing_service.validated_unit_update_values(
            work_area,
            {
                "unit_type": "work_area",
                "name": work_area.name,
                "parent_id": destination_department.id,
                "display_order": work_area.display_order,
                "active": "1",
                "required_headcount": "",
            },
        )
        mutation = review_service.unit_update_mutation(work_area, normalized)
        select_count = 0

        def count_sql(_connection, _cursor, statement, _parameters, _context, _many):
            nonlocal select_count
            if statement.lstrip().upper().startswith("SELECT"):
                select_count += 1

        event.listen(db.engine, "before_cursor_execute", count_sql)
        try:
            review = review_service.prepare_management_relationship_review(mutation)
        finally:
            event.remove(db.engine, "before_cursor_execute", count_sql)

        self.assertEqual(len(review["rows"]), 300)
        self.assertTrue(review["consolidated"])
        self.assertLessEqual(select_count, 5)

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

    def _person(self, employee_id, classification, first_name="Test", last_name="Person"):
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

    def _relationship(self, person, reports_to):
        relationship = StaffingReportingRelationship(
            person=person,
            reports_to_person=reports_to,
            active=True,
        )
        db.session.add(relationship)
        db.session.flush()
        return relationship

    def _linked_user(self, person, username, app_role="watcher"):
        user = User(
            username=username,
            email=f"{username}@example.com",
            employee_id=person.employee_id,
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

    def _login(self, username):
        g.pop("_login_user", None)
        return self.client.post(
            "/login",
            data={"username": username, "password": "TestPassword123!"},
            follow_redirects=False,
        )


if __name__ == "__main__":
    unittest.main()
