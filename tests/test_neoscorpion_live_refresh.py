import unittest
from contextlib import ExitStack
from datetime import date
from unittest.mock import Mock, patch

from sqlalchemy import event, inspect, text

from app import create_app
from app.extensions import db
from app.models import (
    GatewayMembership,
    GatewayNodeRole,
    LiveScreenRefreshSetting,
    NeoNode,
    NeoScorpionSortAssetState,
    PortalAppAccess,
    SortDateOperation,
    User,
)
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.live_screen_refresh import (
    LIVE_SCREEN_REFRESH_ALLOWED_SECONDS,
    live_screen_refresh_value,
    save_live_screen_refresh_override,
)
from app.services.live_screen_refresh_schema import (
    LIVE_SCREEN_REFRESH_SCHEMA_LOCK_KEY,
    _verify_live_screen_refresh_schema,
    ensure_live_screen_refresh_setting_table,
)
from app.services.password_policy import set_user_password
from app.services.permission_rules import ensure_default_permission_rules
from app.services.schema_sync import sync_local_sqlite_schema


class NeoScorpionLiveRefreshTest(unittest.TestCase):
    def setUp(self):
        self.config = type(
            "NeoScorpionLiveRefreshConfig",
            (),
            {
                "SECRET_KEY": "test",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
                "LIVE_SCREEN_REFRESH_INTERVAL_MS": 1000,
            },
        )
        self.app = create_app(self.config)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.gateway = ensure_default_gateway_and_nodes()
        ensure_default_permission_rules()
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_schema_model_factory_wiring_and_post_contract(self):
        table = LiveScreenRefreshSetting.__table__
        self.assertEqual(
            set(table.columns.keys()),
            {
                "id",
                "gateway_id",
                "screen_key",
                "interval_seconds",
                "created_at",
                "updated_at",
            },
        )
        constraint_names = {constraint.name for constraint in table.constraints}
        self.assertIn("uq_live_screen_refresh_setting_gateway_screen", constraint_names)
        self.assertIn("ck_live_screen_refresh_setting_interval", constraint_names)
        self.assertIn(table.name, inspect(db.engine).get_table_names())
        _verify_live_screen_refresh_schema(db.session.connection())

        with patch("app.ensure_live_screen_refresh_setting_table") as ensure:
            app = create_app(self.config)
            response = app.test_client().get("/login")
        self.assertEqual(response.status_code, 200)
        ensure.assert_called_once_with(app)

    def test_targeted_postgresql_ensure_is_bounded_and_idempotent(self):
        self.app.config.update(
            TESTING=False,
            SQLALCHEMY_DATABASE_URI="postgresql://example.test/neoapps",
        )
        connection = Mock()
        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "app.services.live_screen_refresh_schema.db.session.connection",
                    return_value=connection,
                )
            )
            commit = stack.enter_context(
                patch("app.services.live_screen_refresh_schema.db.session.commit")
            )
            create = stack.enter_context(
                patch.object(LiveScreenRefreshSetting.__table__, "create")
            )
            verify = stack.enter_context(
                patch(
                    "app.services.live_screen_refresh_schema._verify_live_screen_refresh_schema"
                )
            )

            self.assertTrue(ensure_live_screen_refresh_setting_table(self.app))
            self.assertTrue(ensure_live_screen_refresh_setting_table(self.app))

        statements = "\n".join(
            str(call.args[0]) for call in connection.execute.call_args_list
        )
        self.assertIn("SET LOCAL lock_timeout", statements)
        self.assertIn("pg_advisory_xact_lock", statements)
        self.assertIn("neoscorpion.refresh_settings.edit", statements)
        self.assertIn("ON CONFLICT (permission_key) DO NOTHING", statements)
        self.assertEqual(
            connection.execute.call_args_list[1].args[1]["lock_key"],
            LIVE_SCREEN_REFRESH_SCHEMA_LOCK_KEY,
        )
        self.assertEqual(create.call_count, 2)
        create.assert_called_with(bind=connection, checkfirst=True)
        self.assertEqual(verify.call_count, 2)
        self.assertEqual(commit.call_count, 2)

    def test_local_schema_sync_recreates_only_missing_refresh_table(self):
        db.session.execute(text("DROP TABLE live_screen_refresh_settings"))
        db.session.commit()

        sync_local_sqlite_schema(self.app)
        db.session.commit()
        sync_local_sqlite_schema(self.app)
        db.session.commit()

        self.assertIn(
            "live_screen_refresh_settings",
            inspect(db.engine).get_table_names(),
        )

    def test_fallback_overrides_off_allowed_values_and_reset(self):
        value = live_screen_refresh_value(
            self.gateway,
            "neoscorpion.fuel_dispatch",
        )
        self.assertEqual(value.effective_interval_ms, 5000)
        self.assertEqual(value.source, "render_default")
        self.assertEqual(LiveScreenRefreshSetting.query.count(), 0)

        for seconds in LIVE_SCREEN_REFRESH_ALLOWED_SECONDS:
            result = save_live_screen_refresh_override(
                self.gateway,
                "neoscorpion.fuel_dispatch",
                "off" if seconds == 0 else str(seconds),
            )
            self.assertTrue(result.changed)
            db.session.commit()
            value = live_screen_refresh_value(
                self.gateway,
                "neoscorpion.fuel_dispatch",
            )
            self.assertEqual(value.override_seconds, seconds)
            self.assertEqual(value.effective_interval_ms, seconds * 1000)
            self.assertEqual(value.source, "override")

        reset = save_live_screen_refresh_override(
            self.gateway,
            "neoscorpion.fuel_dispatch",
            "default",
        )
        self.assertTrue(reset.changed)
        db.session.commit()
        self.assertEqual(LiveScreenRefreshSetting.query.count(), 0)
        self.assertEqual(
            live_screen_refresh_value(
                self.gateway,
                "neoscorpion.fuel_dispatch",
            ).effective_interval_ms,
            5000,
        )

    def test_sub_five_second_override_is_rejected_without_write(self):
        with self.assertRaisesRegex(ValueError, "OFF, 5, 10, 15, 30, or 60"):
            save_live_screen_refresh_override(
                self.gateway,
                "neoscorpion.fuel_dispatch",
                "1",
            )
        db.session.rollback()
        self.assertEqual(LiveScreenRefreshSetting.query.count(), 0)

    def test_refresh_controls_are_central_and_only_grandmaster_can_edit(self):
        master = self._add_user("refresh_master", "master")
        grandmaster = self._add_user("refresh_grandmaster", "grandmaster")
        db.session.commit()

        self._login(master)
        response = self.client.get("/neoscorpion/settings")
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("refresh_interval_seconds", body)
        self.assertIn("Fuel Settings Shell", body)
        self.assertEqual(LiveScreenRefreshSetting.query.count(), 0)
        denied = self.client.post(
            "/motherbrain/system-settings/node-refresh-timings",
            data={
                "screen_key": "neoscorpion.fuel_dispatch",
                "refresh_interval_seconds": "10",
            },
        )
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(LiveScreenRefreshSetting.query.count(), 0)

        self._login(grandmaster)
        saved = self.client.post(
            "/motherbrain/system-settings/node-refresh-timings",
            data={
                "screen_key": "neoscorpion.fuel_dispatch",
                "refresh_interval_seconds": "10",
            },
            follow_redirects=True,
        )
        self.assertEqual(saved.status_code, 200)
        self.assertIn(b"Node refresh timing saved", saved.data)
        setting = LiveScreenRefreshSetting.query.one()
        self.assertEqual(setting.interval_seconds, 10)

    def test_dispatch_revision_is_tiny_one_query_and_read_only(self):
        dispatcher = self._add_user("live_dispatcher", "simulator")
        operation = self._add_operation(revision=6)
        db.session.commit()
        self._login(dispatcher)
        statements = []

        def capture(_connection, _cursor, statement, _params, _context, _many):
            statements.append(statement.strip())

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            with patch.object(db.session, "commit", wraps=db.session.commit) as commit:
                response = self.client.get("/neoscorpion/fuel-dispatch/revision")
                self.assertEqual(commit.call_count, 0)
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json(),
            {"operation_id": operation.id, "revision": 6},
        )
        fingerprint_queries = [
            statement
            for statement in statements
            if "sort_date_operations" in statement.lower()
            and "neoscorpion_sort_asset_states" in statement.lower()
        ]
        self.assertEqual(len(fingerprint_queries), 1)
        self.assertFalse(
            any(
                statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
                for statement in statements
            )
        )

    def test_hanzo_revision_and_hooks_reuse_the_shared_read_only_fingerprint(self):
        dispatcher = self._add_user("hanzo_dispatcher", "simulator")
        operation = self._add_operation(revision=7)
        db.session.add(
            LiveScreenRefreshSetting(
                gateway_id=self.gateway.id,
                screen_key="neoscorpion.hanzo",
                interval_seconds=30,
            )
        )
        db.session.commit()
        self._login(dispatcher)

        response = self.client.get("/neoscorpion/hanzo/revision")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"operation_id": operation.id, "revision": 7})

        page = self.client.get("/neoscorpion/hanzo").get_data(as_text=True)
        self.assertIn('data-hanzo-live', page)
        self.assertIn('data-refresh-interval-ms="30000"', page)
        self.assertIn("neoscorpion_hanzo_live.js", page)
        with open("app/static/js/neoscorpion_hanzo_live.js", encoding="utf-8") as source:
            script = source.read()
        self.assertIn("continuousWhileVisible: true", script)
        self.assertIn("immediate: true", script)
        self.assertIn("window.location.reload()", script)

    def test_dispatch_hooks_dirty_banner_and_effective_interval_render(self):
        dispatcher = self._add_user("dispatch_hooks", "simulator")
        operation = self._add_operation(revision=3)
        db.session.add(
            LiveScreenRefreshSetting(
                gateway_id=self.gateway.id,
                screen_key="neoscorpion.fuel_dispatch",
                interval_seconds=30,
            )
        )
        db.session.commit()
        self._login(dispatcher)

        response = self.client.get("/neoscorpion/fuel-dispatch")
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("data-fuel-dispatch-live", body)
        self.assertIn(f'data-operation-id="{operation.id}"', body)
        self.assertIn('data-revision="3"', body)
        self.assertIn('data-refresh-interval-ms="30000"', body)
        self.assertIn("UPDATES AVAILABLE", body)
        self.assertIn("REFRESH NOW", body)
        self.assertIn("neoscorpion_fuel_dispatch_live.js", body)
        self.assertNotIn("KEEP LIVE / MONITOR MODE", body)

        with open(
            "app/static/js/neoscorpion_fuel_dispatch_live.js",
            encoding="utf-8",
        ) as source:
            script = source.read()
        self.assertIn("continuousWhileVisible: true", script)
        self.assertIn('root.dataset.liveDirty = "true"', script)
        self.assertIn("updateBanner.hidden = false", script)
        self.assertIn("window.location.reload()", script)
        self.assertNotIn("setMonitorMode", script)

    def _add_operation(self, *, revision):
        operation = SortDateOperation(
            gateway_id=self.gateway.id,
            sort_date=date(2026, 8, 18),
            gateway_code=self.gateway.code,
            sort_name="night",
            window_minutes=360,
        )
        db.session.add(operation)
        db.session.flush()
        db.session.add(
            NeoScorpionSortAssetState(
                sort_date_operation_id=operation.id,
                revision=revision,
            )
        )
        return operation

    def _add_user(self, username, role):
        user = User(
            username=username,
            email=f"{username}@example.test",
            first_name=username.replace("_", " ").title(),
            role="watcher",
            is_active=True,
        )
        set_user_password(user, "TestPassword123!")
        db.session.add(user)
        db.session.flush()
        membership = GatewayMembership(
            user_id=user.id,
            gateway_id=self.gateway.id,
            status="approved",
            is_active=True,
        )
        db.session.add(membership)
        db.session.flush()
        scorpion = NeoNode.query.filter_by(code="scorpion").one()
        db.session.add_all(
            [
                PortalAppAccess(
                    user_id=user.id,
                    app_code="neogateway",
                    status="approved",
                    role=role,
                    is_active=True,
                ),
                GatewayNodeRole(
                    gateway_membership_id=membership.id,
                    node_id=scorpion.id,
                    role=role,
                    is_active=True,
                ),
            ]
        )
        return user

    def _login(self, user):
        self.client.post(
            "/login",
            data={"email": user.email, "password": "TestPassword123!"},
            follow_redirects=False,
        )


if __name__ == "__main__":
    unittest.main()
