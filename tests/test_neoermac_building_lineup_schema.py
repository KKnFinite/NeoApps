import unittest
from unittest.mock import Mock, patch

from sqlalchemy import inspect, text

from app import create_app
from app.extensions import db
from app.models import NeoErmacBuildingLineup
from app.services.neoermac_building_lineup_schema import (
    NEOERMAC_BUILDING_LINEUP_ADDITIVE_COLUMNS,
    NEOERMAC_BUILDING_LINEUP_SCHEMA_LOCK_KEY,
    ensure_neoermac_building_lineup_columns,
)
from app.services.schema_sync import (
    LOCAL_SQLITE_OPTIONAL_COLUMNS,
    POSTGRES_OPTIONAL_COLUMNS,
    sync_local_sqlite_schema,
)


class NeoErmacBuildingLineupSchemaTest(unittest.TestCase):
    def setUp(self):
        self.config = type(
            "NeoErmacBuildingLineupSchemaConfig",
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

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_model_and_schema_maps_include_only_nullable_second_slots(self):
        model_columns = {column.name for column in NeoErmacBuildingLineup.__table__.columns}

        for column_name in NEOERMAC_BUILDING_LINEUP_ADDITIVE_COLUMNS:
            with self.subTest(column_name=column_name):
                self.assertIn(column_name, model_columns)
                self.assertTrue(NeoErmacBuildingLineup.__table__.c[column_name].nullable)
                self.assertEqual(
                    LOCAL_SQLITE_OPTIONAL_COLUMNS["neoermac_building_lineups"][column_name],
                    "VARCHAR(8)",
                )
                self.assertEqual(
                    POSTGRES_OPTIONAL_COLUMNS["neoermac_building_lineups"][column_name],
                    "VARCHAR(8)",
                )

    def test_sqlite_expansion_preserves_all_four_existing_assignments(self):
        db.session.execute(text("DROP TABLE neoermac_building_lineups"))
        db.session.execute(
            text(
                """
                CREATE TABLE neoermac_building_lineups (
                    id INTEGER PRIMARY KEY,
                    gateway_id INTEGER NOT NULL,
                    runout_key VARCHAR(32) NOT NULL,
                    runout_name VARCHAR(40) NOT NULL,
                    east_destination_1 VARCHAR(8),
                    east_destination_2 VARCHAR(8),
                    west_destination_1 VARCHAR(8),
                    west_destination_2 VARCHAR(8),
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
                """
            )
        )
        db.session.execute(
            text(
                """
                INSERT INTO neoermac_building_lineups (
                    id, gateway_id, runout_key, runout_name,
                    east_destination_1, east_destination_2,
                    west_destination_1, west_destination_2,
                    created_at, updated_at
                ) VALUES (
                    1, 1, 'green_runout', 'D1-D4 Belts',
                    'SDF', 'ONT', 'LAX', 'PHX',
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            )
        )
        db.session.commit()

        sync_local_sqlite_schema(self.app)
        db.session.commit()
        sync_local_sqlite_schema(self.app)
        db.session.commit()

        row = db.session.execute(
            text("SELECT * FROM neoermac_building_lineups WHERE id = 1")
        ).mappings().one()
        self.assertEqual(
            (
                row["east_destination_1"],
                row["east_destination_2"],
                row["west_destination_1"],
                row["west_destination_2"],
            ),
            ("SDF", "ONT", "LAX", "PHX"),
        )
        self.assertTrue(
            all(row[column_name] is None for column_name in NEOERMAC_BUILDING_LINEUP_ADDITIVE_COLUMNS)
        )
        column_names = {
            column["name"]
            for column in inspect(db.engine).get_columns("neoermac_building_lineups")
        }
        self.assertTrue(set(NEOERMAC_BUILDING_LINEUP_ADDITIVE_COLUMNS).issubset(column_names))

    def test_postgresql_startup_ensure_is_targeted_and_idempotent(self):
        self.app.config.update(
            TESTING=False,
            SQLALCHEMY_DATABASE_URI="postgresql://example.test/neoapps",
        )
        connection = Mock()

        with (
            patch(
                "app.services.neoermac_building_lineup_schema.db.session.connection",
                return_value=connection,
            ),
            patch(
                "app.services.neoermac_building_lineup_schema.db.session.commit"
            ) as commit,
        ):
            self.assertTrue(ensure_neoermac_building_lineup_columns(self.app))
            self.assertTrue(ensure_neoermac_building_lineup_columns(self.app))

        statements = "\n".join(
            str(call.args[0]) for call in connection.execute.call_args_list
        )
        self.assertIn("SET LOCAL lock_timeout", statements)
        self.assertIn("pg_advisory_xact_lock", statements)
        self.assertEqual(
            connection.execute.call_args_list[1].args[1]["lock_key"],
            NEOERMAC_BUILDING_LINEUP_SCHEMA_LOCK_KEY,
        )
        for column_name in NEOERMAC_BUILDING_LINEUP_ADDITIVE_COLUMNS:
            self.assertIn(
                "ALTER TABLE neoermac_building_lineups ADD COLUMN IF NOT EXISTS "
                f"{column_name} VARCHAR(8)",
                statements,
            )
        self.assertEqual(commit.call_count, 2)

    def test_testing_and_sqlite_skip_postgresql_repair(self):
        with patch(
            "app.services.neoermac_building_lineup_schema.db.session.connection"
        ) as connection:
            self.assertFalse(ensure_neoermac_building_lineup_columns(self.app))
            self.app.config["SQLALCHEMY_DATABASE_URI"] = (
                "postgresql://example.test/neoapps"
            )
            self.assertFalse(ensure_neoermac_building_lineup_columns(self.app))

        connection.assert_not_called()

    def test_factory_invokes_targeted_building_lineup_ensure(self):
        with patch("app.ensure_neoermac_building_lineup_columns") as ensure:
            app = create_app(self.config)

        ensure.assert_called_once_with(app)


if __name__ == "__main__":
    unittest.main()
