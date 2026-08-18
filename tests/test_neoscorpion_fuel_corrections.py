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
    NeoScorpionFuelAuditEntry,
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
    classify_fuel_movement,
    complete_fueled_assignment,
    correct_fuel_actuals,
    mark_fueler_off,
    reopen_fueler_off,
    save_fueler_entry,
)
from app.services.password_policy import set_user_password
from app.services.permission_rules import ensure_default_permission_rules
from app.services.schema_sync import sync_local_sqlite_schema


class NeoScorpionFuelCorrectionTest(unittest.TestCase):
    def setUp(self):
        TestConfig = type(
            "TestConfig",
            (),
            {
                "SECRET_KEY": "neoscorpion-correction-test",
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
        self.fueler = self._add_user("correction_fueler", "operator")
        self.dispatcher = self._add_user("correction_dispatcher", "simulator")
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_audit_table_bootstrap_and_dispatch_get_are_read_only(self):
        NeoScorpionFuelAuditEntry.__table__.drop(bind=db.engine)
        sync_local_sqlite_schema(self.app)
        sync_local_sqlite_schema(self.app)

        columns = {
            column["name"]
            for column in inspect(db.engine).get_columns(
                "neoscorpion_fuel_audit_entries"
            )
        }
        self.assertTrue(
            {
                "sort_date_operation_id",
                "fuel_assignment_id",
                "fuel_work_state_id",
                "action",
                "field_name",
                "old_value",
                "new_value",
                "reason",
                "changed_by_user_id",
                "created_at",
            }.issubset(columns)
        )

        _operation, _mission, assignment = self._assignment()
        self._save_tanks(assignment)
        self._login(self.dispatcher)
        audit_count = NeoScorpionFuelAuditEntry.query.count()
        with patch.object(db.session, "commit", wraps=db.session.commit) as commit:
            response = self.client.get("/neoscorpion/fuel-dispatch")
            self.assertEqual(commit.call_count, 0)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"CORRECT ACTUAL", response.data)
        self.assertEqual(NeoScorpionFuelAuditEntry.query.count(), audit_count)

    def test_reopen_requires_reason_and_audits_prior_off(self):
        operation, _mission, assignment = self._assignment()
        work = self._ready_off(assignment)
        original_off = work.off_at_utc
        original_off_by = work.off_by_user_id
        starting_revision = self._revision(operation)

        with self.assertRaisesRegex(ValueError, "reason is required"):
            reopen_fueler_off(
                self.gateway,
                self.dispatcher,
                assignment.id,
                "",
            )
        db.session.rollback()
        self.assertEqual(NeoScorpionFuelAuditEntry.query.count(), 0)
        self.assertEqual(self._revision(operation), starting_revision)

        result = reopen_fueler_off(
            self.gateway,
            self.dispatcher,
            assignment.id,
            "Fueler declared OFF too early.",
            now_utc=datetime(2026, 8, 18, 5, 15),
        )
        self.assertTrue(result.changed)
        self.assertEqual(result.revision, starting_revision + 1)
        db.session.commit()

        db.session.refresh(work)
        self.assertIsNone(work.off_at_utc)
        self.assertIsNone(work.off_by_user_id)
        audit = NeoScorpionFuelAuditEntry.query.one()
        self.assertEqual(audit.action, "reopen_off")
        self.assertEqual(audit.field_name, "off")
        self.assertEqual(audit.reason, "Fueler declared OFF too early.")
        self.assertEqual(audit.changed_by_user_id, self.dispatcher.id)
        self.assertIn(original_off.isoformat(), audit.old_value)
        self.assertIn(str(original_off_by), audit.old_value)

        repeated = reopen_fueler_off(
            self.gateway,
            self.dispatcher,
            assignment.id,
            "Repeated click.",
        )
        self.assertFalse(repeated.changed)
        self.assertEqual(repeated.revision, starting_revision + 1)
        self.assertEqual(NeoScorpionFuelAuditEntry.query.count(), 1)

    def test_reopened_work_can_be_edited_and_marked_off_again(self):
        _operation, _mission, assignment = self._assignment()
        work = self._ready_off(assignment)
        old_off = work.off_at_utc
        reopen_fueler_off(
            self.gateway,
            self.dispatcher,
            assignment.id,
            "Continue physical fueling.",
        )
        db.session.commit()

        changed = save_fueler_entry(
            self.gateway,
            self.fueler,
            self._fuel_form(assignment, actual_left="12.0"),
        )
        self.assertTrue(changed.changed)
        db.session.commit()
        new_off = datetime(2026, 8, 18, 5, 45)
        mark_fueler_off(
            self.gateway,
            self.fueler,
            assignment.id,
            now_utc=new_off,
        )
        db.session.commit()

        db.session.refresh(work)
        self.assertEqual(work.off_at_utc, new_off)
        self.assertNotEqual(work.off_at_utc, old_off)
        self.assertIn(
            old_off.isoformat(), NeoScorpionFuelAuditEntry.query.one().old_value
        )

    def test_completed_assignments_cannot_reopen_or_be_corrected(self):
        _operation, mission, assignment = self._assignment()
        self._ready_off(assignment)
        assignment.completed_at_utc = datetime(2026, 8, 18, 6, 0)
        assignment.completed_by_user_id = self.dispatcher.id
        assignment.review_status = "complete"
        mission.fuel_status = "complete"
        db.session.commit()

        with self.assertRaisesRegex(ValueError, "Completed fuel assignments"):
            reopen_fueler_off(
                self.gateway,
                self.dispatcher,
                assignment.id,
                "Invalid reopen.",
            )
        db.session.rollback()
        with self.assertRaisesRegex(ValueError, "Completed fuel assignments"):
            correct_fuel_actuals(
                self.gateway,
                self.dispatcher,
                self._correction_form(assignment, left="10.0"),
            )
        db.session.rollback()

        assignment.completed_at_utc = None
        assignment.completed_by_user_id = None
        assignment.fuel_on_board_at_utc = datetime(2026, 8, 18, 6, 5)
        db.session.commit()
        with self.assertRaisesRegex(ValueError, "Fuel On Board"):
            reopen_fueler_off(
                self.gateway,
                self.dispatcher,
                assignment.id,
                "Invalid FOB reopen.",
            )

    def test_fueler_save_is_frozen_after_off_and_ui_is_read_only(self):
        operation, _mission, assignment = self._assignment()
        self._ready_off(assignment)
        revision = self._revision(operation)

        with self.assertRaisesRegex(ValueError, "dispatcher must REOPEN OFF"):
            save_fueler_entry(
                self.gateway,
                self.fueler,
                self._fuel_form(assignment, actual_left="12.0"),
            )
        db.session.rollback()
        self.assertEqual(self._revision(operation), revision)

        self._login(self.fueler)
        response = self.client.get("/neoscorpion/fueler")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"dispatcher must REOPEN OFF", response.data)
        self.assertNotIn(b'class="neoscorpion-fueler-form"', response.data)

    def test_actual_correction_audits_each_change_once_and_preserves_off(self):
        operation, _mission, assignment = self._assignment()
        work = self._ready_off(
            assignment,
            actual_left="11.0",
            actual_ctr="11.0",
        )
        old_off = work.off_at_utc
        self.assertEqual(
            classify_fuel_movement(
                assignment,
                work,
                tank_states=list(work.tank_states),
            ),
            "moved",
        )
        starting_revision = self._revision(operation)

        result = correct_fuel_actuals(
            self.gateway,
            self.dispatcher,
            self._correction_form(
                assignment,
                left="10.0",
                ctr="10.0",
                right="10.0",
            ),
            now_utc=datetime(2026, 8, 18, 5, 20),
        )
        self.assertTrue(result.changed)
        self.assertEqual(result.revision, starting_revision + 1)
        self.assertEqual(len(result.audit_entries), 2)
        db.session.commit()

        db.session.refresh(work)
        self.assertEqual(work.off_at_utc, old_off)
        self.assertEqual(
            classify_fuel_movement(
                assignment,
                work,
                tank_states=list(work.tank_states),
            ),
            "not_moved",
        )
        audits = NeoScorpionFuelAuditEntry.query.order_by(
            NeoScorpionFuelAuditEntry.field_name
        ).all()
        self.assertEqual(
            [audit.field_name for audit in audits],
            ["actual_ctr", "actual_left"],
        )
        self.assertTrue(all(audit.reason == "Correct gauge entry." for audit in audits))
        self.assertTrue(
            all(audit.changed_by_user_id == self.dispatcher.id for audit in audits)
        )
        tail_state = NeoScorpionTailFuelState.query.filter_by(
            sort_date_operation_id=operation.id,
            tail_number="N412UP",
        ).one()
        self.assertEqual(tail_state.actual_fuel_lbs, 30000)

    def test_clearing_actual_makes_completion_incomplete_without_clearing_off(self):
        operation, _mission, assignment = self._assignment()
        work = self._ready_off(assignment)
        off_at = work.off_at_utc
        result = correct_fuel_actuals(
            self.gateway,
            self.dispatcher,
            self._correction_form(assignment, left=""),
        )
        self.assertTrue(result.changed)
        db.session.commit()

        db.session.refresh(work)
        self.assertEqual(work.off_at_utc, off_at)
        tail_state = NeoScorpionTailFuelState.query.filter_by(
            sort_date_operation_id=operation.id,
            tail_number="N412UP",
        ).one()
        self.assertIsNone(tail_state.actual_fuel_lbs)
        with self.assertRaisesRegex(ValueError, "Complete Actual fuel"):
            complete_fueled_assignment(
                self.gateway,
                self.dispatcher,
                assignment.id,
            )
        db.session.rollback()

    def test_noop_correction_creates_no_audit_or_revision(self):
        operation, _mission, assignment = self._assignment()
        self._save_tanks(assignment)
        db.session.commit()
        starting_revision = self._revision(operation)

        result = correct_fuel_actuals(
            self.gateway,
            self.dispatcher,
            self._correction_form(
                assignment,
                left="11.0",
                ctr="10.0",
                right="10.0",
            ),
        )
        self.assertFalse(result.changed)
        self.assertEqual(result.revision, starting_revision)
        self.assertEqual(NeoScorpionFuelAuditEntry.query.count(), 0)

    def test_dispatcher_routes_commit_each_meaningful_action_once(self):
        _operation, _mission, assignment = self._assignment()
        self._ready_off(assignment)
        self._login(self.dispatcher)

        with patch.object(db.session, "commit", wraps=db.session.commit) as commit:
            correction = self.client.post(
                "/neoscorpion/fuel-dispatch/correct-actual",
                data=self._correction_form(assignment, left="10.0"),
                follow_redirects=False,
            )
            self.assertEqual(commit.call_count, 1)
        self.assertEqual(correction.status_code, 302)

        with patch.object(db.session, "commit", wraps=db.session.commit) as commit:
            reopen = self.client.post(
                "/neoscorpion/fuel-dispatch/reopen-off",
                data={
                    "assignment_id": str(assignment.id),
                    "reopen_reason": "Return fueler to work.",
                },
                follow_redirects=False,
            )
            self.assertEqual(commit.call_count, 1)
        self.assertEqual(reopen.status_code, 302)

    def _assignment(self):
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
            flight_number="UPS1201",
            origin=self.gateway.code,
            destination="SDF",
            timezone="America/Chicago",
            planned_datetime_local=datetime(2026, 8, 17, 23, 30),
            planned_datetime_utc=datetime(2026, 8, 18, 4, 30),
            planned_source="manual",
            planned_fuel_load=50000,
            assigned_tail_number="N412UP",
            tail_source="manual",
            fuel_status="waiting",
            departure_status="loading",
        )
        db.session.add(mission)
        db.session.flush()
        assignment = NeoScorpionFuelAssignment(
            sort_date_operation_id=operation.id,
            sort_date_mission_id=mission.id,
            assigned_fueler_user_id=self.fueler.id,
        )
        db.session.add_all(
            [
                assignment,
                SortDateTailState(
                    sort_date=day,
                    gateway_code=self.gateway.code,
                    sort_name="night",
                    tail_number="N412UP",
                    aircraft_type="757",
                    aircraft_type_source="derived",
                ),
            ]
        )
        db.session.commit()
        return operation, mission, assignment

    def _save_tanks(
        self,
        assignment,
        *,
        actual_left="11.0",
        actual_ctr="10.0",
        actual_right="10.0",
    ):
        return save_fueler_entry(
            self.gateway,
            self.fueler,
            self._fuel_form(
                assignment,
                actual_left=actual_left,
                actual_ctr=actual_ctr,
                actual_right=actual_right,
            ),
        )

    def _ready_off(self, assignment, **actuals):
        self._save_tanks(assignment, **actuals)
        db.session.commit()
        result = mark_fueler_off(
            self.gateway,
            self.fueler,
            assignment.id,
            now_utc=datetime(2026, 8, 18, 5, 0),
        )
        db.session.commit()
        return result.fuel_work_state

    @staticmethod
    def _fuel_form(
        assignment,
        *,
        actual_left="11.0",
        actual_ctr="10.0",
        actual_right="10.0",
    ):
        return {
            "assignment_id": str(assignment.id),
            "apu_running": "no",
            "remaining_left": "10.0",
            "actual_left": actual_left,
            "remaining_ctr": "10.0",
            "actual_ctr": actual_ctr,
            "remaining_right": "10.0",
            "actual_right": actual_right,
            "transfer_fuel_gallons": "",
            "notes": "",
        }

    @staticmethod
    def _correction_form(assignment, **values):
        form = {
            "assignment_id": str(assignment.id),
            "correction_reason": "Correct gauge entry.",
        }
        form.update(
            {f"correct_actual_{tank_code}": value for tank_code, value in values.items()}
        )
        return form

    @staticmethod
    def _revision(operation):
        state = NeoScorpionSortAssetState.query.filter_by(
            sort_date_operation_id=operation.id
        ).first()
        return int(state.revision if state else 0)

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
