from datetime import datetime
import unittest

from app import create_app
from app.extensions import db
from app.models import GatewayMembership, GatewayNodeRole, NeoNode, PermissionRule, User
from app.services.access_control import backfill_default_gateway_node_roles
from app.services.permission_rules import ensure_default_permission_rules, user_can
from app.services.password_policy import set_user_password
from app.services.request_cache import clear_request_cache
from app.services.shell_metadata import resolve_shell_metadata


class NeoRainRoutesTest(unittest.TestCase):
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
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        ensure_default_permission_rules()
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_neorain_blueprint_and_workspace_routes_are_registered(self):
        endpoints = {rule.endpoint for rule in self.app.url_map.iter_rules()}
        self.assertTrue(
            {
                "neorain.index",
                "neorain.inbound",
                "neorain.outbound",
                "neorain.load_planner_lineup",
                "neorain.settings",
            }
            <= endpoints
        )

    def test_neorain_shell_does_not_enable_google_live_polling(self):
        with self.app.test_request_context("/neorain/inbound") as request_context:
            metadata = resolve_shell_metadata(request_context.request, is_authenticated=True)

        self.assertTrue(metadata["is_neorain_page"])
        self.assertFalse(metadata["uses_google_live_poll_heartbeat"])

    def test_first_visit_defaults_to_inbound_and_remembers_last_valid_page(self):
        user = self._rain_user("rain_navigation_user", "watcher")
        self._login(user)

        first = self.client.get("/neorain", follow_redirects=False)
        inbound = self.client.get("/neorain/inbound")
        outbound = self.client.get("/neorain/outbound")
        remembered = self.client.get("/neorain/", follow_redirects=False)

        self.assertEqual(first.location, "/neorain/inbound")
        self.assertEqual(inbound.status_code, 200)
        self.assertIn(b"INBOUND", inbound.data)
        self.assertEqual(outbound.status_code, 200)
        self.assertIn(b"OUTBOUND", outbound.data)
        self.assertEqual(remembered.location, "/neorain/outbound")

    def test_all_four_pages_require_rain_node_access(self):
        user = self._rain_user("rain_watcher", "watcher")
        self._login(user)

        for path, heading in (
            ("/neorain/inbound", b"INBOUND"),
            ("/neorain/outbound", b"OUTBOUND"),
            ("/neorain/load-planner-lineup", b"LOAD PLANNER LINEUP"),
            ("/neorain/settings", b"SETTINGS"),
        ):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(heading, response.data)

    def test_rain_permission_uses_the_rain_node_role(self):
        user = self._rain_user("rain_permission_user", "master")
        rain = NeoNode.query.filter_by(code="rain").one()
        membership = GatewayMembership.query.filter_by(user_id=user.id).one()
        rain_role = GatewayNodeRole.query.filter_by(
            gateway_membership_id=membership.id,
            node_id=rain.id,
        ).one()
        rain_role.role = "watcher"
        db.session.commit()
        clear_request_cache()

        self.assertFalse(user_can("neorain.inbound.edit", user))
        rain_role.role = "simulator"
        db.session.commit()
        clear_request_cache()
        self.assertTrue(user_can("neorain.inbound.edit", user))

    def test_rain_read_only_view_follows_node_access_not_redundant_threshold(self):
        user = self._rain_user("rain_denied_user", "master")
        rain = NeoNode.query.filter_by(code="rain").one()
        membership = GatewayMembership.query.filter_by(user_id=user.id).one()
        rain_role = GatewayNodeRole.query.filter_by(
            gateway_membership_id=membership.id,
            node_id=rain.id,
        ).one()
        rain_role.role = "watcher"
        PermissionRule.query.filter_by(permission_key="neorain.inbound.view").one().minimum_role = "simulator"
        db.session.commit()
        clear_request_cache()
        self._login(user)

        response = self.client.get("/neorain/inbound", follow_redirects=False)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(user_can("neorain.inbound.edit", user))

    def _rain_user(self, username, role):
        user = User(
            username=username,
            email=f"{username}@example.com",
            first_name="Rain",
            last_name="User",
            full_name="Rain User",
            employee_id=f"EMP-{username}",
            email_verified_at=datetime.utcnow(),
            role=role,
            is_active=True,
        )
        set_user_password(user, "TestPassword123!")
        db.session.add(user)
        db.session.flush()
        backfill_default_gateway_node_roles(user, role=role)
        db.session.commit()
        return user

    def _login(self, user):
        return self.client.post(
            "/login",
            data={"username": user.username, "password": "TestPassword123!"},
            follow_redirects=False,
        )
