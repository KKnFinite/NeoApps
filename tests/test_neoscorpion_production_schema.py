import json
from pathlib import Path
import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

from app import create_app
from app.services.neoscorpion_schema import (
    NEOSCORPION_ADDITIVE_COLUMNS,
    NEOSCORPION_CHECK_CONSTRAINTS,
    NEOSCORPION_EXPRESSION_CHECK_CONSTRAINTS,
    NEOSCORPION_MODEL_TABLES,
    NEOSCORPION_SCHEMA_LOCK_KEY,
    _ensure_additive_columns,
    _ensure_check_constraints,
    _ensure_expression_check_constraints,
    _verify_model_schema_contract,
    ensure_neoscorpion_production_schema,
    neoscorpion_model_schema_contract,
)
from app.services.neoscorpion_spear_schema import SPEAR_SETTINGS_COLUMNS


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

    def test_factory_does_not_run_broad_neoscorpion_schema_bootstrap(self):
        with patch(
            "app.services.neoscorpion_schema.ensure_neoscorpion_production_schema"
        ) as ensure:
            app = create_app(self.config)
            response = app.test_client().get("/login")

        self.assertEqual(response.status_code, 200)
        ensure.assert_not_called()

    def test_current_model_table_and_column_contract_is_covered(self):
        expected_table_names = tuple(json.loads(Path('tests/fixtures/neoscorpion_w3_schema.json').read_text(encoding='utf-8'))['tables'])
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
        expected = json.loads(Path('tests/fixtures/neoscorpion_w3_schema.json').read_text(encoding='utf-8'))
        self.assertEqual(NEOSCORPION_ADDITIVE_COLUMNS, expected['additive_columns'])
        self.assertEqual(json.loads(json.dumps(NEOSCORPION_CHECK_CONSTRAINTS)), expected['checks'])
        self.assertEqual(json.loads(json.dumps(NEOSCORPION_EXPRESSION_CHECK_CONSTRAINTS)), expected['expressions'])
        # Permanent visit identity and immutable evidence remain explicit requirements.
        for table, fields in {
            'neoscorpion_fuel_work_states': ('visit_number', 'fueler_submitted_at_utc', 'physical_cycle_number', 'performance_context'),
            'neoscorpion_fueling_events': ('performance_snapshot',),
            'neoscorpion_sort_trucks': ('inventory_epoch', 'inventory_verified', 'availability_history'),
        }.items():
            for field in fields:
                self.assertIn(field, NEOSCORPION_ADDITIVE_COLUMNS[table])

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
        connection.execute.return_value.scalar.return_value = None
        _ensure_check_constraints(connection)
        constraint_statements = "\n".join(
            str(call.args[0]) for call in connection.execute.call_args_list
        )
        self.assertIn(
            "FROM pg_constraint AS constraint_row",
            constraint_statements,
        )
        self.assertNotIn("AS constraint\n", constraint_statements)
        self.assertIn(
            "DROP CONSTRAINT IF EXISTS ck_neoscorpion_fuel_audit_entry_action",
            constraint_statements,
        )
        self.assertIn("'end_early'", constraint_statements)
        self.assertIn("'needs_sump'", constraint_statements)
        self.assertIn("'uplift'", constraint_statements)
        self.assertIn("'defuel'", constraint_statements)

        connection.reset_mock()
        connection.execute.return_value.scalar.return_value = None
        _ensure_expression_check_constraints(connection)
        expression_statements = "\n".join(
            str(call.args[0]) for call in connection.execute.call_args_list
        )
        self.assertIn("current_cycle_number >= 1", expression_statements)
        self.assertIn("cycle_number >= 1", expression_statements)

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
                patch(
                    "app.services.neoscorpion_schema._ensure_expression_check_constraints"
                )
            )
            stack.enter_context(
                patch("app.services.neoscorpion_schema._verify_model_schema_contract")
            )
            identity=stack.enter_context(patch("app.services.neoscorpion_segment_schema.migrate_physical_segment_identity"))
            continuation=stack.enter_context(patch("app.services.neoscorpion_continuation_schema.migrate_continuation_queue"))
            create_calls = [
                stack.enter_context(patch.object(model.__table__, "create"))
                for model in NEOSCORPION_MODEL_TABLES
            ]

            self.assertTrue(ensure_neoscorpion_production_schema(app))
            self.assertTrue(ensure_neoscorpion_production_schema(app))
            self.assertEqual(identity.call_count,2)
            self.assertEqual(continuation.call_count,2)

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
