from copy import deepcopy
from datetime import date, datetime
import json
from pathlib import Path
import unittest

from app import create_app
from app.extensions import db
from app.models import (
    Gateway,
    SortDateMission,
    SortDateOperation,
    SortDateParkingAssignment,
    SortDateTailState,
)


FIXTURE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "google_motherbrain_current_sort_v1.json"
)
ENDPOINT = "/integrations/google-motherbrain/current-sort/preview"
TOKEN = "integration-test-token-do-not-use"


class GoogleMotherBrainImportTest(unittest.TestCase):
    def setUp(self):
        TestConfig = type(
            "TestConfig",
            (),
            {
                "SECRET_KEY": "google-motherbrain-test-secret",
                "TESTING": True,
                "CSRF_PROTECT_TESTING": True,
                "CSRF_TOKEN_TTL_SECONDS": 7200,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
                "GOOGLE_MOTHERBRAIN_IMPORT_ENABLED": True,
                "GOOGLE_MOTHERBRAIN_IMPORT_TOKEN": TOKEN,
                "GOOGLE_MOTHERBRAIN_SPREADSHEET_ID": (
                    "10Il5VRW-O3-T9RhrVPvvDphUh03vD-heMbqJwxxmyDg"
                ),
                "GOOGLE_MOTHERBRAIN_MAX_REQUEST_BYTES": 524288,
            },
        )
        self.app = create_app(TestConfig)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.gateway = Gateway(code="RFD", name="NeoRFD", is_active=True)
        db.session.add(self.gateway)
        db.session.flush()
        self.operation = SortDateOperation(
            gateway_id=self.gateway.id,
            sort_date=date(2026, 8, 5),
            gateway_code="RFD",
            sort_name="night",
        )
        db.session.add(self.operation)
        db.session.commit()
        self.client = self.app.test_client()
        self.payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_feature_disabled_returns_404_no_store(self):
        self.app.config["GOOGLE_MOTHERBRAIN_IMPORT_ENABLED"] = False
        response = self._post()

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.get_json()["error"]["code"], "not_found")

    def test_missing_and_wrong_tokens_return_generic_401(self):
        missing = self.client.post(ENDPOINT, json=self.payload)
        wrong = self._post(token="wrong-token")

        for response in (missing, wrong):
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.get_json()["error"]["code"], "unauthorized")
            self.assertNotIn(TOKEN, response.get_data(as_text=True))

    def test_correct_token_is_accepted_and_endpoint_is_csrf_exempt(self):
        response = self._post()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["preview_only"])
        self.assertEqual(response.get_json()["operation"]["id"], self.operation.id)

    def test_wrong_content_type_returns_415(self):
        response = self.client.post(
            ENDPOINT,
            data=json.dumps(self.payload),
            content_type="text/plain",
            headers=self._headers(),
        )

        self.assertEqual(response.status_code, 415)
        self.assertEqual(response.get_json()["error"]["code"], "unsupported_media_type")

    def test_oversized_payload_returns_413(self):
        self.app.config["GOOGLE_MOTHERBRAIN_MAX_REQUEST_BYTES"] = 10
        response = self._post()

        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.get_json()["error"]["code"], "payload_too_large")

    def test_malformed_json_returns_400(self):
        response = self.client.post(
            ENDPOINT,
            data=b'{"schema_version":',
            content_type="application/json",
            headers=self._headers(),
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"]["code"], "malformed_json")

    def test_envelope_validation_rejects_locked_fields_and_bad_schema(self):
        cases = (
            ("spreadsheet_id", "wrong", "invalid_spreadsheet"),
            ("schema_version", 2, "unsupported_schema_version"),
            ("sort_date", "08/05/2026", "invalid_sort_date"),
            ("gateway_code", "DFW", "invalid_gateway"),
            ("sort_name", "day", "invalid_sort"),
        )
        for field, value, code in cases:
            with self.subTest(field=field):
                payload = deepcopy(self.payload)
                payload[field] = value
                response = self._post(payload)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.get_json()["error"]["code"], code)

    def test_no_matching_operation_returns_404(self):
        self.payload["sort_date"] = "2026-08-06"
        response = self._post()

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()["error"]["code"], "operation_not_found")

    def test_case_normalized_duplicate_operations_return_409(self):
        duplicate = SortDateOperation(
            gateway_id=self.gateway.id,
            sort_date=self.operation.sort_date,
            gateway_code="rfd",
            sort_name="night",
        )
        db.session.add(duplicate)
        db.session.commit()

        response = self._post()

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["error"]["code"], "operation_ambiguous")

    def test_valid_empty_snapshot_returns_stable_preview(self):
        response = self._post()
        body = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(body["ok"])
        self.assertTrue(body["preview_only"])
        self.assertEqual(len(body["fingerprint"]), 64)
        self.assertEqual(body["summary"]["inbound"]["received"], 0)
        self.assertEqual(body["summary"]["outbound"]["received"], 0)

    def test_inbound_outbound_rows_normalize_and_match(self):
        arrival = self._mission("arrival", "UPS0910", "N152UP")
        departure = self._mission("departure", "UPS7831", "N303UP")
        db.session.commit()
        self.payload["snapshot"]["inbound"]["alp_rows"] = [
            self._alp_row("arrival", 15, "UPS910", "N 999 UP", "03:09 (E)")
        ]
        self.payload["snapshot"]["outbound"]["alp_rows"] = [
            self._alp_row("departure", 16, "5X7831", "n303up", "06:15 (S)")
        ]

        body = self._post().get_json()
        inbound = body["sections"]["inbound"]["matched_rows"][0]
        outbound = body["sections"]["outbound"]["matched_rows"][0]

        self.assertEqual(inbound["flight_number"], "UPS0910")
        self.assertEqual(inbound["tail_number"], "N999UP")
        self.assertEqual(inbound["mission"]["id"], arrival.id)
        self.assertEqual(outbound["flight_number"], "UPS7831")
        self.assertEqual(outbound["mission"]["id"], departure.id)

    def test_cancelled_row_can_preview_without_tail_or_time(self):
        mission = self._mission("arrival", "UPS1487", "N152UP")
        db.session.commit()
        row = self._alp_row("arrival", 15, "UPS1487", "", "")
        row["status"] = "CNL"
        self.payload["snapshot"]["inbound"]["alp_rows"] = [row]

        body = self._post().get_json()
        matched = body["sections"]["inbound"]["matched_rows"][0]

        self.assertEqual(matched["mission"]["id"], mission.id)
        self.assertEqual(matched["status_change"]["proposed"], "cancelled")
        self.assertEqual(body["summary"]["inbound"]["invalid"], 0)

    def test_arrival_a_marker_previews_actual_block_in_and_no_downgrade(self):
        mission = self._mission("arrival", "UPS1487", "N152UP")
        mission.arrival_status = "unloaded"
        db.session.commit()
        self.payload["snapshot"]["inbound"]["alp_rows"] = [
            self._alp_row("arrival", 15, "UPS1487", "N152UP", "03:09 (A)")
        ]

        matched = self._post().get_json()["sections"]["inbound"]["matched_rows"][0]

        self.assertEqual(matched["timing_change"]["field"], "actual_block_in_datetime_utc")
        self.assertEqual(matched["conflict"]["code"], "arrival_status_downgrade")
        self.assertIsNone(matched["status_change"])

    def test_explicit_arrival_status_takes_priority_over_time_marker(self):
        self._mission("arrival", "UPS1487", "N152UP")
        db.session.commit()
        row = self._alp_row("arrival", 15, "UPS1487", "N152UP", "03:09 (S)")
        row["status"] = "ARR"
        self.payload["snapshot"]["inbound"]["alp_rows"] = [row]

        matched = self._post().get_json()["sections"]["inbound"]["matched_rows"][0]

        self.assertEqual(matched["proposed_status"], "arrived")
        self.assertEqual(matched["timing_change"]["field"], "actual_block_in_datetime_utc")

    def test_here_manual_row_previews_tail_action_without_fake_mission(self):
        row = self._manual_row(4, "HERE", "N123UP")
        self.payload["snapshot"]["inbound"]["manual_rows"] = [row]
        before = SortDateMission.query.count()

        body = self._post().get_json()
        action = body["sections"]["inbound"]["standalone_tail_actions"][0]

        self.assertEqual(action["action"], "would_mark_here")
        self.assertFalse(action["creates_mission"])
        self.assertEqual(SortDateMission.query.count(), before)

    def test_outbound_manual_dep_row_is_preserved_without_creating_mission(self):
        row = {
            "sheet_row": 4,
            "date": "2026-08-05",
            "flight_number": "UPS9329",
            "destination": "SDF",
            "tail_number": "N445UP",
            "parking": "A05",
            "status": "DEP",
            "time": "",
        }
        self.payload["snapshot"]["outbound"]["manual_rows"] = [row]
        before = SortDateMission.query.count()

        body = self._post().get_json()
        manual = body["sections"]["outbound"]["manual_snapshot_rows"][0]

        self.assertEqual(manual["flight_number"], "UPS9329")
        self.assertEqual(manual["status_raw"], "DEP")
        self.assertFalse(manual["creates_mission"])
        self.assertEqual(SortDateMission.query.count(), before)

    def test_outbound_hot_destination_previews_tail_action_without_fake_mission(self):
        row = {
            "sheet_row": 4,
            "date": "2026-08-05",
            "flight_number": "UPS9329",
            "destination": "HOT",
            "tail_number": "N445UP",
            "parking": "A05",
            "status": "DEP",
            "time": "",
        }
        self.payload["snapshot"]["outbound"]["manual_rows"] = [row]

        action = self._post().get_json()["sections"]["outbound"][
            "standalone_tail_actions"
        ][0]

        self.assertEqual(action["action"], "would_mark_hot")
        self.assertFalse(action["creates_mission"])

    def test_parking_suffix_and_duplicate_conflicts_are_reported(self):
        self._mission("arrival", "UPS1001", "N101UP")
        self._mission("arrival", "UPS1002", "N102UP")
        db.session.commit()
        self.payload["snapshot"]["parking"]["assignments"] = [
            {"tail_number": "N101UP", "position": "A1-b"},
            {"tail_number": "N101UP", "position": "B01"},
            {"tail_number": "N102UP", "position": "A01-b"},
        ]

        parking = self._post().get_json()["sections"]["parking"]

        self.assertEqual(parking["received_assignments"][0]["position"], "A01")
        self.assertEqual(parking["received_assignments"][0]["lane_number"], 2)
        self.assertEqual(len(parking["duplicate_tail_placements"]), 2)
        self.assertEqual(len(parking["duplicate_position_lane_occupancy"]), 2)
        self.assertFalse(parking["safe_to_apply_atomically"])

    def test_parking_invalid_position_and_unknown_tail_are_reported(self):
        self._mission("arrival", "UPS1001", "N101UP")
        db.session.commit()
        self.payload["snapshot"]["parking"]["assignments"] = [
            {"tail_number": "N101UP", "position": "Z99"},
            {"tail_number": "N999UP", "position": "A01"},
        ]

        parking = self._post().get_json()["sections"]["parking"]

        self.assertEqual(len(parking["invalid_positions"]), 1)
        self.assertEqual(
            parking["invalid_positions"][0]["error_code"],
            "invalid_position",
        )
        self.assertEqual(len(parking["tails_not_known_to_current_sort"]), 1)
        self.assertEqual(
            parking["tails_not_known_to_current_sort"][0]["tail_number"],
            "N999UP",
        )
        self.assertFalse(parking["safe_to_apply_atomically"])

    def test_tail_swap_pending_and_acknowledged_states(self):
        self._mission("departure", "UPS7831", "N303UP")
        self._mission("departure", "UPS9329", "N445UP")
        db.session.commit()
        self.payload["snapshot"]["outbound"]["tail_swaps"] = [
            self._tail_swap(4, "UPS7831", "N999UP", ""),
            self._tail_swap(5, "UPS9329", "N998UP", "ACKNOWLEDGED"),
        ]

        swaps = self._post().get_json()["sections"]["tail_swaps"]["items"]

        self.assertEqual(swaps[0]["acknowledgment_state"], "pending")
        self.assertEqual(swaps[1]["acknowledgment_state"], "ready_to_finalize")
        self.assertFalse(swaps[0]["would_finalize"])
        self.assertFalse(swaps[1]["would_finalize"])

    def test_successful_preview_leaves_operational_database_unchanged(self):
        mission = self._mission("arrival", "UPS1487", "N152UP")
        tail_state = SortDateTailState(
            sort_date=self.operation.sort_date,
            gateway_code="RFD",
            sort_name="night",
            tail_number="N152UP",
            aircraft_type="767",
            aircraft_type_source="manual",
        )
        parking = SortDateParkingAssignment(
            sort_date_operation_id=self.operation.id,
            tail_number="N152UP",
            ramp_code="A",
            position_code="A01",
            lane_number=1,
        )
        db.session.add_all([tail_state, parking])
        db.session.commit()
        self.payload["snapshot"]["inbound"]["alp_rows"] = [
            self._alp_row("arrival", 15, "UPS1487", "N999UP", "03:09 (A)")
        ]
        self.payload["snapshot"]["parking"]["assignments"] = [
            {"tail_number": "N152UP", "position": "B01"}
        ]
        before = self._database_snapshot()

        response = self._post()
        after = self._database_snapshot()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(after, before)
        self.assertEqual(db.session.get(SortDateMission, mission.id).assigned_tail_number, "N152UP")

    def test_invalid_preview_leaves_database_unchanged(self):
        self._mission("arrival", "UPS1487", "N152UP")
        db.session.commit()
        before = self._database_snapshot()
        self.payload["spreadsheet_id"] = "not-authorized"

        response = self._post()

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._database_snapshot(), before)

    def test_token_and_payload_are_not_exposed_in_errors(self):
        secret_tail = "NSECRETTAIL"
        body = json.dumps({"tail_number": secret_tail})
        response = self.client.post(
            ENDPOINT,
            data=body,
            content_type="application/json",
            headers={"X-Neo-Integration-Token": "wrong-secret-token"},
        )
        rendered = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 401)
        self.assertNotIn("wrong-secret-token", rendered)
        self.assertNotIn(secret_tail, rendered)

    def test_normal_unsafe_route_remains_csrf_protected(self):
        response = self.client.post(
            "/login",
            data={"username": "nobody", "password": "not-a-password"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(b"Form session expired", response.data)

    def _post(self, payload=None, token=TOKEN):
        return self.client.post(
            ENDPOINT,
            json=self.payload if payload is None else payload,
            headers=self._headers(token),
        )

    def _headers(self, token=TOKEN):
        return {"X-Neo-Integration-Token": token}

    def _mission(self, mission_type, flight_number, tail_number):
        mission = SortDateMission(
            sort_date=self.operation.sort_date,
            gateway_code="RFD",
            sort_name="night",
            sort_date_operation_id=self.operation.id,
            mission_type=mission_type,
            mission_source="master",
            flight_number=flight_number,
            origin="SDF" if mission_type == "arrival" else "RFD",
            destination="RFD" if mission_type == "arrival" else "SDF",
            planned_datetime_local=datetime(2026, 8, 5, 22, 0),
            planned_datetime_utc=datetime(2026, 8, 6, 3, 0),
            assigned_tail_number=tail_number,
            arrival_status="scheduled" if mission_type == "arrival" else None,
            departure_status="loading" if mission_type == "departure" else None,
        )
        db.session.add(mission)
        db.session.flush()
        return mission

    def _alp_row(self, mission_type, sheet_row, flight, tail, time_value):
        row = {
            "sheet_row": sheet_row,
            "date": "2026-08-05",
            "flight_number": flight,
            "tail_number": tail,
            "parking": "A01",
            "status": "",
            "time": time_value,
        }
        if mission_type == "arrival":
            row["origin"] = "SDF"
        else:
            row["destination"] = "SDF"
        return row

    def _manual_row(self, sheet_row, status, tail):
        return {
            "sheet_row": sheet_row,
            "date": "2026-08-05",
            "flight_number": "",
            "origin": "HERE",
            "tail_number": tail,
            "parking": "A01",
            "status": status,
            "time": "",
        }

    def _tail_swap(self, sheet_row, flight, new_tail, acknowledgment):
        return {
            "sheet_row": sheet_row,
            "flight_number": flight,
            "destination": "SDF",
            "new_tail": new_tail,
            "scorpion_unlock": acknowledgment,
        }

    def _database_snapshot(self):
        missions = [
            (
                row.id,
                row.flight_number,
                row.assigned_tail_number,
                row.arrival_status,
                row.departure_status,
                row.eta_datetime_utc,
                row.actual_block_in_datetime_utc,
                row.actual_block_out_datetime_utc,
            )
            for row in SortDateMission.query.order_by(SortDateMission.id).all()
        ]
        tails = [
            (
                row.id,
                row.tail_number,
                row.aircraft_type,
                row.operational_status,
                row.parking_position,
            )
            for row in SortDateTailState.query.order_by(SortDateTailState.id).all()
        ]
        parking = [
            (
                row.id,
                row.tail_number,
                row.ramp_code,
                row.position_code,
                row.lane_number,
            )
            for row in SortDateParkingAssignment.query.order_by(
                SortDateParkingAssignment.id
            ).all()
        ]
        return {"missions": missions, "tails": tails, "parking": parking}


if __name__ == "__main__":
    unittest.main()
