from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import NeoScorpionFuelingEvent, NeoScorpionSettings, NeoScorpionSpearCalibrationReset
from app.services.neoscorpion_spear import SpearSettings, build_spear_plan, effective_spear_settings, save_spear_settings
from app.services.schema_sync import LOCAL_SQLITE_OPTIONAL_COLUMNS, POSTGRES_OPTIONAL_COLUMNS, sync_local_sqlite_schema
from tests.test_neoscorpion_spear import _row, _fueler, _truck, NOW as PLAN_NOW
from tests import test_neoscorpion_spear as spear_fixtures
from tests import test_neoscorpion_spear_learning_capture as learning_fixtures

from app.services.neoscorpion_spear_calibration import (
    CalibrationObservation,
    LiveCalibration,
    MINIMUM_ACTIVE_SAMPLES,
    blended_estimate,
    calibration_review_payload,
    calibrated_planning_settings,
    build_live_calibration,
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
        proxy = calibrated_planning_settings(_Settings(), {("pump_rate", "B757"): active}, mode="apply")
        self.assertEqual(proxy.pump_rate_for("B757"), Decimal("140"))
        self.assertEqual(proxy.setup_minutes, Decimal("5"))

    def test_observe_uses_configured_timing_and_apply_uses_all_three_candidates(self):
        calibrations = {}
        for metric, configured, candidate in (
            ("pump_rate", "100", "200"), ("setup_minutes", "5", "2"),
            ("finishing_minutes", "4", "2"),
        ):
            calibrations[(metric, "B757")] = LiveCalibration(
                metric, "B757", Decimal(configured), Decimal(candidate), Decimal(candidate),
                3, 1, NOW, NOW, (), (),
            )

        def plan(mode, items):
            return build_spear_plan(
                [_row()], operation=SimpleNamespace(id=1), planning_settings=_Settings(),
                spear_settings=SpearSettings(live_calibration_mode=mode),
                nightly_fuelers=(_fueler(),), nightly_trucks=(_truck(),),
                now_utc=PLAN_NOW, calibrations=items,
            )

        baseline = plan("observe", {})
        observe = plan("observe", calibrations)
        apply = plan("apply", calibrations)
        for attribute in ("projected_start_at_utc", "projected_complete_at_utc", "risk", "truck_id", "fueler_id"):
            self.assertEqual(getattr(observe.steps[0], attribute), getattr(baseline.steps[0], attribute))
        self.assertEqual(observe.steps[0].explanation["alternatives"], baseline.steps[0].explanation["alternatives"])
        self.assertEqual(observe.token, baseline.token)
        self.assertEqual(observe.steps[0].explanation["setup_minutes"], "5 min")
        self.assertEqual(observe.steps[0].explanation["pump_minutes"], "5 min")
        self.assertEqual(observe.steps[0].explanation["finishing_minutes"], "4 min")
        self.assertEqual(apply.steps[0].explanation["setup_minutes"], "2 min")
        self.assertEqual(apply.steps[0].explanation["pump_minutes"], "2.5 min")
        self.assertEqual(apply.steps[0].explanation["finishing_minutes"], "2 min")
        self.assertEqual(observe.steps[0].projected_complete_at_utc - apply.steps[0].projected_complete_at_utc, timedelta(minutes=7.5))
        self.assertNotEqual(observe.token, apply.token)
        self.assertNotEqual(baseline.token, plan("apply", {}).token)
        for note in observe.steps[0].explanation["live_calibration"]:
            self.assertEqual(note["using"], note["configured"])
            self.assertEqual(note["mode"], "OBSERVE")
        for note in apply.steps[0].explanation["live_calibration"]:
            self.assertEqual(note["using"], note["candidate"])
            self.assertEqual(note["mode"], "APPLY")

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


def settings_form(mode, *, learning=False, automation=False):
    return {
        "live_calibration_mode": mode, "recommendations_enabled": "1",
        "learning_capture_enabled": "1" if learning else "0",
        "automation_enabled": "1" if automation else "0",
        "minimum_truck_reserve_gallons": "500", "do_not_top_off_above_percent": "70",
        "truck_minutes_per_ramp_move": "2", "fueler_begins_at": "Remote",
        "truck_begins_at": "Remote", "truck_after_top_off": "Remote",
        "incoming_early_staging_minutes": "15", "recalculation_interval_minutes": "2",
        "automation_stability_delay_seconds": "5",
    }


class SpearCalibrationSettingsTest(unittest.TestCase):
    setUp = spear_fixtures.NeoScorpionSpearSettingsTest.setUp
    tearDown = spear_fixtures.NeoScorpionSpearSettingsTest.tearDown

    def test_observe_default_and_validated_persistent_mode(self):
        self.assertEqual(effective_spear_settings(None).live_calibration_mode, "observe")
        settings = NeoScorpionSettings(gateway_id=self.gateway.id)
        db.session.add(settings)
        db.session.commit()
        self.assertEqual(settings.spear_live_calibration_mode, "observe")
        self.assertTrue(settings.spear_recommendations_enabled)
        self.assertFalse(settings.spear_automation_enabled)
        for mode in ("apply", "observe"):
            result = save_spear_settings(self.gateway, self.user, settings_form(mode))
            db.session.commit()
            db.session.expire_all()
            self.assertEqual(effective_spear_settings(settings).live_calibration_mode, mode)
            self.assertFalse(result.automation_just_enabled)
            self.assertFalse(settings.spear_automation_enabled)
        with self.assertRaisesRegex(ValueError, "OBSERVE or APPLY"):
            save_spear_settings(self.gateway, self.user, settings_form("automatic"))
        self.assertEqual(settings.spear_live_calibration_mode, "observe")
        # Older open settings forms do not reset an already chosen APPLY mode.
        settings.spear_live_calibration_mode = "apply"
        form = settings_form("observe")
        del form["live_calibration_mode"]
        save_spear_settings(self.gateway, self.user, form)
        self.assertEqual(settings.spear_live_calibration_mode, "apply")

    def test_schema_sync_safely_defaults_existing_installs_and_constrains_values(self):
        NeoScorpionSettings.__table__.drop(bind=db.engine)
        with db.engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE neoscorpion_settings (id INTEGER PRIMARY KEY, gateway_id INTEGER)")
            connection.exec_driver_sql("INSERT INTO neoscorpion_settings (id, gateway_id) VALUES (1, 1)")
        sync_local_sqlite_schema(self.app)
        sync_local_sqlite_schema(self.app)
        value = db.session.execute(text("SELECT spear_live_calibration_mode FROM neoscorpion_settings WHERE id=1")).scalar_one()
        self.assertEqual(value, "observe")
        columns = {item["name"]: item for item in inspect(db.engine).get_columns("neoscorpion_settings")}
        self.assertFalse(columns["spear_live_calibration_mode"]["nullable"])
        self.assertEqual(LOCAL_SQLITE_OPTIONAL_COLUMNS["neoscorpion_settings"]["spear_live_calibration_mode"], POSTGRES_OPTIONAL_COLUMNS["neoscorpion_settings"]["spear_live_calibration_mode"])
        with self.assertRaises(IntegrityError):
            db.session.execute(text("UPDATE neoscorpion_settings SET spear_live_calibration_mode='invalid'"))
        db.session.rollback()


class SpearCalibrationObservationTest(unittest.TestCase):
    setUp = learning_fixtures.SpearLearningCaptureTest.setUp
    tearDown = learning_fixtures.SpearLearningCaptureTest.tearDown

    def test_observe_accumulates_candidates_and_switches_preserve_samples_and_learning(self):
        rows = [{"assignment": self.assignment, "detailed_aircraft_type": "B757"}]
        self.assignment.ready_for_fuel_at_utc = datetime(2026, 9, 3, 4, 35)
        first = NeoScorpionFuelingEvent.query.one()
        initial = build_live_calibration(self.operation, _Settings(), rows)
        self.assertEqual(initial[("pump_rate", "B757")].samples, 1)
        for sequence in (2, 3):
            db.session.add(NeoScorpionFuelingEvent(
                sort_date_operation_id=self.operation.id, fuel_assignment_id=self.assignment.id,
                fuel_work_state_id=self.work.id, fuel_truck_id=first.fuel_truck_id,
                tail_number=first.tail_number, sequence_number=sequence,
                started_at_utc=first.started_at_utc, ended_at_utc=first.ended_at_utc,
                transfer_fuel_gallons=first.transfer_fuel_gallons,
            ))
        db.session.commit()
        observed = build_live_calibration(self.operation, _Settings(), rows)
        pump = observed[("pump_rate", "B757")]
        self.assertTrue(pump.active)
        self.assertEqual(pump.samples, 3)
        self.assertEqual(pump.observed, Decimal("30"))
        self.assertEqual(pump.effective, Decimal("65"))
        self.assertEqual(pump.recommendation_using("observe"), Decimal("100"))
        review = calibration_review_payload(self.operation, observed)
        with patch('app.services.neoscorpion_spear.require_learning_vault'):
            for mode, learning, automation in (("apply", True, False), ("observe", True, False), ("apply", False, False), ("observe", False, True)):
                save_spear_settings(self.gateway, self.user, settings_form(mode, learning=learning, automation=automation))
                db.session.commit()
                current = effective_spear_settings(NeoScorpionSettings.query.one())
                self.assertEqual(current.learning_capture_enabled, learning)
                self.assertEqual(current.automation_enabled, automation)
                self.assertEqual(calibration_review_payload(self.operation, build_live_calibration(self.operation, _Settings(), rows)), review)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(), 3)
        self.assertEqual(NeoScorpionSpearCalibrationReset.query.count(), 0)


if __name__ == "__main__":
    unittest.main()
