from datetime import date, datetime
import unittest

from app import create_app
from app.extensions import db
from app.models import Gateway, SortDateMission, SortDateOperation
from app.neonodes.neorain.services import (
    NEORAIN_MILESTONE_SOURCE,
    NeoRainMilestoneError,
    mutate_neorain_departure_milestone,
)
from app.services.time_display import utc_to_local_naive


class NeoRainOutboundMutationTest(unittest.TestCase):
    def setUp(self):
        config = type(
            "NeoRainOutboundMutationTestConfig",
            (),
            {
                "SECRET_KEY": "neorain-outbound-mutation-test-secret",
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
            sort_date=date(2026, 6, 18),
        )
        db.session.add(self.operation)
        db.session.flush()
        self.mission = SortDateMission(
            sort_date_operation_id=self.operation.id,
            sort_date=self.operation.sort_date,
            gateway_code=self.gateway.code,
            sort_name="night",
            mission_type="departure",
            mission_source="master",
            flight_number="UPS0910",
            origin="RFD",
            destination="LAX",
            timezone="America/Chicago",
            planned_datetime_local=datetime(2026, 6, 19, 2, 24),
            departure_status="scheduled",
        )
        db.session.add(self.mission)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_hhmm_resolves_night_sort_after_midnight(self):
        result = mutate_neorain_departure_milestone(
            self.mission,
            self.operation,
            "ramp_load_complete",
            "0237",
        )

        local_value = utc_to_local_naive(
            self.mission.ramp_load_completed_at_utc,
            self.mission.timezone,
        )
        self.assertTrue(result["changed"])
        self.assertEqual(local_value, datetime(2026, 6, 19, 2, 37))
        self.assertEqual(
            self.mission.ramp_load_completed_source,
            NEORAIN_MILESTONE_SOURCE,
        )

    def test_invalid_hhmm_is_rejected(self):
        for raw_value in ("2560", "1267", "123", "12:34", "abcd"):
            with self.subTest(raw_value=raw_value):
                with self.assertRaises(NeoRainMilestoneError):
                    mutate_neorain_departure_milestone(
                        self.mission,
                        self.operation,
                        "ramp_load_complete",
                        raw_value,
                    )
        self.assertIsNone(self.mission.ramp_load_completed_at_utc)

    def test_three_milestones_use_canonical_values_and_block_out_is_not_departed(self):
        mutate_neorain_departure_milestone(
            self.mission, self.operation, "ramp_load_complete", "2359"
        )
        self.assertEqual(self.mission.departure_status, "ramp_load_complete")
        mutate_neorain_departure_milestone(
            self.mission, self.operation, "crew_load_complete", "0001"
        )
        self.assertEqual(self.mission.departure_status, "crew_load_complete")
        result = mutate_neorain_departure_milestone(
            self.mission, self.operation, "official_block_out", "0010"
        )

        self.assertEqual(result["departure_status"], "blocked_out")
        self.assertNotEqual(self.mission.departure_status, "departed")
        self.assertEqual(
            self.mission.actual_block_out_source,
            NEORAIN_MILESTONE_SOURCE,
        )

    def test_clear_neorain_owned_milestone_recomputes_factual_progress(self):
        self._set_all_facts()
        mutate_neorain_departure_milestone(
            self.mission, self.operation, "official_block_out", ""
        )

        self.assertIsNone(self.mission.actual_block_out_datetime_utc)
        self.assertEqual(self.mission.actual_block_out_source, "unknown")
        self.assertEqual(self.mission.departure_status, "crew_load_complete")

    def test_no_return_requires_all_facts_then_sets_and_reverses(self):
        with self.assertRaisesRegex(NeoRainMilestoneError, "requires"):
            mutate_neorain_departure_milestone(
                self.mission, self.operation, "no_return", True
            )

        self._set_all_facts()
        set_result = mutate_neorain_departure_milestone(
            self.mission, self.operation, "no_return", True
        )
        self.assertTrue(set_result["changed"])
        self.assertEqual(self.mission.departure_status, "departed")
        self.assertEqual(
            self.mission.departure_status_source,
            NEORAIN_MILESTONE_SOURCE,
        )

        clear_result = mutate_neorain_departure_milestone(
            self.mission, self.operation, "no_return", False
        )
        self.assertTrue(clear_result["changed"])
        self.assertEqual(self.mission.departure_status, "blocked_out")
        self.assertEqual(self.mission.departure_status_source, "unknown")

    def test_factual_edit_after_no_return_preserves_departed(self):
        self._set_all_facts()
        mutate_neorain_departure_milestone(
            self.mission, self.operation, "no_return", True
        )

        mutate_neorain_departure_milestone(
            self.mission, self.operation, "ramp_load_complete", "0005"
        )
        self.assertEqual(self.mission.departure_status, "departed")
        mutate_neorain_departure_milestone(
            self.mission, self.operation, "crew_load_complete", ""
        )
        self.assertEqual(self.mission.departure_status, "departed")

    def test_foreign_owned_facts_are_protected_and_elmac_is_untouched(self):
        self.mission.ramp_load_completed_at_utc = datetime(2026, 6, 19, 7, 0)
        self.mission.ramp_load_completed_source = "google_rain"
        self.mission.elmac_completed_at_utc = datetime(2026, 6, 19, 6, 30)
        self.mission.elmac_completed_source = "neoreptile"

        mutate_neorain_departure_milestone(
            self.mission, self.operation, "crew_load_complete", "0237"
        )

        with self.assertRaisesRegex(NeoRainMilestoneError, "owned by google_rain"):
            mutate_neorain_departure_milestone(
                self.mission, self.operation, "ramp_load_complete", "0237"
            )

        self.assertEqual(
            self.mission.elmac_completed_at_utc,
            datetime(2026, 6, 19, 6, 30),
        )
        self.assertEqual(self.mission.elmac_completed_source, "neoreptile")

    def _set_all_facts(self):
        mutate_neorain_departure_milestone(
            self.mission, self.operation, "ramp_load_complete", "2350"
        )
        mutate_neorain_departure_milestone(
            self.mission, self.operation, "crew_load_complete", "2355"
        )
        mutate_neorain_departure_milestone(
            self.mission, self.operation, "official_block_out", "0005"
        )


if __name__ == "__main__":
    unittest.main()
