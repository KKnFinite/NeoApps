"""Pending claims, physical work preservation and gateway-scoped read models."""
from datetime import datetime
from unittest import TestCase
from unittest.mock import patch
from flask_login import login_user
from app.extensions import db
from app.models import (User, FuelDriverIdentity, FuelVendorCompany, FuelVendorMembership, NeoScorpionVendorNight,
    NeoScorpionVendorAccount, NeoScorpionTruckDriver, NeoScorpionInventoryReading, NeoScorpionFuelingEvent, SortDateOperation)
from app.services import neoscorpion_vendors as vendors, neoscorpion as operations, fuel_driver_accounts as accounts
from app.services.accounts_workspace import review_token
from tests import test_neoscorpion_vendor_workflow as workflow
from tests.test_account_security_phase1 import PASSWORD

DRIVER=workflow.DRIVER


class FuelDriverSortAccessTest(TestCase):
    setUp=workflow.VendorWorkflowTest.setUp
    prepare=workflow.VendorWorkflowTest.prepare
    login=workflow.VendorWorkflowTest.login
    save=workflow.VendorWorkflowTest.save
    off=workflow.VendorWorkflowTest.off
    final_data=workflow.VendorWorkflowTest.final_data
    vendor=workflow.VendorWorkflowTest.vendor
    connect=workflow.VendorWorkflowTest.connect

    def pending(self):
        user,token=vendors.invite(self.gateway,self.dispatcher,'pending@example.test','Pending Driver')
        db.session.commit()
        vendors.accept_invitation(token,'VendorPassword123!','VendorPassword123!');db.session.commit()
        return user,db.session.get(NeoScorpionVendorAccount,user.id)

    def test_pending_claim_has_no_connection_inventory_or_participation_then_approval_atomic(self):
        self.prepare()
        user,account=self.pending()
        before=(self.selection.current_gallons,NeoScorpionInventoryReading.query.count(),NeoScorpionFuelingEvent.query.count())
        claim=vendors.request_claim(account,self.operation,self.truck.id);db.session.commit()
        self.assertEqual(claim.status,'pending')
        self.assertIsNone(db.session.get(NeoScorpionVendorNight,(self.operation.id,user.id)))
        self.assertFalse(NeoScorpionTruckDriver.query.filter_by(driver_user_id=user.id).count())
        with self.assertRaises(PermissionError): vendors.require_approved(account,self.operation)
        vendors.decide_claim(self.gateway,self.operation,self.dispatcher,claim.id,True,0,expected_night_version=0);db.session.commit()
        self.assertEqual(db.session.get(NeoScorpionVendorNight,(self.operation.id,user.id)).status,'approved')
        self.assertEqual(NeoScorpionTruckDriver.query.filter_by(driver_user_id=user.id).one().fuel_truck_id,self.truck.id)
        self.assertEqual(before,(self.selection.current_gallons,NeoScorpionInventoryReading.query.count(),NeoScorpionFuelingEvent.query.count()))
        with self.assertRaises(ValueError): vendors.decide_claim(self.gateway,self.operation,self.dispatcher,claim.id,True,0,expected_night_version=0)
        db.session.rollback()

    def test_failed_handoff_rolls_back_nightly_approval_and_audit(self):
        self.prepare();user,account=self.pending()
        claim=vendors.request_claim(account,self.operation,self.truck.id);db.session.commit()
        with patch('app.services.neoscorpion_drivers.change_driver',side_effect=ValueError('Synthetic conflict')):
            with self.assertRaisesRegex(ValueError,'Synthetic conflict'):
                vendors.decide_claim(self.gateway,self.operation,self.dispatcher,claim.id,True,0,expected_night_version=0)
        db.session.rollback()
        self.assertIsNone(db.session.get(NeoScorpionVendorNight,(self.operation.id,user.id)))
        self.assertEqual(claim.status,'pending')
        self.assertFalse(NeoScorpionTruckDriver.query.filter_by(driver_user_id=user.id).count())

    def test_preapproval_http_projection_no_missions_and_rollover_rejects_claim(self):
        self.prepare();user,account=self.pending()
        self.client.post('/login',data={'email':user.email,'password':'VendorPassword123!'})
        page=self.client.get(DRIVER+'/my-truck')
        self.assertIn(b'AWAITING DISPATCHER APPROVAL',page.data)
        self.assertNotIn(self.mission.assigned_tail_number.encode(),page.data)
        self.assertNotIn(b'ALL SORT MISSIONS',page.data)
        self.assertEqual(self.client.get(DRIVER+'/live').status_code,403)
        self.assertEqual(self.client.get(DRIVER+'/supervisor').status_code,403)
        self.assertEqual(self.client.get(DRIVER+'/supervisor/revision').status_code,403)
        with patch.object(operations,'current_sort_operation',return_value=None):
            self.client.post(DRIVER+'/my-truck',data={'operation_id':self.operation.id,'truck_id':self.truck.id})
        self.assertFalse(NeoScorpionTruckDriver.query.filter_by(driver_user_id=user.id).count())
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)

    def supervisor_fixture(self):
        self.prepare();driver,driver_account=self.connect()
        supervisor,token=vendors.invite(self.gateway,self.dispatcher,'supervisor@example.test','Company Supervisor')
        db.session.commit();vendors.accept_invitation(token,'VendorPassword123!','VendorPassword123!');db.session.commit()
        self.dispatcher.role='grandmaster';self.dispatcher.email_verified_at=datetime.utcnow();db.session.commit()
        with self.fixture.app.test_request_context():
            login_user(self.dispatcher)
            for user,value in [(driver,'DR-001'),(supervisor,'SV-001')]:
                accounts.verify_ups_id(user.id,review_token(user),value,'Synthetic identity attestation');db.session.commit()
            company=accounts.create_company(self.gateway.id,'Company A','Synthetic company attestation');db.session.commit()
            accounts.assign_company(driver.id,review_token(driver),company.id,False,'Synthetic driver affiliation', 'DriverPassword!1', driver.id)
            db.session.commit()
            accounts.assign_company(supervisor.id,review_token(supervisor),company.id,True,'Synthetic supervisor affiliation','DriverPassword!1',supervisor.id)
            db.session.commit()
        return driver,supervisor,company

    def test_supervisor_gateway_projection_and_no_driver_mutations(self):
        # Fixture password is set explicitly through the normal password service.
        from app.services.password_policy import set_user_password
        set_user_password(self.dispatcher,'DriverPassword!1');db.session.commit()
        driver,supervisor,company=self.supervisor_fixture()
        account=db.session.get(NeoScorpionVendorAccount,supervisor.id)
        context=accounts.supervisor_context(account)
        self.assertEqual([r['user_id'] for r in context['supervisor']['rows']],[driver.id])
        self.assertEqual(context['supervisor']['rows'][0]['missions'][0]['tail'],self.mission.assigned_tail_number)
        self.assertNotIn('gallons',str(context['supervisor']))
        self.assertNotIn(driver.employee_id,str(context['supervisor']))
        with self.fixture.app.test_request_context():
            login_user(self.dispatcher)
            with self.assertRaisesRegex(ValueError,'release current driver participation'):
                accounts.assign_company(driver.id,review_token(driver),company.id,True,'Synthetic unsafe active supervisor change','DriverPassword!1',driver.id)
        self.assertFalse(accounts.current_membership(driver.id).is_supervisor)
        # Company filters cannot narrow or expand the server-authorized gateway scope.
        with self.fixture.app.test_request_context():
            login_user(self.dispatcher)
            other=accounts.create_company(self.gateway.id,'Company B','Synthetic second company');db.session.commit()
        other_user,token=vendors.invite(self.gateway,self.dispatcher,'foreign-company@example.test','Foreign Company Driver')
        db.session.commit();vendors.accept_invitation(token,'VendorPassword123!','VendorPassword123!');db.session.commit()
        with self.fixture.app.test_request_context():
            login_user(self.dispatcher)
            accounts.verify_ups_id(other_user.id,review_token(other_user),'OTHER-01','Synthetic other driver verification');db.session.commit()
            accounts.assign_company(other_user.id,review_token(other_user),other.id,False,'Synthetic other company affiliation','DriverPassword!1',other_user.id);db.session.commit()
        self.assertEqual({r['user_id'] for r in accounts.supervisor_context(account)['supervisor']['rows']},{driver.id,other_user.id})
        from app.models import Gateway
        foreign_gateway=Gateway(code='ALT',name='Synthetic other gateway',is_active=True)
        db.session.add(foreign_gateway);db.session.flush()
        from tests.test_account_security_phase1 import fixture_user
        foreign_user=fixture_user('foreign-gateway-driver')
        db.session.add(NeoScorpionVendorAccount(user_id=foreign_user.id,gateway_id=foreign_gateway.id,enabled=True,invited_by_user_id=self.dispatcher.id));db.session.commit()
        with self.fixture.app.test_request_context():
            login_user(self.dispatcher)
            accounts.verify_ups_id(foreign_user.id,review_token(foreign_user),'OTHER-GATEWAY','Synthetic cross gateway verification');db.session.commit()
        self.assertNotIn(foreign_user.id,{r['user_id'] for r in accounts.supervisor_context(account)['supervisor']['rows']})
        with self.assertRaises(PermissionError): vendors.request_claim(account,self.operation,self.truck.id)
        db.session.rollback()
        from flask import g
        g.pop('_login_user',None)
        self.client.post('/logout')
        g.pop('_login_user',None)
        self.client.post('/login',data={'email':supervisor.email,'password':'VendorPassword123!'})
        self.assertEqual(self.client.get(DRIVER+'/supervisor').status_code,200)
        self.assertIn(b'Foreign Company Driver',self.client.get(DRIVER+f'/supervisor?company_id={other.id}').data)
        self.assertEqual(self.client.get(DRIVER+'/supervisor/revision').status_code,200)
        self.assertEqual(self.client.post(DRIVER+'/fuel-reading',data={'operation_id':self.operation.id}).status_code,403)
        company.is_active=False;db.session.commit()
        self.assertEqual(self.client.get(DRIVER+'/supervisor/revision').status_code,200)
        db.session.get(NeoScorpionVendorAccount,supervisor.id).enabled=False;db.session.commit()
        response=self.client.get(DRIVER+'/supervisor/revision')
        self.assertEqual(response.status_code,302)
        self.assertIn('/login',response.location)
        with self.assertRaises(PermissionError): vendors.require_approved(account,self.operation)

    def test_unified_conversion_blocks_connected_driver_without_altering_active_operations(self):
        from app.services.account_types import decide,current_type
        self.prepare();driver,account=self.connect()
        self.dispatcher.role='grandmaster';self.dispatcher.email_verified_at=datetime.utcnow();db.session.commit()
        with self.fixture.app.test_request_context():
            login_user(self.dispatcher)
            accounts.verify_ups_id(driver.id,review_token(driver),'1234567','Synthetic verified driver identity');db.session.commit()
            before=(NeoScorpionTruckDriver.query.filter_by(driver_user_id=driver.id).one().fuel_truck_id,
                self.selection.current_gallons,NeoScorpionFuelingEvent.query.count())
            with self.assertRaisesRegex(ValueError,'release active truck claims'):
                decide(driver.id,review_token(driver),'vendor_supervisor','approved','1234567','yes',None,'')
            self.assertEqual(current_type(driver),'fuel_driver')
            self.assertEqual(before,(NeoScorpionTruckDriver.query.filter_by(driver_user_id=driver.id).one().fuel_truck_id,
                self.selection.current_gallons,NeoScorpionFuelingEvent.query.count()))
