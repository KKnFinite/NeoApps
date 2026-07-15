import unittest

from app import create_app
from app.config import DEFAULT_DEVELOPMENT_SECRET_KEY


class SecretKeyConfigurationTest(unittest.TestCase):
    def _config(self, secret_key, **overrides):
        values = {
            "DEBUG": False,
            "NEOAPPS_ENV": "production",
            "SECRET_KEY": secret_key,
            "SQLALCHEMY_DATABASE_URI": "postgresql://unused",
            "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            "TESTING": False,
        }
        values.update(overrides)
        return type("SecretKeyConfig", (), values)

    def test_production_startup_rejects_missing_secret_key(self):
        with self.assertRaisesRegex(RuntimeError, "SECRET_KEY configuration error"):
            create_app(self._config(None), auto_bootstrap=False)

    def test_production_startup_rejects_development_fallback(self):
        with self.assertRaisesRegex(RuntimeError, "SECRET_KEY configuration error"):
            create_app(self._config(DEFAULT_DEVELOPMENT_SECRET_KEY), auto_bootstrap=False)

    def test_production_startup_rejects_short_secret_key(self):
        with self.assertRaisesRegex(RuntimeError, "at least 32 characters"):
            create_app(self._config("too-short"), auto_bootstrap=False)

    def test_production_startup_accepts_strong_secret_key(self):
        app = create_app(self._config("strong-production-secret-key-0123456789"), auto_bootstrap=False)

        self.assertEqual(app.config["SECRET_KEY"], "strong-production-secret-key-0123456789")

    def test_explicit_development_allows_the_local_fallback(self):
        app = create_app(
            self._config(None, NEOAPPS_ENV="development"),
            auto_bootstrap=False,
        )

        self.assertEqual(app.config["SECRET_KEY"], DEFAULT_DEVELOPMENT_SECRET_KEY)

    def test_testing_allows_the_local_fallback(self):
        app = create_app(
            self._config(None, NEOAPPS_ENV="", TESTING=True),
            auto_bootstrap=False,
        )

        self.assertEqual(app.config["SECRET_KEY"], DEFAULT_DEVELOPMENT_SECRET_KEY)


if __name__ == "__main__":
    unittest.main()
