import unittest
import copy
from datetime import date, datetime
from unittest.mock import patch

from sqlalchemy import inspect

from app import create_app
from app.extensions import db
from app.models import (
    Gateway, MasterFlightSchedule, NeoScorpionSortFueler, NeoScorpionTruckInventoryAudit,
    GatewayMembership,
    GatewayNodeRole,
    NeoNode,
    NeoScorpionFuelAssignment,
    NeoScorpionFuelingEvent,
    NeoScorpionFuelTankState,
    NeoScorpionFuelTruck,
    NeoScorpionFuelWorkState,
    NeoScorpionSettings,
    NeoScorpionSortAssetState,
    NeoScorpionSortTruck,
    PortalAppAccess,
    SortDateMission,
    SortDateOperation,
    SortDateTailState,
    User,
)
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services import neoscorpion as fuel, neoscorpion_assets as assets
from app.services.neoscorpion import (
    complete_fuel_on_board,
    complete_fueled_assignment,
    fueler_context,
    mark_fueler_off,
    confirm_fueler_truck_on,
    submit_fueler_assignment,
    save_fueler_entry,
)
from app.services.password_policy import set_user_password
from app.services.permission_rules import ensure_default_permission_rules
from app.services.schema_sync import (
    LOCAL_SQLITE_OPTIONAL_COLUMNS,
    POSTGRES_OPTIONAL_COLUMNS,
    sync_local_sqlite_schema,
)


class NeoScorpionDispatcherCompleteTest(unittest.TestCase):
    def setUp(self):
        TestConfig = type(
            "TestConfig",
            (),
            {
                "SECRET_KEY": "neoscorpion-complete-test",
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
        self.fueler = self._add_user("complete_fueler", "operator")
        self.dispatcher = self._add_user("complete_dispatcher", "simulator")
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_schema_sync_adds_normal_completion_audit_columns(self):
        NeoScorpionFuelingEvent.__table__.drop(bind=db.engine)
        NeoScorpionFuelTankState.__table__.drop(bind=db.engine)
        NeoScorpionFuelWorkState.__table__.drop(bind=db.engine)
        NeoScorpionFuelAssignment.__table__.drop(bind=db.engine)
        with db.engine.begin() as connection:
            connection.exec_driver_sql(
                """
                CREATE TABLE neoscorpion_fuel_assignments (
                    id INTEGER PRIMARY KEY,
                    sort_date_operation_id INTEGER NOT NULL,
                    sort_date_mission_id INTEGER NOT NULL,
                    assigned_fueler_user_id INTEGER,
                    assigned_truck_id INTEGER,
                    transfer_fuel_gallons INTEGER,
                    estimated_fuel_gallons INTEGER,
                    calculation_status VARCHAR(32) NOT NULL,
                    review_status VARCHAR(32) NOT NULL,
                    load_planning_note TEXT NOT NULL,
                    fuel_on_board_at_utc DATETIME,
                    fuel_on_board_by_user_id INTEGER,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    CONSTRAINT uq_neoscorpion_fuel_assignment_mission
                        UNIQUE (sort_date_mission_id)
                )
                """
            )

        sync_local_sqlite_schema(self.app)
        sync_local_sqlite_schema(self.app)

        columns = {
            column["name"]
            for column in inspect(db.engine).get_columns(
                "neoscorpion_fuel_assignments"
            )
        }
        self.assertIn("completed_at_utc", columns)
        self.assertIn("completed_by_user_id", columns)
        self.assertEqual(
            LOCAL_SQLITE_OPTIONAL_COLUMNS["neoscorpion_fuel_assignments"][
                "completed_at_utc"
            ],
            "DATETIME",
        )
        self.assertEqual(
            POSTGRES_OPTIONAL_COLUMNS["neoscorpion_fuel_assignments"][
                "completed_by_user_id"
            ],
            "INTEGER",
        )

    def test_off_and_neo_fuel_are_required(self):
        operation = self._operation()
        _mission, assignment = self._assignment(operation)
        self._save_tanks(assignment, movement="not_moved")
        db.session.commit()

        with self.assertRaisesRegex(ValueError, "MARK OFF"):
            complete_fueled_assignment(
                self.gateway,
                self.dispatcher,
                assignment.id,
            )
        db.session.rollback()

        work = NeoScorpionFuelWorkState.query.filter_by(
            fuel_assignment_id=assignment.id
        ).one()
        left = NeoScorpionFuelTankState.query.filter_by(
            fuel_work_state_id=work.id,
            tank_code="left",
        ).one()
        left.actual_lbs = None
        work.off_at_utc = datetime(2026, 8, 18, 5, 0)
        db.session.commit()
        with self.assertRaisesRegex(ValueError, "Complete Actual fuel"):
            complete_fueled_assignment(
                self.gateway,
                self.dispatcher,
                assignment.id,
            )
        db.session.rollback()
        self._assert_not_completed(operation, assignment, expected_revision=1)

    def test_unknown_movement_blocks_completion(self):
        operation=self._operation();mission,assignment=self._assignment(operation)
        work,truck,selection=self._physical(assignment,movement='unknown',transfer=None,submit=False)
        self.assertEqual(fuel.classify_fuel_movement(assignment,work,tank_states=work.tank_states),'unknown')
        revision=self._revision(operation)
        with self.assertRaisesRegex(ValueError,'Actual'):
            self._submit(assignment)
        db.session.rollback()
        with self.assertRaisesRegex(ValueError,'Complete Actual fuel'):
            complete_fueled_assignment(self.gateway,self.dispatcher,assignment.id)
        db.session.rollback()
        self._assert_not_completed(operation,assignment,expected_revision=revision)
        self.assertIsNone(work.fueler_submitted_at_utc)
        self.assertEqual(selection.current_gallons,400)

    def test_moved_completion_validates_truck_tf_and_gallons(self):
        operation=self._operation();mission,assignment=self._assignment(operation)
        # Data entry is not physical authority: no truck/ON means no physical OFF.
        self._save_tanks(assignment,movement='moved',transfer_gallons=10);db.session.commit()
        revision=self._revision(operation)
        with self.assertRaisesRegex(ValueError,'Assign the truck'):
            mark_fueler_off(self.gateway,self.fueler,assignment.id)
        db.session.rollback()
        self._assert_not_completed(operation,assignment,expected_revision=revision)

        work,truck,selection=self._physical(assignment,transfer=None,submit=False)
        for gallons in (None,0):
            with self.subTest(gallons=gallons):
                self._data(assignment,transfer=gallons);db.session.commit()
                revision=self._revision(operation)
                with self.assertRaisesRegex(ValueError,'Positive T/F'):
                    self._submit(assignment)
                db.session.rollback()
                with self.assertRaisesRegex(ValueError,'Fueler COMPLETE ASSIGNMENT'):
                    complete_fueled_assignment(self.gateway,self.dispatcher,assignment.id)
                db.session.rollback()
                self._assert_not_completed(operation,assignment,expected_revision=revision)

        self._data(assignment,transfer=10);db.session.commit();self._submit(assignment)
        # Canonical roster removal after OFF must not fabricate a source event.
        assets.remove_nightly_truck(operation,truck);db.session.commit()
        revision=self._revision(operation)
        with self.assertRaisesRegex(ValueError,"missing from tonight's assets"):
            complete_fueled_assignment(self.gateway,self.dispatcher,assignment.id)
        db.session.rollback()
        self._assert_not_completed(operation,assignment,expected_revision=revision)
        self.assertEqual(work.physical_transfer_gallons,10)

        # Legacy/unreliable balance injection changes ONLY inventory, never the
        # physical facts or submission. Unknown/insufficient balances are audited,
        # not fabricated and not used to deny truthful aircraft completion.
        for index,balance in enumerate((None,5)):
            with self.subTest(legacy_balance=balance):
                mission,assignment=self._assignment(operation,flight_number=f'UPS92{index}',tail_number=f'N41{index+3}UP')
                work,truck,selection=self._physical(assignment,transfer=10,truck_number=f'BAL-{index}')
                if balance is None:
                    assets.mark_nightly_truck_topping_off(operation,truck,changed_by_user=self.dispatcher)
                    db.session.commit()
                selection.current_gallons=balance;db.session.commit()
                completed=complete_fueled_assignment(self.gateway,self.dispatcher,assignment.id);db.session.commit()
                self.assertTrue(completed.changed)
                self.assertEqual(selection.current_gallons,balance)
                self.assertFalse(selection.inventory_verified)
                audit=NeoScorpionTruckInventoryAudit.query.filter_by(fueling_event_id=completed.fueling_event.id).one()
                self.assertEqual(audit.transfer_fuel_gallons,10)
                self.assertFalse(complete_fueled_assignment(self.gateway,self.dispatcher,assignment.id).changed)

    def test_moved_success_creates_event_deducts_once_and_preserves_master(self):
        operation=self._operation();mission,assignment=self._assignment(operation)
        master=MasterFlightSchedule(gateway_id=self.gateway.id,gateway_code=self.gateway.code,sort_name='night',
            mission_type='departure',flight_number=mission.flight_number,origin=mission.origin,destination=mission.destination,
            active_days='monday',planned_time_local=mission.planned_datetime_local.time())
        db.session.add(master);db.session.commit()
        work,truck,selection=self._physical(assignment,transfer=75,legacy_gallons=975)
        master_before=fuel._fuel_rollback_values(master)
        identity={k:getattr(mission,k) for k in ('id','sort_date_operation_id','flight_number','assigned_tail_number','origin','destination','planned_datetime_utc')}
        revision=self._revision(operation)
        result=complete_fueled_assignment(self.gateway,self.dispatcher,assignment.id,now_utc=datetime(2026,8,18,5,20));db.session.commit()
        self.assertTrue(result.changed);self.assertEqual(result.movement_status,'moved')
        self.assertEqual(result.revision,revision+1)
        event=NeoScorpionFuelingEvent.query.one()
        self.assertEqual((event.sort_date_operation_id,event.fuel_assignment_id,event.fuel_work_state_id,event.tail_number,event.fuel_truck_id),
                         (operation.id,assignment.id,work.id,'N412UP',truck.id))
        self.assertEqual((event.sequence_number,event.transfer_fuel_gallons),(1,75))
        self.assertEqual((event.started_at_utc,event.ended_at_utc),(work.manual_on_at_utc,work.off_at_utc))
        self.assertEqual((selection.starting_gallons,selection.current_gallons,truck.remaining_fuel_gallons),(500,325,975))
        self.assertEqual(assignment.completed_by_user_id,self.dispatcher.id)
        self.assertEqual(assignment.completed_at_utc,datetime(2026,8,18,5,20))
        self.assertEqual((assignment.review_status,mission.fuel_status),('complete','complete'))
        self.assertEqual(fuel._fuel_rollback_values(master),master_before)
        self.assertEqual({k:getattr(mission,k) for k in identity},identity)
        frozen=self._event_facts(event)
        repeat=complete_fueled_assignment(self.gateway,self.dispatcher,assignment.id);db.session.commit()
        self.assertFalse(repeat.changed);self.assertEqual(repeat.revision,result.revision)
        self.assertEqual(self._event_facts(event),frozen)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)
        self.assertEqual(selection.current_gallons,325)
        with self.assertRaisesRegex(ValueError,'Completed fuel assignments'):
            self._data(assignment,transfer=80)
        db.session.rollback()
        self.assertEqual(self._event_facts(event),frozen)

    def test_not_moved_completes_without_event_or_truck_deduction(self):
        operation=self._operation();mission,assignment=self._assignment(operation)
        truck,selection=self._assign_truck(operation,assignment,starting_gallons=300,current_gallons=250)
        fuel.unassign_assignment_truck(self.gateway,self.dispatcher,assignment.id,
            expected_cycle=1,expected_tail=mission.assigned_tail_number,expected_truck_id=truck.id)
        db.session.commit()
        work=self._fob_data(assignment)
        self.assertIsNone(work.manual_on_at_utc);self.assertIsNone(work.off_at_utc)
        with self.assertRaisesRegex(ValueError,'MARK OFF'):
            complete_fueled_assignment(self.gateway,self.dispatcher,assignment.id)
        db.session.rollback()
        result=complete_fuel_on_board(self.gateway,self.dispatcher,assignment.id);db.session.commit()
        self.assertTrue(result.changed)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)
        self.assertEqual((selection.current_gallons,truck.remaining_fuel_gallons),(250,900))
        self.assertEqual((assignment.review_status,mission.fuel_status),('complete','complete'))
        self.assertIsNone(work.manual_on_at_utc);self.assertIsNone(work.off_at_utc)
        self.assertEqual(assignment.transfer_fuel_gallons,0) # Explicit no-transfer FOB projection.
        # A real truck visit that confirmed no transfer is also retained as
        # physical history, without creating a fictitious transfer event.
        mission,other=self._assignment(operation,flight_number='UPS902',tail_number='N413UP')
        fuel.autosave_dispatch_field(self.gateway,mission.id,'required_fuel','30',expected_value='50',user=self.dispatcher)
        db.session.commit()
        physical,unused_truck,unused_selection=self._physical(other,movement='not_moved',transfer=0,truck_number='CHECK-ONLY')
        result=complete_fueled_assignment(self.gateway,self.dispatcher,other.id);db.session.commit()
        self.assertEqual(result.movement_status,'not_moved')
        self.assertIsNone(result.fueling_event)
        self.assertIsNotNone(physical.manual_on_at_utc);self.assertIsNotNone(physical.off_at_utc)
        self.assertEqual(unused_selection.current_gallons,400)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)

    def test_topping_off_and_oos_nightly_trucks_may_complete_moved_work(self):
        operation=self._operation()
        for index,status in enumerate(('topping_off','unavailable_oos'),1):
            with self.subTest(status=status):
                mission,assignment=self._assignment(operation,flight_number=f'UPS90{index}',tail_number=f'N41{index+2}UP')
                work,truck,selection=self._physical(assignment,transfer=10,truck_number=f'STATUS-{index}',
                    starting_gallons=100,current_gallons=80)
                # They were available during real ON/OFF. Post-OFF asset changes do
                # not erase physical facts or hold this already released visit.
                if status=='topping_off':
                    assets.mark_nightly_truck_topping_off(operation,truck,changed_by_user=self.dispatcher,
                        now_utc=datetime(2026,8,18,5,10))
                else:
                    assets.update_nightly_truck(operation,truck,status=status,changed_by_user=self.dispatcher,
                        now_utc=datetime(2026,8,18,5,10))
                db.session.commit()
                self.assertEqual(assignment.operational_status,'active')
                result=complete_fueled_assignment(self.gateway,self.dispatcher,assignment.id);db.session.commit()
                self.assertEqual(result.movement_status,'moved')
                self.assertEqual((selection.status,selection.current_gallons),(status,70))
                self.assertEqual(result.fueling_event.fuel_work_state_id,work.id)

    def test_existing_event_and_fuel_on_board_block_normal_complete(self):
        operation=self._operation();mission,assignment=self._assignment(operation)
        work,truck,selection=self._physical(assignment)
        result=complete_fueled_assignment(self.gateway,self.dispatcher,assignment.id);db.session.commit()
        event=result.fueling_event;frozen=self._event_facts(event)
        for _ in range(2):
            self.assertFalse(complete_fueled_assignment(self.gateway,self.dispatcher,assignment.id).changed)
            db.session.commit()
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)
        self.assertEqual(self._event_facts(event),frozen)
        self.assertEqual(selection.current_gallons,325)
        mission,other=self._assignment(operation,flight_number='UPS902',tail_number='N413UP')
        self._fob_data(other)
        complete_fuel_on_board(self.gateway,self.dispatcher,other.id);db.session.commit()
        with self.assertRaisesRegex(ValueError,'do not use normal COMPLETE'):
            complete_fueled_assignment(self.gateway,self.dispatcher,other.id)
        db.session.rollback()
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)
        self.assertEqual(self._event_facts(event),frozen)

    def test_stale_cross_sort_attempt_and_route_permission_do_not_complete(self):
        operation=self._operation();mission,assignment=self._assignment(operation)
        work,truck,selection=self._physical(assignment)
        revision=self._revision(operation)
        self._operation(day=date(2026,8,18))
        self.app.config['CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE']=datetime(2026,8,18,22)
        with self.assertRaisesRegex(ValueError,'current sort operation'):
            complete_fueled_assignment(self.gateway,self.dispatcher,assignment.id)
        db.session.rollback()
        self._assert_not_completed(operation,assignment,expected_revision=revision)
        self._login(self.fueler)
        self.assertEqual(self.client.post('/neoscorpion/fuel-dispatch/complete',data={'assignment_id':assignment.id}).status_code,403)
        self.app.config['CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE']=datetime(2026,8,17,22)
        self._login(self.dispatcher)
        self.app.config['CSRF_PROTECT_TESTING']=True
        headers={'Accept':'application/json','X-Requested-With':'XMLHttpRequest'}
        form={'assignment_id':assignment.id,'expected_work_ref':work.id}
        self.assertEqual(self.client.post('/neoscorpion/fuel-dispatch/complete',data=form,headers=headers).status_code,400)
        import re
        token=re.search(r'<meta name="csrf-token" content="([^"]+)"',self.client.get('/neoscorpion/fuel-dispatch').get_data(as_text=True)).group(1)
        headers['X-CSRFToken']=token
        stale=self.client.post('/neoscorpion/fuel-dispatch/complete',data={**form,'expected_work_ref':work.id+999},headers=headers)
        self.assertEqual(stale.status_code,409,stale.get_json())
        self._assert_not_completed(operation,assignment,expected_revision=revision)
        self.assertEqual(selection.current_gallons,400)
        foreign=Gateway(code='XYZ',name='Foreign test gateway');db.session.add(foreign);db.session.commit()
        with self.assertRaises(ValueError):
            complete_fueled_assignment(foreign,self.dispatcher,assignment.id)
        db.session.rollback()
        self.assertIsNone(assignment.completed_at_utc)

    def test_active_fueler_filter_and_read_only_dispatch_render(self):
        operation=self._operation();mission,completed=self._assignment(operation)
        work,truck,selection=self._physical(completed)
        complete_fueled_assignment(self.gateway,self.dispatcher,completed.id);db.session.commit()
        mission,off_only=self._assignment(operation,flight_number='UPS902',tail_number='N413UP')
        other_work,other_truck,other_selection=self._physical(off_only,submit=False,truck_number='OFF-ONLY')
        active={r['assignment'].id for r in fueler_context(self.gateway,self.fueler)['rows']}
        self.assertNotIn(completed.id,active);self.assertIn(off_only.id,active)
        before=(self._event_facts(NeoScorpionFuelingEvent.query.one()),fuel._fuel_rollback_values(other_work),
                selection.current_gallons,other_selection.current_gallons,self._revision(operation))
        self._login(self.dispatcher)
        with patch.object(db.session,'commit',wraps=db.session.commit) as commit:
            response=self.client.get('/neoscorpion/fuel-dispatch')
            self.assertEqual(commit.call_count,0)
        self.assertEqual(response.status_code,200)
        self.assertIn(b'neoscorpion-table-wrap--sticky',response.data)
        self.assertIn(b'OFF',response.data.upper());self.assertIn(b'COMPLETE',response.data.upper())
        self.assertNotIn(b'<option value="complete"',response.data)
        self.assertEqual((self._event_facts(NeoScorpionFuelingEvent.query.one()),fuel._fuel_rollback_values(other_work),
                selection.current_gallons,other_selection.current_gallons,self._revision(operation)),before)
        with patch('app.neonodes.neoscorpion.routes.permission_access',return_value={'can_view':True,'can_edit':False}):
            html=self.client.get('/neoscorpion/fuel-dispatch').get_data(as_text=True)
            self.assertNotIn('data-dispatch-verification-form',html)
            self.assertNotIn('data-dispatch-assignment-submit',html)

    def test_complete_route_commits_once(self):
        operation = self._operation()
        _mission, assignment = self._assignment(operation)
        self._ready_work(
            assignment,
            movement="moved",
            transfer_gallons=75,
            complete_handoff=True,
        )
        self._login(self.dispatcher)

        with patch.object(db.session, "commit", wraps=db.session.commit) as commit:
            response = self.client.post(
                "/neoscorpion/fuel-dispatch/complete",
                data={"assignment_id": str(assignment.id)},
                follow_redirects=False,
            )
            self.assertEqual(commit.call_count, 1)

        self.assertEqual(response.status_code, 302)
        db.session.refresh(assignment)
        self.assertIsNotNone(assignment.completed_at_utc)

    def test_r03d_completion_failure_rolls_back_and_preserves_handoff(self):
        operation=self._operation();mission,assignment=self._assignment(operation)
        work,truck,selection=self._physical(assignment)
        revision=self._revision(operation)
        receipt=copy.deepcopy(work.fueler_submission_evidence)
        close=fuel._close_current_truck_segment
        def fail_after_posting(*args,**kwargs):
            close(*args,**kwargs)
            self.assertEqual(selection.current_gallons,325)
            raise RuntimeError('Injected failure after event and inventory posting')
        with patch.object(fuel,'_close_current_truck_segment',side_effect=fail_after_posting):
            with self.assertRaisesRegex(RuntimeError,'Injected failure'):
                complete_fueled_assignment(self.gateway,self.dispatcher,assignment.id)
        db.session.rollback();db.session.expire_all()
        self._assert_not_completed(operation,assignment,expected_revision=revision)
        self.assertEqual(selection.current_gallons,400)
        self.assertIsNone(work.physical_finalized_at_utc)
        self.assertEqual(work.fueler_submission_evidence,receipt)
        result=complete_fueled_assignment(self.gateway,self.dispatcher,assignment.id);db.session.commit()
        self.assertTrue(result.changed)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)
        self.assertEqual(selection.current_gallons,325)

    def _operation(self, *, day=date(2026, 8, 17)):
        operation = SortDateOperation(
            generated_by_user_id=self.dispatcher.id,
            gateway_id=self.gateway.id,
            sort_date=day,
            gateway_code=self.gateway.code,
            sort_name="night",
            window_minutes=60,
        )
        db.session.add(operation)
        db.session.commit()
        return operation

    def _revision(self,operation):
        state=NeoScorpionSortAssetState.query.filter_by(sort_date_operation_id=operation.id).first()
        return int(state.revision if state else 0)

    def _data(self,assignment,*,transfer=75,movement='moved'):
        actual='' if movement=='unknown' else '11' if movement=='moved' else '10'
        return save_fueler_entry(self.gateway,self.fueler,dict(assignment_id=assignment.id,
            apu_running='no',remaining_left='10',remaining_ctr='10',remaining_right='10',
            actual_left=actual,actual_ctr='' if movement=='unknown' else '10',
            actual_right='' if movement=='unknown' else '10',
            transfer_fuel_gallons='' if transfer is None else str(transfer)),
            now_utc=datetime(2026,8,18,4,30))

    def _submit(self,assignment):
        row=fueler_context(self.gateway,self.fueler,assignment_id=assignment.id)['rows'][0]
        result=submit_fueler_assignment(self.gateway,self.fueler,assignment.id,
            expected=row['edit_baseline'],now_utc=datetime(2026,8,18,5,1))
        db.session.commit()
        return result

    def _physical(self,assignment,*,transfer=75,movement='moved',submit=True,**truck_options):
        operation=assignment.sort_date_operation
        truck,selection=self._assign_truck(operation,assignment,associate=False,**truck_options)
        if not NeoScorpionSortFueler.query.filter_by(sort_date_operation_id=operation.id,user_id=self.fueler.id).first():
            db.session.add(NeoScorpionSortFueler(sort_date_operation_id=operation.id,user_id=self.fueler.id))
            db.session.commit()
        fuel.save_dispatch_assignment(self.gateway,dict(mission_id=assignment.sort_date_mission_id,
            expected_assigned_fueler_user_id=self.fueler.id,expected_assigned_truck_id='',
            assigned_fueler_user_id=self.fueler.id,assigned_truck_id=truck.id))
        db.session.commit()
        confirm_fueler_truck_on(self.gateway,self.fueler,assignment.id,expected_cycle=1,
            expected_tail=assignment.sort_date_mission.assigned_tail_number,expected_truck_id=truck.id,
            now_utc=datetime(2026,8,18,4))
        db.session.commit()
        result=self._data(assignment,transfer=transfer,movement=movement);db.session.commit()
        mark_fueler_off(self.gateway,self.fueler,assignment.id,now_utc=datetime(2026,8,18,5))
        db.session.commit()
        if submit:self._submit(assignment)
        return result.fuel_work_state,truck,selection

    def _fob_data(self,assignment):
        fuel.autosave_dispatch_field(self.gateway,assignment.sort_date_mission_id,'required_fuel','30',
            expected_value='50',user=self.dispatcher)
        db.session.commit()
        work=self._data(assignment,transfer=None,movement='not_moved').fuel_work_state
        db.session.commit()
        return work

    def _event_facts(self,event):
        return copy.deepcopy((fuel._fuel_rollback_values(event),
            [fuel._fuel_rollback_values(t) for t in event.tank_snapshots]))

    def _assignment(
        self,
        operation,
        *,
        flight_number="UPS901",
        tail_number="N412UP",
    ):
        mission = SortDateMission(
            sort_date=operation.sort_date,
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
            assigned_fueler_user_id=self.fueler.id,
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
        return mission, assignment

    def _save_tanks(self, assignment, *, movement, transfer_gallons=None):
        actual_left = "11.0" if movement == "moved" else "10.0"
        return save_fueler_entry(
            self.gateway,
            self.fueler,
            {
                "assignment_id": str(assignment.id),
                "apu_running": "no",
                "remaining_left": "10.0",
                "actual_left": actual_left,
                "remaining_ctr": "10.0",
                "actual_ctr": "10.0",
                "remaining_right": "10.0",
                "actual_right": "10.0",
                "transfer_fuel_gallons": (
                    "" if transfer_gallons is None else str(transfer_gallons)
                ),
                "notes": "",
            },
        )

    def _ready_work(self, assignment, *, movement, transfer_gallons, complete_handoff=False):
        off_transfer_gallons = (
            transfer_gallons
            if transfer_gallons is not None and transfer_gallons > 0
            else 1
        )
        if complete_handoff:
            self._assign_truck(assignment.sort_date_operation, assignment)
            confirm_fueler_truck_on(self.gateway, self.fueler, assignment.id,
                expected_cycle=1, expected_tail=assignment.sort_date_mission.assigned_tail_number,
                expected_truck_id=assignment.assigned_truck_id,
                now_utc=datetime(2026, 8, 18, 4, 0))
            db.session.commit()
        self._save_tanks(
            assignment,
            movement=movement,
            transfer_gallons=off_transfer_gallons,
        )
        db.session.commit()
        result = mark_fueler_off(
            self.gateway,
            self.fueler,
            assignment.id,
            now_utc=datetime(2026, 8, 18, 5, 0),
        )
        db.session.commit()
        if complete_handoff:
            row = fueler_context(self.gateway, self.fueler, assignment_id=assignment.id)['rows'][0]
            submit_fueler_assignment(self.gateway, self.fueler, assignment.id,
                expected=row['edit_baseline'], now_utc=datetime(2026, 8, 18, 5, 1))
            db.session.commit()
        if transfer_gallons != off_transfer_gallons:
            assignment.transfer_fuel_gallons = transfer_gallons
            db.session.commit()
        return result.fuel_work_state

    def _assign_truck(
        self,
        operation,
        assignment,
        *,
        status="available",
        truck_number="COMPLETE-1",
        starting_gallons=500,
        current_gallons=400,
        legacy_gallons=900,
        associate=True,
    ):
        truck = NeoScorpionFuelTruck(
            gateway_id=self.gateway.id,
            truck_number=truck_number,
            capacity_gallons=1000,
            remaining_fuel_gallons=legacy_gallons,
        )
        db.session.add(truck)
        db.session.flush()
        nightly_truck = NeoScorpionSortTruck(
            sort_date_operation_id=operation.id,
            fuel_truck_id=truck.id,
            status=status,
            starting_gallons=starting_gallons,
            current_gallons=current_gallons,
        )
        if associate:
            assignment.assigned_truck_id = truck.id
        db.session.add(nightly_truck)
        db.session.commit()
        return truck, nightly_truck

    def _assert_not_completed(self, operation, assignment, *, expected_revision):
        db.session.refresh(assignment)
        self.assertIsNone(assignment.completed_at_utc)
        self.assertIsNone(assignment.completed_by_user_id)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(), 0)
        state = NeoScorpionSortAssetState.query.filter_by(
            sort_date_operation_id=operation.id
        ).first()
        self.assertEqual(int(state.revision if state else 0), expected_revision)

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
