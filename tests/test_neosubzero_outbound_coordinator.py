import unittest
from datetime import date, datetime, timedelta

from flask import render_template

from app import create_app
from app.extensions import db
from app.models import (
    Gateway,
    NeoSubZeroDepartureDeiceEvent,
    NeoSubZeroPretreatState,
    NeoSubZeroSetting,
    SortDateMission,
    SortDateOperation,
    SortDateParkingAssignment,
    SortDateTailState,
    User,
)
from app.neonodes.neosubzero.routes import _coordinator_workspace_state
from app.neonodes.neosubzero.services import SURFACE_LABELS
from app.services.neosubzero_departure_deice import (
    COORDINATOR_REFRESH_KEY,
    OUTBOUND_REFRESH_KEY,
    PLAN_LABELS,
    NeoSubZeroDepartureDeiceError,
    departure_deice_context,
    departure_deice_revision,
    mutate_departure_deice,
    neosubzero_fluid_settings,
    set_neosubzero_fluid_settings,
)
from app.services.permission_rules import DEFAULT_PERMISSION_RULES
from app.services.permission_rules import ensure_default_permission_rules
from app.services.access_control import backfill_default_gateway_node_roles
from app.services.password_policy import set_user_password


class NeoSubZeroOutboundCoordinatorTest(unittest.TestCase):
    def setUp(self):
        config = type(
            "TestConfig",
            (),
            {
                "SECRET_KEY": "test",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            },
        )
        self.app = create_app(config)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.gateway = Gateway(code="RFD", name="RFD")
        db.session.add(self.gateway)
        db.session.flush()
        self.operation = SortDateOperation(
            sort_date=date(2026, 8, 31),
            gateway_id=self.gateway.id,
            gateway_code="RFD",
            sort_name="night",
        )
        db.session.add(self.operation)
        db.session.flush()
        self.first = self._departure(
            "5X101", "N101", "ONT", datetime(2026, 9, 1, 1, 0)
        )
        self.second = self._departure(
            "5X202", "N202", "SDF", datetime(2026, 9, 1, 2, 0)
        )
        db.session.add_all(
            [
                SortDateParkingAssignment(
                    sort_date_operation_id=self.operation.id,
                    tail_number="N101",
                    ramp_code="R",
                    position_code="R01",
                ),
                SortDateParkingAssignment(
                    sort_date_operation_id=self.operation.id,
                    tail_number="N202",
                    ramp_code="A",
                    position_code="A03",
                ),
            ]
        )
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def _departure(self, flight, tail, destination, planned):
        mission = SortDateMission(
            sort_date=self.operation.sort_date,
            gateway_code="RFD",
            sort_name="night",
            sort_date_operation_id=self.operation.id,
            mission_type="departure",
            mission_source="master",
            flight_number=flight,
            origin="RFD",
            destination=destination,
            timezone="America/Chicago",
            planned_datetime_local=planned - timedelta(hours=5),
            planned_datetime_utc=planned,
            assigned_tail_number=tail,
            departure_status="scheduled",
        )
        db.session.add(mission)
        db.session.flush()
        return mission

    def test_context_uses_canonical_facts_std_order_and_remote_normally(self):
        self.second.ramp_load_completed_at_utc = datetime(2026, 9, 1, 1, 10)
        self.second.crew_load_completed_at_utc = datetime(2026, 9, 1, 1, 20)
        self.second.actual_block_out_datetime_utc = datetime(2026, 9, 1, 2, 7)
        db.session.commit()
        rows = departure_deice_context(self.gateway, self.operation)["rows"]
        self.assertEqual([row["flight"] for row in rows], ["5X101", "5X202"])
        self.assertEqual(rows[0]["ramp"], "Remote")
        self.assertEqual(rows[1]["parking"], "A03")
        self.assertEqual(rows[1]["ramp_load_complete"], "20:10")
        self.assertEqual(rows[1]["crew_load_complete"], "20:20")
        self.assertEqual(rows[1]["block_out_variance"], "+7")
        self.assertNotIn("w_and_b", rows[1])

    def test_one_pass_completion_clear_and_correction_revokes_clear(self):
        event = mutate_departure_deice(
            self.operation, self.first, "initial_contact", {}, event=None
        )
        mutate_departure_deice(
            self.operation, self.first, "toggle_configured", {}, event=event
        )
        mutate_departure_deice(
            self.operation,
            self.first,
            "save_treatment",
            {
                "treatment_plan": "one_type_i",
                "pass1_surface_area": "wings_only",
                "pass1_start": "2000",
                "pass1_end": "2010",
            },
            event=event,
        )
        self.assertEqual(event.status, "finished")
        mutate_departure_deice(self.operation, self.first, "clear", {}, event=event)
        self.assertEqual(event.status, "cleared")
        mutate_departure_deice(
            self.operation,
            self.first,
            "save_treatment",
            {
                "treatment_plan": "one_type_i",
                "pass1_surface_area": "wings_tail",
                "pass1_start": "2000",
                "pass1_end": "",
            },
            event=event,
        )
        self.assertEqual(event.status, "configured")
        tail = SortDateTailState.query.filter_by(tail_number="N101").one()
        self.assertEqual(tail.deice_status, "configured")

    def test_two_pass_plan_types_chronology_and_minutes(self):
        event = mutate_departure_deice(
            self.operation, self.first, "initial_contact", {}, event=None
        )
        with self.assertRaises(NeoSubZeroDepartureDeiceError):
            mutate_departure_deice(
                self.operation,
                self.first,
                "save_treatment",
                {
                    "treatment_plan": "type_i_type_iv",
                    "pass1_surface_area": "wings_only",
                    "pass2_surface_area": "entire_aircraft",
                    "pass1_start": "2000",
                    "pass1_end": "2010",
                    "pass2_start": "2009",
                    "pass2_end": "2020",
                },
                event=event,
            )
        mutate_departure_deice(
            self.operation,
            self.first,
            "save_treatment",
            {
                "treatment_plan": "type_i_type_iv",
                "pass1_surface_area": "wings_only",
                "pass2_surface_area": "entire_aircraft",
                "pass1_start": "2000",
                "pass1_end": "2010",
                "pass2_start": "2010",
                "pass2_end": "2020",
            },
            event=event,
        )
        db.session.flush()
        row = departure_deice_context(self.gateway, self.operation)["rows"][0]
        self.assertEqual(row["pass_types"], ("Type I", "Type IV"))
        self.assertEqual(row["deice_minutes"], 20)
        self.assertEqual(row["final_fluid"]["concentration"], 100)

    def test_negative_pretreat_and_not_sprayed_collapse_states(self):
        tail_state = SortDateTailState(
            sort_date=self.operation.sort_date,
            gateway_code="RFD",
            sort_name="night",
            tail_number="N101",
            pretreat_status=True,
        )
        pretreat = NeoSubZeroPretreatState(
            sort_date_operation_id=self.operation.id,
            tail_number="N101",
            pass2_surface_area="entire_aircraft",
            pass2_started_at_utc=datetime(2026, 9, 1, 0, 5),
        )
        db.session.add_all([tail_state, pretreat])
        self.second.actual_block_out_datetime_utc = datetime(2026, 9, 1, 1, 0)
        db.session.commit()
        rows = departure_deice_context(
            self.gateway, self.operation, now_utc=datetime(2026, 9, 1, 1, 6)
        )["rows"]
        self.assertEqual(rows[0]["collapse_state"], "pretreated")
        self.assertEqual(rows[1]["collapse_state"], "not_sprayed")
        event = mutate_departure_deice(
            self.operation, self.second, "set_negative", {}, event=None
        )
        self.assertEqual(event.status, "negative")
        mutate_departure_deice(
            self.operation, self.second, "move_to_planned", {}, event=event
        )
        self.assertEqual(event.status, "deice_planned")

    def test_settings_script_and_revision_are_shared(self):
        defaults = neosubzero_fluid_settings(self.gateway)
        self.assertEqual(defaults.type_i_concentration_percent, 50)
        set_neosubzero_fluid_settings(self.gateway, "Kilfrost I", 60, "Kilfrost IV")
        event = mutate_departure_deice(
            self.operation, self.first, "initial_contact", {}, event=None
        )
        mutate_departure_deice(
            self.operation,
            self.first,
            "save_treatment",
            {
                "treatment_plan": "one_type_i",
                "pass1_surface_area": "wings_only",
                "pass1_start": "2000",
                "pass1_end": "2010",
            },
            event=event,
        )
        mutate_departure_deice(self.operation, self.first, "clear", {}, event=event)
        before = departure_deice_revision(self.gateway, self.operation)
        db.session.commit()
        row = departure_deice_context(self.gateway, self.operation)["rows"][0]
        self.assertEqual(row["script"]["fluid"], "Kilfrost I")
        self.assertEqual(row["script"]["concentration"], 60)
        self.assertIn("Start Time 2000 local", row["script"]["text"])
        with self.app.test_request_context("/neosubzero/outbound"):
            html = render_template(
                "neonodes/neosubzero/outbound.html",
                gateway=self.gateway,
                can_edit=True,
                plan_labels=PLAN_LABELS,
                surface_labels=SURFACE_LABELS,
                revision="r1",
                refresh_status={"live_screen_refresh_interval_ms": 5000},
                gateway_timezone="America/Chicago",
                **departure_deice_context(self.gateway, self.operation),
            )
        self.assertIn('data-locked="true"', html)
        self.assertIn("AIR-TO-GROUND", html)
        self.assertIn("EDIT", html)
        event.status = "negative"
        db.session.commit()
        self.assertNotEqual(before, departure_deice_revision(self.gateway, self.operation))

    def test_coordinator_ramps_and_templates_expose_locked_contract(self):
        context = departure_deice_context(self.gateway, self.operation)
        with self.app.test_request_context("/neosubzero/coordinator?ramp=Remote"):
            workspace = _coordinator_workspace_state(
                self.operation, context["rows"]
            )
            self.assertEqual([row["name"] for row in workspace["ramps"]], ["Remote", "Alpha"])
            self.assertEqual(workspace["selected_ramp"], "Remote")
            html = render_template(
                "neonodes/neosubzero/coordinator.html",
                gateway=self.gateway,
                can_edit=True,
                plan_labels=PLAN_LABELS,
                surface_labels=SURFACE_LABELS,
                revision="r1",
                refresh_status={"live_screen_refresh_interval_ms": 5000},
                gateway_timezone="America/Chicago",
                **context,
                **workspace,
            )
        self.assertIn("IN PROGRESS", html)
        self.assertIn("AIR-TO-GROUND SCRIPT", html)
        self.assertIn("Remote", html)
        self.assertNotIn("W&amp;B", html)
        with self.app.test_request_context("/neosubzero/outbound"):
            outbound_html = render_template(
                "neonodes/neosubzero/outbound.html",
                gateway=self.gateway,
                can_edit=True,
                plan_labels=PLAN_LABELS,
                surface_labels=SURFACE_LABELS,
                revision="r1",
                refresh_status={"live_screen_refresh_interval_ms": 5000},
                gateway_timezone="America/Chicago",
                **context,
            )
        self.assertIn("Ramp LC", outbound_html)
        self.assertIn("Crew LC", outbound_html)
        self.assertIn("Block-Out", outbound_html)
        self.assertNotIn("W&amp;B", outbound_html)

    def test_permissions_refresh_keys_and_schema(self):
        defaults = {key: role for key, role, _description in DEFAULT_PERMISSION_RULES}
        self.assertEqual(defaults["neosubzero.outbound.view"], "watcher")
        self.assertEqual(defaults["neosubzero.outbound.edit"], "simulator")
        self.assertEqual(defaults["neosubzero.coordinator.view"], "simulator")
        self.assertEqual(defaults["neosubzero.coordinator.edit"], "simulator")
        self.assertEqual(OUTBOUND_REFRESH_KEY, "neosubzero.outbound")
        self.assertEqual(COORDINATOR_REFRESH_KEY, "neosubzero.coordinator")
        self.assertIn(
            "neosubzero_departure_deice_events", db.metadata.tables
        )
        self.assertIn("neosubzero_settings", db.metadata.tables)
        self.assertEqual(NeoSubZeroDepartureDeiceEvent.query.count(), 0)
        self.assertEqual(NeoSubZeroSetting.query.count(), 0)


class NeoSubZeroRouteAccessTest(unittest.TestCase):
    def setUp(self):
        config = type(
            "TestConfig",
            (),
            {
                "SECRET_KEY": "test",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            },
        )
        self.app = create_app(config)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        ensure_default_permission_rules()
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_outbound_and_coordinator_use_independent_subzero_permissions(self):
        watcher = self._user("subzero_watcher", "watcher")
        self._login(watcher)
        self.assertEqual(self.client.get("/neosubzero/outbound").status_code, 200)
        denied = self.client.get("/neosubzero/coordinator", follow_redirects=False)
        self.assertEqual(denied.status_code, 302)

        simulator = self._user("subzero_simulator", "simulator")
        self._login(simulator)
        coordinator = self.client.get("/neosubzero/coordinator")
        self.assertEqual(coordinator.status_code, 200)
        self.assertIn(b"COORDINATOR", coordinator.data)

    def test_master_can_save_fluid_settings(self):
        master = self._user("subzero_master", "master")
        self._login(master)
        response = self.client.post(
            "/neosubzero/settings",
            data={
                "action": "save_fluids",
                "type_i_fluid_name": "Fluid One",
                "type_i_concentration_percent": "55",
                "type_iv_fluid_name": "Fluid Four",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        setting = NeoSubZeroSetting.query.one()
        self.assertEqual(setting.type_i_concentration_percent, 55)

    def _user(self, username, role):
        user = User(
            username=username,
            email=f"{username}@example.com",
            first_name="SubZero",
            last_name="User",
            full_name="SubZero User",
            employee_id=f"EMP-{username}",
            email_verified_at=datetime.utcnow(),
            role=role,
            is_active=True,
        )
        set_user_password(user, "TestPassword123!")
        db.session.add(user)
        db.session.flush()
        backfill_default_gateway_node_roles(user, role=role)
        db.session.commit()
        return user

    def _login(self, user):
        return self.client.post(
            "/login",
            data={"username": user.username, "password": "TestPassword123!"},
            follow_redirects=False,
        )


if __name__ == "__main__":
    unittest.main()
