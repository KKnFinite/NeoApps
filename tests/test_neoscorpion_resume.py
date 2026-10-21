"""Premature OFF resumes the same unfinished visit, without inventing work."""
import unittest
from datetime import timedelta
from app.extensions import db
from app.models import NeoScorpionFuelWorkState,NeoScorpionFuelingEvent,NeoScorpionFuelAuditEntry
from app.services import neoscorpion as fuel
from tests.test_neoscorpion_dispatch_details import DetailsTest


class ResumeTest(unittest.TestCase):
    setUp=DetailsTest.setUp
    prepare=DetailsTest.prepare
    save=DetailsTest.save
    off=DetailsTest.off
    final_data=DetailsTest.final_data
    baseline=DetailsTest.baseline
    row=DetailsTest.row
    submit=DetailsTest.submit
    login=DetailsTest.login

    def resume(self,stamp=None,now=None):
        return fuel.confirm_fueler_truck_on(self.gateway,self.fueler,self.assignment.id,
            expected_cycle=1,expected_tail=self.mission.assigned_tail_number,expected_truck_id=self.truck.id,
            expected_work_ref=self.work.id,require_work_ref=True,
            resume_off_at=stamp or self.work.off_at_utc.isoformat(),now_utc=now or self.start+timedelta(minutes=14))

    def test_full_same_visit_lifecycle_preserves_measurements_and_posts_once(self):
        self.prepare();self.save(self.start+timedelta(minutes=1),notes='Keep original visit',**self.final_data());self.off()
        original_on=self.work.manual_on_at_utc;off=self.work.off_at_utc.isoformat();wid=self.work.id
        stale_off_form=self.baseline()
        tanks=[(t.id,t.remaining_lbs,t.actual_lbs) for t in self.work.tank_states]
        self.assertTrue(self.resume()[0]);db.session.commit()
        self.assertEqual(self.work.id,wid);self.assertIsNone(self.work.off_at_utc)
        self.assertEqual(self.work.manual_on_at_utc,original_on)
        self.assertEqual(tanks,[(t.id,t.remaining_lbs,t.actual_lbs) for t in self.work.tank_states])
        self.assertEqual(self.work.notes,'Keep original visit');self.assertEqual(self.work.physical_transfer_gallons,3000)
        self.assertFalse(self.resume(off)[0]);db.session.commit()
        self.assertEqual(NeoScorpionFuelWorkState.query.count(),1)
        audit=NeoScorpionFuelAuditEntry.query.filter_by(field_name='resume_fueling').one()
        self.assertIn(off,audit.old_value);self.assertIn('resumed_at_utc=',audit.new_value)
        with self.assertRaises(fuel.FuelerDataConflict):
            fuel.mark_fueler_off(self.gateway,self.fueler,self.assignment.id,expected=stale_off_form)
        db.session.rollback();self.assertIsNone(self.work.off_at_utc)
        fuel.mark_fueler_off(self.gateway,self.fueler,self.assignment.id,now_utc=self.start+timedelta(minutes=20));db.session.commit()
        with self.assertRaises(fuel.FuelerDataConflict):self.resume(off,now=self.start+timedelta(minutes=21))
        db.session.rollback();self.submit()
        fuel.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id);db.session.commit()
        fuel.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id);db.session.commit()
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)
        self.assertEqual(self.selection.current_gallons,7000)
        self.assertIsNotNone(self.assignment.completed_at_utc)

    def test_explicit_resume_accepts_final_data_after_off_not_a_new_visit(self):
        self.prepare();self.save(self.start+timedelta(minutes=1),remaining_left='6',remaining_ctr='0',remaining_right='6',apu_running='no');self.off()
        self.resume();db.session.commit()
        self.save(self.start+timedelta(minutes=15),**self.final_data())
        fuel.mark_fueler_off(self.gateway,self.fueler,self.assignment.id,now_utc=self.start+timedelta(minutes=20));db.session.commit();self.submit()
        self.assertEqual(NeoScorpionFuelWorkState.query.count(),1)
        self.assertTrue(self.row()['normal_completion_ready'])

    def test_old_on_and_unconfirmed_new_visit_do_not_resume_or_allocate(self):
        self.prepare();self.off()
        with self.assertRaisesRegex(ValueError,'already OFF'):
            fuel.confirm_fueler_truck_on(self.gateway,self.fueler,self.assignment.id,expected_cycle=1,
                expected_tail='N491UP',expected_truck_id=self.truck.id)
        db.session.rollback()
        with self.assertRaisesRegex(ValueError,'RESUME FUELING'):
            fuel.begin_physical_visit(self.gateway,self.fueler,self.assignment.id,expected=self.baseline())
        db.session.rollback();self.assertEqual(NeoScorpionFuelWorkState.query.count(),1)
        fuel.begin_physical_visit(self.gateway,self.fueler,self.assignment.id,expected=self.baseline(),separate_visit_confirmed=True)
        db.session.commit();self.assertEqual(NeoScorpionFuelWorkState.query.count(),2)

    def test_cached_zero_version_submit_still_works_but_resumed_stale_submit_does_not(self):
        self.prepare();self.save(self.start+timedelta(minutes=1),**self.final_data());self.off()
        cached=self.baseline();cached.pop('fueling_resume_version')
        self.assertTrue(self.submit(expected=cached).changed)

    def test_submitted_or_different_truck_cannot_resume_original(self):
        self.prepare();self.save(self.start+timedelta(minutes=1),**self.final_data());self.off();stamp=self.work.off_at_utc.isoformat();self.submit()
        with self.assertRaisesRegex(ValueError,'cannot resume'):self.resume(stamp)
        db.session.rollback()
        # A resource replacement must never be reconciled as the old visit.
        self.work.fueler_submitted_at_utc=None
        other=self.fixture._nightly_truck('OTHER');self.assignment.assigned_truck_id=other.id;db.session.commit()
        with self.assertRaisesRegex(ValueError,'Assigned truck changed'):self.resume(stamp)
        db.session.rollback();self.assertEqual(NeoScorpionFuelWorkState.query.count(),1)

    def duplicate_fixture(self):
        self.prepare();self.save(self.start+timedelta(minutes=1),apu_running='no',remaining_left='4.6',remaining_ctr='0',remaining_right='5.9');self.off()
        off=self.work.off_at_utc
        new=fuel.begin_physical_visit(self.gateway,self.fueler,self.assignment.id,expected=self.baseline(),
            separate_visit_confirmed=True,now_utc=off+timedelta(seconds=6)).fuel_work_state;db.session.commit()
        fuel.confirm_fueler_truck_on(self.gateway,self.fueler,self.assignment.id,expected_cycle=1,
            expected_tail='N491UP',expected_truck_id=self.truck.id,now_utc=off+timedelta(seconds=8));db.session.commit()
        fuel.save_fueler_entry(self.gateway,self.fueler,dict(assignment_id=self.assignment.id,
            remaining_left='4.6',remaining_ctr='0',remaining_right='5.9'));db.session.commit()
        return new,off

    def test_explicit_duplicate_repair_preserves_raw_records_and_finishes_original(self):
        from app.services.neoscorpion_resume import reconcile_duplicate_visit
        new,off=self.duplicate_fixture();raw_on=new.manual_on_at_utc;old_on=self.work.manual_on_at_utc
        result=reconcile_duplicate_visit(self.gateway,self.dispatcher,self.assignment.id,self.work.id,new.id,
            reason='Approved duplicate fixture repair',now_utc=off+timedelta(minutes=1));db.session.commit()
        self.assertEqual(result.id,self.work.id);self.assertEqual(self.assignment.active_work_state_id,self.work.id)
        self.assertEqual(NeoScorpionFuelWorkState.query.count(),2)  # Raw evidence is not deleted.
        self.assertEqual(new.manual_on_at_utc,raw_on);self.assertIsNone(new.off_at_utc)
        self.assertEqual(self.work.manual_on_at_utc,old_on);self.assertIsNone(self.work.off_at_utc)
        self.assertFalse(self.work.apu_running)
        self.assertEqual(new.performance_context['duplicate_of_work_id'],self.work.id)
        self.assertEqual(len(self.row()['console_visits']),1)
        self.assertEqual(NeoScorpionFuelAuditEntry.query.filter_by(field_name='resume_fueling').one().changed_by_user_id,self.dispatcher.id)
        self.save(off+timedelta(minutes=2),**self.final_data())
        fuel.mark_fueler_off(self.gateway,self.fueler,self.assignment.id,now_utc=off+timedelta(minutes=3));db.session.commit();self.submit()
        fuel.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id);db.session.commit()
        self.assertIsNotNone(self.assignment.completed_at_utc)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)
        self.assertEqual(self.selection.current_gallons,7000)

    def test_repair_rejects_conflicting_remaining_and_new_actual_without_writes(self):
        from app.services.neoscorpion_resume import reconcile_duplicate_visit
        new,off=self.duplicate_fixture();new.tank_states[0].remaining_lbs=9999;db.session.commit()
        with self.assertRaisesRegex(ValueError,'Conflicting Remaining'):
            reconcile_duplicate_visit(self.gateway,self.dispatcher,self.assignment.id,self.work.id,new.id,reason='Approved')
        db.session.rollback();self.assertEqual(self.assignment.active_work_state_id,new.id)
        self.assertIsNone(new.physical_finalized_at_utc);self.assertEqual(self.work.off_at_utc,off)
        new.tank_states[0].remaining_lbs=next(t.remaining_lbs for t in self.work.tank_states if t.tank_code==new.tank_states[0].tank_code)
        new.tank_states[0].actual_lbs=12000;db.session.commit()
        with self.assertRaisesRegex(ValueError,'New fuel evidence'):
            reconcile_duplicate_visit(self.gateway,self.dispatcher,self.assignment.id,self.work.id,new.id,reason='Approved')
        db.session.rollback();self.assertEqual(NeoScorpionFuelAuditEntry.query.filter_by(field_name='duplicate_visit_reconciliation').count(),0)

    def test_already_posted_duplicate_evidence_cannot_deduct_again(self):
        new,off=self.duplicate_fixture()
        fuel.save_fueler_entry(self.gateway,self.fueler,dict(assignment_id=self.assignment.id,
            apu_running='no',actual_left='15',actual_ctr='0',actual_right='15',transfer_fuel_gallons='2645'));db.session.commit()
        fuel.mark_fueler_off(self.gateway,self.fueler,self.assignment.id,now_utc=off+timedelta(minutes=1));db.session.commit();self.submit()
        fuel.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id);db.session.commit()
        fuel.save_fueler_entry(self.gateway,self.fueler,dict(assignment_id=self.assignment.id,
            physical_work_id=self.work.id,physical_sequence=self.work.physical_sequence_number,
            actual_left='15',actual_ctr='0',actual_right='15',transfer_fuel_gallons='2645'));db.session.commit()
        row=fuel.fueler_context(self.gateway,self.fueler,assignment_id=self.assignment.id,
            physical_work_id=self.work.id,physical_sequence=self.work.physical_sequence_number)['rows'][0]
        fuel.submit_fueler_assignment(self.gateway,self.fueler,self.assignment.id,expected=row['edit_baseline'],
            physical_work_id=self.work.id,physical_sequence=self.work.physical_sequence_number);db.session.commit()
        with self.assertRaisesRegex(ValueError,'already posted'):
            fuel.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id,
                physical_work_id=self.work.id,physical_sequence=self.work.physical_sequence_number)
        db.session.rollback();self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)
        self.assertEqual(self.selection.current_gallons,7355)
