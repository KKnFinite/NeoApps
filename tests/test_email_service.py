import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app import create_app
from app.services import email_service


class EmailServiceTest(unittest.TestCase):
    def setUp(self):
        TestConfig = type(
            "TestConfig",
            (),
            {
                "SECRET_KEY": "test",
                "TESTING": True,
                "SEND_EMAIL_IN_TESTS": True,
                "BREVO_API_KEY": "test-brevo-key",
                "MAIL_FROM_NAME": "NeoApps Portal",
                "MAIL_FROM_EMAIL": "no-reply@example.com",
                "APP_BASE_URL": "https://neoapps.example",
            },
        )
        self.app = create_app(TestConfig)
        self.context = self.app.app_context()
        self.context.push()

    def tearDown(self):
        self.context.pop()

    def test_access_approval_email_uses_neoapps_portal_copy(self):
        captured = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"messageId":"test"}'

        def fake_urlopen(request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return FakeResponse()

        user = SimpleNamespace(
            email="new.user@example.com",
            display_name="New User",
        )
        gateway = SimpleNamespace(name="NeoGateway")

        with patch("app.services.email_service.urlopen", side_effect=fake_urlopen):
            result = email_service.send_access_approved(user, gateway)

        payload = json.loads(captured["request"].data.decode("utf-8"))
        self.assertTrue(result["sent"])
        self.assertEqual(captured["timeout"], 10)
        self.assertEqual(payload["sender"]["name"], "NeoApps Portal")
        self.assertEqual(payload["sender"]["email"], "no-reply@example.com")
        self.assertEqual(payload["to"][0]["email"], "new.user@example.com")
        self.assertEqual(payload["subject"], "NeoApps access approved")
        self.assertIn("Your access request has been approved.", payload["htmlContent"])
        self.assertIn("Approved access: <strong>NeoGateway</strong>", payload["htmlContent"])
        self.assertIn("https://neoapps.example/login", payload["htmlContent"])
        self.assertIn("Open NeoApps Portal: https://neoapps.example/login", payload["textContent"])


if __name__ == "__main__":
    unittest.main()
