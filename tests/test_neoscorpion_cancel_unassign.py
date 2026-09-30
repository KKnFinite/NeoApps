import unittest
from datetime import datetime

from tests import test_neoscorpion_uplift_defuel as fixture
from app.extensions import db
from app.models import (
    NeoScorpionFuelAuditEntry, NeoScorpionFuelCycleHistory, NeoScorpionFuelingEvent,
    NeoScorpionFuelWorkState, NeoScorpionTailFuelState, NeoScorpionFuelTankState,
)
from app.services import neoscorpion as service


class CancelUnassignTest(unittest.TestCase):
    setUp = fixture.NeoScorpionUpliftDefuelTest.setUp
    tearDown = fixture.NeoScorpionUpliftDefuelTest.tearDown
    _add_user = fixture.NeoScorpionUpliftDefuelTest._add_user
    _operation = fixture.NeoScorpionUpliftDefuelTest._operation
    _assignment = fixture.NeoScorpionUpliftDefuelTest._assignment
    _truck = fixture.NeoScorpionUpliftDefuelTest._truck
    _save_cycle = fixture.NeoScorpionUpliftDefuelTest._save_cycle
    _complete_cycle = fixture.NeoScorpionUpliftDefuelTest._complete_cycle
    _reassign_cycle = fixture.NeoScorpionUpliftDefuelTest._reassign_cycle
    _revision = fixture.NeoScorpionUpliftDefuelTest._revision
    _login = fixture.NeoScorpionUpliftDefuelTest._login

    def _completed(self):
        operation = self._operation()
        mission, assignment = self._assignment(operation)
        truck, nightly = self._truck(operation, assignment, 'CANCEL-1', 500)
        work = self._complete_cycle(operation, assignment, remaining=(10,10,10), actual=(11,10,10), transfer=50)
        assignment.ready_for_fuel_at_utc = datetime(2026,8,18,4,30)
        assignment.ready_for_fuel_by_user_id = self.dispatcher.id
        assignment.load_planning_note = 'prior note'
        assignment.estimated_fuel_gallons = 123
        mission.planned_fuel_updated_at = datetime(2026,8,18,4,0)
        db.session.commit()
        return operation, mission, assignment, work, truck, nightly

    def _snapshot(self, assignment, mission, work):
        tail = NeoScorpionTailFuelState.query.filter_by(tail_number=work.tail_number).one()
        return {
            'assignment': service._fuel_rollback_values(assignment),
            'mission': service._fuel_rollback_values(mission, service._MISSION_FUEL_FIELDS),
            'work': service._fuel_rollback_values(work),
            'tanks': [service._fuel_rollback_values(t) for t in sorted(work.tank_states, key=lambda t:t.id)],
            'tail': service._fuel_rollback_values(tail),
        }

    def _start(self, assignment):
        result = service.start_follow_up_fuel_cycle(self.gateway, self.dispatcher, assignment.id,
            'uplift', '55', '', '', expected_cycle=assignment.current_cycle_number,
            expected_tail=assignment.confirmed_tail_number, now_utc=datetime(2026,8,18,6,0))
        db.session.commit()
        return result

    def _cancel(self, assignment, **overrides):
        return service.cancel_uplift(self.gateway, self.dispatcher, assignment.id,
            **{'expected_cycle':assignment.current_cycle_number, 'expected_tail':assignment.confirmed_tail_number, **overrides})

    def _unassign(self, assignment, **overrides):
        return service.unassign_assignment_truck(self.gateway, self.dispatcher, assignment.id,
            **{'expected_truck_id':assignment.assigned_truck_id, 'expected_cycle':assignment.current_cycle_number,
               'expected_tail':assignment.confirmed_tail_number, **overrides})

    def test_cancel_restores_exact_state_and_preserves_events_accounting_history(self):
        operation, mission, assignment, work, truck, nightly = self._completed()
        before = self._snapshot(assignment, mission, work)
        event = NeoScorpionFuelingEvent.query.one()
        event_before = service._fuel_rollback_values(event)
        tank_snapshots = [service._fuel_rollback_values(t) for t in event.tank_snapshots]
        self._start(assignment)
        history = NeoScorpionFuelCycleHistory.query.one()
        display = {k:v for k,v in history.snapshot.items() if not k.startswith('_')}
        revision = self._revision(operation)
        result = self._cancel(assignment)
        db.session.commit()
        self.assertTrue(result.changed)
        self.assertEqual(result.revision, revision+1)
        self.assertEqual(self._snapshot(assignment,mission,work),before)
        self.assertEqual(service._fuel_rollback_values(event),event_before)
        self.assertEqual([service._fuel_rollback_values(t) for t in event.tank_snapshots],tank_snapshots)
        self.assertEqual(nightly.current_gallons,450)
        self.assertEqual({k:v for k,v in history.snapshot.items() if not k.startswith('_')},display)
        audit = NeoScorpionFuelAuditEntry.query.one()
        self.assertEqual(audit.action,'cancel_uplift')
        self.assertEqual(audit.reason,'Dispatcher cancelled uplift before fuel movement.')
        self.assertEqual(audit.changed_by_user_id,self.dispatcher.id)
        self.assertEqual(service.fuel_dispatch_context(self.gateway)['rows'][0]['history'],[])
        # Restart gets a fresh cycle identity; old cancel requests cannot cancel it.
        self._start(assignment)
        self.assertEqual(assignment.current_cycle_number,3)
        self.assertEqual(NeoScorpionFuelCycleHistory.query.count(),1)
        with self.assertRaisesRegex(ValueError,'cycle changed'):
            self._cancel(assignment,expected_cycle=2)
        db.session.rollback()
        self._cancel(assignment); db.session.commit()
        self.assertEqual(assignment.current_cycle_number,1)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)

    def test_on_apu_remaining_setup_and_apu_burn_do_not_block_cancellation(self):
        operation, mission, assignment, work, truck, nightly = self._completed()
        before = self._snapshot(assignment,mission,work)
        self._start(assignment)
        self._reassign_cycle(assignment,truck.id)
        work.on_at_utc = datetime(2026,8,18,6,10)
        work.apu_running = True; work.apu_allowance_lbs = 100
        work.automatic_apu_allowance_lbs = 100; work.apu_confirmed_at_utc = work.on_at_utc
        work.apu_source_tank_code = 'left'
        db.session.commit()
        self._cancel(assignment); db.session.commit()
        self.assertEqual(self._snapshot(assignment,mission,work),before)
        # Explicit APU burn readings in a second cancellation attempt.
        self._start(assignment); self._reassign_cycle(assignment,truck.id)
        work.on_at_utc=datetime(2026,8,18,6,10); work.apu_running=True; work.apu_allowance_lbs=100
        for t in work.tank_states: t.actual_lbs=t.remaining_lbs-(100 if t.tank_code=='left' else 0)
        db.session.commit(); self._cancel(assignment); db.session.commit()
        self.assertEqual(self._snapshot(assignment,mission,work),before)

    def test_movement_ambiguous_identity_and_legacy_rollback_fail_without_writes(self):
        operation, mission, assignment, work, truck, nightly = self._completed()
        self._start(assignment)
        self._reassign_cycle(assignment,truck.id)
        for values, message in [({'expected_cycle':1},'cycle changed'),({'expected_tail':'N999UP'},'tail changed')]:
            with self.assertRaisesRegex(ValueError,message): self._cancel(assignment,**values)
            db.session.rollback()
        work.apu_running=False; work.apu_allowance_lbs=0
        work.tank_states[0].actual_lbs=12000; db.session.commit()
        revision=self._revision(operation)
        before=self._snapshot(assignment,mission,work)
        with self.assertRaisesRegex(ValueError,'REVIEW REQUIRED'): self._cancel(assignment)
        db.session.rollback()
        self.assertEqual(self._snapshot(assignment,mission,work),before)
        for tank in work.tank_states: tank.actual_lbs=tank.remaining_lbs+1000
        db.session.commit()
        with self.assertRaisesRegex(ValueError,'Fuel movement occurred'): self._cancel(assignment)
        db.session.rollback()
        assignment.transfer_fuel_gallons=10
        for tank in work.tank_states: tank.actual_lbs=None
        db.session.commit()
        with self.assertRaisesRegex(ValueError,'Fuel movement occurred'): self._cancel(assignment)
        db.session.rollback()
        assignment.transfer_fuel_gallons=None
        history=NeoScorpionFuelCycleHistory.query.one()
        history.snapshot={k:v for k,v in history.snapshot.items() if not k.startswith('_')}
        db.session.commit()
        with self.assertRaisesRegex(ValueError,'rollback state is unavailable'): self._cancel(assignment)
        db.session.rollback()
        self.assertEqual(self._revision(operation),revision)
        self.assertEqual(NeoScorpionFuelAuditEntry.query.count(),0)
        self.assertEqual(nightly.current_gallons,450)

    def test_unassign_before_work_and_noop_stale_request(self):
        operation=self._operation(); mission,assignment=self._assignment(operation)
        truck,nightly=self._truck(operation,assignment,'REMOVE-1',500)
        revision=self._revision(operation)
        result=self._unassign(assignment); db.session.commit()
        self.assertEqual(result.revision,revision+1)
        self.assertIsNone(assignment.assigned_truck_id)
        self.assertEqual(assignment.assigned_fueler_user_id,self.fueler.id)
        self.assertIsNone(result.fueling_event)
        self.assertEqual(nightly.current_gallons,500)
        with self.assertRaisesRegex(ValueError,'truck changed'): self._unassign(assignment,expected_truck_id=truck.id)
        db.session.rollback()
        result=self._unassign(assignment); db.session.commit()
        self.assertFalse(result.changed)
        self.assertEqual(self._revision(operation),revision+1)
        self.assertEqual(NeoScorpionFuelAuditEntry.query.count(),1)

    def test_unassign_after_setup_preserves_readings_and_exposes_clean_assignment(self):
        operation=self._operation(); mission,assignment=self._assignment(operation)
        truck,nightly=self._truck(operation,assignment,'SETUP-1',500)
        self._save_cycle(assignment,remaining=(10,10,10),actual=('','',''),transfer='')
        work=NeoScorpionFuelWorkState.query.one(); db.session.commit()
        aircraft=service._fuel_rollback_values(work)
        tanks=[service._fuel_rollback_values(t) for t in work.tank_states]
        self._unassign(assignment); db.session.commit()
        for key in ('on_at_utc','apu_running','apu_source_tank_code','apu_allowance_lbs'):
            self.assertEqual(getattr(work,key),aircraft[key] if not key.endswith('_utc') else datetime.fromisoformat(aircraft[key]) if aircraft[key] else None)
        self.assertEqual([service._fuel_rollback_values(t) for t in work.tank_states],tanks)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)
        row=service.fuel_dispatch_context(self.gateway)['rows'][0]
        self.assertTrue(row['initial_truck_assignment_available'])
        self.assertEqual(row['dispatch_status_detail'],'Needs truck')
        self._login(self.fueler)
        self.assertIn(b'TRUCK UNASSIGNED',self.client.get('/neoscorpion/fueler').data)
        revision=self.client.get('/neoscorpion/fuel-assignments/revision')
        self.assertEqual(revision.status_code,200)
        panel=self.client.get('/neoscorpion/fueler/live-panel')
        self.assertEqual(panel.status_code,200)
        self.assertIn('TRUCK UNASSIGNED',panel.json['html'])
        new,new_nightly=self._truck(operation,assignment,'SETUP-2',500)
        assignment.assigned_truck_id=None; db.session.commit()
        self._assign_new(assignment,new.id)
        self.assertIsNotNone(work.truck_segment_started_at_utc)
        self.assertIsNone(assignment.transfer_fuel_gallons)

    def _assign_new(self,assignment,truck_id):
        service.save_dispatch_assignment(self.gateway,{
            'mission_id':str(assignment.sort_date_mission_id),
            'assigned_fueler_user_id':str(self.fueler.id),'assigned_truck_id':str(truck_id),
            'expected_assigned_fueler_user_id':str(self.fueler.id),'expected_assigned_truck_id':'',
        }); db.session.commit()

    def test_moved_unassign_closes_segment_and_reassignment_does_not_recount_old_movement(self):
        operation=self._operation(); mission,assignment=self._assignment(operation)
        truck,nightly=self._truck(operation,assignment,'MOVED-1',500)
        self._save_cycle(assignment,remaining=(10,10,10),actual=(11,10,10),transfer=25)
        db.session.commit(); work=NeoScorpionFuelWorkState.query.one()
        before=[(t.tank_code,t.remaining_lbs,t.actual_lbs) for t in work.tank_states]
        result=self._unassign(assignment); db.session.commit()
        self.assertEqual(result.fueling_event.transfer_fuel_gallons,25)
        self.assertEqual(result.fueling_event.fuel_truck_id,truck.id)
        self.assertEqual(nightly.current_gallons,475)
        self.assertEqual([(t.tank_code,t.remaining_lbs,t.actual_lbs) for t in work.tank_states],before)
        self.assertIsNone(work.truck_segment_started_at_utc)
        self.assertIsNone(assignment.transfer_fuel_gallons)
        new,new_nightly=self._truck(operation,assignment,'MOVED-2',600)
        assignment.assigned_truck_id=None; db.session.commit()
        self._assign_new(assignment,new.id)
        self._unassign(assignment); db.session.commit()
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)
        self.assertEqual(new_nightly.current_gallons,600)
        self._assign_new(assignment,new.id)
        self._save_cycle(assignment,remaining=(10,10,10),actual=(12,10,10),transfer=40)
        db.session.commit()
        self._unassign(assignment); db.session.commit()
        self.assertEqual(nightly.current_gallons,475)
        self.assertEqual(new_nightly.current_gallons,560)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),2)
        audits=NeoScorpionFuelAuditEntry.query.order_by(NeoScorpionFuelAuditEntry.id).all()
        self.assertTrue(all(a.action=='unassign_truck' for a in audits))
        self.assertTrue(all(a.reason=='Dispatcher unassigned the fuel truck.' for a in audits))

    def test_unknown_movement_off_and_wrong_operation_are_atomic(self):
        operation=self._operation(); mission,assignment=self._assignment(operation)
        truck,nightly=self._truck(operation,assignment,'BLOCKED-1',500)
        self._save_cycle(assignment,remaining=(10,10,10),actual=(11,'',''),transfer='')
        db.session.commit(); work=NeoScorpionFuelWorkState.query.one()
        revision=self._revision(operation)
        with self.assertRaisesRegex(ValueError,'REVIEW REQUIRED'): self._unassign(assignment)
        db.session.rollback()
        work.off_at_utc=datetime(2026,8,18,6,10); db.session.commit()
        with self.assertRaisesRegex(ValueError,'REOPEN OFF'): self._unassign(assignment)
        db.session.rollback()
        self.assertEqual(assignment.assigned_truck_id,truck.id)
        self.assertEqual(nightly.current_gallons,500)
        self.assertEqual(self._revision(operation),revision)
        self.assertEqual(NeoScorpionFuelAuditEntry.query.count(),0)
        other=self._operation(day=operation.sort_date.replace(day=16))
        other_mission,other_assignment=self._assignment(other,flight='UPS902')
        with self.assertRaisesRegex(ValueError,'current sort operation'):
            self._unassign(other_assignment)
        db.session.rollback()

    def test_dispatch_routes_permissions_csrf_and_no_internal_metadata(self):
        operation,mission,assignment,work,truck,nightly=self._completed()
        self._start(assignment); self._reassign_cycle(assignment,truck.id)
        self._login(self.fueler)
        for route in ('cancel-uplift','unassign-truck'):
            self.assertEqual(self.client.post('/neoscorpion/fuel-dispatch/'+route,headers={'Accept':'application/json'}).status_code,403)
        self._login(self.dispatcher)
        page=self.client.get('/neoscorpion/fuel-dispatch')
        self.assertIn(b'CANCEL UPLIFT',page.data); self.assertIn(b'UNASSIGN TRUCK',page.data)
        self.assertNotIn(b'_uplift_rollbacks',page.data)
        self.app.config['CSRF_PROTECT_TESTING']=True
        data={'assignment_id':assignment.id,'expected_cycle':assignment.current_cycle_number,
              'expected_tail':assignment.confirmed_tail_number}
        self.assertEqual(self.client.post('/neoscorpion/fuel-dispatch/cancel-uplift',data=data,headers={'Accept':'application/json'}).status_code,400)
        import re
        token=re.search(r'<meta name="csrf-token" content="([^"]+)"',self.client.get('/neoscorpion/fuel-dispatch').get_data(as_text=True)).group(1)
        stale=self.client.post('/neoscorpion/fuel-dispatch/cancel-uplift',data={**data,'expected_cycle':1},headers={'Accept':'application/json','X-CSRFToken':token})
        self.assertEqual(stale.status_code,400)
        response=self.client.post('/neoscorpion/fuel-dispatch/cancel-uplift',data=data,headers={'Accept':'application/json','X-CSRFToken':token})
        self.assertEqual(response.status_code,200)
        self.assertTrue(response.json['ok'])
        self.assertEqual(assignment.current_cycle_number,1)
        self.assertEqual(NeoScorpionFuelAuditEntry.query.count(),1)

    def test_unassign_http_success_revision_and_stale_truck_rejection(self):
        operation=self._operation(); mission,assignment=self._assignment(operation)
        truck,nightly=self._truck(operation,assignment,'HTTP-1',500)
        self._login(self.dispatcher)
        data={'assignment_id':assignment.id,'expected_cycle':assignment.current_cycle_number,
              'expected_tail':assignment.confirmed_tail_number,'expected_truck_id':truck.id}
        revision=self._revision(operation)
        response=self.client.post('/neoscorpion/fuel-dispatch/unassign-truck',data=data,headers={'Accept':'application/json'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json['revision'],revision+1)
        self.assertIsNone(assignment.assigned_truck_id)
        stale=self.client.post('/neoscorpion/fuel-dispatch/unassign-truck',data=data,headers={'Accept':'application/json'})
        self.assertEqual(stale.status_code,400)
        self.assertEqual(NeoScorpionFuelAuditEntry.query.count(),1)
        self.assertEqual(self._revision(operation),revision+1)

    def test_cancel_rejects_movement_recorded_in_an_earlier_truck_segment(self):
        operation,mission,assignment,work,truck,nightly=self._completed()
        self._start(assignment); self._reassign_cycle(assignment,truck.id)
        self._save_cycle(assignment,remaining=(11,10,10),actual=(12,10,10),transfer=20)
        db.session.commit(); self._unassign(assignment); db.session.commit()
        # Even cleared T/F and blank live Actual cannot erase a persisted event.
        for tank in work.tank_states: tank.actual_lbs=None
        db.session.commit(); revision=self._revision(operation)
        with self.assertRaisesRegex(ValueError,'Fuel movement occurred'): self._cancel(assignment)
        db.session.rollback()
        self.assertEqual(self._revision(operation),revision)
        self.assertEqual(NeoScorpionFuelAuditEntry.query.filter_by(action='cancel_uplift').count(),0)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),2)

    def test_audit_constraint_upgrade_preserves_rows_and_is_idempotent(self):
        from sqlalchemy import inspect, text
        from app.services.schema_sync import _sync_neoscorpion_fuel_audit_actions_sqlite
        operation=self._operation(); mission,assignment=self._assignment(operation)
        truck,nightly=self._truck(operation,assignment,'SCHEMA-1',500)
        # Emulate the production constraint before these new actions existed.
        sql=db.session.execute(text("SELECT sql FROM sqlite_master WHERE name='neoscorpion_fuel_audit_entries'")).scalar()
        old=sql.replace(", 'cancel_uplift', 'unassign_truck'",'')
        NeoScorpionFuelAuditEntry.__table__.drop(db.engine)
        db.session.execute(text(old))
        db.session.add(NeoScorpionFuelAuditEntry(sort_date_operation_id=operation.id,
            fuel_assignment_id=assignment.id,action='swap_truck',reason='existing history',
            changed_by_user_id=self.dispatcher.id))
        db.session.commit()
        tables=set(inspect(db.engine).get_table_names())
        self.assertTrue(_sync_neoscorpion_fuel_audit_actions_sqlite(inspect(db.engine),tables))
        db.session.commit()
        self.assertFalse(_sync_neoscorpion_fuel_audit_actions_sqlite(inspect(db.engine),tables))
        self.assertEqual(NeoScorpionFuelAuditEntry.query.one().reason,'existing history')
        self._unassign(assignment); db.session.commit()
        self.assertEqual(NeoScorpionFuelAuditEntry.query.count(),2)

    def test_other_mission_cannot_have_new_tail_measurements_erased_by_cancellation(self):
        operation,mission,assignment,work,truck,nightly=self._completed()
        self._start(assignment)
        other_mission,other=self._assignment(operation,flight='UPS903',tail='N413UP')
        other_mission.assigned_tail_number=work.tail_number
        other.confirmed_tail_number=work.tail_number
        other_work=NeoScorpionFuelWorkState(fuel_assignment_id=other.id,tail_number=work.tail_number)
        db.session.add(other_work); db.session.flush()
        db.session.add(NeoScorpionFuelingEvent(sort_date_operation_id=operation.id,
            fuel_assignment_id=other.id,fuel_work_state_id=other_work.id,tail_number=work.tail_number,
            fuel_truck_id=truck.id,sequence_number=1,event_type='fuel',cycle_number=1,
            started_at_utc=datetime(2026,8,18,6,30),ended_at_utc=datetime(2026,8,18,6,40),transfer_fuel_gallons=10))
        tail=NeoScorpionTailFuelState.query.filter_by(tail_number=work.tail_number).one()
        tail.actual_fuel_lbs=77000; db.session.commit()
        revision=self._revision(operation)
        with self.assertRaisesRegex(ValueError,'another mission updated'): self._cancel(assignment)
        db.session.rollback()
        self.assertEqual(tail.actual_fuel_lbs,77000)
        self.assertEqual(assignment.current_cycle_type,'uplift')
        self.assertEqual(self._revision(operation),revision)
        self.assertEqual(NeoScorpionFuelAuditEntry.query.count(),0)

    def test_unassign_unavailable_truck_resolves_its_hold_without_stranding_started_work(self):
        operation=self._operation(); mission,assignment=self._assignment(operation)
        truck,nightly=self._truck(operation,assignment,'OOS-1',500)
        self._save_cycle(assignment,remaining=(10,10,10),actual=('','',''),transfer='')
        assignment.operational_status='hold_review'; assignment.hold_reason='Assigned truck is unavailable'
        truck.is_out_of_service=True; nightly.status='unavailable_oos'
        db.session.commit()
        self._unassign(assignment); db.session.commit()
        self.assertEqual(assignment.operational_status,'active')
        row=service.fuel_dispatch_context(self.gateway)['rows'][0]
        self.assertTrue(row['initial_truck_assignment_available'])
        self.assertEqual(row['dispatch_status_detail'],'Needs truck')
        new,new_nightly=self._truck(operation,assignment,'OOS-2',500)
        assignment.assigned_truck_id=None; db.session.commit()
        self._assign_new(assignment,new.id)
        self.assertEqual(assignment.assigned_truck_id,new.id)
