import unittest

from app import create_app
from app.extensions import db
from app.models import GatewayMembership, GatewayNodeRole, NeoNode, PermissionRule, User
from app.services.access_control import backfill_default_gateway_node_roles, ensure_default_gateway_and_nodes
from app.services.permission_rules import ensure_default_permission_rules, user_can
from app.models.user import ROLE_LEVELS


class PermissionRulesTest(unittest.TestCase):
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
        ensure_default_gateway_and_nodes()
        ensure_default_permission_rules()
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_permission_rules_seed_correctly(self):
        rules = {
            rule.permission_key: rule.minimum_role
            for rule in PermissionRule.query.order_by(PermissionRule.permission_key).all()
        }

        self.assertEqual(
            rules,
            {
                "motherbrain.parking_conflicts.view": "operator",
                "motherbrain.parking_optimizer.apply": "master",
                "motherbrain.parking_optimizer.run": "master",
                "motherbrain.parking_rules.edit": "simulator",
                "motherbrain.parking_rules.view": "simulator",
                "neomotherbrain.dashboard.view": "operator",
                "neomotherbrain.flight_api_auto_poll.trigger": "simulator",
                "neomotherbrain.flight_api_review.edit": "simulator",
                "neomotherbrain.flight_api_review.view": "simulator",
                "neomotherbrain.gateway_matrix.view": "operator",
                "neomotherbrain.manage_sort.view": "operator",
                "neomotherbrain.master_schedule.view": "operator",
                "neoermac.building_lineup.edit": "simulator",
                "neoermac.building_lineup.view": "operator",
                "neoermac.door_view.edit": "operator",
                "neoermac.door_view.view": "operator",
                "neoermac.tug_assignments.edit": "master",
                "neoermac.view_outbound.view": "watcher",
                "neosektor.conductor.view": "simulator",
                "neosektor.discharge.edit": "operator",
                "neosektor.discharge.view": "operator",
                "neosektor.driver_routing.view": "watcher",
                "neosektor.ebm.edit": "operator",
                "neosektor.ebm.view": "operator",
                "neosektor.live_counts.view": "watcher",
                "neosektor.tunnel_conductor.edit": "simulator",
                "neosektor.wbm.edit": "operator",
                "neosektor.wbm.view": "operator",
                "neoscorpion.fuel_dispatch.edit": "simulator",
                "neoscorpion.fuel_dispatch.view": "operator",
                "neoscorpion.fueler.edit": "operator",
                "neoscorpion.fueler.view": "watcher",
                "neoscorpion.history.view": "operator",
                "neoscorpion.settings.edit": "master",
                "neoscorpion.settings.view": "simulator",
                "neoscorpion.truck_manager.edit": "simulator",
                "neoscorpion.truck_manager.view": "operator",
            },
        )

    def test_role_order_is_watcher_to_grandmaster(self):
        self.assertLess(ROLE_LEVELS["watcher"], ROLE_LEVELS["operator"])
        self.assertLess(ROLE_LEVELS["operator"], ROLE_LEVELS["simulator"])
        self.assertLess(ROLE_LEVELS["simulator"], ROLE_LEVELS["master"])
        self.assertLess(ROLE_LEVELS["master"], ROLE_LEVELS["grandmaster"])

    def test_grandmaster_can_manage_permission_rules(self):
        grandmaster = self._user_with_ermac_role("permission_grandmaster", "grandmaster")
        backfill_default_gateway_node_roles(grandmaster, role="grandmaster")
        db.session.commit()
        self._login(grandmaster.username)
        rule = PermissionRule.query.filter_by(
            permission_key="neoermac.building_lineup.edit"
        ).one()

        response = self.client.post(
            "/motherbrain/permissions",
            data={
                "rule_ids": [str(rule.id)],
                f"description_{rule.id}": "Updated Building Lineup rule.",
                f"minimum_role_{rule.id}": "master",
            },
            follow_redirects=True,
        )

        updated = db.session.get(PermissionRule, rule.id)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"PERMISSION RULES", response.data)
        self.assertIn(b"DESCRIPTION", response.data)
        self.assertIn(b"MINIMUM ROLE", response.data)
        self.assertIn(b"NeoGateway / System", response.data)
        self.assertIn(b"NeoMotherBrain", response.data)
        self.assertIn(b"NeoSektor", response.data)
        self.assertIn(b"NeoErmac", response.data)
        self.assertIn(b"NeoScorpion", response.data)
        self.assertIn(b"NeoReptile", response.data)
        self.assertIn(b"NeoSub-Zero", response.data)
        self.assertIn(b"NeoRain", response.data)
        self.assertNotIn(b"PERMISSION KEY", response.data)
        self.assertIn(b"neoermac.building_lineup.edit", response.data)
        self.assertIn(b'data-motherbrain-desktop-side-nav', response.data)
        self.assertIn(b'href="/motherbrain"', response.data)
        self.assertNotIn(b"motherbrain-main-menu-return", response.data)
        self.assertEqual(updated.minimum_role, "master")
        self.assertEqual(updated.description, "Updated Building Lineup rule.")

    def test_motherbrain_view_defaults_to_operator_and_remains_editable(self):
        grandmaster = self._user_with_ermac_role("motherbrain_rule_grandmaster", "grandmaster")
        backfill_default_gateway_node_roles(grandmaster, role="grandmaster")
        db.session.commit()
        self._login(grandmaster.username)
        rule = PermissionRule.query.filter_by(permission_key="neomotherbrain.dashboard.view").one()

        response = self.client.post(
            "/motherbrain/permissions",
            data={
                "rule_ids": [str(rule.id)],
                f"description_{rule.id}": rule.description,
                f"minimum_role_{rule.id}": "simulator",
            },
            follow_redirects=True,
        )

        updated = db.session.get(PermissionRule, rule.id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(updated.minimum_role, "simulator")

    def test_simulator_can_pass_building_lineup_edit(self):
        simulator = self._user_with_ermac_role("ermac_simulator", "simulator")

        self.assertTrue(user_can("neoermac.building_lineup.edit", simulator))

    def test_operator_cannot_pass_building_lineup_edit(self):
        operator = self._user_with_ermac_role("ermac_operator_low", "operator")

        self.assertFalse(user_can("neoermac.building_lineup.edit", operator))

    def test_operator_can_pass_building_lineup_view(self):
        operator = self._user_with_ermac_role("ermac_operator_view", "operator")

        self.assertTrue(user_can("neoermac.building_lineup.view", operator))

    def test_operator_can_view_and_edit_door_view(self):
        operator = self._user_with_ermac_role("ermac_operator", "operator")

        self.assertTrue(user_can("neoermac.door_view.view", operator))
        self.assertTrue(user_can("neoermac.door_view.edit", operator))

    def test_watcher_can_view_outbound_summary(self):
        watcher = self._user_with_ermac_role("ermac_outbound_watcher", "watcher")

        self.assertTrue(user_can("neoermac.view_outbound.view", watcher))

    def test_motherbrain_view_permission_uses_operator_minimum(self):
        watcher = self._user_with_node_role("motherbrain_watcher", "motherbrain", "watcher")
        operator = self._user_with_node_role("motherbrain_operator", "motherbrain", "operator")

        self.assertFalse(user_can("neomotherbrain.dashboard.view", watcher))
        self.assertTrue(user_can("neomotherbrain.dashboard.view", operator))

    def test_flight_api_review_defaults_to_simulator(self):
        operator = self._user_with_node_role("motherbrain_review_operator", "motherbrain", "operator")
        simulator = self._user_with_node_role("motherbrain_review_simulator", "motherbrain", "simulator")

        self.assertFalse(user_can("neomotherbrain.flight_api_review.view", operator))
        self.assertFalse(user_can("neomotherbrain.flight_api_review.edit", operator))
        self.assertTrue(user_can("neomotherbrain.flight_api_review.view", simulator))
        self.assertTrue(user_can("neomotherbrain.flight_api_review.edit", simulator))

    def test_flight_api_auto_poll_trigger_defaults_to_simulator(self):
        operator = self._user_with_node_role("motherbrain_auto_operator", "motherbrain", "operator")
        simulator = self._user_with_node_role("motherbrain_auto_simulator", "motherbrain", "simulator")

        self.assertFalse(user_can("neomotherbrain.flight_api_auto_poll.trigger", operator))
        self.assertTrue(user_can("neomotherbrain.flight_api_auto_poll.trigger", simulator))

    def test_neosektor_rules_are_grouped_and_use_existing_role_order(self):
        watcher = self._user_with_node_role("sektor_watcher", "sektor", "watcher")
        operator = self._user_with_node_role("sektor_operator", "sektor", "operator")
        simulator = self._user_with_node_role("sektor_simulator", "sektor", "simulator")

        self.assertFalse(user_can("neosektor.conductor.view", watcher))
        self.assertFalse(user_can("neosektor.conductor.view", operator))
        self.assertTrue(user_can("neosektor.conductor.view", simulator))
        self.assertTrue(user_can("neosektor.tunnel_conductor.edit", simulator))
        self.assertTrue(user_can("neosektor.ebm.view", operator))
        self.assertTrue(user_can("neosektor.ebm.edit", operator))
        self.assertTrue(user_can("neosektor.wbm.view", operator))
        self.assertTrue(user_can("neosektor.wbm.edit", operator))
        self.assertFalse(user_can("neosektor.driver_routing.edit", operator))

    def test_lower_role_cannot_view_or_edit_door_view(self):
        watcher = self._user_with_ermac_role("ermac_watcher", "watcher")

        self.assertFalse(user_can("neoermac.door_view.view", watcher))
        self.assertFalse(user_can("neoermac.door_view.edit", watcher))

    def test_missing_permission_key_denies_non_grandmaster_and_allows_grandmaster(self):
        master = self._user_with_ermac_role("ermac_master", "master")
        grandmaster = self._user_with_ermac_role("ermac_missing_grandmaster", "grandmaster")

        self.assertFalse(user_can("neoermac.unknown_screen.edit", master))
        self.assertTrue(user_can("neoermac.unknown_screen.edit", grandmaster))

    def _user_with_ermac_role(self, username, role):
        user = User(username=username, role="watcher")
        user.set_password("TestPassword123!")
        db.session.add(user)
        db.session.flush()

        gateway = ensure_default_gateway_and_nodes()
        membership = GatewayMembership(
            user_id=user.id,
            gateway_id=gateway.id,
            status="approved",
            is_active=True,
        )
        db.session.add(membership)
        db.session.flush()

        ermac = NeoNode.query.filter_by(code="ermac").one()
        if role != "watcher":
            db.session.add(
                GatewayNodeRole(
                    gateway_membership_id=membership.id,
                    node_id=ermac.id,
                    role=role,
                    is_active=True,
                )
            )
        db.session.commit()
        return user

    def _user_with_node_role(self, username, node_code, role):
        user = User(username=username, role="watcher")
        user.set_password("TestPassword123!")
        db.session.add(user)
        db.session.flush()

        gateway = ensure_default_gateway_and_nodes()
        membership = GatewayMembership(
            user_id=user.id,
            gateway_id=gateway.id,
            status="approved",
            is_active=True,
        )
        db.session.add(membership)
        db.session.flush()

        node = NeoNode.query.filter_by(code=node_code).one()
        if role != "watcher":
            db.session.add(
                GatewayNodeRole(
                    gateway_membership_id=membership.id,
                    node_id=node.id,
                    role=role,
                    is_active=True,
                )
            )
        db.session.commit()
        return user

    def _login(self, username):
        return self.client.post(
            "/login",
            data={"username": username, "password": "TestPassword123!"},
            follow_redirects=False,
        )


if __name__ == "__main__":
    unittest.main()
