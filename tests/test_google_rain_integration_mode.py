import unittest

from app import create_app
from app.extensions import db
from app.models import (
    Gateway,
    MotherBrainGoogleIntegrationSetting,
    NeoSektorOperationalSetting,
)
from app.services.google_motherbrain_live_polling import (
    google_motherbrain_live_polling_enabled,
    set_google_motherbrain_live_polling_enabled,
)
from app.services.google_rain_integration_mode import (
    GOOGLE_PRIMARY,
    NEO_ONLY,
    NEO_PRIMARY_GOOGLE_MIRROR,
    ensure_rain_integration_setting,
    rain_integration_mode,
    rain_integration_status,
    set_rain_integration_mode,
)
from app.services.neosektor_sheets_compat import neosektor_integration_mode


class GoogleRainIntegrationModeTest(unittest.TestCase):
    def setUp(self):
        config = type(
            "GoogleRainIntegrationModeTestConfig",
            (),
            {
                "SECRET_KEY": "google-rain-mode-test-secret",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            },
        )
        self.app = create_app(config)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.gateway = Gateway(code="RFD", name="NeoGateway", is_active=True)
        db.session.add(self.gateway)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_default_read_is_google_primary_without_creating_persistence(self):
        status = rain_integration_status(self.gateway, "night")

        self.assertEqual(rain_integration_mode(self.gateway, "night"), GOOGLE_PRIMARY)
        self.assertEqual(status["mode"], GOOGLE_PRIMARY)
        self.assertEqual(status["mode_label"], "GOOGLE PRIMARY")
        self.assertEqual(
            tuple(option["value"] for option in status["modes"]),
            (GOOGLE_PRIMARY, NEO_PRIMARY_GOOGLE_MIRROR, NEO_ONLY),
        )
        self.assertFalse(status["persisted"])
        self.assertEqual(MotherBrainGoogleIntegrationSetting.query.count(), 0)

    def test_explicit_ensure_and_set_persist_per_gateway_and_sort(self):
        setting = ensure_rain_integration_setting(self.gateway, "night")
        self.assertEqual(setting.rain_integration_mode, GOOGLE_PRIMARY)
        set_rain_integration_mode(
            self.gateway,
            "night",
            NEO_PRIMARY_GOOGLE_MIRROR,
        )
        db.session.commit()
        db.session.remove()

        gateway = Gateway.query.filter_by(code="RFD").one()
        self.assertEqual(
            rain_integration_mode(gateway, "night"),
            NEO_PRIMARY_GOOGLE_MIRROR,
        )
        self.assertEqual(rain_integration_mode(gateway, "day"), GOOGLE_PRIMARY)
        self.assertEqual(MotherBrainGoogleIntegrationSetting.query.count(), 1)

    def test_invalid_mode_is_rejected_without_mutation(self):
        with self.assertRaisesRegex(ValueError, "valid NeoRain integration mode"):
            set_rain_integration_mode(self.gateway, "night", "rain_magic")

        self.assertEqual(MotherBrainGoogleIntegrationSetting.query.count(), 0)

    def test_rain_mode_is_independent_from_neosektor_and_live_polling(self):
        sektor_setting = NeoSektorOperationalSetting(
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
            integration_mode=NEO_ONLY,
        )
        db.session.add(sektor_setting)
        set_google_motherbrain_live_polling_enabled(
            self.gateway,
            "night",
            True,
        )
        set_rain_integration_mode(self.gateway, "night", NEO_PRIMARY_GOOGLE_MIRROR)
        db.session.commit()

        self.assertEqual(neosektor_integration_mode(self.gateway), NEO_ONLY)
        self.assertEqual(
            rain_integration_mode(self.gateway, "night"),
            NEO_PRIMARY_GOOGLE_MIRROR,
        )
        self.assertTrue(
            google_motherbrain_live_polling_enabled(self.gateway, "night")
        )


if __name__ == "__main__":
    unittest.main()
