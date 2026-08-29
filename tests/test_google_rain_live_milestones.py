from datetime import date, datetime
import unittest

from app import create_app
from app.extensions import db
from app.models import Gateway, SortDateMission, SortDateOperation
from app.services.google_rain_live_milestones import (
    GOOGLE_RAIN_SOURCE,
    apply_google_rain_departure_milestones,
)


class GoogleRainLiveMilestonesTest(unittest.TestCase):
    def setUp(self):
        TestConfig = type(
            "GoogleRainLiveMilestonesTestConfig",
            (),
            {
                "SECRET_KEY": "google-rain-milestone-test-secret",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
                "DEFAULT_GATEWAY_TIMEZONE": "America/Chicago",
            },
        )
        self.app = create_app(TestConfig)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.gateway = Gateway(code="RFD", name="NeoGateway", is_active=True)
        db.session.add(self.gateway)
        db.session.flush()
        self.operation = SortDateOperation(
            gateway_id=self.gateway.id,
            gateway_code="RFD",
            sort_name="night",
            sort_date=date(2026, 8, 10),
        )
        db.session.add(self.operation)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_unique_flight_applies_despite_tail_difference_without_changing_tail(self):
        mission = self._mission("UPS0910", "LAX", tail="N343UP")

        result = self._apply(
            self._row(
                "UPS0910",
                destination="LAX",
                tail="N999UP",
                crew_load_complete="8/11 2:28",
            )
        )

        self.assertEqual(result["applied_count"], 1)
        self.assertEqual(mission.assigned_tail_number, "N343UP")
        self.assertEqual(
            mission.crew_load_completed_at_utc,
            datetime(2026, 8, 11, 7, 28),
        )
        self.assertEqual(mission.crew_load_completed_source, GOOGLE_RAIN_SOURCE)
        self.assertEqual(mission.departure_status, "crew_load_complete")

    def test_ambiguous_duplicate_flight_is_skipped_without_guessing(self):
        first = self._mission("UPS1000", "LAX", std=datetime(2026, 8, 11, 1, 0))
        second = self._mission("UPS1000", "SDF", std=datetime(2026, 8, 11, 2, 0))

        result = self._apply(self._row("UPS1000", destination="", std=""))

        self.assertEqual(result["applied_count"], 0)
        self.assertEqual(result["skipped_count"], 1)
        self.assertIn("multiple", result["results"][0]["reason"].lower())
        self.assertIsNone(first.ramp_load_completed_at_utc)
        self.assertIsNone(second.ramp_load_completed_at_utc)

    def test_unmatched_rain_row_never_creates_a_mission(self):
        result = self._apply(
            self._row("UPS7777", destination="ONT", block="2:29")
        )

        self.assertEqual(result["applied_count"], 0)
        self.assertEqual(result["skipped_count"], 1)
        self.assertEqual(SortDateMission.query.count(), 0)

    def test_destination_and_std_can_disambiguate_duplicate_flights(self):
        self._mission("UPS1000", "SDF", std=datetime(2026, 8, 11, 1, 0))
        target = self._mission("UPS1000", "SDF", std=datetime(2026, 8, 11, 2, 0))

        result = self._apply(
            self._row(
                "UPS1000",
                destination="SDF",
                std="2:00",
                crew_load_complete="2:10",
            )
        )

        self.assertEqual(result["applied_count"], 1)
        self.assertEqual(target.departure_status, "crew_load_complete")

    def test_duplicate_flight_destination_does_not_bypass_mismatched_std(self):
        first = self._mission("UPS1000", "SDF", std=datetime(2026, 8, 11, 1, 0))
        second = self._mission("UPS1000", "LAX", std=datetime(2026, 8, 11, 2, 0))

        result = self._apply(
            self._row(
                "UPS1000",
                destination="SDF",
                std="2:00",
                crew_load_complete="2:10",
            )
        )

        self.assertEqual(result["applied_count"], 0)
        self.assertIsNone(first.crew_load_completed_at_utc)
        self.assertIsNone(second.crew_load_completed_at_utc)

    def test_elmac_is_audited_without_advancing_departure_status(self):
        mission = self._mission("UPS0910", "LAX")

        self._apply(self._row("UPS0910", elmac="8/10 23:55"))

        self.assertEqual(mission.elmac_completed_at_utc, datetime(2026, 8, 11, 4, 55))
        self.assertEqual(mission.elmac_completed_source, GOOGLE_RAIN_SOURCE)
        self.assertEqual(mission.departure_status, "scheduled")

    def test_r_lc_is_not_applied_as_part_of_the_rain_bundle(self):
        mission = self._mission("UPS0910", "LAX")
        crew = self._mission("UPS0948", "OAK")

        self._apply(self._row("UPS0910", ramp_load_complete="2:22"))
        self._apply(self._row("UPS0948", crew_load_complete="2:24"))

        self.assertIsNone(mission.ramp_load_completed_at_utc)
        self.assertEqual(mission.departure_status, "scheduled")
        self.assertIsNone(crew.ramp_load_completed_at_utc)
        self.assertEqual(crew.departure_status, "crew_load_complete")

    def test_official_block_out_sets_blocked_out_without_fabricating_earlier_events(self):
        mission = self._mission("UPS0910", "LAX")

        self._apply(self._row("UPS0910", block="8/11 2:29"))

        self.assertEqual(
            mission.actual_block_out_datetime_utc,
            datetime(2026, 8, 11, 7, 29),
        )
        self.assertEqual(mission.actual_block_out_source, GOOGLE_RAIN_SOURCE)
        self.assertEqual(mission.departure_status, "blocked_out")
        self.assertIsNone(mission.ramp_load_completed_at_utc)
        self.assertIsNone(mission.crew_load_completed_at_utc)

    def test_no_return_checkbox_advances_to_departed_and_can_clear_back_to_blocked_out(self):
        mission = self._mission("UPS0910", "LAX")

        self._apply(
            self._row(
                "UPS0910",
                block="8/11 2:29",
                no_return="TRUE",
            )
        )

        self.assertEqual(mission.departure_status, "departed")
        self.assertEqual(mission.departure_status_source, GOOGLE_RAIN_SOURCE)

        self._apply(self._row("UPS0910", no_return="FALSE"))

        self.assertEqual(mission.actual_block_out_source, GOOGLE_RAIN_SOURCE)
        self.assertEqual(mission.departure_status, "blocked_out")
        self.assertEqual(mission.departure_status_source, "unknown")

    def test_rain_owned_timestamps_can_be_corrected_and_cleared(self):
        mission = self._mission("UPS0910", "LAX")
        self._apply(
            self._row(
                "UPS0910",
                elmac="1:40",
                ramp_load_complete="1:50",
                crew_load_complete="2:00",
                block="2:10",
            )
        )

        self._apply(
            self._row(
                "UPS0910",
                elmac="1:41",
                ramp_load_complete="1:51",
                crew_load_complete="2:01",
                block="2:11",
            )
        )
        self.assertEqual(mission.elmac_completed_at_utc, datetime(2026, 8, 11, 6, 41))
        self.assertEqual(
            mission.actual_block_out_datetime_utc,
            datetime(2026, 8, 11, 7, 11),
        )

        self._apply(
            self._row(
                "UPS0910",
                elmac="",
                ramp_load_complete="-",
                crew_load_complete="",
                block="",
            )
        )
        self.assertIsNone(mission.elmac_completed_at_utc)
        self.assertIsNone(mission.ramp_load_completed_at_utc)
        self.assertIsNone(mission.crew_load_completed_at_utc)
        self.assertIsNone(mission.actual_block_out_datetime_utc)
        self.assertEqual(mission.elmac_completed_source, "unknown")
        self.assertEqual(mission.actual_block_out_source, "unknown")
        self.assertEqual(mission.departure_status, "scheduled")

    def test_cleared_rain_block_restores_strongest_remaining_progress(self):
        mission = self._mission("UPS0910", "LAX")
        self._apply(
            self._row(
                "UPS0910",
                ramp_load_complete="1:50",
                crew_load_complete="2:00",
                block="2:10",
            )
        )

        self._apply(self._row("UPS0910", block=""))

        self.assertIsNone(mission.actual_block_out_datetime_utc)
        self.assertEqual(mission.actual_block_out_source, "unknown")
        self.assertEqual(mission.departure_status, "crew_load_complete")

    def test_blank_rain_block_preserves_non_google_departed_state(self):
        mission = self._mission("UPS0910", "LAX", status="departed")
        mission.actual_block_out_datetime_utc = None
        mission.actual_block_out_source = "unknown"
        mission.departure_status_source = "manual"
        db.session.commit()

        self._apply(self._row("UPS0910", block=""))

        self.assertEqual(mission.departure_status, "departed")

    def test_cleared_no_return_preserves_non_google_departed_state(self):
        mission = self._mission("UPS0910", "LAX", status="departed")
        mission.actual_block_out_datetime_utc = datetime(2026, 8, 11, 7, 29)
        mission.actual_block_out_source = "manual"
        mission.departure_status_source = "manual"
        db.session.commit()

        self._apply(self._row("UPS0910", no_return="FALSE"))

        self.assertEqual(mission.departure_status, "departed")
        self.assertEqual(mission.actual_block_out_source, "manual")

    def test_native_and_unattributed_milestones_are_protected(self):
        native_time = datetime(2026, 8, 11, 7, 0)
        mission = self._mission("UPS0910", "LAX", status="crew_load_complete")
        mission.elmac_completed_at_utc = native_time
        mission.elmac_completed_source = "manual"
        mission.ramp_load_completed_at_utc = native_time
        mission.ramp_load_completed_source = "unknown"
        mission.crew_load_completed_at_utc = native_time
        mission.crew_load_completed_source = "neo"
        mission.actual_block_out_datetime_utc = native_time
        mission.actual_block_out_source = "manual"
        db.session.commit()

        result = self._apply(
            self._row(
                "UPS0910",
                elmac="",
                ramp_load_complete="2:30",
                crew_load_complete="",
                block="2:45",
            )
        )

        self.assertEqual(mission.elmac_completed_at_utc, native_time)
        self.assertEqual(mission.ramp_load_completed_at_utc, native_time)
        self.assertEqual(mission.crew_load_completed_at_utc, native_time)
        self.assertEqual(mission.actual_block_out_datetime_utc, native_time)
        self.assertEqual(mission.actual_block_out_source, "manual")
        self.assertTrue(result["results"][0]["warnings"])

    def test_stronger_progress_never_regresses_when_rain_event_clears(self):
        mission = self._mission("UPS0910", "LAX", status="blocked_out")
        mission.crew_load_completed_at_utc = datetime(2026, 8, 11, 7, 0)
        mission.crew_load_completed_source = GOOGLE_RAIN_SOURCE
        db.session.commit()

        self._apply(self._row("UPS0910", crew_load_complete=""))

        self.assertIsNone(mission.crew_load_completed_at_utc)
        self.assertEqual(mission.departure_status, "blocked_out")

    def test_cancelled_mission_is_not_uncancelled(self):
        mission = self._mission("UPS0910", "LAX", status="cancelled")

        self._apply(
            self._row(
                "UPS0910",
                ramp_load_complete="2:22",
                crew_load_complete="2:24",
                block="2:29",
            )
        )

        self.assertEqual(mission.departure_status, "cancelled")
        self.assertEqual(mission.actual_block_out_source, GOOGLE_RAIN_SOURCE)

    def _apply(self, *rows):
        result = apply_google_rain_departure_milestones(self.operation, rows=rows)
        db.session.commit()
        return result

    def _mission(
        self,
        flight_number,
        destination,
        *,
        tail="N123UP",
        status="scheduled",
        std=None,
    ):
        mission = SortDateMission(
            sort_date=self.operation.sort_date,
            gateway_code="RFD",
            sort_name="night",
            sort_date_operation_id=self.operation.id,
            mission_type="departure",
            mission_source="master",
            flight_number=flight_number,
            origin="RFD",
            destination=destination,
            timezone="America/Chicago",
            planned_datetime_local=std or datetime(2026, 8, 11, 2, 24),
            assigned_tail_number=tail,
            departure_status=status,
        )
        db.session.add(mission)
        db.session.commit()
        return mission

    @staticmethod
    def _row(flight_number, destination="LAX", std="2:24", **values):
        return {
            "sheet_row": 4,
            "flight_number": flight_number,
            "destination": destination,
            "std": std,
            **values,
        }


if __name__ == "__main__":
    unittest.main()
