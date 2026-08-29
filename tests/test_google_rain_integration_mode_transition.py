from datetime import date, datetime
import unittest
from unittest.mock import patch

from app import create_app
from app.extensions import db
from app.models import Gateway, SortDateMission, SortDateOperation
from app.services.google_rain_integration_mode import (
    GOOGLE_PRIMARY,
    NEO_ONLY,
    NEO_PRIMARY_GOOGLE_MIRROR,
    RainIntegrationTransitionError,
    change_rain_integration_mode,
    rain_integration_mode,
    set_rain_integration_mode,
)


class GoogleRainIntegrationModeTransitionTest(unittest.TestCase):
    def setUp(self):
        config = type(
            "GoogleRainIntegrationModeTransitionTestConfig",
            (),
            {
                "SECRET_KEY": "google-rain-transition-test-secret",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
                "DEFAULT_GATEWAY_TIMEZONE": "America/Chicago",
            },
        )
        self.app = create_app(config)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.gateway = Gateway(code="RFD", name="NeoGateway", is_active=True)
        db.session.add(self.gateway)
        db.session.flush()
        self.operation = SortDateOperation(
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
            sort_name="night",
            sort_date=date(2026, 8, 29),
        )
        db.session.add(self.operation)
        db.session.flush()
        self.mission = SortDateMission(
            sort_date_operation_id=self.operation.id,
            sort_date=self.operation.sort_date,
            gateway_code=self.gateway.code,
            sort_name=self.operation.sort_name,
            mission_type="departure",
            mission_source="master",
            flight_number="UPS0910",
            origin="RFD",
            destination="LAX",
            timezone="America/Chicago",
            planned_datetime_local=datetime(2026, 8, 30, 2, 24),
            departure_status="scheduled",
        )
        db.session.add(self.mission)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_google_primary_to_neo_imports_before_switching(self):
        self.mission.elmac_completed_at_utc = datetime(2026, 8, 30, 6, 0)
        self.mission.elmac_completed_source = "google_rain"
        db.session.commit()

        with self._current_operation(), patch(
            "app.services.google_rain_sheets.read_google_rain_outbound_milestones",
            return_value=[
                self._row(
                    ramp_load_complete="2:22",
                    crew_load_complete="2:24",
                    block="2:29",
                    no_return="TRUE",
                )
            ],
        ) as reader:
            status = change_rain_integration_mode(
                self.gateway,
                "night",
                NEO_PRIMARY_GOOGLE_MIRROR,
            )

        self.assertEqual(rain_integration_mode(self.gateway, "night"), NEO_PRIMARY_GOOGLE_MIRROR)
        self.assertTrue(status["handoff_performed"])
        self.assertEqual(status["handoff_direction"], "google_to_neo")
        reader.assert_called_once_with()
        self.assertEqual(self.mission.ramp_load_completed_source, "google_rain")
        self.assertEqual(self.mission.crew_load_completed_source, "google_rain")
        self.assertEqual(self.mission.actual_block_out_source, "google_rain")
        self.assertEqual(self.mission.departure_status, "departed")
        self.assertEqual(self.mission.departure_status_source, "google_rain")
        self.assertEqual(self.mission.elmac_completed_at_utc, datetime(2026, 8, 30, 6, 0))
        self.assertEqual(self.mission.elmac_completed_source, "google_rain")

    def test_failed_google_to_neo_import_rolls_back_state_and_mode(self):
        original_timestamp = self.mission.ramp_load_completed_at_utc

        def failing_apply(*_args, **_kwargs):
            self.mission.ramp_load_completed_at_utc = datetime(2026, 8, 30, 7, 0)
            self.mission.ramp_load_completed_source = "google_rain"
            db.session.flush()
            raise RuntimeError("apply failed")

        with self._current_operation(), patch(
            "app.services.google_rain_sheets.read_google_rain_outbound_milestones",
            return_value=[self._row(ramp_load_complete="2:00")],
        ), patch(
            "app.services.google_rain_live_milestones.apply_google_rain_departure_milestones",
            side_effect=failing_apply,
        ):
            with self.assertRaises(RainIntegrationTransitionError):
                change_rain_integration_mode(self.gateway, "night", NEO_ONLY)

        db.session.expire_all()
        mission = db.session.get(SortDateMission, self.mission.id)
        self.assertEqual(mission.ramp_load_completed_at_utc, original_timestamp)
        self.assertEqual(mission.ramp_load_completed_source, "unknown")
        self.assertEqual(rain_integration_mode(self.gateway, "night"), GOOGLE_PRIMARY)

    def test_neo_modes_switch_without_google_io(self):
        self._set_mode(NEO_PRIMARY_GOOGLE_MIRROR)
        with patch(
            "app.services.google_rain_integration_mode.current_operational_sort_operation"
        ) as current_operation, patch(
            "app.services.google_rain_sheets.read_google_rain_outbound_milestones"
        ) as reader, patch(
            "app.services.google_rain_live_milestones.apply_google_rain_departure_milestones"
        ) as applier, patch(
            "app.services.google_rain_sheets.write_google_rain_departure_milestone"
        ) as writer:
            first = change_rain_integration_mode(self.gateway, "night", NEO_ONLY)
            second = change_rain_integration_mode(
                self.gateway,
                "night",
                NEO_PRIMARY_GOOGLE_MIRROR,
            )

        self.assertFalse(first["handoff_performed"])
        self.assertFalse(second["handoff_performed"])
        current_operation.assert_not_called()
        reader.assert_not_called()
        applier.assert_not_called()
        writer.assert_not_called()
        self.assertEqual(rain_integration_mode(self.gateway, "night"), NEO_PRIMARY_GOOGLE_MIRROR)

    def test_same_mode_is_a_read_only_no_op(self):
        with patch(
            "app.services.google_rain_integration_mode.current_operational_sort_operation"
        ) as current_operation, patch(
            "app.services.google_rain_sheets.read_google_rain_outbound_milestones"
        ) as reader:
            status = change_rain_integration_mode(
                self.gateway,
                "night",
                GOOGLE_PRIMARY,
            )

        self.assertEqual(status["mode"], GOOGLE_PRIMARY)
        self.assertFalse(status["persisted"])
        self.assertFalse(status["handoff_performed"])
        current_operation.assert_not_called()
        reader.assert_not_called()

    def test_neo_to_google_replaces_and_clears_neorain_owned_values(self):
        self._set_mode(NEO_ONLY)
        self.mission.ramp_load_completed_at_utc = datetime(2026, 8, 30, 6, 50)
        self.mission.ramp_load_completed_source = "neorain"
        self.mission.crew_load_completed_at_utc = datetime(2026, 8, 30, 7, 0)
        self.mission.crew_load_completed_source = "neorain"
        self.mission.actual_block_out_datetime_utc = datetime(2026, 8, 30, 7, 10)
        self.mission.actual_block_out_source = "neorain"
        self.mission.departure_status = "departed"
        self.mission.departure_status_source = "neorain"
        db.session.commit()

        with self._current_operation(), patch(
            "app.services.google_rain_sheets.read_google_rain_outbound_milestones",
            return_value=[
                self._row(
                    ramp_load_complete="2:30",
                    crew_load_complete="",
                    block="",
                    no_return="FALSE",
                )
            ],
        ):
            status = change_rain_integration_mode(
                self.gateway,
                "night",
                GOOGLE_PRIMARY,
            )

        self.assertEqual(status["handoff_direction"], "neo_to_google")
        self.assertEqual(self.mission.ramp_load_completed_at_utc, datetime(2026, 8, 30, 7, 30))
        self.assertEqual(self.mission.ramp_load_completed_source, "google_rain")
        self.assertIsNone(self.mission.crew_load_completed_at_utc)
        self.assertEqual(self.mission.crew_load_completed_source, "unknown")
        self.assertIsNone(self.mission.actual_block_out_datetime_utc)
        self.assertEqual(self.mission.actual_block_out_source, "unknown")
        self.assertEqual(self.mission.departure_status, "ramp_load_complete")
        self.assertEqual(self.mission.departure_status_source, "unknown")
        self.assertEqual(rain_integration_mode(self.gateway, "night"), GOOGLE_PRIMARY)

    def test_reverse_handoff_preserves_foreign_owned_values(self):
        self._set_mode(NEO_ONLY)
        manual_time = datetime(2026, 8, 30, 7, 10)
        self.mission.ramp_load_completed_at_utc = manual_time
        self.mission.ramp_load_completed_source = "manual"
        self.mission.crew_load_completed_at_utc = datetime(2026, 8, 30, 7, 0)
        self.mission.crew_load_completed_source = "neorain"
        self.mission.actual_block_out_datetime_utc = manual_time
        self.mission.actual_block_out_source = "manual"
        self.mission.departure_status = "departed"
        self.mission.departure_status_source = "manual"
        db.session.commit()

        with self._current_operation(), patch(
            "app.services.google_rain_sheets.read_google_rain_outbound_milestones",
            return_value=[
                self._row(
                    ramp_load_complete="",
                    crew_load_complete="",
                    block="",
                    no_return="FALSE",
                )
            ],
        ):
            change_rain_integration_mode(self.gateway, "night", GOOGLE_PRIMARY)

        self.assertEqual(self.mission.ramp_load_completed_at_utc, manual_time)
        self.assertEqual(self.mission.ramp_load_completed_source, "manual")
        self.assertIsNone(self.mission.crew_load_completed_at_utc)
        self.assertEqual(self.mission.actual_block_out_datetime_utc, manual_time)
        self.assertEqual(self.mission.actual_block_out_source, "manual")
        self.assertEqual(self.mission.departure_status, "departed")
        self.assertEqual(self.mission.departure_status_source, "manual")

    def test_failed_reverse_handoff_keeps_previous_neo_mode(self):
        self._set_mode(NEO_ONLY)
        with self._current_operation(), patch(
            "app.services.google_rain_sheets.read_google_rain_outbound_milestones",
            side_effect=RuntimeError("read failed"),
        ):
            with self.assertRaises(RainIntegrationTransitionError):
                change_rain_integration_mode(
                    self.gateway,
                    "night",
                    GOOGLE_PRIMARY,
                )

        self.assertEqual(rain_integration_mode(self.gateway, "night"), NEO_ONLY)

    def test_no_current_operation_persists_mode_without_google_or_creation(self):
        operation_count = SortDateOperation.query.count()
        with patch(
            "app.services.google_rain_integration_mode.current_operational_sort_operation",
            return_value=None,
        ), patch(
            "app.services.google_rain_sheets.read_google_rain_outbound_milestones"
        ) as reader, patch(
            "app.services.google_rain_live_milestones.apply_google_rain_departure_milestones"
        ) as applier:
            status = change_rain_integration_mode(self.gateway, "night", NEO_ONLY)

        self.assertEqual(status["mode"], NEO_ONLY)
        self.assertFalse(status["handoff_performed"])
        reader.assert_not_called()
        applier.assert_not_called()
        self.assertEqual(SortDateOperation.query.count(), operation_count)

    def _set_mode(self, mode):
        set_rain_integration_mode(self.gateway, "night", mode)
        db.session.commit()

    def _current_operation(self):
        return patch(
            "app.services.google_rain_integration_mode.current_operational_sort_operation",
            return_value=self.operation,
        )

    def _row(self, **values):
        return {
            "sheet_row": 3,
            "flight_number": self.mission.flight_number,
            "destination": self.mission.destination,
            "std": "2:24",
            **values,
        }


if __name__ == "__main__":
    unittest.main()
