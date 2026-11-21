from datetime import datetime, timedelta
from unittest import TestCase
from unittest.mock import patch

from app.extensions import db
from app.models import NeoRainMissionNote, NeoRainReportEntry, SortDateGoogleMissionLink, SortDateOperation
from app.services.google_rain_integration_mode import set_rain_integration_mode, NEO_ONLY
from app.services.neorain_fuel_authority import rain_fuel_data_source, set_rain_fuel_data_source
from app.services.google_motherbrain_live_poll_execution import _run_google_rain_best_effort
from app.services.live_collaboration import entity_version
from app.services.neorain_rainrock import dashboard_context
from tests import test_neorain_overrides as fixtures


class FullNativeRainTest(TestCase):
    mode = NEO_ONLY
    def setUp(self):
        self.rain = fixtures.RainOverrideTest()
        self.rain.setUp()
        self.addCleanup(self.rain.doCleanups)
        self.app, self.client, self.operation = self.rain.app, self.rain.client, self.rain.operation
        self.rain.actor = self.rain.fixture._user("rain_notes_master", "master", rain_role="master")
        self.rain.fixture._login(self.rain.actor)
        self.arrival, self.departure = self.rain.arrival, self.rain.departure
        self.arrival.wave = self.departure.wave = "1"
        set_rain_integration_mode(self.rain.gateway, self.operation.sort_name, self.mode)
        set_rain_fuel_data_source(self.rain.gateway, self.operation.sort_name, "google")
        db.session.commit()

    def notes(self, mission, action, **options):
        return self.client.post("/neorain/mission-notes", data={
            "operation_id": self.operation.id, "mission_id": mission.id,
            "board": "inbound" if mission.mission_type == "arrival" else "outbound",
            "action": action, **options,
        })

    def test_cancelled_notes_crud_scope_and_reports(self):
        self.arrival.arrival_status = "cancelled"
        self.departure.departure_status = "cancelled"
        self.arrival.actual_block_in_datetime_utc = self.arrival.planned_datetime_utc + timedelta(minutes=9)
        self.departure.actual_block_out_datetime_utc = self.departure.planned_datetime_utc + timedelta(minutes=9)
        other = self.rain.fixture._mission(self.operation, "UPS825", self.departure.destination,
                                          planned=self.departure.planned_datetime_utc, wave=None)
        db.session.commit()
        for mission in (self.arrival, self.departure, other):
            self.assertEqual(self.notes(mission, "add", text=f"{mission.flight_number} note").status_code, 200)
        note = NeoRainMissionNote.query.filter_by(sort_date_mission_id=self.departure.id).one()
        self.assertEqual(note.created_by_user_id, self.rain.actor.id)
        self.assertEqual(note.updated_by_user_id, self.rain.actor.id)
        self.assertIsNotNone(note.created_at)
        for mission in (self.arrival, other):
            self.assertEqual(self.notes(mission, "update", note_id=note.id, version=1, text="Wrong mission").status_code, 409)
        self.assertEqual(self.notes(self.departure, "update", note_id=note.id, version=1, text="Mechanical issue").status_code, 200)
        self.assertEqual(self.notes(self.departure, "delete", note_id=note.id, version=1).status_code, 409)
        context = dashboard_context(self.operation, current=True)
        self.assertEqual(context["eligible_inbound"], 0)
        self.assertEqual(context["eligible_outbound"], 0)
        self.assertEqual(context["recorded_inbound"], 0)
        self.assertEqual(context["recorded_outbound"], 0)
        for mission in (self.arrival, self.departure):
            report_row = next(row for row in context["inbound_rows"] + context["outbound_rows"] if row["mission_id"] == mission.id)
            self.assertEqual(set(report_row), {"mission_id", "flight", "cancelled", "mission_notes"})
        state = self.client.get(f"/neorain/daily-briefing/state?operation_id={self.operation.id}").json
        self.assertIn("UPS948 — CANCELLED\n", state["briefing"])
        self.assertNotIn("Mechanical issue", state["briefing"])
        self.assertIn("UPS948 — CANCELLED — Mechanical issue", state["briefing_detailed"])
        self.assertNotIn("N948UP", state["briefing_detailed"])
        self.assertEqual(self.notes(self.departure, "delete", note_id=note.id, version=2).status_code, 200)
        self.assertIsNone(db.session.get(NeoRainMissionNote, note.id))
        self.assertEqual(NeoRainMissionNote.query.count(), 2)

    def test_native_writes_resets_reports_and_poll_never_call_google(self):
        self.departure.actual_block_out_datetime_utc = self.departure.planned_datetime_utc + timedelta(minutes=4)
        self.departure.actual_block_out_source = "google_rain"
        self.departure.departure_status = "blocked_out"
        db.session.add(SortDateGoogleMissionLink(sort_date_operation_id=self.operation.id,
            sort_date_mission_id=self.departure.id, mission_type="departure",
            last_flight_number=self.departure.flight_number, source_sheet="Outbound", source_row=12))
        db.session.add(NeoRainReportEntry(gateway_id=self.rain.gateway.id, sort_date_operation_id=self.operation.id,
            sort_date=self.operation.sort_date, sort_name=self.operation.sort_name, field_name="planned_volume",
            source_report_date=self.operation.sort_date, data_source="google", value="34000"))
        db.session.commit()
        with patch("app.services.neorain_reports.read_google_recap", side_effect=AssertionError("Google recap unavailable")) as recap, \
             patch("app.services.neorain_google_overrides.read_fresh_upstream", side_effect=AssertionError("Upstream unavailable")) as reset, \
             patch("app.services.neorain_google_overrides.certified_write", side_effect=AssertionError("Writer unavailable")) as write, \
             patch("app.services.google_motherbrain_live_poll_execution.read_google_rain_outbound_milestones",
                   side_effect=AssertionError("Rain sheet unavailable")) as reader:
            self.assertEqual(rain_fuel_data_source(self.rain.gateway, self.operation.sort_name), "neo")
            self.assertEqual(rain_fuel_data_source(self.rain.gateway, self.operation.sort_name, configured=True), "google")
            self.assertEqual(self.rain.edit(self.arrival, "block_in", "2359").status_code, 200)
            self.assertEqual(self.arrival.arrival_status, "arrived")
            self.assertEqual(self.arrival.actual_block_in_source, "neorain")
            self.assertEqual(self.rain.edit(self.departure, "official_block_out", "0255").status_code, 200)
            self.assertEqual(self.departure.actual_block_out_source, "neorain")
            self.assertEqual(self.rain.edit(self.departure, "official_block_out", action="reset").status_code, 200)
            self.assertIsNone(self.departure.actual_block_out_datetime_utc)
            self.assertEqual(_run_google_rain_best_effort(self.operation, operation_id=self.operation.id,
                reader=reader)["status"], "skipped_neo_authoritative")
            self.assertIsNone(self.client.get("/neorain/rainrock/state").json["fields"]["planned_volume"]["value"])
            self.assertIn("Volume Plan: UNAVAILABLE", self.client.get("/neorain/daily-briefing/state").json["briefing"])
            for adapter in (recap, reset, write, reader):
                adapter.assert_not_called()

    def test_notes_preserve_history_and_require_board_edit_authority(self):
        watched = self.rain.fixture._user("rain_notes_watcher", "watcher", rain_role="watcher")
        db.session.commit()
        self.rain.fixture._login(watched)
        self.assertEqual(self.notes(self.departure, "add", text="Forbidden").status_code, 403)
        self.rain.fixture._login(self.rain.actor)
        self.assertEqual(self.notes(self.departure, "add", text="Historical note").status_code, 200)
        historical = self.operation
        next_sort = SortDateOperation(gateway_id=historical.gateway_id, gateway_code=historical.gateway_code,
            sort_name=historical.sort_name, sort_date=historical.sort_date + timedelta(days=1))
        db.session.add(next_sort)
        db.session.commit()
        with patch("app.neonodes.neorain.routes.current_neorain_outbound_operation", return_value=next_sort):
            self.assertEqual(self.notes(self.departure, "add", text="Stale board").status_code, 409)
            old_state = self.client.get(f"/neorain/daily-briefing/state?operation_id={historical.id}").json
            new_state = self.client.get(f"/neorain/daily-briefing/state?operation_id={next_sort.id}").json
        self.assertIn("Historical note", old_state["briefing_detailed"])
        self.assertNotIn("Historical note", new_state["briefing_detailed"])

    def test_staffing_payroll_and_worked_keep_distinct_values_both_directions(self):
        url = f"?operation_id={self.operation.id}"
        values = {"ramp_planned_payroll": "610", "ramp_actual_payroll": "0",
                  "briefing_ramp_staffing_planned": "480", "briefing_ramp_staffing_actual": "427",
                  "hub_planned_payroll": "781", "hub_actual_payroll": "796",
                  "briefing_hub_staffing_planned": "650", "briefing_hub_staffing_actual": "599"}
        response = self.client.post("/neorain/rainrock/inputs" + url,
            json={"values": values, "versions": dict.fromkeys(values, 0)})
        self.assertEqual(response.status_code, 200, response.json)
        state = self.client.get("/neorain/daily-briefing/state" + url).json
        self.assertIn("Ramp Staffing: 427/480", state["briefing"])
        self.assertIn("Hub Staffing: 599/650", state["briefing"])
        for key, value in values.items():
            self.assertEqual(state["fields"][key]["value"], value)
        response = self.client.post("/neorain/daily-briefing/inputs" + url,
            json={"values": {"ramp_actual_payroll": "668"}, "versions": {"ramp_actual_payroll": 1}})
        self.assertEqual(response.status_code, 200)
        state = self.client.get("/neorain/rainrock/state" + url).json
        self.assertEqual(state["fields"]["ramp_actual_payroll"]["value"], "668")
        self.assertEqual(NeoRainReportEntry.query.filter_by(data_source="manual").count(), len(values))
