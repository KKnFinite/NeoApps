import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

from app import create_app
from app.services.neoscorpion_schema import (
    NEOSCORPION_ADDITIVE_COLUMNS,
    NEOSCORPION_CHECK_CONSTRAINTS,
    NEOSCORPION_MODEL_TABLES,
    NEOSCORPION_SCHEMA_LOCK_KEY,
    _ensure_additive_columns,
    _ensure_check_constraints,
    _verify_model_schema_contract,
    ensure_neoscorpion_production_schema,
    neoscorpion_model_schema_contract,
)


class NeoScorpionProductionSchemaTest(unittest.TestCase):
    def setUp(self):
        self.config = type(
            "NeoScorpionProductionSchemaConfig",
            (),
            {
                "SECRET_KEY": "test",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
                "AUTO_BOOTSTRAP_DATABASE": False,
            },
        )

    def test_factory_invokes_ensure_once_and_get_does_not_invoke_it(self):
        with patch("app.ensure_neoscorpion_production_schema") as ensure:
            app = create_app(self.config)
            response = app.test_client().get("/login")

        self.assertEqual(response.status_code, 200)
        ensure.assert_called_once_with(app)

    def test_current_model_table_and_column_contract_is_covered(self):
        expected_table_names = (
            "neoscorpion_tail_fuel_states",
            "neoscorpion_fuel_trucks",
            "neoscorpion_settings",
            "neoscorpion_sort_asset_states",
            "neoscorpion_sort_fuelers",
            "neoscorpion_sort_trucks",
            "neoscorpion_fuel_assignments",
            "neoscorpion_fuel_work_states",
            "neoscorpion_fuel_tank_states",
            "neoscorpion_aircraft_fuel_settings",
            "neoscorpion_fueling_events",
            "neoscorpion_fuel_audit_entries",
        )
        self.assertEqual(
            tuple(model.__table__.name for model in NEOSCORPION_MODEL_TABLES),
            expected_table_names,
        )
        contract = neoscorpion_model_schema_contract()
        self.assertEqual(set(contract), set(expected_table_names))
        for model in NEOSCORPION_MODEL_TABLES:
            self.assertEqual(
                contract[model.__table__.name],
                frozenset(model.__table__.columns.keys()),
            )

    def test_required_additive_columns_and_constraints_are_current(self):
        self.assertEqual(
            set(NEOSCORPION_ADDITIVE_COLUMNS["neoscorpion_fuel_assignments"]),
            {
                "fuel_on_board_at_utc",
                "fuel_on_board_by_user_id",
                "completed_at_utc",
                "completed_by_user_id",
                "confirmed_tail_number",
                "operational_status",
                "hold_reason",
                "hold_at_utc",
                "hold_by_user_id",
            },
        )
        self.assertIn(
            "NOT NULL DEFAULT 'active'",
            NEOSCORPION_ADDITIVE_COLUMNS["neoscorpion_fuel_assignments"][
                "operational_status"
            ],
        )
        self.assertEqual(
            set(NEOSCORPION_ADDITIVE_COLUMNS["neoscorpion_fuel_work_states"]),
            {
                "apu_running",
                "apu_confirmed_at_utc",
                "apu_allowance_lbs",
                "applied_apu_rate_thousand_lbs_per_hour",
                "off_at_utc",
                "off_by_user_id",
                "truck_segment_started_at_utc",
                "ended_early_at_utc",
                "ended_early_by_user_id",
                "ended_early_reason",
            },
        )
        constraints = {
            constraint_name: frozenset(allowed_values)
            for _table, constraint_name, _column, allowed_values in (
                NEOSCORPION_CHECK_CONSTRAINTS
            )
        }
        self.assertEqual(
            constraints["ck_neoscorpion_fuel_assignment_operational_status"],
            frozenset(("active", "hold_review")),
        )
        self.assertEqual(
            constraints["ck_neoscorpion_fuel_audit_entry_action"],
            frozenset(
                (
                    "reopen_off",
                    "correct_actual",
                    "auto_hold",
                    "resume_hold",
                    "swap_fueler",
                    "swap_truck",
                    "confirm_tail",
                    "end_early",
                )
            ),
        )

    def test_additive_sql_and_constraint_repair_are_targeted(self):
        connection = Mock()
        _ensure_additive_columns(connection)
        additive_statements = "\n".join(
            str(call.args[0]) for call in connection.execute.call_args_list
        )
        self.assertEqual(
            additive_statements.count("ADD COLUMN IF NOT EXISTS"),
            sum(len(columns) for columns in NEOSCORPION_ADDITIVE_COLUMNS.values()),
        )
        self.assertNotIn("CREATE TABLE", additive_statements)

        connection.reset_mock()
        current_operational = Mock()
        current_operational.scalar.return_value = (
            "CHECK (operational_status IN ('active', 'hold_review'))"
        )
        old_audit = Mock()
        old_audit.scalar.return_value = (
            "CHECK (action IN ('reopen_off', 'correct_actual'))"
        )
        connection.execute.side_effect = [
            current_operational,
            old_audit,
            Mock(),
            Mock(),
        ]
        _ensure_check_constraints(connection)
        constraint_statements = "\n".join(
            str(call.args[0]) for call in connection.execute.call_args_list
        )
        self.assertNotIn(
            "DROP CONSTRAINT IF EXISTS "
            "ck_neoscorpion_fuel_assignment_operational_status",
            constraint_statements,
        )
        self.assertIn(
            "DROP CONSTRAINT IF EXISTS ck_neoscorpion_fuel_audit_entry_action",
            constraint_statements,
        )
        self.assertIn("'end_early'", constraint_statements)

    def test_post_ensure_contract_rejects_missing_model_column(self):
        contract = neoscorpion_model_schema_contract()
        schema_inspector = Mock()
        schema_inspector.get_table_names.return_value = list(contract)
        schema_inspector.get_columns.side_effect = lambda table_name: [
            {"name": column_name}
            for column_name in (
                contract[table_name] - {"operational_status"}
                if table_name == "neoscorpion_fuel_assignments"
                else contract[table_name]
            )
        ]
        with (
            patch(
                "app.services.neoscorpion_schema.inspect",
                return_value=schema_inspector,
            ),
            self.assertRaisesRegex(RuntimeError, "operational_status"),
        ):
            _verify_model_schema_contract(Mock())

    def test_postgresql_ensure_is_bounded_and_idempotent(self):
        app = create_app(self.config)
        app.config.update(
            TESTING=False,
            SQLALCHEMY_DATABASE_URI="postgresql://example.test/neoapps",
        )
        connection = Mock()
        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "app.services.neoscorpion_schema.db.session.connection",
                    return_value=connection,
                )
            )
            commit = stack.enter_context(
                patch("app.services.neoscorpion_schema.db.session.commit")
            )
            stack.enter_context(
                patch("app.services.neoscorpion_schema._ensure_additive_columns")
            )
            stack.enter_context(
                patch("app.services.neoscorpion_schema._ensure_check_constraints")
            )
            stack.enter_context(
                patch("app.services.neoscorpion_schema._verify_model_schema_contract")
            )
            create_calls = [
                stack.enter_context(patch.object(model.__table__, "create"))
                for model in NEOSCORPION_MODEL_TABLES
            ]

            self.assertTrue(ensure_neoscorpion_production_schema(app))
            self.assertTrue(ensure_neoscorpion_production_schema(app))

        statements = "\n".join(
            str(call.args[0]) for call in connection.execute.call_args_list
        )
        self.assertIn("SET LOCAL lock_timeout", statements)
        self.assertIn("pg_advisory_xact_lock", statements)
        self.assertEqual(
            connection.execute.call_args_list[1].args[1]["lock_key"],
            NEOSCORPION_SCHEMA_LOCK_KEY,
        )
        for create in create_calls:
            self.assertEqual(create.call_count, 2)
            create.assert_called_with(bind=connection, checkfirst=True)
        self.assertEqual(commit.call_count, 2)

    def test_testing_and_sqlite_skip_targeted_ensure(self):
        app = create_app(self.config)
        self.assertFalse(ensure_neoscorpion_production_schema(app))
        app.config["TESTING"] = False
        self.assertFalse(ensure_neoscorpion_production_schema(app))


if __name__ == "__main__":
    unittest.main()
