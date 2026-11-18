"""New pending-to-approved handoff uses the existing physical Sort lock."""
import unittest
from threading import Barrier
from unittest.mock import patch
from app.extensions import db
from app.models import SortDateOperation, NeoScorpionVendorAccount, NeoScorpionVendorNight, NeoScorpionVendorRequest, NeoScorpionTruckDriver, NeoScorpionVendorAudit, NeoScorpionFuelingEvent
from app.services import neoscorpion_vendors as vendors
from tests import test_neoscorpion_vendor_postgres as existing
from tests import test_fuel_driver_sort_access as scenarios


@unittest.skipUnless(existing.segments.URI,'Requires disposable loopback PostgreSQL')
class FuelDriverClaimPostgresTest(unittest.TestCase):
    setUp=existing.VendorPostgresTest.setUp
    prepare=existing.VendorPostgresTest.prepare
    concurrent=existing.VendorPostgresTest.concurrent
    pending=scenarios.FuelDriverSortAccessTest.pending

    def drop_schema(self):
        # Preserve each synthetic schema and all previous jobs/databases.
        self.engine.dispose()

    def test_duplicate_pending_approval_connects_once_and_posts_no_transfer(self):
        user,account=self.pending()
        claim=vendors.request_claim(account,self.operation,self.truck.id);db.session.commit()
        op_id,request_id,user_id=self.operation.id,claim.id,user.id
        barrier=Barrier(2)
        def approve(index,gateway,_fueler,dispatcher):
            barrier.wait(5)
            try:
                vendors.decide_claim(gateway,db.session.get(SortDateOperation,op_id),dispatcher,request_id,True,0,expected_night_version=0)
                return 'accepted'
            except ValueError:
                db.session.rollback();return 'stale'
        self.assertEqual(sorted(self.concurrent(approve)),['accepted','stale'])
        self.assertEqual(NeoScorpionTruckDriver.query.filter_by(sort_date_operation_id=op_id,driver_user_id=user_id).count(),1)
        self.assertEqual(db.session.get(NeoScorpionVendorNight,(op_id,user_id)).version,1)
        self.assertEqual(NeoScorpionVendorAudit.query.filter_by(subject_user_id=user_id,action='claim_approved').count(),1)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)

    def test_failure_after_approval_rolls_back_claim_night_connection_and_audits(self):
        user,account=self.pending()
        claim=vendors.request_claim(account,self.operation,self.truck.id);db.session.commit()
        with patch('app.services.neoscorpion_drivers.change_driver',side_effect=ValueError('Synthetic conflict')):
            with self.assertRaises(ValueError):
                vendors.decide_claim(self.gateway,self.operation,self.dispatcher,claim.id,True,0,expected_night_version=0)
        db.session.rollback()
        self.assertIsNone(db.session.get(NeoScorpionVendorNight,(self.operation.id,user.id)))
        self.assertEqual(claim.status,'pending')
        self.assertEqual(NeoScorpionTruckDriver.query.filter_by(driver_user_id=user.id).count(),0)
        self.assertEqual(NeoScorpionVendorAudit.query.filter_by(subject_user_id=user.id,action='vendor_approved').count(),0)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)
