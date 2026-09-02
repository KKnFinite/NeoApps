from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sqlalchemy import event

from app import create_app
from app.extensions import db
from app.models import GatewayMembership, GatewayNodeRole, NeoNode, PermissionRule, PortalAppAccess, User
from app.services.access_control import backfill_default_gateway_node_roles, ensure_default_gateway_and_nodes
from app.services.permission_rules import (
    DEFAULT_PERMISSION_RULES,
    PERMISSION_RULE_ITEMS,
    ensure_default_permission_rules,
    get_permission_rule,
    permission_is_configurable,
    preload_permission_rules,
    user_can,
)
from app.models.user import ROLE_LEVELS
from app.services.password_policy import set_user_password


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

        expected = {
            permission_key: minimum_role
            for permission_key, minimum_role, _description in DEFAULT_PERMISSION_RULES
        }
        self.assertEqual(rules, expected)
        self.assertEqual(rules["neoapps.portal.view"], "watcher")
        self.assertEqual(rules["neogateway.landing.view"], "watcher")
        self.assertEqual(rules["neostaffing.board.view"], "watcher")
        self.assertEqual(rules["neostaffing.people.edit"], "simulator")
        self.assertEqual(rules["neostaffing.people.bulk_actions"], "simulator")
        self.assertEqual(rules["neostaffing.attendance.take"], "operator")
        self.assertEqual(rules["neostaffing.reports.view"], "operator")
        self.assertEqual(rules["neostaffing.management.assign"], "simulator")
        self.assertEqual(rules["neostaffing.org_chart.edit_structure"], "master")
        self.assertEqual(rules["neobid.placeholder.view"], "watcher")
        self.assertEqual(rules["neoermac.dashboard.view"], "watcher")
        self.assertEqual(
            rules["neomotherbrain.google_live_polling.edit"],
            "grandmaster",
        )
        self.assertEqual(rules["neosektor.dashboard.view"], "watcher")
        self.assertEqual(rules["neosektor.settings.view"], "master")
        self.assertEqual(rules["neosektor.settings.edit"], "master")
        self.assertEqual(rules["neoscorpion.dashboard.view"], "watcher")
        self.assertEqual(rules["neoscorpion.hanzo.view"], "operator")
        self.assertEqual(rules["neorain.inbound.view"], "watcher")
        self.assertEqual(rules["neorain.inbound.edit"], "simulator")
        self.assertEqual(rules["neorain.outbound.view"], "watcher")
        self.assertEqual(rules["neorain.outbound.edit"], "simulator")
        self.assertEqual(rules["neorain.load_planner_lineup.view"], "watcher")
        self.assertEqual(rules["neorain.load_planner_lineup.edit"], "operator")
        self.assertEqual(rules["neorain.settings.view"], "watcher")
        self.assertEqual(rules["neorain.settings.edit"], "simulator")

    def test_role_order_is_watcher_to_grandmaster(self):
        self.assertLess(ROLE_LEVELS["watcher"], ROLE_LEVELS["operator"])
        self.assertLess(ROLE_LEVELS["operator"], ROLE_LEVELS["simulator"])
        self.assertLess(ROLE_LEVELS["simulator"], ROLE_LEVELS["master"])
        self.assertLess(ROLE_LEVELS["master"], ROLE_LEVELS["grandmaster"])

    def test_current_route_views_are_registered_by_app_node(self):
        seeded_keys = {permission_key for permission_key, _role, _description in DEFAULT_PERMISSION_RULES}
        view_keys_by_group = {
            "NeoApps Portal": {
                "neoapps.portal.view",
                "neoapps.portal_management.view",
                "neoapps.user_management.view",
                "neoapps.access_requests.view",
            },
            "NeoGateway": {"neogateway.landing.view"},
            "NeoMotherBrain": {
                "neomotherbrain.dashboard.view",
                "neomotherbrain.manage_sort.view",
                "neomotherbrain.arrival_planning.view",
                "neomotherbrain.departure_planning.view",
                "neomotherbrain.master_schedule.view",
                "neomotherbrain.gateway_matrix.view",
                "neomotherbrain.sort_timeline.view",
                "neomotherbrain.manage_api.view",
                "neomotherbrain.flight_api_review.view",
                "neomotherbrain.permission_rules.view",
                "motherbrain.parking_rules.view",
                "motherbrain.parking_plan.view",
                "motherbrain.parking_conflicts.view",
            },
            "NeoErmac": {
                "neoermac.dashboard.view",
                "neoermac.upcoming_pulls.view",
                "neoermac.building_lineup.view",
                "neoermac.door_view.view",
                "neoermac.view_outbound.view",
                "neoermac.tug_assignments.view",
            },
            "NeoSektor": {
                "neosektor.dashboard.view",
                "neosektor.settings.view",
                "neosektor.live_counts.view",
                "neosektor.conductor.view",
                "neosektor.ebm.view",
                "neosektor.wbm.view",
                "neosektor.discharge.view",
                "neosektor.driver_routing.view",
            },
            "NeoScorpion": {
                "neoscorpion.dashboard.view",
                "neoscorpion.fuel_dispatch.view",
                "neoscorpion.hanzo.view",
                "neoscorpion.fueler.view",
                "neoscorpion.truck_manager.view",
                "neoscorpion.settings.view",
                "neoscorpion.history.view",
            },
            "NeoRain": {
                "neorain.inbound.view",
                "neorain.outbound.view",
                "neorain.load_planner_lineup.view",
                "neorain.settings.view",
            },
            "NeoStaffing": {
                "neostaffing.board.view",
                "neostaffing.seniority.view",
                "neostaffing.people.view",
                "neostaffing.org_chart.view",
                "neostaffing.reports.view",
                "neostaffing.app_management.view",
                "neostaffing.hierarchy.view",
                "neostaffing.planned_staffing.view",
                "neostaffing.people_management.view",
                "neostaffing.work_assignments.view",
                "neostaffing.management_assignments.view",
            },
            "NeoBid": {"neobid.placeholder.view"},
        }

        for group, permission_keys in view_keys_by_group.items():
            with self.subTest(group=group):
                self.assertTrue(permission_keys <= seeded_keys)

    def test_permission_rule_items_reference_seeded_rules(self):
        seeded_keys = {permission_key for permission_key, _role, _description in DEFAULT_PERMISSION_RULES}
        item_keys = {
            item_key
            for _group_key, item_key, _label, _description, _action_keys in PERMISSION_RULE_ITEMS
        }

        for _group_key, item_key, _label, _description, action_keys in PERMISSION_RULE_ITEMS:
            for action_type, permission_key in action_keys.items():
                with self.subTest(item=item_key, action=action_type):
                    self.assertIn(permission_key, seeded_keys)
        self.assertIn("neomotherbrain.dashboard", item_keys)

    def test_permission_edit_descriptions_are_specific(self):
        descriptions = {
            permission_key: description
            for permission_key, _minimum_role, description in DEFAULT_PERMISSION_RULES
        }

        for permission_key, description in descriptions.items():
            if permission_key.endswith(".edit"):
                with self.subTest(permission_key=permission_key):
                    self.assertNotIn("edit access", description.lower())

        self.assertIn(
            "Save Arrival Planning mission row time, tail, route, status, and parking updates.",
            descriptions["neomotherbrain.arrival_planning.edit"],
        )
        self.assertIn(
            "Assign, clear, swap, annotate, and update tail-state controls on Parking Plan.",
            descriptions["motherbrain.parking_plan.edit"],
        )
        self.assertIn(
            "Save Parking Rules rows, blocked positions, aircraft rules, and optimizer settings.",
            descriptions["motherbrain.parking_rules.edit"],
        )

    def test_legacy_permission_descriptions_are_refreshed_without_overwriting_custom_copy(self):
        legacy_rule = PermissionRule.query.filter_by(
            permission_key="neomotherbrain.permission_rules.edit"
        ).one()
        custom_rule = PermissionRule.query.filter_by(
            permission_key="neomotherbrain.manage_sort.edit"
        ).one()
        legacy_rule.description = "Edit NeoApps Permission Rules."
        custom_rule.description = "Custom Manage Sort copy."
        db.session.commit()

        ensure_default_permission_rules()
        db.session.commit()

        db.session.refresh(legacy_rule)
        db.session.refresh(custom_rule)
        self.assertEqual(
            legacy_rule.description,
            "Save minimum-role dropdown settings for NeoApps Permission Rules.",
        )
        self.assertEqual(custom_rule.description, "Custom Manage Sort copy.")

    def test_app_level_permission_rules_use_global_and_app_roles(self):
        portal_user = self._user("portal_watcher", role="watcher")
        staffing_user, staffing_access = self._user_with_app_access(
            "staffing_watcher",
            "neostaffing",
            "watcher",
        )

        self.assertTrue(user_can("neoapps.portal.view", portal_user))
        self.assertFalse(user_can("neoapps.portal_management.view", portal_user))
        portal_user.role = "grandmaster"
        self.assertTrue(user_can("neoapps.portal_management.view", portal_user))
        self.assertTrue(user_can("neostaffing.board.view", staffing_user))
        self.assertTrue(user_can("neostaffing.reports.view", staffing_user))

        staffing_access.role = "master"
        db.session.commit()

        self.assertTrue(user_can("neostaffing.hierarchy.view", staffing_user))
        self.assertTrue(user_can("neostaffing.org_chart.edit_structure", staffing_user))

    def test_menu_permission_preload_uses_one_query_and_seeds_missing_keys(self):
        user = self._user_with_ermac_role("permission_batch_operator", "operator")
        custom_key = "neoermac.dashboard.view"
        fallback_key = "neoermac.upcoming_pulls.view"
        custom_rule = PermissionRule.query.filter_by(permission_key=custom_key).one()
        fallback_rule = PermissionRule.query.filter_by(permission_key=fallback_key).one()
        custom_rule.minimum_role = "master"
        db.session.delete(fallback_rule)
        db.session.add(
            PortalAppAccess(
                user_id=user.id,
                app_code="neogateway",
                status="approved",
                role="operator",
                is_active=True,
            )
        )
        db.session.commit()
        statements = []

        def capture(_connection, _cursor, statement, _parameters, _context, _many):
            statements.append(statement.strip())

        with self.app.test_request_context("/neoermac"):
            event.listen(db.engine, "before_cursor_execute", capture)
            try:
                loaded = preload_permission_rules(
                    (custom_key, fallback_key, custom_key, "missing.permission")
                )
                self.assertEqual(loaded[custom_key].minimum_role, "master")
                self.assertIsNone(loaded[fallback_key])
                self.assertIsNone(loaded["missing.permission"])
                self.assertIsNone(get_permission_rule(fallback_key))
                self.assertTrue(user_can(custom_key, user))
                self.assertTrue(user_can(fallback_key, user))
                self.assertFalse(user_can("missing.permission", user))
                preload_permission_rules((custom_key, fallback_key))
            finally:
                event.remove(db.engine, "before_cursor_execute", capture)

        permission_selects = [
            statement
            for statement in statements
            if statement.upper().startswith("SELECT")
            and "permission_rules" in statement.lower()
        ]
        writes = [
            statement
            for statement in statements
            if statement.upper().startswith(("INSERT", "UPDATE", "DELETE"))
        ]
        self.assertEqual(len(permission_selects), 1)
        self.assertIn(" IN ", permission_selects[0])
        self.assertEqual(writes, [])

    def test_node_read_only_route_uses_app_access_not_redundant_view_threshold(self):
        view_rule = PermissionRule.query.filter_by(
            permission_key="neostaffing.people.view"
        ).one()
        view_rule.minimum_role = "grandmaster"
        staffing_user, _access = self._user_with_app_access(
            "staffing_master",
            "neostaffing",
            "master",
        )
        db.session.commit()
        self._login(staffing_user.username)

        response = self.client.get("/neostaffing/app-management", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/neostaffing/people")

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
        self.assertIn(b"Node Permissions", response.data)
        self.assertIn(b"permission-rule-item", response.data)
        self.assertIn(b'data-permission-action="view"', response.data)
        self.assertIn(b'data-permission-action="edit"', response.data)
        self.assertIn(b'data-permission-action="trigger"', response.data)
        self.assertIn(b"data-permission-role-select", response.data)
        self.assertIn(b"NeoGateway / System", response.data)
        self.assertIn(b"NeoMotherBrain", response.data)
        self.assertIn(b"NeoSektor", response.data)
        self.assertIn(b"NeoErmac", response.data)
        self.assertIn(b"NeoScorpion", response.data)
        self.assertIn(b"NeoStaffing", response.data)
        self.assertIn(b"NeoBid", response.data)
        self.assertIn(b"NeoReptile", response.data)
        self.assertIn(b"NeoSub-Zero", response.data)
        self.assertIn(b"NeoRain", response.data)
        self.assertNotIn(b"PERMISSION KEY", response.data)
        self.assertNotIn(b"<th>DESCRIPTION</th>", response.data)
        self.assertNotIn(b"<th>MINIMUM ROLE</th>", response.data)
        self.assertIn(b"neoermac.building_lineup.edit", response.data)
        self.assertIn(b'<option value="master" selected>Master</option>', response.data)
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

    def test_permission_page_renders_action_dropdowns_only_where_applicable(self):
        grandmaster = self._user_with_node_role("rule_ui_grandmaster", "motherbrain", "grandmaster")
        backfill_default_gateway_node_roles(grandmaster, role="grandmaster")
        db.session.commit()
        self._login(grandmaster.username)

        response = self.client.get("/motherbrain/permissions")
        html = response.data.decode()
        manage_sort_block = html.split(
            'data-permission-item="neomotherbrain.manage_sort"',
            1,
        )[1].split("</article>", 1)[0]
        manage_api_block = html.split(
            'data-permission-item="neomotherbrain.manage_api"',
            1,
        )[1].split("</article>", 1)[0]
        optimizer_apply_block = html.split(
            'data-permission-item="motherbrain.parking_optimizer_apply"',
            1,
        )[1].split("</article>", 1)[0]
        ermac_lineup_block = html.split(
            'data-permission-item="neoermac.building_lineup"',
            1,
        )[1].split("</article>", 1)[0]

        self.assertEqual(response.status_code, 200)
        self.assertIn('data-permission-action="view"', manage_sort_block)
        self.assertIn('data-permission-action="edit"', manage_sort_block)
        self.assertNotIn('data-permission-action="trigger"', manage_sort_block)
        self.assertIn('data-permission-action="view"', manage_api_block)
        self.assertIn('data-permission-action="trigger"', manage_api_block)
        self.assertIn('data-permission-action="trigger"', optimizer_apply_block)
        self.assertNotIn('data-permission-action="view"', ermac_lineup_block)
        self.assertIn('data-permission-action="edit"', ermac_lineup_block)
        self.assertIn("neomotherbrain.manage_api.run", manage_api_block)
        self.assertIn("motherbrain.parking_optimizer.apply", optimizer_apply_block)

    def test_permission_rules_mobile_layout_uses_simplified_card_hooks(self):
        grandmaster = self._user_with_node_role("rule_mobile_grandmaster", "motherbrain", "grandmaster")
        backfill_default_gateway_node_roles(grandmaster, role="grandmaster")
        db.session.commit()
        self._login(grandmaster.username)

        response = self.client.get("/motherbrain/permissions")
        css = Path("app/static/css/base.css").read_text(encoding="utf-8")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"motherbrain-permission-rules-page", response.data)
        self.assertIn(
            "body.mobile-app-chrome.motherbrain-permission-rules-page .users-panel.centered-command-page",
            css,
        )
        self.assertIn(
            "body.mobile-app-chrome.motherbrain-permission-rules-page .permission-rule-item {\n"
            "        grid-template-columns: 1fr;\n"
            "        gap: 7px;\n"
            "        padding: 8px;\n"
            "        border: 0;",
            css,
        )
        self.assertIn(
            "body.mobile-app-chrome.motherbrain-permission-rules-page .permission-rule-action {\n"
            "        grid-template-columns: minmax(58px, 0.34fr) minmax(0, 1fr);",
            css,
        )

    def test_invalid_permission_role_is_rejected_safely(self):
        grandmaster = self._user_with_node_role("rule_invalid_grandmaster", "motherbrain", "grandmaster")
        backfill_default_gateway_node_roles(grandmaster, role="grandmaster")
        db.session.commit()
        self._login(grandmaster.username)
        rule = PermissionRule.query.filter_by(
            permission_key="neomotherbrain.manage_sort.edit"
        ).one()

        response = self.client.post(
            "/motherbrain/permissions",
            data={
                "rule_ids": [str(rule.id)],
                f"description_{rule.id}": rule.description,
                f"minimum_role_{rule.id}": "captain",
            },
            follow_redirects=True,
        )

        db.session.refresh(rule)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Unsupported minimum role selected.", response.data)
        self.assertEqual(rule.minimum_role, "simulator")

    def test_manage_sort_view_permission_controls_route_access(self):
        view_rule = PermissionRule.query.filter_by(
            permission_key="neomotherbrain.manage_sort.view"
        ).one()
        view_rule.minimum_role = "master"
        simulator = self._user_with_node_role("manage_sort_view_sim", "motherbrain", "simulator")
        db.session.commit()
        self._login(simulator.username)

        response = self.client.get("/motherbrain/manage-sort", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/rfd")

    def test_motherbrain_menu_hides_denied_page_and_direct_route_is_denied(self):
        system_rule = PermissionRule.query.filter_by(
            permission_key="neomotherbrain.system_settings.view"
        ).one()
        system_rule.minimum_role = "grandmaster"
        operator = self._user_with_node_role(
            "motherbrain_menu_operator", "motherbrain", "operator"
        )
        db.session.commit()
        self._login(operator.username)

        dashboard = self.client.get("/motherbrain")
        direct = self.client.get("/motherbrain/system-settings", follow_redirects=False)

        self.assertEqual(dashboard.status_code, 200)
        self.assertNotIn(b'href="/motherbrain/system-settings"', dashboard.data)
        self.assertEqual(direct.status_code, 302)
        self.assertEqual(direct.location, "/rfd")

    def test_missing_mutation_rule_blocks_direct_post_even_for_grandmaster(self):
        edit_rule = PermissionRule.query.filter_by(
            permission_key="neomotherbrain.manage_sort.edit"
        ).one()
        db.session.delete(edit_rule)
        grandmaster = self._user_with_node_role(
            "missing_action_grandmaster", "motherbrain", "grandmaster"
        )
        db.session.commit()
        self._login(grandmaster.username)

        response = self.client.post(
            "/motherbrain/manage-sort/create-tonight",
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/rfd")

    def test_invalid_mutation_threshold_fails_read_only(self):
        simulator = self._user_with_ermac_role(
            "invalid_action_simulator", "simulator"
        )
        with patch(
            "app.services.permission_rules.get_permission_rule",
            return_value=SimpleNamespace(minimum_role="invalid-role"),
        ):
            self.assertFalse(
                user_can("neoermac.building_lineup.edit", simulator)
            )

    def test_system_grandmaster_safeguard_survives_lowered_action_threshold(self):
        integration_rule = PermissionRule.query.filter_by(
            permission_key="neomotherbrain.integrations.edit"
        ).one()
        integration_rule.minimum_role = "watcher"
        operator = self._user_with_node_role(
            "integration_operator", "motherbrain", "operator"
        )
        db.session.commit()
        self._login(operator.username)

        response = self.client.post(
            "/motherbrain/system-settings/integrations",
            data={"action": "save_google_live_polling"},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 403)

    def test_manage_api_run_permission_controls_manual_actions(self):
        view_rule = PermissionRule.query.filter_by(
            permission_key="neomotherbrain.manage_api.view"
        ).one()
        run_rule = PermissionRule.query.filter_by(
            permission_key="neomotherbrain.manage_api.run"
        ).one()
        view_rule.minimum_role = "simulator"
        run_rule.minimum_role = "grandmaster"
        simulator = self._user_with_node_role("manage_api_run_sim", "motherbrain", "simulator")
        db.session.commit()
        self._login(simulator.username)

        response = self.client.post(
            "/motherbrain/flight-api-test",
            data={
                "flight_api_action": "replay",
                "replay_payload": "{}",
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/rfd")

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

        self.assertTrue(user_can("neosektor.conductor.view", watcher))
        self.assertTrue(user_can("neosektor.conductor.view", operator))
        self.assertTrue(user_can("neosektor.conductor.view", simulator))
        self.assertTrue(user_can("neosektor.tunnel_conductor.edit", simulator))
        self.assertTrue(user_can("neosektor.ebm.view", operator))
        self.assertTrue(user_can("neosektor.ebm.edit", operator))
        self.assertTrue(user_can("neosektor.wbm.view", operator))
        self.assertTrue(user_can("neosektor.wbm.edit", operator))
        self.assertFalse(user_can("neosektor.driver_routing.edit", operator))

    def test_watcher_can_view_but_cannot_edit_door_view(self):
        watcher = self._user_with_ermac_role("ermac_watcher", "watcher")

        self.assertTrue(user_can("neoermac.door_view.view", watcher))
        self.assertFalse(user_can("neoermac.door_view.edit", watcher))

    def test_missing_action_permission_fails_read_only_for_every_role(self):
        master = self._user_with_ermac_role("ermac_master", "master")
        grandmaster = self._user_with_ermac_role("ermac_missing_grandmaster", "grandmaster")

        self.assertFalse(user_can("neoermac.unknown_screen.edit", master))
        self.assertFalse(user_can("neoermac.unknown_screen.edit", grandmaster))

    def test_only_motherbrain_and_action_rules_are_centrally_configurable(self):
        self.assertFalse(permission_is_configurable("neoermac.door_view.view"))
        self.assertFalse(permission_is_configurable("neorain.outbound.view"))
        self.assertTrue(permission_is_configurable("neoermac.door_view.edit"))
        self.assertTrue(permission_is_configurable("neomotherbrain.manage_sort.view"))

    def _user(self, username, role="watcher"):
        user = User(username=username, role=role)
        set_user_password(user, "TestPassword123!")
        db.session.add(user)
        db.session.commit()
        return user

    def _user_with_app_access(self, username, app_code, role):
        user = self._user(username)
        access = PortalAppAccess(
            user_id=user.id,
            app_code=app_code,
            status="approved",
            role=role,
            is_active=True,
        )
        db.session.add(access)
        db.session.commit()
        return user, access

    def _user_with_ermac_role(self, username, role):
        user = User(username=username, role="watcher")
        set_user_password(user, "TestPassword123!")
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
        set_user_password(user, "TestPassword123!")
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
