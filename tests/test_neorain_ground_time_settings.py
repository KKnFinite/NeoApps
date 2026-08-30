import unittest

from app import create_app
from app.extensions import db
from app.models import Gateway, NeoRainOperationalSetting
from app.services.neorain_ground_time_settings import (
    DEFAULT_NEORAIN_GROUND_TIME_THRESHOLD_MINUTES,
    neorain_ground_time_threshold_minutes,
    set_neorain_ground_time_threshold_minutes,
)


class NeoRainGroundTimeSettingsTest(unittest.TestCase):
    def setUp(self):
        self.app = create_app(type("TestConfig", (), {
            "SECRET_KEY": "test", "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "SQLALCHEMY_TRACK_MODIFICATIONS": False,
        }))
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.rfd = Gateway(code="RFD", name="RFD")
        self.ont = Gateway(code="ONT", name="ONT")
        db.session.add_all([self.rfd, self.ont])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_default_and_gateway_isolation(self):
        self.assertEqual(neorain_ground_time_threshold_minutes(self.rfd), DEFAULT_NEORAIN_GROUND_TIME_THRESHOLD_MINUTES)
        set_neorain_ground_time_threshold_minutes(self.rfd, 95)
        db.session.commit()
        self.assertEqual(neorain_ground_time_threshold_minutes(self.rfd), 95)
        self.assertEqual(neorain_ground_time_threshold_minutes(self.ont), 120)

    def test_setter_stages_without_commit_and_rejects_invalid_values(self):
        setting = set_neorain_ground_time_threshold_minutes(self.rfd, "135")
        self.assertEqual(setting.ground_time_threshold_minutes, 135)
        self.assertTrue(db.session.new)
        for invalid in (0, -1, "12.5", "", True):
            with self.subTest(value=invalid):
                with self.assertRaises(ValueError):
                    set_neorain_ground_time_threshold_minutes(self.ont, invalid)

    def test_persisted_model_is_one_row_per_gateway(self):
        set_neorain_ground_time_threshold_minutes(self.rfd, 120)
        db.session.commit()
        self.assertEqual(NeoRainOperationalSetting.query.count(), 1)
