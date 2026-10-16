"""Consolidated October 16 lifecycle contracts, using isolated operational data."""
import copy
import unittest
from datetime import timedelta
from unittest.mock import patch

from app.extensions import db
from app.models import NeoScorpionFuelWorkState, NeoScorpionFuelingEvent, NeoScorpionFuelAuditEntry
from app.services import neoscorpion as fuel
from app.services.neoscorpion_spear import _current_resource_owners
from tests import test_neoscorpion_fueler_submission as cases


class LifecycleAuditTest(unittest.TestCase):
    setUp = cases.SubmissionTest.setUp
    prepare = cases.SubmissionTest.prepare
    save = cases.SubmissionTest.save
    off = cases.SubmissionTest.off
    final_data = cases.SubmissionTest.final_data
    baseline = cases.SubmissionTest.baseline
    submit = cases.SubmissionTest.submit
    row = cases.SubmissionTest.row
    login = cases.SubmissionTest.login

    def finish(self):
        self.save(self.start+timedelta(minutes=10), **self.final_data())
        self.off(); self.submit()
        result=fuel.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id)
        db.session.commit()
        return result

    def start_followup(self, kind='uplift', target='40'):
        result=fuel.start_follow_up_fuel_cycle(self.gateway,self.dispatcher,self.assignment.id,kind,target,
            self.fueler.id,self.truck.id,expected_cycle=self.assignment.current_cycle_number,
            expected_tail=self.mission.assigned_tail_number)
        db.session.commit()
        if kind=='uplift':
            fuel.save_dispatch_assignment(self.gateway,dict(mission_id=self.mission.id,
                assignment_id=self.assignment.id, expected_work_ref=self.assignment.active_work_state_id,
                expected_assigned_fueler_user_id=self.assignment.assigned_fueler_user_id or '',
                expected_assigned_truck_id=self.assignment.assigned_truck_id or '',
                assigned_fueler_user_id=self.fueler.id,assigned_truck_id=self.truck.id))
            db.session.commit()
        return result.fuel_work_state

    def on_current(self):
        result=fuel.confirm_fueler_truck_on(self.gateway,self.fueler,self.assignment.id,
            expected_cycle=self.assignment.current_cycle_number,expected_tail=self.mission.assigned_tail_number,
            expected_truck_id=self.assignment.assigned_truck_id)
        db.session.commit()
        return result

    def test_three_independent_followups_fresh_evidence_and_inventory_once(self):
        self.prepare();self.finish()
        original=NeoScorpionFuelingEvent.query.one()
        original_snapshot=copy.deepcopy(original.performance_snapshot)
        old_ids={original.fuel_work_state_id}
        for cycle in range(2,5):
            with self.subTest(cycle=cycle):
                work=self.start_followup(target=str(30+cycle*5))
                self.assertNotIn(work.id,old_ids);old_ids.add(work.id)
                self.assertTrue(all(t.actual_lbs is None for t in work.tank_states))
                self.assertTrue(all(t.remaining_lbs is not None for t in work.tank_states))
                for name in ('manual_on_at_utc','off_at_utc','apu_running','notes','physical_transfer_gallons','fueler_submitted_at_utc'):
                    self.assertIsNone(getattr(work,name),name)
                self.on_current()
                tanks={t.tank_code:t.remaining_lbs for t in work.tank_states}
                data=dict(assignment_id=self.assignment.id,apu_running='no',transfer_fuel_gallons='100')
                data.update({f'actual_{c}':str((v+(1000 if c!='ctr' else 0))/1000) for c,v in tanks.items()})
                fuel.save_fueler_entry(self.gateway,self.fueler,data);db.session.commit()
                fuel.mark_fueler_off(self.gateway,self.fueler,self.assignment.id);db.session.commit()
                self.assertIsNone(self.assignment.completed_at_utc)
                self.submit();self.assertIsNone(self.assignment.completed_at_utc)
                fuel.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id);db.session.commit()
                self.assertFalse(fuel.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id).changed)
                self.assertEqual(NeoScorpionFuelingEvent.query.count(),cycle)
        self.assertEqual(original.performance_snapshot,original_snapshot)
        self.assertEqual(self.selection.current_gallons,6700)
        self.assertEqual({e.fuel_work_state_id for e in NeoScorpionFuelingEvent.query.all()},old_ids)

    def test_exact_missing_tank_and_apu_blockers_saved_at_off(self):
        self.prepare();self.off()
        self.assertIn('Remaining: LEFT, CTR, RIGHT',self.row()['fueler_completion_reason'])
        self.save(self.start+timedelta(minutes=13),remaining_left='6',remaining_ctr='0',remaining_right='6')
        self.assertIn('Actual: LEFT, CTR, RIGHT',self.row()['fueler_completion_reason'])
        self.save(self.start+timedelta(minutes=14),actual_left='15',actual_ctr='0',actual_right='15')
        self.assertIn('APU',self.row()['fueler_completion_reason'])
        self.save(self.start+timedelta(minutes=15),apu_running='no')
        self.assertIn('Positive T/F',self.row()['fueler_completion_reason'])
        self.save(self.start+timedelta(minutes=16),transfer_fuel_gallons='3000')
        self.assertTrue(self.row()['fueler_completion_ready']);self.assertIsNone(self.assignment.completed_at_utc)

    def test_duplicate_same_baseline_submission_has_one_receipt_audit(self):
        self.prepare();self.save(self.start+timedelta(minutes=10),**self.final_data());self.off()
        expected=self.baseline();first=self.submit(expected)
        receipt=(self.work.fueler_submitted_at_utc,self.work.fueler_submission_version,self.work.fueler_submission_evidence)
        self.assertTrue(first.changed);self.assertFalse(self.submit(expected).changed)
        self.assertEqual(receipt,(self.work.fueler_submitted_at_utc,self.work.fueler_submission_version,self.work.fueler_submission_evidence))
        audits=NeoScorpionFuelAuditEntry.query.filter_by(field_name='fueler_submission').all()
        self.assertEqual(len(audits),1);self.assertEqual(audits[0].fuel_work_state_id,self.work.id)
        self.assertEqual(audits[0].changed_by_user_id,self.fueler.id)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)

    def test_active_on_cannot_be_closed_by_swap_or_unassignment(self):
        self.prepare();other=self.fixture._nightly_truck('ALT');db.session.commit()
        for action in (
            lambda:fuel.unassign_assignment_truck(self.gateway,self.dispatcher,self.assignment.id,
                expected_cycle=1,expected_tail='N491UP',expected_truck_id=self.truck.id),
            lambda:fuel.swap_assignment_truck(self.gateway,self.dispatcher,self.assignment.id,other.id),
        ):
            with self.assertRaisesRegex(ValueError,'physical TRUCK OFF'):action()
            db.session.rollback()
            self.assertIsNone(self.work.off_at_utc);self.assertEqual(self.assignment.assigned_truck_id,self.truck.id)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)

    def test_cancel_both_followups_before_and_after_on_off_preserves_facts(self):
        self.prepare();self.finish()
        prior=self.work;prior_event=NeoScorpionFuelingEvent.query.one()
        for kind in ('uplift','defuel'):
            for stage in ('preparing','on','off'):
                with self.subTest(kind=kind,stage=stage):
                    work=self.start_followup(kind,'40' if kind=='uplift' else '20')
                    if stage!='preparing':self.on_current()
                    if stage=='off':fuel.mark_fueler_off(self.gateway,self.fueler,self.assignment.id);db.session.commit()
                    data=dict(assignment_id=self.assignment.id,apu_running='no',transfer_fuel_gallons='0',notes='Cancel setup only')
                    data.update({f'actual_{t.tank_code}':str(t.remaining_lbs/1000) for t in work.tank_states})
                    fuel.save_fueler_entry(self.gateway,self.fueler,data);db.session.commit()
                    tank_facts=[(t.id,t.remaining_lbs,t.actual_lbs) for t in work.tank_states]
                    physical=(work.manual_on_at_utc,work.off_at_utc)
                    getattr(fuel,'cancel_'+kind)(self.gateway,self.dispatcher,self.assignment.id,
                        expected_cycle=self.assignment.current_cycle_number,expected_tail='N491UP')
                    db.session.commit()
                    self.assertEqual((work.manual_on_at_utc,work.off_at_utc),physical)
                    self.assertEqual([(t.id,t.remaining_lbs,t.actual_lbs) for t in work.tank_states],tank_facts)
                    self.assertIsNotNone(work.physical_finalized_at_utc)
                    self.assertEqual(self.assignment.active_work_state_id,prior.id)
                    self.assertIsNotNone(self.assignment.completed_at_utc)
                    self.assertNotIn(self.truck.id,_current_resource_owners(fuel.fuel_dispatch_context(self.gateway)['rows'])[1])
                    self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)
                    self.assertEqual(prior_event.transfer_fuel_gallons,3000)
                    self.assertEqual(self.selection.current_gallons,7000)

    def test_original_tail_requires_its_own_submission_and_explicit_verification(self):
        self.prepare();self.save(self.start+timedelta(minutes=2),**self.final_data())
        self.mission.assigned_tail_number='N492UP';db.session.commit()
        fuel.confirm_assignment_tail(self.gateway,self.dispatcher,self.assignment.id,expected_cycle=1,expected_tail='N492UP');db.session.commit()
        replacement=self.assignment.active_work_state_id
        identity=dict(physical_work_id=self.work.id,physical_sequence=self.work.physical_sequence_number)
        self.off(**identity)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)
        source=fuel.fueler_context(self.gateway,self.fueler,assignment_id=self.assignment.id,**identity)['rows'][0]
        fuel.submit_fueler_assignment(self.gateway,self.fueler,self.assignment.id,expected=source['edit_baseline'],**identity);db.session.commit()
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)
        self.login(self.dispatcher)
        response=self.client.get(f'/neoscorpion/fuel-dispatch/fueler-data/{self.assignment.id}',query_string=identity)
        self.assertIn('data-source-verification-form',response.json['html'])
        rejected=self.client.post('/neoscorpion/fuel-dispatch/complete',data=dict(assignment_id=self.assignment.id,
            expected_submission_version=self.work.fueler_submission_version-1,**identity))
        self.assertEqual(rejected.status_code,400)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)
        result=self.client.post('/neoscorpion/fuel-dispatch/complete',data=dict(assignment_id=self.assignment.id,**identity))
        self.assertEqual(result.status_code,302)
        self.assertEqual(NeoScorpionFuelingEvent.query.one().fuel_work_state_id,self.work.id)
        self.assertEqual(self.assignment.active_work_state_id,replacement)
        self.assertIsNone(self.assignment.completed_at_utc)
        self.assertIsNone(db.session.get(NeoScorpionFuelWorkState,replacement).off_at_utc)

    def test_tail_changes_before_on_after_off_and_after_submission_preserve_exact_work(self):
        from app.models import NeoScorpionSortFueler
        db.session.add(NeoScorpionSortFueler(sort_date_operation_id=self.operation.id,user_id=self.fueler.id))
        db.session.commit()
        for index,stage in enumerate(('preparing','off','submitted')):
            with self.subTest(stage=stage):
                truck=self.fixture._nightly_truck('SWAP'+str(index))
                mission=self.fixture._mission('UPS90'+str(index+2),'N491UP',25000,index+2)
                assignment=self.fixture._assignment(mission,truck=truck)
                assignment.assigned_fueler_user_id=self.fueler.id;db.session.commit()
                fuel.save_fueler_entry(self.gateway,self.fueler,dict(assignment_id=assignment.id,**self.final_data()));db.session.commit()
                work=db.session.get(NeoScorpionFuelWorkState,assignment.active_work_state_id)
                if stage!='preparing':
                    fuel.confirm_fueler_truck_on(self.gateway,self.fueler,assignment.id,
                        expected_cycle=1,expected_tail='N491UP',expected_truck_id=truck.id)
                    db.session.commit()
                    fuel.mark_fueler_off(self.gateway,self.fueler,assignment.id);db.session.commit()
                identity=dict(physical_work_id=work.id,physical_sequence=work.physical_sequence_number)
                if stage=='submitted':
                    source=fuel.fueler_context(self.gateway,self.fueler,assignment_id=assignment.id,**identity)['rows'][0]
                    fuel.submit_fueler_assignment(self.gateway,self.fueler,assignment.id,expected=source['edit_baseline'],**identity);db.session.commit()
                original=(work.tail_number,work.manual_on_at_utc,work.off_at_utc,work.physical_transfer_gallons,work.fueler_submission_evidence)
                mission.assigned_tail_number='N492UP';db.session.commit()
                fuel.confirm_assignment_tail(self.gateway,self.dispatcher,assignment.id,expected_cycle=1,expected_tail='N492UP');db.session.commit()
                replacement=db.session.get(NeoScorpionFuelWorkState,assignment.active_work_state_id)
                self.assertNotEqual(work.id,replacement.id)
                self.assertEqual((work.tail_number,work.manual_on_at_utc,work.off_at_utc,work.physical_transfer_gallons,work.fueler_submission_evidence),original)
                self.assertTrue(all(t.actual_lbs is None and t.remaining_lbs is None for t in replacement.tank_states))
                self.assertIsNone(replacement.manual_on_at_utc);self.assertIsNone(replacement.physical_transfer_gallons)
                self.assertIsNone(replacement.fueler_submitted_at_utc);self.assertIsNone(assignment.completed_at_utc)
                if stage=='submitted':
                    fuel.complete_fueled_assignment(self.gateway,self.dispatcher,assignment.id,**identity);db.session.commit()
                    self.assertIsNone(assignment.completed_at_utc)
                self.assertFalse(next(r for r in fuel.fuel_dispatch_context(self.gateway)['rows'] if r['assignment'].id==assignment.id)['normal_completion_ready'])

    def test_completion_only_panel_never_waits_for_optional_spear_or_writes(self):
        self.prepare();self.save(self.start+timedelta(minutes=10),**self.final_data());self.off();self.submit()
        self.login(self.dispatcher)
        with patch.object(fuel,'build_spear_plan',side_effect=AssertionError('Optional planner must not run')) as planner, patch.object(db.session,'commit',wraps=db.session.commit) as commit:
            response=self.client.get('/neoscorpion/fuel-dispatch/live-panel?completion_only=1')
        self.assertEqual(response.status_code,200);self.assertEqual(planner.call_count,0);self.assertEqual(commit.call_count,0)
        self.assertIn('>COMPLETE</button>',response.json['html'])
        self.assertIn('expected_submission_version',response.json['html'])

    def test_http_submission_returns_durable_receipt_not_generic_success(self):
        self.prepare();self.save(self.start+timedelta(minutes=10),**self.final_data());self.off()
        self.login(self.fueler)
        import json
        response=self.client.post(f'/neoscorpion/fueler/assignments/{self.assignment.id}/submit',data=dict(expected=json.dumps(self.baseline())))
        self.assertEqual(response.status_code,200)
        receipt=response.json['submission_receipt']
        db.session.expire_all()
        self.assertEqual(receipt['work_id'],self.work.id)
        self.assertEqual(receipt['submitted_at'],self.work.fueler_submitted_at_utc.isoformat())
        self.assertTrue(self.row()['normal_completion_ready'])

    def test_dispatch_stale_form_cannot_verify_a_new_submission(self):
        self.prepare();self.save(self.start+timedelta(minutes=10),**self.final_data());self.off();self.submit()
        version=self.work.fueler_submission_version
        self.save(self.start+timedelta(minutes=15),actual_left='16');self.submit()
        self.login(self.dispatcher)
        response=self.client.post('/neoscorpion/fuel-dispatch/complete',data=dict(assignment_id=self.assignment.id,
            expected_work_ref=self.work.id,expected_cycle=1,expected_tail='N491UP',expected_submission_version=version))
        self.assertEqual(response.status_code,400);self.assertIsNone(self.assignment.completed_at_utc)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)

    def test_changed_departure_target_cannot_accept_old_fueler_screen(self):
        self.prepare();self.save(self.start+timedelta(minutes=10),**self.final_data());self.off()
        old=self.baseline()
        fuel.autosave_dispatch_field(self.gateway,self.mission.id,'required_fuel','26',expected_value='25');db.session.commit()
        with self.assertRaises(fuel.FuelerDataConflict):self.submit(old)
        db.session.rollback();self.assertIsNone(self.work.fueler_submitted_at_utc)
        self.submit();self.assertTrue(self.row()['normal_completion_ready'])

    def test_a_b_a_pending_original_actuals_independent_handoffs_and_postings(self):
        self._a_b_a()

    def test_a_b_a_different_truck_has_independent_inventory_and_history(self):
        self._a_b_a(different_truck=True)

    def _a_b_a(self, different_truck=False):
        self.prepare();self.finish();original=self.work
        frozen=(original.id,original.manual_on_at_utc,original.off_at_utc,original.physical_transfer_gallons)
        self.mission.assigned_tail_number='N492UP';db.session.commit()
        fuel.confirm_assignment_tail(self.gateway,self.dispatcher,self.assignment.id,expected_cycle=1,expected_tail='N492UP');db.session.commit()
        self.on_current();b=db.session.get(NeoScorpionFuelWorkState,self.assignment.active_work_state_id)
        fuel.save_fueler_entry(self.gateway,self.fueler,dict(assignment_id=self.assignment.id,
            remaining_left='7',remaining_ctr='0',remaining_right='7',transfer_fuel_gallons='1000'));db.session.commit()
        self.mission.assigned_tail_number='N491UP';db.session.commit()
        fuel.confirm_assignment_tail(self.gateway,self.dispatcher,self.assignment.id,expected_cycle=1,expected_tail='N491UP');db.session.commit()
        new_a=db.session.get(NeoScorpionFuelWorkState,self.assignment.active_work_state_id)
        self.assertEqual(len({original.id,b.id,new_a.id}),3)
        with self.assertRaisesRegex(ValueError,'TRUCK STILL FUELING'):self.on_current()
        db.session.rollback()
        bid=dict(physical_work_id=b.id,physical_sequence=b.physical_sequence_number)
        fuel.mark_fueler_off(self.gateway,self.fueler,self.assignment.id,**bid);db.session.commit()
        waiting=self.row()
        self.assertEqual(waiting['physical_segment_resources'],[])
        self.assertFalse(waiting['fueling_on_active'])
        self.assertNotEqual(waiting['truck_start_reason'],'TRUCK STILL FUELING')
        # A future reservation remains a SPEAR commitment, not physical ON.
        other=None
        if different_truck:
            other=self.fixture._nightly_truck('ALT',current_gallons=10000);db.session.commit()
            fuel.swap_assignment_truck(self.gateway,self.dispatcher,self.assignment.id,other.id);db.session.commit()
            new_a=db.session.get(NeoScorpionFuelWorkState,self.assignment.active_work_state_id)
        self.on_current()
        fuel.save_fueler_entry(self.gateway,self.fueler,dict(assignment_id=self.assignment.id,
            **{**self.final_data(),'actual_left':'14','actual_right':'14','transfer_fuel_gallons':'2000'}));db.session.commit()
        fuel.mark_fueler_off(self.gateway,self.fueler,self.assignment.id);db.session.commit();self.submit()
        fuel.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id);db.session.commit()
        completed=self.assignment.completed_at_utc
        fuel.save_fueler_entry(self.gateway,self.fueler,dict(assignment_id=self.assignment.id,**bid,
            apu_running='no',actual_left='10',actual_ctr='0',actual_right='10'));db.session.commit()
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),2)
        source=fuel.fueler_context(self.gateway,self.fueler,assignment_id=self.assignment.id,**bid)['rows'][0]
        fuel.submit_fueler_assignment(self.gateway,self.fueler,self.assignment.id,expected=source['edit_baseline'],**bid);db.session.commit()
        fuel.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id,**bid);db.session.commit()
        self.assertEqual((original.id,original.manual_on_at_utc,original.off_at_utc,original.physical_transfer_gallons),frozen)
        self.assertEqual(self.assignment.completed_at_utc,completed)
        events=NeoScorpionFuelingEvent.query.order_by(NeoScorpionFuelingEvent.id).all()
        self.assertEqual([(e.fuel_work_state_id,e.tail_number,e.transfer_fuel_gallons) for e in events],
            [(original.id,'N491UP',3000),(new_a.id,'N491UP',2000),(b.id,'N492UP',1000)])
        expected_balance=6000 if different_truck else 4000
        self.assertEqual(self.selection.current_gallons,expected_balance)
        if other:
            from app.models import NeoScorpionSortTruck
            self.assertEqual(NeoScorpionSortTruck.query.filter_by(fuel_truck_id=other.id).one().current_gallons,8000)
        for event in events:
            self.assertFalse(fuel.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id,
                physical_work_id=event.fuel_work_state_id,physical_sequence=event.sequence_number).changed)
        db.session.commit();self.assertEqual(self.selection.current_gallons,expected_balance)
        from app.services.neoscorpion_rolldown import report_context
        report=report_context(self.gateway,self.operation)
        for event in events:
            transfer=next(e['columns'] for e in report['events'] if e['source']==f'fuel:{event.id}')
            self.assertEqual(transfer['Tail'],event.tail_number)

    def test_replacement_inherits_only_matching_verified_remaining_and_fresh_apu_for_fob(self):
        self.prepare();self.finish()
        mission=self.fixture._mission('UPS902','N491UP',25000,2)
        assignment=self.fixture._assignment(mission)
        assignment.confirmed_tail_number='N492UP';assignment.assigned_fueler_user_id=self.fueler.id
        db.session.commit()
        fuel.confirm_assignment_tail(self.gateway,self.dispatcher,assignment.id,expected_cycle=1,expected_tail='N491UP');db.session.commit()
        work=db.session.get(NeoScorpionFuelWorkState,assignment.active_work_state_id)
        self.assertEqual({t.tank_code:t.remaining_lbs for t in work.tank_states},{'left':15000,'ctr':0,'right':15000})
        self.assertTrue(all(t.actual_lbs is None for t in work.tank_states));self.assertIsNone(work.apu_running)
        row=lambda:next(r for r in fuel.fuel_dispatch_context(self.gateway)['rows'] if r['mission'].id==mission.id)
        self.assertFalse(row()['fuel_on_board_ready'])
        fuel.save_fueler_entry(self.gateway,self.fueler,dict(assignment_id=assignment.id,apu_running='no'));db.session.commit()
        self.assertTrue(row()['fuel_on_board_ready'])
        fuel.complete_fuel_on_board(self.gateway,self.dispatcher,assignment.id);db.session.commit()
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)
        self.assertIsNone(work.manual_on_at_utc);self.assertIsNone(work.off_at_utc)
        self.assertTrue(all(t.actual_lbs is None for t in work.tank_states))

    def test_positive_transfer_in_older_visit_blocks_followup_cancellation(self):
        self.prepare();self.finish();work=self.start_followup()
        work.physical_transfer_gallons=100;db.session.commit()
        # The current projection cannot erase older visit movement evidence.
        from app.services.neoscorpion_segments import new_work
        new_work(self.assignment,'N491UP',self.dispatcher,__import__('datetime').datetime.utcnow());db.session.commit()
        self.assertIsNone(self.assignment.transfer_fuel_gallons)
        with self.assertRaisesRegex(ValueError,'Fuel movement occurred'):
            fuel.cancel_uplift(self.gateway,self.dispatcher,self.assignment.id,expected_cycle=2,expected_tail='N491UP')
        db.session.rollback();self.assertEqual(work.physical_transfer_gallons,100)

    def test_assigned_immediate_and_waiting_for_busy_truck_does_not_start_physical_work(self):
        self.prepare()
        mission=self.fixture._mission('UPS902','N492UP',25000,2)
        assignment=self.fixture._assignment(mission,truck=self.truck)
        assignment.assigned_fueler_user_id=self.fueler.id;db.session.commit()
        fuel.save_fueler_entry(self.gateway,self.fueler,dict(assignment_id=assignment.id,
            remaining_left='6',remaining_ctr='0',remaining_right='6',apu_running='no'));db.session.commit()
        work=db.session.get(NeoScorpionFuelWorkState,assignment.active_work_state_id)
        row=next(r for r in fuel.fuel_dispatch_context(self.gateway)['rows'] if r['mission'].id==mission.id)
        self.assertEqual(row['dispatch_status_key'],'assigned')
        self.assertIn('WAITING FOR TRUCK',row['fuel_status_secondary'])
        self.assertIsNone(work.manual_on_at_utc);self.assertIsNone(work.on_at_utc)
        with self.assertRaisesRegex(ValueError,'TRUCK STILL FUELING'):
            fuel.confirm_fueler_truck_on(self.gateway,self.fueler,assignment.id,
                expected_cycle=1,expected_tail='N492UP',expected_truck_id=self.truck.id)
        db.session.rollback();self.assertIsNone(work.off_at_utc)

    def test_defuel_off_does_not_make_received_fuel_dispatchable_before_verification(self):
        self.prepare();self.finish();work=self.start_followup('defuel','20')
        self.on_current()
        fuel.save_fueler_entry(self.gateway,self.fueler,dict(assignment_id=self.assignment.id,
            apu_running='no',actual_left='10',actual_ctr='0',actual_right='10',transfer_fuel_gallons='1000'));db.session.commit()
        fuel.mark_fueler_off(self.gateway,self.fueler,self.assignment.id);db.session.commit()
        self.assertIsNone(self.assignment.completed_at_utc)
        self.assertEqual(self.selection.status,'needs_sump')
        self.assertIsNotNone(work.off_at_utc)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)  # no premature posting
        self.submit()
        from app.services.neoscorpion_assets import mark_nightly_truck_sumped
        mark_nightly_truck_sumped(self.operation,self.truck,8000,changed_by_user=self.dispatcher);db.session.commit()
        fuel.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id);db.session.commit()
        self.assertEqual(self.selection.status,'available')  # later admin approval must not revoke real sumping
        self.assertEqual(self.selection.current_gallons,8000)  # confirmed reading already covers the transfer

    def test_unresolved_defuel_off_is_free_physically_but_requires_recovery_before_next_on(self):
        self.prepare();self.finish();work=self.start_followup('defuel','20');self.on_current()
        fuel.mark_fueler_off(self.gateway,self.fueler,self.assignment.id);db.session.commit()
        self.assertIsNotNone(work.off_at_utc)
        mission=self.fixture._mission('UPS902','N492UP',25000,2)
        assignment=self.fixture._assignment(mission,truck=self.truck)
        assignment.assigned_fueler_user_id=self.fueler.id;db.session.commit()
        with self.assertRaisesRegex(ValueError,'DEFUEL RECOVERY REVIEW'):
            fuel.confirm_fueler_truck_on(self.gateway,self.fueler,assignment.id,
                expected_cycle=1,expected_tail='N492UP',expected_truck_id=self.truck.id)
        db.session.rollback()
        self.assertIsNone(self.assignment.completed_at_utc)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)

    def test_repeated_destination_labels_never_select_another_mission_visit(self):
        self.prepare();self.save(self.start+timedelta(minutes=1),remaining_left='6',notes='First physical tail')
        other_mission=self.fixture._mission('UPS902','N492UP',25000,2)
        other_mission.destination=self.mission.destination
        other=self.fixture._assignment(other_mission)
        other.assigned_fueler_user_id=self.dispatcher.id;db.session.commit()
        saved=fuel.save_fueler_entry(self.gateway,self.dispatcher,dict(assignment_id=other.id,
            remaining_left='8',notes='Different flight, same destination'));db.session.commit()
        self.assertNotEqual(saved.fuel_work_state.id,self.work.id)
        self.assertEqual(next(t.remaining_lbs for t in self.work.tank_states if t.tank_code=='left'),6000)
        self.assertEqual(self.work.notes,'First physical tail')
        self.assertEqual(saved.fuel_work_state.tail_number,'N492UP')
        self.assertIsNone(saved.fuel_work_state.manual_on_at_utc)
        rows=fuel.fuel_dispatch_context(self.gateway)['rows']
        self.assertEqual({r['mission'].id:r['tail_number'] for r in rows},
            {self.mission.id:'N491UP',other_mission.id:'N492UP'})
