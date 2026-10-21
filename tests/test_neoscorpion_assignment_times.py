"""Dispatcher sent-time receipts, separate from physical/activation clocks."""
from copy import deepcopy
from datetime import datetime, timedelta
import unittest
from unittest.mock import patch
from flask_login import login_user

from app.extensions import db
from app.models import (NeoScorpionFuelAuditEntry, NeoScorpionFuelWorkState,
                        NeoScorpionFuelingEvent, NeoScorpionSortFueler)
from app.services import neoscorpion as fuel
from app.services import neoscorpion_pending_uplift as plans
from app.services.gateway_matrix import gateway_timezone
from app.services.time_display import format_local_hhmm
from tests import test_neoscorpion_dispatch_details as cases


class AssignmentTimesTest(unittest.TestCase):
    setUp = cases.DetailsTest.setUp
    prepare = cases.DetailsTest.prepare
    save = cases.DetailsTest.save
    off = cases.DetailsTest.off
    final_data = cases.DetailsTest.final_data
    submit = cases.DetailsTest.submit
    baseline = cases.DetailsTest.baseline
    login = cases.DetailsTest.login
    row = cases.DetailsTest.row
    target = cases.DetailsTest.target
    handoff = cases.DetailsTest.handoff
    plan = cases.DetailsTest.plan
    finish_child = cases.DetailsTest.finish_child

    def assign(self, truck, at, fueler=None):
        actor = fueler or self.fueler
        form = dict(mission_id=self.mission.id, expected_work_ref=self.assignment.active_work_state_id or '',
            expected_assigned_fueler_user_id=self.assignment.assigned_fueler_user_id or '',
            expected_assigned_truck_id=self.assignment.assigned_truck_id or '',
            assigned_fueler_user_id=actor.id, assigned_truck_id=truck.id)
        class Clock(datetime):
            @classmethod
            def utcnow(cls): return at
        with self.fixture.app.test_request_context(), patch.object(fuel, 'datetime', Clock):
            login_user(self.dispatcher)
            result = fuel.save_dispatch_assignment(self.gateway, form)
            db.session.commit()
        return result

    def setup_resources(self):
        truck = self.fixture._nightly_truck('A')
        db.session.add(NeoScorpionSortFueler(sort_date_operation_id=self.operation.id, user_id=self.fueler.id))
        db.session.commit()
        return truck

    def test_initial_assignment_noop_update_autosave_and_refresh_are_not_new_receipts(self):
        truck = self.setup_resources(); at = datetime.utcnow() - timedelta(minutes=10)
        self.assign(truck, at)
        before = NeoScorpionFuelAuditEntry.query.count()
        receipts = deepcopy(self.row()['assignment_receipt'])
        self.assertEqual([r['at'] for r in receipts], [at, at])
        self.assertEqual([r['actor_id'] for r in receipts], [self.dispatcher.id]*2)
        self.assign(truck, at + timedelta(minutes=3))
        self.target('26')
        for _ in range(2): self.assertEqual(self.row()['assignment_receipt'], receipts)
        self.assertEqual(NeoScorpionFuelAuditEntry.query.count(), before)

    def test_pre_on_reassignment_retains_initial_receipt_and_binds_existing_preparation(self):
        truck = self.setup_resources(); other = self.fixture._nightly_truck('B'); db.session.commit()
        at = datetime.utcnow() - timedelta(minutes=10); self.assign(truck, at)
        work = fuel.save_fueler_entry(self.gateway,self.fueler,dict(assignment_id=self.assignment.id,
            remaining_left='6')).fuel_work_state; db.session.commit()
        self.assign(other, at + timedelta(minutes=2))
        row = self.row()
        self.assertEqual(row['assignment_receipt'][1]['at'], at + timedelta(minutes=2))
        initial = next(r for r in row['assignment_changes'] if r['kind']=='truck' and r['resource_id']==truck.id)
        self.assertEqual(initial['at'], at)
        audit = NeoScorpionFuelAuditEntry.query.filter_by(new_value=str(other.id),field_name='assigned_truck_id').one()
        self.assertEqual(audit.fuel_work_state_id, work.id)
        self.assertIsNone(work.manual_on_at_utc)

    def test_repeated_truck_a_b_a_visits_keep_three_independent_assignment_receipts(self):
        self.prepare(); original_at = self.assignment.truck_assigned_at_utc
        original_work = self.work
        other = self.fixture._nightly_truck('B'); db.session.commit()
        self.off(at=self.start+timedelta(minutes=2))
        original_facts = deepcopy(self.work.performance_context)
        second_at = self.start + timedelta(minutes=3)
        result = fuel.swap_assignment_truck(self.gateway,self.dispatcher,self.assignment.id,other.id,now_utc=second_at)
        db.session.commit(); second = result.fuel_work_state
        fuel.confirm_fueler_truck_on(self.gateway,self.fueler,self.assignment.id,expected_cycle=1,
            expected_tail='N491UP',expected_truck_id=other.id,now_utc=self.start+timedelta(minutes=4));db.session.commit()
        self.off(at=self.start+timedelta(minutes=5))
        third_at = self.start + timedelta(minutes=6)
        result = fuel.swap_assignment_truck(self.gateway,self.dispatcher,self.assignment.id,self.truck.id,now_utc=third_at)
        db.session.commit(); third = result.fuel_work_state
        row = self.row(); visits = {v.id:v for v in row['console_visits']}
        self.assertEqual(len(visits),3)
        for work,truck,at in ((original_work,self.truck,original_at),(second,other,second_at),(third,self.truck,third_at)):
            receipt = visits[work.id].console_assignment_receipt[1]
            self.assertEqual((receipt['resource_id'],receipt['at']),(truck.id,at))
        self.assertEqual(original_work.performance_context,original_facts)
        self.assertEqual(original_work.manual_on_at_utc,self.start)

    def test_fueler_swap_does_not_relabel_original_physical_fueler(self):
        self.prepare(); before = self.assignment.fueler_assigned_at_utc
        replacement = self.fixture._login_user('replacement-fueler','operator')
        db.session.add(NeoScorpionSortFueler(sort_date_operation_id=self.operation.id,user_id=replacement.id));db.session.commit()
        at = self.start + timedelta(minutes=2)
        fuel.swap_assignment_fueler(self.gateway,self.dispatcher,self.assignment.id,replacement.id,now_utc=at);db.session.commit()
        row = self.row(); work = row['console_visits'][0]
        self.assertEqual((work.console_assignment_receipt[0]['resource_id'],work.console_assignment_receipt[0]['at']),
                         (self.fueler.id,before))
        self.assertEqual((row['assignment_receipt'][0]['resource_id'],row['assignment_receipt'][0]['at']),
                         (replacement.id,at))
        self.assertEqual(work.console_assignment_changes[0]['actor_id'],self.dispatcher.id)

    def test_second_truck_sent_time_is_dispatcher_plan_not_fueler_activation(self):
        self.prepare(); self.target('40'); truck=self.fixture._nightly_truck('NEXT');db.session.commit()
        sent = self.start + timedelta(minutes=1)
        p=plans.plan_continuation(self.gateway,self.dispatcher,dict(assignment_id=self.assignment.id,
            expected_cycle=1,expected_tail='N491UP',expected_work_ref=self.work.id,
            proposed_truck_id=truck.id,pending_action='create'),now_utc=sent).pending;db.session.commit()
        # Same truck UPDATE and target changes are not new assignment decisions.
        plans.plan_continuation(self.gateway,self.dispatcher,dict(assignment_id=self.assignment.id,
            expected_cycle=1,expected_tail='N491UP',pending_id=p.id,expected_version=p.version,
            proposed_truck_id=truck.id,pending_action='save'),now_utc=sent+timedelta(minutes=1));db.session.commit()
        self.target('41')
        row=self.row();self.assertEqual(row['planned_assignment_receipt'][1]['at'],sent)
        self.save(self.start+timedelta(minutes=5),**self.final_data());self.off();self.submit()
        row=self.row();child=next(w for w in row['console_visits'] if w.id==p.promoted_work_state_id)
        self.assertEqual(child.console_assignment_receipt[1]['at'],sent)
        self.assertEqual(row['assignment_receipt'][1]['at'],sent)
        self.assertEqual(row['assignment_receipt'][1]['actor_id'],self.dispatcher.id)
        self.assertNotEqual(sent,self.assignment.truck_assigned_at_utc)

    def test_planned_truck_reassignment_a_b_a_records_last_intent_not_target_edit(self):
        self.prepare();self.target('40');a=self.fixture._nightly_truck('NEXT-A');b=self.fixture._nightly_truck('NEXT-B');db.session.commit()
        sent=self.start+timedelta(minutes=1);p=None
        for i,truck in enumerate((a,b,a,a)):
            p=plans.plan_continuation(self.gateway,self.dispatcher,dict(assignment_id=self.assignment.id,
                expected_cycle=1,expected_tail='N491UP',expected_work_ref=self.work.id,
                proposed_truck_id=truck.id,pending_action='save' if p else 'create',
                pending_id=p.id if p else '',expected_version=p.version if p else ''),
                now_utc=sent+timedelta(minutes=i)).pending;db.session.commit()
        receipt=self.row()['planned_assignment_receipt'][1]
        self.assertEqual((receipt['resource_id'],receipt['at']),(a.id,sent+timedelta(minutes=2)))

    def test_post_completion_cycle_fresh_assignment_and_original_event_unchanged(self):
        self.prepare();self.save(self.start+timedelta(minutes=5),**self.final_data());self.off();self.submit()
        fuel.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id);db.session.commit()
        event=NeoScorpionFuelingEvent.query.one();before=deepcopy({c.name:getattr(event,c.name) for c in event.__table__.columns})
        old=self.row()['console_visits'][0].console_assignment_receipt
        at=self.start+timedelta(minutes=20)
        # DEFUEL explicitly assigns resources at cycle creation; UPLIFT leaves
        # its new resource selections blank until Dispatcher assigns them.
        result=fuel.start_follow_up_fuel_cycle(self.gateway,self.dispatcher,self.assignment.id,'defuel','20',
            self.fueler.id,self.truck.id,now_utc=at,expected_cycle=1,expected_tail='N491UP');db.session.commit()
        row=self.row();self.assertEqual([r['at'] for r in row['assignment_receipt']],[at,at])
        self.assertEqual(row['console_visits'][0].console_assignment_receipt,old)
        self.assertIsNone(result.fuel_work_state.manual_on_at_utc)
        self.assertEqual({c.name:getattr(event,c.name) for c in event.__table__.columns},before)

    def test_third_truck_continuation_keeps_each_completed_visits_original_plan_time(self):
        cases.base.ContinuationTest.test_three_visits_a_b_a_preserve_each_receipt_tanks_and_inventory_event(self)
        row=self.row();visits=row['console_visits']
        self.assertEqual(len(visits),3)
        for visit in visits:
            receipts=visit.console_assignment_receipt
            self.assertTrue(all(r['at'] is not None for r in receipts))
            self.assertEqual(receipts[1]['resource_id'],visit.physical_truck_id)
        self.assertNotEqual(visits[0].console_assignment_receipt[1]['at'],visits[2].console_assignment_receipt[1]['at'])
        self.assertEqual([v.physical_truck_id for v in visits],[self.truck.id,visits[1].physical_truck_id,self.truck.id])

    def test_missing_historical_provenance_is_not_work_created_on_or_refreshed_time(self):
        self.prepare();self.work.performance_context=None
        self.assignment.fueler_assigned_at_utc=None;self.assignment.truck_assigned_at_utc=None;db.session.commit()
        row=self.row()
        self.assertEqual([r['display'] for r in row['assignment_receipt']],['-','-'])
        self.assertEqual([r['at'] for r in row['console_visits'][0].console_assignment_receipt],[None,None])
        self.assertIsNotNone(self.work.manual_on_at_utc)

    def test_completed_event_snapshot_supplies_original_time_without_rewriting_event(self):
        self.prepare();self.save(self.start+timedelta(minutes=5),**self.final_data());self.off();self.submit()
        fuel.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id);db.session.commit()
        event=NeoScorpionFuelingEvent.query.one();snapshot=deepcopy(event.performance_snapshot)
        expected=[self.assignment.fueler_assigned_at_utc,self.assignment.truck_assigned_at_utc]
        self.work.performance_context=None
        self.assignment.fueler_assigned_at_utc=None;self.assignment.truck_assigned_at_utc=None;db.session.commit()
        row=self.row()
        self.assertEqual([r['at'] for r in row['console_visits'][0].console_assignment_receipt],expected)
        self.assertEqual(event.performance_snapshot,snapshot)

    def test_gateway_local_clock_midnight_and_readonly_render(self):
        truck=self.setup_resources();at=datetime(2026,10,20,5,5)
        self.assign(truck,at);row=self.row();zone=gateway_timezone(self.gateway)
        self.assertEqual(row['assignment_receipt'][1]['display'],format_local_hhmm(at,zone))
        self.assertEqual(row['assignment_receipt'][1]['display'],'00:05')
        self.assertEqual(row['assignment_receipt'][1]['utc'],'2026-10-20T05:05:00Z')
        self.login(self.dispatcher);html=self.client.get('/neoscorpion/fuel-dispatch').get_data(as_text=True)
        self.assertIn('ASSIGNED '+format_local_hhmm(at,zone),html)
        self.assertIn('data-mobile-assignment-times',html)
        before=NeoScorpionFuelAuditEntry.query.count()
        self.client.get('/neoscorpion/fuel-dispatch/live')
        self.assertEqual(NeoScorpionFuelAuditEntry.query.count(),before)

    def test_other_assignment_audits_never_supply_this_missions_time(self):
        truck=self.setup_resources();self.assign(truck,datetime.utcnow()-timedelta(minutes=3))
        other=self.fixture._mission('UPS902','N492UP',25000,2);a=self.fixture._assignment(other,truck)
        a.assigned_fueler_user_id=self.fueler.id;db.session.commit()
        row=next(r for r in fuel.fuel_dispatch_context(self.gateway)['rows'] if r['mission'].id==other.id)
        self.assertEqual([r['at'] for r in row['assignment_receipt']],[None,None])


if __name__ == '__main__': unittest.main()
