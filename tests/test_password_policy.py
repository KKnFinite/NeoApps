import hashlib
import unittest
from unittest.mock import patch
from urllib.error import URLError

from app.services.password_policy import (
    HIBP_RANGE_API_URL,
    PasswordPolicyError,
    validate_password,
)


class _HibpResponse:
    def __init__(self, body, status=200):
        self.body = body.encode("ascii")
        self.status = status

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class PasswordPolicyHibpTest(unittest.TestCase):
    def _hash_parts(self, password):
        password_hash = hashlib.sha1(password.encode("utf-8")).hexdigest().upper()
        return password_hash[:5], password_hash[5:], password_hash

    @patch("app.services.password_policy._hibp_check_enabled", return_value=True)
    @patch("app.services.password_policy.urlopen")
    def test_known_breached_password_is_rejected(self, mocked_urlopen, _enabled):
        password = "amber glass river lantern"
        _prefix, suffix, _full_hash = self._hash_parts(password)
        mocked_urlopen.return_value = _HibpResponse(f"{suffix}:42\r\n")

        with self.assertRaisesRegex(PasswordPolicyError, "known breach"):
            validate_password(password)

    @patch("app.services.password_policy._hibp_check_enabled", return_value=True)
    @patch("app.services.password_policy.urlopen")
    def test_non_matching_password_is_accepted(self, mocked_urlopen, _enabled):
        password = "cobalt meadow lantern phrase"
        mocked_urlopen.return_value = _HibpResponse(f"{'A' * 35}:1\r\n")

        validate_password(password)

    @patch("app.services.password_policy._hibp_check_enabled", return_value=True)
    @patch("app.services.password_policy.urlopen", side_effect=TimeoutError)
    def test_hibp_timeout_falls_back_to_local_validation(self, _urlopen, _enabled):
        validate_password("cobalt meadow lantern phrase")

    @patch("app.services.password_policy._hibp_check_enabled", return_value=True)
    @patch("app.services.password_policy.urlopen", side_effect=URLError("unavailable"))
    def test_hibp_connection_error_falls_back_to_local_validation(
        self, _urlopen, _enabled
    ):
        validate_password("cobalt meadow lantern phrase")

    @patch("app.services.password_policy._hibp_check_enabled", return_value=True)
    @patch("app.services.password_policy.urlopen")
    def test_unexpected_hibp_response_falls_back_to_local_validation(
        self, mocked_urlopen, _enabled
    ):
        mocked_urlopen.return_value = _HibpResponse("unexpected response")

        validate_password("cobalt meadow lantern phrase")

    @patch("app.services.password_policy._hibp_check_enabled", return_value=True)
    @patch("app.services.password_policy.urlopen")
    def test_only_hibp_hash_prefix_is_sent(self, mocked_urlopen, _enabled):
        password = "violet river lantern phrase"
        prefix, _suffix, full_hash = self._hash_parts(password)
        mocked_urlopen.return_value = _HibpResponse(f"{'B' * 35}:1\r\n")

        validate_password(password)

        request = mocked_urlopen.call_args.args[0]
        self.assertEqual(request.full_url, f"{HIBP_RANGE_API_URL}{prefix}")
        self.assertNotIn(password, request.full_url)
        self.assertNotIn(full_hash, request.full_url)

    @patch("app.services.password_policy._hibp_check_enabled", return_value=True)
    @patch("app.services.password_policy.urlopen", side_effect=TimeoutError)
    @patch("app.services.password_policy.logger")
    def test_hibp_warning_never_logs_password_or_hash(
        self, mocked_logger, _urlopen, _enabled
    ):
        password = "violet river lantern phrase"
        _prefix, suffix, full_hash = self._hash_parts(password)

        validate_password(password)

        logged_text = " ".join(
            str(argument)
            for call in mocked_logger.warning.call_args_list
            for argument in call.args
        )
        self.assertNotIn(password, logged_text)
        self.assertNotIn(full_hash, logged_text)
        self.assertNotIn(suffix, logged_text)


if __name__ == "__main__":
    unittest.main()
