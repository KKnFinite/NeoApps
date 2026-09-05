import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from app import create_app
from app.extensions import db
from app.models import PortalAppAccess, SortDateOperation, User
from app.services.access_control import (
    backfill_default_gateway_node_roles,
    ensure_default_gateway_and_nodes,
)
from app.services.password_policy import set_user_password
from app.services.permission_rules import ensure_default_permission_rules


class GatewayShellTest(unittest.TestCase):
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
        self.gateway = ensure_default_gateway_and_nodes()
        ensure_default_permission_rules()
        self.user = User(username="gateway-shell", role="grandmaster")
        set_user_password(self.user, "TestPassword123!")
        db.session.add(self.user)
        db.session.flush()
        backfill_default_gateway_node_roles(self.user, role="grandmaster")
        db.session.add(
            PortalAppAccess(
                user_id=self.user.id,
                app_code="neostaffing",
                status="approved",
                role="grandmaster",
                is_active=True,
            )
        )
        db.session.commit()
        self.client = self.app.test_client()
        self.client.post(
            "/login",
            data={"username": "gateway-shell", "password": "TestPassword123!"},
        )

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def _hub_with_operation(self, operation, *, node_access=None):
        state = {"operations": [operation]}
        node_access = node_access or (lambda _user, _gateway, _node: True)
        with patch("app.neomotherbrain.routes._current_sort_state", return_value=state), patch(
            "app.neomotherbrain.routes.user_can_access_node", side_effect=node_access
        ):
            return self.client.get(f"/rfd?operation_id={operation.id}")

    def test_gateway_renders_its_own_header_launcher_and_locked_node_logos(self):
        response = self.client.get("/rfd")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"data-gateway-shell", response.data)
        self.assertIn(b"gateway-shell-topbar", response.data)
        self.assertIn(b"gateway-mobile-header", response.data)
        self.assertIn(b"gateway-mobile-bottom-nav", response.data)
        self.assertIn(b"gateway-page-mobile-context", response.data)
        self.assertIn(b"gateway-node-mobile-title", response.data)
        self.assertIn(b"gateway-node-title--short", response.data)
        self.assertIn(b"gateway-node-title--medium", response.data)
        self.assertIn(b"gateway-node-title--long", response.data)
        self.assertIn(b"Operational Nodes", response.data)
        self.assertNotIn(b"data-operational-sidebar", response.data)
        self.assertIn(b"images/logos/newlogo_motherbrain_small.png", response.data)
        self.assertIn(b"images/logos/newlogo_sektor_small.png", response.data)
        self.assertIn(b"images/logos/newlogo_reptile_small.png", response.data)
        self.assertIn(b"Operational Brain", response.data)
        self.assertIn(b"Inbound Ops", response.data)
        self.assertIn(b"Shift Ops", response.data)
        self.assertIn(b"Fueling Ops \xc2\xb7 SPEAR", response.data)
        self.assertIn(b"Load Planning Ops", response.data)
        self.assertIn(b"Deice Ops \xc2\xb7 FROST", response.data)
        self.assertIn(b"Ramp Ops", response.data)
        self.assertIn(b"Coming Soon", response.data)
        self.assertNotIn(b">Available<", response.data)
        self.assertIn(b"js/mobile_drawer.js", response.data)
        self.assertIn(b'images/hero/hero_gateway.png', response.data)
        self.assertIn(b'images/hero/hero_gateway_small.png', response.data)
        self.assertIn(b'width="2172" height="724"', response.data)
        self.assertEqual(response.data.count(b'class="gateway-dashboard-hero"'), 1)
        self.assertNotIn(b'Choose a system to enter', response.data)
        self.assertNotIn(b'gateway-page-identity', response.data)

    def test_gateway_retains_selected_sort_controls(self):
        operations = [SortDateOperation(gateway_id=self.gateway.id, gateway_code=self.gateway.code,
                      sort_date=date(2026,9,4), sort_name=name) for name in ('am','pm')]
        db.session.add_all(operations)
        db.session.commit()
        selected = operations[1]
        with patch('app.neomotherbrain.routes._current_sort_state', return_value={'operations':operations}):
            response = self.client.get(f'/rfd?operation_id={selected.id}')
        self.assertEqual(response.status_code, 200)
        self.assertIn(f'value="{selected.id}" selected'.encode(), response.data)
        self.assertIn(b'name="operation_id" data-submit-on-change', response.data)
        self.assertIn(f'href="/motherbrain/manage-sort?operation_id={selected.id}"'.encode(), response.data)

    def test_gateway_mobile_hero_keeps_its_title_in_a_separate_row(self):
        css = (Path(__file__).resolve().parents[1] / "app" / "static" / "css" / "base.css").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            ".gateway-node-motherbrain { grid-column:1 / -1; grid-template-rows:auto minmax(0, 1fr) auto;",
            css,
        )
        self.assertIn(".gateway-node-motherbrain .gateway-node-mobile-title { display:block; }", css)
        self.assertIn(
            ".gateway-node-motherbrain .gateway-node-card-copy .gateway-node-desktop-title { display:none; }",
            css,
        )

    def test_gateway_preserves_operation_forwarding_for_node_launches(self):
        operation = SortDateOperation(
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
            sort_date=date(2026, 9, 4),
            sort_name="night",
        )
        db.session.add(operation)
        db.session.commit()

        response = self._hub_with_operation(operation)

        self.assertEqual(response.status_code, 200)
        self.assertIn(f'href="/motherbrain?operation_id={operation.id}"'.encode(), response.data)
        self.assertIn(f'href="/neosektor?operation_id={operation.id}"'.encode(), response.data)
        self.assertIn(f'href="/neoermac?operation_id={operation.id}"'.encode(), response.data)
        self.assertIn(f'href="/neoscorpion?operation_id={operation.id}"'.encode(), response.data)
        self.assertIn(f'href="/motherbrain/manage-sort?operation_id={operation.id}"'.encode(), response.data)

    def test_gateway_marks_unavailable_nodes_disabled_without_changing_launch_contracts(self):
        operation = SortDateOperation(
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
            sort_date=date(2026, 9, 4),
            sort_name="night",
        )
        db.session.add(operation)
        db.session.commit()

        response = self._hub_with_operation(
            operation,
            node_access=lambda _user, _gateway, node: node != "sektor",
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"gateway-node-sektor gateway-node-disabled", response.data)
        self.assertIn(b"No Access", response.data)
        self.assertIn(b"gateway-node-reptile gateway-node-coming-soon", response.data)
        self.assertNotIn(f'href="/neosektor?operation_id={operation.id}"'.encode(), response.data)

    def test_gateway_shell_does_not_change_portal_or_staffing_shells(self):
        for path in ("/portal", "/neostaffing"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertNotIn(b"data-gateway-shell", response.data)
                self.assertNotIn(b"gateway-shell-topbar", response.data)
