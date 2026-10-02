import unittest
from unittest.mock import patch

from app import create_app
from app.neonodes.neoscorpion.routes import _capture_learning_after_commit


class SpearLearningCaptureRouteSafetyTest(unittest.TestCase):
    def setUp(self):
        TestConfig = type(
            "TestConfig",
            (),
            {
                "SECRET_KEY": "spear-learning-route-test",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
                "AUTO_BOOTSTRAP_DATABASE": False,
            },
        )
        self.app = create_app(TestConfig)
        self.context = self.app.app_context()
        self.context.push()

    def tearDown(self):
        self.context.pop()

    @patch(
        "app.neonodes.neoscorpion.routes.capture_completed_learning_outcome",
        side_effect=RuntimeError("vault unavailable"),
    )
    def test_post_commit_capture_failure_is_fail_open(self, capture):
        gateway = type("GatewayStub", (), {"id": 1, "code": "RFD"})()
        _capture_learning_after_commit(gateway, 99)
        capture.assert_called_once_with(gateway, 99)


if __name__ == "__main__":
    unittest.main()
