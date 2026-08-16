import unittest
from unittest.mock import Mock, patch

from sqlalchemy import inspect, text

from app import create_app
from app.extensions import db
from app.models import StaffingGroup, StaffingGroupMembership
from app.services.neostaffing_staffing_group_schema import (
    NEOSTAFFING_STAFFING_GROUP_SCHEMA_LOCK_KEY,
    ensure_neostaffing_staffing_group_tables,
)
from app.services.schema_sync import sync_local_sqlite_schema


class NeoStaffingStaffingGroupSchemaTest(unittest.TestCase):
    def setUp(self):
        self.config = type(
            "NeoStaffingStaffingGroupSchemaConfig",
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

    def test_models_are_additive_and_do_not_store_totals(self):
        group_columns = set(StaffingGroup.__table__.c.keys())
        membership_columns = set(StaffingGroupMembership.__table__.c.keys())

        self.assertEqual(
            group_columns,
            {"id", "name", "active", "created_at", "updated_at"},
        )
        self.assertEqual(
            membership_columns,
            {"id", "group_id", "staffing_unit_id"},
        )
        self.assertTrue(StaffingGroupMembership.__table__.c.group_id.foreign_keys)
        self.assertTrue(
            StaffingGroupMembership.__table__.c.staffing_unit_id.foreign_keys
        )

    def test_local_schema_sync_recreates_only_missing_group_tables(self):
        db.session.execute(text("DROP TABLE staffing_group_memberships"))
        db.session.execute(text("DROP TABLE staffing_groups"))
        db.session.commit()

        sync_local_sqlite_schema(self.app)
        db.session.commit()
        sync_local_sqlite_schema(self.app)
        db.session.commit()

        table_names = inspect(db.engine).get_table_names()
        self.assertIn("staffing_groups", table_names)
        self.assertIn("staffing_group_memberships", table_names)

    def test_postgresql_targeted_ensure_is_bounded_idempotent_and_seeds_permissions(self):
        self.app.config.update(
            TESTING=False,
            SQLALCHEMY_DATABASE_URI="postgresql://example.test/neoapps",
        )
        connection = Mock()
        with (
            patch(
                "app.services.neostaffing_staffing_group_schema.db.session.connection",
                return_value=connection,
            ),
            patch(
                "app.services.neostaffing_staffing_group_schema.db.session.commit"
            ) as commit,
            patch.object(StaffingGroup.__table__, "create") as create_group,
            patch.object(
                StaffingGroupMembership.__table__,
                "create",
            ) as create_membership,
        ):
            self.assertTrue(ensure_neostaffing_staffing_group_tables(self.app))
            self.assertTrue(ensure_neostaffing_staffing_group_tables(self.app))

        statements = "\n".join(
            str(call.args[0]) for call in connection.execute.call_args_list
        )
        self.assertIn("SET LOCAL lock_timeout", statements)
        self.assertIn("pg_advisory_xact_lock", statements)
        self.assertIn("ON CONFLICT (permission_key) DO NOTHING", statements)
        self.assertIn("neostaffing.staffing_groups.view", statements)
        self.assertIn("neostaffing.staffing_groups.edit", statements)
        self.assertEqual(
            connection.execute.call_args_list[1].args[1]["lock_key"],
            NEOSTAFFING_STAFFING_GROUP_SCHEMA_LOCK_KEY,
        )
        for create in (create_group, create_membership):
            self.assertEqual(create.call_count, 2)
            create.assert_called_with(bind=connection, checkfirst=True)
        self.assertEqual(commit.call_count, 2)

    def test_factory_invokes_targeted_staffing_group_ensure(self):
        with patch("app.ensure_neostaffing_staffing_group_tables") as ensure:
            app = create_app(self.config)
        ensure.assert_called_once_with(app)


if __name__ == "__main__":
    unittest.main()
