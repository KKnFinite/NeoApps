import unittest
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch

from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app import create_app
from app.extensions import db
from app.models import (
    GatewayMembership,
    GatewayNodeRole,
    NeoNode,
    NeoScorpionAircraftFuelSetting,
    NeoScorpionFuelAssignment,
    NeoScorpionFuelWorkState,
    NeoScorpionSettings,
    NeoScorpionSortAssetState,
    NeoScorpionTailFuelState,
    PortalAppAccess,
    SortDateMission,
    SortDateOperation,
    SortDateTailState,
    User,
)
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.neoscorpion import (
    DEFAULT_APU_RATE_THOUSAND_LBS_PER_HOUR,
    calculate_apu_allowance_lbs,
    fueler_context,
    save_fueler_entry,
    settings_context,
)
from app.services.password_policy import set_user_password
from app.services.permission_rules import ensure_default_permission_rules
from app.services.schema_sync import (
    LOCAL_SQLITE_OPTIONAL_COLUMNS,
    POSTGRES_OPTIONAL_COLUMNS,
    sync_local_sqlite_schema,
)


class NeoScorpionApuFuelTest(unittest.TestCase):
    def setUp(self):
        TestConfig = type(
            "TestConfig",
            (),
            {
                "SECRET_KEY": "neoscorpion-apu-test",
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
        ensure_default_permission_rules()
        self.operator = self._add_user("apu_operator", "operator")
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_schema_bootstrap_and_aircraft_rate_constraints(self):
        NeoScorpionAircraftFuelSetting.__table__.drop(bind=db.engine)

        sync_local_sqlite_schema(self.app)
        sync_local_sqlite_schema(self.app)

        self.assertIn(
            "neoscorpion_aircraft_fuel_settings",
            set(inspect(db.engine).get_table_names()),
        )
        for columns in (LOCAL_SQLITE_OPTIONAL_COLUMNS, POSTGRES_OPTIONAL_COLUMNS):
            self.assertEqual(
                set(columns["neoscorpion_fuel_work_states"]),
                {
                    "apu_running",
                    "apu_confirmed_at_utc",
                    "apu_allowance_lbs",
                    "applied_apu_rate_thousand_lbs_per_hour",
                    "off_at_utc",
                    "off_by_user_id",
                },
            )

        valid = NeoScorpionAircraftFuelSetting(
            gateway_id=self.gateway.id,
            aircraft_type="B757",
            apu_rate_thousand_lbs_per_hour=Decimal("0.35"),
        )
        db.session.add(valid)
        db.session.commit()

        self._assert_rejected(
            NeoScorpionAircraftFuelSetting(
                gateway_id=self.gateway.id,
                aircraft_type="B757",
                apu_rate_thousand_lbs_per_hour=Decimal("0.40"),
            )
        )
        self._assert_rejected(
            NeoScorpionAircraftFuelSetting(
                gateway_id=self.gateway.id,
                aircraft_type="MD11",
                apu_rate_thousand_lbs_per_hour=Decimal("0.30"),
            )
        )
        self._assert_rejected(
            NeoScorpionAircraftFuelSetting(
                gateway_id=self.gateway.id,
                aircraft_type="A300",
                apu_rate_thousand_lbs_per_hour=Decimal("-0.01"),
            )
        )

    def test_missing_rate_uses_virtual_default_and_settings_get_is_read_only(self):
        simulator = self._add_user("apu_simulator", "simulator")
        db.session.commit()
        self._login(simulator)

        context = settings_context(self.gateway)
        self.assertTrue(
            all(
                Decimal(item["rate"]) == DEFAULT_APU_RATE_THOUSAND_LBS_PER_HOUR
                for item in context["apu_rate_settings"]
            )
        )
        with patch.object(db.session, "commit", wraps=db.session.commit) as commit:
            response = self.client.get("/neoscorpion/settings")
            self.assertEqual(commit.call_count, 0)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(NeoScorpionAircraftFuelSetting.query.count(), 0)
        self.assertEqual(NeoScorpionSettings.query.count(), 0)
        self.assertEqual(NeoScorpionFuelWorkState.query.count(), 0)

    def test_simulator_can_edit_apu_rates_but_operator_cannot(self):
        simulator = self._add_user("rate_simulator", "simulator")
        db.session.commit()
        self._login(simulator)

        allowed = self.client.post(
            "/neoscorpion/settings",
            data={"action": "save_apu_rates", "apu_rate_b757": "0.42"},
        )
        self.assertEqual(allowed.status_code, 302)
        setting = NeoScorpionAircraftFuelSetting.query.one()
        self.assertEqual(setting.aircraft_type, "B757")
        self.assertEqual(setting.apu_rate_thousand_lbs_per_hour, Decimal("0.4200"))
        self.assertEqual(setting.updated_by_user_id, simulator.id)

        self._clear_login()
        self._login(self.operator)
        denied = self.client.post(
            "/neoscorpion/settings",
            data={"action": "save_apu_rates", "apu_rate_b757": "0.55"},
        )
        self.assertIn(denied.status_code, {302, 403})
        db.session.refresh(setting)
        self.assertEqual(setting.apu_rate_thousand_lbs_per_hour, Decimal("0.4200"))

    def test_apu_rounding_examples(self):
        effective_departure = datetime(2026, 8, 18, 5, 0)
        examples = (
            (datetime(2026, 8, 18, 3, 45), 400),
            (datetime(2026, 8, 18, 3, 30), 500),
            (datetime(2026, 8, 18, 2, 30), 800),
        )
        for confirmation_time, expected_lbs in examples:
            with self.subTest(confirmation_time=confirmation_time):
                self.assertEqual(
                    calculate_apu_allowance_lbs(
                        effective_departure,
                        0,
                        confirmation_time,
                        Decimal("0.30"),
                    ),
                    expected_lbs,
                )

    def test_apu_no_and_configured_yes_snapshot_legacy_and_target(self):
        operation, mission, assignment = self._assignment(required_lbs=50000)
        confirmation_time = datetime(2026, 8, 18, 3, 45)

        no_result = save_fueler_entry(
            self.gateway,
            self.operator,
            self._form(assignment, apu_running="no"),
            now_utc=confirmation_time,
        )
        self.assertTrue(no_result.changed)
        self.assertFalse(no_result.fuel_work_state.apu_running)
        self.assertEqual(no_result.fuel_work_state.apu_allowance_lbs, 0)
        self.assertEqual(no_result.fuel_work_state.apu_confirmed_at_utc, confirmation_time)
        self.assertEqual(no_result.tail_fuel_state.apu_lbs, 0)
        self.assertEqual(no_result.revision, 1)
        db.session.commit()

        no_row = fueler_context(self.gateway, self.operator)["rows"][0]
        self.assertEqual(no_row["fueling_target_display"], "50.0")
        self.assertEqual(no_row["neo_fuel_display"], "INCOMPLETE")

        db.session.add(
            NeoScorpionAircraftFuelSetting(
                gateway_id=self.gateway.id,
                aircraft_type="B757",
                apu_rate_thousand_lbs_per_hour=Decimal("0.40"),
            )
        )
        db.session.commit()
        yes_result = save_fueler_entry(
            self.gateway,
            self.operator,
            self._form(assignment, apu_running="yes"),
            now_utc=confirmation_time,
        )
        self.assertTrue(yes_result.fuel_work_state.apu_running)
        self.assertEqual(yes_result.fuel_work_state.apu_allowance_lbs, 500)
        self.assertEqual(
            yes_result.fuel_work_state.applied_apu_rate_thousand_lbs_per_hour,
            Decimal("0.40"),
        )
        self.assertEqual(yes_result.tail_fuel_state.apu_lbs, 500)
        self.assertEqual(yes_result.revision, 2)
        db.session.commit()

        yes_row = fueler_context(self.gateway, self.operator)["rows"][0]
        self.assertEqual(yes_row["fueling_target_display"], "50.5")
        self.assertEqual(mission.planned_fuel_load, 50000)
        self.assertEqual(operation.window_minutes, 60)

        reset = save_fueler_entry(
            self.gateway,
            self.operator,
            self._form(assignment, apu_running="not_confirmed"),
            now_utc=datetime(2026, 8, 18, 4, 0),
        )
        self.assertIsNone(reset.fuel_work_state.apu_running)
        self.assertIsNone(reset.fuel_work_state.apu_confirmed_at_utc)
        self.assertIsNone(reset.fuel_work_state.apu_allowance_lbs)
        self.assertIsNone(reset.fuel_work_state.applied_apu_rate_thousand_lbs_per_hour)
        self.assertIsNone(reset.tail_fuel_state.apu_lbs)
        self.assertEqual(reset.revision, 3)

    def test_apu_yes_requires_planned_departure(self):
        operation, mission, assignment = self._assignment(required_lbs=50000)
        mission.planned_datetime_utc = None
        db.session.commit()

        with self.assertRaisesRegex(ValueError, "Planned departure is required"):
            save_fueler_entry(
                self.gateway,
                self.operator,
                self._form(assignment, apu_running="yes"),
                now_utc=datetime(2026, 8, 18, 3, 45),
            )

        self.assertEqual(NeoScorpionFuelWorkState.query.count(), 0)
        self.assertEqual(NeoScorpionSortAssetState.query.count(), 0)
        self.assertIsNotNone(operation.id)

    def test_allowance_does_not_drift_on_get_or_unrelated_tank_save(self):
        _operation, _mission, assignment = self._assignment(required_lbs=50000)
        first_time = datetime(2026, 8, 18, 3, 45)
        first = save_fueler_entry(
            self.gateway,
            self.operator,
            self._form(assignment, apu_running="yes"),
            now_utc=first_time,
        )
        db.session.commit()
        original_allowance = first.fuel_work_state.apu_allowance_lbs
        original_rate = first.fuel_work_state.applied_apu_rate_thousand_lbs_per_hour

        fueler_context(self.gateway, self.operator)
        later = save_fueler_entry(
            self.gateway,
            self.operator,
            self._form(
                assignment,
                apu_running="yes",
                remaining_left="10.0",
            ),
            now_utc=datetime(2026, 8, 18, 4, 30),
        )

        self.assertTrue(later.changed)
        self.assertEqual(later.revision, 2)
        self.assertEqual(later.fuel_work_state.apu_confirmed_at_utc, first_time)
        self.assertEqual(later.fuel_work_state.apu_allowance_lbs, original_allowance)
        self.assertEqual(
            later.fuel_work_state.applied_apu_rate_thousand_lbs_per_hour,
            original_rate,
        )

    def test_target_neo_fuel_and_copy_hook_require_complete_inputs(self):
        _operation, _mission, assignment = self._assignment(required_lbs=50000)
        self._login(self.operator)

        with patch.object(db.session, "commit", wraps=db.session.commit) as commit:
            unconfirmed = self.client.get("/neoscorpion/fueler")
            self.assertEqual(commit.call_count, 0)
        self.assertIn(b"FUELING TARGET", unconfirmed.data.upper())
        self.assertIn(b"NEO FUEL", unconfirmed.data.upper())
        self.assertNotIn(b"data-copy-neo-fuel", unconfirmed.data)
        self.assertEqual(NeoScorpionAircraftFuelSetting.query.count(), 0)
        self.assertEqual(NeoScorpionFuelWorkState.query.count(), 0)

        incomplete = save_fueler_entry(
            self.gateway,
            self.operator,
            self._form(
                assignment,
                apu_running="no",
                remaining_left="10.0",
                actual_left="9.0",
            ),
            now_utc=datetime(2026, 8, 18, 3, 45),
        )
        self.assertEqual(incomplete.revision, 1)
        db.session.commit()
        incomplete_row = fueler_context(self.gateway, self.operator)["rows"][0]
        self.assertEqual(incomplete_row["fueling_target_display"], "50.0")
        self.assertEqual(incomplete_row["neo_fuel_display"], "INCOMPLETE")

        complete = save_fueler_entry(
            self.gateway,
            self.operator,
            self._form(
                assignment,
                apu_running="no",
                remaining_ctr="20.0",
                remaining_right="30.0",
                actual_ctr="18.0",
                actual_right="27.0",
            ),
            now_utc=datetime(2026, 8, 18, 4, 0),
        )
        self.assertEqual(complete.revision, 2)
        db.session.commit()

        complete_row = fueler_context(self.gateway, self.operator)["rows"][0]
        self.assertEqual(complete_row["actual_total_display"], "54.0")
        self.assertEqual(complete_row["neo_fuel_display"], "54.0")
        rendered = self.client.get("/neoscorpion/fueler")
        self.assertIn(b'data-copy-neo-fuel="54.0"', rendered.data)
        self.assertIn(b"COPY NEO FUEL", rendered.data)

    def test_combined_tank_apu_save_increments_revision_once_and_noop_does_not(self):
        operation, _mission, assignment = self._assignment(required_lbs=50000)
        form = self._form(
            assignment,
            apu_running="yes",
            remaining_left="10.0",
        )
        first = save_fueler_entry(
            self.gateway,
            self.operator,
            form,
            now_utc=datetime(2026, 8, 18, 3, 45),
        )
        self.assertEqual(first.revision, 1)
        db.session.commit()

        noop = save_fueler_entry(
            self.gateway,
            self.operator,
            form,
            now_utc=datetime(2026, 8, 18, 4, 30),
        )
        self.assertFalse(noop.changed)
        self.assertEqual(noop.revision, 1)
        state = NeoScorpionSortAssetState.query.filter_by(
            sort_date_operation_id=operation.id
        ).one()
        self.assertEqual(state.revision, 1)

    def test_reassigned_fueler_cannot_submit_apu_change(self):
        operation, _mission, assignment = self._assignment(required_lbs=50000)
        replacement = self._add_user("apu_replacement", "operator")
        assignment.assigned_fueler_user_id = replacement.id
        state = NeoScorpionSortAssetState(
            sort_date_operation_id=operation.id,
            revision=7,
        )
        db.session.add(state)
        db.session.commit()

        with self.assertRaisesRegex(ValueError, "not found for this fueler"):
            save_fueler_entry(
                self.gateway,
                self.operator,
                self._form(assignment, apu_running="yes"),
                now_utc=datetime(2026, 8, 18, 3, 45),
            )

        db.session.refresh(state)
        self.assertEqual(state.revision, 7)
        self.assertEqual(NeoScorpionFuelWorkState.query.count(), 0)

    def _assignment(self, *, required_lbs, tail_number="N412UP"):
        operation = SortDateOperation(
            gateway_id=self.gateway.id,
            sort_date=date(2026, 8, 17),
            gateway_code=self.gateway.code,
            sort_name="night",
            window_minutes=60,
        )
        db.session.add(operation)
        db.session.flush()
        mission = SortDateMission(
            sort_date=operation.sort_date,
            gateway_code=self.gateway.code,
            sort_name="night",
            sort_date_operation_id=operation.id,
            mission_type="departure",
            mission_source="manual",
            flight_number="UPS701",
            origin=self.gateway.code,
            destination="SDF",
            timezone="America/Chicago",
            planned_datetime_local=datetime(2026, 8, 17, 23, 0),
            planned_datetime_utc=datetime(2026, 8, 18, 4, 0),
            planned_source="manual",
            planned_fuel_load=required_lbs,
            assigned_tail_number=tail_number,
            tail_source="manual",
            fuel_status="waiting",
            departure_status="loading",
        )
        db.session.add(mission)
        db.session.flush()
        assignment = NeoScorpionFuelAssignment(
            sort_date_operation_id=operation.id,
            sort_date_mission_id=mission.id,
            assigned_fueler_user_id=self.operator.id,
        )
        db.session.add_all(
            [
                assignment,
                SortDateTailState(
                    sort_date=operation.sort_date,
                    gateway_code=self.gateway.code,
                    sort_name="night",
                    tail_number=tail_number,
                    aircraft_type="757",
                    aircraft_type_source="derived",
                ),
            ]
        )
        db.session.commit()
        return operation, mission, assignment

    @staticmethod
    def _form(assignment, **values):
        return {
            "assignment_id": str(assignment.id),
            "transfer_fuel_gallons": "",
            "notes": "",
            "tail_fuel_status": "pending",
            **values,
        }

    def _add_user(self, username, role):
        user = User(
            username=username,
            email=f"{username}@example.test",
            first_name=username.replace("_", " ").title(),
            role="watcher",
            is_active=True,
        )
        set_user_password(user, "TestPassword123!")
        db.session.add(user)
        db.session.flush()
        membership = GatewayMembership(
            user_id=user.id,
            gateway_id=self.gateway.id,
            status="approved",
            is_active=True,
        )
        db.session.add(membership)
        db.session.flush()
        scorpion = NeoNode.query.filter_by(code="scorpion").one()
        db.session.add_all(
            [
                PortalAppAccess(
                    user_id=user.id,
                    app_code="neogateway",
                    status="approved",
                    role=role,
                    is_active=True,
                ),
                GatewayNodeRole(
                    gateway_membership_id=membership.id,
                    node_id=scorpion.id,
                    role=role,
                    is_active=True,
                ),
            ]
        )
        return user

    def _login(self, user):
        self.client.post(
            "/login",
            data={"email": user.email, "password": "TestPassword123!"},
        )

    def _clear_login(self):
        with self.client.session_transaction() as session:
            session.clear()

    def _assert_rejected(self, model):
        db.session.add(model)
        with self.assertRaises(IntegrityError):
            db.session.commit()
        db.session.rollback()


if __name__ == "__main__":
    unittest.main()
