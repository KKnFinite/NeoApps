"""Canonical personnel handoffs never allocate another physical visit."""
from datetime import timedelta
import copy
import json
import unittest

from app.extensions import db
from app.models import (NeoScorpionSortFueler, NeoScorpionFuelWorkState, NeoScorpionFuelingEvent,
                        NeoScorpionFuelAuditEntry, NeoScorpionTruckInventoryAudit)
from app.services import neoscorpion as fuel
from app.services.neoscorpion_prof import event_math, load_jobs, load_observation_jobs, totals
from app.services import neoscorpion_participation as participation
from tests import test_neoscorpion_direct_entry as fixtures


class FuelerHandoffTest(unittest.TestCase):
    setUp = fixtures.DirectEntryTest.setUp
    prepare = fixtures.DirectEntryTest.prepare
    save = fixtures.DirectEntryTest.save
    final_data = fixtures.DirectEntryTest.final_data
    login = fixtures.DirectEntryTest.login

    def replacement(self, name='replacement-b'):
        user = self.fixture._login_user(name,'operator')
        db.session.add(NeoScorpionSortFueler(sort_date_operation_id=self.operation.id,user_id=user.id))
        db.session.commit()
        return user

    def row(self, user=None, dispatcher=False):
        ctx = fuel.fueler_context(self.gateway,user or self.fueler,assignment_id=self.assignment.id,dispatcher=dispatcher)
        return next(r for r in ctx['rows'] if r['physical_work_id'] == self.work.id)

    def swap(self, user):
        at=self.start+timedelta(minutes=4)
        work=getattr(self,'work',None)
        if work and work.manual_on_at_utc: at=max(at,work.manual_on_at_utc+timedelta(minutes=2))
        if work and work.off_at_utc: at=max(at,work.off_at_utc+timedelta(minutes=1))
        result = fuel.swap_assignment_fueler(self.gateway,self.dispatcher,self.assignment.id,user.id,
                                             now_utc=at)
        db.session.commit()
        return result

    def state(self):
        w=self.work
        return dict(id=w.id,visit=w.visit_number,sequence=w.physical_sequence_number,
            cycle=w.physical_cycle_number,truck=w.physical_truck_id,original_fueler=w.physical_fueler_user_id,
            on=w.manual_on_at_utc,off=w.off_at_utc,off_actor=w.off_by_user_id,
            apu=w.apu_running,source=w.apu_source_tank_code,automatic=w.automatic_apu_allowance_lbs,
            override=w.apu_override_allowance_lbs,allowance=w.apu_allowance_lbs,notes=w.notes,
            transfer=w.physical_transfer_gallons,tanks=sorted((t.tank_code,t.remaining_lbs,t.actual_lbs) for t in w.tank_states))

    def submit(self, user):
        result=fuel.submit_fueler_assignment(self.gateway,user,self.assignment.id,expected=self.row(user)['edit_baseline'])
        db.session.commit()
        return result

    def complete(self, user):
        fuel.mark_fueler_off(self.gateway,user,self.assignment.id,now_utc=self.start+timedelta(minutes=12))
        db.session.commit()
        self.submit(user)
        fuel.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id)
        db.session.commit()
        return NeoScorpionFuelingEvent.query.filter_by(fuel_work_state_id=self.work.id).one()

    def test_active_handoff_preserves_all_saved_values_and_original_identity(self):
        self.prepare()
        data=self.final_data();data.update(apu_running='yes',apu_source_tank_code='left',
             apu_override_present='1',apu_override_enabled='1',apu_override_allowance='0.5',notes='Saved by A')
        self.save(self.start+timedelta(minutes=1),**data)
        before=self.state(); inventory=self.selection.current_gallons
        actor=self.replacement()
        result=self.swap(actor)
        self.assertEqual(result.fuel_work_state.id,self.work.id)
        self.assertEqual(self.state(),before)
        row=self.row(actor)
        self.assertEqual(row['remaining_total_display'],'12.0');self.assertEqual(row['actual_total_display'],'30.0')
        self.assertEqual(row['fueler_notes'],'Saved by A');self.assertTrue(row['manual_on_confirmed'])
        self.assertTrue(row['assignment_update_pending'])
        self.assertTrue(row['apu_override_enabled']);self.assertEqual(row['apu_source_tank_code'],'left')
        self.assertEqual(NeoScorpionFuelWorkState.query.count(),1)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)
        self.assertEqual(self.selection.current_gallons,inventory)
        old_baseline=copy.deepcopy(row['edit_baseline']);old_baseline['assigned_fueler_user_id']=self.fueler.id
        with self.assertRaises(ValueError):
            fuel.save_fueler_entry(self.gateway,self.fueler,dict(assignment_id=self.assignment.id,
                physical_work_id=self.work.id,physical_sequence=self.work.physical_sequence_number,
                expected=json.dumps(old_baseline),actual_left='99'))
        db.session.rollback()
        fuel.save_fueler_entry(self.gateway,actor,dict(assignment_id=self.assignment.id,
            physical_work_id=self.work.id,physical_sequence=self.work.physical_sequence_number,
            expected=json.dumps(self.row(actor)['edit_baseline']),notes='B continues'))
        db.session.commit()
        self.assertEqual(self.state()['tanks'],before['tanks'])
        event=self.complete(actor)
        self.assertEqual(event.fuel_work_state_id,before['id'])
        self.assertEqual(event.fueler_user_id,self.fueler.id)
        self.assertEqual(self.work.fueler_submitted_by_user_id,actor.id)
        self.assertEqual(self.selection.current_gallons,inventory-3000)
        self.assertIn(participation.MULTIPLE_FUELERS,event_math(event)['fueler_reasons'])
        self.assertIsNone(event_math(event)['truck_prof'])
        from app.services.neoscorpion_spear_learning_capture import build_completed_learning_record
        from app.services.neoscorpion_learning_vault import _validate_learning_record
        record=build_completed_learning_record(self.gateway,self.operation,self.assignment,self.mission)
        _validate_learning_record(record)
        self.assertFalse(record['training_eligible'])
        self.assertIsNone(record['operational_timelines'][0]['fueler_prof'])

    def test_off_handoff_needs_no_additional_clock_and_submission_locks_swap(self):
        self.prepare();self.save(self.start+timedelta(minutes=1),**self.final_data())
        fuel.mark_fueler_off(self.gateway,self.fueler,self.assignment.id,now_utc=self.start+timedelta(minutes=12));db.session.commit()
        before=self.state();actor=self.replacement();self.swap(actor)
        self.assertEqual(self.state(),before)
        self.assertTrue(self.row(actor)['fueler_completion_ready'])
        self.submit(actor)
        for person in (self.fueler,actor):
            with self.subTest(person=person.id),self.assertRaisesRegex(ValueError,'locked after COMPLETE ASSIGNMENT'):
                self.swap(person)
            db.session.rollback()
        self.assertEqual(self.assignment.assigned_fueler_user_id,actor.id)
        fuel.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id);db.session.commit()
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)
        self.assertEqual(self.work.off_at_utc,before['off'])

    def test_pre_on_handoff_keeps_preparation_and_multiple_successive_contributors(self):
        from datetime import datetime
        self.start=datetime.utcnow()-timedelta(minutes=30)
        self.truck=self.fixture._nightly_truck()
        self.assignment.assigned_fueler_user_id=self.fueler.id;self.assignment.assigned_truck_id=self.truck.id
        db.session.add(NeoScorpionSortFueler(sort_date_operation_id=self.operation.id,user_id=self.fueler.id));db.session.commit()
        result=fuel.save_fueler_entry(self.gateway,self.fueler,dict(assignment_id=self.assignment.id,
            remaining_left='6',remaining_ctr='0',remaining_right='6',apu_running='no',notes='Preparation'),
            now_utc=self.start+timedelta(minutes=1))
        db.session.commit();self.work=result.fuel_work_state
        before=self.state();b=self.replacement();c=self.replacement('replacement-c')
        self.swap(b);self.swap(c)
        self.assertEqual(self.state(),before)
        self.assertEqual(self.row(c)['fueler_notes'],'Preparation')
        self.assertEqual(self.work.performance_context['participation']['fueler_ids'],[self.fueler.id])
        fuel.confirm_fueler_truck_on(self.gateway,c,self.assignment.id,expected_cycle=1,expected_tail='N491UP',
            expected_truck_id=self.truck.id,now_utc=self.start+timedelta(minutes=5));db.session.commit()
        self.assertEqual(self.work.physical_fueler_user_id,c.id)
        self.assertEqual(self.work.performance_context['participation']['fueler_ids'],sorted([self.fueler.id,c.id]))
        self.assertEqual(NeoScorpionFuelWorkState.query.count(),1)

    def test_single_resource_cycle_and_pre_work_swap_remain_eligible(self):
        actor=self.fixture._login_user('single-operator','operator')
        self.fueler=actor
        self.prepare();self.save(self.start+timedelta(minutes=1),**self.final_data())
        # The pre-work assignment is represented by the actual first operator;
        # no invented participant or planned truck enters the physical ledger.
        event=self.complete(actor)
        assessment=participation.cycles([event])[participation.key(event)]
        self.assertEqual(participation.reasons(assessment),[])
        self.assertIsNotNone(event_math(event)['fueler_prof'])
        self.assertEqual(totals(load_jobs(self.gateway.id,operation_id=self.operation.id),'fueler')['jobs'],1)
        self.assertEqual(load_observation_jobs(self.gateway.id,operation_id=self.operation.id)[0]['fueler_prof'],event_math(event)['fueler_prof'])

    def test_swap_permission_and_stale_dispatch_baseline_are_enforced(self):
        self.prepare();actor=self.replacement()
        self.login(self.fueler)
        response=self.client.post('/neoscorpion/fuel-dispatch/swap-fueler',data={
            'assignment_id':self.assignment.id,'replacement_fueler_user_id':actor.id})
        self.assertEqual(response.status_code,403)
        self.login(self.dispatcher)
        response=self.client.post('/neoscorpion/fuel-dispatch/swap-fueler',data={
            'assignment_id':self.assignment.id,'replacement_fueler_user_id':actor.id,
            'expected_assigned_fueler_user_id':actor.id,'expected_work_ref':self.work.id})
        self.assertEqual(response.status_code,400)
        self.assertEqual(self.assignment.assigned_fueler_user_id,self.fueler.id)

    def test_replacement_can_resume_same_accidental_off_without_rewriting_original_actor(self):
        self.prepare();self.save(self.start+timedelta(minutes=1),**self.final_data())
        fuel.mark_fueler_off(self.gateway,self.fueler,self.assignment.id,now_utc=self.start+timedelta(minutes=12));db.session.commit()
        off=self.work.off_at_utc;original=self.work.manual_on_at_utc;b=self.replacement();self.swap(b)
        fuel.confirm_fueler_truck_on(self.gateway,b,self.assignment.id,expected_cycle=1,expected_tail='N491UP',
            expected_truck_id=self.truck.id,resume_off_at=off.isoformat(),now_utc=self.start+timedelta(minutes=14));db.session.commit()
        self.assertIsNone(self.work.off_at_utc);self.assertEqual(self.work.manual_on_at_utc,original)
        self.assertEqual(self.work.physical_fueler_user_id,self.fueler.id)
        self.assertEqual(NeoScorpionFuelWorkState.query.count(),1)

    def _independent_cycles(self, first_handoff=False, second_handoff=False):
        self.prepare();self.save(self.start+timedelta(minutes=1),**self.final_data())
        first_actor=self.fueler
        if first_handoff:
            first_actor=self.replacement();self.swap(first_actor)
        original=self.complete(first_actor);snapshot=copy.deepcopy(original.performance_snapshot)
        actor=self.replacement('uplift-operator');truck=self.fixture._nightly_truck('UPLIFT-TRUCK');db.session.commit()
        result=fuel.start_follow_up_fuel_cycle(self.gateway,self.dispatcher,self.assignment.id,'uplift','34',actor.id,truck.id)
        db.session.commit();self.work=result.fuel_work_state
        fuel.save_dispatch_assignment(self.gateway,dict(mission_id=self.mission.id,
            assigned_fueler_user_id=actor.id,assigned_truck_id=truck.id,
            expected_assigned_fueler_user_id='',expected_assigned_truck_id=''))
        db.session.commit()
        from datetime import datetime
        now=datetime.utcnow()
        fuel.confirm_fueler_truck_on(self.gateway,actor,self.assignment.id,expected_cycle=2,
            expected_tail='N491UP',expected_truck_id=truck.id,now_utc=now);db.session.commit()
        fuel.save_fueler_entry(self.gateway,actor,dict(assignment_id=self.assignment.id,apu_running='no',
            actual_left='18',actual_ctr='0',actual_right='18',transfer_fuel_gallons='1000'),now_utc=now+timedelta(minutes=1));db.session.commit()
        if second_handoff:
            actor=self.replacement('uplift-handoff')
            self.swap(actor)
        fuel.mark_fueler_off(self.gateway,actor,self.assignment.id,now_utc=now+timedelta(minutes=5));db.session.commit()
        self.submit(actor)
        fuel.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id);db.session.commit()
        uplift=NeoScorpionFuelingEvent.query.filter_by(cycle_number=2).one()
        assessment=participation.cycles([original,uplift])
        self.assertEqual(participation.reasons(assessment[participation.key(original)]),[participation.MULTIPLE_FUELERS] if first_handoff else [])
        self.assertEqual(participation.reasons(assessment[participation.key(uplift)]),[participation.MULTIPLE_FUELERS] if second_handoff else [])
        self.assertEqual(assessment[participation.key(original)]['truck_ids'],[original.fuel_truck_id])
        self.assertIn(actor.id,assessment[participation.key(uplift)]['fueler_ids'])
        self.assertEqual(original.performance_snapshot,snapshot)
        for e,excluded in ((original,first_handoff),(uplift,second_handoff)):
            if excluded:self.assertIsNone(event_math(e)['fueler_prof'])
            else:self.assertIsNotNone(event_math(e)['fueler_prof'],event_math(e)['fueler_reasons'])
        # The same independence survives archival read-back. An assignment-wide
        # training flag must not discard another cycle's verified PROF evidence.
        from unittest.mock import patch, MagicMock
        from app.services import neoscorpion_spear_scorecard as scorecard
        from app.services.neoscorpion_spear_learning_capture import build_completed_learning_record
        from app.services.neoscorpion_learning_vault import _validate_learning_record
        record=build_completed_learning_record(self.gateway,self.operation,self.assignment,self.mission)
        _validate_learning_record(record)
        original_query=db.session.query
        def no_canonical_events(*entities,**kwargs):
            if entities==(NeoScorpionFuelingEvent,):
                query=MagicMock()
                for method in ('join','filter','order_by','limit'):getattr(query,method).return_value=query
                query.all.return_value=[]
                return query
            return original_query(*entities,**kwargs)
        with patch.object(db.session,'query',side_effect=no_canonical_events):
            archived=scorecard.report_context(self.gateway,self.operation,archive=record)['off']
        self.assertEqual(len(archived),2)
        by_id={row['key']:row for row in archived}
        for e,excluded in ((original,first_handoff),(uplift,second_handoff)):
            reasons=by_id[f'fuel:{e.id}']['reasons']
            self.assertEqual(participation.MULTIPLE_FUELERS in reasons,excluded)
            if not excluded:self.assertNotIn('Archived record is not verified for learning',reasons)

    def test_independent_fuel_and_uplift_are_both_single_resource_eligible(self):
        self._independent_cycles()

    def test_eligible_original_and_excluded_uplift(self):
        self._independent_cycles(second_handoff=True)

    def test_excluded_original_and_eligible_uplift(self):
        self._independent_cycles(first_handoff=True)

    def test_missing_archived_participation_is_unverified(self):
        from types import SimpleNamespace
        # Pure archived evidence without a whole-cycle assessment fails closed.
        event=SimpleNamespace(performance_snapshot=dict(version=3,on_source='manual',pump_rate='150',
             fueler_id=1,truck_id=2,setup_allowance='0'),fueler_user_id=1,fuel_truck_id=2,
             event_type='fuel',transfer_fuel_gallons=3000,started_at_utc=None,ended_at_utc=None)
        self.assertIn(participation.UNVERIFIED,event_math(event)['fueler_reasons'])

    def _partial_handoff(self, data):
        self.prepare();self.save(self.start+timedelta(minutes=1),**data)
        before=self.state();b=self.replacement();self.swap(b)
        self.assertEqual(self.state(),before)
        self.assertEqual(self.row(b)['physical_work_id'],before['id'])

    def test_remaining_only_handoff(self):
        self._partial_handoff(dict(remaining_left='6',remaining_ctr='0',remaining_right='6'))

    def test_actual_handoff(self):
        self._partial_handoff(dict(remaining_left='6',actual_left='15'))

    def test_apu_automatic_handoff(self):
        self._partial_handoff(dict(apu_running='yes',apu_source_tank_code='left'))

    def test_transfer_and_notes_handoff(self):
        self._partial_handoff(dict(transfer_fuel_gallons='3000',notes='Preserve saved transfer'))

    def _source_visibility(self, legacy=False, foreign_audit=False):
        self.prepare();self.save(self.start+timedelta(minutes=1),**self.final_data(),notes='Original saved aircraft data')
        before=self.state();b=self.replacement();self.swap(b)
        if legacy:
            facts=dict(self.work.performance_context)
            facts.pop('participation')
            facts['exclusions']=['Fueler changed during physical segment']
            self.work.performance_context=facts
            db.session.commit()  # Synthetic pre-release metadata; physical work is unchanged.
        if foreign_audit:
            from app.models import SortDateOperation
            foreign=SortDateOperation(gateway_id=self.gateway.id,gateway_code=self.gateway.code,
                sort_date=self.operation.sort_date-timedelta(days=10),sort_name='day',
                generated_by_user_id=self.dispatcher.id,window_minutes=60)
            db.session.add(foreign);db.session.flush()
            stranger=self.replacement('foreign-audit-candidate')
            db.session.add(NeoScorpionFuelAuditEntry(sort_date_operation_id=foreign.id,
                fuel_assignment_id=self.assignment.id,fuel_work_state_id=self.work.id,
                action='swap_fueler',field_name='assigned_fueler_user_id',old_value=str(b.id),
                new_value=str(stranger.id),reason='Synthetic inconsistent historical audit',changed_by_user_id=self.dispatcher.id))
            db.session.commit()
        self.assertEqual(NeoScorpionFuelWorkState.query.count(),1)
        # A later canonical tail acknowledgment retains the old physical visit.
        self.mission.assigned_tail_number='N492UP';db.session.commit()
        fuel.confirm_assignment_tail(self.gateway,self.dispatcher,self.assignment.id,
            expected_cycle=1,expected_tail='N492UP');db.session.commit()
        row=self.row(b)
        self.assertTrue(row['is_source_segment'])
        self.assertEqual(row['fueler_notes'],'Original saved aircraft data')
        self.assertEqual(row['tail_number'],'N491UP')
        self.assertEqual(self.state(),before)
        ctx=fuel.fueler_context(self.gateway,self.fueler,assignment_id=self.assignment.id)
        self.assertNotIn(self.work.id,[r['physical_work_id'] for r in ctx['rows']])
        form=dict(assignment_id=self.assignment.id,physical_work_id=self.work.id,
            physical_sequence=self.work.physical_sequence_number,notes='Replacement continues original visit')
        with self.assertRaisesRegex(ValueError,'another Fueler'):
            fuel.save_fueler_entry(self.gateway,self.fueler,form)
        db.session.rollback()
        fuel.save_fueler_entry(self.gateway,b,form);db.session.commit()
        self.assertEqual(self.work.notes,form['notes'])
        self.assertEqual(self.work.physical_fueler_user_id,self.fueler.id)
        if foreign_audit:
            self.assertNotIn(self.work.id,[r['physical_work_id'] for r in
                fuel.fueler_context(self.gateway,stranger,assignment_id=self.assignment.id)['rows']])

    def test_handed_off_source_visit_stays_visible_and_editable_only_to_replacement(self):
        self._source_visibility()

    def test_existing_audited_legacy_handoff_is_resolved_without_a_data_backfill(self):
        self._source_visibility(legacy=True)

    def test_foreign_sort_audit_cannot_grant_access_to_another_visit(self):
        self._source_visibility(legacy=True,foreign_audit=True)

    def test_pre_work_a_b_c_reassignments_count_only_the_actual_operator(self):
        from datetime import datetime
        from app.models import NeoScorpionSortTruck
        self.start=datetime.utcnow()-timedelta(minutes=30)
        self.truck=self.fixture._nightly_truck()
        self.selection=NeoScorpionSortTruck.query.filter_by(fuel_truck_id=self.truck.id).one()
        self.assignment.assigned_fueler_user_id=self.fueler.id;self.assignment.assigned_truck_id=self.truck.id
        self.assignment.truck_assigned_at_utc=self.start-timedelta(minutes=5)
        self.assignment.fueler_assigned_at_utc=self.start-timedelta(minutes=8)
        db.session.add(NeoScorpionSortFueler(sort_date_operation_id=self.operation.id,user_id=self.fueler.id));db.session.commit()
        b=self.replacement();c=self.replacement('pre-work-c')
        self.swap(b);self.swap(c)
        self.assertEqual(NeoScorpionFuelWorkState.query.count(),0)
        self.start+=timedelta(minutes=5)
        _,_,self.work=fuel.confirm_fueler_truck_on(self.gateway,c,self.assignment.id,expected_cycle=1,
            expected_tail='N491UP',expected_truck_id=self.truck.id,now_utc=self.start)
        db.session.commit()
        fuel.save_fueler_entry(self.gateway,c,dict(assignment_id=self.assignment.id,**self.final_data()),
            now_utc=self.start+timedelta(minutes=1));db.session.commit()
        event=self.complete(c)
        data=participation.cycles([event])[participation.key(event)]
        self.assertEqual(data['fueler_ids'],[c.id]);self.assertEqual(participation.reasons(data),[])

    def test_off_takeover_can_finish_missing_actual_apu_and_transfer_without_new_on(self):
        self.prepare();self.save(self.start+timedelta(minutes=1),remaining_left='6',remaining_ctr='0',remaining_right='6')
        fuel.mark_fueler_off(self.gateway,self.fueler,self.assignment.id,now_utc=self.start+timedelta(minutes=12));db.session.commit()
        on,off=self.work.manual_on_at_utc,self.work.off_at_utc
        b=self.replacement();self.swap(b)
        fuel.save_fueler_entry(self.gateway,b,dict(assignment_id=self.assignment.id,**self.final_data(),
            expected=json.dumps(self.row(b)['edit_baseline'])));db.session.commit()
        self.submit(b)
        fuel.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id);db.session.commit()
        self.assertEqual((self.work.manual_on_at_utc,self.work.off_at_utc),(on,off))
        self.assertEqual(NeoScorpionFuelWorkState.query.count(),1)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)
        self.assertEqual(self.selection.current_gallons,7000)

    def test_pre_work_planning_reassignment_does_not_invent_a_contributor_or_leave_stale_ownership(self):
        self.truck=self.fixture._nightly_truck()
        self.assignment.assigned_fueler_user_id=self.fueler.id;self.assignment.assigned_truck_id=self.truck.id
        db.session.add(NeoScorpionSortFueler(sort_date_operation_id=self.operation.id,user_id=self.fueler.id));db.session.commit()
        result=fuel.save_fueler_entry(self.gateway,self.fueler,dict(assignment_id=self.assignment.id,remaining_left='6'))
        db.session.commit();self.work=result.fuel_work_state
        b=self.replacement()
        for old,new in ((self.fueler,b),(b,self.fueler)):
            fuel.save_dispatch_assignment(self.gateway,dict(mission_id=self.mission.id,
                assigned_fueler_user_id=new.id,assigned_truck_id=self.truck.id,
                expected_assigned_fueler_user_id=old.id,expected_assigned_truck_id=self.truck.id))
            db.session.commit()
            self.assertEqual(participation.owner_id(self.work),new.id)
            self.assertEqual(self.work.performance_context['participation']['fueler_ids'],[self.fueler.id])
        self.assertIsNone(self.work.manual_on_at_utc)
        self.assertEqual(NeoScorpionFuelWorkState.query.count(),1)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)

    def test_successive_active_a_b_c_handoffs_preserve_data_and_revoke_both_prior_actors(self):
        self.prepare();self.save(self.start+timedelta(minutes=1),**self.final_data(),notes='Original persisted notes')
        before=self.state();b=self.replacement();c=self.replacement('active-handoff-c')
        self.swap(b);self.swap(c)
        self.assertEqual(self.state(),before)
        self.assertEqual(self.row(c)['fueler_notes'],'Original persisted notes')
        for old in (self.fueler,b):
            with self.subTest(old=old.id),self.assertRaises(ValueError):
                fuel.save_fueler_entry(self.gateway,old,dict(assignment_id=self.assignment.id,notes='Stale write'))
            db.session.rollback()
        e=self.complete(c)
        self.assertEqual(e.fuel_work_state_id,before['id'])
        self.assertEqual(self.work.performance_context['participation']['fueler_ids'],sorted([self.fueler.id,b.id,c.id]))
        self.assertIsNone(event_math(e)['fueler_prof'])
        self.assertEqual(self.selection.current_gallons,7000)
