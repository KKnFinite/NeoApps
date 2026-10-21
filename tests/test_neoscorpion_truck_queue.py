"""Unlimited advance plans, exact receipt verification and incident regressions."""
from datetime import timedelta
from app.extensions import db
from app.models import NeoScorpionPendingUplift,NeoScorpionFuelWorkState,NeoScorpionFuelingEvent
from app.services import neoscorpion as fuel
from app.services import neoscorpion_pending_uplift as plans
from tests.test_neoscorpion_continuations import ContinuationTest


class TruckQueueTest(ContinuationTest):
    def add_plan(self,truck):
        queue=plans.outstanding_plans(self.assignment.id)
        return self.plan(truck,expected_last_plan_ref=queue[-1].id if queue else '')

    def finish_visit(self,work,amount,transfer='100'):
        if not work.manual_on_at_utc:
            fuel.confirm_fueler_truck_on(self.gateway,self.fueler,self.assignment.id,expected_cycle=1,
                expected_tail='N491UP',expected_truck_id=self.assignment.assigned_truck_id)
            db.session.commit()
        fuel.save_fueler_entry(self.gateway,self.fueler,dict(assignment_id=self.assignment.id,apu_running='no',
            remaining_left=str(next(t.remaining_lbs for t in work.tank_states if t.tank_code=='left')/1000) if work.tank_states else '6',
            remaining_ctr='0',remaining_right=str(next(t.remaining_lbs for t in work.tank_states if t.tank_code=='right')/1000) if work.tank_states else '6',
            actual_left=str(amount),actual_ctr='0',actual_right=str(amount),transfer_fuel_gallons=transfer))
        db.session.commit();fuel.mark_fueler_off(self.gateway,self.fueler,self.assignment.id);db.session.commit();self.submit()

    def test_five_trucks_planned_in_advance_each_receipt_handoff_and_reverse_verification(self):
        self.prepare();self.target('60')
        b=self.fixture._nightly_truck('B');db.session.commit()
        queue=[self.add_plan(t) for t in (b,self.truck,b,self.truck)]
        self.assertEqual(len(plans.outstanding_plans(self.assignment.id)),4)
        self.assertEqual(NeoScorpionFuelWorkState.query.count(),1)
        self.assertEqual(len(self.row()['pending_truck_plans']),4)
        works=[]
        for number in range(5):
            work=db.session.get(NeoScorpionFuelWorkState,self.assignment.active_work_state_id);works.append(work)
            if number:
                self.assertEqual({t.tank_code:t.remaining_lbs for t in work.tank_states},
                    {t.tank_code:t.actual_lbs for t in works[-2].tank_states})
                self.assertTrue(all(t.actual_lbs is None for t in work.tank_states))
                self.assertIsNone(work.off_at_utc);self.assertIsNone(work.apu_running);self.assertIsNone(work.physical_transfer_gallons)
            self.finish_visit(work,15+number*4,transfer=str(100+number))
            self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)
        for work in reversed(works):
            result=fuel.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id,
                physical_work_id=work.id,physical_sequence=work.physical_sequence_number);db.session.commit()
            self.assertTrue(result.changed);stamp=work.physical_finalized_at_utc
            duplicate=fuel.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id,
                physical_work_id=work.id,physical_sequence=work.physical_sequence_number);db.session.commit()
            self.assertFalse(duplicate.changed);self.assertEqual(work.physical_finalized_at_utc,stamp)
            self.assertEqual({t.tank_code:t.actual_lbs for t in work.tank_states},{'left':(15+works.index(work)*4)*1000,'ctr':0,'right':(15+works.index(work)*4)*1000})
        self.assertIsNotNone(self.assignment.completed_at_utc)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),5)
        self.assertEqual(len({w.id for w in works}),5)
        self.assertEqual([p.status for p in queue],['promoted']*4)

    def test_verified_last_visit_retains_complete_line_and_exact_time_until_source_verified(self):
        p,child=self.handoff();self.finish_child(p)
        fuel.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id);db.session.commit()
        stamp=child.physical_finalized_at_utc
        self.assertIsNone(self.assignment.completed_at_utc)
        self.assertEqual(self.row()['unverified_visit_count'],1)
        self.login(self.dispatcher)
        html=self.client.get('/neoscorpion/fuel-dispatch').get_data(as_text=True)
        self.assertIn('VERIFY 1 EARLIER VISIT',html)
        self.assertIn(f'data-truck-line-work="{child.id}"',html)
        self.assertIn(f'data-action-key="verify-{self.work.id}"',html)
        self.assertIn('data-visit-state>COMPLETE</strong>',html)
        self.assertFalse(fuel.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id).changed)
        db.session.commit();self.assertEqual(child.physical_finalized_at_utc,stamp)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)
        fuel.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id,
            physical_work_id=self.work.id,physical_sequence=self.work.physical_sequence_number);db.session.commit()
        self.assertIsNotNone(self.assignment.completed_at_utc)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),2)

    def test_cancel_middle_and_unnecessary_head_keeps_other_plans_explicit(self):
        self.prepare();self.target('40');b=self.fixture._nightly_truck('B');db.session.commit()
        first=self.add_plan(b);middle=self.add_plan(self.truck);last=self.add_plan(b)
        self.plan(self.truck,pending_action='cancel',pending_id=middle.id,expected_version=middle.version)
        self.save(self.start+timedelta(minutes=1),**{**self.final_data(),'actual_left':'21','actual_right':'21'})
        self.off();self.submit()
        self.assertEqual(first.status,'review');self.assertEqual(last.status,'pending')
        self.assertEqual(NeoScorpionFuelWorkState.query.count(),1)
        self.plan(b,pending_action='cancel',pending_id=first.id,expected_version=first.version)
        self.assertEqual(last.status,'review');self.assertEqual(middle.status,'cancelled')
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)

    def test_target_changes_update_all_queued_plans_without_extra_physical_work(self):
        self.prepare();b=self.fixture._nightly_truck('B');db.session.commit()
        first=self.add_plan(b);last=self.add_plan(self.truck)
        self.save(self.start+timedelta(minutes=1),remaining_left='6',actual_left='15')
        self.target('50')
        self.assertEqual([p.required_fuel_lbs for p in (first,last)],[50000,50000])
        self.assertEqual(NeoScorpionPendingUplift.query.count(),2)
        self.assertEqual(NeoScorpionFuelWorkState.query.count(),1)
