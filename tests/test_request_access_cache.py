from contextlib import contextmanager
from datetime import date, datetime, time
import unittest
from unittest.mock import patch

from flask import g, render_template_string
from sqlalchemy import event

from app import create_app
from app.auth.decorators import gateway_node_required
from app.extensions import db
from app.models import (
    GatewayMembership,
    GatewayNodeRole,
    NeoNode,
    PermissionRule,
    PortalAppAccess,
    SortDateOperation,
    SortTimelineSettings,
    SortTimelineSortSetting,
    User,
)
from app.services.access_control import (
    backfill_default_gateway_node_roles,
    get_current_gateway,
    get_user_app_access,
    get_user_gateway_membership,
    get_user_node_role,
    prime_lightweight_live_request_scope,
    user_can_access_node,
    user_has_gateway_access,
)
from app.services.node_refresh import node_auto_refresh_status
from app.services.operation_scope import operation_by_id
from app.services.password_policy import set_user_password
from app.services.permission_rules import ensure_default_permission_rules, user_can


class RequestAccessCacheTest(unittest.TestCase):
    def setUp(self):
        TestConfig = type(
            "TestConfig",
            (),
            {
                "SECRET_KEY": "test",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            },
        )
        self.app = create_app(TestConfig)
        self._register_probe_route()
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        ensure_default_permission_rules()

        self.user = User(username="request_cache_user", role="watcher")
        set_user_password(self.user, "TestPassword123!")
        db.session.add(self.user)
        db.session.flush()
        backfill_default_gateway_node_roles(self.user, role="simulator")
        db.session.commit()

        self.client = self.app.test_client()
        self.client.post(
            "/login",
            data={
                "username": self.user.username,
                "password": "TestPassword123!",
            },
        )

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_repeated_current_gateway_resolution_queries_once_per_request(self):
        statements = []
        with self.app.test_request_context("/_cache-direct"), self._capture_selects(statements):
            first = get_current_gateway()
            second = get_current_gateway()
            third = get_current_gateway()

        self.assertEqual(first.id, second.id)
        self.assertEqual(second.id, third.id)
        self.assertEqual(self._table_query_count(statements, "gateways"), 1)

    def test_repeated_user_can_reuses_role_and_permission_resolution(self):
        statements = []
        with self.app.test_request_context("/_cache-direct"), self._capture_selects(statements):
            self.assertTrue(user_can("neoermac.dashboard.view", self.user))
            self.assertTrue(user_can("NEOERMAC.DASHBOARD.VIEW", self.user))
            self.assertTrue(user_can(" neoermac.dashboard.view ", self.user))

        self.assertEqual(self._permission_query_count(statements, "neoermac.dashboard.view"), 1)
        self.assertEqual(self._table_query_count(statements, "gateway_memberships"), 1)
        self.assertEqual(self._table_query_count(statements, "portal_app_accesses"), 1)
        self.assertEqual(self._table_query_count(statements, "gateway_node_roles"), 1)

    def test_decorator_route_and_template_share_access_resolution(self):
        statements = []
        with self._capture_selects(statements):
            response = self.client.get("/_request-cache-probe")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_data(as_text=True).strip(), "RFD|True|True")
        self.assertEqual(self._table_query_count(statements, "gateway_memberships"), 1)
        self.assertEqual(self._table_query_count(statements, "portal_app_accesses"), 1)
        self.assertEqual(self._table_query_count(statements, "gateway_node_roles"), 1)
        self.assertEqual(self._permission_query_count(statements, "neoermac.dashboard.view"), 1)

    def test_independent_request_resolves_updated_permission_again(self):
        first_statements = []
        with self._capture_selects(first_statements):
            first = self.client.get("/_request-cache-probe")

        rule = PermissionRule.query.filter_by(
            permission_key="neoermac.dashboard.view"
        ).one()
        rule.minimum_role = "master"
        db.session.commit()

        second_statements = []
        with self._capture_selects(second_statements):
            second = self.client.get("/_request-cache-probe")

        self.assertEqual(first.get_data(as_text=True).strip(), "RFD|True|True")
        self.assertEqual(second.get_data(as_text=True).strip(), "RFD|False|False")
        for statements in (first_statements, second_statements):
            self.assertEqual(
                self._permission_query_count(statements, "neoermac.dashboard.view"),
                1,
            )
            self.assertEqual(self._table_query_count(statements, "gateway_memberships"), 1)
            self.assertEqual(self._table_query_count(statements, "gateway_node_roles"), 1)

    def test_lightweight_scope_seeds_access_operation_and_window_in_one_query(self):
        gateway = get_current_gateway()
        settings = SortTimelineSettings(
            gateway_id=gateway.id,
            gateway_code=gateway.code,
        )
        sort_setting = SortTimelineSortSetting(
            timeline_settings=settings,
            gateway_id=gateway.id,
            gateway_code=gateway.code,
            sort_name="night",
            sort_window_start_local=time(0, 0),
            sort_window_end_local=time(23, 59),
            ops_window_start_local=time(0, 0),
            ops_window_end_local=time(23, 59),
        )
        operation = SortDateOperation(
            gateway_id=gateway.id,
            gateway_code=gateway.code,
            sort_date=date.today(),
            sort_name="night",
        )
        db.session.add_all([settings, sort_setting, operation])
        db.session.commit()
        user_id = self.user.id
        operation_id = operation.id
        gateway_id = gateway.id

        statements = []
        with self.app.test_request_context("/_lightweight-scope"), self._capture_selects(
            statements
        ):
            g.is_lightweight_live_state_request = True
            scope = prime_lightweight_live_request_scope(
                self.user,
                "motherbrain",
                operation_id=operation_id,
            )
            self.assertEqual(scope.membership.user_id, user_id)
            self.assertEqual(get_current_gateway().id, gateway_id)
            self.assertEqual(
                get_user_gateway_membership(self.user, gateway.code).id,
                scope.membership.id,
            )
            self.assertEqual(
                get_user_app_access(self.user, "neogateway").id,
                scope.app_access.id,
            )
            self.assertEqual(
                get_user_node_role(self.user, gateway.code, "motherbrain"),
                "simulator",
            )
            self.assertEqual(operation_by_id(operation_id).id, operation_id)
            refresh = node_auto_refresh_status(
                scope.gateway,
                operation=scope.operation,
                now=datetime.combine(date.today(), time(12, 0)),
            )

        self.assertTrue(refresh["auto_refresh_enabled"])
        self.assertEqual(len(statements), 1)
        for table_name in (
            "gateways",
            "gateway_memberships",
            "portal_app_accesses",
            "neo_nodes",
            "gateway_node_roles",
            "sort_date_operations",
            "sort_timeline_sort_settings",
        ):
            self.assertEqual(self._table_query_count(statements, table_name), 1)

    def test_lightweight_scope_preserves_access_denials(self):
        gateway = get_current_gateway()
        membership = GatewayMembership.query.filter_by(
            user_id=self.user.id,
            gateway_id=gateway.id,
        ).one()
        app_access = PortalAppAccess.query.filter_by(
            user_id=self.user.id,
            app_code="neogateway",
        ).one()
        node = NeoNode.query.filter_by(code="motherbrain").one()
        node_role = GatewayNodeRole.query.filter_by(
            gateway_membership_id=membership.id,
            node_id=node.id,
        ).one()

        membership.status = "denied"
        db.session.commit()
        with self.app.test_request_context("/_membership-denied"):
            g.is_lightweight_live_state_request = True
            prime_lightweight_live_request_scope(self.user, "motherbrain")
            self.assertFalse(user_has_gateway_access(self.user, gateway.code))
            self.assertIsNone(get_user_node_role(self.user, gateway.code, "motherbrain"))

        membership.status = "approved"
        app_access.status = "denied"
        db.session.commit()
        with self.app.test_request_context("/_app-denied"):
            g.is_lightweight_live_state_request = True
            prime_lightweight_live_request_scope(self.user, "motherbrain")
            self.assertFalse(user_has_gateway_access(self.user, gateway.code))
            self.assertIsNone(get_user_node_role(self.user, gateway.code, "motherbrain"))

        app_access.status = "approved"
        node_role.role = "watcher"
        db.session.commit()
        with self.app.test_request_context("/_role-denied"):
            g.is_lightweight_live_state_request = True
            prime_lightweight_live_request_scope(self.user, "motherbrain")
            self.assertTrue(user_has_gateway_access(self.user, gateway.code))
            self.assertFalse(
                user_can_access_node(
                    self.user,
                    gateway.code,
                    "motherbrain",
                    minimum_role="operator",
                )
            )

    def test_lightweight_scope_is_fresh_per_request_and_after_commit(self):
        gateway = get_current_gateway()
        membership = GatewayMembership.query.filter_by(
            user_id=self.user.id,
            gateway_id=gateway.id,
        ).one()
        node = NeoNode.query.filter_by(code="motherbrain").one()
        node_role = GatewayNodeRole.query.filter_by(
            gateway_membership_id=membership.id,
            node_id=node.id,
        ).one()

        with self.app.test_request_context("/_first-live-request"):
            g.is_lightweight_live_state_request = True
            prime_lightweight_live_request_scope(self.user, "motherbrain")
            self.assertEqual(
                get_user_node_role(self.user, gateway.code, "motherbrain"),
                "simulator",
            )

            node_role.role = "watcher"
            db.session.commit()
            self.assertEqual(
                get_user_node_role(self.user, gateway.code, "motherbrain"),
                "watcher",
            )

        node_role.role = "master"
        db.session.commit()
        statements = []
        with self.app.test_request_context("/_second-live-request"), self._capture_selects(
            statements
        ):
            g.is_lightweight_live_state_request = True
            prime_lightweight_live_request_scope(self.user, "motherbrain")
            self.assertEqual(
                get_user_node_role(self.user, gateway.code, "motherbrain"),
                "master",
            )

        self.assertEqual(self._table_query_count(statements, "gateway_node_roles"), 1)

    def test_normal_page_and_write_method_do_not_prime_lightweight_scope(self):
        with patch(
            "app.services.access_control.prime_lightweight_live_request_scope"
        ) as prime_scope:
            page = self.client.get("/neoermac")
            write_method = self.client.post("/neoermac/door-view/state")

        self.assertEqual(page.status_code, 200)
        self.assertEqual(write_method.status_code, 405)
        prime_scope.assert_not_called()

    def _register_probe_route(self):
        @gateway_node_required("ermac")
        def request_cache_probe():
            gateway = get_current_gateway()
            route_allowed = user_can("neoermac.dashboard.view")
            return render_template_string(
                "{{ gateway.code }}|{{ route_allowed }}|"
                "{{ user_can('neoermac.dashboard.view') }}",
                gateway=gateway,
                route_allowed=route_allowed,
            )

        self.app.add_url_rule(
            "/_request-cache-probe",
            endpoint="request_cache_probe",
            view_func=request_cache_probe,
        )

    @contextmanager
    def _capture_selects(self, statements):
        def capture(_connection, _cursor, statement, parameters, _context, _executemany):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append((" ".join(statement.lower().split()), parameters))

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            yield
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)

    def _table_query_count(self, statements, table_name):
        return sum(table_name in statement for statement, _parameters in statements)

    def _permission_query_count(self, statements, permission_key):
        return sum(
            "permission_rules" in statement and permission_key in repr(parameters)
            for statement, parameters in statements
        )


if __name__ == "__main__":
    unittest.main()
