from datetime import date, datetime
import unittest
from unittest.mock import Mock, patch

from sqlalchemy import event, inspect

from app import create_app
from app.extensions import db
from app.models import (
    Gateway,
    GatewaySortMatrix,
    PortalAppAccess,
    SortDateOperation,
    StaffingOperationSchedule,
    StaffingUnit,
    User,
)
from app.models.staffing_operation_schedule import (
    STAFFING_OPERATION_SCHEDULE_WEEKDAYS,
)
from app.services import neostaffing_operation_schedule as schedule_service
from app.services.neostaffing_operation_schedule_schema import (
    NEOSTAFFING_OPERATION_SCHEDULE_SCHEMA_LOCK_KEY,
    ensure_neostaffing_operation_schedule_table,
)


class NeoStaffingOperationScheduleTest(unittest.TestCase):
    def setUp(self):
        self.config = type(
            "NeoStaffingOperationScheduleConfig",
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
        self.night = StaffingUnit(unit_type="sort", name="Night", active=True)
        self.ramp = StaffingUnit(
            unit_type="operation",
            name="Ramp",
            parent=self.night,
            active=True,
        )
        self.hub = StaffingUnit(
            unit_type="operation",
            name="Hub",
            parent=self.night,
            active=True,
        )
        self.master = self._user("schedule_master", "master")
        self.simulator = self._user("schedule_simulator", "simulator")
        db.session.add_all(
            [
                self.gateway,
                self.night,
                self.ramp,
                self.hub,
                self.master,
                self.simulator,
            ]
        )
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    @staticmethod
    def _user(username, app_role):
        user = User(
            username=username,
            password_hash="unused",
            role="watcher",
            is_active=True,
        )
        user.portal_app_accesses.append(
            PortalAppAccess(
                app_code="neostaffing",
                status="approved",
                role=app_role,
                is_active=True,
            )
        )
        return user

    def _schedule(
        self,
        operation,
        start,
        through=None,
        weekdays=("monday",),
        user=None,
    ):
        return schedule_service.create_operation_schedule(
            operation.id,
            start,
            through,
            weekdays,
            user or self.master,
        )

    def _fallback_days(self, operation, start, through=None):
        return schedule_service.normal_operational_days_for_operation(
            operation.id,
            start,
            through,
        )

    def test_recurring_weekdays_and_different_operations_resolve_independently(self):
        self._schedule(
            self.ramp,
            "2026-01-01",
            weekdays=("monday", "wednesday"),
        )
        self._schedule(
            self.hub,
            "2026-01-01",
            weekdays=("tuesday", "thursday"),
        )
        db.session.commit()

        ramp = self._fallback_days(self.ramp, date(2026, 1, 4), date(2026, 1, 10))
        hub = self._fallback_days(self.hub, date(2026, 1, 4), date(2026, 1, 10))

        self.assertEqual(
            ramp.operational_dates,
            (date(2026, 1, 5), date(2026, 1, 7)),
        )
        self.assertEqual(
            hub.operational_dates,
            (date(2026, 1, 6), date(2026, 1, 8)),
        )
        self.assertTrue(
            all(
                row.source == schedule_service.EXPLICIT_OPERATION_SCHEDULE_SOURCE
                for row in ramp.resolutions + hub.resolutions
            )
        )

    def test_future_change_and_historical_date_use_effective_schedule(self):
        historical = self._schedule(
            self.ramp,
            "2026-01-01",
            "2026-06-30",
            ("monday",),
        )
        future = self._schedule(
            self.ramp,
            "2026-07-01",
            None,
            ("tuesday",),
        )
        db.session.commit()

        june_monday = schedule_service.normal_operational_date_resolution(
            self.ramp.id,
            date(2026, 6, 29),
        )
        june_tuesday = schedule_service.normal_operational_date_resolution(
            self.ramp.id,
            date(2026, 6, 30),
        )
        july_monday = schedule_service.normal_operational_date_resolution(
            self.ramp.id,
            date(2026, 7, 6),
        )
        july_tuesday = schedule_service.normal_operational_date_resolution(
            self.ramp.id,
            date(2026, 7, 7),
        )

        self.assertTrue(june_monday.is_operational)
        self.assertFalse(june_tuesday.is_operational)
        self.assertEqual(june_monday.schedule_id, historical.id)
        self.assertFalse(july_monday.is_operational)
        self.assertTrue(july_tuesday.is_operational)
        self.assertEqual(july_tuesday.schedule_id, future.id)

    def test_overlapping_ranges_are_rejected_and_adjacent_ranges_are_allowed(self):
        self._schedule(self.ramp, "2026-01-01", "2026-03-31")
        db.session.commit()
        with self.assertRaisesRegex(ValueError, "overlap"):
            self._schedule(self.ramp, "2026-03-31", "2026-04-30")
        db.session.rollback()

        # Re-load IDs after rollback and verify the next non-overlapping date works.
        ramp = db.session.get(StaffingUnit, self.ramp.id)
        master = db.session.get(User, self.master.id)
        adjacent = self._schedule(
            ramp,
            "2026-04-01",
            None,
            weekdays=("tuesday",),
            user=master,
        )
        db.session.commit()
        self.assertEqual(adjacent.effective_from, date(2026, 4, 1))
        self.assertEqual(StaffingOperationSchedule.query.count(), 2)

    def test_gateway_matrix_fallback_and_explicit_override_are_distinct(self):
        db.session.add_all(
            [
                GatewaySortMatrix(
                    gateway_id=self.gateway.id,
                    gateway_code=self.gateway.code,
                    day_of_week=day,
                    sort_name="night",
                    is_active=True,
                )
                for day in ("monday", "wednesday")
            ]
        )
        db.session.commit()

        fallback = self._fallback_days(
            self.ramp,
            date(2026, 1, 4),
            date(2026, 1, 10),
        )
        self.assertEqual(
            fallback.operational_dates,
            (date(2026, 1, 5), date(2026, 1, 7)),
        )
        self.assertTrue(
            all(
                row.source == schedule_service.GATEWAY_SORT_MATRIX_SOURCE
                for row in fallback.resolutions
            )
        )

        self._schedule(self.ramp, "2026-01-01", weekdays=("tuesday",))
        db.session.commit()
        explicit = self._fallback_days(
            self.ramp,
            date(2026, 1, 4),
            date(2026, 1, 10),
        )
        self.assertEqual(explicit.operational_dates, (date(2026, 1, 6),))
        self.assertTrue(
            all(
                row.source == schedule_service.EXPLICIT_OPERATION_SCHEDULE_SOURCE
                for row in explicit.resolutions
            )
        )

    def test_actual_one_off_operation_does_not_affect_normal_schedule(self):
        db.session.add(
            GatewaySortMatrix(
                gateway_id=self.gateway.id,
                gateway_code=self.gateway.code,
                day_of_week="monday",
                sort_name="night",
                is_active=True,
            )
        )
        db.session.add(
            SortDateOperation(
                gateway_id=self.gateway.id,
                gateway_code=self.gateway.code,
                sort_name="night",
                sort_date=date(2026, 1, 6),  # Tuesday one-off actual row.
            )
        )
        db.session.commit()

        result = schedule_service.normal_operational_date_resolution(
            self.ramp.id,
            date(2026, 1, 6),
        )
        self.assertFalse(result.is_operational)
        self.assertEqual(result.source, schedule_service.GATEWAY_SORT_MATRIX_SOURCE)

    def test_master_authority_allows_mutation_and_lower_role_is_rejected(self):
        self.assertTrue(schedule_service.can_manage_operation_schedules(self.master))
        self.assertFalse(schedule_service.can_manage_operation_schedules(self.simulator))
        with self.assertRaisesRegex(ValueError, "Master access"):
            self._schedule(
                self.ramp,
                "2026-01-01",
                user=self.simulator,
            )
        self.assertEqual(StaffingOperationSchedule.query.count(), 0)

        schedule = self._schedule(self.ramp, "2026-01-01", user=self.master)
        db.session.commit()
        self.assertEqual(schedule.created_by_user_id, self.master.id)
        self.assertEqual(schedule.updated_by_user_id, self.master.id)

    def test_range_resolver_query_count_is_bounded(self):
        db.session.add(
            GatewaySortMatrix(
                gateway_id=self.gateway.id,
                gateway_code=self.gateway.code,
                day_of_week="monday",
                sort_name="night",
                is_active=True,
            )
        )
        db.session.commit()
        ramp_id = self.ramp.id
        statements = []

        def capture(_connection, _cursor, statement, _parameters, _context, _many):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            result = schedule_service.normal_operational_days_for_operation(
                ramp_id,
                date(2026, 1, 1),
                date(2026, 3, 31),
            )
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)

        self.assertEqual(len(result.resolutions), 90)
        self.assertLessEqual(len(statements), 4)

    def test_model_and_schema_are_additive_and_idempotent(self):
        table_names = set(inspect(db.engine).get_table_names())
        self.assertIn("staffing_operation_schedules", table_names)
        self.assertEqual(
            set(STAFFING_OPERATION_SCHEDULE_WEEKDAYS),
            {
                column.name
                for column in StaffingOperationSchedule.__table__.columns
                if column.name in STAFFING_OPERATION_SCHEDULE_WEEKDAYS
            },
        )

        self.app.config.update(
            TESTING=False,
            SQLALCHEMY_DATABASE_URI="postgresql://example.test/neoapps",
        )
        connection = Mock()
        with (
            patch(
                "app.services.neostaffing_operation_schedule_schema.db.session.connection",
                return_value=connection,
            ),
            patch(
                "app.services.neostaffing_operation_schedule_schema.db.session.commit"
            ) as commit,
            patch.object(StaffingOperationSchedule.__table__, "create") as create,
        ):
            self.assertTrue(ensure_neostaffing_operation_schedule_table(self.app))
            self.assertTrue(ensure_neostaffing_operation_schedule_table(self.app))

        self.assertEqual(create.call_count, 2)
        self.assertTrue(all(call.kwargs["checkfirst"] for call in create.call_args_list))
        statements = "\n".join(
            str(call.args[0]) for call in connection.execute.call_args_list
        )
        self.assertIn("SET LOCAL lock_timeout", statements)
        self.assertIn("pg_advisory_xact_lock", statements)
        self.assertEqual(
            connection.execute.call_args_list[1].args[1]["lock_key"],
            NEOSTAFFING_OPERATION_SCHEDULE_SCHEMA_LOCK_KEY,
        )
        self.assertNotIn("DROP TABLE", statements)
        self.assertNotIn("UPDATE ", statements)
        self.assertNotIn("DELETE FROM", statements)
        self.assertEqual(commit.call_count, 2)

    def test_factory_invokes_targeted_operation_schedule_ensure(self):
        with patch("app.ensure_neostaffing_operation_schedule_table") as ensure:
            app = create_app(self.config)
        ensure.assert_called_once_with(app)


if __name__ == "__main__":
    unittest.main()
