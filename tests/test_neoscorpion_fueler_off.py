import unittest
from datetime import date, datetime
from unittest.mock import patch

from sqlalchemy import inspect

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
from app.services.neoscorpion import mark_fueler_off, save_fueler_entry
from app.services.password_policy import set_user_password
from app.services.permission_rules import ensure_default_permission_rules
from app.services.schema_sync import (
    LOCAL_SQLITE_OPTIONAL_COLUMNS,
    POSTGRES_OPTIONAL_COLUMNS,
    sync_local_sqlite_schema,
)


class NeoScorpionFuelerOffTest(unittest.TestCase):
    def setUp(self):
        TestConfig = type(
            "TestConfig",
            (),
            {
                "SECRET_KEY": "neoscorpion-off-test",
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
        self.user = self._add_user("off_fueler", "operator")
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_schema_sync_adds_nullable_off_columns(self):
        NeoScorpionFuelTankState.__table__.drop(bind=db.engine)
        NeoScorpionFuelWorkState.__table__.drop(bind=db.engine)
        with db.engine.begin() as connection:
            connection.exec_driver_sql(
                """
                CREATE TABLE neoscorpion_fuel_work_states (
                    id INTEGER PRIMARY KEY,
                    fuel_assignment_id INTEGER NOT NULL,
                    tail_number VARCHAR(32) NOT NULL,
                    on_at_utc DATETIME,
                    apu_running BOOLEAN,
                    apu_confirmed_at_utc DATETIME,
                    apu_allowance_lbs INTEGER,
                    applied_apu_rate_thousand_lbs_per_hour NUMERIC(8, 4),
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    CONSTRAINT uq_neoscorpion_fuel_work_state_assignment_tail
                        UNIQUE (fuel_assignment_id, tail_number)
                )
                """
            )

        sync_local_sqlite_schema(self.app)
        sync_local_sqlite_schema(self.app)

        columns = {
            column["name"]
            for column in inspect(db.engine).get_columns(
                "neoscorpion_fuel_work_states"
            )
        }
        self.assertIn("off_at_utc", columns)
        self.assertIn("off_by_user_id", columns)
        self.assertEqual(
            LOCAL_SQLITE_OPTIONAL_COLUMNS["neoscorpion_fuel_work_states"][
                "off_at_utc"
            ],
            "DATETIME",
        )
        self.assertEqual(
            POSTGRES_OPTIONAL_COLUMNS["neoscorpion_fuel_work_states"][
                "off_by_user_id"
            ],
            "INTEGER",
        )

    def test_incomplete_actual_and_unconfirmed_apu_block_off(self):
        operation, _mission, assignment = self._assignment()
        partial = save_fueler_entry(
            self.gateway,
            self.user,
            self._form(
                assignment,
                apu_running="no",
                remaining_left="10.0",
                actual_left="9.0",
            ),
        )
        self.assertEqual(partial.revision, 1)
        db.session.commit()

        with self.assertRaisesRegex(ValueError, "Complete Remaining fuel before OFF"):
            mark_fueler_off(self.gateway, self.user, assignment.id)
        db.session.rollback()

        complete_unconfirmed = save_fueler_entry(
            self.gateway,
            self.user,
            self._form(
                assignment,
                apu_running="not_confirmed",
                remaining_ctr="20.0",
                actual_ctr="18.0",
                remaining_right="30.0",
                actual_right="27.0",
            ),
        )
        self.assertEqual(complete_unconfirmed.revision, 2)
        db.session.commit()

        with self.assertRaisesRegex(
            ValueError,
            "Confirm APU Running before OFF",
        ):
            mark_fueler_off(self.gateway, self.user, assignment.id)
        db.session.rollback()
        work = NeoScorpionFuelWorkState.query.filter_by(
            fuel_assignment_id=assignment.id
        ).one()
        self.assertIsNone(work.off_at_utc)
        self.assertEqual(
            NeoScorpionSortAssetState.query.filter_by(
                sort_date_operation_id=operation.id
            ).one().revision,
            2,
        )

    def test_valid_neo_fuel_requires_positive_tf_before_off_and_repeat_is_noop(self):
        operation, _mission, assignment = self._assignment()
        saved = self._save_complete(assignment)
        self.assertEqual(saved.revision, 1)
        db.session.commit()
        self.assertEqual(assignment.transfer_fuel_gallons, 1)

        assignment.transfer_fuel_gallons = None
        db.session.commit()
        with self.assertRaisesRegex(ValueError, "positive T/F"):
            mark_fueler_off(self.gateway, self.user, assignment.id)
        db.session.rollback()
        assignment.transfer_fuel_gallons = 1
        db.session.commit()

        self._login(self.user)
        with patch.object(db.session, "commit", wraps=db.session.commit) as commit:
            response = self.client.post(
                "/neoscorpion/fueler/off",
                data={"assignment_id": str(assignment.id)},
                follow_redirects=False,
            )
            self.assertEqual(commit.call_count, 1)
        self.assertEqual(response.status_code, 302)

        work = NeoScorpionFuelWorkState.query.filter_by(
            fuel_assignment_id=assignment.id
        ).one()
        original_off_at = work.off_at_utc
        self.assertIsNotNone(original_off_at)
        self.assertEqual(work.off_by_user_id, self.user.id)
        state = NeoScorpionSortAssetState.query.filter_by(
            sort_date_operation_id=operation.id
        ).one()
        self.assertEqual(state.revision, 2)

        repeated = mark_fueler_off(
            self.gateway,
            self.user,
            assignment.id,
            now_utc=datetime(2026, 8, 18, 7, 30),
        )
        self.assertFalse(repeated.changed)
        self.assertEqual(repeated.revision, 2)
        self.assertEqual(repeated.fuel_work_state.off_at_utc, original_off_at)

        rendered = self.client.get("/neoscorpion/fueler")
        self.assertEqual(rendered.status_code, 200)
        self.assertIn(f'data-fuel-assignment-id="{assignment.id}"'.encode(), rendered.data)
        self.assertIn(b"<dt>OFF</dt>", rendered.data)
        self.assertNotIn(b">MARK OFF</button>", rendered.data)

    def test_reassigned_fueler_cannot_mark_off(self):
        operation, _mission, assignment = self._assignment()
        self._save_complete(assignment)
        replacement = self._add_user("off_replacement", "operator")
        assignment.assigned_fueler_user_id = replacement.id
        db.session.commit()

        with self.assertRaisesRegex(ValueError, "not found for this fueler"):
            mark_fueler_off(self.gateway, self.user, assignment.id)
        db.session.rollback()

        work = NeoScorpionFuelWorkState.query.filter_by(
            fuel_assignment_id=assignment.id
        ).one()
        self.assertIsNone(work.off_at_utc)
        self.assertEqual(
            NeoScorpionSortAssetState.query.filter_by(
                sort_date_operation_id=operation.id
            ).one().revision,
            1,
        )

    def test_get_never_marks_off_and_ready_card_offers_action(self):
        _operation, _mission, assignment = self._assignment()
        self._save_complete(assignment)
        db.session.commit()
        self._login(self.user)

        with patch.object(db.session, "commit", wraps=db.session.commit) as commit:
            response = self.client.get("/neoscorpion/fueler")
            self.assertEqual(commit.call_count, 0)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b">MARK OFF</button>", response.data)
        work = NeoScorpionFuelWorkState.query.filter_by(
            fuel_assignment_id=assignment.id
        ).one()
        self.assertIsNone(work.off_at_utc)
        self.assertIsNone(work.off_by_user_id)

    def test_fueler_status_is_not_rendered_or_mutated(self):
        _operation, _mission, assignment = self._assignment()
        saved = self._save_complete(assignment)
        db.session.commit()
        saved.tail_fuel_state.status = "review"
        db.session.commit()

        ignored_status = self._form(
            assignment,
            apu_running="no",
            remaining_left="10.0",
            actual_left="9.0",
            remaining_ctr="20.0",
            actual_ctr="18.0",
            remaining_right="30.0",
            actual_right="27.0",
            transfer_fuel_gallons="1",
            tail_fuel_status="complete",
        )
        result = save_fueler_entry(self.gateway, self.user, ignored_status)
        self.assertFalse(result.changed)
        self.assertEqual(
            NeoScorpionTailFuelState.query.filter_by(
                sort_date_operation_id=assignment.sort_date_operation_id
            ).one().status,
            "review",
        )

        self._login(self.user)
        rendered = self.client.get("/neoscorpion/fueler")
        self.assertNotIn(b'name="tail_fuel_status"', rendered.data)
        self.assertEqual(saved.fuel_work_state.apu_running, False)
        self.assertEqual(saved.tail_fuel_state.actual_fuel_lbs, 54000)

    def _save_complete(self, assignment):
        return save_fueler_entry(
            self.gateway,
            self.user,
            self._form(
                assignment,
                apu_running="no",
                remaining_left="10.0",
                actual_left="9.0",
                remaining_ctr="20.0",
                actual_ctr="18.0",
                remaining_right="30.0",
                actual_right="27.0",
                transfer_fuel_gallons="1",
            ),
        )

    def _assignment(self, tail_number="N412UP"):
        day = date(2026, 8, 17)
        operation = SortDateOperation(
            gateway_id=self.gateway.id,
            sort_date=day,
            gateway_code=self.gateway.code,
            sort_name="night",
            window_minutes=60,
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
            flight_number="UPS801",
            origin=self.gateway.code,
            destination="SDF",
            timezone="America/Chicago",
            planned_datetime_local=datetime(2026, 8, 17, 23, 30),
            planned_datetime_utc=datetime(2026, 8, 18, 4, 30),
            planned_source="manual",
            planned_fuel_load=50000,
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
    def _form(assignment, **values):
        return {
            "assignment_id": str(assignment.id),
            "transfer_fuel_gallons": "",
            "notes": "",
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
            follow_redirects=False,
        )


if __name__ == "__main__":
    unittest.main()
