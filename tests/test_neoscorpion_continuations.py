"""Independent submitted/verified visits and planned resource handoff."""
import unittest
from datetime import timedelta
from app.extensions import db
from app.models import NeoScorpionPendingUplift,NeoScorpionFuelWorkState,NeoScorpionFuelingEvent
from app.services import neoscorpion as fuel
from app.services import neoscorpion_pending_uplift as plans
from tests import test_neoscorpion_fueler_submission as base


class ContinuationTest(unittest.TestCase):
    setUp=base.SubmissionTest.setUp
    prepare=base.SubmissionTest.prepare
    save=base.SubmissionTest.save
    off=base.SubmissionTest.off
    final_data=base.SubmissionTest.final_data
    baseline=base.SubmissionTest.baseline
    row=base.SubmissionTest.row
    submit=base.SubmissionTest.submit
    login=base.SubmissionTest.login

    def plan(self,truck=None,**changes):
        if truck is None:truck=self.fixture._nightly_truck('NEXT');db.session.commit()
        form=dict(assignment_id=self.assignment.id,expected_cycle=1,expected_tail='N491UP',
            expected_work_ref=self.assignment.active_work_state_id or '',proposed_truck_id=truck.id,pending_action='create')
        form.update(changes)
        result=plans.plan_continuation(self.gateway,self.dispatcher,form);db.session.commit()
        return result.pending

    def target(self,value):
        old=fuel.format_display_thousands(self.mission.planned_fuel_load)
        fuel.autosave_dispatch_field(self.gateway,self.mission.id,'required_fuel',value,expected_value=old,user=self.dispatcher)
        db.session.commit()

    def handoff(self):
        self.prepare();self.target('40');p=self.plan()
        self.save(self.start+timedelta(minutes=1),**self.final_data());self.off();self.submit()
        return p,db.session.get(NeoScorpionFuelWorkState,self.assignment.active_work_state_id)

    def finish_child(self,p):
        fuel.confirm_fueler_truck_on(self.gateway,self.fueler,self.assignment.id,expected_cycle=1,
            expected_tail='N491UP',expected_truck_id=p.proposed_truck_id);db.session.commit()
        fuel.save_fueler_entry(self.gateway,self.fueler,dict(assignment_id=self.assignment.id,apu_running='no',
            actual_left='21',actual_ctr='0',actual_right='21',transfer_fuel_gallons='2000'));db.session.commit()
        fuel.mark_fueler_off(self.gateway,self.fueler,self.assignment.id);db.session.commit();self.submit()

    def test_explicit_mobile_submit_returns_new_visit_and_duplicate_is_idempotent(self):
        self.prepare();self.target('40');p=self.plan()
        self.save(self.start+timedelta(minutes=1),**self.final_data());self.off();expected=self.baseline()
        self.login(self.fueler)
        import json
        url=f'/neoscorpion/fueler/assignments/{self.assignment.id}/submit?physical_work_id={self.work.id}&physical_sequence={self.work.physical_sequence_number}'
        response=self.client.post(url,data={'expected':json.dumps(expected)},headers={'X-Requested-With':'XMLHttpRequest'})
        self.assertEqual(response.status_code,200,response.get_data(as_text=True))
        self.assertTrue(response.json['next_visit_activated'])
        self.assertIn(f'data-physical-work-id="{p.promoted_work_state_id}"',response.json['html'])
        duplicate=self.client.post(url,data={'expected':json.dumps(expected)},headers={'X-Requested-With':'XMLHttpRequest'})
        self.assertEqual(duplicate.status_code,200,duplicate.get_data(as_text=True))
        self.assertEqual(NeoScorpionFuelWorkState.query.count(),2)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)

    def test_last_visit_verified_first_does_not_hide_source_verification(self):
        p,child=self.handoff();self.finish_child(p)
        fuel.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id);db.session.commit()
        self.assertIsNone(self.assignment.completed_at_utc)
        source=next(r for r in self.row()['console_sources'] if r['physical_work_id']==self.work.id)
        self.assertTrue(source['normal_completion_ready'],source['normal_completion_reason'])
        fuel.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id,
            physical_work_id=self.work.id,physical_sequence=self.work.physical_sequence_number);db.session.commit()
        self.assertIsNotNone(self.assignment.completed_at_utc)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),2)

    def test_correction_cannot_mutate_a_finalized_descendant(self):
        p,child=self.handoff();self.finish_child(p)
        fuel.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id);db.session.commit()
        old=self.work.tank_states[0].actual_lbs;version=self.work.fueler_submission_version
        with self.assertRaisesRegex(ValueError,'verified later visit'):
            fuel.save_fueler_entry(self.gateway,self.fueler,dict(assignment_id=self.assignment.id,
                physical_work_id=self.work.id,physical_sequence=self.work.physical_sequence_number,actual_left='16'))
        db.session.rollback();self.assertEqual(self.work.tank_states[0].actual_lbs,old)
        self.assertEqual(self.work.fueler_submission_version,version)
        self.assertIsNone(child.continuation_review_reason)

    def test_review_accepts_explicit_measured_handoff_without_rewriting_it(self):
        p,child=self.handoff();remaining={t.tank_code:t.remaining_lbs for t in child.tank_states}
        fuel.save_fueler_entry(self.gateway,self.fueler,dict(assignment_id=self.assignment.id,
            physical_work_id=self.work.id,physical_sequence=self.work.physical_sequence_number,actual_left='16'));db.session.commit()
        row=fuel.fueler_context(self.gateway,self.fueler,assignment_id=self.assignment.id,
            physical_work_id=self.work.id,physical_sequence=self.work.physical_sequence_number)['rows'][0]
        fuel.submit_fueler_assignment(self.gateway,self.fueler,self.assignment.id,expected=row['edit_baseline'],
            physical_work_id=self.work.id,physical_sequence=self.work.physical_sequence_number);db.session.commit()
        plans.review_handoff(self.gateway,self.dispatcher,dict(assignment_id=self.assignment.id,pending_id=p.id,
            expected_version=p.version,pending_action='resolve_handoff',reason='Physically measured next visit',confirm_remaining_verified='1'));db.session.commit()
        self.assertIsNone(child.continuation_review_reason)
        self.assertEqual({t.tank_code:t.remaining_lbs for t in child.tank_states},remaining)
        self.assertEqual(p.handoff_snapshot['review']['actor'],self.dispatcher.id)

    def test_corrected_source_http_receipt_does_not_duplicate_current_child_card(self):
        import json
        p,child=self.handoff()
        fuel.save_fueler_entry(self.gateway,self.fueler,dict(assignment_id=self.assignment.id,
            physical_work_id=self.work.id,physical_sequence=self.work.physical_sequence_number,actual_left='16'));db.session.commit()
        source=fuel.fueler_context(self.gateway,self.fueler,assignment_id=self.assignment.id,
            physical_work_id=self.work.id,physical_sequence=self.work.physical_sequence_number)['rows'][0]
        self.login(self.fueler)
        response=self.client.post(f'/neoscorpion/fueler/assignments/{self.assignment.id}/submit?physical_work_id={self.work.id}&physical_sequence={self.work.physical_sequence_number}',
            data={'expected':json.dumps(source['edit_baseline'])},headers={'X-Requested-With':'XMLHttpRequest'})
        self.assertEqual(response.status_code,200,response.get_data(as_text=True))
        self.assertFalse(response.json['next_visit_activated'])
        self.assertIn(f'data-physical-work-id="{self.work.id}"',response.json['html'])
        self.assertNotIn(f'data-physical-work-id="{child.id}"',response.json['html'])

    def test_three_action_center_items_remain_independent_when_one_is_resolved(self):
        p,child=self.handoff()
        fuel.save_fueler_entry(self.gateway,self.fueler,dict(assignment_id=self.assignment.id,
            physical_work_id=self.work.id,physical_sequence=self.work.physical_sequence_number,actual_left='16'));db.session.commit()
        source=fuel.fueler_context(self.gateway,self.fueler,assignment_id=self.assignment.id,
            physical_work_id=self.work.id,physical_sequence=self.work.physical_sequence_number)['rows'][0]
        fuel.submit_fueler_assignment(self.gateway,self.fueler,self.assignment.id,expected=source['edit_baseline'],
            physical_work_id=self.work.id,physical_sequence=self.work.physical_sequence_number);db.session.commit()
        fuel.save_fueler_entry(self.gateway,self.fueler,dict(assignment_id=self.assignment.id,actual_left='18'));db.session.commit()
        self.target('45');next_plan=plans.outstanding(self.assignment.id)
        self.assertEqual(self.row()['action_count'],3)
        self.plan(self.truck,pending_action='cancel',pending_id=next_plan.id,expected_version=next_plan.version)
        row=self.row();self.assertEqual(row['action_count'],2)
        self.assertTrue(row['console_sources'][0]['normal_completion_ready'])
        self.assertEqual(len(row['console_dependencies']),1)

    def test_decrease_reconciles_existing_plan_no_new_visit(self):
        self.prepare();self.target('40');p=self.plan();self.save(self.start+timedelta(minutes=1),**self.final_data())
        self.target('28');self.assertEqual(p.status,'review');self.off();self.submit()
        self.assertEqual(NeoScorpionPendingUplift.query.count(),1)
        self.assertEqual(NeoScorpionFuelWorkState.query.count(),1)
        self.assertEqual(p.required_fuel_lbs,28000);self.assertEqual(p.status,'review')
        self.assertIn('NOT REQUIRED',p.review_reason)

    def test_reduced_target_after_activation_requires_explicit_cancel_and_preserves_source(self):
        p,child=self.handoff();self.target('28')
        self.assertIn('NOT REQUIRED',child.continuation_review_reason)
        self.assertEqual(self.assignment.active_work_state_id,child.id)
        plans.review_handoff(self.gateway,self.dispatcher,dict(assignment_id=self.assignment.id,pending_id=p.id,
            expected_version=p.version,pending_action='cancel_unstarted_handoff',reason='Source measured fuel meets reduced target'));db.session.commit()
        self.assertEqual(self.assignment.active_work_state_id,self.work.id)
        self.assertIsNone(child.manual_on_at_utc);self.assertIsNone(child.off_at_utc)
        self.assertIsNotNone(child.physical_finalized_at_utc);self.assertEqual(p.status,'cancelled')
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)
        fuel.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id);db.session.commit()
        self.assertIsNotNone(self.assignment.completed_at_utc);self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)
        self.assertEqual(self.mission.planned_fuel_load,28000)

    def test_reduced_target_never_cancels_a_physically_started_child(self):
        p,child=self.handoff()
        fuel.confirm_fueler_truck_on(self.gateway,self.fueler,self.assignment.id,expected_cycle=1,expected_tail='N491UP',expected_truck_id=p.proposed_truck_id);db.session.commit()
        self.target('28');on=child.manual_on_at_utc
        with self.assertRaisesRegex(ValueError,'physically started'):
            plans.review_handoff(self.gateway,self.dispatcher,dict(assignment_id=self.assignment.id,pending_id=p.id,
                expected_version=p.version,pending_action='cancel_unstarted_handoff',reason='Target reduced'))
        db.session.rollback();self.assertEqual(child.manual_on_at_utc,on);self.assertEqual(p.status,'promoted')
        fuel.mark_fueler_off(self.gateway,self.fueler,self.assignment.id);db.session.commit()
        self.assertIsNotNone(child.off_at_utc)

    def test_missing_planned_asset_preserves_successful_source_submission(self):
        from app.models import NeoScorpionSortTruck
        self.prepare();self.target('40');p=self.plan()
        NeoScorpionSortTruck.query.filter_by(fuel_truck_id=p.proposed_truck_id).delete();db.session.commit()
        self.save(self.start+timedelta(minutes=1),**self.final_data());self.off();self.submit()
        self.assertEqual(p.status,'review');self.assertIn('NEXT TRUCK NEEDS REVIEW',p.review_reason)
        self.assertTrue(self.row()['normal_completion_ready']);self.assertEqual(self.assignment.active_work_state_id,self.work.id)

    def test_tail_swap_cannot_carry_source_actuals_into_replacement(self):
        self.prepare();self.target('40');p=self.plan()
        self.save(self.start+timedelta(minutes=1),**self.final_data());self.off()
        self.mission.assigned_tail_number='N492UP';db.session.commit()
        fuel.confirm_assignment_tail(self.gateway,self.dispatcher,self.assignment.id,expected_cycle=1,expected_tail='N492UP');db.session.commit()
        source=fuel.fueler_context(self.gateway,self.fueler,assignment_id=self.assignment.id,
            physical_work_id=self.work.id,physical_sequence=self.work.physical_sequence_number)['rows'][0]
        fuel.submit_fueler_assignment(self.gateway,self.fueler,self.assignment.id,expected=source['edit_baseline'],
            physical_work_id=self.work.id,physical_sequence=self.work.physical_sequence_number);db.session.commit()
        self.assertNotEqual(p.status,'promoted');self.assertIsNone(p.promoted_work_state_id)
        current=fuel.current_work(self.assignment,'N492UP')
        self.assertFalse(current and any(t.actual_lbs is not None for t in current.tank_states))
        self.assertEqual(self.work.tail_number,'N491UP')

    def test_action_center_has_exact_source_receipt_and_mobile_readonly_details(self):
        p,child=self.handoff();self.login(self.dispatcher)
        response=self.client.get('/neoscorpion/fuel-dispatch');self.assertEqual(response.status_code,200)
        html=response.get_data(as_text=True)
        self.assertIn('data-dispatch-action-center',html);self.assertIn(f'data-action-key="verify-{self.work.id}"',html)
        self.assertIn('expected_submission_version',html);self.assertIn('PLAN ANOTHER TRUCK',html)
        self.assertNotIn('ADD PENDING UPLIFT',html);self.assertNotIn('data-source-verification-form',html)
        self.assertIn('scorpion-mobile-readonly',html)

    def test_stale_plan_edit_rejected_and_no_duplicate_line(self):
        self.prepare();p=self.plan()
        with self.assertRaises(plans.PendingUpliftConflict):self.plan(self.truck)
        db.session.rollback();self.assertEqual(NeoScorpionPendingUplift.query.count(),1)
        with self.assertRaises(plans.PendingUpliftConflict):self.plan(self.truck,pending_action='save',pending_id=p.id,expected_version=p.version-1)
        db.session.rollback();self.assertEqual(p.status,'pending')

    def test_three_visits_a_b_a_preserve_each_receipt_tanks_and_inventory_event(self):
        p,b=self.handoff();first_id=self.work.id
        fuel.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id,
            physical_work_id=first_id,physical_sequence=self.work.physical_sequence_number);db.session.commit()
        third_plan=self.plan(self.truck)
        fuel.confirm_fueler_truck_on(self.gateway,self.fueler,self.assignment.id,expected_cycle=1,expected_tail='N491UP',expected_truck_id=p.proposed_truck_id);db.session.commit()
        fuel.save_fueler_entry(self.gateway,self.fueler,dict(assignment_id=self.assignment.id,apu_running='no',
            actual_left='18',actual_ctr='0',actual_right='18',transfer_fuel_gallons='1000'));db.session.commit()
        fuel.mark_fueler_off(self.gateway,self.fueler,self.assignment.id);db.session.commit();self.submit()
        third=db.session.get(NeoScorpionFuelWorkState,self.assignment.active_work_state_id)
        self.assertEqual(third_plan.status,'promoted');self.assertNotIn(third.id,(first_id,b.id))
        self.assertEqual({t.tank_code:t.remaining_lbs for t in third.tank_states},{'left':18000,'ctr':0,'right':18000})
        self.assertTrue(all(t.actual_lbs is None for t in third.tank_states))
        fuel.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id,
            physical_work_id=b.id,physical_sequence=b.physical_sequence_number);db.session.commit()
        self.finish_child(third_plan)
        fuel.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id);db.session.commit()
        events=NeoScorpionFuelingEvent.query.order_by(NeoScorpionFuelingEvent.id).all()
        self.assertEqual([e.fuel_work_state_id for e in events],[first_id,b.id,third.id])
        self.assertEqual([e.transfer_fuel_gallons for e in events],[3000,1000,2000])
        self.assertEqual([e.fuel_truck_id for e in events],[self.truck.id,p.proposed_truck_id,self.truck.id])
        self.assertEqual({t.tank_code:t.actual_lbs for t in self.work.tank_states},{'left':15000,'ctr':0,'right':15000})
        self.assertIsNotNone(self.assignment.completed_at_utc)

    def test_busy_next_truck_can_be_planned_but_cannot_start_until_real_off(self):
        from app.models import NeoScorpionSortFueler
        self.prepare();self.target('40')
        truck=self.fixture._nightly_truck('BUSY')
        operator=self.fixture._login_user('other-physical-fueler','operator')
        db.session.add(NeoScorpionSortFueler(sort_date_operation_id=self.operation.id,user_id=operator.id))
        other=self.fixture._assignment(self.fixture._mission('UPS902','N492UP',25000,2),truck)
        other.assigned_fueler_user_id=operator.id;db.session.commit()
        fuel.confirm_fueler_truck_on(self.gateway,operator,other.id,expected_cycle=1,expected_tail='N492UP',expected_truck_id=truck.id);db.session.commit()
        p=self.plan(truck);self.save(self.start+timedelta(minutes=1),**self.final_data());self.off();self.submit()
        self.assertEqual(p.status,'promoted')
        with self.assertRaisesRegex(ValueError,'TRUCK STILL FUELING'):
            fuel.confirm_fueler_truck_on(self.gateway,self.fueler,self.assignment.id,expected_cycle=1,expected_tail='N491UP',expected_truck_id=truck.id)
        db.session.rollback();r=self.row()
        self.assertIn('WAITING FOR TRUCK',r['dispatch_status_label']+' '+r['dispatch_status_detail'])
        fuel.mark_fueler_off(self.gateway,operator,other.id);db.session.commit()
        fuel.confirm_fueler_truck_on(self.gateway,self.fueler,self.assignment.id,expected_cycle=1,expected_tail='N491UP',expected_truck_id=truck.id);db.session.commit()
        self.assertIsNotNone(db.session.get(NeoScorpionFuelWorkState,self.assignment.active_work_state_id).manual_on_at_utc)

    def test_old_tail_plan_can_be_cancelled_without_changing_physical_source(self):
        self.prepare();self.target('40');p=self.plan()
        self.save(self.start+timedelta(minutes=1),remaining_left='6',actual_left='15')
        self.mission.assigned_tail_number='N492UP';db.session.commit()
        fuel.confirm_assignment_tail(self.gateway,self.dispatcher,self.assignment.id,expected_cycle=1,expected_tail='N492UP');db.session.commit()
        self.plan(self.truck,pending_action='cancel',pending_id=p.id,expected_version=p.version,expected_tail='N492UP')
        self.assertEqual(p.status,'cancelled');self.assertIsNone(self.work.off_at_utc)
        self.assertEqual(self.work.tail_number,'N491UP');self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)

    def test_frozen_source_off_after_tail_swap_uses_its_own_physical_identity(self):
        self.prepare();self.save(self.start+timedelta(minutes=1),remaining_left='6',actual_left='15');self.target('40')
        self.mission.assigned_tail_number='N492UP';db.session.commit()
        fuel.confirm_assignment_tail(self.gateway,self.dispatcher,self.assignment.id,expected_cycle=1,expected_tail='N492UP');db.session.commit()
        self.off(physical_work_id=self.work.id,physical_sequence=self.work.physical_sequence_number)
        self.assertIsNotNone(self.work.off_at_utc);self.assertEqual(self.work.physical_required_fuel_lbs,25000)
        self.assertEqual(self.work.tail_number,'N491UP');self.assertIsNone(self.assignment.completed_at_utc)

    def test_failed_handoff_rolls_back_receipt_and_resource_changes(self):
        from unittest.mock import patch
        self.prepare();self.target('40');p=self.plan();self.save(self.start+timedelta(minutes=1),**self.final_data());self.off()
        with patch.object(fuel,'new_work',side_effect=ValueError('Injected handoff failure')):
            with self.assertRaisesRegex(ValueError,'Injected'):self.submit()
        db.session.rollback()
        self.assertIsNone(self.work.fueler_submitted_at_utc);self.assertEqual(self.assignment.assigned_truck_id,self.truck.id)
        self.assertEqual(self.assignment.active_work_state_id,self.work.id);self.assertEqual(p.status,'pending')
        self.assertEqual(NeoScorpionFuelWorkState.query.count(),1);self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)
        self.submit();self.assertEqual(p.status,'promoted')

    def test_plan_route_requires_dispatch_permissions_and_current_visit(self):
        from unittest.mock import patch
        self.prepare();self.login(self.fueler)
        with patch('app.neonodes.neoscorpion.routes.permission_access',return_value={'can_view':True,'can_edit':False}):
            response=self.client.post('/neoscorpion/fuel-dispatch/pending-uplift',data=dict(plan_kind='continuation',assignment_id=self.assignment.id))
        self.assertEqual(response.status_code,403);self.assertEqual(NeoScorpionPendingUplift.query.count(),0)
        with self.assertRaises(plans.PendingUpliftConflict):self.plan(expected_work_ref=self.work.id+999)
        db.session.rollback();self.assertEqual(NeoScorpionPendingUplift.query.count(),0)

    def test_sort_rollover_never_activates_old_planned_truck(self):
        from unittest.mock import patch
        from app.models import SortDateOperation
        self.prepare();self.target('40');p=self.plan();self.save(self.start+timedelta(minutes=1),**self.final_data());self.off()
        other=SortDateOperation(generated_by_user_id=self.dispatcher.id,gateway_id=self.gateway.id,gateway_code=self.gateway.code,
            sort_date=self.operation.sort_date+timedelta(days=1),sort_name='night',window_minutes=60)
        db.session.add(other);db.session.commit();old=self.baseline()
        with patch.object(fuel,'current_sort_operation',return_value=other):
            with self.assertRaises(ValueError):self.submit(old)
        db.session.rollback();self.assertEqual(p.status,'pending')
        self.assertIsNone(p.promoted_work_state_id);self.assertIsNone(self.work.fueler_submitted_at_utc)

    def test_same_truck_reverse_verification_after_baseline_correction_keeps_unposted_usage_unverified(self):
        from app.services.neoscorpion_inventory import correct_baseline
        self.prepare();self.target('40');p=self.plan(self.truck)
        self.save(self.start+timedelta(minutes=1),**self.final_data());self.off();self.submit()
        fuel.confirm_fueler_truck_on(self.gateway,self.fueler,self.assignment.id,expected_cycle=1,expected_tail='N491UP',expected_truck_id=self.truck.id);db.session.commit()
        correct_baseline(self.gateway,self.operation,self.dispatcher,self.truck.id,'5500',self.selection.inventory_epoch);db.session.commit()
        fuel.save_fueler_entry(self.gateway,self.fueler,dict(assignment_id=self.assignment.id,apu_running='no',
            actual_left='21',actual_ctr='0',actual_right='21',transfer_fuel_gallons='2000'));db.session.commit()
        fuel.mark_fueler_off(self.gateway,self.fueler,self.assignment.id);db.session.commit();self.submit()
        fuel.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id);db.session.commit()
        self.assertEqual(self.selection.current_gallons,3500)
        self.assertFalse(self.selection.inventory_verified,'Older unposted source transfer is not covered by a baseline correction.')
        fuel.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id,
            physical_work_id=self.work.id,physical_sequence=self.work.physical_sequence_number);db.session.commit()
        self.assertEqual(self.selection.current_gallons,500);self.assertTrue(self.selection.inventory_verified)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),2)

    def test_increase_without_actual_stays_same_work_and_notifies(self):
        self.prepare();self.save(self.start+timedelta(minutes=1),remaining_left='6',apu_running='no')
        old=(self.work.id,self.work.manual_on_at_utc,self.work.apu_running)
        self.target('40')
        self.assertEqual((self.assignment.active_work_state_id,self.work.manual_on_at_utc,self.work.apu_running),old)
        self.assertEqual(NeoScorpionPendingUplift.query.count(),0)
        self.assertIn('25.0 -> 40.0',self.assignment.fueler_update_message)

    def test_increase_after_off_preserves_visit_while_decrease_alone_creates_no_plan(self):
        self.prepare();self.save(self.start+timedelta(minutes=1),**self.final_data());self.off()
        on,off=self.work.manual_on_at_utc,self.work.off_at_utc
        self.target('24');self.assertEqual(NeoScorpionPendingUplift.query.count(),0)
        self.target('40');self.assertEqual(NeoScorpionPendingUplift.query.count(),1)
        self.assertEqual((self.work.manual_on_at_utc,self.work.off_at_utc),(on,off))
        self.assertEqual(self.work.physical_required_fuel_lbs,24000)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)

    def test_zero_actual_is_saved_measurement_and_defuel_cannot_be_mixed_with_continuation(self):
        self.prepare();self.save(self.start+timedelta(minutes=1),remaining_ctr='0',actual_ctr='0')
        self.target('40');p=plans.outstanding(self.assignment.id)
        self.assertIsNotNone(p);self.plan(self.truck,pending_action='cancel',pending_id=p.id,expected_version=p.version)
        self.save(self.start+timedelta(minutes=2),**self.final_data());self.off();self.submit()
        fuel.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id);db.session.commit()
        fuel.start_follow_up_fuel_cycle(self.gateway,self.dispatcher,self.assignment.id,'defuel','20',
            self.fueler.id,self.truck.id,expected_cycle=1,expected_tail='N491UP');db.session.commit()
        with self.assertRaisesRegex(ValueError,'DEFUEL'):
            self.plan(self.truck,expected_cycle=2)
        db.session.rollback();self.assertEqual(self.assignment.current_cycle_type,'defuel')
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)

    def test_one_actual_increase_reconciles_one_plan_and_freezes_target(self):
        self.prepare();self.save(self.start+timedelta(minutes=1),remaining_left='6',actual_left='15')
        on=self.work.manual_on_at_utc;self.target('40');self.target('41')
        p=NeoScorpionPendingUplift.query.one()
        self.assertEqual(p.plan_kind,'increase');self.assertEqual(p.required_fuel_lbs,41000)
        self.assertEqual(self.work.physical_required_fuel_lbs,25000)
        self.assertEqual(self.work.manual_on_at_utc,on);self.assertIsNone(self.work.off_at_utc)
        self.assertEqual(self.assignment.active_work_state_id,self.work.id)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)
        self.mission.planned_datetime_utc+=timedelta(hours=2);db.session.commit()
        self.assertEqual(self.row()['mission'].planned_datetime_utc,self.mission.planned_datetime_utc)

    def test_second_truck_activates_on_submission_and_both_verify_independently(self):
        self.prepare();self.target('40');p=self.plan();first=self.work
        self.save(self.start+timedelta(minutes=1),**self.final_data());self.off()
        self.assertEqual(p.status,'pending');self.assertEqual(self.assignment.active_work_state_id,first.id)
        self.submit()
        self.assertEqual(p.status,'promoted',p.review_reason)
        second=db.session.get(NeoScorpionFuelWorkState,self.assignment.active_work_state_id)
        self.assertNotEqual(first.id,second.id);self.assertEqual(self.assignment.current_cycle_number,1)
        self.assertEqual({t.tank_code:t.remaining_lbs for t in second.tank_states},{'left':15000,'ctr':0,'right':15000})
        self.assertTrue(all(t.actual_lbs is None for t in second.tank_states))
        self.assertIsNone(second.manual_on_at_utc);self.assertIsNone(second.apu_running);self.assertIsNone(second.physical_transfer_gallons)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)
        fuel.confirm_fueler_truck_on(self.gateway,self.fueler,self.assignment.id,expected_cycle=1,expected_tail='N491UP',expected_truck_id=p.proposed_truck_id);db.session.commit()
        identity=dict(physical_work_id=first.id,physical_sequence=first.physical_sequence_number)
        fuel.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id,**identity);db.session.commit()
        self.assertIsNotNone(first.physical_finalized_at_utc);self.assertIsNone(self.assignment.completed_at_utc)
        self.assertEqual(self.assignment.active_work_state_id,second.id)
        fuel.save_fueler_entry(self.gateway,self.fueler,dict(assignment_id=self.assignment.id,apu_running='no',
            actual_left='21',actual_ctr='0',actual_right='21',transfer_fuel_gallons='2000'));db.session.commit()
        fuel.mark_fueler_off(self.gateway,self.fueler,self.assignment.id);db.session.commit();self.submit()
        fuel.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id);db.session.commit()
        self.assertIsNotNone(self.assignment.completed_at_utc)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),2)

    def test_not_required_second_truck_stays_review_until_dispatcher_cancels(self):
        self.prepare();p=self.plan();self.save(self.start+timedelta(minutes=1),**self.final_data());self.off();self.submit()
        self.assertEqual(p.status,'review');self.assertIn('NOT REQUIRED',p.review_reason)
        fuel.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id);db.session.commit()
        self.assertIsNone(self.assignment.completed_at_utc)
        self.plan(self.truck,pending_action='cancel',pending_id=p.id,expected_version=p.version)
        self.assertIsNotNone(self.assignment.completed_at_utc);self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)

    def test_source_correction_never_rewrites_started_child(self):
        self.prepare();self.target('40');p=self.plan();self.save(self.start+timedelta(minutes=1),**self.final_data());self.off();self.submit()
        second=db.session.get(NeoScorpionFuelWorkState,self.assignment.active_work_state_id)
        readings=[(t.remaining_lbs,t.actual_lbs) for t in second.tank_states]
        fuel.confirm_fueler_truck_on(self.gateway,self.fueler,self.assignment.id,expected_cycle=1,expected_tail='N491UP',expected_truck_id=p.proposed_truck_id);db.session.commit()
        fuel.save_fueler_entry(self.gateway,self.fueler,dict(assignment_id=self.assignment.id,physical_work_id=self.work.id,
            physical_sequence=self.work.physical_sequence_number,actual_left='16'));db.session.commit()
        self.assertEqual([(t.remaining_lbs,t.actual_lbs) for t in second.tank_states],readings)
        self.assertIn('SOURCE MEASUREMENT CHANGED',second.continuation_review_reason)
        fuel.mark_fueler_off(self.gateway,self.fueler,self.assignment.id);db.session.commit()
        self.assertIsNotNone(second.off_at_utc)
