import unittest

from app import create_app
from app.extensions import db
from app.models import (
    GatewayMembership,
    GatewayNodeRole,
    NeoNode,
    NeoSektorBallmatCount,
    NeoSektorBayStatus,
    NeoSektorDriverRouteSetting,
    NeoSektorOpenBayState,
    NeoSektorSortState,
    NeoSektorWaveState,
    User,
)
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.permission_rules import ensure_default_permission_rules


class NeoSektorRoutesTest(unittest.TestCase):
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
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_neosektor_dashboard_requires_login(self):
        response = self.client.get("/neosektor", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.location)

    def test_operator_can_open_neosektor_dashboard(self):
        self._login_approved_user(role="operator")

        response = self.client.get("/neosektor")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"NeoSektor", response.data)
        for label in (
            b"TUNNEL CONDUCTOR",
            b"EBM",
            b"WBM",
            b"DISCHARGE",
            b"VIEW LIVE COUNTS",
            b"DRIVER ROUTING",
        ):
            self.assertIn(label, response.data)

    def test_placeholder_routes_load_for_view_authorized_user(self):
        self._login_approved_user(role="operator")

        paths = {
            "/neosektor/tunnel-conductor": b"TUNNEL CONDUCTOR",
            "/neosektor/ebm": b"EBM",
            "/neosektor/wbm": b"WBM",
            "/neosektor/discharge": b"DISCHARGE",
            "/neosektor/driver-routing": b"DRIVER ROUTING",
        }

        for path, title in paths.items():
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(title, response.data)
                self.assertIn(b"SCREEN LOGIC WILL BE COPIED", response.data)

    def test_live_counts_loads_default_database_backed_state(self):
        self._login_approved_user(role="watcher")

        response = self.client.get("/neosektor/live-counts")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"VIEW LIVE COUNTS", response.data)
        self.assertIn(b"READ ONLY DATABASE FOUNDATION", response.data)
        self.assertIn(b"LEFT TO UNLOAD", response.data)
        self.assertIn(b"1ST WAVE", response.data)
        self.assertIn(b"2ND WAVE", response.data)
        self.assertIn(b"EAST BALLMAT", response.data)
        self.assertIn(b"WEST BALLMAT", response.data)
        self.assertIn(b"Empty", response.data)
        self.assertNotIn(b"SCREEN LOGIC WILL BE COPIED", response.data)
        self.assertEqual(NeoSektorSortState.query.count(), 1)
        self.assertEqual(NeoSektorWaveState.query.count(), 2)
        self.assertEqual(NeoSektorBallmatCount.query.count(), 2)
        self.assertEqual(NeoSektorOpenBayState.query.count(), 2)
        self.assertEqual(NeoSektorBayStatus.query.count(), 6)
        self.assertEqual(NeoSektorDriverRouteSetting.query.count(), 2)

    def test_neosektor_dashboard_and_header_link_to_real_live_counts(self):
        self._login_approved_user(role="operator")

        dashboard = self.client.get("/neosektor")
        live_counts = self.client.get("/neosektor/live-counts")

        self.assertEqual(dashboard.status_code, 200)
        self.assertIn(b'href="/neosektor/live-counts"', dashboard.data)
        self.assertEqual(live_counts.status_code, 200)
        self.assertIn(b'href="/neosektor/live-counts"', live_counts.data)
        self.assertIn(b'aria-current="page"', live_counts.data)

    def test_watcher_can_open_dashboard_and_live_counts_but_not_operator_pages(self):
        self._login_approved_user(role="watcher")

        dashboard = self.client.get("/neosektor", follow_redirects=False)
        ebm = self.client.get("/neosektor/ebm", follow_redirects=False)
        live_counts = self.client.get("/neosektor/live-counts", follow_redirects=False)

        self.assertEqual(dashboard.status_code, 200)
        self.assertIn(b"NeoSektor", dashboard.data)
        self.assertEqual(ebm.status_code, 302)
        self.assertEqual(ebm.location, "/neosektor")
        self.assertEqual(live_counts.status_code, 200)
        self.assertIn(b"VIEW LIVE COUNTS", live_counts.data)

    def test_rfd_sektor_still_points_to_standalone_service(self):
        self._login_approved_user(role="operator")

        response = self.client.get("/rfd/sektor", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "https://neosektor.onrender.com/")

    def test_rfd_hub_neosektor_tile_points_to_internal_dashboard(self):
        self._login_approved_user(role="operator")

        response = self.client.get("/rfd")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"NeoSektor", response.data)
        self.assertIn(b'href="/neosektor"', response.data)
        self.assertNotIn(b'href="/rfd/sektor"', response.data)

    def _login_approved_user(self, role):
        user = User(
            username=f"sektor_{role}_user",
            email=f"sektor_{role}@example.test",
            role="watcher",
            is_active=True,
        )
        user.set_password("TestPassword123!")
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

        sektor = NeoNode.query.filter_by(code="sektor").one()
        if role != "watcher":
            db.session.add(
                GatewayNodeRole(
                    gateway_membership_id=membership.id,
                    node_id=sektor.id,
                    role=role,
                    is_active=True,
                )
            )
        db.session.commit()

        return self.client.post(
            "/login",
            data={"email": user.email, "password": "TestPassword123!"},
            follow_redirects=False,
        )


if __name__ == "__main__":
    unittest.main()
