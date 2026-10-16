"""Queued uplift leaves active work untouched and promotes only verified completion."""
import unittest
from datetime import timedelta
from unittest.mock import patch
from app.extensions import db
from app.models import (NeoScorpionPendingUplift,NeoScorpionFuelCycleHistory,NeoScorpionFuelingEvent,
    NeoScorpionFuelWorkState,NeoScorpionSortTruck,SortDateMission)
from app.services import neoscorpion as fuel
from app.services import neoscorpion_pending_uplift as pending
from tests import test_neoscorpion_fueler_submission as fixtures

class PendingUpliftTest(unittest.TestCase):
    setUp=fixtures.SubmissionTest.setUp
    prepare=fixtures.SubmissionTest.prepare
    save=fixtures.SubmissionTest.save
    off=fixtures.SubmissionTest.off
    final_data=fixtures.SubmissionTest.final_data
    baseline=fixtures.SubmissionTest.baseline
    submit=fixtures.SubmissionTest.submit
    row=fixtures.SubmissionTest.row
    login=fixtures.SubmissionTest.login
    def queue(self,target='40'):
        self.prepare()
        result=pending.save_pending_uplift(self.gateway,self.dispatcher,dict(assignment_id=self.assignment.id,pending_action='create',expected_cycle=self.assignment.current_cycle_number,expected_tail=self.mission.assigned_tail_number))
        db.session.commit();self.pending=result.pending
        self.edit(target=target)
        return self.pending
    def edit(self,*,target='40',fueler='current',truck='current',action='save',expected=None):
        p=self.pending
        result=pending.save_pending_uplift(self.gateway,self.dispatcher,dict(assignment_id=self.assignment.id,
            pending_id=p.id,expected_version=p.version if expected is None else expected,pending_action=action,
            required_fuel=target,proposed_fueler_user_id=self.fueler.id if fueler=='current' else fueler,
            proposed_truck_id=self.truck.id if truck=='current' else truck))
        db.session.commit();return result
    def complete_parent(self):
        self.save(self.start+timedelta(minutes=10),**self.final_data());self.off();self.submit()
        result=fuel.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id,now_utc=self.start+timedelta(minutes=16))
        db.session.commit();return result
    def test_edit_pending_while_on_never_changes_active_work_or_counts(self):
        self.queue();original=(self.assignment.current_cycle_number,self.assignment.assigned_truck_id,self.assignment.assigned_fueler_user_id,self.mission.planned_fuel_load,self.work.manual_on_at_utc,self.work.off_at_utc,self.selection.current_gallons)
        self.save(self.start+timedelta(minutes=2),notes='Current physical note',remaining_left='6',remaining_ctr='0',remaining_right='6',apu_running='no')
        self.edit(target='45.12',truck='',fueler='');self.edit(target='41.12')
        self.assertEqual((self.assignment.current_cycle_number,self.assignment.assigned_truck_id,self.assignment.assigned_fueler_user_id,self.mission.planned_fuel_load,self.work.manual_on_at_utc,self.work.off_at_utc,self.selection.current_gallons),original)
        self.assertEqual(self.pending.required_fuel_lbs,41120);self.assertEqual(self.work.notes,'Current physical note')
        current=fuel.fueler_context(self.gateway,self.fueler)['rows'][0];self.assertEqual(current['required_fuel_lbs'],25000)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0);self.assertEqual(NeoScorpionFuelCycleHistory.query.count(),0)
        self.assertEqual(NeoScorpionFuelWorkState.query.count(),1)
    def test_promotes_exact_parent_actuals_resets_evidence_and_posts_inventory_once(self):
        self.queue('40.12');old_id=self.work.id;on=self.work.manual_on_at_utc
        self.complete_parent();p=self.pending
        self.assertEqual(p.status,'promoted',p.review_reason);self.assertEqual(self.assignment.current_cycle_number,2)
        self.assertEqual(self.mission.planned_fuel_load,40120);self.assertIsNone(self.assignment.completed_at_utc)
        current=db.session.get(NeoScorpionFuelWorkState,self.assignment.active_work_state_id)
        self.assertNotEqual(current.id,old_id);self.assertEqual({t.tank_code:t.remaining_lbs for t in current.tank_states},{'left':15000,'ctr':0,'right':15000})
        for name in ('on_at_utc','manual_on_at_utc','confirmed_on_at_utc','off_at_utc','apu_running','fueler_submitted_at_utc','fueler_submission_evidence','notes','physical_transfer_gallons'):
            self.assertIsNone(getattr(current,name),name)
        self.assertTrue(all(t.actual_lbs is None for t in current.tank_states));self.assertIsNone(self.assignment.transfer_fuel_gallons)
        self.assertEqual(self.work.manual_on_at_utc,on);self.assertIsNotNone(self.work.physical_finalized_at_utc)
        self.assertEqual(self.selection.current_gallons,7000);self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)
        history=NeoScorpionFuelCycleHistory.query.one();self.assertEqual(history.snapshot['required_fuel_lbs'],25000)
        self.assertEqual(p.promoted_cycle_number,2)
        with self.assertRaises(ValueError):self.edit(action='retry')
        db.session.rollback();self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)
    def test_unavailable_resource_retains_completed_parent_then_correct_retry(self):
        self.queue();other=self.fixture._nightly_truck(number='NEW',current_gallons=10000)
        self.edit(truck=other.id)
        selection=NeoScorpionSortTruck.query.filter_by(fuel_truck_id=other.id).one();selection.status='topping_off';db.session.commit()
        self.complete_parent();self.assertEqual(self.pending.status,'review');self.assertIn('topping off',self.pending.review_reason)
        self.assertIsNotNone(self.assignment.completed_at_utc);self.assertEqual(self.assignment.current_cycle_number,1)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),1);self.assertEqual(self.selection.current_gallons,7000)
        self.edit(truck=self.truck.id);self.edit(action='retry')
        self.assertEqual(self.pending.status,'promoted',self.pending.review_reason);self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)
    def test_failed_promotion_savepoint_never_undoes_genuine_completion(self):
        self.queue()
        with patch.object(fuel,'start_follow_up_fuel_cycle',side_effect=ValueError('Injected follow-up validation failure')):self.complete_parent()
        self.assertEqual(self.pending.status,'review');self.assertIn('Injected',self.pending.review_reason)
        self.assertIsNotNone(self.assignment.completed_at_utc);self.assertIsNotNone(self.work.off_at_utc)
        self.assertEqual(self.selection.current_gallons,7000);self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)
        self.edit(action='retry');self.assertEqual(self.pending.status,'promoted',self.pending.review_reason)
    def test_target_already_met_requires_review_and_cancel_preserves_audit(self):
        self.queue('29');self.complete_parent()
        self.assertEqual(self.pending.status,'review');self.assertIn('meets or exceeds',self.pending.review_reason)
        self.edit(action='cancel');self.assertEqual(self.pending.status,'cancelled');self.assertIsNotNone(self.assignment.completed_at_utc)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)
    def test_cancellation_stale_version_and_duplicate_create_are_safe(self):
        self.queue();version=self.pending.version
        again=pending.save_pending_uplift(self.gateway,self.dispatcher,dict(assignment_id=self.assignment.id,pending_action='create',expected_cycle=1,expected_tail='N491UP'))
        self.assertFalse(again.changed);self.assertEqual(NeoScorpionPendingUplift.query.count(),1)
        self.edit(target='42')
        with self.assertRaises(pending.PendingUpliftConflict):self.edit(target='43',expected=version)
        db.session.rollback();self.edit(action='cancel')
        self.assertEqual(self.pending.status,'cancelled');self.assertIsNone(self.work.off_at_utc);self.assertEqual(self.mission.planned_fuel_load,25000)
    def test_insufficient_truck_reserve_requires_review_without_extra_inventory_post(self):
        self.queue('100');self.complete_parent()
        self.assertEqual(self.pending.status,'review');self.assertIn('reserve',self.pending.review_reason)
        self.assertEqual(self.assignment.current_cycle_number,1);self.assertEqual(self.selection.current_gallons,7000)
    def test_cancellation_after_promotion_uses_existing_cycle_rollback(self):
        self.queue();self.complete_parent()
        self.assertEqual(self.pending.status,'promoted',self.pending.review_reason)
        result=fuel.cancel_uplift(self.gateway,self.dispatcher,self.assignment.id,expected_cycle=2,expected_tail='N491UP')
        db.session.commit();self.assertTrue(result.changed)
        self.assertEqual(self.assignment.current_cycle_number,1);self.assertIsNotNone(self.assignment.completed_at_utc)
        self.assertEqual(self.pending.status,'promoted');self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)
    def test_rolled_forward_sort_rejects_old_pending_mutation(self):
        self.queue()
        other=type(self.operation)(generated_by_user_id=self.dispatcher.id,gateway_id=self.gateway.id,gateway_code=self.gateway.code,sort_date=self.operation.sort_date+timedelta(days=1),sort_name='night',window_minutes=60)
        db.session.add(other);db.session.commit()
        with patch.object(fuel,'current_sort_operation',return_value=other),self.assertRaises(ValueError):self.edit(target='44')
        db.session.rollback();self.assertEqual(self.pending.required_fuel_lbs,40000)
    def test_missing_verified_parent_measurements_retains_review_without_history_rewrite(self):
        self.queue()
        with patch.object(pending,'_attempt_promotion'):self.complete_parent()
        with patch.object(fuel,'_locked_tank_states',return_value=[]):self.edit(action='retry')
        self.assertEqual(self.pending.status,'review');self.assertIn('Actual readings are missing',self.pending.review_reason)
        self.assertIsNotNone(self.assignment.completed_at_utc);self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)
    def test_pending_endpoint_permissions_and_initial_live_panel_render(self):
        self.queue();self.login(self.fueler)
        self.assertEqual(self.client.post('/neoscorpion/fuel-dispatch/pending-uplift',data={'assignment_id':self.assignment.id,'pending_action':'create'}).status_code,403)
        self.login(self.dispatcher)
        response=self.client.get('/neoscorpion/fuel-dispatch/live-panel');self.assertEqual(response.status_code,200)
        self.assertIn('PENDING UPLIFT',response.json['html']);self.assertIn('proposed_truck_id',response.json['html'])
    def test_tail_change_and_missing_verified_tanks_cannot_promote(self):
        self.queue();self.save(self.start+timedelta(minutes=10),**self.final_data());self.off();self.submit()
        with patch.object(pending,'_attempt_promotion'):fuel.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id)
        db.session.commit();self.mission.assigned_tail_number='N492UP';db.session.commit()
        self.edit(action='retry');self.assertEqual(self.pending.status,'review');self.assertIn('tail changed',self.pending.review_reason)
        self.assertEqual(self.assignment.current_cycle_number,1);self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)
