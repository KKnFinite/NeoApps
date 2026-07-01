import unittest

from app import create_app
from app.extensions import db
from app.models import Gateway, GatewayMembership, GatewayNodeRole, NeoNode, PortalAppAccess, User
from app.models.user import ROLE_LEVELS
from app.services.access_control import (
    DEFAULT_NEONODES,
    backfill_default_gateway_node_roles,
    ensure_default_gateway_and_nodes,
    get_current_gateway,
    get_default_gateway,
    get_user_gateway_membership,
    get_user_node_role,
    request_default_gateway_access_for_user,
    user_has_app_access,
    user_can_access_node,
    user_has_gateway_access,
)
from app.services.permission_rules import ensure_default_permission_rules


class AccessControlTest(unittest.TestCase):
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

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_role_ladder_order_and_no_legacy_read_only_role(self):
        self.assertLess(ROLE_LEVELS["watcher"], ROLE_LEVELS["operator"])
        self.assertLess(ROLE_LEVELS["operator"], ROLE_LEVELS["simulator"])
        self.assertLess(ROLE_LEVELS["simulator"], ROLE_LEVELS["master"])
        self.assertLess(ROLE_LEVELS["master"], ROLE_LEVELS["grandmaster"])
        unsupported_role = "view" + "er"
        self.assertNotIn(unsupported_role, ROLE_LEVELS)

    def test_gateway_membership_has_no_role_column(self):
        self.assertNotIn("role", GatewayMembership.__table__.columns)

    def test_default_gateway_and_nodes_are_seeded_for_rfd(self):
        gateway = ensure_default_gateway_and_nodes()
        db.session.commit()

        self.assertEqual(gateway.code, "RFD")
        self.assertEqual(gateway.name, "NeoGateway")
        self.assertTrue(gateway.is_active)
        self.assertEqual(get_default_gateway().code, "RFD")
        self.assertEqual(get_current_gateway().code, "RFD")
        self.assertEqual(
            {node.code for node in NeoNode.query.filter_by(is_active=True).all()},
            {code for code, _name, _sort_order in DEFAULT_NEONODES},
        )

    def test_approved_gateway_membership_grants_default_watcher_node_access(self):
        user = self._user("watcher_user")
        gateway = ensure_default_gateway_and_nodes()
        db.session.add(
            GatewayMembership(
                user_id=user.id,
                gateway_id=gateway.id,
                status="approved",
                is_active=True,
            )
        )
        db.session.commit()

        self.assertTrue(user_has_gateway_access(user, "RFD"))
        for node_code, _name, _sort_order in DEFAULT_NEONODES:
            with self.subTest(node_code=node_code):
                self.assertEqual(get_user_node_role(user, "RFD", node_code), "watcher")
                self.assertTrue(user_can_access_node(user, "RFD", node_code))
                self.assertFalse(
                    user_can_access_node(
                        user,
                        "RFD",
                        node_code,
                        minimum_role="operator",
                    )
                )

    def test_no_gateway_membership_denies_gateway_node_and_data_access(self):
        user = self._user("no_gateway_user")
        ensure_default_gateway_and_nodes()
        db.session.commit()

        self.assertFalse(user_has_gateway_access(user, "RFD"))
        self.assertIsNone(get_user_gateway_membership(user, "RFD"))
        self.assertIsNone(get_user_node_role(user, "RFD", "motherbrain"))
        self.assertFalse(user_can_access_node(user, "RFD", "motherbrain"))

        client = self.app.test_client()
        client.post(
            "/login",
            data={"username": "no_gateway_user", "password": "TestPassword123!"},
        )
        response = client.get("/motherbrain", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/access-pending")

    def test_approved_rfd_login_redirects_to_portal(self):
        user, _membership = self._approved_user("approved_hub_user")
        db.session.commit()
        client = self.app.test_client()

        response = client.post(
            "/login",
            data={"username": user.username, "password": "TestPassword123!"},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/portal")

    def test_blocked_users_land_on_portal_and_cannot_access_rfd_or_sektor_launcher(self):
        gateway = ensure_default_gateway_and_nodes()
        pending = self._user("pending_hub_user")
        denied = self._user("denied_hub_user")
        no_membership = self._user("no_membership_hub_user")
        db.session.add(
            GatewayMembership(
                user_id=pending.id,
                gateway_id=gateway.id,
                status="pending",
                is_active=True,
            )
        )
        db.session.add(
            GatewayMembership(
                user_id=denied.id,
                gateway_id=gateway.id,
                status="denied",
                is_active=True,
            )
        )
        db.session.commit()

        for user in (pending, denied, no_membership):
            client = self.app.test_client()
            client.post(
                "/login",
                data={"username": user.username, "password": "TestPassword123!"},
                follow_redirects=False,
            )
            login = client.post(
                "/login",
                data={"username": user.username, "password": "TestPassword123!"},
                follow_redirects=False,
            )
            self.assertEqual(login.location, "/portal")

            for path in ("/rfd", "/rfd/sektor"):
                with self.subTest(username=user.username, path=path):
                    response = client.get(path, follow_redirects=False)
                    self.assertEqual(response.status_code, 302)
                    self.assertEqual(response.location, "/access-pending")

    def test_watcher_can_see_rfd_hub_but_cannot_enter_motherbrain(self):
        self._approved_user("watcher_hub_user")
        db.session.commit()
        client = self.app.test_client()
        client.post(
            "/login",
            data={"username": "watcher_hub_user", "password": "TestPassword123!"},
        )

        hub = client.get("/rfd")
        motherbrain = client.get("/motherbrain", follow_redirects=False)

        self.assertEqual(hub.status_code, 200)
        hub_html = hub.data.decode()
        left_column = hub_html.split('rfd-node-column-left"', 1)[1].split('rfd-node-column-right"', 1)[0]
        right_column = hub_html.split('rfd-node-column-right"', 1)[1].split("</section>", 1)[0]
        self.assertIn(b"RFD Hub", hub.data)
        self.assertNotIn(b"RFD Command Hub", hub.data)
        self.assertIn(b"NeoGateway - RFD", hub.data)
        self.assertIn(b"rfd-hub-page", hub.data)
        self.assertNotIn(b"NeoRFD", hub.data)
        self.assertIn(b"watcher_hub_user", hub.data)
        self.assertNotIn(b'neogateway_logo3_large.png', hub.data)
        self.assertNotIn(b'neogateway_logo3_medium.png', hub.data)
        self.assertNotIn(b'neogateway_logo3_small.png', hub.data)
        self.assertIn(b'class="rfd-gateway-brand-strip"', hub.data)
        self.assertIn(b'src="/static/images/icons/neogateway/inapp/neogateway-inapp-128.png"', hub.data)
        self.assertIn(b"rfd-gateway-brand-title neo-brand-title", hub.data)
        self.assertIn(b"neo-brand-title__node--gateway", hub.data)
        self.assertIn(b"NeoMotherBrain", hub.data)
        self.assertIn(b"NeoSektor", hub.data)
        self.assertIn(b'href="/neoermac"', hub.data)
        self.assertIn(b'src="/static/images/icons/neomotherbrain/inapp/neomotherbrain-inapp-128.png"', hub.data)
        self.assertIn(b'src="/static/images/icons/neosektor/inapp/neosektor-icon-128x128.png"', hub.data)
        self.assertIn(b'src="/static/images/icons/neoermac/inapp/neoermac-inapp-128.png"', hub.data)
        self.assertIn(b'src="/static/images/icons/neoscorpion/inapp/neoscorpion-128x128.png"', hub.data)
        self.assertIn(b'src="/static/images/icons/reptile/icon_192.png"', hub.data)
        self.assertIn(b'src="/static/images/icons/subzero/icon_192.png"', hub.data)
        self.assertIn(b'src="/static/images/icons/rain/icon_192.png"', hub.data)
        self.assertNotIn(b"Sort planning, flight schedules, parking, and API review.", hub.data)
        self.assertNotIn(b"Inbound operations, ballmat counts, discharge, and routing.", hub.data)
        self.assertNotIn(b"Shift execution, doors, belts, ULD requests, and outbound pulls.", hub.data)
        self.assertNotIn(b"Future node.", hub.data)
        for node_name in (
            b"NeoScorpion",
            b"NeoReptile",
            b"NeoErmac",
            b"NeoSub-Zero",
            b"NeoRain",
        ):
            self.assertIn(node_name, hub.data)
        self.assertNotIn(b"Placeholder", hub.data)
        self.assertNotIn(b"Launch", hub.data)
        self.assertNotIn(b"Gateway Command Layer", hub.data)
        self.assertLess(hub_html.index('aria-label="NeoMotherBrain"'), hub_html.index('class="rfd-node-grid"'))
        self.assertLess(hub_html.index('rfd-node-column-left"'), hub_html.index('rfd-node-column-right"'))
        left_order = (
            "NeoSektor",
            "NeoReptile",
            "NeoRain",
        )
        right_order = (
            "NeoErmac",
            "NeoSub-Zero",
            "NeoScorpion",
        )
        left_positions = [left_column.index(f'aria-label="{node}"') for node in left_order]
        right_positions = [right_column.index(f'aria-label="{node}"') for node in right_order]
        self.assertEqual(left_positions, sorted(left_positions))
        self.assertEqual(right_positions, sorted(right_positions))
        self.assertIn(b'href="/logout"', hub.data)
        self.assertNotIn(b'href="/motherbrain/operations"', hub.data)
        self.assertNotIn(b'href="/motherbrain/master-schedule"', hub.data)
        self.assertNotIn(b"Nightly Operations", hub.data)
        self.assertNotIn(b"Master Schedule", hub.data)
        self.assertNotIn(b"Access Requests", hub.data)
        self.assertNotIn(b"User Management", hub.data)
        self.assertNotIn(b'class="gateway-context"', hub.data)
        self.assertNotIn(b'class="platform-brand"', hub.data)
        self.assertNotIn(b'class="powered-by"', hub.data)
        self.assertNotIn(b'href="/motherbrain"', hub.data)
        self.assertEqual(motherbrain.status_code, 302)
        self.assertEqual(motherbrain.location, "/rfd")

    def test_change_characters_menu_filters_accessible_node_targets(self):
        self._approved_user("watcher_character_user")
        db.session.commit()
        client = self.app.test_client()
        client.post(
            "/login",
            data={"username": "watcher_character_user", "password": "TestPassword123!"},
        )

        response = client.get("/neoermac")
        switcher = self._character_switcher_html(response)

        self.assertIn("Change Characters", switcher)
        self.assertIn("Neo", switcher)
        self.assertNotIn('href="/rfd"', switcher)
        self.assertNotIn("Gateway", switcher)
        self.assertNotIn("RFD Hub", switcher)
        self.assertIn('class="character-switcher-icon"', switcher)
        self.assertIn('href="/neosektor"', switcher)
        self.assertIn('src="/static/images/icons/sektor/icon_192.png"', switcher)
        self.assertIn("Sektor", switcher)
        self.assertNotIn('href="/neoermac"', switcher)
        self.assertNotIn("Ermac", switcher)
        self.assertNotIn('href="/motherbrain"', switcher)
        self.assertNotIn("MotherBrain", switcher)
        for unavailable_node in (
            "Scorpion",
            "Reptile",
            "Sub-Zero",
            "Rain",
        ):
            self.assertNotIn(unavailable_node, switcher)

    def test_change_characters_shows_motherbrain_only_with_motherbrain_access(self):
        _user, membership = self._approved_user("simulator_character_user")
        motherbrain = NeoNode.query.filter_by(code="motherbrain").one()
        db.session.add(
            GatewayNodeRole(
                gateway_membership_id=membership.id,
                node_id=motherbrain.id,
                role="simulator",
                is_active=True,
            )
        )
        db.session.commit()
        client = self.app.test_client()
        client.post(
            "/login",
            data={
                "username": "simulator_character_user",
                "password": "TestPassword123!",
            },
        )

        response = client.get("/neoermac")
        switcher = self._character_switcher_html(response)

        self.assertIn('href="/motherbrain"', switcher)
        self.assertIn("MotherBrain", switcher)

    def test_change_characters_appears_on_authenticated_node_pages_not_rfd_hub(self):
        self._approved_user("ermac_character_user")
        db.session.commit()
        client = self.app.test_client()
        client.post(
            "/login",
            data={"username": "ermac_character_user", "password": "TestPassword123!"},
        )

        rfd_response = client.get("/rfd")
        self.assertEqual(rfd_response.status_code, 200)
        self.assertNotIn(b"Change Characters", rfd_response.data)
        self.assertNotIn(b"data-character-switcher", rfd_response.data)

        for path in ("/neoermac", "/neosektor"):
            with self.subTest(path=path):
                response = client.get(path)
                self.assertEqual(response.status_code, 200)
                switcher = self._character_switcher_html(response)
                self.assertIn("Change Characters", switcher)
                self.assertNotIn(f'href="{path}"', switcher)

    def test_simulator_or_higher_can_enter_motherbrain(self):
        user, membership = self._approved_user("simulator_motherbrain_user")
        motherbrain = NeoNode.query.filter_by(code="motherbrain").first()
        db.session.add(
            GatewayNodeRole(
                gateway_membership_id=membership.id,
                node_id=motherbrain.id,
                role="simulator",
                is_active=True,
            )
        )
        db.session.commit()
        client = self.app.test_client()
        client.post(
            "/login",
            data={"username": user.username, "password": "TestPassword123!"},
        )

        response = client.get("/motherbrain")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'src="/static/images/icons/neomotherbrain/inapp/neomotherbrain-inapp-128.png"', response.data)
        self.assertIn(b'src="/static/images/icons/neomotherbrain/inapp/neomotherbrain-inapp-256.png"', response.data)
        self.assertIn(b"motherbrain-dashboard-brand neo-brand-title", response.data)
        self.assertNotIn(b"motherbrain_logo1.png", response.data)

    def test_approved_rfd_user_can_launch_neosektor(self):
        self._approved_user("sektor_launcher_user")
        db.session.commit()
        client = self.app.test_client()
        client.post(
            "/login",
            data={"username": "sektor_launcher_user", "password": "TestPassword123!"},
        )

        hub = client.get("/rfd")
        launch = client.get("/rfd/sektor", follow_redirects=False)
        internal_dashboard = client.get("/neosektor")

        self.assertEqual(hub.status_code, 200)
        self.assertIn(b"NeoSektor", hub.data)
        self.assertIn(b'href="/neosektor"', hub.data)
        self.assertNotIn(b'href="/rfd/sektor"', hub.data)
        self.assertEqual(internal_dashboard.status_code, 200)
        self.assertIn(b"NeoSektor", internal_dashboard.data)
        self.assertEqual(launch.status_code, 302)
        self.assertEqual(launch.location, "https://neosektor.onrender.com/")

    def test_approved_rfd_user_can_open_neoermac_from_hub(self):
        self._approved_user("ermac_launcher_user")
        db.session.commit()
        client = self.app.test_client()
        client.post(
            "/login",
            data={"username": "ermac_launcher_user", "password": "TestPassword123!"},
        )

        hub = client.get("/rfd")
        ermac = client.get("/neoermac")

        self.assertEqual(hub.status_code, 200)
        self.assertIn(b"NeoErmac", hub.data)
        self.assertIn(b'href="/neoermac"', hub.data)
        self.assertEqual(ermac.status_code, 200)
        self.assertIn(b"NeoErmac", ermac.data)

    def test_specific_gateway_node_role_overrides_default_watcher_per_node(self):
        user = self._user("node_role_user")
        gateway = ensure_default_gateway_and_nodes()
        membership = GatewayMembership(
            user_id=user.id,
            gateway_id=gateway.id,
            status="approved",
            is_active=True,
        )
        db.session.add(membership)
        db.session.flush()

        sektor = NeoNode.query.filter_by(code="sektor").first()
        db.session.add(
            GatewayNodeRole(
                gateway_membership_id=membership.id,
                node_id=sektor.id,
                role="operator",
                is_active=True,
            )
        )
        db.session.commit()

        self.assertEqual(get_user_node_role(user, "RFD", "sektor"), "operator")
        self.assertEqual(get_user_node_role(user, "RFD", "ermac"), "watcher")
        self.assertTrue(
            user_can_access_node(user, "RFD", "sektor", minimum_role="operator")
        )
        self.assertFalse(
            user_can_access_node(user, "RFD", "ermac", minimum_role="operator")
        )

    def test_user_can_have_rfd_access_without_dfw_access(self):
        user = self._user("rfd_only_user")
        rfd = ensure_default_gateway_and_nodes()
        db.session.add(Gateway(code="DFW", name="NeoDFW", is_active=True))
        db.session.add(
            GatewayMembership(
                user_id=user.id,
                gateway_id=rfd.id,
                status="approved",
                is_active=True,
            )
        )
        db.session.commit()

        self.assertTrue(user_has_gateway_access(user, "RFD"))
        self.assertFalse(user_has_gateway_access(user, "DFW"))
        self.assertFalse(user_can_access_node(user, "DFW", "motherbrain"))

    def test_new_account_defaults_to_pending_rfd_access_request(self):
        user = self._user("pending_user")

        membership = request_default_gateway_access_for_user(user)
        db.session.commit()
        app_access = PortalAppAccess.query.filter_by(
            user_id=user.id,
            app_code="neogateway",
        ).one()

        self.assertEqual(membership.gateway.code, "RFD")
        self.assertEqual(membership.status, "pending")
        self.assertEqual(app_access.status, "pending")
        self.assertTrue(membership.is_active)
        self.assertFalse(user_has_app_access(user, "neogateway"))
        self.assertFalse(user_has_gateway_access(user, "RFD"))
        self.assertEqual(GatewayNodeRole.query.count(), 0)

    def test_existing_approved_gateway_membership_backfills_neogateway_app_access(self):
        user, membership = self._approved_user("legacy_gateway_user")
        db.session.commit()

        self.assertEqual(PortalAppAccess.query.count(), 0)
        self.assertTrue(user_has_app_access(user, "neogateway"))
        access = PortalAppAccess.query.filter_by(
            user_id=user.id,
            app_code="neogateway",
        ).one()

        self.assertEqual(access.status, "approved")
        self.assertEqual(access.role, "watcher")
        self.assertTrue(user_has_gateway_access(user, "RFD"))
        self.assertIsNotNone(membership)

    def test_user_without_neogateway_app_access_cannot_open_neogateway(self):
        user, _membership = self._approved_user("denied_app_user")
        db.session.add(
            PortalAppAccess(
                user_id=user.id,
                app_code="neogateway",
                status="denied",
                role="watcher",
                is_active=True,
            )
        )
        db.session.commit()
        client = self.app.test_client()
        client.post(
            "/login",
            data={"username": user.username, "password": "TestPassword123!"},
        )

        response = client.get("/rfd", follow_redirects=False)

        self.assertFalse(user_has_gateway_access(user, "RFD"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/access-pending")

    def test_backfill_grants_admin_approved_rfd_roles_only(self):
        user = self._user("admin_user")
        db.session.add(Gateway(code="DFW", name="NeoDFW", is_active=True))
        db.session.flush()

        membership = backfill_default_gateway_node_roles(user, role="grandmaster")
        db.session.commit()

        self.assertEqual(membership.gateway.code, "RFD")
        self.assertEqual(membership.status, "approved")
        self.assertTrue(user_has_app_access(user, "neogateway"))
        self.assertTrue(user_can_access_node(user, "RFD", "motherbrain", "grandmaster"))
        self.assertFalse(user_has_gateway_access(user, "DFW"))

    def _user(self, username):
        user = User(username=username, role="watcher")
        user.set_password("TestPassword123!")
        db.session.add(user)
        db.session.flush()
        return user

    def _approved_user(self, username):
        user = self._user(username)
        gateway = ensure_default_gateway_and_nodes()
        membership = GatewayMembership(
            user_id=user.id,
            gateway_id=gateway.id,
            status="approved",
            is_active=True,
        )
        db.session.add(membership)
        db.session.flush()
        return user, membership

    def _character_switcher_html(self, response):
        html = response.data.decode()
        self.assertIn("data-character-switcher", html)
        return html.split("data-character-switcher", 1)[1].split("</details>", 1)[0]


if __name__ == "__main__":
    unittest.main()
