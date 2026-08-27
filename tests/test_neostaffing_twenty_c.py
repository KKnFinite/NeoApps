import unittest
from datetime import date
from unittest.mock import Mock, patch

from sqlalchemy import event, inspect, text

from app import create_app
from app.extensions import db
from app.models import (
    StaffingLeadershipAssignment,
    StaffingPerson,
    StaffingReportingRelationship,
    StaffingTwentyCAffiliation,
    StaffingUnit,
    StaffingWorkAssignment,
    User,
)
from app.services import neostaffing as staffing_service
from app.services import neostaffing_change_requests as request_service
from app.services.schema_sync import sync_local_sqlite_schema
from app.services.neostaffing_twenty_c_schema import (
    NEOSTAFFING_TWENTY_C_SCHEMA_LOCK_KEY,
    ensure_neostaffing_twenty_c_affiliation_table,
)


class NeoStaffingTwentyCFoundationTest(unittest.TestCase):
    def setUp(self):
        self.config = type(
            "NeoStaffingTwentyCConfig",
            (),
            {
                "SECRET_KEY": "test",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            },
        )
        self.app = create_app(self.config)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.sort = self._unit("Sort", "sort")
        self.op_a = self._unit("Alpha", "operation", self.sort)
        self.op_b = self._unit("Bravo", "operation", self.sort)
        self.op_c = self._unit("Charlie", "operation", self.sort)
        self.dept_a = self._unit("Alpha Dept", "department", self.op_a)
        self.dept_b = self._unit("Bravo Dept", "department", self.op_b)
        self.dept_c = self._unit("Charlie Dept", "department", self.op_c)
        self.area_a = self._unit("Alpha Area", "work_area", self.dept_a)
        self.area_b = self._unit("Bravo Area", "work_area", self.dept_b)
        self.area_c = self._unit("Charlie Area", "work_area", self.dept_c)
        self.ft_a = self._person("FT-A", "full_time_supervisor")
        self.ft_b = self._person("FT-B", "full_time_supervisor")
        self.twenty_c = self._person("20C-A", "twenty_c_full_time_supervisor")
        self._linked_user(self.ft_a)
        self._linked_user(self.ft_b)
        self.twenty_c_user = self._linked_user(self.twenty_c, role="operator")
        db.session.add_all(
            [
                StaffingLeadershipAssignment(
                    person=self.ft_a,
                    unit=self.dept_a,
                    leadership_level="department",
                    active=True,
                ),
                StaffingLeadershipAssignment(
                    person=self.ft_b,
                    unit=self.dept_b,
                    leadership_level="department",
                    active=True,
                ),
            ]
        )
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def _unit(self, name, unit_type, parent=None):
        row = StaffingUnit(
            name=name,
            unit_type=unit_type,
            parent=parent,
            active=True,
            display_order=0,
        )
        db.session.add(row)
        db.session.flush()
        return row

    def _person(self, employee_id, classification, *, active=True):
        row = StaffingPerson(
            employee_id=employee_id,
            first_name=employee_id,
            last_name="Person",
            seniority_date=date(2020, 1, 1),
            classification=classification,
            employee_status="active",
            active=active,
        )
        db.session.add(row)
        db.session.flush()
        return row

    def _linked_user(self, person, role="watcher"):
        row = User(
            username=f"user-{person.employee_id.lower()}",
            employee_id=person.employee_id.lower(),
            password_hash="unused",
            role=role,
            is_management=True,
            management_level=person.classification,
            is_active=True,
        )
        db.session.add(row)
        db.session.flush()
        return row

    def _affiliations(self):
        primary = staffing_service.create_twenty_c_affiliation(
            self.twenty_c, self.ft_a, self.sort, "primary"
        )
        secondary = staffing_service.create_twenty_c_affiliation(
            self.twenty_c, self.ft_b, self.sort, "secondary"
        )
        return primary, secondary

    def test_classification_identity_and_node_role_are_separate(self):
        self.assertIn(
            "twenty_c_full_time_supervisor",
            dict(staffing_service.classification_choices()),
        )
        self.assertEqual(
            staffing_service.CLASSIFICATION_LABELS[
                "twenty_c_full_time_supervisor"
            ],
            "20C Full-Time Supervisor",
        )
        self.assertIs(
            staffing_service.linked_user_for_person(self.twenty_c),
            self.twenty_c_user,
        )
        self.assertEqual(self.twenty_c_user.role, "operator")
        unrelated = User(
            username="operator-without-person",
            password_hash="unused",
            role="operator",
            is_management=False,
            is_active=True,
        )
        db.session.add(unrelated)
        db.session.flush()
        self.assertIsNone(unrelated.management_level)

    def test_reporting_tier_requires_ft_and_rejects_skips(self):
        self._affiliations()
        self.assertTrue(
            staffing_service.validate_reporting_relationship(
                self.twenty_c, self.ft_a
            )
        )
        manager = self._person("MGR", "manager")
        with self.assertRaisesRegex(ValueError, "must report to a Full Time Supervisor"):
            staffing_service.validate_reporting_relationship(self.twenty_c, manager)
        with self.assertRaisesRegex(ValueError, "Primary FT Supervisor"):
            staffing_service.validate_reporting_relationship(self.twenty_c, self.ft_b)

    def test_primary_secondary_uniqueness_and_history(self):
        primary, secondary = self._affiliations()
        relationship = StaffingReportingRelationship(
            person=self.twenty_c,
            reports_to_person=self.ft_a,
            active=True,
            effective_start=date.today(),
        )
        db.session.add(relationship)
        db.session.flush()
        self.assertEqual(primary.affiliation_type, "primary")
        self.assertEqual(secondary.affiliation_type, "secondary")
        with self.assertRaisesRegex(ValueError, "only one active Primary"):
            staffing_service.create_twenty_c_affiliation(
                self.twenty_c,
                self._person("FT-C", "full_time_supervisor"),
                self.sort,
                "primary",
            )
        with self.assertRaisesRegex(ValueError, "already active"):
            staffing_service.create_twenty_c_affiliation(
                self.twenty_c, self.ft_b, self.sort, "secondary"
            )
        replacement_ft = self._person("FT-C2", "full_time_supervisor")
        result = staffing_service.replace_twenty_c_primary_affiliation(
            self.twenty_c, replacement_ft, self.sort
        )
        self.assertTrue(result["reports_to_review_required"])
        self.assertFalse(primary.active)
        self.assertIsNotNone(primary.effective_end)
        self.assertEqual(result["affiliation"].ft_supervisor_person_id, replacement_ft.id)
        self.assertEqual(relationship.reports_to_person_id, self.ft_a.id)
        self.assertTrue(relationship.active)

    def test_primary_is_scoped_per_sort(self):
        self._affiliations()
        other_sort = self._unit("Day", "sort")
        other_primary = staffing_service.create_twenty_c_affiliation(
            self.twenty_c, self.ft_a, other_sort, "primary"
        )
        self.assertTrue(other_primary.active)
        self.assertEqual(
            StaffingTwentyCAffiliation.query.filter_by(
                twenty_c_person_id=self.twenty_c.id,
                affiliation_type="primary",
                active=True,
            ).count(),
            2,
        )

    def test_secondary_requires_primary(self):
        other = self._person("20C-B", "twenty_c_full_time_supervisor")
        with self.assertRaisesRegex(ValueError, "Primary"):
            staffing_service.create_twenty_c_affiliation(
                other, self.ft_b, self.sort, "secondary"
            )

    def test_inherited_scope_unions_and_deduplicates(self):
        self._affiliations()
        # An overlapping direct assignment and an outside direct assignment add scope once.
        staffing_service.create_leadership_assignment(self.twenty_c, self.area_a)
        staffing_service.create_leadership_assignment(self.twenty_c, self.dept_c)
        scope = staffing_service.twenty_c_effective_scope_unit_ids(
            self.twenty_c, self.sort
        )
        self.assertTrue({
            self.dept_a.id,
            self.area_a.id,
            self.dept_b.id,
            self.area_b.id,
            self.dept_c.id,
            self.area_c.id,
        }.issubset(scope))
        self.assertEqual(len(scope), len(set(scope)))
        self.assertEqual(StaffingWorkAssignment.query.filter_by(person_id=self.twenty_c.id).count(), 0)

    def test_direct_work_area_and_department_leadership_and_sole_supervisor(self):
        area_assignment = staffing_service.create_leadership_assignment(
            self.twenty_c, self.area_a
        )
        department_assignment = staffing_service.create_leadership_assignment(
            self.twenty_c, self.dept_b
        )
        self.assertEqual(area_assignment.leadership_level, "work_area")
        self.assertEqual(department_assignment.leadership_level, "department")
        leadership = staffing_service._board_work_area_leadership_counts(
            {
                self.area_a.id: {"twenty_c_full_time_supervisor": 1},
                self.dept_a.id: {"twenty_c_full_time_supervisor": 1},
            },
            self.sort,
            self.op_a,
            self.dept_a,
            self.area_a,
        )
        self.assertEqual(leadership["pt_supervisors"], 1)
        self.assertEqual(leadership["ft_supervisors"], 1)

    def test_work_area_candidates_accept_pt_and_twenty_c_only(self):
        pt = self._person("PT-SUP", "part_time_supervisor")
        manager = self._person("MGR-CAND", "manager")
        self._linked_user(pt)
        self._linked_user(manager)
        candidates = staffing_service.management_candidates_for_unit(self.area_a)
        self.assertEqual(
            {row["person"].classification for row in candidates},
            {"part_time_supervisor", "twenty_c_full_time_supervisor"},
        )
        department_candidates = staffing_service.management_candidates_for_unit(
            self.dept_a
        )
        self.assertIn(
            "twenty_c_full_time_supervisor",
            {row["person"].classification for row in department_candidates},
        )
        self.assertNotIn(
            "manager",
            {row["person"].classification for row in department_candidates},
        )

    def test_formal_reporting_remains_primary_with_secondary(self):
        self._affiliations()
        relationship = StaffingReportingRelationship(
            person=self.twenty_c,
            reports_to_person=self.ft_a,
            active=True,
            effective_start=date.today(),
        )
        db.session.add(relationship)
        db.session.flush()
        self.assertTrue(staffing_service._reporting_tiers_are_valid(self.twenty_c, self.ft_a))
        self.assertEqual(relationship.reports_to_person_id, self.ft_a.id)

    def test_management_tree_marks_primary_and_secondary(self):
        self._affiliations()
        db.session.add(
            StaffingReportingRelationship(
                person=self.twenty_c,
                reports_to_person=self.ft_a,
                active=True,
                effective_start=date.today(),
            )
        )
        db.session.flush()
        context = staffing_service.management_org_chart_context(self.twenty_c.id)

        def all_nodes(nodes):
            for node in nodes:
                yield node
                yield from all_nodes(node.get("children", ()))

        nodes = list(all_nodes(context["tree"] + context["unassigned_tree"]))
        primary = next(node for node in nodes if node["person"].id == self.twenty_c.id)
        self.assertEqual(primary["affiliation_type"], "primary")
        secondary_aliases = [
            alias
            for node in nodes
            for alias in node.get("affiliation_aliases", ())
            if alias["person"].id == self.twenty_c.id
        ]
        self.assertEqual([row["affiliation_type"] for row in secondary_aliases], ["secondary"])

    def test_request_routing_and_approval_authority_include_affiliated_twenty_c(self):
        self._affiliations()
        routed = request_service._route_approver_person_ids(
            self.area_a.id, None, None
        )
        self.assertEqual(set(routed), {self.ft_a.id, self.twenty_c.id})
        self.assertTrue(
            request_service._can_approve_with_context(
                self.twenty_c_user, "operator", self.twenty_c
            )
        )
        authority = request_service._management_authority_unit_ids_from_rows(
            {
                self.ft_a.id: [self.ft_a.leadership_assignments[0]],
                self.ft_b.id: [self.ft_b.leadership_assignments[0]],
            },
            StaffingTwentyCAffiliation.query.filter_by(active=True).all(),
        )
        self.assertEqual(authority[self.twenty_c.id], {self.dept_a.id, self.dept_b.id})

    def test_inactive_status_preserves_relationships_and_assignments(self):
        primary, secondary = self._affiliations()
        direct = staffing_service.create_leadership_assignment(self.twenty_c, self.area_a)
        relationship = StaffingReportingRelationship(
            person=self.twenty_c,
            reports_to_person=self.ft_a,
            active=True,
            effective_start=date.today(),
        )
        db.session.add(relationship)
        db.session.flush()
        staffing_service.toggle_person_active(self.twenty_c)
        self.assertFalse(self.twenty_c.active)
        self.assertTrue(primary.active)
        self.assertTrue(secondary.active)
        self.assertTrue(direct.active)
        self.assertTrue(relationship.active)
        staffing_service.toggle_person_active(self.ft_a)
        self.assertFalse(self.ft_a.active)
        self.assertTrue(relationship.active)

    def test_management_context_read_is_write_free(self):
        self._affiliations()
        writes = []

        def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
            if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
                writes.append(statement)

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            staffing_service.management_org_chart_context()
            staffing_service.twenty_c_effective_scope_unit_ids(self.twenty_c, self.sort)
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)
        self.assertEqual(writes, [])

    def test_scope_resolution_query_count_is_bounded(self):
        self._affiliations()
        selects = []

        def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
            if statement.lstrip().upper().startswith("SELECT"):
                selects.append(statement)

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            staffing_service.twenty_c_effective_scope_unit_ids(
                self.twenty_c, self.sort
            )
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)
        self.assertLessEqual(len(selects), 4)

    def test_schema_model_and_local_sync_are_additive(self):
        table = StaffingTwentyCAffiliation.__table__
        self.assertEqual(table.name, "staffing_twenty_c_affiliations")
        indexes = {index.name: index for index in table.indexes}
        self.assertTrue(indexes["uq_staffing_twenty_c_affiliations_active_primary"].unique)
        self.assertTrue(indexes["uq_staffing_twenty_c_affiliations_active_target"].unique)
        db.session.execute(text("DROP TABLE staffing_twenty_c_affiliations"))
        db.session.commit()
        sync_local_sqlite_schema(self.app)
        self.assertIn("staffing_twenty_c_affiliations", inspect(db.engine).get_table_names())

    def test_postgresql_schema_ensure_is_targeted_and_lock_bounded(self):
        self.app.config.update(
            TESTING=False,
            SQLALCHEMY_DATABASE_URI="postgresql://example.test/neoapps",
        )
        connection = Mock()
        with (
            patch(
                "app.services.neostaffing_twenty_c_schema.db.session.connection",
                return_value=connection,
            ),
            patch(
                "app.services.neostaffing_twenty_c_schema.db.session.commit"
            ) as commit,
            patch.object(
                StaffingTwentyCAffiliation.__table__, "create"
            ) as create_table,
        ):
            self.assertTrue(
                ensure_neostaffing_twenty_c_affiliation_table(self.app)
            )
        statements = "\n".join(
            str(call.args[0]) for call in connection.execute.call_args_list
        )
        self.assertIn("SET LOCAL lock_timeout", statements)
        self.assertIn("pg_advisory_xact_lock", statements)
        self.assertEqual(
            connection.execute.call_args_list[1].args[1]["lock_key"],
            NEOSTAFFING_TWENTY_C_SCHEMA_LOCK_KEY,
        )
        create_table.assert_called_once_with(bind=connection, checkfirst=True)
        commit.assert_called_once()

    def test_factory_invokes_twenty_c_schema_ensure(self):
        with patch(
            "app.ensure_neostaffing_twenty_c_affiliation_table"
        ) as ensure:
            app = create_app(self.config)
        ensure.assert_called_once_with(app)


if __name__ == "__main__":
    unittest.main()
