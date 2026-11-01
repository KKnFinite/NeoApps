from io import BytesIO
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app import create_app
from app.services import email_service


class _BufferedResponse:
    def read(self, size=-1):
        if not hasattr(self, '_stream'):
            self._stream = BytesIO(self._payload_bytes())
        return self._stream.read(size)


class EmailServiceTest(unittest.TestCase):
    def test_email_receipts_are_bounded_closed_and_never_retried(self):
        from tests.test_external_response_limits import Response
        user = SimpleNamespace(email='fixture@example.test', display_name='Fixture')
        for body in (b'x' * (64 * 1024 + 1), b'{', b'[]'):
            response = Response(body)
            with patch.object(email_service, 'urlopen', return_value=response) as request:
                self.assertFalse(email_service.send_email_verification(user, 'fixture-token')['sent'])
            self.assertTrue(response.closed)
            request.assert_called_once()

    def test_email_http_error_body_is_closed_without_private_logging(self):
        from urllib.error import HTTPError
        body = BytesIO(b'private')
        error = HTTPError('https://example.test', 401, 'private-key', {}, body)
        with patch.object(email_service, 'urlopen', side_effect=error), self.assertLogs(self.app.logger, level='WARNING') as logs:
            result = email_service.send_email_verification(SimpleNamespace(email='fixture@example.test', display_name='Fixture'), 'token')
        self.assertFalse(result['sent'])
        self.assertTrue(body.closed)
        self.assertNotIn('private-key', str(logs.output))

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

        class FakeResponse(_BufferedResponse):
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def _payload_bytes(self):
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

    def test_email_verification_copy_uses_the_default_seven_day_expiration(self):
        captured = {}

        class FakeResponse(_BufferedResponse):
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def _payload_bytes(self):
                return b'{"messageId":"test"}'

        def fake_urlopen(request, timeout):
            captured["request"] = request
            return FakeResponse()

        user = SimpleNamespace(email="verify@example.com", display_name="Verify User")
        with patch("app.services.email_service.urlopen", side_effect=fake_urlopen):
            result = email_service.send_email_verification(user, "verification-token")

        payload = json.loads(captured["request"].data.decode("utf-8"))
        self.assertTrue(result["sent"])
        self.assertIn("expires in 7 days", payload["htmlContent"])
        self.assertIn("expires in 7 days", payload["textContent"])


if __name__ == "__main__":
    unittest.main()
