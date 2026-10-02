import unittest
from datetime import date, datetime
from unittest.mock import patch

from app import create_app
from app.extensions import db
from app.models import (
    NeoScorpionFuelAssignment,
    NeoScorpionFuelAuditEntry,
    NeoScorpionFuelingEvent,
    NeoScorpionFuelingEventTankSnapshot,
    NeoScorpionFuelTruck,
    NeoScorpionFuelWorkState,
    NeoScorpionSettings,
    SortDateMission,
    SortDateOperation,
    User,
)
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.neoscorpion_spear_learning_capture import (
    build_completed_learning_record,
    capture_completed_learning_outcome,
    capture_current_sort_learning_outcomes,
)


class SpearLearningCaptureTest(unittest.TestCase):
    def setUp(self):
        TestConfig = type(
            "TestConfig",
            (),
            {
                "SECRET_KEY": "spear-learning-capture-test",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
                "AUTO_BOOTSTRAP_DATABASE": False,
            },
        )
        self.app = create_app(TestConfig)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.gateway = ensure_default_gateway_and_nodes()
        self.user = User(
            username="learning_fueler",
            email="learning_fueler@example.test",
            role="watcher",
            is_active=True,
        )
        db.session.add(self.user)
        db.session.flush()
        self.operation = SortDateOperation(
            generated_by_user_id=self.user.id,
            gateway_id=self.gateway.id,
            sort_date=date(2026, 9, 3),
            gateway_code=self.gateway.code,
            sort_name="night",
            window_minutes=60,
        )
        db.session.add(self.operation)
        db.session.flush()
        self.mission = SortDateMission(
            sort_date=self.operation.sort_date,
            gateway_code=self.gateway.code,
            sort_name="night",
            sort_date_operation_id=self.operation.id,
            mission_type="departure",
            mission_source="manual",
            flight_number="UPS100",
            origin=self.gateway.code,
            destination="SDF",
            timezone="America/Chicago",
            planned_datetime_local=datetime(2026, 9, 3, 1, 0),
            planned_datetime_utc=datetime(2026, 9, 3, 6, 0),
            planned_source="manual",
            planned_fuel_load=50000,
            assigned_tail_number="N412UP",
            tail_source="manual",
            fuel_status="complete",
            fuel_completed_at_utc=datetime(2026, 9, 3, 5, 30),
            departure_status="loading",
        )
        truck = NeoScorpionFuelTruck(
            gateway_id=self.gateway.id,
            truck_number="415709",
            capacity_gallons=10000,
            is_active=True,
            is_out_of_service=False,
        )
        db.session.add_all([self.mission, truck])
        db.session.flush()
        self.assignment = NeoScorpionFuelAssignment(
            sort_date_operation_id=self.operation.id,
            sort_date_mission_id=self.mission.id,
            assigned_fueler_user_id=self.user.id,
            assigned_truck_id=truck.id,
            transfer_fuel_gallons=1200,
            review_status="complete",
            completed_at_utc=datetime(2026, 9, 3, 5, 30),
            completed_by_user_id=self.user.id,
            confirmed_tail_number="N412UP",
            operational_status="active",
        )
        db.session.add(self.assignment)
        db.session.flush()
        self.work = NeoScorpionFuelWorkState(
            fuel_assignment_id=self.assignment.id,
            tail_number="N412UP",
            on_at_utc=datetime(2026, 9, 3, 4, 40),
            off_at_utc=datetime(2026, 9, 3, 5, 20),
            off_by_user_id=self.user.id,
            apu_running=False,
            apu_allowance_lbs=0,
        )
        db.session.add(self.work)
        db.session.flush()
        event = NeoScorpionFuelingEvent(
            sort_date_operation_id=self.operation.id,
            fuel_assignment_id=self.assignment.id,
            fuel_work_state_id=self.work.id,
            tail_number="N412UP",
            fuel_truck_id=truck.id,
            sequence_number=1,
            event_type="fuel",
            cycle_number=1,
            started_at_utc=datetime(2026, 9, 3, 4, 40),
            ended_at_utc=datetime(2026, 9, 3, 5, 20),
            transfer_fuel_gallons=1200,
            fueler_user_id=self.user.id,
            required_fuel_lbs=50000,
            apu_running=False,
            apu_allowance_lbs=0,
            neo_fuel_lbs=48000,
        )
        db.session.add(event)
        db.session.flush()
        db.session.add(
            NeoScorpionFuelingEventTankSnapshot(
                fueling_event_id=event.id,
                tank_code="left",
                remaining_lbs=10000,
                planned_lbs=15000,
                actual_lbs=14000,
            )
        )
        db.session.add(
            NeoScorpionSettings(
                gateway_id=self.gateway.id,
                spear_learning_capture_enabled=True,
            )
        )
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_clean_committed_outcome_is_training_eligible(self):
        record = build_completed_learning_record(
            self.gateway,
            self.operation,
            self.assignment,
            self.mission,
        )
        self.assertTrue(record["training_eligible"])
        self.assertEqual(record["exclusion_reasons"], [])
        self.assertEqual(record["fuel_events"][0]["transfer_fuel_gallons"], 1200)
        self.assertEqual(record["work_states"][0]["off_at_utc"], "2026-09-03T05:20:00.000000Z")

    def test_audited_or_interrupted_work_is_archived_but_not_training_eligible(self):
        db.session.add(
            NeoScorpionFuelAuditEntry(
                sort_date_operation_id=self.operation.id,
                fuel_assignment_id=self.assignment.id,
                fuel_work_state_id=self.work.id,
                action="correct_actual",
                field_name="left",
                old_value="13000",
                new_value="14000",
                reason="Correction",
                changed_by_user_id=self.user.id,
            )
        )
        db.session.commit()
        record = build_completed_learning_record(
            self.gateway,
            self.operation,
            self.assignment,
            self.mission,
        )
        self.assertFalse(record["training_eligible"])
        self.assertIn(
            "correction_or_interruption_history",
            record["exclusion_reasons"],
        )

    @patch(
        "app.services.neoscorpion_spear_learning_capture.export_learning_record"
    )
    def test_capture_only_runs_when_enabled_and_is_idempotent_at_vault_boundary(self, export):
        export.return_value = {
            "key": "learning-record/example.json.gz",
            "checksum": "abc",
            "already_saved": False,
        }
        first = capture_completed_learning_outcome(
            self.gateway,
            self.assignment.id,
        )
        self.assertTrue(first.captured)
        self.assertTrue(first.training_eligible)
        export.assert_called_once()

        settings = NeoScorpionSettings.query.filter_by(
            gateway_id=self.gateway.id
        ).one()
        settings.spear_learning_capture_enabled = False
        db.session.commit()
        export.reset_mock()
        disabled = capture_completed_learning_outcome(
            self.gateway,
            self.assignment.id,
        )
        self.assertFalse(disabled.captured)
        self.assertEqual(disabled.reason, "learning_capture_off")
        export.assert_not_called()

    @patch(
        "app.services.neoscorpion_spear_learning_capture.export_learning_record"
    )
    def test_enable_backfill_captures_completed_current_sort(self, export):
        export.return_value = {
            "key": "learning-record/example.json.gz",
            "checksum": "abc",
            "already_saved": False,
        }
        results = capture_current_sort_learning_outcomes(
            self.gateway,
            self.operation.id,
        )
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].captured)
        export.assert_called_once()


if __name__ == "__main__":
    unittest.main()
