"""Vendor isolation and inventory interval contracts using canonical fueling fixtures."""
from datetime import datetime, timedelta
from io import BytesIO
import json
import re
from unittest import TestCase
from unittest.mock import patch
from urllib.parse import urlparse
from tests.neoscorpion_handoff_fixture import submit_and_verify, submit_fueler_only
from app.extensions import db
from app.models import (User, NeoScorpionVendorAccount, NeoScorpionVendorNight, NeoScorpionVendorRequest,
    NeoScorpionTruckDriver, NeoScorpionSortTruck, NeoScorpionFuelTruck, NeoScorpionFuelingEvent,
    NeoScorpionInventoryReading, NeoScorpionVendorAudit, SortDateOperation)
from app.services import neoscorpion_vendors as vendors, neoscorpion_inventory as inventory, neoscorpion as service
from app.services.neoscorpion_drivers import change_driver
from tests import test_neoscorpion_operational_timeline as timeline


DRIVER = '/neoscorpion/driver'


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

    def sort_mission_groups(self):
        response = self.client.get(f'{DRIVER}/my-truck')
        self.assertEqual(response.status_code, 200)
        section = response.get_data(as_text=True).split('data-vendor-sort-missions', 1)[1].split('</section>', 1)[0]
        return {
            group: re.findall(r'<tr>((?:<td>.*?</td>){4})</tr>', content)
            for group, content in re.findall(r'data-sort-mission-group="(\w+)">(.*?)</tbody>', section, re.S)
        }

    def test_sort_missions_follow_canonical_physical_and_dispatch_lifecycle(self):
        self.prepare()
        self.fixture._mission('UPS987', 'N876UP', 25000, 13, destination='PHX')
        self.fixture._mission('UPS986', 'N875UP', 25000, 9, destination='ONT')
        user, _ = self.connect()
        self.client.post('/login', data={'email': user.email, 'password': 'VendorPassword123!'})

        groups = self.sort_mission_groups()
        self.assertEqual(list(groups), ['working', 'left', 'complete'])
        self.assertEqual(groups['left'], [
            '<td>N875UP</td><td>ONT</td><td>-</td><td>22:09</td>',
            '<td>N876UP</td><td>PHX</td><td>-</td><td>22:13</td>',
        ])
        self.assertIn('<td>B06</td>', groups['working'][0])
        self.assertFalse(groups['complete'])

        prior_status = self.assignment.operational_status
        self.assignment.operational_status = 'hold_review'
        db.session.commit()
        self.assertIn('N491UP', self.sort_mission_groups()['working'][0])
        self.assignment.operational_status = prior_status
        db.session.commit()
        self.save(self.start + timedelta(minutes=10), **self.final_data())
        self.off()
        self.assertIn('N491UP', self.sort_mission_groups()['working'][0])
        submit_fueler_only(self.gateway, self.assignment.id)
        db.session.commit()
        self.assertIn('N491UP', self.sort_mission_groups()['working'][0])
        self.assertFalse(self.sort_mission_groups()['complete'])

        service.complete_fueled_assignment(self.gateway, self.dispatcher, self.assignment.id)
        db.session.commit()
        groups = self.sort_mission_groups()
        self.assertFalse(groups['working'])
        self.assertIn('N491UP', groups['complete'][0])

        service.start_follow_up_fuel_cycle(self.gateway, self.dispatcher, self.assignment.id, 'uplift', '35.0',
            self.fueler.id, self.truck.id, expected_cycle=1, expected_tail=self.mission.assigned_tail_number)
        db.session.commit()
        groups = self.sort_mission_groups()
        self.assertFalse(groups['working'])
        self.assertFalse(groups['complete'])
        self.assertIn('N491UP', ''.join(groups['left']))

    def test_sort_missions_fob_awaits_dispatch_completion(self):
        self.truck = self.fixture._nightly_truck()
        self.assignment.assigned_fueler_user_id = self.fueler.id
        service.save_fueler_entry(self.gateway, self.fueler, dict(assignment_id=self.assignment.id,
            apu_running='no', remaining_left='30', remaining_ctr='0', remaining_right='0',
            actual_left='30', actual_ctr='0', actual_right='0'))
        db.session.commit()
        user, _ = self.connect()
        self.client.post('/login', data={'email': user.email, 'password': 'VendorPassword123!'})
        self.assertIn('N491UP', self.sort_mission_groups()['left'][0])
        service.complete_fuel_on_board(self.gateway, self.dispatcher, self.assignment.id)
        db.session.commit()
        self.assertIn('N491UP', self.sort_mission_groups()['complete'][0])

    def test_sort_missions_hide_on_sort_change_and_revocation(self):
        self.prepare()
        user, _ = self.connect()
        old_operation = self.operation.id
        other_sort = SortDateOperation(sort_date=self.operation.sort_date, gateway_id=self.gateway.id,
            gateway_code=self.gateway.code, sort_name='different-sort')
        other_mission = self.fixture._mission('UPS999', 'N999UP', 25000, 20)
        other_mission.sort_date_operation = other_sort
        db.session.commit()

        self.client.post('/login', data={'email': user.email, 'password': 'VendorPassword123!'})
        page = self.client.get(f'{DRIVER}/my-truck').data
        self.assertIn(b'ALL SORT MISSIONS', page)
        self.assertNotIn(b'N999UP', page)
        revision_url = f'{DRIVER}/revision?operation_id={old_operation}'
        self.assertIsNotNone(self.client.get(revision_url).json['sort_missions_revision'])
        with patch.object(service, 'current_sort_operation', return_value=other_sort):
            self.assertEqual(self.client.get(revision_url).status_code, 403)
            page = self.client.get(f'{DRIVER}/my-truck').data
            self.assertNotIn(b'ALL SORT MISSIONS', page)
            self.assertNotIn(b'N491UP', page)
            self.assertNotIn(b'N999UP', page)
        vendors.set_participation(self.gateway, self.operation, self.dispatcher, user.id, 'revoked', 1)
        db.session.commit()
        self.assertEqual(self.client.get(revision_url).status_code, 403)
        self.assertNotIn(b'ALL SORT MISSIONS', self.client.get(f'{DRIVER}/my-truck').data)

    def test_conversion_rejects_a_selected_nightly_fueler(self):
        self.prepare()
        from app.services.neoscorpion_account_types import convert_account
        from app.models import NeoScorpionSortFueler
        if self.fueler.email_verified_at is None:
            self.fueler.email_verified_at = datetime.utcnow()
            db.session.commit()
        self.assertTrue(NeoScorpionSortFueler.query.filter_by(
            sort_date_operation_id=self.operation.id, user_id=self.fueler.id,
        ).first())
        with self.assertRaisesRegex(ValueError, "Nightly Fuelers"):
            convert_account(self.gateway, self.dispatcher, self.fueler, "vendor")
        db.session.rollback()
        self.assertIsNone(db.session.get(NeoScorpionVendorAccount, self.fueler.id))

    def test_conversion_with_sort_rollover_leaves_existing_classification_intact(self):
        from app.services.neoscorpion_account_types import convert_account
        user, _account, _token = self.vendor()
        with patch.object(service, 'current_sort_operation', side_effect=[self.operation, None]):
            with self.assertRaisesRegex(ValueError, 'active Sort changed'):
                convert_account(self.gateway, self.dispatcher, user, 'full')
        db.session.rollback()
        self.assertIsNotNone(db.session.get(NeoScorpionVendorAccount, user.id))
        self.assertEqual(db.session.get(NeoScorpionVendorNight, (self.operation.id, user.id)).status, 'approved')

    def test_active_driver_conversion_revokes_live_access_without_erasing_history(self):
        self.prepare()
        driver, account = self.connect()
        from app.services.neoscorpion_account_types import convert_account
        from app.models import GatewayMembership
        driver_id = driver.id
        link = NeoScorpionTruckDriver.query.filter_by(
            sort_date_operation_id=self.operation.id, driver_user_id=driver_id,
        ).one()
        self.assertEqual(link.fuel_truck_id, self.truck.id)

        changed = convert_account(self.gateway, self.dispatcher, driver, "full")
        self.assertTrue(changed)
        db.session.commit()
        self.assertEqual(driver.id, driver_id)
        self.assertIsNone(db.session.get(NeoScorpionVendorAccount, driver_id))
        db.session.refresh(link)
        self.assertIsNone(link.driver_user_id)
        night = db.session.get(NeoScorpionVendorNight, (self.operation.id, driver_id))
        self.assertEqual(night.status, "revoked")
        membership = GatewayMembership.query.filter_by(
            gateway_id=self.gateway.id, user_id=driver_id,
        ).one()
        self.assertEqual(membership.status, "pending")
        self.assertTrue(NeoScorpionVendorAudit.query.filter_by(
            subject_user_id=driver_id, action="account_converted_full").first())

        self.assertTrue(convert_account(self.gateway, self.dispatcher, driver, "vendor"))
        db.session.commit()
        self.assertIsNotNone(db.session.get(NeoScorpionVendorAccount, driver_id))
        self.assertEqual(night.status, "revoked")
        self.assertEqual(User.query.filter_by(id=driver_id).count(), 1)

    def test_invitation_single_use_and_no_staffing_identity(self):
        user,account,token=self.vendor()
        self.assertIsNone(user.employee_id)
        self.assertIsNotNone(user.email_verified_at)
        self.assertEqual(account.gateway_id,self.gateway.id)
        with self.assertRaisesRegex(ValueError,'used'):
            vendors.accept_invitation(token,'AnotherPassword123!','AnotherPassword123!')
        db.session.rollback()

    def test_account_workspace_returns_current_controls_and_sort_scope(self):
        user, _, _ = self.vendor()
        self.login(self.dispatcher)
        manage_url = f'{DRIVER}/manage'
        operation_id = self.operation.id

        def update(action, version, state, actions):
            response = self.client.post(manage_url, data={
                'action': action, 'version': version, 'operation_id': operation_id,
                'user_id': user.id, 'dispatch_assets_workspace': '1',
            })
            self.assertEqual(response.status_code, 200, response.data)
            html = response.json['account_html']
            self.assertIn(state, html)
            self.assertEqual(re.findall(r'name="action" value="(\w+)"', html), actions)
            return response.json

        update('revoked', 1, 'ENABLED · REVOKED · EMAIL VERIFIED', ['approved', 'disable'])
        update('approved', 2, 'ENABLED · APPROVED · EMAIL VERIFIED', ['revoked', 'disable'])
        update('disable', 3, 'DISABLED · REVOKED · EMAIL VERIFIED', ['enable'])

        with patch.object(service, 'current_sort_operation', return_value=None):
            page = self.client.get(manage_url).get_data(as_text=True)
            self.assertIn('DISABLED · NOT APPROVED · EMAIL VERIFIED', page)
            self.assertEqual(re.findall(r'<button[^>]+name="action" value="(\w+)"', page), ['enable'])
            payload = update('enable', 4, 'ENABLED · NOT APPROVED · EMAIL VERIFIED', ['disable'])
            self.assertIsNone(payload['operation_id'])

        page = self.client.get(manage_url).get_data(as_text=True)
        self.assertIn('ENABLED · REVOKED · EMAIL VERIFIED', page)
        self.assertEqual(re.findall(r'<button[^>]+name="action" value="(\w+)"', page), ['approved', 'disable'])

    def test_default_deny_all_other_routes_and_apis(self):
        user,account,_=self.vendor()
        self.client.post('/login',data={'email':user.email,'password':'VendorPassword123!'})
        for method,path in [('get','/neostaffing/'),('get','/motherbrain/'),('get','/neoscorpion/fuel-dispatch'),
                ('get','/neoscorpion/fuel-assignments/revision'),('get','/neorain/'),('post','/neoscorpion/fuel-dispatch/assets'),
                ('get','/portal'),('get','/admin/users'),('get','/neoscorpion/driver/manage'),
                ('post','/neoscorpion/driver/inventory')]:
            self.assertEqual(getattr(self.client,method)(path).status_code,403,path)
        self.assertEqual(self.client.get('/login').location, f'{DRIVER}/my-truck')
        self.assertEqual(self.client.get(f'{DRIVER}/account').location, f'{DRIVER}/my-truck')
        account.enabled=False;db.session.commit()
        self.assertEqual(self.client.get(f'{DRIVER}/live').location, '/login')

    def test_nonapproved_cannot_view_or_claim(self):
        user,account,_=self.vendor()
        vendors.set_participation(self.gateway,self.operation,self.dispatcher,user.id,'revoked',1);db.session.commit()
        with self.assertRaises(PermissionError):vendors.request_claim(account,self.operation,123)
        db.session.rollback()
        self.client.post('/login',data={'email':user.email,'password':'VendorPassword123!'})
        response = self.client.get(f'{DRIVER}/my-truck')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'DISPATCHER APPROVAL REVOKED', response.data)
        self.assertNotIn(b'REQUEST DISPATCH APPROVAL', response.data)
        self.assertEqual(self.client.get('/neoscorpion/driver/live').status_code,403)

    def test_approved_truck_unlocks_missions_and_inactive_truck_can_be_disconnected(self):
        self.prepare()
        user, account, _ = self.vendor()
        self.client.post('/login', data={'email': user.email, 'password': 'VendorPassword123!'})
        driver_url = '/neoscorpion/driver'
        tail = self.mission.assigned_tail_number.encode()
        self.assertNotIn(tail, self.client.get(f'{driver_url}/my-truck').data)
        self.assertIsNone(self.client.get(f'{driver_url}/revision').json['sort_missions_revision'])
        self.assertEqual(self.client.get(f'{driver_url}/live').status_code, 403)
        claim = vendors.request_claim(account, self.operation, self.truck.id)
        vendors.decide_claim(self.gateway, self.operation, self.dispatcher, claim.id, True, 0)
        db.session.commit()
        self.assertIn(tail, self.client.get(f'{driver_url}/my-truck').data)
        self.assertIn(tail, self.client.get(f'{driver_url}/live').data)

        self.truck.is_active = False
        db.session.commit()
        response = self.client.post(f'{driver_url}/my-truck', data={
            'action': 'disconnect', 'operation_id': self.operation.id,
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'NO APPROVED TRUCK', response.data)
        self.assertNotIn(tail, response.data)
        self.assertNotIn(b'ALL SORT MISSIONS', response.data)
        self.assertIsNone(self.client.get(f'{driver_url}/revision').json['sort_missions_revision'])
        self.assertEqual(self.client.get(f'{driver_url}/live').status_code, 403)

    def test_legacy_invitation_uses_shared_verification_and_password_recovery(self):
        user, token = vendors.invite(self.gateway, self.dispatcher, 'isolated@example.test', 'Isolated Driver')
        db.session.commit()
        password = 'VendorPassword123!'
        response = self.client.post(f'{DRIVER}/invitation/{token}', data={
            'password': password, 'confirm_password': password,
        })
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.location, f'/verify-email/{token}')
        self.assertIsNone(user.email_verified_at)
        self.assertFalse(user.check_password(password))
        verified = self.client.get(response.location)
        self.assertIn(b'Set or Reset Password', verified.data)
        self.assertIsNotNone(user.email_verified_at)
        with patch('app.auth.routes.email_service.send_password_reset') as send:
            self.client.post('/forgot-password', data={'email': user.email})
            reset_token = send.call_args.args[1]
        old_reset = self.client.get(f'{DRIVER}/reset-password/{reset_token}')
        self.assertEqual(old_reset.location, f'/reset-password/{reset_token}')
        self.client.post(old_reset.location, data={'password': password, 'confirm_password': password})
        self.assertTrue(user.check_password(password))
        self.login(self.dispatcher)
        self.client.post(f'{DRIVER}/manage', data={'action': 'disable', 'user_id': user.id})
        self.client.post('/logout')
        response = self.client.post('/login', data={'email': user.email, 'password': password})
        self.assertEqual(response.status_code, 403)
        self.assertIn(b'account is disabled', response.data)

    def test_unified_login_waits_for_nightly_approval_and_invalidates_old_sessions(self):
        self.client.post('/logout')
        user, token = vendors.invite(self.gateway, self.dispatcher, 'waiting@example.test', 'Waiting Driver')
        db.session.commit()
        password = 'VendorPassword123!'
        credentials = {'email': user.email, 'password': password}
        vendors.accept_invitation(token, password, password)
        db.session.commit()
        self.assertEqual(self.client.get(f'{DRIVER}/my-truck').location, '/login')
        self.assertEqual(self.client.get(f'{DRIVER}/login').location, '/login')
        self.assertEqual(self.client.post(f'{DRIVER}/login', data=credentials).status_code, 307)
        self.assertEqual(self.client.post('/login', data=credentials).location, f'{DRIVER}/my-truck')
        waiting = self.client.get(f'{DRIVER}/my-truck')
        self.assertEqual(waiting.status_code, 200)
        self.assertIn(b'AWAITING DISPATCHER APPROVAL', waiting.data)
        self.assertNotIn(b'name="truck_id"', waiting.data)
        self.assertEqual(self.client.get(f'{DRIVER}/revision?waiting=1').json,
                         {'status': 'AWAITING DISPATCHER APPROVAL', 'operation_id': self.operation.id,
                          'claim': None, 'trucks': [], 'supervisor': False})
        self.assertEqual(self.client.get(f'{DRIVER}/revision').status_code, 403)
        with patch.object(service, 'current_sort_operation', return_value=None):
            closed = self.client.get(f'{DRIVER}/my-truck')
            self.assertIn(b'NO ACTIVE SORT', closed.data)

        user.auth_session_version += 1
        db.session.commit()
        self.assertEqual(self.client.get(f'{DRIVER}/my-truck').location, '/login')
        self.assertEqual(self.client.post('/login', data=credentials).location, f'{DRIVER}/my-truck')
        self.assertEqual(self.client.post('/logout').location, '/login')
        self.assertEqual(self.client.get(f'{DRIVER}/my-truck').location, '/login')

    def test_unified_password_recovery_uses_one_single_use_neoapps_link(self):
        self.client.post('/logout')
        user, _, _ = self.vendor()
        config = dict(SEND_EMAIL_IN_TESTS=True, BREVO_API_KEY='fixture-key', MAIL_FROM_NAME='Fixture',
                      MAIL_FROM_EMAIL='sender@example.test', APP_BASE_URL='https://neoapps.example')
        with patch.dict(self.fixture.app.config, config), patch('app.services.email_service.urlopen',
                return_value=BytesIO(b'{"messageId":"fixture-reset"}')) as brevo:
            response = self.client.post('/forgot-password', data={'email': user.email})
            self.assertEqual(response.status_code, 200)
            self.assertIn(b'If an account exists', response.data)
            brevo.assert_called_once()
            payload = json.loads(brevo.call_args.args[0].data)
        link = urlparse(payload['textContent'].splitlines()[2].removeprefix('Reset your password: '))
        self.assertEqual(link.netloc, 'neoapps.example')
        self.assertTrue(link.path.startswith('/reset-password/'))
        weak = {'password': 'weak', 'confirm_password': 'weak'}
        self.assertEqual(self.client.post(link.path, data=weak).status_code, 400)
        password = 'NewVendorPass123!'
        form = {'password': password, 'confirm_password': password}
        self.assertEqual(self.client.post(link.path, data=form).location, '/login')
        self.assertEqual(self.client.post(link.path, data=form).status_code, 400)
        self.assertEqual(self.client.post('/login', data={
            'email': user.email, 'password': 'VendorPassword123!',
        }).status_code, 401)
        self.assertEqual(self.client.post('/login', data={
            'email': user.email, 'password': password,
        }).location, f'{DRIVER}/my-truck')

    def test_every_non_allowlisted_endpoint_is_default_denied(self):
        from flask import request
        from app import PUBLIC_ASSET_ENDPOINTS,_is_public_asset_request
        from werkzeug.exceptions import Forbidden
        user,_account,_token=self.vendor()
        allowed={'healthz','auth.renew_csrf','auth.login','auth.logout',
            'auth.forgot_password','auth.reset_password','auth.change_password','auth.verify_email','auth.my_account',
            'scorpion_vendor.login','scorpion_vendor.logout',
            'scorpion_vendor.change_password','scorpion_vendor.forgot_password','scorpion_vendor.reset_password',
            'scorpion_vendor.account','scorpion_vendor.my_truck','scorpion_vendor.live',
            'scorpion_vendor.revision','scorpion_vendor.fuel_reading','scorpion_vendor.driver_fuel_reading',
            'scorpion_vendor.invitation','scorpion_vendor.supervisor','scorpion_vendor.supervisor_revision'}
        checked=0
        for rule in self.fixture.app.url_map.iter_rules():
            if rule.endpoint in allowed:continue
            methods=('GET','HEAD','POST','PATCH','DELETE') if rule.endpoint in PUBLIC_ASSET_ENDPOINTS else ('GET',)
            for method in methods:
                with self.fixture.app.test_request_context('/',method=method):
                    request.url_rule=rule
                    with patch.object(vendors,'current_user',user):
                        if _is_public_asset_request():
                            self.assertIsNone(vendors.vendor_request_boundary())
                        else:
                            with self.assertRaises(Forbidden,msg=(rule.endpoint,method)):vendors.vendor_request_boundary()
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
