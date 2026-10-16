"""A published amount changes review state; timestamps and refresh do not."""
from datetime import datetime, timedelta
import unittest
from sqlalchemy import event, delete
from app.extensions import db
from app.models import NeoRainFuelPublication, NeoScorpionFuelAssignment, NeoScorpionFuelWorkState, NeoScorpionFuelingEvent, NeoScorpionFuelTruck
from app.services.neorain_fuel_authority import completed_scorpion_fuel_by_mission, fuel_review_pending_by_mission, acknowledge_fuel_review
from tests import test_neorain_fuel_authority as fixtures


class FuelReviewRegressionTest(unittest.TestCase):
    setUp = fixtures.NeoRainFuelAuthorityTest.setUp
    tearDown = fixtures.NeoRainFuelAuthorityTest.tearDown

    def publish(self, *, complete=True, neo=45000, center=1000):
        truck=NeoScorpionFuelTruck(gateway_id=self.gateway.id,truck_number='90',capacity_gallons=20000)
        self.assignment=NeoScorpionFuelAssignment(sort_date_operation_id=self.operation.id,sort_date_mission_id=self.mission.id,
            completed_at_utc=datetime(2026,9,4,6) if complete else None)
        db.session.add_all([truck,self.assignment]);db.session.flush()
        self.work=NeoScorpionFuelWorkState(fuel_assignment_id=self.assignment.id,tail_number='N1')
        db.session.add(self.work);db.session.flush()
        self.event=NeoScorpionFuelingEvent(sort_date_operation_id=self.operation.id,fuel_assignment_id=self.assignment.id,
            fuel_work_state_id=self.work.id,tail_number='N1',fuel_truck_id=truck.id,sequence_number=1,
            neo_fuel_lbs=neo,center_fuel_lbs=center,created_at=datetime(2026,9,4,6,0,2),updated_at=datetime(2026,9,4,9))
        db.session.add(self.event);db.session.commit()

    def value(self):
        return completed_scorpion_fuel_by_mission(self.operation)[self.mission.id]

    def pending(self):
        return fuel_review_pending_by_mission(self.operation,{self.mission.id:self.value()})[self.mission.id]

    def test_initial_baseline_and_unrelated_updates_imports_and_reads_are_stable(self):
        self.publish();first=self.value();self.assertFalse(self.pending())
        self.event.updated_at += timedelta(hours=12)
        self.event.required_fuel_lbs=50000;self.event.transfer_fuel_gallons=1200
        self.assignment.completed_at_utc += timedelta(seconds=1)
        self.mission.flight_number='UPS101';db.session.commit()
        for _ in range(3):
            self.assertEqual(self.value()['revision'],first['revision']);self.assertFalse(self.pending())
        pub=NeoRainFuelPublication.query.one()
        self.assertEqual((pub.baseline_neo_fuel_lbs,pub.baseline_center_fuel_lbs,pub.change_number),(45000,1000,0))
        statements=[]
        def capture(conn,cursor,statement,parameters,context,executemany):statements.append(statement)
        event.listen(db.engine,'before_cursor_execute',capture)
        try:self.value();self.value()
        finally:event.remove(db.engine,'before_cursor_execute',capture)
        self.assertFalse(any(s.lstrip().upper().startswith(('INSERT','UPDATE','DELETE','ALTER','CREATE')) for s in statements))

    def test_each_actual_change_acknowledgement_and_return_to_prior_amount(self):
        self.publish();initial=self.value()['revision']
        for field,amount in [('neo_fuel_lbs',45100),('center_fuel_lbs',1500),('neo_fuel_lbs',45000),('center_fuel_lbs',1000)]:
            prior=self.value()['revision'];setattr(self.event,field,amount);db.session.commit()
            current=self.value();self.assertNotEqual(current['revision'],prior);self.assertNotEqual(current['revision'],initial)
            self.assertTrue(self.pending())
            with self.assertRaisesRegex(ValueError,'changed'):acknowledge_fuel_review(self.operation,self.mission.id,prior,self.user)
            acknowledge_fuel_review(self.operation,self.mission.id,current['revision'],self.user);db.session.commit()
            self.assertFalse(self.pending())
            self.event.updated_at+=timedelta(minutes=1);db.session.commit()
            self.assertFalse(self.pending());self.assertEqual(self.value()['revision'],current['revision'])
        self.assertEqual(NeoRainFuelPublication.query.one().change_number,4)

    def test_first_completion_with_final_measurements_in_same_flush_and_transaction(self):
        self.publish(complete=False,neo=12000,center=None)
        self.assertEqual(completed_scorpion_fuel_by_mission(self.operation),{})
        self.assignment.completed_at_utc=datetime(2026,9,4,7);self.event.neo_fuel_lbs=45000;self.event.center_fuel_lbs=1000
        db.session.flush()
        self.event.neo_fuel_lbs=45100;db.session.flush();db.session.commit()
        self.assertFalse(self.pending());self.assertEqual(NeoRainFuelPublication.query.one().baseline_neo_fuel_lbs,45100)
        self.event.neo_fuel_lbs=45200;db.session.commit();self.assertTrue(self.pending())

    def test_unchanged_new_cycle_preserves_ack_and_changed_cycle_reopens(self):
        self.publish();self.event.neo_fuel_lbs=45100;db.session.commit()
        reviewed=self.value()['revision'];acknowledge_fuel_review(self.operation,self.mission.id,reviewed,self.user);db.session.commit()
        second=NeoScorpionFuelingEvent(sort_date_operation_id=self.operation.id,fuel_assignment_id=self.assignment.id,
            fuel_work_state_id=self.work.id,tail_number='N1',fuel_truck_id=self.event.fuel_truck_id,sequence_number=2,
            cycle_number=2,neo_fuel_lbs=45100,center_fuel_lbs=1000)
        db.session.add(second);db.session.commit();self.assertFalse(self.pending());self.assertEqual(self.value()['revision'],reviewed)
        second.center_fuel_lbs=1100;db.session.commit();self.assertTrue(self.pending())

    def test_legacy_without_proven_baseline_does_not_infer_correction_from_timestamps(self):
        self.publish();db.session.execute(delete(NeoRainFuelPublication));db.session.commit()
        self.assertFalse(self.pending())
        self.event.updated_at+=timedelta(days=3);db.session.commit();self.assertFalse(self.pending())
        self.event.neo_fuel_lbs=46000;db.session.commit();self.assertTrue(self.pending())

    def test_rollback_preserves_baseline_and_zero_is_distinct_from_unknown(self):
        self.publish(neo=0,center=None);self.assertFalse(self.pending());revision=self.value()['revision']
        self.event.center_fuel_lbs=0;db.session.flush();self.assertTrue(self.pending());db.session.rollback()
        self.assertFalse(self.pending());self.assertEqual(self.value()['revision'],revision)
        self.event.center_fuel_lbs=0;db.session.commit();self.assertTrue(self.pending())
