import unittest
from datetime import date, datetime
from unittest.mock import patch

from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app import create_app
from app.extensions import db
from app.models import (
    GatewayMembership,
    GatewayNodeRole,
    NeoNode,
    NeoScorpionFuelAssignment,
    NeoScorpionFuelTankState,
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
    detailed_aircraft_type_for_tail,
    save_fueler_entry,
    tank_layout_for_tail,
)
from app.services.password_policy import set_user_password
from app.services.permission_rules import ensure_default_permission_rules
from app.services.schema_sync import sync_local_sqlite_schema


class NeoScorpionTankFuelTest(unittest.TestCase):
    def setUp(self):
        TestConfig = type(
            "TestConfig",
            (),
            {
                "SECRET_KEY": "neoscorpion-tank-test",
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
        db.session.add(NeoScorpionSettings(gateway_id=self.gateway.id))
        self.user = self._add_user("tank_fueler", "operator")
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_schema_bootstrap_and_database_constraints(self):
        NeoScorpionFuelTankState.__table__.drop(bind=db.engine)
        NeoScorpionFuelWorkState.__table__.drop(bind=db.engine)

        sync_local_sqlite_schema(self.app)
        sync_local_sqlite_schema(self.app)

        tables = set(inspect(db.engine).get_table_names())
        self.assertIn("neoscorpion_fuel_work_states", tables)
        self.assertIn("neoscorpion_fuel_tank_states", tables)

        _operation, _mission, assignment = self._assignment()
        work = NeoScorpionFuelWorkState(
            fuel_assignment_id=assignment.id,
            tail_number=" n412up ",
        )
        db.session.add(work)
        db.session.flush()
        self.assertEqual(work.tail_number, "N412UP")
        db.session.add(
            NeoScorpionFuelTankState(
                fuel_work_state_id=work.id,
                tank_code="left",
                remaining_lbs=10000,
                actual_lbs=9000,
            )
        )
        db.session.commit()

        self._assert_rejected(
            NeoScorpionFuelWorkState(
                fuel_assignment_id=assignment.id,
                tail_number="N412UP",
            )
        )
        self._assert_rejected(
            NeoScorpionFuelTankState(
                fuel_work_state_id=work.id,
                tank_code="left",
            )
        )

        negative_remaining_work = NeoScorpionFuelWorkState(
            fuel_assignment_id=assignment.id,
            tail_number="N413UP",
        )
        db.session.add(negative_remaining_work)
        db.session.commit()
        self._assert_rejected(
            NeoScorpionFuelTankState(
                fuel_work_state_id=negative_remaining_work.id,
                tank_code="left",
                remaining_lbs=-1,
            )
        )

        negative_actual_work = NeoScorpionFuelWorkState(
            fuel_assignment_id=assignment.id,
            tail_number="N414UP",
        )
        db.session.add(negative_actual_work)
        db.session.commit()
        self._assert_rejected(
            NeoScorpionFuelTankState(
                fuel_work_state_id=negative_actual_work.id,
                tank_code="left",
                actual_lbs=-1,
            )
        )

    def test_all_detailed_aircraft_mappings_and_layouts(self):
        expected = {
            "N123UP": ("A300", ("l_out", "l_in", "ctr", "r_in", "r_out", "tt")),
            "N456UP": ("B757", ("left", "ctr", "right")),
            "N345UP": ("B767ER", ("left", "ctr", "right")),
            "N912UP": ("B767ER", ("left", "ctr", "right")),
            "N567UP": (
                "B747-400",
                (
                    "main_l_out",
                    "main_l_in",
                    "main_r_in",
                    "main_r_out",
                    "reserve_2_l",
                    "reserve_3_r",
                    "center_wing",
                ),
            ),
            "N678UP": (
                "B747-8",
                (
                    "main_l_out",
                    "main_l_in",
                    "main_r_in",
                    "main_r_out",
                    "reserve_1_l",
                    "reserve_4_r",
                    "center_wing",
                ),
            ),
            "N234UP": ("UNCONFIGURED", ()),
        }
        expected_labels = {
            "N123UP": ("L-OUT", "L-IN", "CTR", "R-IN", "R-OUT", "TT"),
            "N456UP": ("LEFT", "CTR", "RIGHT"),
            "N345UP": ("LEFT", "CTR", "RIGHT"),
            "N912UP": ("LEFT", "CTR", "RIGHT"),
            "N567UP": (
                "MAIN-L-OUT",
                "MAIN-L-IN",
                "MAIN R IN",
                "MAIN R OUT",
                "RESERVE 2 L",
                "RESERVE 3 R",
                "CENTER WING",
            ),
            "N678UP": (
                "MAIN-L-OUT",
                "MAIN-L-IN",
                "MAIN R IN",
                "MAIN R OUT",
                "RESERVE 1 L",
                "RESERVE 4 R",
                "CENTER WING",
            ),
            "N234UP": (),
        }
        for tail_number, (aircraft_type, tank_codes) in expected.items():
            with self.subTest(tail_number=tail_number):
                self.assertEqual(
                    detailed_aircraft_type_for_tail(tail_number),
                    aircraft_type,
                )
                self.assertEqual(
                    tuple(code for code, _label in tank_layout_for_tail(tail_number)),
                    tank_codes,
                )
                self.assertEqual(
                    tuple(label for _code, label in tank_layout_for_tail(tail_number)),
                    expected_labels[tail_number],
                )

    def test_first_remaining_stamps_on_once_and_exact_noop_does_not_commit(self):
        operation, _mission, assignment = self._assignment("N412UP")

        first = save_fueler_entry(
            self.gateway,
            self.user,
            self._form(assignment, remaining_left="10.0"),
        )
        self.assertTrue(first.changed)
        self.assertEqual(first.revision, 1)
        self.assertIsNotNone(first.fuel_work_state.on_at_utc)
        original_on = first.fuel_work_state.on_at_utc
        db.session.commit()

        same = save_fueler_entry(
            self.gateway,
            self.user,
            self._form(assignment, remaining_left="10.0"),
        )
        self.assertFalse(same.changed)
        self.assertEqual(same.revision, 1)
        self.assertEqual(same.fuel_work_state.on_at_utc, original_on)
        db.session.rollback()

        changed = save_fueler_entry(
            self.gateway,
            self.user,
            self._form(assignment, remaining_left="11.0"),
        )
        self.assertTrue(changed.changed)
        self.assertEqual(changed.revision, 2)
        self.assertEqual(changed.fuel_work_state.on_at_utc, original_on)
        db.session.commit()

        self._login(self.user)
        with patch.object(db.session, "commit", wraps=db.session.commit) as commit:
            response = self.client.post(
                "/neoscorpion/fueler",
                data=self._form(assignment, remaining_left="11.0"),
            )
            self.assertEqual(response.status_code, 302)
            self.assertEqual(commit.call_count, 0)
        state = NeoScorpionSortAssetState.query.filter_by(
            sort_date_operation_id=operation.id
        ).one()
        self.assertEqual(state.revision, 2)

        cleared = save_fueler_entry(
            self.gateway,
            self.user,
            self._form(assignment, remaining_left=""),
        )
        self.assertTrue(cleared.changed)
        self.assertEqual(cleared.revision, 3)
        self.assertEqual(cleared.fuel_work_state.on_at_utc, original_on)

    def test_actual_requires_same_tank_remaining_and_unknown_rejects_tanks(self):
        _operation, _mission, assignment = self._assignment("N412UP")
        with self.assertRaisesRegex(ValueError, "Remaining before Actual"):
            save_fueler_entry(
                self.gateway,
                self.user,
                self._form(assignment, actual_left="9.0"),
            )
        self.assertEqual(NeoScorpionFuelWorkState.query.count(), 0)
        self.assertEqual(NeoScorpionFuelTankState.query.count(), 0)
        self.assertEqual(NeoScorpionSortAssetState.query.count(), 0)
        db.session.rollback()

        with self.assertRaisesRegex(ValueError, "cannot be negative"):
            save_fueler_entry(
                self.gateway,
                self.user,
                self._form(assignment, remaining_left="-1.0"),
            )
        self.assertEqual(NeoScorpionFuelWorkState.query.count(), 0)
        self.assertEqual(NeoScorpionSortAssetState.query.count(), 0)
        db.session.rollback()

        work = NeoScorpionFuelWorkState(
            fuel_assignment_id=assignment.id,
            tail_number="N412UP",
        )
        work.tank_states.append(
            NeoScorpionFuelTankState(
                tank_code="left",
                remaining_lbs=10000,
            )
        )
        db.session.add(work)
        db.session.commit()
        actual_only = save_fueler_entry(
            self.gateway,
            self.user,
            self._form(assignment, actual_left="9.0"),
        )
        self.assertTrue(actual_only.changed)
        self.assertIsNone(actual_only.fuel_work_state.on_at_utc)
        db.session.commit()

        _operation, _mission, unknown_assignment = self._assignment(
            "N212UP",
            day=date(2026, 8, 18),
            flight_number="UPS602",
        )
        with self.assertRaisesRegex(ValueError, "not configured"):
            save_fueler_entry(
                self.gateway,
                self.user,
                self._form(unknown_assignment, remaining_left="5.0"),
            )

    def test_partial_and_complete_tank_totals_update_legacy_aggregates(self):
        _operation, _mission, assignment = self._assignment("N412UP")

        save_fueler_entry(
            self.gateway,
            self.user,
            self._form(assignment, remaining_left="10.0"),
        )
        tail_state = NeoScorpionTailFuelState.query.one()
        self.assertIsNone(tail_state.fob_lbs)
        self.assertIsNone(tail_state.actual_fuel_lbs)

        save_fueler_entry(
            self.gateway,
            self.user,
            self._form(
                assignment,
                remaining_ctr="20.0",
                remaining_right="30.0",
                actual_left="9.0",
            ),
        )
        self.assertEqual(tail_state.fob_lbs, 60000)
        self.assertIsNone(tail_state.actual_fuel_lbs)

        save_fueler_entry(
            self.gateway,
            self.user,
            self._form(
                assignment,
                actual_ctr="18.0",
                actual_right="27.0",
            ),
        )
        self.assertEqual(tail_state.fob_lbs, 60000)
        self.assertEqual(tail_state.actual_fuel_lbs, 54000)

    def test_a300_center_actual_mirrors_legacy_center(self):
        _operation, _mission, assignment = self._assignment("N123UP")

        remaining = save_fueler_entry(
            self.gateway,
            self.user,
            self._form(assignment, remaining_ctr="5.5"),
        )
        self.assertIsNone(remaining.tail_fuel_state.center_fuel_lbs)

        actual = save_fueler_entry(
            self.gateway,
            self.user,
            self._form(assignment, actual_ctr="4.7"),
        )

        self.assertTrue(actual.changed)
        self.assertEqual(actual.tail_fuel_state.center_fuel_lbs, 4700)
        self.assertIsNone(actual.tail_fuel_state.fob_lbs)

    def test_tail_change_preserves_old_work_and_creates_new_tail_scope(self):
        _operation, mission, assignment = self._assignment("N412UP")
        old = save_fueler_entry(
            self.gateway,
            self.user,
            self._form(assignment, remaining_left="10.0"),
        )
        old_work_id = old.fuel_work_state.id
        db.session.commit()

        mission.assigned_tail_number = "N413UP"
        db.session.add(
            SortDateTailState(
                sort_date=mission.sort_date,
                gateway_code=mission.gateway_code,
                sort_name=mission.sort_name,
                tail_number="N413UP",
                aircraft_type="757",
                aircraft_type_source="derived",
            )
        )
        db.session.commit()

        new = save_fueler_entry(
            self.gateway,
            self.user,
            self._form(assignment, remaining_left="12.0"),
        )
        db.session.commit()

        self.assertNotEqual(new.fuel_work_state.id, old_work_id)
        self.assertEqual(
            NeoScorpionFuelWorkState.query.filter_by(
                fuel_assignment_id=assignment.id
            ).count(),
            2,
        )
        old_tank = NeoScorpionFuelTankState.query.filter_by(
            fuel_work_state_id=old_work_id,
            tank_code="left",
        ).one()
        self.assertEqual(old_tank.remaining_lbs, 10000)
        self.assertEqual(new.fuel_work_state.tail_number, "N413UP")

    def test_fueler_get_is_read_only_and_renders_phone_safe_tank_grid(self):
        _operation, _mission, assignment = self._assignment("N412UP")
        NeoScorpionSettings.query.delete()
        db.session.commit()
        self._login(self.user)

        with patch.object(db.session, "commit", wraps=db.session.commit) as commit:
            response = self.client.get("/neoscorpion/fueler")
            self.assertEqual(commit.call_count, 0)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"B757", response.data)
        self.assertIn(b"Remaining Total", response.data)
        self.assertIn(b'name="remaining_left"', response.data)
        self.assertIn(b'name="actual_right"', response.data)
        self.assertEqual(NeoScorpionFuelWorkState.query.count(), 0)
        self.assertEqual(NeoScorpionFuelTankState.query.count(), 0)
        self.assertEqual(NeoScorpionSettings.query.count(), 0)
        self.assertIsNotNone(assignment.id)

    def test_reassigned_fueler_cannot_submit_stale_assignment(self):
        operation, _mission, assignment = self._assignment("N412UP")
        other = self._add_user("replacement_fueler", "operator")
        state = NeoScorpionSortAssetState(
            sort_date_operation_id=operation.id,
            revision=4,
        )
        assignment.assigned_fueler_user_id = other.id
        db.session.add(state)
        db.session.commit()

        with self.assertRaisesRegex(ValueError, "not found for this fueler"):
            save_fueler_entry(
                self.gateway,
                self.user,
                self._form(assignment, remaining_left="10.0"),
            )

        db.session.refresh(state)
        self.assertEqual(state.revision, 4)
        self.assertEqual(NeoScorpionFuelWorkState.query.count(), 0)

    def _assignment(
        self,
        tail_number="N412UP",
        *,
        day=date(2026, 8, 17),
        flight_number="UPS601",
    ):
        operation = SortDateOperation(
            gateway_id=self.gateway.id,
            sort_date=day,
            gateway_code=self.gateway.code,
            sort_name="night",
            window_minutes=360,
        )
        db.session.add(operation)
        db.session.flush()
        mission = SortDateMission(
            sort_date=day,
            gateway_code=self.gateway.code,
            sort_name="night",
            sort_date_operation_id=operation.id,
            mission_type="departure",
            mission_source="manual",
            flight_number=flight_number,
            origin=self.gateway.code,
            destination="SDF",
            timezone="America/Chicago",
            planned_datetime_local=datetime(2026, 8, 17, 23, 30),
            planned_datetime_utc=datetime(2026, 8, 18, 4, 30),
            planned_source="manual",
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
            assigned_fueler_user_id=self.user.id,
        )
        db.session.add_all(
            [
                assignment,
                SortDateTailState(
                    sort_date=day,
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
    def _form(assignment, **tank_values):
        return {
            "assignment_id": str(assignment.id),
            "apu_lbs": "",
            "transfer_fuel_gallons": "",
            "notes": "",
            "tail_fuel_status": "pending",
            **tank_values,
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
            follow_redirects=False,
        )

    def _assert_rejected(self, model):
        db.session.add(model)
        with self.assertRaises(IntegrityError):
            db.session.commit()
        db.session.rollback()


if __name__ == "__main__":
    unittest.main()
