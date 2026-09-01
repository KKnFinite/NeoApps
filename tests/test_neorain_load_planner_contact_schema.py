import unittest
from unittest.mock import Mock, patch

from app import create_app
from app.extensions import db
from app.models import NeoRainLoadPlannerContact
from app.services.neorain_load_planner_contact_schema import (
    NEORAIN_LOAD_PLANNER_CONTACT_SCHEMA_LOCK_KEY,
    ensure_neorain_load_planner_contact_table,
)


class NeoRainLoadPlannerContactSchemaTest(unittest.TestCase):
    def setUp(self):
        config = type(
            "NeoRainLoadPlannerContactSchemaTestConfig",
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

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_contact_table_is_gateway_and_person_unique(self):
        columns = NeoRainLoadPlannerContact.__table__.columns
        self.assertTrue(columns["gateway_id"].index)
        self.assertTrue(columns["staffing_person_id"].index)
        self.assertTrue(columns["extension"].nullable)
        self.assertTrue(columns["radio_channel"].nullable)
        self.assertIn(
            "uq_neorain_load_planner_contact_gateway_person",
            {constraint.name for constraint in NeoRainLoadPlannerContact.__table__.constraints},
        )

    def test_postgresql_ensure_creates_only_the_contact_table_under_lock(self):
        self.app.config.update(
            TESTING=False,
            SQLALCHEMY_DATABASE_URI="postgresql://example.test/neoapps",
        )
        connection = Mock()
        with (
            patch(
                "app.services.neorain_load_planner_contact_schema.db.session.connection",
                return_value=connection,
            ),
            patch.object(NeoRainLoadPlannerContact.__table__, "create") as create,
            patch("app.services.neorain_load_planner_contact_schema.db.session.commit") as commit,
            patch("app.services.neorain_load_planner_contact_schema.db.session.rollback"),
        ):
            self.assertTrue(ensure_neorain_load_planner_contact_table(self.app))

        statements = "\n".join(str(call.args[0]) for call in connection.execute.call_args_list)
        self.assertIn("SET LOCAL lock_timeout = '5s'", statements)
        self.assertIn("pg_advisory_xact_lock", statements)
        self.assertEqual(connection.execute.call_args_list[1].args[1]["lock_key"], NEORAIN_LOAD_PLANNER_CONTACT_SCHEMA_LOCK_KEY)
        create.assert_called_once_with(bind=connection, checkfirst=True)
        commit.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
