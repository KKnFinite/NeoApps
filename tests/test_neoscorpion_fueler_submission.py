"""Explicit Fueler handoff is independent of physical OFF and inventory posting."""
import unittest
from datetime import timedelta
from app.extensions import db
from app.models import NeoScorpionFuelingEvent,NeoScorpionSortFueler,NeoScorpionFuelWorkState
from app.services import neoscorpion as service
from tests import test_neoscorpion_operational_timeline as fixtures

class SubmissionTest(unittest.TestCase):
    setUp=fixtures.OperationalTimelineTest.setUp
    prepare=fixtures.OperationalTimelineTest.prepare
    save=fixtures.OperationalTimelineTest.save
    off=fixtures.OperationalTimelineTest.off
    final_data=fixtures.OperationalTimelineTest.final_data
    login=fixtures.OperationalTimelineTest.login
    def row(self):
        return next(r for r in service.fuel_dispatch_context(self.gateway)['rows'] if r['mission'].id==self.mission.id)
    def baseline(self):
        return service.fueler_context(self.gateway,self.fueler,assignment_id=self.assignment.id)['rows'][0]['edit_baseline']
    def submit(self,expected=None):
        result=service.submit_fueler_assignment(self.gateway,self.fueler,self.assignment.id,expected=self.baseline() if expected is None else expected)
        db.session.commit();return result
    def test_pre_on_assign_unassign_keeps_controls_and_preparation_then_locks_on(self):
        truck=self.fixture._nightly_truck()
        db.session.add(NeoScorpionSortFueler(sort_date_operation_id=self.operation.id,user_id=self.fueler.id))
        self.assignment.assigned_fueler_user_id=self.fueler.id;self.assignment.assigned_truck_id=truck.id;db.session.commit()
        saved=service.save_fueler_entry(self.gateway,self.fueler,dict(assignment_id=self.assignment.id,apu_running='no',remaining_left='6',remaining_ctr='0',remaining_right='6',notes='Preparation'));db.session.commit()
        work=saved.fuel_work_state;self.assertIsNone(work.on_at_utc);self.assertFalse(self.row()['resource_swap_required'])
        service.save_dispatch_assignment(self.gateway,dict(mission_id=self.mission.id,expected_work_ref=work.id,expected_assigned_fueler_user_id=self.fueler.id,expected_assigned_truck_id=truck.id,assigned_fueler_user_id=self.fueler.id,assigned_truck_id=''));db.session.commit()
        self.assertIsNone(self.assignment.assigned_truck_id);self.assertEqual(self.assignment.assigned_fueler_user_id,self.fueler.id)
        self.assertEqual(work.notes,'Preparation');self.assertEqual(work.apu_running,False);self.assertFalse(self.row()['resource_swap_required'])
        self.login(self.dispatcher);html=self.client.get('/neoscorpion/fuel-dispatch').get_data(as_text=True)
        self.assertIn('aria-label="Assigned fueler for UPS901"',html);self.assertIn('aria-label="Assigned fuel truck for UPS901"',html)
        self.assignment.assigned_truck_id=truck.id;db.session.commit()
        service.confirm_fueler_truck_on(self.gateway,self.fueler,self.assignment.id,expected_cycle=1,expected_tail='N491UP',expected_truck_id=truck.id);db.session.commit()
        self.assertTrue(self.row()['resource_swap_required'])
        with self.assertRaisesRegex(ValueError,'SWAP'):
            service.save_dispatch_assignment(self.gateway,dict(mission_id=self.mission.id,expected_work_ref=work.id,expected_assigned_fueler_user_id=self.fueler.id,expected_assigned_truck_id=truck.id,assigned_fueler_user_id=self.fueler.id,assigned_truck_id=''))
    def test_truck_only_unassign_does_not_allocate_work_or_remove_selectors(self):
        truck=self.fixture._nightly_truck();self.assignment.assigned_truck_id=truck.id;self.assignment.assigned_fueler_user_id=None;db.session.commit()
        service.unassign_assignment_truck(self.gateway,self.dispatcher,self.assignment.id,expected_cycle=1,expected_tail='N491UP',expected_truck_id=truck.id);db.session.commit()
        self.assertIsNone(self.assignment.assigned_truck_id);self.assertIsNone(self.assignment.assigned_fueler_user_id)
        self.assertEqual(NeoScorpionFuelWorkState.query.count(),0);self.assertFalse(self.row()['resource_swap_required'])
    def test_off_without_actual_and_submission_required_before_dispatcher_complete(self):
        self.prepare();self.off()
        self.assertIsNone(self.work.actuals_received_at_utc);self.assertIsNone(self.assignment.completed_at_utc)
        self.assertFalse(self.row()['fueler_completion_ready'])
        with self.assertRaises(ValueError):self.submit()
        db.session.rollback();self.save(self.start+timedelta(minutes=15),**self.final_data())
        self.assertTrue(self.row()['fueler_completion_ready']);self.assertFalse(self.row()['normal_completion_ready'])
        with self.assertRaisesRegex(ValueError,'COMPLETE ASSIGNMENT'):
            service.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id)
        db.session.rollback();self.submit()
        self.assertTrue(self.row()['normal_completion_ready']);self.assertEqual(self.work.fueler_submitted_by_user_id,self.fueler.id)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)
        service.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id);db.session.commit()
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),1);self.assertEqual(self.selection.current_gallons,7000)
        self.assertFalse(service.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id).changed)
    def test_required_correction_invalidates_notes_preserve_and_resubmit_is_safe(self):
        self.prepare();self.save(self.start+timedelta(minutes=10),**self.final_data())
        self.off()
        self.submit()
        self.assertIsNotNone(self.work.off_at_utc)
        old=self.baseline()
        self.save(self.start+timedelta(minutes=14),notes='Optional note')
        self.assertTrue(self.row()['fueler_submitted'])
        self.save(self.start+timedelta(minutes=15),actual_left='16')
        self.assertFalse(self.row()['fueler_submitted']);self.assertFalse(self.row()['normal_completion_ready'])
        with self.assertRaises(service.FuelerDataConflict):self.submit(expected=old)
        db.session.rollback();self.submit();self.assertTrue(self.row()['normal_completion_ready'])
        self.assertFalse(self.submit().changed)
        service.reopen_fueler_off(self.gateway,self.dispatcher,self.assignment.id,'Verify OFF');db.session.commit()
        self.assertFalse(self.row()['fueler_submitted'])
    def test_required_target_increase_and_revert_preserves_submitted_source_for_review(self):
        self.prepare();self.save(self.start+timedelta(minutes=10),**self.final_data());self.off();self.submit()
        for value,old in (('26','25'),('25','26')):
            service.autosave_dispatch_field(self.gateway,self.mission.id,'required_fuel',value,expected_value=old,user=self.dispatcher)
            db.session.commit()
        self.assertTrue(self.row()['fueler_submitted']);self.assertTrue(self.row()['normal_completion_ready'])
        from app.models import NeoScorpionPendingUplift
        plan=NeoScorpionPendingUplift.query.one()
        self.assertEqual(plan.status,'review');self.assertEqual(plan.parent_work_state_id,self.work.id)
        self.assertEqual(self.work.physical_required_fuel_lbs,25000)
    def test_complete_assignment_requires_separate_physical_off(self):
        self.prepare()
        self.save(self.start+timedelta(minutes=10),**self.final_data())
        self.assertIsNone(self.work.off_at_utc)
        self.assertFalse(self.row()['fueler_completion_ready'])
        with self.assertRaisesRegex(ValueError,'TRUCK OFF'):
            self.submit()
        db.session.rollback()
        self.assertIsNone(self.work.off_at_utc)
        self.assertIsNone(self.work.fueler_submitted_at_utc)
        self.off()
        self.submit()
        self.assertIsNotNone(self.work.off_at_utc)
        self.assertEqual(self.work.off_by_user_id,self.fueler.id)
        self.assertTrue(self.row()['fueler_submitted'])
        self.assertFalse(self.selection.inventory_verified)
        self.assertIsNone(self.assignment.completed_at_utc)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)
        service.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id)
        db.session.commit()
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)

    def test_complete_assignment_cannot_invent_physical_on(self):
        self.prepare()
        self.save(self.start+timedelta(minutes=10),**self.final_data())
        self.work.manual_on_at_utc=None
        self.work.confirmed_on_at_utc=None
        self.work.on_at_utc=None
        db.session.commit()
        with self.assertRaisesRegex(ValueError,'TRUCK ON'):
            self.submit()
        db.session.rollback()
        self.assertIsNone(self.work.off_at_utc)
        self.assertIsNone(self.work.fueler_submitted_at_utc)

    def test_missing_apu_tf_hold_and_actor_block_submission(self):
        self.prepare();self.off();data=self.final_data();data.pop('apu_running');data.pop('transfer_fuel_gallons')
        self.save(self.start+timedelta(minutes=14),**data)
        self.assertFalse(self.row()['fueler_completion_ready'])
        self.save(self.start+timedelta(minutes=15),apu_running='no');self.assertFalse(self.row()['fueler_completion_ready'])
        self.save(self.start+timedelta(minutes=16),transfer_fuel_gallons='3000');self.assertTrue(self.row()['fueler_completion_ready'])
        with self.assertRaisesRegex(ValueError,'assigned Fueler'):
            service.submit_fueler_assignment(self.gateway,self.dispatcher,self.assignment.id,expected=self.baseline())
        self.assignment.operational_status='hold_review';db.session.commit();self.assertFalse(self.row()['fueler_completion_ready'])
