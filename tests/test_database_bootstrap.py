import io
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

from app import create_app, maybe_auto_bootstrap_database
from app.config import resolve_database_uri
from app.extensions import db
from datetime import time

from sqlalchemy import Column, MetaData, Table, inspect, text

from app.models import (
    Gateway,
    GatewayMembership,
    GatewayNodeRole,
    MasterFlightSchedule,
    MotherBrainGoogleIntegrationSetting,
    NeoNode,
    NeoSektorOperationalSetting,
    PermissionRule,
    StaffingUnit,
    User,
)
from app.services.access_control import DEFAULT_NEONODES, user_can_access_node
from app.services.permission_rules import DEFAULT_PERMISSION_RULES
from app.services.password_policy import set_user_password
from app.services.database_bootstrap import (
    LOCAL_SQLITE_FALLBACK_PASSWORD,
    bootstrap_database,
)
from app.services.schema_sync import (
    LOCAL_SQLITE_OPTIONAL_COLUMNS,
    POSTGRES_OPTIONAL_COLUMNS,
    _create_missing_application_tables,
    _sync_neoermac_legacy_defaults_postgres,
    _mark_existing_approved_users_for_password_policy_update,
    _migrate_legacy_second_mix_pull_values,
)


class DatabaseBootstrapTest(unittest.TestCase):
    def setUp(self):
        TestConfig = type(
            "TestConfig",
            (),
            {
                "SECRET_KEY": "test",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
                "AUTO_BOOTSTRAP_DATABASE": False,
            },
        )
        self.app = create_app(TestConfig)
        self.context = self.app.app_context()
        self.context.push()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_database_url_env_is_used_when_present(self):
        neon_url = "postgresql://neo_user:dbpass@example.neon.tech/neogateway"

        with patch.dict(os.environ, {"DATABASE_URL": neon_url}, clear=False):
            self.assertEqual(resolve_database_uri(), neon_url)

    def test_staffing_sort_roots_created_without_children_and_idempotent(self):
        bootstrap_database(self.app)
        roots = StaffingUnit.query.filter_by(unit_type="sort", parent_id=None).all()
        self.assertEqual({row.name for row in roots}, {"Twilight", "Day", "Sunrise", "Preload"})
        original = {row.name: row.id for row in roots}
        self.assertTrue(all(row.active and not row.children for row in roots))
        bootstrap_database(self.app)
        self.assertEqual({row.name: row.id for row in StaffingUnit.query.all()}, original)

    def test_staffing_sort_seed_preserves_existing_roots_and_children(self):
        db.create_all()
        twilight = StaffingUnit(unit_type="sort", name=" twilight ", active=False, display_order=17)
        night = StaffingUnit(unit_type="sort", name="Night")
        ramp = StaffingUnit(unit_type="operation", name="Ramp", parent=night)
        db.session.add_all([twilight, night, ramp])
        db.session.commit()
        identifiers = twilight.id, night.id, ramp.id
        for _ in range(2):
            bootstrap_database(self.app)
        self.assertEqual((twilight.id, night.id, ramp.id), identifiers)
        self.assertFalse(twilight.active)
        self.assertEqual(twilight.name, " twilight ")
        self.assertEqual(twilight.display_order, 17)
        self.assertEqual(ramp.parent_id, night.id)
        self.assertEqual(StaffingUnit.query.filter_by(unit_type="sort").count(), 5)
        self.assertEqual(StaffingUnit.query.filter(StaffingUnit.unit_type != "sort").count(), 1)

    def test_bootstrap_repairs_former_worker_contracts_and_remains_idempotent(self):
        from app.models import (
            NeoRainFuelReviewAcknowledgement, NeoRainGoogleFuelValue,
            NeoScorpionSpearAuditEntry, NeoScorpionSpearCalibrationReset,
        )
        from app.services.neoscorpion_spear_schema import SPEAR_SETTINGS_COLUMNS, SPEAR_ASSIGNMENT_COLUMNS

        db.create_all()
        missing_columns = {
            "neosektor_ballmat_counts": [
                "spotter_mode", "right_first", "right_second", "right_open",
            ],
            "neoermac_door_pulls": ["sort_date_mission_id"],
            "neoscorpion_settings": list(SPEAR_SETTINGS_COLUMNS),
            "neoscorpion_fuel_assignments": list(SPEAR_ASSIGNMENT_COLUMNS),
            "motherbrain_google_integration_settings": ["rain_integration_mode", "rain_fuel_data_source"],
        }
        for table, columns in missing_columns.items():
            # Model-derived legacy fixture without constraints referencing the
            # not-yet-introduced columns (SQLite cannot DROP those in place).
            legacy = Table(table, MetaData(), *(
                Column(c.name, c.type, primary_key=c.primary_key,
                       nullable=c.nullable, server_default=c.server_default)
                for c in db.metadata.tables[table].columns if c.name not in columns
            ))
            db.metadata.tables[table].drop(db.engine)
            legacy.create(db.engine)
        missing_tables = (
            NeoRainFuelReviewAcknowledgement, NeoRainGoogleFuelValue,
            NeoScorpionSpearAuditEntry, NeoScorpionSpearCalibrationReset,
        )
        for model in missing_tables:
            model.__table__.drop(db.engine)

        for _ in range(2):
            bootstrap_database(self.app)
            inspector = inspect(db.engine)
            for table, columns in missing_columns.items():
                self.assertTrue(set(columns) <= {c["name"] for c in inspector.get_columns(table)})
            for model in missing_tables:
                self.assertTrue(inspector.has_table(model.__tablename__))
            self.assertEqual(User.query.count(), 1)

        # Both dialect maps, not duplicate bootstrap ALTERs, own the columns.
        for column, ddl in SPEAR_SETTINGS_COLUMNS.items():
            self.assertEqual(POSTGRES_OPTIONAL_COLUMNS["neoscorpion_settings"][column], ddl)
        for table, columns in missing_columns.items():
            self.assertTrue(set(columns) <= POSTGRES_OPTIONAL_COLUMNS[table].keys())
            self.assertTrue(set(columns) <= LOCAL_SQLITE_OPTIONAL_COLUMNS[table].keys())

    def test_bootstrap_repairs_legacy_ballmat_columns_preserving_counts_twice(self):
        from datetime import date
        from app.models import NeoSektorBallmatCount, NeoSektorSortState

        bootstrap_database(self.app)
        gateway = Gateway.query.first()
        sort = NeoSektorSortState(gateway_id=gateway.id, gateway_code=gateway.code,
                                 sort_date=date.today(), sort_name="night")
        db.session.add(sort)
        db.session.flush()
        row = NeoSektorBallmatCount(sort_state_id=sort.id, side="EAST", count=17, status="Full")
        db.session.add(row)
        db.session.commit()
        row_id = row.id
        columns = ("spotter_mode", "right_first", "right_second", "right_open")
        for column in columns:
            db.session.execute(text(f"ALTER TABLE neosektor_ballmat_counts DROP COLUMN {column}"))
        db.session.commit()
        db.session.remove()

        for _ in range(2):
            bootstrap_database(self.app)
            actual = {c["name"] for c in inspect(db.engine).get_columns("neosektor_ballmat_counts")}
            self.assertTrue(set(columns) <= actual)
            repaired = db.session.get(NeoSektorBallmatCount, row_id)
            self.assertEqual((repaired.count, repaired.status), (17, "Full"))
            self.assertEqual(tuple(getattr(repaired, c) for c in columns), (1, 0, 0, 0))
        for column in columns:
            expected = "INTEGER NOT NULL DEFAULT " + ("1" if column == "spotter_mode" else "0")
            self.assertEqual(POSTGRES_OPTIONAL_COLUMNS["neosektor_ballmat_counts"][column], expected)

    def test_schema_sync_creates_missing_rain_and_calibration_tables_only_once(self):
        from app.models import NeoRainFuelReviewAcknowledgement, NeoRainGoogleFuelValue, NeoScorpionSpearCalibrationReset
        db.create_all()
        models = (NeoRainFuelReviewAcknowledgement, NeoRainGoogleFuelValue, NeoScorpionSpearCalibrationReset)
        for model in models:
            model.__table__.drop(db.engine)
        _create_missing_application_tables(set(inspect(db.engine).get_table_names()))
        for model in models:
            self.assertTrue(inspect(db.engine).has_table(model.__tablename__))
        with patch("sqlalchemy.sql.schema.Table.create", side_effect=AssertionError("Redundant CREATE")):
            _create_missing_application_tables(set(inspect(db.engine).get_table_names()))

    def test_postgres_legacy_door_defaults_are_missing_only_and_non_destructive(self):
        inspector = Mock()
        columns = [{"name": name, "default": None} for name in ("no_first_mix_pull", "no_second_mix_pull")]
        columns.append({"name": "unrelated", "default": None})
        inspector.get_columns.return_value = columns
        def apply_default(statement):
            column_name = str(statement).split("ALTER COLUMN ")[1].split()[0]
            next(c for c in columns if c["name"] == column_name)["default"] = "false"
        with patch.object(db.session, "execute", side_effect=apply_default) as execute:
            _sync_neoermac_legacy_defaults_postgres(inspector, {"neoermac_door_pulls"})
            self.assertEqual(execute.call_count, 2)
            _sync_neoermac_legacy_defaults_postgres(inspector, {"neoermac_door_pulls"})
            self.assertEqual(execute.call_count, 2)
            self.assertTrue(all("SET DEFAULT FALSE" in str(c.args[0]) for c in execute.call_args_list))
        self.assertIsNone(columns[-1]["default"])

    def test_postgres_bootstrap_verifies_schema_before_any_seed_and_fails_loudly(self):
        from app.services.schema_sync import sync_database_schema
        # Real local schema synchronization, with the PostgreSQL verification
        # branch selected. No external server or fixture configuration is used.
        order = []
        def sync(app):
            order.append("sync")
            sync_database_schema(app)
        def fail_verification(connection):
            order.append("verify")
            raise RuntimeError("SPEAR contract missing")
        with (
            patch("app.services.database_bootstrap._is_sqlite_database", return_value=False),
            patch.dict(os.environ, {"BOOTSTRAP_ADMIN_PASSWORD": "CircuitRiverQuartz2026!"}),
            patch("app.services.database_bootstrap.sync_database_schema", side_effect=sync),
            patch("app.services.neoscorpion_spear_schema._verify_spear_schema_contract", side_effect=fail_verification),
            patch("app.services.database_bootstrap.ensure_default_gateway_and_nodes") as seed,
            patch.object(db.session, "commit") as commit,
        ):
            with self.assertRaisesRegex(RuntimeError, "SPEAR contract missing"):
                bootstrap_database(self.app)
            seed.assert_not_called()
            commit.assert_not_called()
        self.assertEqual(order, ["sync", "verify"])

    def test_bootstrap_success_order_includes_verification_before_seeds_and_commit(self):
        from contextlib import ExitStack
        from app.services import database_bootstrap as pipeline
        from app.services import neoscorpion_spear_schema as spear_schema
        order = []
        def tracked(label, function):
            def invoke(*args, **kwargs):
                order.append(label)
                return function(*args, **kwargs)
            return invoke
        # Exercise real schema/verification/seeding on isolated SQLite; choose
        # the production verification branch without making a Postgres socket.
        with ExitStack() as stack:
            stack.enter_context(patch.object(pipeline, "_is_sqlite_database", return_value=False))
            stack.enter_context(patch.dict(os.environ, {"BOOTSTRAP_ADMIN_PASSWORD": "CircuitRiverQuartz2026!"}))
            for owner, name, label in (
                (db, "create_all", "create"),
                (pipeline, "sync_database_schema", "sync"),
                (spear_schema, "_verify_spear_schema_contract", "verify"),
                (pipeline, "ensure_default_gateway_and_nodes", "gateway"),
                (pipeline, "ensure_default_permission_rules", "permissions"),
                (pipeline, "ensure_sheets_compatibility_setting", "sheets"),
                (pipeline, "ensure_google_motherbrain_live_polling_setting", "poll"),
                (pipeline, "_find_or_create_bootstrap_user", "admin"),
                (pipeline, "backfill_default_gateway_node_roles", "access"),
                (db.session, "commit", "commit"),
            ):
                stack.enter_context(patch.object(owner, name, side_effect=tracked(label, getattr(owner, name))))
            bootstrap_database(self.app)
        self.assertEqual(order, ["create", "sync", "verify", "gateway", "permissions", "sheets", "poll", "admin", "access", "commit"])

    def test_sqlite_fallback_is_used_when_database_url_missing(self):
        with patch.dict(os.environ, {}, clear=True):
            database_uri = resolve_database_uri()

        self.assertTrue(database_uri.startswith("sqlite:///"))
        self.assertIn("neoapps.sqlite", database_uri)

    def test_bootstrap_creates_gateway_nodes_admin_membership_and_roles(self):
        result = self._bootstrap()

        user = User.query.filter_by(username="Kessler").first()
        gateway = Gateway.query.filter_by(code="RFD").first()
        membership = GatewayMembership.query.filter_by(
            user_id=user.id,
            gateway_id=gateway.id,
        ).first()

        self.assertEqual(result["username"], "Kessler")
        self.assertEqual(gateway.name, "NeoGateway")
        self.assertTrue(gateway.is_active)
        self.assertEqual(
            {node.code for node in NeoNode.query.filter_by(is_active=True).all()},
            {code for code, _name, _sort_order in DEFAULT_NEONODES},
        )
        self.assertEqual(user.email, "bootstrap-admin@local.neoapps")
        self.assertEqual(user.role, "grandmaster")
        self.assertTrue(user.is_active)
        self.assertTrue(user.email_verified_at)
        self.assertFalse(user.password_reset_required)
        neosektor_settings = NeoSektorOperationalSetting.query.filter_by(
            gateway_id=gateway.id
        ).one()
        self.assertFalse(neosektor_settings.google_sheets_compat_enabled)
        self.assertEqual(neosektor_settings.integration_mode, "google_primary")
        google_polling_setting = MotherBrainGoogleIntegrationSetting.query.filter_by(
            gateway_id=gateway.id,
            sort_name="night",
        ).one()
        self.assertFalse(google_polling_setting.live_polling_enabled)
        self.assertTrue(user.check_password(LOCAL_SQLITE_FALLBACK_PASSWORD))
        self.assertTrue(result["created_user"])
        self.assertTrue(result["password_applied"])
        self.assertEqual(membership.status, "approved")
        self.assertTrue(membership.is_active)
        self.assertTrue(user_can_access_node(user, "RFD", "motherbrain", "grandmaster"))
        self.assertEqual(
            GatewayNodeRole.query.filter_by(
                gateway_membership_id=membership.id,
                role="grandmaster",
                is_active=True,
            ).count(),
            len(DEFAULT_NEONODES),
        )
        expected_rules = {
            permission_key: minimum_role
            for permission_key, minimum_role, _description in DEFAULT_PERMISSION_RULES
        }
        self.assertEqual(
            {
                rule.permission_key: rule.minimum_role
                for rule in PermissionRule.query.order_by(PermissionRule.permission_key).all()
            },
            expected_rules,
        )

    def test_bootstrap_updates_existing_kessler_user_without_overwriting_password(self):
        db.create_all()
        user = User(
            username="Kessler",
            email="old@example.com",
            role="watcher",
            is_active=False,
        )
        set_user_password(user, "OldPassword123!")
        db.session.add(user)
        db.session.commit()

        with patch.dict(
            os.environ,
            {
                "BOOTSTRAP_ADMIN_EMAIL": "bootstrap@example.com",
                "BOOTSTRAP_ADMIN_PASSWORD": "NewHarborSignal123!",
            },
            clear=False,
        ):
            bootstrap_database(self.app)

        updated = User.query.filter_by(username="Kessler").first()
        self.assertEqual(User.query.count(), 1)
        self.assertEqual(updated.email, "bootstrap@example.com")
        self.assertEqual(updated.role, "grandmaster")
        self.assertTrue(updated.is_active)
        self.assertTrue(updated.email_verified_at)
        self.assertTrue(updated.check_password("OldPassword123!"))
        self.assertFalse(updated.check_password("NewHarborSignal123!"))
        self.assertTrue(user_can_access_node(updated, "RFD", "motherbrain", "grandmaster"))

    def test_bootstrap_can_run_twice_without_duplicates(self):
        first_result = self._bootstrap()
        second_result = self._bootstrap()
        user = User.query.filter_by(username="Kessler").first()
        membership = GatewayMembership.query.filter_by(user_id=user.id).first()

        self.assertEqual(first_result["username"], second_result["username"])
        self.assertTrue(first_result["password_applied"])
        self.assertFalse(second_result["password_applied"])
        self.assertEqual(Gateway.query.filter_by(code="RFD").count(), 1)
        self.assertEqual(NeoNode.query.count(), len(DEFAULT_NEONODES))
        self.assertEqual(PermissionRule.query.count(), len(DEFAULT_PERMISSION_RULES))
        self.assertEqual(MotherBrainGoogleIntegrationSetting.query.count(), 1)
        self.assertEqual(User.query.filter_by(username="Kessler").count(), 1)
        self.assertEqual(GatewayMembership.query.filter_by(user_id=user.id).count(), 1)
        self.assertEqual(
            GatewayNodeRole.query.filter_by(gateway_membership_id=membership.id).count(),
            len(DEFAULT_NEONODES),
        )

    def test_bootstrap_rejects_passwords_that_fail_shared_policy(self):
        with patch.dict(
            os.environ,
            {"BOOTSTRAP_ADMIN_PASSWORD": "password123!"},
            clear=False,
        ):
            with self.assertRaisesRegex(ValueError, "commonly compromised"):
                bootstrap_database(self.app)

    def test_password_policy_migration_marks_existing_approved_users_only(self):
        db.create_all()
        approved_user = User(username="approved_policy_user")
        set_user_password(approved_user, "TestPassword123!")
        pending_user = User(username="pending_policy_user")
        set_user_password(pending_user, "TestPassword123!")
        db.session.add_all((approved_user, pending_user))
        db.session.flush()
        gateway = Gateway(code="PW", name="Password Policy")
        db.session.add(gateway)
        db.session.flush()
        db.session.add_all(
            (
                GatewayMembership(
                    user_id=approved_user.id,
                    gateway_id=gateway.id,
                    status="approved",
                    is_active=True,
                ),
                GatewayMembership(
                    user_id=pending_user.id,
                    gateway_id=gateway.id,
                    status="pending",
                    is_active=True,
                ),
            )
        )
        db.session.flush()

        _mark_existing_approved_users_for_password_policy_update(
            {"users", "gateway_memberships"}
        )
        db.session.commit()

        self.assertTrue(db.session.get(User, approved_user.id).password_policy_update_required)
        self.assertFalse(db.session.get(User, pending_user.id).password_policy_update_required)

    def test_legacy_final_mix_migrates_to_mix_pull_without_using_first_mix(self):
        db.create_all()
        db.session.execute(
            text(
                "ALTER TABLE master_flight_schedules "
                "ADD COLUMN first_mix_pull_time_local TIME"
            )
        )
        db.session.execute(
            text(
                "ALTER TABLE master_flight_schedules "
                "ADD COLUMN final_mix_pull_time_local TIME"
            )
        )
        master = MasterFlightSchedule(
            gateway_code="RFD",
            sort_name="night",
            mission_type="departure",
            flight_number="UPS100",
            origin="RFD",
            destination="SDF",
            active=True,
            active_days="monday",
            planned_time_local=time(2, 30),
            timezone="America/Chicago",
            pure_pull_time_local=time(1, 20),
        )
        db.session.add(master)
        db.session.flush()
        master_id = master.id
        db.session.execute(
            text(
                "UPDATE master_flight_schedules "
                "SET first_mix_pull_time_local = '01:40:00', "
                "final_mix_pull_time_local = '01:55:00' "
                "WHERE id = :master_id"
            ),
            {"master_id": master_id},
        )
        db.session.commit()

        _migrate_legacy_second_mix_pull_values({"master_flight_schedules"})
        db.session.commit()
        master = db.session.get(MasterFlightSchedule, master_id)

        self.assertEqual(master.pure_pull_time_local, time(1, 20))
        self.assertEqual(master.mix_pull_time_local, time(1, 55))
        self.assertNotIn(
            "first_mix_pull_time_local",
            MasterFlightSchedule.__table__.columns.keys(),
        )
        self.assertIn(
            "first_mix_pull_time_local",
            {column["name"] for column in inspect(db.engine).get_columns("master_flight_schedules")},
        )

        blank_mix = MasterFlightSchedule(
            gateway_code="RFD",
            sort_name="night",
            mission_type="departure",
            flight_number="UPS101",
            origin="RFD",
            destination="ONT",
            active=True,
            active_days="monday",
            planned_time_local=time(2, 45),
            timezone="America/Chicago",
        )
        db.session.add(blank_mix)
        db.session.flush()
        blank_mix_id = blank_mix.id
        db.session.execute(
            text(
                "UPDATE master_flight_schedules "
                "SET first_mix_pull_time_local = '02:10:00' "
                "WHERE id = :master_id"
            ),
            {"master_id": blank_mix_id},
        )
        db.session.commit()

        _migrate_legacy_second_mix_pull_values({"master_flight_schedules"})
        db.session.commit()
        blank_mix = db.session.get(MasterFlightSchedule, blank_mix_id)

        self.assertIsNone(blank_mix.mix_pull_time_local)

    def test_non_sqlite_bootstrap_requires_password_env(self):
        TestConfig = type(
            "PostgresConfig",
            (),
            {
                "SECRET_KEY": "test",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "postgresql://user:pass@example/db",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            },
        )
        postgres_app = create_app(TestConfig)

        with postgres_app.app_context(), patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                bootstrap_database(postgres_app)

    def _bootstrap(self):
        with patch.dict(os.environ, {}, clear=True):
            return bootstrap_database(self.app)


class AutoDatabaseBootstrapTest(unittest.TestCase):
    def test_auto_bootstrap_disabled_does_not_run_bootstrap(self):
        with patch.dict(os.environ, self._render_env(), clear=True):
            app = create_app(self._config(auto_bootstrap=False))

        with app.app_context():
            db.create_all()
            self.assertEqual(Gateway.query.count(), 0)
            self.assertEqual(NeoNode.query.count(), 0)
            db.drop_all()

    def test_auto_bootstrap_enabled_runs_bootstrap(self):
        with patch.dict(os.environ, self._render_env(), clear=True):
            with self.assertLogs("app", level="INFO") as logs:
                app = create_app(self._config(auto_bootstrap=True), auto_bootstrap=True)

        output = "\n".join(logs.output)
        self.assertIn("Auto bootstrap enabled", output)
        self.assertIn("Bootstrap completed", output)

        with app.app_context():
            user = User.query.filter_by(username="Kessler").first()
            gateway = Gateway.query.filter_by(code="RFD").first()
            membership = GatewayMembership.query.filter_by(
                user_id=user.id,
                gateway_id=gateway.id,
            ).first()

            self.assertIsNotNone(user)
            self.assertTrue(user.email_verified_at)
            self.assertEqual(membership.status, "approved")
            self.assertTrue(user_can_access_node(user, "RFD", "motherbrain", "grandmaster"))
            db.drop_all()

    def test_auto_bootstrap_true_without_database_url_skips_bootstrap(self):
        env = self._render_env()
        env.pop("DATABASE_URL")

        with patch.dict(os.environ, env, clear=True):
            with self.assertLogs("app", level="INFO") as logs:
                app = create_app(self._config(auto_bootstrap=True), auto_bootstrap=True)

        self.assertIn("Bootstrap skipped", "\n".join(logs.output))
        with app.app_context():
            db.create_all()
            self.assertEqual(Gateway.query.count(), 0)
            db.drop_all()

    def test_auto_bootstrap_can_run_twice_without_duplicates_or_password_overwrite(self):
        first_env = self._render_env(password="FirstHarborSignal123!")
        second_env = self._render_env(password="SecondHarborSignal123!")

        with patch.dict(os.environ, first_env, clear=True):
            app = create_app(self._config(auto_bootstrap=True), auto_bootstrap=True)

        with patch.dict(os.environ, second_env, clear=True):
            maybe_auto_bootstrap_database(app)

        with app.app_context():
            user = User.query.filter_by(username="Kessler").first()
            membership = GatewayMembership.query.filter_by(user_id=user.id).first()

            self.assertEqual(Gateway.query.filter_by(code="RFD").count(), 1)
            self.assertEqual(NeoNode.query.count(), len(DEFAULT_NEONODES))
            self.assertEqual(User.query.filter_by(username="Kessler").count(), 1)
            self.assertEqual(GatewayMembership.query.filter_by(user_id=user.id).count(), 1)
            self.assertEqual(
                GatewayNodeRole.query.filter_by(gateway_membership_id=membership.id).count(),
                len(DEFAULT_NEONODES),
            )
            self.assertTrue(user.check_password("FirstHarborSignal123!"))
            self.assertFalse(user.check_password("SecondHarborSignal123!"))
            db.drop_all()

    def test_auto_bootstrap_does_not_log_secrets(self):
        env = self._render_env(
            database_url="postgresql://neo_user:dbpass-placeholder@example.neon.tech/neogateway",
            password="HarborSignalPlaceholder123!",
        )
        env["BREVO_API_KEY"] = "brevo-api-key-placeholder"

        stdout = io.StringIO()
        with patch.dict(os.environ, env, clear=True):
            with redirect_stdout(stdout), self.assertLogs("app", level="INFO") as logs:
                app = create_app(self._config(auto_bootstrap=True), auto_bootstrap=True)

        output = stdout.getvalue() + "\n".join(logs.output)
        self.assertIn("Auto bootstrap enabled", output)
        self.assertIn("Bootstrap completed", output)
        self.assertNotIn("HarborSignalPlaceholder123!", output)
        self.assertNotIn("dbpass-placeholder", output)
        self.assertNotIn(env["DATABASE_URL"], output)
        self.assertNotIn("brevo-api-key-placeholder", output)

        with app.app_context():
            db.drop_all()

    def _config(self, auto_bootstrap):
        return type(
            "AutoBootstrapTestConfig",
            (),
            {
                "SECRET_KEY": "test",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
                "AUTO_BOOTSTRAP_DATABASE": auto_bootstrap,
            },
        )

    def _render_env(
        self,
        database_url="postgresql://neo_user:dbpass@example.neon.tech/neogateway",
        password="HarborSignalPassword123!",
    ):
        return {
            "DATABASE_URL": database_url,
            "AUTO_BOOTSTRAP_DATABASE": "true",
            "BOOTSTRAP_ADMIN_USERNAME": "Kessler",
            "BOOTSTRAP_ADMIN_EMAIL": "bootstrap@example.com",
            "BOOTSTRAP_ADMIN_PASSWORD": password,
            "BREVO_API_KEY": "test-brevo-key",
            "MAIL_FROM_NAME": "NeoGateway",
            "MAIL_FROM_EMAIL": "no-reply@example.com",
            "APP_BASE_URL": "https://example.com",
        }


if __name__ == "__main__":
    unittest.main()
