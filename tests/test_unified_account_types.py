"""Unified registration, administrator decisions and restricted account boundaries."""
from unittest import TestCase
from unittest.mock import patch
from flask_login import login_user
from sqlalchemy import event
from app.extensions import db
from app.models import (User, FuelDriverIdentity, NeoScorpionVendorAccount, AccountSecurityEvent,
    AccountAccessRequest, PortalAppAccess, GatewayMembership, GatewayNodeRole)
from app.services import account_types as types, fuel_driver_accounts as drivers
from app.services.accounts_workspace import review_token
from tests import test_accounts_workspace as workspace
from tests.test_account_security_phase1 import PASSWORD


class UnifiedAccountTypesTest(TestCase):
    setUp=workspace.AccountsWorkspaceTest.setUp
    tearDown=workspace.AccountsWorkspaceTest.tearDown
    login=workspace.AccountsWorkspaceTest.login
    review=workspace.AccountsWorkspaceTest.review
    action=workspace.AccountsWorkspaceTest.action

    def decide(self,kind='vendor_supervisor',**extra):
        token=extra.pop('review_token',None) or self.review(tab='access')
        return self.client.post(f'/accounts/people/{self.target.id}/account-type?tab=access',data={
            'review_token':token,'account_type':kind,'outcome':'approved','employee_id':'1234567',
            'identity_confirmed':'yes','app_codes':['neostaffing'],'role_neostaffing':'operator',**extra})

    def test_all_signup_types_are_requests_not_self_granted_authority(self):
        with patch('app.auth.routes.email_service.send_email_verification',return_value={'sent':False}):
            for index,kind in enumerate(types.TYPES):
                with self.subTest(kind=kind):
                    email=f'{kind}@example.test'
                    form=dict(first_name='Synthetic',last_name=kind,email=email,employee_id=str(1234500+index),
                        supervisor_name='Manager',work_area='Operations',access_reason='Synthetic requested access',
                        password=PASSWORD,confirm_password=PASSWORD,account_type=kind,
                        app_codes=['neogateway','neostaffing'] if kind=='full' else ['neogateway'],is_supervisor='1')
                    self.assertEqual(self.client.post('/create-account',data=form).status_code,200)
                    user=User.query.filter_by(email=email).one()
                    self.assertEqual(types.requested_type(user),kind)
                    self.assertIsNone(user.scorpion_vendor)
                    self.assertFalse(PortalAppAccess.query.filter_by(user_id=user.id,status='approved').count())
                    receipt=AccountSecurityEvent.query.filter_by(subject_user_id=user.id,action='account_type_requested').one()
                    self.assertEqual(receipt.details['account_type'],kind)
                    self.assertFalse(drivers.identity_verified(user))

    def test_admin_changes_request_and_converts_all_types_without_replacing_identity(self):
        self.login(self.admin)
        target_id=self.target.id;password=self.target.password_hash
        db.session.add_all([FuelDriverIdentity(user_id=target_id,requested_driver=True,account_type='fuel_driver'),
            AccountSecurityEvent(actor_user_id=target_id,subject_user_id=target_id,action='account_type_requested',details={'account_type':'fuel_driver'})])
        db.session.commit()
        session=self.target.auth_session_version
        for kind in ('vendor_supervisor','fuel_driver','full','vendor_supervisor','full'):
            with self.subTest(kind=kind):
                response=self.decide(kind)
                self.assertEqual(response.status_code,302,response.data.decode()[:1000])
                db.session.refresh(self.target)
                self.assertEqual(types.current_type(self.target),kind)
                self.assertEqual((self.target.id,self.target.password_hash),(target_id,password))
                self.assertGreater(self.target.auth_session_version,session);session=self.target.auth_session_version
                if kind=='full':
                    self.assertTrue(PortalAppAccess.query.filter_by(user_id=target_id,app_code='neostaffing',status='approved',is_active=True).count())
                else:
                    self.assertFalse(PortalAppAccess.query.filter_by(user_id=target_id,status='approved',is_active=True).count())
                    self.assertFalse(GatewayNodeRole.query.join(GatewayMembership).filter(GatewayMembership.user_id==target_id,GatewayNodeRole.is_active.is_(True)).count())
        receipts=AccountSecurityEvent.query.filter_by(subject_user_id=target_id,action='account_type_decided').all()
        self.assertEqual(len(receipts),5)
        self.assertTrue(all(r.details['original_request']=='fuel_driver' for r in receipts))
        self.assertEqual(AccountSecurityEvent.query.filter_by(subject_user_id=target_id,action='account_type_requested').count(),1)

    def test_supervisor_without_company_or_sort_is_read_only_and_revokes_immediately(self):
        self.login(self.admin);self.assertEqual(self.decide().status_code,302)
        self.login(self.target)
        writes=[]
        def sql(_c,_cu,statement,*_):
            if statement.lstrip().upper().startswith(('INSERT','UPDATE','DELETE')):writes.append(statement)
        event.listen(db.engine,'before_cursor_execute',sql)
        try:
            with patch('app.services.neoscorpion.current_sort_operation',return_value=None):
                page=self.client.get('/neoscorpion/driver/supervisor')
                self.assertEqual(page.status_code,200)
                self.assertIn(b'NO ACTIVE SORT',page.data)
                self.assertNotIn(b'AWAITING DISPATCHER APPROVAL',page.data)
                self.assertEqual(self.client.get('/neoscorpion/driver/supervisor/revision').status_code,200)
            self.assertEqual(writes,[])
        finally:event.remove(db.engine,'before_cursor_execute',sql)
        self.assertEqual(self.client.post('/neoscorpion/driver/my-truck',data={}).status_code,302)
        self.assertEqual(self.client.post('/neoscorpion/driver/fuel-reading',data={}).status_code,302)
        for path in ('inventory',):
            self.assertEqual(self.client.post('/neoscorpion/driver/'+path,data={}).status_code,403,path)
        self.login(self.admin);self.assertEqual(self.decide('fuel_driver').status_code,302)
        self.login(self.target)
        self.assertEqual(self.client.get('/neoscorpion/driver/supervisor/revision').status_code,403)

    def test_authority_stale_review_and_unreleased_apps_rollback(self):
        self.login(self.admin);token=self.review(tab='access')
        self.assertEqual(self.decide('full',app_codes=['neobid']).status_code,400)
        self.assertEqual(self.decide('full',role_neostaffing='bogus').status_code,400)
        self.assertIsNone(db.session.get(FuelDriverIdentity,self.target.id))
        self.assertEqual(self.decide(review_token=token).status_code,302)
        self.assertEqual(self.decide('fuel_driver',review_token=token).status_code,409)
        self.assertEqual(types.current_type(self.target),'vendor_supervisor')
        self.login(self.target)
        self.assertEqual(self.client.post(f'/accounts/people/{self.backup.id}/account-type',data={}).status_code,403)
        self.login(self.admin)
        self.assertEqual(self.client.post(f'/accounts/people/{self.admin.id}/account-type',data={'account_type':'full','outcome':'approved','app_codes':'neostaffing','review_token':self.review(self.admin)}).status_code,400)

    def test_identity_duplicate_and_failed_audit_roll_back_every_grant(self):
        self.login(self.admin)
        self.backup.employee_id='1234567';db.session.commit()
        self.assertEqual(self.decide().status_code,400)
        self.assertEqual(self.target.employee_id,'SEC-workspace-target')
        self.backup.employee_id='7654321';db.session.commit()
        before=self.target.auth_session_version
        with patch.object(types,'record_security_change',side_effect=ValueError('Synthetic audit rejection')):
            self.assertEqual(self.decide().status_code,400)
        self.assertIsNone(db.session.get(FuelDriverIdentity,self.target.id))
        self.assertIsNone(db.session.get(NeoScorpionVendorAccount,self.target.id))
        self.assertEqual(self.target.auth_session_version,before)
        self.assertEqual(AccountSecurityEvent.query.filter_by(subject_user_id=self.target.id).count(),0)

    def test_deny_optional_reason_keeps_original_request_and_history(self):
        self.login(self.admin)
        db.session.add_all([FuelDriverIdentity(user_id=self.target.id,requested_driver=True,account_type='vendor_supervisor')]);db.session.commit()
        access=PortalAppAccess.query.filter_by(user_id=self.target.id,app_code='neogateway').one();access.status='pending';db.session.commit()
        self.assertEqual(self.decide(outcome='denied',reason='').status_code,302)
        self.assertIsNone(self.target.scorpion_vendor)
        self.assertEqual(types.requested_type(self.target),'vendor_supervisor')
        self.assertEqual(PortalAppAccess.query.filter_by(user_id=self.target.id,app_code='neogateway').one().status,'denied')
        receipt=AccountSecurityEvent.query.filter_by(subject_user_id=self.target.id,action='account_type_decided').one()
        self.assertEqual((receipt.details['outcome'],receipt.details['reason']),('denied',''))
        self.assertEqual(self.decide(outcome='denied').status_code,400)

    def test_people_filters_and_shared_form_remove_company_requirements(self):
        self.login(self.admin);self.assertEqual(self.decide().status_code,302)
        page=self.client.get('/accounts?type=vendor_supervisor')
        self.assertIn(self.target.email.encode(),page.data)
        self.assertNotIn(self.backup.email.encode(),page.data)
        page=self.client.get('/accounts?type=fuel_driver')
        self.assertNotIn(self.target.email.encode(),page.data)
        page=self.client.get(f'/accounts/people/{self.target.id}?tab=access')
        self.assertIn(b'data-account-approval',page.data)
        self.assertNotIn(b'name="company_id"',page.data)
        self.assertNotIn(b'name="current_password"',page.data)
        self.assertEqual(self.client.post(f'/accounts/people/{self.target.id}/vendor-company',data={'company_id':'1'}).status_code,410)
        self.assertEqual(self.client.post('/accounts/vendor-companies',data={}).status_code,410)
        self.assertEqual(self.client.get('/accounts/vendor-companies').status_code,302)


class ScopedUnifiedAccountTypesTest(TestCase):
    from tests.test_scoped_authorization import ScopedAuthorizationTest as _fixture
    setUp=_fixture.setUp
    tearDown=_fixture.tearDown
    as_user=_fixture.as_user
    cutover=_fixture.cutover

    def test_live_scoped_engine_conversion_preserves_other_accounts_and_rejects_node_admin(self):
        self.target.employee_id='7654321';db.session.commit();self.cutover()
        inherited=[(r.id,r.role,r.is_active) for r in GatewayNodeRole.query.join(GatewayMembership).filter(GatewayMembership.user_id==self.inherited.id).all()]
        for kind in ('vendor_supervisor','fuel_driver','full'):
            self.as_user(self.root)
            types.decide(self.target.id,review_token(self.target),kind,'approved',self.target.employee_id,'yes',None,['neostaffing'],{'neostaffing':'operator'},'')
            db.session.commit()
            self.assertEqual(types.current_type(self.target),kind)
        self.assertEqual(inherited,[(r.id,r.role,r.is_active) for r in GatewayNodeRole.query.join(GatewayMembership).filter(GatewayMembership.user_id==self.inherited.id).all()])
        self.as_user(self.master)
        with self.assertRaises(PermissionError):
            types.decide(self.target.id,review_token(self.target),'vendor_supervisor','approved',self.target.employee_id,'yes',None,[],{},'')
