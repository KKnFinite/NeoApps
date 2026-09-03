from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
import unittest

from app.services.neoscorpion_spear_calibration import (
    CalibrationObservation,
    MINIMUM_ACTIVE_SAMPLES,
    blended_estimate,
    calibration_review_payload,
    calibrated_planning_settings,
)


NOW = datetime(2026, 9, 3, 2, 0)


class _Settings:
    setup_minutes = Decimal("5")
    finishing_minutes = Decimal("4")
    eta_safety_buffer_minutes = Decimal("5")
    pump_rates_gallons_per_minute = {"B757": Decimal("100")}

    @staticmethod
    def pump_rate_for(kind):
        return _Settings.pump_rates_gallons_per_minute.get(kind)

    @staticmethod
    def is_complete_for(_kind):
        return True


class SpearLiveCalibrationTest(unittest.TestCase):
    def test_fewer_than_three_observations_keeps_configured_baseline(self):
        self.assertEqual(blended_estimate(Decimal("5"), (Decimal("9"), Decimal("9"))), Decimal("5"))

    def test_three_observations_uses_exact_weighted_blend(self):
        self.assertEqual(
            blended_estimate(Decimal("5"), (Decimal("9"), Decimal("9"), Decimal("9"))),
            Decimal("7"),
        )

    def test_active_aircraft_specific_pump_rate_overrides_only_that_type(self):
        active = SimpleNamespace(active=True, effective=Decimal("140"), samples=3)
        proxy = calibrated_planning_settings(_Settings(), {("pump_rate", "B757"): active})
        self.assertEqual(proxy.pump_rate_for("B757"), Decimal("140"))
        self.assertEqual(proxy.setup_minutes, Decimal("5"))

    def test_review_payload_is_deterministic_and_never_training_eligible(self):
        payload = calibration_review_payload(SimpleNamespace(id=7), {})
        self.assertEqual(payload["schema_version"], "v1")
        self.assertFalse(payload["training_eligible"])
        self.assertEqual(payload["capture_mode"], "live_calibration_review")

    def test_observation_contract_can_identify_excluded_operational_delay(self):
        observation = CalibrationObservation(
            "setup_minutes", "fleet-wide", Decimal("14"), NOW, 1, 2,
            "Ready for Fuel to fuel start", "Ramp congestion",
        )
        self.assertEqual(observation.excluded_reason, "Ramp congestion")
        self.assertEqual(MINIMUM_ACTIVE_SAMPLES, 3)


if __name__ == "__main__":
    unittest.main()
