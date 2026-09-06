"""Shared assertion for the bootstrap-only PostgreSQL schema contract."""

from unittest.mock import patch

from flask import Flask

from app import create_app


def assert_factory_leaves_schema_to_bootstrap(testcase, ensure_path):
    config = type("OfflinePostgresWorker", (), {
        "SECRET_KEY": "offline-worker-test-secret-at-least-32-characters",
        "TESTING": False,
        "SQLALCHEMY_DATABASE_URI": "postgresql://neo@example.test/neoapps",
        "SQLALCHEMY_TRACK_MODIFICATIONS": False,
        "AUTO_BOOTSTRAP_DATABASE": False,
    })
    with (
        patch(ensure_path) as ensure,
        patch("sqlalchemy.engine.base.Engine.connect",
              side_effect=AssertionError("web construction attempted PostgreSQL")) as connect,
    ):
        app = create_app(config)
    testcase.assertIsInstance(app, Flask)
    ensure.assert_not_called()
    connect.assert_not_called()
