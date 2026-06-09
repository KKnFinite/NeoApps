import unittest

from app import create_app
from app.extensions import db
from app.models import GatewayMembership, User
from app.services.access_control import ensure_default_gateway_and_nodes


class NeoErmacRoutesTest(unittest.TestCase):
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
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_unauthenticated_users_cannot_access_neoermac_pages(self):
        for path in self._neoermac_paths():
            with self.subTest(path=path):
                response = self.client.get(path, follow_redirects=False)

                self.assertEqual(response.status_code, 302)
                self.assertIn("/login", response.location)

    def test_authenticated_user_with_neoermac_access_can_access_menu(self):
        self._login_approved_user()

        response = self.client.get("/neoermac")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"NeoErmac", response.data)
        self.assertIn(b"Building Lineup", response.data)
        self.assertIn(b"View Outbound", response.data)
        self.assertIn(b"Door View", response.data)
        self.assertIn(b"Tug Assignments", response.data)
        self.assertIn(b"Back to NeoGateway", response.data)

    def test_neoermac_menu_links_work(self):
        self._login_approved_user()

        response = self.client.get("/neoermac")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'href="/neoermac/building-lineup"', response.data)
        self.assertIn(b'href="/neoermac/outbound"', response.data)
        self.assertIn(b'href="/neoermac/door-view"', response.data)
        self.assertIn(b'href="/neoermac/tug-assignments"', response.data)
        self.assertIn(b'href="/rfd"', response.data)

    def test_placeholder_pages_render(self):
        self._login_approved_user()
        expected_pages = {
            "/neoermac/building-lineup": b"Building Lineup",
            "/neoermac/outbound": b"View Outbound",
            "/neoermac/door-view": b"Door View",
            "/neoermac/tug-assignments": b"Tug Assignments",
        }

        for path, title in expected_pages.items():
            with self.subTest(path=path):
                response = self.client.get(path)

                self.assertEqual(response.status_code, 200)
                self.assertIn(title, response.data)
                self.assertIn(b"Back to NeoErmac", response.data)
                self.assertIn(b"Operational logic will be added in a later pass.", response.data)

    def test_ermac_route_is_not_used(self):
        self._login_approved_user()

        menu = self.client.get("/neoermac")
        response = self.client.get("/ermac")

        self.assertEqual(response.status_code, 404)
        self.assertNotIn(b'href="/ermac"', menu.data)

    def _login_approved_user(self):
        user = User(username="neoermac_user", email="neoermac@example.test", role="watcher")
        user.set_password("TestPassword123!")
        db.session.add(user)
        db.session.flush()

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

        return self.client.post(
            "/login",
            data={"email": user.email, "password": "TestPassword123!"},
            follow_redirects=False,
        )

    def _neoermac_paths(self):
        return (
            "/neoermac",
            "/neoermac/building-lineup",
            "/neoermac/outbound",
            "/neoermac/door-view",
            "/neoermac/tug-assignments",
        )


if __name__ == "__main__":
    unittest.main()
