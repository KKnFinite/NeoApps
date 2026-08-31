import unittest
from datetime import date, datetime, timedelta

from app import create_app
from app.extensions import db
from app.models import Gateway, NeoSubZeroPretreatState, SortDateMission, SortDateOperation, SortDateParkingAssignment, SortDateTailState
from app.neonodes.neosubzero.services import NeoSubZeroPretreatError, mutate_pretreat, pretreat_context, pretreat_revision
from app.services.permission_rules import DEFAULT_PERMISSION_RULES
from flask import render_template
from app.neonodes.neosubzero.services import SURFACE_LABELS


class NeoSubZeroPretreatTest(unittest.TestCase):
    def setUp(self):
        self.app = create_app(type("TestConfig", (), {"SECRET_KEY":"test","TESTING":True,"SQLALCHEMY_DATABASE_URI":"sqlite:///:memory:","SQLALCHEMY_TRACK_MODIFICATIONS":False}))
        self.context = self.app.app_context(); self.context.push(); db.create_all()
        self.gateway = Gateway(code="RFD", name="RFD"); db.session.add(self.gateway); db.session.flush()
        self.operation = SortDateOperation(sort_date=date(2026, 8, 31), gateway_id=self.gateway.id, gateway_code="RFD", sort_name="night")
        db.session.add(self.operation); db.session.flush()
        self.arrival = self._mission("arrival", "A1", "T1", datetime(2026,8,31,23,0), eta=datetime(2026,8,31,22,45))
        self.departure = self._mission("departure", "D1", "T1", datetime(2026,9,1,1,0))
        self.future_departure = self._mission("departure", "D2", "T2", datetime(2026,9,1,2,0))
        db.session.add(SortDateParkingAssignment(sort_date_operation_id=self.operation.id, tail_number="T1", position_code="A1")); db.session.commit()

    def tearDown(self):
        db.session.remove(); db.drop_all(); self.context.pop()

    def _mission(self, kind, flight, tail, planned, eta=None):
        row = SortDateMission(sort_date=self.operation.sort_date, gateway_code="RFD", sort_name="night", sort_date_operation_id=self.operation.id, mission_type=kind, flight_number=flight, origin="RFD" if kind=="departure" else "ONT", destination="ONT" if kind=="departure" else "RFD", assigned_tail_number=tail, planned_datetime_utc=planned, eta_datetime_utc=eta)
        db.session.add(row); db.session.flush(); return row

    def test_context_uses_canonical_current_operation_data_and_includes_future_tail(self):
        rows = pretreat_context(self.gateway, self.operation)["rows"]
        self.assertEqual(NeoSubZeroPretreatState.query.count(), 0)
        self.assertEqual([row["tail"] for row in rows], ["T1", "T2"])
        self.assertEqual(rows[0]["inbound_eta"], "17:45")
        self.assertEqual(rows[0]["parking"], "A1")
        self.assertEqual(rows[1]["parking"], "TBD")
        self.assertNotIn("fuel", rows[0])

    def test_reversible_plan_config_chronology_and_completion(self):
        state = mutate_pretreat(self.operation, self.departure, "toggle_planned", {}, None)
        self.assertTrue(state.pretreat_planned)
        mutate_pretreat(self.operation, self.departure, "toggle_configured", {}, state)
        first_configured = state.configured_at_utc; self.assertIsNotNone(first_configured)
        mutate_pretreat(self.operation, self.departure, "toggle_configured", {}, state); self.assertIsNone(state.configured_at_utc)
        with self.assertRaises(NeoSubZeroPretreatError):
            mutate_pretreat(self.operation, self.departure, "save_treatment", {"pass1_surface_area":"wings_only","pass2_surface_area":"entire_aircraft","pass1_start":"2300","pass1_end":"2259"}, state)
        values = {"pass1_surface_area":"wings_only","pass2_surface_area":"wings_tail","pass1_start":"2300","pass1_end":"2310","pass2_start":"2310","pass2_end":"2320","notes":"  done  "}
        mutate_pretreat(self.operation, self.departure, "save_treatment", values, state); db.session.flush()
        tail_state = SortDateTailState.query.filter_by(tail_number="T1").one(); self.assertTrue(tail_state.pretreat_status); self.assertEqual(state.notes,"done")
        values["pass2_end"] = ""; mutate_pretreat(self.operation, self.departure, "save_treatment", values, state); self.assertFalse(tail_state.pretreat_status)

    def test_revision_changes_with_shared_pretreat_state(self):
        before = pretreat_revision(self.gateway, self.operation)
        mutate_pretreat(self.operation, self.departure, "toggle_planned", {}, None); db.session.commit()
        self.assertNotEqual(before, pretreat_revision(self.gateway, self.operation))

    def test_routes_and_permission_defaults(self):
        endpoints = {rule.endpoint for rule in self.app.url_map.iter_rules()}
        self.assertTrue({
            "neosubzero.index", "neosubzero.pretreat", "neosubzero.settings",
            "neosubzero.pretreat_revision_endpoint", "neosubzero.outbound",
            "neosubzero.coordinator", "neosubzero.departure_deice_mutate",
            "neosubzero.outbound_revision_endpoint",
            "neosubzero.coordinator_revision_endpoint",
        } <= endpoints)
        defaults = {key: role for key, role, _ in DEFAULT_PERMISSION_RULES}
        self.assertEqual(defaults["neosubzero.pretreat.view"], "watcher")
        self.assertEqual(defaults["neosubzero.pretreat.edit"], "simulator")
        self.assertEqual(defaults["neosubzero.settings.edit"], "master")

    def test_responsive_template_exposes_locked_pretreat_contract(self):
        with self.app.test_request_context("/neosubzero/pretreat"):
            html = render_template(
                "neonodes/neosubzero/pretreat.html", gateway=self.gateway,
                can_edit=True, revision="r1", surface_labels=SURFACE_LABELS,
                gateway_timezone="America/Chicago",
                refresh_status={"live_screen_refresh_interval_ms": 5000},
                **pretreat_context(self.gateway, self.operation),
            )
        self.assertIn("neosubzero-desktop", html)
        self.assertIn("neosubzero-mobile", html)
        self.assertIn("PASS 1 · TYPE I", html)
        self.assertIn("PASS 2 · TYPE IV", html)
        self.assertNotIn("Fuel Status", html)
