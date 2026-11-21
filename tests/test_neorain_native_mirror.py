"""The same native closeout and operation contract applies with mirror writes off."""
from app.extensions import db
from app.services.google_rain_integration_mode import NEO_PRIMARY_GOOGLE_MIRROR
from app.services.google_rain_live_milestones import apply_google_rain_departure_milestones
from tests import test_neorain_full_native as native


class NativeMirrorTest(native.FullNativeRainTest):
    mode = NEO_PRIMARY_GOOGLE_MIRROR

    def test_import_cannot_use_stale_google_data_in_native_authority(self):
        before = self.departure.ramp_load_completed_at_utc
        with self.assertRaisesRegex(ValueError, "GOOGLE PRIMARY"):
            apply_google_rain_departure_milestones(self.operation, rows=[{
                "sheet_row": 4, "flight_number": self.departure.flight_number, "ramp_load_complete": "02:01",
            }])
        db.session.rollback()
        self.assertEqual(self.departure.ramp_load_completed_at_utc, before)

    def test_unknown_stored_mode_cannot_enable_google_or_apply_an_import(self):
        from app.services.google_rain_integration_mode import (
            RainIntegrationTransitionError, ensure_rain_integration_setting, rain_google_read_enabled,
        )
        row = ensure_rain_integration_setting(self.rain.gateway, "night")
        row.rain_integration_mode = "invalid_fixture"
        db.session.commit()
        self.assertFalse(rain_google_read_enabled(self.rain.gateway, "night"))
        with self.assertRaisesRegex(RainIntegrationTransitionError, "Invalid NeoRain authority"):
            apply_google_rain_departure_milestones(self.operation, rows=[])
