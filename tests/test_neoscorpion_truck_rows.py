"""Continuation presentation reuses mission columns, without lifecycle writes."""
from datetime import timedelta
import re
import unittest
from unittest.mock import patch
from app.extensions import db
from app.models import NeoScorpionFuelWorkState,NeoScorpionFuelingEvent
from app.services import neoscorpion as fuel
from tests import test_neoscorpion_truck_queue as fixtures


class TruckRowPresentationTest(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.TruckQueueTest();self.f.setUp();self.addCleanup(self.f.doCleanups)

    def render(self):
        self.f.login(self.f.dispatcher)
        with patch.object(db.session,'commit',wraps=db.session.commit) as commit:
            response=self.f.client.get('/neoscorpion/fuel-dispatch')
        self.assertEqual(response.status_code,200)
        self.assertEqual(commit.call_count,0)
        html=response.get_data(as_text=True)
        self.assertNotIn('class="scorpion-truck-lines"',html)
        rows=re.findall(r'<tr\b([^>]*data-truck-lines-mission="'+str(self.f.mission.id)+r'"[^>]*)>(.*?)</tr>',html,re.S)
        for attributes,body in rows:
            self.assertEqual(len(re.findall(r'<td\b',body)),16)
            self.assertNotIn('colspan',body)
            self.assertNotIn('data-visit-evidence',body)
            self.assertNotIn('<form',body)
            self.assertNotIn('Remaining / Actual',body)
        return html,rows

    def test_two_through_five_trucks_are_distinct_aligned_plans(self):
        f=self.f;f.prepare();f.target('60')
        other=f.fixture._nightly_truck('NEXT');db.session.commit()
        for number,truck in enumerate((other,f.truck,other,f.truck),1):
            plan=f.add_plan(truck)
            html,rows=self.render()
            self.assertEqual(len(rows),number)
            self.assertIn(f'data-truck-line-plan="{plan.id}"',rows[-1][0])
            self.assertIn('data-visit-state>PLANNED</strong>',rows[-1][1])
            self.assertIn(f'data-dispatch-action-target="plan-{plan.id}"',rows[-1][1])
            self.assertTrue(all(' hidden' not in attrs for attrs,body in rows))
        self.assertEqual(NeoScorpionFuelWorkState.query.count(),1)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)

    def test_submitted_source_keeps_exact_verification_and_evidence_in_details(self):
        f=self.f;plan,child=f.handoff()
        html,rows=self.render()
        self.assertEqual(len(rows),1)
        self.assertIn(f'data-truck-line-work="{f.work.id}"',rows[0][0])
        self.assertIn('data-visit-state>SUBMITTED</strong>',rows[0][1])
        self.assertIn(f'data-dispatch-action-target="verify-{f.work.id}"',rows[0][1])
        self.assertIn(f'name="physical_work_id" value="{f.work.id}"',html)
        self.assertIn(f'data-visit-evidence="{f.work.id}"',html)
        self.assertIn('15,000',html)
        fuel.complete_fueled_assignment(f.gateway,f.dispatcher,f.assignment.id,
            physical_work_id=f.work.id,physical_sequence=f.work.physical_sequence_number);db.session.commit()
        html,rows=self.render()
        self.assertIn('data-visit-state>COMPLETE</strong>',rows[0][1])
        self.assertTrue(all(t.actual_lbs is None for t in child.tank_states))
        self.assertIsNone(child.off_at_utc)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)

    def test_latest_verified_first_is_complete_without_hiding_earlier_verification(self):
        f=self.f;plan,child=f.handoff();f.finish_child(plan)
        fuel.complete_fueled_assignment(f.gateway,f.dispatcher,f.assignment.id);db.session.commit()
        html,rows=self.render()
        self.assertIsNone(f.assignment.completed_at_utc)
        self.assertEqual(len(rows),2)
        latest=next((a,b) for a,b in rows if f'data-truck-line-work="{child.id}"' in a)
        self.assertIn('data-visit-state>COMPLETE</strong>',latest[1])
        self.assertTrue(all(' hidden' not in a for a,b in rows))
        self.assertIn(f'data-action-key="verify-{f.work.id}"',html)

    def test_completed_five_trucks_collapse_but_all_evidence_remains(self):
        f=self.f;f.prepare();f.target('60');other=f.fixture._nightly_truck('NEXT');db.session.commit()
        for truck in (other,f.truck,other,f.truck):f.add_plan(truck)
        works=[]
        for number in range(5):
            work=db.session.get(NeoScorpionFuelWorkState,f.assignment.active_work_state_id);works.append(work)
            f.finish_visit(work,15+number*4,transfer=str(100+number))
        for work in works:
            fuel.complete_fueled_assignment(f.gateway,f.dispatcher,f.assignment.id,
                physical_work_id=work.id,physical_sequence=work.physical_sequence_number);db.session.commit()
        html,rows=self.render()
        self.assertIsNotNone(f.assignment.completed_at_utc)
        self.assertEqual(len(rows),4)
        self.assertTrue(all(' hidden' in a for a,b in rows))
        for work in works:self.assertIn(f'data-visit-evidence="{work.id}"',html)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),5)

    def test_unnecessary_plan_review_and_explicit_cancel_remain_available(self):
        f=self.f;f.prepare();f.target('40');plan=f.add_plan(f.truck)
        f.save(f.start+timedelta(minutes=10),**{**f.final_data(),'actual_left':'21','actual_right':'21'})
        f.off();f.submit()
        html,rows=self.render()
        self.assertIn('data-visit-state>REVIEW</strong>',rows[0][1])
        self.assertIn(f'data-dispatch-action-target="plan-{plan.id}"',rows[0][1])
        self.assertIn('CANCEL PLANNED TRUCK',html)
        f.plan(f.truck,pending_action='cancel',pending_id=plan.id,expected_version=plan.version)
        html,rows=self.render();self.assertEqual(rows,[])

    def test_uplift_reference_rows_are_preserved_without_duplicate_old_visit(self):
        f=self.f;f.prepare();f.save(f.start+timedelta(minutes=10),**f.final_data());f.off();f.submit()
        fuel.complete_fueled_assignment(f.gateway,f.dispatcher,f.assignment.id);db.session.commit()
        fuel.start_follow_up_fuel_cycle(f.gateway,f.dispatcher,f.assignment.id,'uplift','40',f.fueler.id,f.truck.id);db.session.commit()
        html,rows=self.render()
        self.assertIn('data-cycle-history',html)
        self.assertIn('UPLIFT',html)
        self.assertEqual(rows,[])
        self.assertIn(f'data-visit-evidence="{f.work.id}"',html)
