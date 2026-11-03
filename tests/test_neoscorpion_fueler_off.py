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
    NeoScorpionFuelTruck,
    NeoScorpionSortTruck,
    NeoScorpionSettings,
    NeoScorpionSortAssetState,
    NeoScorpionTailFuelState,
    PortalAppAccess,
    SortDateMission,
    SortDateOperation,
    SortDateTailState,
    User,
)
from tests import neoscorpion_off_workflow as off_workflow
from tests import neoscorpion_off_presentation as presentation
from app.services import neoscorpion as fuel
from app.models import NeoScorpionFuelingEvent
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.neoscorpion import mark_fueler_off, save_fueler_entry, confirm_fueler_truck_on
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
                "CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE": datetime(2026, 8, 17, 22, 0),
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
        operation,mission,assignment=self._assignment()
        dispatcher=self._add_user('off_dispatcher','simulator');db.session.commit()
        truck,stock=off_workflow.assign_truck(self,assignment,self.user,dispatcher)
        saved=save_fueler_entry(self.gateway,self.user,self._form(assignment,apu_running='not_confirmed',
            remaining_left='10',actual_left='11'),now_utc=datetime(2026,8,18,3,45))
        db.session.commit()
        work=saved.fuel_work_state
        off=mark_fueler_off(self.gateway,self.user,assignment.id,now_utc=datetime(2026,8,18,4))
        db.session.commit();self.assertTrue(off.changed)
        physical=(work.id,work.visit_number,work.physical_sequence_number,work.off_at_utc,work.off_by_user_id)
        for extra,error in (({},'Save Remaining:'),
                ({'remaining_ctr':'20','remaining_right':'20'},'Save Actual:'),
                ({'actual_ctr':'20','actual_right':'20','transfer_fuel_gallons':'150'},
                 'Confirm APU status and valid source/allowance')):
            if extra:
                save_fueler_entry(self.gateway,self.user,dict(assignment_id=assignment.id,**extra),
                    now_utc=datetime(2026,8,18,4,5));db.session.commit()
            with self.assertRaisesRegex(ValueError,error):off_workflow.submit(self,assignment,self.user)
            db.session.rollback()
            self.assertEqual((work.id,work.visit_number,work.physical_sequence_number,work.off_at_utc,work.off_by_user_id),physical)
            self.assertEqual(stock.current_gallons,1000)
            self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)
        save_fueler_entry(self.gateway,self.user,dict(assignment_id=assignment.id,apu_running='no'),
            now_utc=datetime(2026,8,18,4,10));db.session.commit()
        self.assertTrue(off_workflow.submit(self,assignment,self.user).changed)
        self.assertEqual((work.id,work.visit_number,work.physical_sequence_number,work.off_at_utc,work.off_by_user_id),physical)
        self.assertIsNone(assignment.completed_at_utc)
        before=off_workflow.revision(operation)
        self.assertFalse(mark_fueler_off(self.gateway,self.user,assignment.id,
            now_utc=datetime(2026,8,18,4,20)).changed)
        self.assertEqual(off_workflow.revision(operation),before)
        self.assertEqual(stock.current_gallons,1000)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)

    def test_physical_off_does_not_require_final_tf_and_repeat_is_noop(self):
        operation, mission, assignment = self._assignment()
        mission.planned_fuel_load=31000
        dispatcher=self._add_user('presentation_dispatcher','simulator');db.session.commit()
        truck,stock=off_workflow.assign_truck(self,assignment,self.user,dispatcher)
        work=save_fueler_entry(self.gateway,self.user,self._form(assignment,apu_running='no',
            remaining_left='10',remaining_ctr='10',remaining_right='10',actual_left='11',actual_ctr='10',actual_right='10'),
            now_utc=datetime(2026,8,18,3,45)).fuel_work_state
        db.session.commit();self.assertIsNone(assignment.transfer_fuel_gallons)
        off_at=datetime(2026,8,18,4)
        first=mark_fueler_off(self.gateway,self.user,assignment.id,now_utc=off_at);db.session.commit()
        self.assertTrue(first.changed)
        self.assertEqual((work.off_at_utc,work.off_by_user_id),(off_at,self.user.id))
        self._login(self.user)
        before=presentation.snapshot();revision=off_workflow.revision(operation)
        node=presentation.read_views(self,assignment)[0]
        presentation.confirmed_off(self,node,'23:00')
        expected=fuel.fueler_context(self.gateway,self.user,assignment_id=assignment.id,include_timing=False)['rows'][0]['edit_baseline']
        import json
        with patch.object(db.session, "commit", wraps=db.session.commit) as commit:
            response=self.client.post(f'/neoscorpion/fueler/assignments/{assignment.id}/off',
                data={'expected':json.dumps(expected)},headers={'Accept':'application/json'})
            self.assertEqual(commit.call_count, 0)
        self.assertEqual(response.status_code,200);self.assertTrue(response.json['ok'])
        self.assertEqual(presentation.snapshot(),before)
        repeated=mark_fueler_off(self.gateway,self.user,assignment.id,now_utc=datetime(2026,8,18,7,30))
        self.assertFalse(repeated.changed)
        self.assertEqual(repeated.revision,revision);self.assertEqual(presentation.snapshot(),before)
        for node in presentation.read_views(self,assignment):presentation.confirmed_off(self,node,'23:00')
        # Permitted final data remains separate from the physical clock.
        save_fueler_entry(self.gateway,self.user,dict(assignment_id=assignment.id,transfer_fuel_gallons='150'),
            now_utc=datetime(2026,8,18,4,5));db.session.commit()
        self.assertEqual((work.off_at_utc,work.off_by_user_id),(off_at,self.user.id))
        self.assertIsNone(work.fueler_submitted_at_utc);self.assertIsNone(assignment.completed_at_utc)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0);self.assertEqual(stock.current_gallons,1000)
        self.assertTrue(off_workflow.submit(self,assignment,self.user,now=datetime(2026,8,18,4,10)).changed)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0);self.assertEqual(stock.current_gallons,1000)
        fuel.complete_fueled_assignment(self.gateway,dispatcher,assignment.id,now_utc=datetime(2026,8,18,4,15));db.session.commit()
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),1);self.assertEqual(stock.current_gallons,850)
        self.assertEqual((work.off_at_utc,work.off_by_user_id),(off_at,self.user.id))

    def test_spear_auto_complete_failure_does_not_rollback_valid_off(self):
        _operation, mission, assignment = self._assignment()
        self._save_complete(assignment)
        self._physical_truck(assignment)
        settings = NeoScorpionSettings.query.filter_by(
            gateway_id=self.gateway.id
        ).one()
        settings.spear_recommendations_enabled = True
        settings.spear_automation_enabled = True
        db.session.commit()
        self._login(self.user)

        with patch(
            "app.neonodes.neoscorpion.routes.complete_fueled_assignment",
            side_effect=RuntimeError("simulated automation failure"),
        ):
            response = self.client.post(
                "/neoscorpion/fueler/off",
                data={"assignment_id": str(assignment.id)},
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 302)
        work = NeoScorpionFuelWorkState.query.filter_by(
            fuel_assignment_id=assignment.id
        ).one()
        self.assertIsNotNone(work.off_at_utc)
        self.assertIsNotNone(work.off_by_user_id)
        db.session.refresh(assignment)
        db.session.refresh(mission)
        self.assertIsNone(assignment.completed_at_utc)
        self.assertNotEqual(assignment.review_status, "complete")
        self.assertNotEqual(mission.fuel_status, "complete")

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
        operation, mission, assignment = self._assignment()
        mission.planned_fuel_load=30000
        work=save_fueler_entry(self.gateway,self.user,self._form(assignment,apu_running='no',
            remaining_left='10',remaining_ctr='10',remaining_right='10',actual_left='10',actual_ctr='10',actual_right='10')).fuel_work_state
        db.session.commit()
        self._login(self.user)
        with patch.object(db.session, "commit", wraps=db.session.commit) as commit:
            nodes=presentation.read_views(self,assignment)
            self.assertEqual(commit.call_count, 0)
        row=fuel.fueler_context(self.gateway,self.user,assignment_id=assignment.id,include_timing=False)['rows'][0]
        self.assertTrue(row['fuel_on_board_ready'])
        before=presentation.snapshot()
        for node in nodes:presentation.off_form(self,node,assignment,enabled=False,reason='No physical truck')
        form=presentation.off_form(self,nodes[0],assignment,enabled=False)
        # Disabling a button is presentation only: forging its real form still fails.
        rejected=self.client.post(form.attrs['action'],data=form.values())
        self.assertEqual(rejected.status_code,400);self.assertIn('No physical truck',rejected.json['error'])
        self.assertEqual(presentation.snapshot(),before)
        dispatcher=self._add_user('eligibility_dispatcher','simulator');db.session.commit()
        truck,stock=off_workflow.assign_truck(self,assignment,self.user,dispatcher,start=False)
        for node in presentation.read_views(self,assignment):
            form=presentation.off_form(self,node,assignment,enabled=False,reason='Confirm physical TRUCK ON')
        before=presentation.snapshot();rejected=self.client.post(form.attrs['action'],data=form.values())
        self.assertEqual(rejected.status_code,400);self.assertIn('Confirm physical TRUCK ON',rejected.json['error'])
        self.assertEqual(presentation.snapshot(),before)
        confirm_fueler_truck_on(self.gateway,self.user,assignment.id,expected_cycle=1,
            expected_tail=mission.assigned_tail_number,expected_truck_id=truck.id,
            expected_work_ref=work.id,require_work_ref=True,now_utc=datetime(2026,8,18,3,30));db.session.commit()
        for node in presentation.read_views(self,assignment):form=presentation.off_form(self,node,assignment,enabled=True)
        accepted=self.client.post(form.attrs['action'],data=form.values())
        self.assertEqual(accepted.status_code,200);self.assertTrue(accepted.json['ok'])
        self.assertIsNotNone(work.off_at_utc);self.assertEqual(work.off_by_user_id,self.user.id)
        self.assertIsNone(work.fueler_submitted_at_utc);self.assertIsNone(assignment.completed_at_utc)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0);self.assertEqual(stock.current_gallons,1000)

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

    def _physical_truck(self, assignment):
        truck=NeoScorpionFuelTruck(gateway_id=self.gateway.id,truck_number="PHYSICAL",capacity_gallons=10000)
        db.session.add(truck); db.session.flush()
        db.session.add(NeoScorpionSortTruck(sort_date_operation_id=assignment.sort_date_operation_id,
            fuel_truck_id=truck.id,status="available",starting_gallons=1000,current_gallons=1000))
        assignment.assigned_truck_id=truck.id
        db.session.commit()
        confirm_fueler_truck_on(self.gateway,self.user,assignment.id,expected_cycle=assignment.current_cycle_number,
                               expected_tail=assignment.sort_date_mission.assigned_tail_number,expected_truck_id=truck.id)
        db.session.commit()

    def _assignment(self, tail_number="N412UP"):
        day = date(2026, 8, 17)
        operation = SortDateOperation(
            generated_by_user_id=self.user.id,
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
