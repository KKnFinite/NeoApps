from datetime import date, datetime, time
import unittest

from app import create_app
from app.extensions import db
from app.models import (
    Gateway,
    NeoRainGoogleRolloverState,
    SortDateOperation,
    SortTimelineSettings,
    SortTimelineSortSetting,
)
from app.services.google_rain_rollover_gate import gate_google_rain_rollover_rows


class GoogleRainRolloverGateTest(unittest.TestCase):
    def setUp(self):
        config = type(
            "GoogleRainRolloverGateTestConfig",
            (),
            {
                "SECRET_KEY": "google-rain-rollover-test-secret",
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
        settings = SortTimelineSettings(
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
        )
        db.session.add(settings)
        db.session.flush()
        db.session.add(
            SortTimelineSortSetting(
                timeline_settings=settings,
                gateway_id=self.gateway.id,
                gateway_code=self.gateway.code,
                sort_name="night",
                sort_window_start_local=time(14, 0),
                sort_window_end_local=time(7, 0),
                ops_window_start_local=time(20, 0),
                ops_window_end_local=time(6, 0),
            )
        )
        self.operation = SortDateOperation(
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
            sort_name="night",
            sort_date=date(2026, 8, 10),
        )
        db.session.add(self.operation)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_rollover_values_are_fenced_until_a_field_changes(self):
        stale = self._row(
            elmac="1:40",
            ramp_load_complete="1:45",
            crew_load_complete="1:50",
            block="2:00",
            no_return="TRUE",
        )

        first = self._gate(stale)
        second = self._gate(stale)
        changed = self._gate({**stale, "crew_load_complete": "1:51"})

        self.assertEqual(first["rows"], ())
        self.assertEqual(second["rows"], ())
        self.assertEqual(len(changed["rows"]), 1)
        released = changed["rows"][0]
        self.assertEqual(released["crew_load_complete"], "1:51")
        self.assertNotIn("elmac", released)
        self.assertNotIn("ramp_load_complete", released)
        self.assertNotIn("block", released)
        self.assertNotIn("no_return", released)

    def test_clear_then_repopulate_releases_timestamp_for_rest_of_sort(self):
        self._gate(self._row(ramp_load_complete="1:45"))

        cleared = self._gate(self._row(ramp_load_complete=""))
        repopulated = self._gate(self._row(ramp_load_complete="1:47"))

        self.assertEqual(cleared["rows"][0]["ramp_load_complete"], "")
        self.assertEqual(repopulated["rows"][0]["ramp_load_complete"], "1:47")

    def test_elmac_clear_then_repopulate_releases_rollover_fence(self):
        self._gate(self._row(elmac="1:40"))

        cleared = self._gate(self._row(elmac=""))
        repopulated = self._gate(self._row(elmac="1:42"))

        self.assertEqual(cleared["rows"][0]["elmac"], "")
        self.assertEqual(repopulated["rows"][0]["elmac"], "1:42")

    def test_equivalent_timestamp_format_does_not_false_release(self):
        self._gate(self._row(ramp_load_complete="8/11 1:45"))

        result = self._gate(self._row(ramp_load_complete="1:45"))

        self.assertEqual(result["rows"], ())

    def test_no_return_checkbox_requires_change_from_rollover_state(self):
        self._gate(self._row(no_return="TRUE"))
        unchanged = self._gate(self._row(no_return=True))
        cleared = self._gate(self._row(no_return="FALSE"))
        restored = self._gate(self._row(no_return="TRUE"))

        self.assertEqual(unchanged["rows"], ())
        self.assertEqual(cleared["rows"][0]["no_return"], "FALSE")
        self.assertEqual(restored["rows"][0]["no_return"], "TRUE")

    def test_outside_operational_window_neither_imports_nor_captures_baseline(self):
        result = self._gate(
            self._row(block="2:00"),
            now=datetime(2026, 8, 10, 19, 59),
        )

        self.assertEqual(result["status"], "outside_operational_window")
        self.assertEqual(result["rows"], ())
        self.assertEqual(NeoRainGoogleRolloverState.query.count(), 0)

    def test_cross_midnight_late_running_sort_uses_configured_ops_window(self):
        self._gate(
            self._row(block="2:00"),
            now=datetime(2026, 8, 10, 20, 0),
        )
        result = self._gate(
            self._row(block="5:31"),
            now=datetime(2026, 8, 11, 5, 30),
        )

        self.assertEqual(result["status"], "active")
        self.assertEqual(result["rows"][0]["block"], "5:31")

    def test_released_state_survives_session_and_service_restart_boundary(self):
        self._gate(self._row(crew_load_complete="1:50"))
        self._gate(self._row(crew_load_complete="1:51"))
        db.session.commit()
        operation_id = self.operation.id
        db.session.remove()
        operation = db.session.get(SortDateOperation, operation_id)

        result = gate_google_rain_rollover_rows(
            operation,
            rows=(self._row(crew_load_complete="1:52"),),
            now=datetime(2026, 8, 10, 22, 0),
        )

        self.assertEqual(result["rows"][0]["crew_load_complete"], "1:52")
        self.assertEqual(NeoRainGoogleRolloverState.query.count(), 1)

    def test_model_is_bounded_to_one_state_per_operation_sheet_row(self):
        self._gate(self._row(block="2:00"))
        self._gate(self._row(block="2:01"))

        self.assertEqual(NeoRainGoogleRolloverState.query.count(), 1)
        columns = NeoRainGoogleRolloverState.__table__.columns
        self.assertIn("baseline_values_json", columns)
        self.assertIn("released_fields_json", columns)

    def _gate(self, row, now=None):
        return gate_google_rain_rollover_rows(
            self.operation,
            rows=(row,),
            now=now or datetime(2026, 8, 10, 22, 0),
        )

    @staticmethod
    def _row(**values):
        return {
            "sheet_row": 4,
            "flight_number": "UPS0910",
            "destination": "LAX",
            "std": "2:24",
            **values,
        }


if __name__ == "__main__":
    unittest.main()
