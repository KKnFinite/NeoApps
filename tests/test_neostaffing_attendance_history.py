from datetime import date, datetime
import inspect as python_inspect
import unittest
from unittest.mock import Mock, patch

from sqlalchemy import event, inspect

from app import create_app
from app.extensions import db
from app.models import (
    Gateway,
    SortDateOperation,
    StaffingAttendanceSummary,
    StaffingDailyAttendance,
    StaffingPerson,
    StaffingUnit,
    StaffingWorkAssignment,
)
from app.neostaffing import routes as staffing_routes
from app.services import neostaffing_attendance_history as history_service
from app.services.neostaffing_attendance_history_schema import (
    ensure_neostaffing_attendance_summary_table,
)


class NeoStaffingAttendanceHistoryTest(unittest.TestCase):
    PRIOR_DATE = date(2026, 8, 23)
    CURRENT_DATE = date(2026, 8, 24)

    def setUp(self):
        self.config = type(
            "NeoStaffingAttendanceHistoryConfig",
            (),
            {
                "SECRET_KEY": "test",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
                "DEFAULT_GATEWAY_CODE": "RFD",
            },
        )
        self.app = create_app(self.config)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()

        self.gateway = Gateway(code="RFD", name="Rockford", is_active=True)
        self.staffing_sort = StaffingUnit(
            unit_type="sort", name="Night", active=True, display_order=1
        )
        self.ramp = StaffingUnit(
            unit_type="operation",
            name="Ramp",
            parent=self.staffing_sort,
            active=True,
            display_order=1,
        )
        self.hub = StaffingUnit(
            unit_type="operation",
            name="Hub",
            parent=self.staffing_sort,
            active=True,
            display_order=2,
        )
        self.outbound = StaffingUnit(
            unit_type="department",
            name="Outbound",
            parent=self.hub,
            active=True,
            display_order=1,
        )
        self.ramp_area = StaffingUnit(
            unit_type="work_area",
            name="Ramp Direct",
            parent=self.ramp,
            active=True,
        )
        self.hub_area = StaffingUnit(
            unit_type="work_area",
            name="Door 9",
            parent=self.outbound,
            active=True,
        )
        db.session.add_all(
            [
                self.gateway,
                self.staffing_sort,
                self.ramp,
                self.hub,
                self.outbound,
                self.ramp_area,
                self.hub_area,
            ]
        )
        db.session.flush()
        self.prior = self._operation(self.PRIOR_DATE)
        self.current = self._operation(self.CURRENT_DATE)
        self.ramp_here = self._person("HS100", self.ramp_area)
        self.hub_here = self._person("HS101", self.hub_area)
        self.hub_absent = self._person("HS102", self.hub_area)
        self.hub_unmarked = self._person("HS103", self.hub_area)
        db.session.flush()
        self._attendance(self.ramp_here, self.ramp_area, "here", self.ramp)
        self._attendance(
            self.hub_here,
            self.hub_area,
            "here",
            self.hub,
            self.outbound,
        )
        self._attendance(
            self.hub_absent,
            self.hub_area,
            "call_in",
            self.hub,
            self.outbound,
        )
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def _operation(self, sort_date):
        operation = SortDateOperation(
            gateway=self.gateway,
            gateway_code="RFD",
            sort_name="night",
            sort_date=sort_date,
        )
        db.session.add(operation)
        db.session.flush()
        return operation

    def _person(self, employee_id, work_area):
        person = StaffingPerson(
            employee_id=employee_id,
            first_name="Test",
            last_name=employee_id,
            seniority_date=date(2020, 1, 1),
            classification="part_time",
            employee_status="active",
            active=True,
        )
        db.session.add(person)
        db.session.flush()
        db.session.add(
            StaffingWorkAssignment(
                person_id=person.id,
                work_area_unit_id=work_area.id,
                active=True,
            )
        )
        return person

    def _attendance(self, person, area, status, operation, department=None):
        record = StaffingDailyAttendance(
            person_id=person.id,
            attendance_date=self.prior.sort_date,
            sort_unit_id=self.staffing_sort.id,
            work_area_unit_id=area.id,
            department_unit_id=department.id if department else None,
            operation_unit_id=operation.id,
            sort_date_operation_id=self.prior.id,
            status=status,
        )
        db.session.add(record)
        return record

    def _summary(self, scope):
        return StaffingAttendanceSummary.query.filter_by(
            sort_date_operation_id=self.prior.id,
            scope_type=scope.unit_type,
            scope_unit_id=scope.id,
        ).one()

    def test_operation_and_department_summaries_store_payroll_and_worked_only(self):
        result = history_service.finalize_attendance_summaries(self.prior)
        db.session.commit()

        self.assertEqual(result.summary_count, 3)
        self.assertEqual(
            (self._summary(self.ramp).on_payroll_count, self._summary(self.ramp).worked_count),
            (1, 1),
        )
        self.assertEqual(
            (self._summary(self.hub).on_payroll_count, self._summary(self.hub).worked_count),
            (3, 1),
        )
        self.assertEqual(
            (
                self._summary(self.outbound).on_payroll_count,
                self._summary(self.outbound).worked_count,
            ),
            (3, 1),
        )
        columns = set(StaffingAttendanceSummary.__table__.columns.keys())
        self.assertFalse(
            columns
            & {
                "person_id",
                "employee_id",
                "status",
                "absence_counts",
                "work_area_unit_id",
            }
        )

    def test_finalization_is_idempotent_and_post_close_correction_recalculates(self):
        first = history_service.finalize_attendance_summaries(self.prior)
        db.session.commit()
        first_ids = {row.id for row in StaffingAttendanceSummary.query.all()}

        second = history_service.finalize_attendance_summaries(self.prior)
        db.session.commit()
        self.assertEqual(second.summary_count, first.summary_count)
        self.assertEqual(
            {row.id for row in StaffingAttendanceSummary.query.all()},
            first_ids,
        )

        record = StaffingDailyAttendance.query.filter_by(
            person_id=self.hub_absent.id
        ).one()
        record.status = "here"
        db.session.commit()
        history_service.finalize_attendance_summaries(self.prior)
        db.session.commit()
        self.assertEqual(self._summary(self.hub).worked_count, 2)
        self.assertEqual(self._summary(self.outbound).worked_count, 2)

    def test_next_active_sort_finalizes_then_purges_prior_details(self):
        with patch.object(history_service, "operation_is_active_at", return_value=True):
            result = history_service.process_attendance_rollover(
                self.current,
                now_local=datetime(2026, 8, 24, 21, 0),
            )
        db.session.commit()

        self.assertEqual(result.status, "processed")
        self.assertEqual(result.finalized_summary_count, 3)
        self.assertEqual(result.purged_detail_count, 3)
        self.assertEqual(StaffingDailyAttendance.query.count(), 0)
        self.assertEqual(StaffingAttendanceSummary.query.count(), 3)

    def test_rollover_purges_legacy_unlinked_rows_after_prior_finalization(self):
        history_service.finalize_attendance_summaries(self.prior)
        for record in StaffingDailyAttendance.query.all():
            record.sort_date_operation_id = None
        db.session.commit()

        with patch.object(history_service, "operation_is_active_at", return_value=True):
            result = history_service.process_attendance_rollover(self.current)
        db.session.commit()

        self.assertEqual(result.status, "processed")
        self.assertEqual(result.purged_detail_count, 3)
        self.assertEqual(StaffingDailyAttendance.query.count(), 0)

    def test_midnight_alone_does_not_trigger_detail_purge(self):
        with patch.object(history_service, "operation_is_active_at", return_value=False):
            result = history_service.process_attendance_rollover(
                self.current,
                now_local=datetime(2026, 8, 24, 0, 1),
            )

        self.assertEqual(result.status, "current_operation_not_active")
        self.assertEqual(StaffingDailyAttendance.query.count(), 3)
        self.assertEqual(StaffingAttendanceSummary.query.count(), 0)

    def test_summary_failure_prevents_any_detail_purge(self):
        with (
            patch.object(history_service, "operation_is_active_at", return_value=True),
            patch.object(
                history_service,
                "finalize_attendance_summaries",
                side_effect=RuntimeError("summary failed"),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "summary failed"):
                history_service.process_attendance_rollover(self.current)
        db.session.rollback()

        self.assertEqual(StaffingDailyAttendance.query.count(), 3)
        self.assertEqual(StaffingAttendanceSummary.query.count(), 0)

    def test_database_statements_finalize_before_detail_delete(self):
        statements = []

        def capture(_connection, _cursor, statement, _parameters, _context, _many):
            normalized = " ".join(statement.casefold().split())
            if normalized.startswith(("insert", "update", "delete")):
                statements.append(normalized)

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            with patch.object(history_service, "operation_is_active_at", return_value=True):
                history_service.process_attendance_rollover(self.current)
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)

        summary_positions = [
            index
            for index, statement in enumerate(statements)
            if "staffing_attendance_summaries" in statement
        ]
        detail_delete = next(
            index
            for index, statement in enumerate(statements)
            if statement.startswith("delete from staffing_daily_attendance")
        )
        self.assertTrue(summary_positions)
        self.assertLess(max(summary_positions), detail_delete)

    def test_one_year_retention_cleanup_is_bounded(self):
        history_service.finalize_attendance_summaries(self.prior)
        db.session.commit()
        old_operation = self._operation(date(2025, 8, 23))
        for index in range(3):
            db.session.add(
                StaffingAttendanceSummary(
                    sort_date_operation_id=old_operation.id,
                    attendance_date=date(2025, 8, 23),
                    scope_type="operation",
                    scope_unit_id=self.ramp.id if index == 0 else self.hub.id,
                    on_payroll_count=index,
                    worked_count=0,
                )
            )
            if index == 1:
                break
        db.session.commit()

        deleted = history_service.purge_expired_attendance_summaries(
            as_of=date(2026, 8, 24),
            batch_size=1,
        )
        db.session.commit()
        self.assertEqual(deleted, 1)
        self.assertEqual(
            StaffingAttendanceSummary.query.filter_by(
                sort_date_operation_id=self.prior.id
            ).count(),
            3,
        )
        self.assertEqual(
            StaffingAttendanceSummary.query.filter_by(
                sort_date_operation_id=old_operation.id
            ).count(),
            1,
        )

    def test_finalization_queries_are_bounded_across_all_scopes(self):
        prior_id = self.prior.id
        statements = []

        def capture(_connection, _cursor, statement, _parameters, _context, _many):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            history_service.finalize_attendance_summaries(prior_id)
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)

        self.assertLessEqual(len(statements), 7)

    def test_schema_is_additive_idempotent_and_factory_invokes_ensure(self):
        self.assertIn(
            "staffing_attendance_summaries",
            set(inspect(db.engine).get_table_names()),
        )
        self.app.config.update(
            TESTING=False,
            SQLALCHEMY_DATABASE_URI="postgresql://example.test/neoapps",
        )
        connection = Mock()
        with (
            patch(
                "app.services.neostaffing_attendance_history_schema.db.session.connection",
                return_value=connection,
            ),
            patch(
                "app.services.neostaffing_attendance_history_schema.db.session.commit"
            ) as commit,
            patch.object(StaffingAttendanceSummary.__table__, "create") as create,
        ):
            self.assertTrue(ensure_neostaffing_attendance_summary_table(self.app))
            self.assertTrue(ensure_neostaffing_attendance_summary_table(self.app))
        self.assertEqual(create.call_count, 2)
        self.assertTrue(all(call.kwargs["checkfirst"] for call in create.call_args_list))
        self.assertEqual(commit.call_count, 2)

        with patch("app.ensure_neostaffing_attendance_summary_table") as ensure:
            app = create_app(self.config)
        ensure.assert_called_once_with(app)

    def test_attendance_get_has_user_driven_rollover_hook(self):
        source = python_inspect.getsource(staffing_routes._handle_attendance)
        self.assertIn("maintain_current_attendance_rollover", source)
        self.assertIn("db.session.commit()", source)
        self.assertIn("db.session.rollback()", source)


if __name__ == "__main__":
    unittest.main()
