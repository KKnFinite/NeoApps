import unittest
from datetime import time

from sqlalchemy import inspect, text

from app import create_app
from app.extensions import db
from app.models import NeoErmacDoorPull
from app.services.schema_sync import (
    POSTGRES_OPTIONAL_COLUMNS,
    sync_database_schema,
    sync_local_sqlite_schema,
)


class NeoErmacDoorPullSchemaSyncTest(unittest.TestCase):
    def setUp(self):
        TestConfig = type(
            "NeoErmacDoorPullSchemaSyncTestConfig",
            (),
            {
                "SECRET_KEY": "test",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            },
        )
        self.app = create_app(TestConfig)
        self.context = self.app.app_context()
        self.context.push()
        # This table is created manually below, so SQLAlchemy metadata cleanup alone
        # cannot guarantee isolation between schema-repair scenarios.
        db.session.execute(text("DROP TABLE IF EXISTS neoermac_door_pulls"))
        db.session.commit()

    def tearDown(self):
        db.session.execute(text("DROP TABLE IF EXISTS neoermac_door_pulls"))
        db.session.commit()
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_legacy_second_mix_rows_gain_current_columns_and_preserve_rows(self):
        self._create_legacy_door_pull_table()
        self._insert_legacy_row(1, "01:55:00", True)
        self._insert_legacy_row(2, "02:10:00", False)
        db.session.commit()

        sync_local_sqlite_schema(self.app)
        db.session.commit()

        first = db.session.get(NeoErmacDoorPull, 1)
        second = db.session.get(NeoErmacDoorPull, 2)
        column_names = self._column_names()

        self.assertEqual(NeoErmacDoorPull.query.count(), 2)
        self.assertEqual(first.actual_mix_pull_time_local, time(1, 55))
        self.assertTrue(first.no_mix_pull)
        self.assertEqual(second.actual_mix_pull_time_local, time(2, 10))
        self.assertFalse(second.no_mix_pull)
        self.assertTrue(first.created_at)
        self.assertTrue(first.updated_at)
        self.assertTrue(
            {column.name for column in NeoErmacDoorPull.__table__.columns}.issubset(
                column_names
            )
        )
        self.assertIn("actual_first_mix_pull_time_local", column_names)
        self.assertIn("actual_second_mix_pull_time_local", column_names)
        self.assertIn("no_first_mix_pull", column_names)
        self.assertIn("no_second_mix_pull", column_names)

        sync_local_sqlite_schema(self.app)
        db.session.commit()
        repeated = db.session.get(NeoErmacDoorPull, 1)
        self.assertEqual(NeoErmacDoorPull.query.count(), 2)
        self.assertEqual(repeated.actual_mix_pull_time_local, time(1, 55))
        self.assertTrue(repeated.no_mix_pull)

    def test_existing_mix_pull_value_is_not_overwritten_by_legacy_second_mix(self):
        self._create_legacy_door_pull_table(include_current_mix_columns=True)
        self._insert_legacy_row(
            1,
            "01:55:00",
            True,
            actual_mix_pull_time_local="01:45:00",
            no_mix_pull=False,
        )
        db.session.commit()

        sync_local_sqlite_schema(self.app)
        db.session.commit()

        row = db.session.get(NeoErmacDoorPull, 1)
        self.assertEqual(row.actual_mix_pull_time_local, time(1, 45))
        self.assertTrue(row.no_mix_pull)

    def test_postgres_repair_map_covers_every_additive_current_door_pull_column(self):
        foundation_columns = {"id", "gateway_id", "door", "destination"}
        current_columns = {
            column.name for column in NeoErmacDoorPull.__table__.columns
        }

        self.assertTrue(
            (current_columns - foundation_columns).issubset(
                POSTGRES_OPTIONAL_COLUMNS["neoermac_door_pulls"]
            )
        )
        self.assertEqual(
            POSTGRES_OPTIONAL_COLUMNS["neoermac_door_pulls"][
                "actual_mix_pull_time_local"
            ],
            "TIME",
        )
        self.assertIn(
            "NOT NULL DEFAULT FALSE",
            POSTGRES_OPTIONAL_COLUMNS["neoermac_door_pulls"]["no_mix_pull"],
        )

    def test_postgres_sync_adds_and_migrates_missing_mix_pull_columns(self):
        from unittest.mock import patch

        legacy_columns = {
            column.name
            for column in NeoErmacDoorPull.__table__.columns
            if column.name not in {"actual_mix_pull_time_local", "no_mix_pull"}
        }
        legacy_columns.update(
            {
                "actual_first_mix_pull_time_local",
                "no_first_mix_pull",
                "actual_second_mix_pull_time_local",
                "no_second_mix_pull",
            }
        )

        class LegacyDoorPullInspector:
            def get_table_names(self):
                return ["neoermac_door_pulls"]

            def get_columns(self, table_name):
                self_outer.assertEqual(table_name, "neoermac_door_pulls")
                return [{"name": column_name} for column_name in legacy_columns]

        self_outer = self
        inspector = LegacyDoorPullInspector()
        with (
            patch.dict(
                self.app.config,
                {"SQLALCHEMY_DATABASE_URI": "postgresql://example.test/neoapps"},
            ),
            patch("app.services.schema_sync.db.create_all"),
            patch("app.services.schema_sync.inspect", return_value=inspector),
            patch("app.services.schema_sync.db.session.execute") as execute,
            patch("app.services.schema_sync.db.session.flush"),
            patch("app.services.schema_sync._sync_staffing_people_employee_status_postgres"),
            patch(
                "app.services.schema_sync._sync_sort_date_mission_status_constraints_postgres"
            ),
            patch("app.services.schema_sync._sync_uld_request_unique_constraint_postgres"),
        ):
            sync_database_schema(self.app)

        statements = "\n".join(str(call.args[0]) for call in execute.call_args_list)
        self.assertIn(
            "ADD COLUMN IF NOT EXISTS actual_mix_pull_time_local TIME",
            statements,
        )
        self.assertIn(
            "ADD COLUMN IF NOT EXISTS no_mix_pull BOOLEAN NOT NULL DEFAULT FALSE",
            statements,
        )
        self.assertIn(
            "SET actual_mix_pull_time_local = actual_second_mix_pull_time_local",
            statements,
        )
        self.assertIn("SET no_mix_pull = TRUE", statements)

    def _create_legacy_door_pull_table(self, include_current_mix_columns=False):
        current_columns = ""
        if include_current_mix_columns:
            current_columns = """
                actual_mix_pull_time_local TIME,
                no_mix_pull BOOLEAN NOT NULL DEFAULT 0,
            """
        db.session.execute(
            text(
                f"""
                CREATE TABLE neoermac_door_pulls (
                    id INTEGER PRIMARY KEY,
                    gateway_id INTEGER NOT NULL,
                    door VARCHAR(8) NOT NULL,
                    destination VARCHAR(8) NOT NULL,
                    actual_first_mix_pull_time_local TIME,
                    no_first_mix_pull BOOLEAN NOT NULL DEFAULT 0,
                    actual_second_mix_pull_time_local TIME,
                    no_second_mix_pull BOOLEAN NOT NULL DEFAULT 0,
                    {current_columns}
                    legacy_note TEXT
                )
                """
            )
        )

    def _insert_legacy_row(
        self,
        identifier,
        second_mix,
        no_second_mix,
        actual_mix_pull_time_local=None,
        no_mix_pull=None,
    ):
        columns = [
            "id",
            "gateway_id",
            "door",
            "destination",
            "actual_first_mix_pull_time_local",
            "no_first_mix_pull",
            "actual_second_mix_pull_time_local",
            "no_second_mix_pull",
            "legacy_note",
        ]
        values = {
            "id": identifier,
            "gateway_id": 1,
            "door": "D34",
            "destination": "SDF",
            "actual_first_mix_pull_time_local": "01:40:00",
            "no_first_mix_pull": False,
            "actual_second_mix_pull_time_local": second_mix,
            "no_second_mix_pull": no_second_mix,
            "legacy_note": f"legacy-{identifier}",
        }
        if actual_mix_pull_time_local is not None:
            columns.append("actual_mix_pull_time_local")
            values["actual_mix_pull_time_local"] = actual_mix_pull_time_local
        if no_mix_pull is not None:
            columns.append("no_mix_pull")
            values["no_mix_pull"] = no_mix_pull

        quoted_columns = ", ".join(columns)
        placeholders = ", ".join(f":{column}" for column in columns)
        db.session.execute(
            text(
                f"INSERT INTO neoermac_door_pulls ({quoted_columns}) "
                f"VALUES ({placeholders})"
            ),
            values,
        )

    def _column_names(self):
        return {
            column["name"]
            for column in inspect(db.engine).get_columns("neoermac_door_pulls")
        }
