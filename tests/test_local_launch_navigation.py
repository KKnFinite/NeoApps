from datetime import date
import importlib
from pathlib import Path
import unittest

from flask import Flask

from app import create_app
from app.extensions import db
from app.models import SortDateOperation
from scripts.seed_dev_user import seed_dev_grandmaster


class LocalLaunchNavigationTest(unittest.TestCase):
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

    def test_run_py_imports_current_flask_app(self):
        run_module = importlib.import_module("run")

        self.assertIsInstance(run_module.app, Flask)

    def test_root_app_py_is_intentionally_absent(self):
        self.assertFalse(Path("app.py").exists())

    def test_public_home_uses_enter_login_form_without_separate_login_button(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'<form class="command-login-form" method="post" action="/login">', response.data)
        self.assertIn(b'name="username"', response.data)
        self.assertIn(b'name="password"', response.data)
        self.assertIn(b'<button class="command-access-panel command-enter-button" type="submit">', response.data)
        self.assertIn(b"ENTER", response.data)
        self.assertNotIn(b">Login<", response.data)
        self.assertNotIn(b"Authorize Access", response.data)

    def test_public_home_renders_neosektor_external_tile_with_static_logo(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'href="https://neosektor.onrender.com/"', response.data)
        self.assertIn(b'target="_blank"', response.data)
        self.assertIn(b'rel="noopener"', response.data)
        self.assertIn(b'src="/static/images/neosektor_logo1.png"', response.data)
        self.assertIn(b"NeoSektor", response.data)

    def test_login_route_is_reachable(self):
        response = self.client.get("/login")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'<form class="command-login-form" method="post" action="/login">', response.data)
        self.assertIn(b"ENTER", response.data)

    def test_seeded_kessler_login_is_case_insensitive_and_enters_motherbrain(self):
        seed_dev_grandmaster(self.app)

        response = self.client.post(
            "/login",
            data={"username": " kessler ", "password": "1313"},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/motherbrain")

    def test_seeded_kessler_grandmaster_accesses_motherbrain_routes(self):
        seed_dev_grandmaster(self.app)
        operation = SortDateOperation(
            sort_date=date(2026, 6, 1),
            gateway_code="RFD",
            sort_name="night",
        )
        db.session.add(operation)
        db.session.commit()

        login_response = self.client.post(
            "/login",
            data={"username": "Kessler", "password": "1313"},
            follow_redirects=False,
        )

        self.assertEqual(login_response.status_code, 302)

        direct_paths = (
            "/motherbrain",
            "/motherbrain/operations",
            "/motherbrain/master-schedule",
            f"/motherbrain/operations/{operation.id}",
            f"/motherbrain/operations/{operation.id}/arrivals",
            f"/motherbrain/operations/{operation.id}/departures",
        )
        for path in direct_paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)

        dashboard_response = self.client.get("/")
        self.assertIn(b"NeoMotherBrain", dashboard_response.data)


if __name__ == "__main__":
    unittest.main()
