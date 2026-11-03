"""R03B: physical facts through canonical APIs, never inferred from a tail alone."""
import copy
import unittest
from datetime import timedelta
from unittest.mock import patch
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    NeoScorpionFuelAuditEntry, NeoScorpionFuelingEvent, NeoScorpionFuelWorkState,
    NeoScorpionTailFuelState,
    NeoScorpionFuelTankState, NeoScorpionFuelAssignment, SortDateOperation, SortDateMission,
)
from app.services import neoscorpion as fuel
from app.services.neoscorpion_assets import update_nightly_truck
from tests import test_neoscorpion_lifecycle_audit as lifecycle


class PhysicalEvidenceTest(unittest.TestCase):
    setUp = lifecycle.LifecycleAuditTest.setUp
    prepare = lifecycle.LifecycleAuditTest.prepare
    save = lifecycle.LifecycleAuditTest.save
    off = lifecycle.LifecycleAuditTest.off
    final_data = lifecycle.LifecycleAuditTest.final_data
    baseline = lifecycle.LifecycleAuditTest.baseline
    submit = lifecycle.LifecycleAuditTest.submit
    row = lifecycle.LifecycleAuditTest.row
    finish = lifecycle.LifecycleAuditTest.finish
    start_followup = lifecycle.LifecycleAuditTest.start_followup
    on_current = lifecycle.LifecycleAuditTest.on_current

    def cancel(self):
        return getattr(fuel, 'cancel_' + self.assignment.current_cycle_type)(
            self.gateway, self.dispatcher, self.assignment.id,
            expected_cycle=self.assignment.current_cycle_number,
            expected_tail=self.mission.assigned_tail_number)

    def unassign(self):
        return fuel.unassign_assignment_truck(self.gateway, self.dispatcher, self.assignment.id,
            expected_cycle=self.assignment.current_cycle_number,
            expected_tail=self.mission.assigned_tail_number,
            expected_truck_id=self.assignment.assigned_truck_id)

    @staticmethod
    def facts(work):
        return (fuel._fuel_rollback_values(work),
                [fuel._fuel_rollback_values(t) for t in work.tank_states])

    def replacement(self, tail='N491UP'):
        mission = self.fixture._mission('UPS1476', 'N492UP', 25000, 2)
        assignment = self.fixture._assignment(mission)
        assignment.confirmed_tail_number = 'N492UP'
        mission.assigned_tail_number = tail
        db.session.commit()
        result = fuel.confirm_assignment_tail(self.gateway, self.dispatcher, assignment.id,
            expected_cycle=1, expected_tail=tail)
        db.session.commit()
        return mission, assignment, result.fuel_work_state

    def test_same_tail_completed_event_is_remaining_only_with_provenance(self):
        self.prepare(); self.finish()
        source = NeoScorpionFuelingEvent.query.one()
        frozen = self.facts(self.work), copy.deepcopy(source.performance_snapshot)
        mission, assignment, work = self.replacement()
        self.assertNotEqual(assignment.id, source.fuel_assignment_id)
        self.assertNotEqual(work.id, source.fuel_work_state_id)
        self.assertEqual({t.tank_code: t.remaining_lbs for t in work.tank_states},
                         {t.tank_code: t.actual_lbs for t in source.tank_snapshots})
        self.assertTrue(all(t.actual_lbs is None for t in work.tank_states))
        self.assertIsNone(work.physical_transfer_gallons)
        self.assertIsNone(work.apu_running)
        row = next(r for r in fuel.fuel_dispatch_context(self.gateway)['rows']
                   if r['mission'].id == mission.id)
        self.assertFalse(row['fuel_on_board_ready'])
        self.assertFalse(row['neo_fuel_available'])
        audit = NeoScorpionFuelAuditEntry.query.filter_by(fuel_work_state_id=work.id,
            field_name=fuel.TAIL_SWAP_INHERITED_EVENT_AUDIT_FIELD).one()
        self.assertEqual((audit.old_value, audit.new_value), ('N491UP', str(source.id)))
        self.assertEqual((self.facts(self.work), source.performance_snapshot), frozen)
        version = work.updated_at
        self.assertFalse(fuel.confirm_assignment_tail(self.gateway,self.dispatcher,assignment.id,
            expected_cycle=1,expected_tail='N491UP').changed)
        db.session.commit()
        self.assertEqual(work.updated_at, version)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(), 1)

    def test_different_tail_has_no_inherited_measurement_or_transfer(self):
        self.prepare(); self.finish()
        _, assignment, work = self.replacement('N493UP')
        self.assertEqual(work.tank_states, [])
        self.assertIsNone(work.physical_transfer_gallons)
        self.assertIsNone(assignment.transfer_fuel_gallons)
        self.assertEqual(self.work.tail_number, 'N491UP')
        self.assertEqual(self.selection.current_gallons, 7000)

    def test_completed_other_mission_and_actual_readings_stay_independent(self):
        self.prepare(); self.finish()
        source_facts = self.facts(self.work)
        mission, assignment, work = self.replacement()
        assignment.assigned_fueler_user_id = self.fueler.id
        db.session.commit()
        fuel.save_fueler_entry(self.gateway, self.fueler, dict(assignment_id=assignment.id,
            apu_running='no', actual_left='14', actual_ctr='0', actual_right='14'))
        db.session.commit()
        self.assertEqual({t.tank_code:t.actual_lbs for t in work.tank_states},
                         {'left':14000,'ctr':0,'right':14000})
        self.assertEqual(self.facts(self.work), source_facts)
        self.assertIsNone(work.physical_transfer_gallons)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(), 1)

    def test_cancel_untransferred_followup_retains_original_and_cancelled_facts(self):
        self.prepare(); self.finish()
        original = self.facts(self.work)
        event = NeoScorpionFuelingEvent.query.one()
        snapshots = [fuel._fuel_rollback_values(t) for t in event.tank_snapshots]
        for kind in ('uplift', 'defuel'):
            with self.subTest(kind=kind):
                work = self.start_followup(kind, '40' if kind=='uplift' else '20')
                self.on_current()
                fuel.mark_fueler_off(self.gateway,self.fueler,self.assignment.id)
                db.session.commit()
                physical = work.manual_on_at_utc, work.off_at_utc
                tanks = [(t.id,t.remaining_lbs,t.actual_lbs) for t in work.tank_states]
                self.cancel(); db.session.commit()
                self.assertEqual((work.manual_on_at_utc,work.off_at_utc), physical)
                self.assertEqual([(t.id,t.remaining_lbs,t.actual_lbs) for t in work.tank_states], tanks)
                self.assertIsNotNone(work.physical_finalized_at_utc)
                self.assertEqual(self.assignment.active_work_state_id, self.work.id)
                self.assertEqual(self.facts(self.work), original)
                self.assertEqual([fuel._fuel_rollback_values(t) for t in event.tank_snapshots], snapshots)
                self.assertEqual(self.selection.current_gallons, 7000)
                self.assertEqual(NeoScorpionFuelingEvent.query.count(), 1)

    def test_cancel_rejects_transfer_on_earlier_visit_after_unassignment(self):
        self.prepare(); self.finish()
        work = self.start_followup()
        self.on_current()
        fuel.save_fueler_entry(self.gateway,self.fueler,dict(assignment_id=self.assignment.id,
            apu_running='no',actual_left='16',actual_ctr='0',actual_right='15',transfer_fuel_gallons='150'))
        fuel.mark_fueler_off(self.gateway,self.fueler,self.assignment.id)
        db.session.commit()
        # OFF + readings alone do not post an event or pretend handoff occurred.
        self.unassign(); db.session.commit()
        self.assertNotEqual(self.assignment.active_work_state_id, work.id)
        self.assertIsNone(self.assignment.transfer_fuel_gallons)
        self.assertEqual(work.physical_transfer_gallons, 150)
        before = self.facts(work), self.selection.current_gallons
        with self.assertRaisesRegex(ValueError, 'Fuel movement occurred'):
            self.cancel()
        db.session.rollback()
        self.assertEqual((self.facts(work),self.selection.current_gallons), before)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(), 1)
        self.assertEqual(NeoScorpionFuelAuditEntry.query.filter_by(action='cancel_uplift').count(), 0)

    def test_unassign_unavailable_truck_before_on_resolves_only_satisfied_hold(self):
        self.prepare()
        # Start a separate preparation; the existing physical visit stays owned.
        mission = self.fixture._mission('UPS1476','N492UP',25000,2)
        truck = self.fixture._nightly_truck('OOS-PREP')
        assignment = self.fixture._assignment(mission,truck=truck)
        assignment.assigned_fueler_user_id = self.fueler.id
        db.session.commit()
        work = fuel.save_fueler_entry(self.gateway,self.fueler,dict(assignment_id=assignment.id,
            remaining_left='6',remaining_ctr='0',remaining_right='6')).fuel_work_state
        db.session.commit()
        update_nightly_truck(self.operation,truck,status='unavailable_oos',changed_by_user=self.dispatcher)
        db.session.commit()
        self.assertEqual(assignment.operational_status,'hold_review')
        tanks = [(t.id,t.remaining_lbs,t.actual_lbs) for t in work.tank_states]
        fuel.unassign_assignment_truck(self.gateway,self.dispatcher,assignment.id,
            expected_cycle=1,expected_tail='N492UP',expected_truck_id=truck.id)
        db.session.commit()
        self.assertEqual(assignment.operational_status,'active')
        self.assertIsNone(work.manual_on_at_utc)
        self.assertEqual([(t.id,t.remaining_lbs,t.actual_lbs) for t in work.tank_states],tanks)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)

    def test_cancel_database_failure_rolls_back_projection_and_audit(self):
        self.prepare(); self.finish(); work = self.start_followup()
        before = self.facts(work), self.assignment.active_work_state_id
        with patch.object(fuel, 'record_nightly_operational_change', side_effect=RuntimeError('write failure')):
            with self.assertRaisesRegex(RuntimeError,'write failure'):
                self.cancel()
        db.session.rollback(); db.session.expire_all()
        self.assertEqual((self.facts(work),self.assignment.active_work_state_id),before)
        self.assertEqual(self.assignment.current_cycle_type,'uplift')
        self.assertEqual(NeoScorpionFuelAuditEntry.query.filter_by(action='cancel_uplift').count(),0)

    def test_other_mission_actuals_without_completed_event_block_cancel(self):
        self.prepare(); self.finish(); self.start_followup()
        mission, assignment, work = self.replacement()
        assignment.assigned_fueler_user_id = self.fueler.id
        db.session.commit()
        fuel.save_fueler_entry(self.gateway,self.fueler,dict(assignment_id=assignment.id,
            apu_running='no',remaining_left='15',remaining_ctr='0',remaining_right='15',
            actual_left='14',actual_ctr='0',actual_right='14'))
        db.session.commit()
        tail = NeoScorpionTailFuelState.query.filter_by(sort_date_operation_id=self.operation.id,
            tail_number='N491UP').one()
        before = self.facts(work), fuel._fuel_rollback_values(tail)
        with self.assertRaisesRegex(ValueError,'another mission updated'):
            self.cancel()
        db.session.rollback()
        self.assertEqual((self.facts(work),fuel._fuel_rollback_values(tail)),before)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)

    def test_different_tail_entry_does_not_block_cancellation(self):
        self.prepare(); self.finish(); self.start_followup()
        _,other,work=self.replacement('N493UP')
        other.assigned_fueler_user_id=self.fueler.id
        db.session.commit()
        fuel.save_fueler_entry(self.gateway,self.fueler,dict(assignment_id=other.id,
            remaining_left='14',remaining_ctr='0',remaining_right='14',apu_running='no'))
        db.session.commit()
        facts=self.facts(work)
        self.cancel();db.session.commit()
        self.assertEqual(self.facts(work),facts)
        self.assertEqual(self.assignment.current_cycle_number,1)

    def test_visit_uniqueness_and_negative_measurements_preserve_session(self):
        self.prepare()
        self.save(self.start+timedelta(minutes=1),**self.final_data())
        self.off(); self.submit()
        fuel.begin_physical_visit(self.gateway,self.fueler,self.assignment.id,
            expected=self.baseline())
        db.session.commit()
        current=db.session.get(NeoScorpionFuelWorkState,self.assignment.active_work_state_id)
        self.assertNotEqual(current.id,self.work.id)
        self.assertEqual((self.work.visit_number,current.visit_number),(1,2))
        for row in (
            NeoScorpionFuelWorkState(fuel_assignment_id=self.assignment.id,tail_number='N492UP',visit_number=1),
            NeoScorpionFuelTankState(fuel_work_state_id=current.id,tank_code='left',remaining_lbs=-1),
            NeoScorpionFuelTankState(fuel_work_state_id=current.id,tank_code='right',actual_lbs=-1),
        ):
            with self.assertRaises(IntegrityError):
                with db.session.begin_nested():
                    db.session.add(row);db.session.flush()
        self.assertEqual(NeoScorpionFuelWorkState.query.filter_by(fuel_assignment_id=self.assignment.id).count(),2)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)

    def test_unassignment_does_not_clear_an_ineligible_fueler_hold(self):
        self.prepare()
        mission=self.fixture._mission('UPS1476','N492UP',25000,2)
        truck=self.fixture._nightly_truck('HOLD-PREP')
        assignment=self.fixture._assignment(mission,truck=truck)
        assignment.assigned_fueler_user_id=self.fueler.id
        db.session.commit()
        update_nightly_truck(self.operation,truck,status='unavailable_oos',changed_by_user=self.dispatcher)
        self.fueler.is_active=False
        db.session.commit()
        fuel.unassign_assignment_truck(self.gateway,self.dispatcher,assignment.id,
            expected_cycle=1,expected_tail='N492UP',expected_truck_id=truck.id)
        db.session.commit()
        self.assertEqual(assignment.operational_status,'hold_review')
        self.assertIsNone(assignment.assigned_truck_id)
        self.assertIsNone(assignment.active_work_state_id)

    def test_foreign_sort_cancellation_and_save_are_rejected(self):
        self.prepare()
        operation=SortDateOperation(generated_by_user_id=self.dispatcher.id,gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,sort_date=self.operation.sort_date-timedelta(days=1),sort_name='night',window_minutes=60)
        db.session.add(operation);db.session.flush()
        mission=SortDateMission(sort_date_operation_id=operation.id,sort_date=operation.sort_date,
            gateway_code=self.gateway.code,sort_name='night',mission_type='departure',mission_source='manual',
            flight_number='UPS1476',assigned_tail_number='N491UP',origin=self.gateway.code,destination='SDF')
        db.session.add(mission);db.session.flush()
        assignment=NeoScorpionFuelAssignment(sort_date_operation_id=operation.id,sort_date_mission_id=mission.id,
            assigned_fueler_user_id=self.fueler.id,confirmed_tail_number='N491UP')
        db.session.add(assignment);db.session.commit()
        for action in (
            lambda:fuel.cancel_uplift(self.gateway,self.dispatcher,assignment.id,expected_cycle=1,expected_tail='N491UP'),
            lambda:fuel.save_fueler_entry(self.gateway,self.fueler,dict(assignment_id=assignment.id,remaining_left='9')),
        ):
            with self.assertRaises(ValueError):action()
            db.session.rollback()
        self.assertIsNone(assignment.active_work_state_id)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)
