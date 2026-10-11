from datetime import date, datetime
from unittest import TestCase
from unittest.mock import patch
from sqlalchemy import inspect

from app import create_app
from app.extensions import db
from app.models import (
    Gateway, NeoRainDelayInfo, NeoRainReportEntry, SortDateMission,
    SortDateOperation, StaffingAttendanceSummary, StaffingUnit, User,
)
from app.services.access_control import backfill_default_gateway_node_roles
from app.services.neorain_reports import (
    GOOGLE_RANGES, REPORT_FIELDS, _cached_google_recap, briefing_text, normalize_report_value, report_context,
    resolved_report_fields, save_report_entry, snapshot_current_google,
)
from app.services.password_policy import set_user_password
from app.services.permission_rules import ensure_default_permission_rules


class NeoRainReportTest(TestCase):
    def setUp(self):
        config = type("TestConfig", (), {
            "SECRET_KEY": "test", "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "SQLALCHEMY_TRACK_MODIFICATIONS": False,
        })
        self.app = create_app(config)
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        ensure_default_permission_rules()
        self.gateway = Gateway(code="RFD", name="RFD")
        db.session.add(self.gateway)
        db.session.flush()
        self.operation = SortDateOperation(gateway_id=self.gateway.id, gateway_code="RFD",
                                           sort_date=date(2026, 10, 10), sort_name="Night")
        db.session.add(self.operation)
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def _google_response(self, report_date="10/10/2026", **values):
        cells = {"Inputs!B9": "610", "Inputs!C10": "534", "Inputs!B11": "480",
                 "Inputs!C12": "#VALUE!", "Inputs!B18": "164,613", "Inputs!B19": "164,658"}
        cells.update(values)
        return {"valueRanges": [{"values": [[report_date]]}] + [
            {"values": [[cells.get(cell, "")]]} for _, cell, _ in REPORT_FIELDS.values()
        ]}

    def test_google_ramp_mapping_zero_and_invalid_error(self):
        with patch("app.services.neorain_reports.read_google_recap", return_value=self._google_response()):
            self.assertEqual(snapshot_current_google(self.operation), "Google recap date verified")
        ramp = StaffingUnit(unit_type="operation", name="Ramp", active=True)
        db.session.add(ramp)
        db.session.flush()
        db.session.add(StaffingAttendanceSummary(
            sort_date_operation_id=self.operation.id, attendance_date=self.operation.sort_date,
            scope_type="operation", scope_unit_id=ramp.id, on_payroll_count=999,
            worked_count=999, finalized_at=datetime.utcnow(),
        ))
        db.session.commit()
        fields, _ = resolved_report_fields(self.operation)
        self.assertEqual(fields["ramp_planned_payroll"]["value"], "610")
        self.assertEqual(fields["ramp_actual_payroll"]["value"], "534")
        self.assertEqual(fields["ramp_planned_working"]["value"], "480")
        self.assertIsNone(fields["ramp_actual_working"]["value"])
        save_report_entry(self.operation, "ramp_actual_working", "0", 0, None)
        fields, _ = resolved_report_fields(self.operation)
        self.assertEqual(fields["ramp_actual_working"]["value"], "0")
        self.assertEqual(fields["ramp_actual_working"]["source"], "NeoRain entry")
        save_report_entry(self.operation, "planned_volume", "0", 0, None)
        fields, _ = resolved_report_fields(self.operation)
        self.assertEqual(fields["planned_volume"]["value"], "0")
        self.assertEqual(fields["planned_volume"]["source"], "NeoRain entry")

    def test_date_mismatch_and_historical_snapshot_are_isolated(self):
        with patch("app.services.neorain_reports.read_google_recap", return_value=self._google_response("10/09/2026")):
            self.assertIn("withheld", snapshot_current_google(self.operation))
        self.assertEqual(NeoRainReportEntry.query.count(), 0)
        with patch("app.services.neorain_reports.read_google_recap", return_value=self._google_response()):
            snapshot_current_google(self.operation)
        with patch("app.services.neorain_reports.read_google_recap", side_effect=AssertionError("Historical Google read")):
            fields, _ = resolved_report_fields(self.operation, current=False)
        self.assertEqual(fields["planned_volume"]["value"], "164613")

    def test_google_outage_keeps_existing_neo_entries(self):
        save_report_entry(self.operation, "actual_volume", "37", 0, None)
        with patch("app.services.neorain_reports.read_google_recap", side_effect=TimeoutError()):
            self.assertEqual(snapshot_current_google(self.operation), "Google unavailable")
        fields, _ = resolved_report_fields(self.operation)
        self.assertEqual(fields["actual_volume"]["value"], "37")

    def test_invalid_google_update_does_not_keep_a_false_current_value(self):
        with patch("app.services.neorain_reports.read_google_recap", return_value=self._google_response(**{"Inputs!C12": "9"})):
            snapshot_current_google(self.operation)
        with patch("app.services.neorain_reports.read_google_recap", return_value=self._google_response(**{"Inputs!C12": "#REF!"})):
            snapshot_current_google(self.operation)
        fields, _ = resolved_report_fields(self.operation)
        self.assertIsNone(fields["ramp_actual_working"]["value"])

    def test_manual_version_and_canonical_hub_working_priority(self):
        save_report_entry(self.operation, "actual_volume", "0", 0, None)
        with self.assertRaisesRegex(ValueError, "changed"):
            save_report_entry(self.operation, "actual_volume", "10", 0, None)
        hub = StaffingUnit(unit_type="operation", name="Hub", active=True)
        db.session.add(hub)
        db.session.flush()
        db.session.add(StaffingAttendanceSummary(
            sort_date_operation_id=self.operation.id, attendance_date=self.operation.sort_date,
            scope_type="operation", scope_unit_id=hub.id, on_payroll_count=80,
            worked_count=0, finalized_at=datetime.utcnow(),
        ))
        db.session.commit()
        fields, _ = resolved_report_fields(self.operation)
        self.assertEqual(fields["hub_actual_working"]["value"], "0")
        self.assertEqual(fields["hub_actual_working"]["source"], "NeoStaffing")
        with self.assertRaisesRegex(ValueError, "owns"):
            save_report_entry(self.operation, "hub_actual_working", "5", 0, None)

    def _departure(self, flight, actual=None):
        mission = SortDateMission(
            sort_date_operation_id=self.operation.id, sort_date=self.operation.sort_date,
            gateway_code="RFD", sort_name="Night", mission_type="departure",
            mission_source="master", wave="1", flight_number=flight, origin="RFD", destination="SDF",
            timezone="UTC", planned_datetime_utc=datetime(2026, 10, 11, 1, 0),
            actual_block_out_datetime_utc=actual, departure_status="scheduled",
        )
        db.session.add(mission)
        db.session.flush()
        return mission

    def test_briefing_lists_all_recorded_departures_and_ordered_delay_facts(self):
        first = self._departure("UPS07831", datetime(2026, 10, 11, 1, 11))
        second = self._departure("UPS910", datetime(2026, 10, 11, 1, 20))
        second.late_metrics_included_override = False
        self._departure("UPS948")
        db.session.add_all([
            NeoRainDelayInfo(sort_date_mission_id=first.id, minutes=3, code="RS", notes="MULTIPLE LATE INBOUND AIRCRAFT"),
            NeoRainDelayInfo(sort_date_mission_id=first.id, minutes=2, code="TD", notes="HEAT WX RESTRICT"),
        ])
        db.session.commit()
        report = report_context(self.operation)
        text = briefing_text(report)
        self.assertIn("▪ UPS7831 RFD-SDF ATD: 01:11, RS MULTIPLE LATE INBOUND AIRCRAFT; TD HEAT WX RESTRICT", text)
        self.assertIn("▪ UPS910 RFD-SDF ATD: 01:20", text)
        self.assertNotIn("▪ UPS948", text)
        self.assertIn("• HPS Plan UNAVAILABLE vs Actual UNAVAILABLE", text)
        self.assertEqual(report["outbound_late"]["aircraft_late"], 1)
        self.assertEqual(report["outbound_late"]["late_minutes"], 11)
        self.assertEqual(report["eligible_outbound"], 2)

    def test_report_routes_and_edit_permission(self):
        user = User(username="rain_report_viewer", email="rain_report_viewer@example.test",
                    first_name="Rain", last_name="Viewer", full_name="Rain Viewer",
                    employee_id="RAIN-REPORT-VIEWER", email_verified_at=datetime.utcnow(),
                    role="watcher", is_active=True)
        set_user_password(user, "TestPassword123!")
        db.session.add(user)
        db.session.flush()
        backfill_default_gateway_node_roles(user, role="watcher")
        db.session.commit()
        self.client.post("/login", data={"username": user.username, "password": "TestPassword123!"})
        for path in (f"/neorain/rainrock?operation_id={self.operation.id}",
                     f"/neorain/daily-briefing?operation_id={self.operation.id}"):
            self.assertEqual(self.client.get(path).status_code, 200)
        self.assertEqual(self.client.post("/neorain/rainrock/entry", data={
            "operation_id": self.operation.id, "field": "actual_volume", "value": "10", "expected_version": "0",
        }).status_code, 403)
        self.assertEqual(NeoRainReportEntry.query.filter_by(data_source="manual").count(), 0)

    def test_operator_entry_and_stale_version(self):
        user = User(username="rain_report_operator", email="rain_report_operator@example.test",
                    first_name="Rain", last_name="Operator", full_name="Rain Operator",
                    employee_id="RAIN-REPORT-OPERATOR", email_verified_at=datetime.utcnow(),
                    role="operator", is_active=True)
        set_user_password(user, "TestPassword123!")
        db.session.add(user)
        db.session.flush()
        backfill_default_gateway_node_roles(user, role="operator")
        db.session.commit()
        self.client.post("/login", data={"username": user.username, "password": "TestPassword123!"})
        payload = {"operation_id": self.operation.id, "field": "ramp_actual_working",
                   "value": "0", "expected_version": "0"}
        first = self.client.post("/neorain/rainrock/entry", data=payload, follow_redirects=True)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(NeoRainReportEntry.query.filter_by(data_source="manual").one().value, "0")
        second = self.client.post("/neorain/rainrock/entry", data={**payload, "value": "12"}, follow_redirects=True)
        self.assertIn(b"changed. Reload", second.data)
        self.assertEqual(NeoRainReportEntry.query.filter_by(data_source="manual").one().value, "0")

    def test_numeric_normalization(self):
        self.assertEqual(normalize_report_value("planned_volume", "0"), "0")
        self.assertEqual(normalize_report_value("smalls_percent", "50%"), "50")
        self.assertIsNone(normalize_report_value("ramp_actual_working", "#VALUE!"))

    def test_production_missing_table_bootstrap_is_idempotent(self):
        from app.services.schema_sync import _create_missing_application_tables
        NeoRainReportEntry.__table__.drop(db.engine)
        existing = set(inspect(db.engine).get_table_names())
        self.assertNotIn(NeoRainReportEntry.__tablename__, existing)
        _create_missing_application_tables(existing)
        self.assertIn(NeoRainReportEntry.__tablename__, inspect(db.engine).get_table_names())
        _create_missing_application_tables(set(inspect(db.engine).get_table_names()))

    def test_google_reader_uses_one_bounded_readonly_batch(self):
        from unittest.mock import Mock
        from app.services.neorain_reports import read_google_recap
        _cached_google_recap.cache_clear()
        client = Mock()
        spreadsheet = client.open_by_key.return_value
        spreadsheet.values_batch_get.return_value = self._google_response()
        with patch("app.services.google_motherbrain_sheets._credential_json", return_value=("test-credential", "test")), \
             patch("app.services.neorain_reports._configured_reader_inputs", return_value=({"client_email": "test"}, "ignored")), \
             patch("app.services.neorain_reports._create_gspread_client", return_value=client):
            read_google_recap()
            read_google_recap()
        self.assertEqual(spreadsheet.values_batch_get.call_count, 1)
        self.assertEqual(tuple(spreadsheet.values_batch_get.call_args.args[0]), GOOGLE_RANGES)
        self.assertEqual(GOOGLE_RANGES[0], "'Completed Form'!B2")
        _cached_google_recap.cache_clear()
