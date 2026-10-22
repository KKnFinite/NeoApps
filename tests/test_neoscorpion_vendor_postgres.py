"""Real row-lock races and additive bootstrap in disposable local PostgreSQL."""
import unittest
from unittest.mock import patch
from datetime import timedelta
from sqlalchemy import text, inspect
from app.extensions import db
from app.models import (User, SortDateOperation, NeoScorpionVendorAccount, NeoScorpionVendorRequest,
    NeoScorpionSortTruck, NeoScorpionTruckDriver, NeoScorpionInventoryReading, NeoScorpionFuelingEvent)
from app.services import neoscorpion_vendors as vendors, neoscorpion_inventory as inventory, neoscorpion as service
from app.services.neoscorpion_schema import ensure_neoscorpion_production_schema
from tests import test_neoscorpion_segments_postgres as segments
from tests import test_neoscorpion_vendor_workflow as workflow
from tests import test_neoscorpion_fueler_submission as submissions


@unittest.skipUnless(segments.URI,'Requires disposable local PostgreSQL')
class VendorPostgresTest(unittest.TestCase):
    setUp=segments.PhysicalSegmentPostgresTest.setUp
    drop_schema=segments.PhysicalSegmentPostgresTest.drop_schema
    prepare=segments.PhysicalSegmentPostgresTest.prepare
    concurrent=segments.PhysicalSegmentPostgresTest.concurrent
    vendor=workflow.VendorWorkflowTest.vendor
    connect=workflow.VendorWorkflowTest.connect
    save=workflow.VendorWorkflowTest.save
    off=workflow.VendorWorkflowTest.off
    final_data=workflow.VendorWorkflowTest.final_data
    next_visit=workflow.VendorWorkflowTest.next_visit
    row=submissions.SubmissionTest.row
    baseline=submissions.SubmissionTest.baseline
    submit=submissions.SubmissionTest.submit

    def test_competing_baselines_accept_one_version_without_duplicate_epochs(self):
        operation_id,truck_id=self.operation.id,self.truck.id
        def change(index,gateway,fueler,dispatcher):
            try:
                inventory.correct_baseline(gateway,db.session.get(SortDateOperation,operation_id),dispatcher,truck_id,5500+index,0)
                return 'accepted'
            except ValueError:
                db.session.rollback();return 'stale'
        self.assertEqual(sorted(self.concurrent(change)),['accepted','stale'])
        self.assertEqual(NeoScorpionInventoryReading.query.count(),1)
        self.assertEqual(NeoScorpionSortTruck.query.filter_by(fuel_truck_id=truck_id).one().inventory_epoch,1)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)

    def test_duplicate_claim_approval_cannot_duplicate_asset_or_connection(self):
        from app.models import NeoScorpionFuelTruck
        user,account,_token=self.vendor()
        truck=NeoScorpionFuelTruck(gateway_id=self.gateway.id,truck_number='PG-NEW',capacity_gallons=10000)
        db.session.add(truck);db.session.commit()
        request=vendors.request_claim(account,self.operation,truck.id,'5500');db.session.commit()
        operation_id,request_id,truck_id,user_id=self.operation.id,request.id,truck.id,user.id
        def approve(index,gateway,fueler,dispatcher):
            try:
                vendors.decide_claim(gateway,db.session.get(SortDateOperation,operation_id),dispatcher,request_id,True,0)
                return 'accepted'
            except ValueError:
                db.session.rollback();return 'stale'
        self.assertEqual(sorted(self.concurrent(approve)),['accepted','stale'])
        self.assertEqual(NeoScorpionSortTruck.query.filter_by(fuel_truck_id=truck_id).count(),1)
        self.assertEqual(NeoScorpionTruckDriver.query.filter_by(fuel_truck_id=truck_id,driver_user_id=user_id).count(),1)

    def test_dispatcher_baseline_racing_completion_never_erases_posted_transfer(self):
        self.selection.current_gallons=6000;db.session.commit()
        self.save(self.start+timedelta(minutes=10),**{**self.final_data(),'transfer_fuel_gallons':'1200'});self.off()
        # Dispatcher verification is intentionally independent of physical OFF
        # and requires a durable Fueler receipt before this inventory race.
        self.submit()
        operation_id,truck_id,assignment_id=self.operation.id,self.truck.id,self.assignment.id
        def action(index,gateway,fueler,dispatcher):
            if index==0:
                try:
                    inventory.correct_baseline(gateway,db.session.get(SortDateOperation,operation_id),dispatcher,truck_id,5500,0,0)
                    return 'corrected'
                except ValueError:
                    db.session.rollback();return 'stale'
            service.complete_fueled_assignment(gateway,dispatcher,assignment_id)
            return 'posted'
        results=self.concurrent(action)
        self.assertIn('posted',results)
        expected=4300 if 'corrected' in results else 4800
        self.assertEqual(NeoScorpionSortTruck.query.filter_by(fuel_truck_id=truck_id).one().current_gallons,expected)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)

    def test_invitation_concurrent_acceptance_is_single_use(self):
        user,token=vendors.invite(self.gateway,self.dispatcher,'pg-invite@example.test','Single Use');db.session.commit()
        def accept(index,gateway,fueler,dispatcher):
            try:
                vendors.accept_invitation(token,'VendorPassword123!','VendorPassword123!');return 'accepted'
            except ValueError:
                db.session.rollback();return 'used'
        self.assertEqual(sorted(self.concurrent(accept)),['accepted','used'])

    def test_two_drivers_cannot_silently_acquire_one_truck(self):
        user,account,_token=self.vendor()
        first=vendors.request_claim(account,self.operation,self.truck.id);db.session.commit()
        second_user,token=vendors.invite(self.gateway,self.dispatcher,'pg-second@example.test','Second Driver');db.session.commit()
        vendors.accept_invitation(token,'VendorPassword123!','VendorPassword123!')
        vendors.set_participation(self.gateway,self.operation,self.dispatcher,second_user.id,'approved',0);db.session.commit()
        second=vendors.request_claim(db.session.get(NeoScorpionVendorAccount,second_user.id),self.operation,self.truck.id);db.session.commit()
        request_ids=[first.id,second.id];operation_id,truck_id=self.operation.id,self.truck.id
        def approve(index,gateway,fueler,dispatcher):
            try:
                vendors.decide_claim(gateway,db.session.get(SortDateOperation,operation_id),dispatcher,request_ids[index],True,0)
                return 'approved'
            except ValueError:
                db.session.rollback();return 'conflict'
        self.assertEqual(sorted(self.concurrent(approve)),['approved','conflict'])
        self.assertEqual(NeoScorpionTruckDriver.query.filter_by(fuel_truck_id=truck_id).count(),1)
        self.assertEqual(NeoScorpionSortTruck.query.filter_by(fuel_truck_id=truck_id).one().current_gallons,10000)

    def test_reading_never_crosses_another_sort_physical_visit(self):
        self.off();user,_account=self.connect()
        request=inventory.submit_verification(self.gateway,self.operation,user,self.truck.id,'6000');db.session.commit()
        other=SortDateOperation(generated_by_user_id=self.dispatcher.id,gateway_id=self.gateway.id,gateway_code=self.gateway.code,
            sort_date=self.operation.sort_date+timedelta(days=1),sort_name='night',window_minutes=60)
        db.session.add(other);db.session.flush()
        mission=self.fixture._mission('UPS902','N492UP',25000,2);mission.sort_date_operation_id=other.id
        assignment=self.fixture._assignment(mission,truck=self.truck);assignment.sort_date_operation_id=other.id
        assignment.assigned_fueler_user_id=self.dispatcher.id
        selection=NeoScorpionSortTruck(sort_date_operation_id=other.id,fuel_truck_id=self.truck.id,status='available',starting_gallons=8000,current_gallons=8000)
        db.session.add(selection);db.session.commit()
        with patch.object(service,'current_sort_operation',return_value=other):
            service.confirm_fueler_truck_on(self.gateway,self.dispatcher,assignment.id,expected_cycle=1,
                expected_tail='N492UP',expected_truck_id=self.truck.id)
            db.session.commit()
        self.assertEqual(request.status,'expired')
        self.assertEqual(self.selection.current_gallons,10000)
        self.assertEqual(selection.current_gallons,8000)

    def test_pending_approval_racing_next_transfer_cannot_overwrite_new_transfer(self):
        self.off();user,_account=self.connect()
        request=inventory.submit_verification(self.gateway,self.operation,user,self.truck.id,'6000');db.session.commit()
        assignment=self.next_visit()
        request_id,operation_id,assignment_id,truck_id=request.id,self.operation.id,assignment.id,self.truck.id
        at=self.start+timedelta(minutes=20)
        def action(index,gateway,fueler,dispatcher):
            if index==0:
                try:
                    result=inventory.decide_verification(gateway,db.session.get(SortDateOperation,operation_id),dispatcher,request_id,True,0)
                    return result.status
                except ValueError:
                    db.session.rollback();return 'expired'
            service.save_fueler_entry(gateway,dispatcher,dict(assignment_id=assignment_id,transfer_fuel_gallons='1200'),now_utc=at)
            return 'transfer'
        results=self.concurrent(action)
        request=db.session.get(NeoScorpionVendorRequest,request_id)
        self.assertIn(request.status,{'approved','expired'})
        self.assertIn('transfer',results)
        balance=NeoScorpionSortTruck.query.filter_by(fuel_truck_id=truck_id).one()
        self.assertEqual(balance.current_gallons,6000 if request.status=='approved' else 10000)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)

    def test_bootstrap_additive_idempotent_preserves_existing_inventory_and_capacity(self):
        self.save(self.start+timedelta(minutes=10),**self.final_data());self.off()
        self.submit()
        service.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id);db.session.commit()
        event=NeoScorpionFuelingEvent.query.one()
        event_before=(event.id,event.fuel_work_state_id,event.transfer_fuel_gallons,event.started_at_utc,event.ended_at_utc)
        original=(self.truck.capacity_gallons,self.selection.starting_gallons,self.selection.current_gallons,self.work.manual_on_at_utc)
        db.session.execute(text('ALTER TABLE neoscorpion_settings DROP COLUMN truck_fuel_discrepancy_threshold_gallons'))
        db.session.execute(text('ALTER TABLE neoscorpion_truck_drivers DROP COLUMN manual_name'))
        for table in ('neoscorpion_vendor_audits','neoscorpion_inventory_readings','neoscorpion_vendor_requests','neoscorpion_vendor_nights','neoscorpion_vendor_accounts'):
            db.session.execute(text('DROP TABLE '+table))
        db.session.commit()
        with patch.dict(self.fixture.app.config,{'TESTING':False}):
            ensure_neoscorpion_production_schema(self.fixture.app)
            ensure_neoscorpion_production_schema(self.fixture.app)
        db.session.expire_all()
        self.assertEqual(original,(self.truck.capacity_gallons,self.selection.starting_gallons,self.selection.current_gallons,self.work.manual_on_at_utc))
        self.assertEqual(self.settings.truck_fuel_discrepancy_threshold_gallons,1000)
        self.assertEqual(NeoScorpionVendorAccount.query.count(),0)
        event=NeoScorpionFuelingEvent.query.one()
        self.assertEqual(event_before,(event.id,event.fuel_work_state_id,event.transfer_fuel_gallons,event.started_at_utc,event.ended_at_utc))
        self.assertIn('manual_name',{c['name'] for c in inspect(db.engine).get_columns('neoscorpion_truck_drivers')})
