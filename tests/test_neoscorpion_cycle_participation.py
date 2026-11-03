"""Whole-cycle resource eligibility uses actual visits, not planning rows."""
import unittest
from datetime import timedelta
from sqlalchemy import event
from app.extensions import db
from app.models import NeoScorpionFuelingEvent
from app.services import neoscorpion as fuel, neoscorpion_participation as participation
from app.services.neoscorpion_prof import event_math, load_jobs, load_observation_jobs, totals
from tests import test_neoscorpion_continuations as fixtures


class CycleParticipationTest(unittest.TestCase):
    setUp=fixtures.ContinuationTest.setUp
    prepare=fixtures.ContinuationTest.prepare
    save=fixtures.ContinuationTest.save
    off=fixtures.ContinuationTest.off
    final_data=fixtures.ContinuationTest.final_data
    baseline=fixtures.ContinuationTest.baseline
    row=fixtures.ContinuationTest.row
    submit=fixtures.ContinuationTest.submit
    login=fixtures.ContinuationTest.login
    plan=fixtures.ContinuationTest.plan
    target=fixtures.ContinuationTest.target
    handoff=fixtures.ContinuationTest.handoff
    finish_child=fixtures.ContinuationTest.finish_child

    def test_three_actual_truck_visits_a_b_a_exclude_the_whole_cycle_without_history_writes(self):
        fixtures.ContinuationTest.test_three_visits_a_b_a_preserve_each_receipt_tanks_and_inventory_event(self)
        events=NeoScorpionFuelingEvent.query.order_by(NeoScorpionFuelingEvent.id).all()
        frozen=[e.performance_snapshot.copy() for e in events]
        gateway_id,operation_id=self.gateway.id,self.operation.id
        statements=[]
        def capture(_c,_cu,sql,*_args):statements.append(sql)
        event.listen(db.engine,'before_cursor_execute',capture)
        try:jobs=load_jobs(gateway_id,operation_id=operation_id)
        finally:event.remove(db.engine,'before_cursor_execute',capture)
        self.assertEqual(len(statements),3)
        self.assertTrue(all(sql.lstrip().upper().startswith('SELECT') for sql in statements))
        self.assertEqual(len(jobs),3)
        for job in jobs:
            self.assertIn(participation.MULTIPLE_TRUCKS,job['fueler_reasons'])
            self.assertIn(participation.MULTIPLE_TRUCKS,job['truck_reasons'])
            self.assertIsNone(job['fueler_prof']);self.assertIsNone(job['truck_prof'])
        self.assertEqual(totals(jobs,'fueler')['jobs'],0)
        self.assertTrue(all(j['fueler_prof'] is None for j in load_observation_jobs(self.gateway.id,operation_id=self.operation.id)))
        self.assertEqual([e.performance_snapshot for e in events],frozen)

    def test_planned_unused_next_truck_does_not_exclude_the_actual_single_truck(self):
        plan,child=self.handoff()
        self.assertIsNone(child.manual_on_at_utc)
        fuel.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id,
            physical_work_id=self.work.id,physical_sequence=self.work.physical_sequence_number);db.session.commit()
        source=NeoScorpionFuelingEvent.query.one()
        evidence=participation.cycles([source])[participation.key(source)]
        self.assertEqual(evidence['truck_ids'],[self.truck.id])
        self.assertEqual(participation.reasons(evidence),[])

    def test_missing_or_ambiguous_participation_excludes_both_resources(self):
        for data,expected in ((None,participation.UNVERIFIED),
            ({'version':1,'verified':False,'fueler_ids':[1],'truck_ids':[2]},participation.UNVERIFIED),
            ({'version':1,'verified':True,'fueler_ids':[1,2],'truck_ids':[2]},participation.MULTIPLE_FUELERS),
            ({'version':1,'verified':True,'fueler_ids':[1],'truck_ids':[2,3]},participation.MULTIPLE_TRUCKS)):
            with self.subTest(data=data):self.assertIn(expected,participation.reasons(data))

    def test_archived_assessment_cannot_be_reused_for_a_different_cycle_or_visit(self):
        from tests import test_neoscorpion_resource_prof as math_fixtures
        e=math_fixtures.ResourceProfMathTest().event()
        valid=dict(version=1,verified=True,assignment_id=e.fuel_assignment_id,cycle_number=e.cycle_number,
                   work_ids=[e.fuel_work_state_id],fueler_ids=[e.fueler_user_id],truck_ids=[e.fuel_truck_id])
        e.performance_snapshot['prof_participation']=valid
        self.assertIsNotNone(event_math(e)['fueler_prof'])
        for change in ({'assignment_id':999},{'cycle_number':2},{'work_ids':[1000]},
                       {'fueler_ids':[1000]},{'truck_ids':[1000]}):
            with self.subTest(change=change):
                e.performance_snapshot['prof_participation']={**valid,**change}
                self.assertIn(participation.UNVERIFIED,event_math(e)['fueler_reasons'])
                self.assertIsNone(event_math(e)['truck_prof'])

    def test_two_actual_fuelers_and_trucks_exclude_both_measurements(self):
        from app.models import NeoScorpionSortFueler
        plan,child=self.handoff()
        fuel.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id,
            physical_work_id=self.work.id,physical_sequence=self.work.physical_sequence_number);db.session.commit()
        operator=self.fixture._login_user('second-cycle-operator','operator')
        db.session.add(NeoScorpionSortFueler(sort_date_operation_id=self.operation.id,user_id=operator.id));db.session.commit()
        fuel.swap_assignment_fueler(self.gateway,self.dispatcher,self.assignment.id,operator.id);db.session.commit()
        self.fueler=operator
        self.finish_child(plan)
        fuel.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id);db.session.commit()
        events=NeoScorpionFuelingEvent.query.all()
        assessment=participation.cycles(events)
        for e in events:
            self.assertEqual(participation.reasons(assessment[participation.key(e)]),
                [participation.MULTIPLE_FUELERS,participation.MULTIPLE_TRUCKS])
            self.assertIsNone(event_math(e)['fueler_prof'])


def test_real_defuel_cycle_uses_the_same_single_resource_eligibility():
    from tests import test_neoscorpion_uplift_defuel as defuel
    case=defuel.NeoScorpionUpliftDefuelTest();case.setUp()
    try:
        case.test_defuel_adds_gallons_sets_sump_and_holds_other_assignment()
        e=NeoScorpionFuelingEvent.query.filter_by(event_type='defuel').one()
        self_evidence=participation.cycles([e])[participation.key(e)]
        assert participation.reasons(self_evidence)==[]
        assert 'No qualifying positive FUEL/UPLIFT/DEFUEL transfer' not in event_math(e)['fueler_reasons']
    finally:case.tearDown()
