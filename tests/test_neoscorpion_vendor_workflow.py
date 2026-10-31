"""Vendor isolation and inventory interval contracts using canonical fueling fixtures."""
from datetime import datetime, timedelta
from unittest import TestCase
from unittest.mock import patch
from tests.neoscorpion_handoff_fixture import submit_and_verify
from app.extensions import db
from app.models import (User, NeoScorpionVendorAccount, NeoScorpionVendorNight, NeoScorpionVendorRequest,
    NeoScorpionTruckDriver, NeoScorpionSortTruck, NeoScorpionFuelTruck, NeoScorpionFuelingEvent,
    NeoScorpionInventoryReading, NeoScorpionVendorAudit)
from app.services import neoscorpion_vendors as vendors, neoscorpion_inventory as inventory, neoscorpion as service
from app.services.neoscorpion_drivers import change_driver
from tests import test_neoscorpion_operational_timeline as timeline


class VendorWorkflowTest(TestCase):
    setUp=timeline.OperationalTimelineTest.setUp
    prepare=timeline.OperationalTimelineTest.prepare
    login=timeline.OperationalTimelineTest.login
    save=timeline.OperationalTimelineTest.save
    off=timeline.OperationalTimelineTest.off
    final_data=timeline.OperationalTimelineTest.final_data

    def vendor(self):
        user,token=vendors.invite(self.gateway,self.dispatcher,'driver@example.test','Vendor Driver')
        db.session.commit()
        vendors.accept_invitation(token,'VendorPassword123!','VendorPassword123!');db.session.commit()
        vendors.set_participation(self.gateway,self.operation,self.dispatcher,user.id,'approved',0);db.session.commit()
        return user,db.session.get(NeoScorpionVendorAccount,user.id),token

    def connect(self):
        user,account,token=self.vendor()
        claim=vendors.request_claim(account,self.operation,self.truck.id);db.session.commit()
        vendors.decide_claim(self.gateway,self.operation,self.dispatcher,claim.id,True,0);db.session.commit()
        return user,account

    def test_invitation_single_use_and_no_staffing_identity(self):
        user,account,token=self.vendor()
        self.assertIsNone(user.employee_id)
        self.assertIsNotNone(user.email_verified_at)
        self.assertEqual(account.gateway_id,self.gateway.id)
        with self.assertRaisesRegex(ValueError,'used'):
            vendors.accept_invitation(token,'AnotherPassword123!','AnotherPassword123!')
        db.session.rollback()

    def test_default_deny_all_other_routes_and_apis(self):
        user,account,_=self.vendor()
        self.client.post('/login',data={'email':user.email,'password':'VendorPassword123!'})
        for method,path in [('get','/neostaffing/'),('get','/motherbrain/'),('get','/neoscorpion/fuel-dispatch'),
                ('get','/neoscorpion/fuel-assignments/revision'),('get','/neorain/'),('post','/neoscorpion/fuel-dispatch/assets'),
                ('get','/admin/users'),('get','/neoscorpion/driver/manage'),('post','/neoscorpion/driver/inventory')]:
            self.assertEqual(getattr(self.client,method)(path).status_code,403,path)
        self.assertEqual(self.client.get('/neoscorpion/driver/account').status_code,200)
        account.enabled=False;db.session.commit()
        self.assertEqual(self.client.get('/neoscorpion/driver/live').status_code,403)

    def test_nonapproved_cannot_view_or_claim(self):
        user,account,_=self.vendor()
        vendors.set_participation(self.gateway,self.operation,self.dispatcher,user.id,'revoked',1);db.session.commit()
        with self.assertRaises(PermissionError):vendors.request_claim(account,self.operation,123)
        db.session.rollback()
        self.client.post('/login',data={'email':user.email,'password':'VendorPassword123!'})
        self.assertEqual(self.client.get('/neoscorpion/driver/my-truck').status_code,403)
        self.assertEqual(self.client.get('/neoscorpion/driver/live').status_code,403)

    def test_every_non_allowlisted_endpoint_is_default_denied(self):
        from flask import request
        from werkzeug.exceptions import Forbidden
        user,_account,_token=self.vendor()
        allowed={'static','healthz','auth.portal_dashboard','neonodes.index','auth.login','auth.logout',
            'auth.change_password','auth.forgot_password','auth.reset_password','auth.verify_email','auth.renew_csrf',
            'scorpion_vendor.account','scorpion_vendor.my_truck','scorpion_vendor.live',
            'scorpion_vendor.revision','scorpion_vendor.fuel_reading','scorpion_vendor.invitation'}
        checked=0
        for rule in self.fixture.app.url_map.iter_rules():
            if rule.endpoint in allowed:continue
            with self.fixture.app.test_request_context('/'):
                request.url_rule=rule
                with patch.object(vendors,'current_user',user):
                    with self.assertRaises(Forbidden,msg=rule.endpoint):vendors.vendor_request_boundary()
            checked+=1
        self.assertGreater(checked,100)

    def test_pending_claim_does_not_create_asset_approval_captures_opening_once(self):
        user,account,_=self.vendor()
        truck=NeoScorpionFuelTruck(gateway_id=self.gateway.id,truck_number='V-22',capacity_gallons=10000,is_active=True)
        db.session.add(truck);db.session.commit()
        claim=vendors.request_claim(account,self.operation,truck.id,'5500');db.session.commit()
        self.assertIsNone(NeoScorpionSortTruck.query.filter_by(fuel_truck_id=truck.id).first())
        self.assertEqual(vendors.request_claim(account,self.operation,truck.id,'5500').id,claim.id)
        vendors.decide_claim(self.gateway,self.operation,self.dispatcher,claim.id,True,0);db.session.commit()
        selected=NeoScorpionSortTruck.query.filter_by(fuel_truck_id=truck.id).one()
        self.assertEqual((selected.starting_gallons,selected.current_gallons),(5500,5500))
        with self.assertRaises(ValueError):vendors.decide_claim(self.gateway,self.operation,self.dispatcher,claim.id,True,0)

    def test_existing_asset_never_reset_and_verified_disconnect_does_not_restore_manual(self):
        self.prepare()
        user,account=self.connect()
        row=NeoScorpionTruckDriver.query.one()
        self.assertEqual(self.selection.current_gallons,10000)
        row.manual_name='OLD MANUAL';db.session.commit()
        vendors.disconnect(self.gateway,self.operation,self.dispatcher,user_id=user.id);db.session.commit()
        self.truck.vendor_driver_name='STALE LEGACY NAME';db.session.commit()
        self.assertEqual(vendors.driver_name(self.gateway,self.operation,self.truck.id),'UNKNOWN')
        self.assertEqual(service.fueler_context(self.gateway,self.fueler)['rows'][0]['truck_driver_name'],'UNKNOWN')
        self.assertEqual(self.assignment.assigned_truck_id,self.truck.id)
        self.assertIsNotNone(self.work.manual_on_at_utc)

    def test_active_baseline_correction_tf_once_and_original_start_preserved(self):
        self.prepare();self.selection.current_gallons=6000;db.session.commit()
        inventory.correct_baseline(self.gateway,self.operation,self.dispatcher,self.truck.id,'5500',0);db.session.commit()
        self.save(self.start+timedelta(minutes=5),**{**self.final_data(),'transfer_fuel_gallons':'1200'})
        self.off()
        submit_and_verify(self.gateway,self.dispatcher,self.assignment.id);db.session.commit()
        self.assertEqual(self.selection.current_gallons,4300)
        self.assertEqual(self.selection.starting_gallons,10000)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)
        submit_and_verify(self.gateway,self.dispatcher,self.assignment.id);db.session.commit()
        self.assertEqual(self.selection.current_gallons,4300)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)

    def test_optional_threshold_is_inclusive_and_never_changes_status(self):
        self.prepare();self.off()
        user,account=self.connect()
        reading=inventory.submit_verification(self.gateway,self.operation,user,self.truck.id,'9000');db.session.commit()
        self.assertEqual(reading.status,'approved')
        self.assertEqual(self.selection.current_gallons,9000)
        self.assertEqual(self.selection.status,'available')
        again=inventory.submit_verification(self.gateway,self.operation,self.fueler,self.truck.id,'8000');db.session.commit()
        self.assertEqual(again.id,reading.id)
        self.assertEqual(NeoScorpionInventoryReading.query.count(),1)
        self.assertEqual(NeoScorpionVendorAudit.query.filter_by(action='fuel_reading_approved',request_id=reading.id).count(),1)
        self.assertEqual(NeoScorpionVendorAudit.query.filter_by(action='fuel_reading_expired',request_id=reading.id).count(),0)

    def test_pending_is_nonblocking_and_manual_correction_invalidates_it(self):
        self.prepare();self.off();user,_account=self.connect()
        reading=inventory.submit_verification(self.gateway,self.operation,user,self.truck.id,'6000');db.session.commit()
        self.assertEqual(reading.status,'pending');self.assertEqual(self.selection.current_gallons,10000)
        inventory.correct_baseline(self.gateway,self.operation,self.dispatcher,self.truck.id,'8000',0);db.session.commit()
        self.assertEqual(reading.status,'expired');self.assertEqual(self.selection.current_gallons,8000)
        self.assertIsNone(self.assignment.completed_at_utc)

    def next_visit(self):
        mission=self.fixture._mission('UPS902','N492UP',25000,2)
        assignment=self.fixture._assignment(mission,truck=self.truck)
        assignment.assigned_fueler_user_id=self.dispatcher.id
        assignment.fueler_assigned_at_utc=assignment.truck_assigned_at_utc=self.start+timedelta(minutes=13)
        db.session.commit()
        service.confirm_fueler_truck_on(self.gateway,self.dispatcher,assignment.id,expected_cycle=1,
            expected_tail=mission.assigned_tail_number,expected_truck_id=self.truck.id,now_utc=self.start+timedelta(minutes=14))
        db.session.commit()
        return assignment

    def test_pending_survives_next_on_and_approval_covers_only_previous_transfer(self):
        self.prepare();self.off();user,_account=self.connect()
        reading=inventory.submit_verification(self.gateway,self.operation,user,self.truck.id,'6000');db.session.commit()
        next_assignment=self.next_visit()
        self.assertEqual(reading.status,'pending');self.assertIsNotNone(reading.next_work_id)
        inventory.decide_verification(self.gateway,self.operation,self.dispatcher,reading.id,True,0);db.session.commit()
        self.assertEqual(reading.status,'approved')
        self.save(self.start+timedelta(minutes=16),**self.final_data())
        submit_and_verify(self.gateway,self.dispatcher,self.assignment.id);db.session.commit()
        self.assertEqual(self.selection.current_gallons,6000)  # Previous transfer already in physical reading.
        service.save_fueler_entry(self.gateway,self.dispatcher,dict(assignment_id=next_assignment.id,
            **{**self.final_data(),'transfer_fuel_gallons':'1200'}),now_utc=self.start+timedelta(minutes=20))
        service.mark_fueler_off(self.gateway,self.dispatcher,next_assignment.id,now_utc=self.start+timedelta(minutes=25))
        submit_and_verify(self.gateway,self.dispatcher,next_assignment.id);db.session.commit()
        self.assertEqual(self.selection.current_gallons,4800)
        self.assertEqual(sorted(e.transfer_fuel_gallons for e in NeoScorpionFuelingEvent.query.all()),[1200,3000])
        submit_and_verify(self.gateway,self.dispatcher,next_assignment.id);db.session.commit()
        self.assertEqual(self.selection.current_gallons,4800)

    def test_pending_expires_on_next_off_before_any_actuals(self):
        self.prepare();self.off();user,_account=self.connect()
        reading=inventory.submit_verification(self.gateway,self.operation,user,self.truck.id,'6000');db.session.commit()
        next_assignment=self.next_visit()
        self.assertEqual(reading.status,'pending')
        service.mark_fueler_off(self.gateway,self.dispatcher,next_assignment.id,now_utc=self.start+timedelta(minutes=25));db.session.commit()
        self.assertEqual(reading.status,'expired')
        with self.assertRaises(ValueError):inventory.decide_verification(self.gateway,self.operation,self.dispatcher,reading.id,True,0)
        self.assertEqual(self.selection.current_gallons,10000)

    def test_pending_expires_on_next_transfer_entry_before_off(self):
        self.prepare();self.off();user,_account=self.connect()
        reading=inventory.submit_verification(self.gateway,self.operation,user,self.truck.id,'6000');db.session.commit()
        next_assignment=self.next_visit()
        service.save_fueler_entry(self.gateway,self.dispatcher,dict(assignment_id=next_assignment.id,transfer_fuel_gallons='1200'),now_utc=self.start+timedelta(minutes=20));db.session.commit()
        self.assertEqual(reading.status,'expired')
        self.assertEqual(self.selection.current_gallons,10000)

    def test_older_post_off_paperwork_does_not_expire_later_interval_reading(self):
        self.prepare();self.off();user,_account=self.connect()
        next_assignment=self.next_visit()
        service.mark_fueler_off(self.gateway,self.dispatcher,next_assignment.id,now_utc=self.start+timedelta(minutes=25));db.session.commit()
        reading=inventory.submit_verification(self.gateway,self.operation,user,self.truck.id,'6000');db.session.commit()
        self.save(self.start+timedelta(minutes=30),**self.final_data())
        self.assertEqual(reading.status,'pending')
        inventory.decide_verification(self.gateway,self.operation,self.dispatcher,reading.id,True,0);db.session.commit()
        submit_and_verify(self.gateway,self.dispatcher,self.assignment.id);db.session.commit()
        self.assertEqual(self.selection.current_gallons,6000)

    def test_physical_reading_then_baseline_never_rededucts_prior_transfer(self):
        self.prepare();self.off();user,_account=self.connect()
        reading=inventory.submit_verification(self.gateway,self.operation,user,self.truck.id,'9000');db.session.commit()
        inventory.correct_baseline(self.gateway,self.operation,self.dispatcher,self.truck.id,'8500',1);db.session.commit()
        self.save(self.start+timedelta(minutes=16),**self.final_data())
        submit_and_verify(self.gateway,self.dispatcher,self.assignment.id);db.session.commit()
        self.assertEqual(self.selection.current_gallons,8500)

    def test_limits_authorization_night_expiry_and_unverified_inventory(self):
        self.prepare();user,account=self.connect()
        with self.assertRaises(ValueError):inventory.submit_verification(self.gateway,self.operation,user,self.truck.id,'9000')
        db.session.rollback();self.off()
        for invalid in ('-1',str(self.truck.capacity_gallons+1),'1.5','','abc'):
            with self.assertRaises(ValueError):inventory.submit_verification(self.gateway,self.operation,user,self.truck.id,invalid)
            db.session.rollback()
        with self.assertRaises(PermissionError):inventory.correct_baseline(self.gateway,self.operation,user,self.truck.id,'5000',0)
        self.selection.status='unavailable_oos';self.selection.current_gallons=None
        self.selection.inventory_verified=False;db.session.commit()
        reading=inventory.submit_verification(self.gateway,self.operation,user,self.truck.id,'5000');db.session.commit()
        self.assertEqual(reading.status,'pending')
        self.assertIsNone(reading.calculated_gallons)
        self.operation.archived_at_utc=datetime.utcnow();db.session.commit()
        with self.assertRaises(PermissionError):vendors.require_approved(account,self.operation)

    def test_invitation_expiry_and_shared_password_policy(self):
        from app.models import UserToken
        user,token=vendors.invite(self.gateway,self.dispatcher,'expired@example.test','Expired')
        db.session.commit()
        with self.assertRaises(ValueError):vendors.accept_invitation(token,'weak','weak')
        db.session.rollback()
        record=UserToken.query.filter_by(user_id=user.id).one();record.expires_at=datetime.utcnow()-timedelta(seconds=1);db.session.commit()
        with self.assertRaisesRegex(ValueError,'expired'):vendors.accept_invitation(token,'VendorPassword123!','VendorPassword123!')

    def test_disabled_account_revokes_connection_approval_and_pending_reading(self):
        self.prepare();self.off();user,account=self.connect()
        request=inventory.submit_verification(self.gateway,self.operation,user,self.truck.id,'6000');db.session.commit()
        self.login(self.dispatcher)
        response=self.client.post('/neoscorpion/driver/manage',data=dict(action='disable',operation_id=self.operation.id,user_id=user.id))
        self.assertEqual(response.status_code,302)
        db.session.expire_all()
        self.assertFalse(account.enabled)
        self.assertEqual(request.status,'expired')
        self.assertEqual(db.session.get(NeoScorpionVendorNight,(self.operation.id,user.id)).status,'revoked')
        self.assertEqual(vendors.driver_name(self.gateway,self.operation,self.truck.id),'UNKNOWN')
        self.assertEqual(self.assignment.assigned_truck_id,self.truck.id)

    def test_new_request_preserves_current_connection_until_explicit_conflict_override(self):
        self.prepare();user,account=self.connect()
        second=NeoScorpionFuelTruck(gateway_id=self.gateway.id,truck_number='SWITCH',capacity_gallons=10000)
        db.session.add(second);db.session.commit()
        request=vendors.request_claim(account,self.operation,second.id,'5000');db.session.commit()
        self.assertEqual(NeoScorpionTruckDriver.query.filter_by(driver_user_id=user.id).one().fuel_truck_id,self.truck.id)
        with self.assertRaisesRegex(ValueError,'override'):
            vendors.decide_claim(self.gateway,self.operation,self.dispatcher,request.id,True,0)
        db.session.rollback()
        vendors.decide_claim(self.gateway,self.operation,self.dispatcher,request.id,True,0,override=True);db.session.commit()
        self.assertEqual(vendors.driver_name(self.gateway,self.operation,self.truck.id),'UNKNOWN')
        self.assertEqual(NeoScorpionTruckDriver.query.filter_by(driver_user_id=user.id).one().fuel_truck_id,second.id)

    def test_capacity_default_existing_preserved_and_initial_setup_cannot_rewrite_balance(self):
        from app.services.neoscorpion_assets import select_nightly_truck,update_nightly_truck
        self.prepare()
        truck=service.save_truck(self.gateway,dict(truck_number='NEW-DEFAULT',is_active='1'),self.dispatcher);db.session.commit()
        self.assertEqual(truck.capacity_gallons,10000)
        service.save_truck(self.gateway,dict(truck_id=self.truck.id,truck_number=self.truck.truck_number,is_active='1',capacity_gallons=''),self.dispatcher);db.session.commit()
        self.assertEqual(self.truck.capacity_gallons,20000)
        with self.assertRaisesRegex(ValueError,'already configured'):
            select_nightly_truck(self.operation,self.truck,status='available',current_gallons=5000,starting_gallons=5000,changed_by_user=self.dispatcher)
        db.session.rollback()
        update_nightly_truck(self.operation,self.truck,starting_gallons=1,changed_by_user=self.dispatcher);db.session.commit()
        self.assertEqual(self.selection.starting_gallons,10000)

    def test_invitation_renewal_invalidates_old_token_and_management_without_sort(self):
        user,old=vendors.invite(self.gateway,self.dispatcher,'renew@example.test','Renew');db.session.commit()
        _user,new=vendors.renew_invitation(self.gateway,self.dispatcher,user.id);db.session.commit()
        with self.assertRaises(ValueError):vendors.accept_invitation(old,'VendorPassword123!','VendorPassword123!')
        db.session.rollback()
        vendors.accept_invitation(new,'VendorPassword123!','VendorPassword123!');db.session.commit()
        self.login(self.dispatcher)
        with patch.object(service,'current_sort_operation',return_value=None):
            response=self.client.get('/neoscorpion/driver/manage')
            self.assertEqual(response.status_code,200)
            self.assertIn(b'NO ACTIVE SORT',response.data)

    def test_discrepancy_setting_persists_zero_and_rejects_invalid_and_unauthorized(self):
        data=dict(action='save_settings',red_assignment_alert_threshold_minutes='30',fuel_density_lbs_per_gallon='6.7',
            truck_fuel_discrepancy_threshold_gallons='0')
        self.login(self.fueler)
        self.assertEqual(self.client.post('/neoscorpion/settings',data=data).status_code,403)
        manager=self.fixture._login_user('vendor-settings','master');self.login(manager)
        from app.services.neoscorpion_admin_forms import settings_baseline
        data['settings_baseline']=settings_baseline(self.gateway,'save_settings')
        self.assertEqual(self.client.post('/neoscorpion/settings',data=data).status_code,302)
        data['settings_baseline']=settings_baseline(self.gateway,'save_settings')
        db.session.expire_all();self.assertEqual(self.settings.truck_fuel_discrepancy_threshold_gallons,0)
        for value in ('-1','1.5','','9999999999','unknown'):
            self.assertEqual(self.client.post('/neoscorpion/settings',data={**data,'truck_fuel_discrepancy_threshold_gallons':value}).status_code,400)
        self.assertEqual(self.settings.truck_fuel_discrepancy_threshold_gallons,0)

    def test_physical_reading_never_clears_sump_or_oos_protection(self):
        self.prepare();self.off();user,_account=self.connect()
        self.selection.status='needs_sump';self.truck.is_out_of_service=True;db.session.commit()
        reading=inventory.submit_verification(self.gateway,self.operation,user,self.truck.id,'9000');db.session.commit()
        self.assertEqual(reading.status,'approved')
        self.assertEqual(self.selection.status,'needs_sump');self.assertTrue(self.truck.is_out_of_service)
        with self.assertRaises(ValueError):service.confirm_fueler_truck_on(self.gateway,self.fueler,self.assignment.id,
            expected_cycle=1,expected_tail=self.mission.assigned_tail_number,expected_truck_id=self.truck.id)

    def test_top_off_return_expires_pending_and_baseline_correction_does_not_return_truck(self):
        from app.services.neoscorpion_assets import mark_nightly_truck_topping_off,complete_nightly_truck_top_off
        self.prepare();self.off();user,_account=self.connect()
        request=inventory.submit_verification(self.gateway,self.operation,user,self.truck.id,'6000');db.session.commit()
        mark_nightly_truck_topping_off(self.operation,self.truck,changed_by_user=self.dispatcher);db.session.commit()
        self.assertFalse(inventory.verification_available(self.operation,self.truck.id))
        complete_nightly_truck_top_off(self.operation,self.truck,15000,changed_by_user=self.dispatcher);db.session.commit()
        self.assertEqual(request.status,'expired');self.assertEqual(self.selection.current_gallons,15000)
        mark_nightly_truck_topping_off(self.operation,self.truck,changed_by_user=self.dispatcher);db.session.commit()
        inventory.correct_baseline(self.gateway,self.operation,self.dispatcher,self.truck.id,14000,self.selection.inventory_epoch);db.session.commit()
        self.assertEqual(self.selection.status,'topping_off')
        self.assertEqual(self.selection.starting_gallons,10000)

    def test_old_asset_form_never_silently_discards_a_balance_edit(self):
        self.prepare();self.login(self.dispatcher)
        form=dict(action='update_truck',fuel_truck_id=self.truck.id,status='available',current_gallons='5500',starting_gallons='1')
        response=self.client.post('/neoscorpion/fuel-dispatch/assets',data=form,follow_redirects=True)
        self.assertEqual(response.status_code,200)
        self.assertIn(b'corrections moved',response.data)
        self.assertEqual(self.selection.current_gallons,10000)
        response=self.client.post('/neoscorpion/fuel-dispatch/assets',data={**form,'current_gallons':'10000'})
        self.assertEqual(response.status_code,302)
        self.assertEqual(self.selection.starting_gallons,10000)
