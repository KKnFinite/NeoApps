"""R03H: measured no-transfer FOB and explicit historical ON provenance."""
import copy
import json
import unittest
from datetime import timedelta
from unittest.mock import patch

from sqlalchemy import event
from app.extensions import db
from app.models import (NeoScorpionFuelingEvent, NeoScorpionFuelWorkState,
    NeoScorpionFuelAuditEntry, NeoScorpionTruckInventoryAudit, NeoScorpionSortTruck, SortDateOperation)
from app.services import neoscorpion as fuel
from tests import test_neoscorpion_fueling_start as fixtures


def snapshot(case):
    return copy.deepcopy(dict(assignment=fuel._fuel_rollback_values(case.assignment),
        mission=fuel._fuel_rollback_values(case.mission),
        works=[fuel._fuel_rollback_values(w) for w in NeoScorpionFuelWorkState.query.order_by(NeoScorpionFuelWorkState.id)],
        tanks=[fuel._fuel_rollback_values(t) for w in NeoScorpionFuelWorkState.query.order_by(NeoScorpionFuelWorkState.id) for t in w.tank_states],
        events=NeoScorpionFuelingEvent.query.count(),audits=NeoScorpionFuelAuditEntry.query.count(),
        inventory_audits=NeoScorpionTruckInventoryAudit.query.count(),
        stock=[fuel._fuel_rollback_values(s) for s in NeoScorpionSortTruck.query.all()]))


class FobEvidenceContractTest(unittest.TestCase):
    setUp=fixtures.FuelingStartHotfixTest.setUp
    prepare=fixtures.FuelingStartHotfixTest.prepare
    save=fixtures.FuelingStartHotfixTest.save
    readings=fixtures.FuelingStartHotfixTest.readings
    row=fixtures.FuelingStartHotfixTest.row
    confirm=fixtures.FuelingStartHotfixTest.confirm

    def ready(self):
        self.prepare(truck=False)
        self.mission.planned_fuel_load=25000;db.session.commit()
        return self.save(remaining_left='30',remaining_ctr='0',remaining_right='0',
            actual_left='15',actual_ctr='0',actual_right='15',apu_running='no').fuel_work_state

    def assert_no_physical_posting(self,work):
        self.assertEqual((work.on_at_utc,work.confirmed_on_at_utc,work.manual_on_at_utc,
            work.off_at_utc,self.assignment.assigned_truck_id),(None,)*5)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)
        self.assertEqual(NeoScorpionTruckInventoryAudit.query.count(),0)
        self.assertEqual(NeoScorpionFuelWorkState.query.count(),1)

    def test_greater_than_target_and_duplicate_fob_preserve_measurements_without_movement(self):
        work=self.ready();wid=work.id
        measured=[(t.tank_code,t.remaining_lbs,t.actual_lbs) for t in work.tank_states]
        result=fuel.complete_fuel_on_board(self.gateway,self.dispatcher,self.assignment.id);db.session.commit()
        self.assertTrue(result.changed);self.assert_no_physical_posting(work)
        before=snapshot(self)
        self.assertFalse(fuel.complete_fuel_on_board(self.gateway,self.dispatcher,self.assignment.id).changed)
        db.session.commit();self.assertEqual(snapshot(self),before)
        self.assertEqual(work.id,wid)
        self.assertEqual([(t.tank_code,t.remaining_lbs,t.actual_lbs) for t in work.tank_states],measured)
        self.assertEqual((work.physical_transfer_gallons,self.assignment.transfer_fuel_gallons),(0,0))

    def test_remaining_and_unknown_apu_cannot_substitute_for_verified_actual(self):
        self.prepare(truck=False);self.mission.planned_fuel_load=25000;db.session.commit()
        work=self.save(remaining_left='60',remaining_ctr='0',remaining_right='0').fuel_work_state
        for entries in ({},{'actual_left':'15','actual_ctr':'0','actual_right':'15'}):
            if entries:self.save(**entries)
            before=snapshot(self)
            with self.assertRaisesRegex(ValueError,'Complete Actual fuel and confirm APU'):
                fuel.complete_fuel_on_board(self.gateway,self.dispatcher,self.assignment.id)
            db.session.rollback();self.assertEqual(snapshot(self),before)
        self.save(apu_running='no',actual_left='10',actual_right='10')
        with self.assertRaisesRegex(ValueError,'Insufficient verified fuel'):
            fuel.complete_fuel_on_board(self.gateway,self.dispatcher,self.assignment.id)
        db.session.rollback();self.assert_no_physical_posting(work)
        self.assertFalse(self.row()['fuel_on_board_ready'])

    def test_fob_failure_after_finalization_flush_rolls_back_all_changes(self):
        work=self.ready();before=snapshot(self)
        with patch('app.services.neoscorpion_spear_learning_capture.capture_completed_learning_outcome',side_effect=RuntimeError('synthetic capture failure')):
            with self.assertRaisesRegex(RuntimeError,'synthetic capture failure'):
                fuel.complete_fuel_on_board(self.gateway,self.dispatcher,self.assignment.id)
        db.session.rollback();self.assertEqual(snapshot(self),before)
        self.assertTrue(fuel.complete_fuel_on_board(self.gateway,self.dispatcher,self.assignment.id).changed)
        db.session.commit();self.assert_no_physical_posting(work)

    def test_fueler_permission_missing_csrf_and_foreign_sort_cannot_verify_fob(self):
        self.ready();before=snapshot(self);app=self.fixture.app
        with app.app_context():
            client=app.test_client()
            self.assertEqual(client.post('/login',data={'email':self.fueler.email,'password':'TestPassword123!'}).status_code,302)
            denied=client.post('/neoscorpion/fuel-dispatch/fuel-on-board',data={'assignment_id':self.assignment.id})
            self.assertEqual(denied.status_code,403)
        self.assertEqual(snapshot(self),before)
        with app.app_context():
            client=app.test_client();client.post('/login',data={'email':self.dispatcher.email,'password':'TestPassword123!'})
            with patch.dict(app.config,{'CSRF_PROTECT_TESTING':True}):
                denied=client.post('/neoscorpion/fuel-dispatch/fuel-on-board',data={'assignment_id':self.assignment.id})
                self.assertEqual(denied.status_code,400)
        self.assertEqual(snapshot(self),before)
        other=SortDateOperation(generated_by_user_id=self.dispatcher.id,gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,sort_date=self.operation.sort_date+timedelta(days=1),
            sort_name='night',window_minutes=60)
        db.session.add(other)
        db.session.commit()
        with patch.object(fuel,'current_sort_operation',return_value=other):
            with self.assertRaisesRegex(ValueError,'not found for the current sort'):
                fuel.complete_fuel_on_board(self.gateway,self.dispatcher,self.assignment.id)
        db.session.rollback();self.assertEqual(snapshot(self),before)

    def test_read_only_contexts_do_not_create_physical_evidence(self):
        self.ready();before=snapshot(self);writes=[]
        def record(_conn,_cursor,statement,*_args):
            if statement.lstrip().split(' ',1)[0].upper() in ('INSERT','UPDATE','DELETE') and 'neoscorpion_' in statement.lower():writes.append(statement)
        event.listen(db.engine,'before_cursor_execute',record)
        try:
            self.row();fuel.fueler_context(self.gateway,self.fueler,assignment_id=self.assignment.id,include_timing=False)
        finally:event.remove(db.engine,'before_cursor_execute',record)
        self.assertEqual(writes,[]);self.assertEqual(snapshot(self),before)

    def test_stale_work_and_foreign_mission_cannot_change_or_borrow_fob_measurements(self):
        work=self.ready()
        baseline=fuel.fueler_context(self.gateway,self.fueler,assignment_id=self.assignment.id,include_timing=False)['rows'][0]['edit_baseline']
        stale={**baseline,'work_ref':work.id+1000};before=snapshot(self)
        with self.assertRaises(fuel.FuelerDataConflict):
            fuel.save_fueler_entry(self.gateway,self.fueler,dict(assignment_id=str(self.assignment.id),
                expected=json.dumps(stale),actual_left='1'),require_expected=True)
        db.session.rollback();self.assertEqual(snapshot(self),before)
        other=self.fixture._mission('UPS-OTHER','N492UP',25000,2)
        assignment=self.fixture._assignment(other);assignment.assigned_fueler_user_id=self.fueler.id
        db.session.commit()
        with self.assertRaisesRegex(ValueError,'Complete Actual fuel and confirm APU'):
            fuel.complete_fuel_on_board(self.gateway,self.dispatcher,assignment.id)
        db.session.rollback()
        self.assertIsNone(assignment.fuel_on_board_at_utc)
        self.assertEqual(sum(t.actual_lbs for t in work.tank_states),30000)
        self.assert_no_physical_posting(work)

    def test_uncertain_raw_legacy_on_is_preserved_but_not_used_as_physical_or_performance_evidence(self):
        self.prepare();work=self.save(**self.readings()).fuel_work_state
        # Historical import fixture only: raw ON predates the tank evidence.
        raw=self.now-timedelta(hours=1);work.on_at_utc=raw;db.session.commit()
        for when in (self.now,self.now+timedelta(hours=1)):
            row=self.row(when)
            self.assertFalse(row['fueling_on_trusted']);self.assertFalse(row['fueling_on_active'])
            self.assertEqual(row['dispatch_status_key'],'review')
            self.assertIsNone(row['fueling_effective_on_utc'])
            self.assertIsNone(fuel.active_fueler_off(row,self.operation,fuel.assignment_planning_settings(self.gateway,settings=self.settings)))
        self.save(now=self.now+timedelta(hours=1),remaining_left='13')
        self.assertEqual(work.on_at_utc,raw);self.assertIsNone(work.confirmed_on_at_utc)
        self.assertEqual(self.row(self.now+timedelta(hours=2))['dispatch_status_key'],'review')
        self.assertIsNone(work.manual_on_at_utc)
        self.assertIsNone(work.off_at_utc)

    def test_persisted_automatic_confirmation_survives_later_tank_updates(self):
        self.prepare();work=self.save(**self.readings()).fuel_work_state
        # Explicit accepted historical confirmation, never inferred by a new save.
        work.on_at_utc=self.now;work.confirmed_on_at_utc=self.now
        work.performance_context={'on_source':'trusted automatic','truck_id':self.truck.id,'fueler_id':self.fueler.id}
        db.session.commit();facts=copy.deepcopy(work.performance_context)
        self.save(now=self.now+timedelta(minutes=10),remaining_left='13')
        row=self.row(self.now+timedelta(minutes=11))
        self.assertTrue(row['fueling_on_trusted']);self.assertEqual(row['fueling_effective_on_utc'],self.now)
        self.assertEqual((work.on_at_utc,work.confirmed_on_at_utc),(self.now,self.now))
        self.assertEqual({key:work.performance_context[key] for key in facts},facts)
        self.assertIsNone(work.manual_on_at_utc)
