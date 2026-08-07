from datetime import date, datetime
import unittest
from unittest.mock import patch

from app import create_app
from app.extensions import db
from app.models import (
    Gateway,
    MotherBrainAlert,
    SortDateMission,
    SortDateOperation,
    SortDateParkingAssignment,
    SortDateTailState,
)
from app.services.google_motherbrain_live_polling import (
    google_motherbrain_live_polling_enabled,
)
from app.services.google_motherbrain_parking import (
    apply_google_motherbrain_parking,
    apply_google_motherbrain_parking_batch,
    normalize_google_motherbrain_parking,
)


class GoogleMotherBrainParkingTest(unittest.TestCase):
    def setUp(self):
        TestConfig = type(
            "TestConfig",
            (),
            {
                "SECRET_KEY": "google-parking-test",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
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
            sort_date=date(2026, 8, 7),
        )
        db.session.add(self.operation)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_normalizes_a1_to_a01_primary(self):
        self.assertEqual(
            normalize_google_motherbrain_parking("A1"),
            {"ramp_code": "A", "position_code": "A01", "lane_number": 1},
        )

    def test_normalizes_e4_to_e04_primary(self):
        self.assertEqual(
            normalize_google_motherbrain_parking("E4"),
            {"ramp_code": "E", "position_code": "E04", "lane_number": 1},
        )

    def test_moves_tail_from_existing_neo_position(self):
        self._add_tail("N457UP")
        self._park("N457UP", "A03")
        db.session.commit()

        result = self._apply("N457UP", "E4")
        db.session.commit()
        operation_id = self.operation.id
        db.session.remove()

        assignment = SortDateParkingAssignment.query.filter_by(
            sort_date_operation_id=operation_id,
            tail_number="N457UP",
        ).one()
        self.assertEqual(result["status"], "applied")
        self.assertEqual((assignment.position_code, assignment.lane_number), ("E04", 1))

    def test_secondary_e04_b_uses_physical_e04_slot_two(self):
        self._add_tail("N111UP")
        self._add_tail("N222UP")
        self._park("N111UP", "E04", lane=1)
        db.session.commit()

        result = self._apply("N222UP", "E04-b")
        db.session.commit()

        self.assertEqual((result["position_code"], result["lane_number"]), ("E04", 2))
        self.assertEqual(
            (self._assignment("N222UP").position_code, self._assignment("N222UP").lane_number),
            ("E04", 2),
        )

    def test_secondary_e4_b_normalizes_to_e04_slot_two(self):
        self._add_tail("N111UP")
        self._add_tail("N222UP")
        self._park("N111UP", "E04", lane=1)
        db.session.commit()

        self._apply("N222UP", "e4-b")
        db.session.commit()

        assignment = self._assignment("N222UP")
        self.assertEqual((assignment.position_code, assignment.lane_number), ("E04", 2))

    def test_existing_tail_moves_without_duplicate_assignment(self):
        self._add_tail("N457UP")
        self._park("N457UP", "A01")
        db.session.commit()

        self._apply("N457UP", "B2")
        self._apply("N457UP", "C3")
        db.session.commit()

        self.assertEqual(
            SortDateParkingAssignment.query.filter_by(
                sort_date_operation_id=self.operation.id,
                tail_number="N457UP",
            ).count(),
            1,
        )
        self.assertEqual(self._assignment("N457UP").position_code, "C03")

    def test_primary_to_secondary_move(self):
        self._add_tail("N111UP")
        self._add_tail("N222UP")
        self._park("N111UP", "A01")
        self._park("N222UP", "E04", lane=1)
        db.session.commit()

        self._apply("N111UP", "E4-b")
        db.session.commit()

        assignment = self._assignment("N111UP")
        self.assertEqual((assignment.position_code, assignment.lane_number), ("E04", 2))

    def test_secondary_to_primary_move(self):
        self._add_tail("N111UP")
        self._add_tail("N222UP")
        self._park("N111UP", "E04", lane=1)
        self._park("N222UP", "E04", lane=2)
        db.session.commit()

        self._apply("N222UP", "A1")
        db.session.commit()

        assignment = self._assignment("N222UP")
        self.assertEqual((assignment.position_code, assignment.lane_number), ("A01", 1))

    def test_native_slot_two_promotion_runs_after_google_move(self):
        self._add_tail("N111UP")
        self._add_tail("N222UP")
        self._park("N111UP", "E04", lane=1)
        self._park("N222UP", "E04", lane=2)
        db.session.commit()

        self._apply("N111UP", "A1")
        db.session.commit()

        promoted = self._assignment("N222UP")
        self.assertEqual((promoted.position_code, promoted.lane_number), ("E04", 1))

    def test_physical_violation_applies_and_creates_native_alert(self):
        self._add_tail("N967UP")
        self._add_tail("N457UP")
        self._park("N457UP", "E03")
        db.session.commit()

        result = self._apply("N967UP", "E04")
        db.session.commit()

        self.assertEqual(result["status"], "applied")
        self.assertEqual(self._assignment("N967UP").position_code, "E04")
        self.assertEqual(
            MotherBrainAlert.query.filter_by(
                sort_date_operation_id=self.operation.id,
                active=True,
                title="Echo 767 clearance conflict",
            ).count(),
            1,
        )

    def test_later_google_correction_clears_native_conflict_alert(self):
        self._add_tail("N967UP")
        self._add_tail("N457UP")
        self._park("N457UP", "E03")
        db.session.commit()
        self._apply("N967UP", "E04")
        db.session.commit()
        alert = MotherBrainAlert.query.filter_by(active=True).one()

        self._apply("N457UP", "E02")
        db.session.commit()

        self.assertFalse(db.session.get(MotherBrainAlert, alert.id).active)

    def test_echo_valid_combinations_remain_conflict_free(self):
        for tail in ("N123UP", "N967UP", "N968UP", "N969UP"):
            self._add_tail(tail)
        db.session.commit()

        rows = [
            {"effective_tail": "N123UP", "parking_value": "E01"},
            {"effective_tail": "N967UP", "parking_value": "E02"},
            {"effective_tail": "N968UP", "parking_value": "E04"},
            {"effective_tail": "N969UP", "parking_value": "E08"},
        ]
        result = apply_google_motherbrain_parking_batch(self.operation, rows)
        db.session.commit()

        self.assertEqual(result["applied_count"], 4)
        self.assertFalse(result["physical_validation"]["has_conflicts"])

    def test_alpha_delta_767_rules_remain_active(self):
        self._add_tail("N967UP")
        self._add_tail("N457UP")
        self._park("N457UP", "A04")
        db.session.commit()

        result = self._apply("N967UP", "A03")
        db.session.commit()

        self.assertEqual(result["status"], "applied")
        self.assertTrue(result["physical_validation"]["has_conflicts"])
        self.assertGreater(
            MotherBrainAlert.query.filter_by(
                sort_date_operation_id=self.operation.id,
                active=True,
            ).count(),
            0,
        )

    def test_malformed_parking_is_skipped_and_preserves_existing_assignment(self):
        self._add_tail("N457UP")
        self._park("N457UP", "A03")
        db.session.commit()

        result = self._apply(
            "N457UP",
            "not-a-position",
            source_sheet="Inbound",
            source_row=17,
        )
        db.session.commit()

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["source_sheet"], "Inbound")
        self.assertEqual(result["source_row"], 17)
        self.assertEqual(self._assignment("N457UP").position_code, "A03")

    def test_missing_effective_tail_is_skipped(self):
        result = self._apply("", "A1", source_sheet="Outbound", source_row=9)
        db.session.commit()

        self.assertEqual(result["status"], "skipped")
        self.assertIn("effective tail", result["reason"])
        self.assertEqual(SortDateParkingAssignment.query.count(), 0)

    def test_bad_batch_row_does_not_prevent_valid_row(self):
        self._add_tail("N457UP")
        db.session.commit()

        result = apply_google_motherbrain_parking_batch(
            self.operation,
            [
                {
                    "effective_tail": "N457UP",
                    "parking_value": "bad",
                    "source_sheet": "Inbound",
                    "source_row": 4,
                },
                {
                    "effective_tail": "N457UP",
                    "parking_value": "E4",
                    "source_sheet": "Inbound",
                    "source_row": 5,
                },
            ],
        )
        db.session.commit()

        self.assertEqual((result["applied_count"], result["skipped_count"]), (1, 1))
        self.assertEqual(self._assignment("N457UP").position_code, "E04")

    def test_application_path_never_calls_google_client_or_reader(self):
        self._add_tail("N457UP")
        db.session.commit()

        with patch(
            "app.services.google_motherbrain_sheets._create_gspread_client"
        ) as client_factory, patch(
            "app.services.google_motherbrain_sheets.read_google_motherbrain_envelope"
        ) as reader:
            self._apply("N457UP", "A1")
            db.session.commit()

        client_factory.assert_not_called()
        reader.assert_not_called()

    def test_application_keeps_live_polling_off_and_does_not_auto_read(self):
        self._add_tail("N457UP")
        db.session.commit()
        self.assertFalse(google_motherbrain_live_polling_enabled(self.gateway, "night"))

        with patch(
            "app.services.google_motherbrain_sheets.read_google_motherbrain_envelope"
        ) as reader:
            self._apply("N457UP", "A1")
            db.session.commit()

        reader.assert_not_called()
        self.assertFalse(google_motherbrain_live_polling_enabled(self.gateway, "night"))

    def _apply(self, tail_number, parking_value, **source):
        return apply_google_motherbrain_parking(
            self.operation,
            tail_number,
            parking_value,
            **source,
        )

    def _add_tail(self, tail_number):
        mission = SortDateMission(
            sort_date_operation=self.operation,
            sort_date=self.operation.sort_date,
            gateway_code=self.operation.gateway_code,
            sort_name=self.operation.sort_name,
            mission_type="departure",
            mission_source="manual",
            flight_number=f"UPS{tail_number[1:4]}",
            origin="RFD",
            destination="SDF",
            planned_datetime_local=datetime(2026, 8, 8, 1, 0),
            planned_datetime_utc=datetime(2026, 8, 8, 6, 0),
            assigned_tail_number=tail_number,
        )
        state = SortDateTailState(
            sort_date=self.operation.sort_date,
            gateway_code=self.operation.gateway_code,
            sort_name=self.operation.sort_name,
            tail_number=tail_number,
            aircraft_type_source="manual",
        )
        db.session.add_all([mission, state])
        db.session.flush()

    def _park(self, tail_number, position_code, lane=1):
        assignment = SortDateParkingAssignment(
            sort_date_operation_id=self.operation.id,
            tail_number=tail_number,
            ramp_code=position_code[0],
            position_code=position_code,
            lane_number=lane,
        )
        db.session.add(assignment)
        db.session.flush()
        return assignment

    def _assignment(self, tail_number):
        return SortDateParkingAssignment.query.filter_by(
            sort_date_operation_id=self.operation.id,
            tail_number=tail_number,
        ).one()


if __name__ == "__main__":
    unittest.main()
