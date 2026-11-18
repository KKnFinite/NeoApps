"""Phase 4 identity, restricted approval and company authorization contracts."""
from datetime import datetime
from unittest import TestCase
from unittest.mock import patch
from flask_login import login_user
from sqlalchemy import event

from app.extensions import db
from app.models import (User, FuelDriverIdentity, FuelVendorCompany, FuelVendorMembership,
    NeoScorpionVendorAccount, AccountScopedRole, AccountSecurityEvent, PortalAppAccess, StaffingPerson)
from app.services import fuel_driver_accounts as service
from app.services.accounts_workspace import review_token
from app.services.account_foundation import AccountConflict
from tests import test_accounts_workspace as workspace
from tests.test_account_security_phase1 import PASSWORD, fixture_user


class FuelDriverAccountsTest(TestCase):
    setUp = workspace.AccountsWorkspaceTest.setUp
    tearDown = workspace.AccountsWorkspaceTest.tearDown
    login = workspace.AccountsWorkspaceTest.login
    review = workspace.AccountsWorkspaceTest.review
    action = workspace.AccountsWorkspaceTest.action

    def restricted(self, user=None):
        user = user or self.target
        from app.services.neoscorpion_account_types import convert_account
        self.login(self.admin)
        from app.services.access_control import get_current_gateway
        with self.app.test_request_context():
            login_user(self.admin)
            convert_account(get_current_gateway(), self.admin, user, 'vendor')
            db.session.commit()
        return db.session.get(NeoScorpionVendorAccount, user.id)

    def verify(self, value='000123', user=None, token=None):
        return self.action('fuel-driver-identity', dict(ups_id=value,
            verification_reference='Synthetic identity verification'), user=user, review=token)

    def company(self, name='Synthetic vendor'):
        from app.services.access_control import get_current_gateway
        with self.app.test_request_context():
            login_user(self.admin)
            row = service.create_company(get_current_gateway().id, name, 'Synthetic company attestation')
            db.session.commit()
            return row

    def membership(self, company, supervisor=True, user=None, token=None, **extra):
        user = user or self.target
        return self.action('vendor-company', dict(company_id=company.id if company else '',
            is_supervisor='1' if supervisor else '0', verification_reference='Synthetic company affiliation',
            current_password=PASSWORD, confirmed_user_id=user.id, **extra), user=user, review=token)

    def test_signup_requires_id_and_never_self_grants_driver_or_supervisor(self):
        form = dict(first_name='Synthetic',last_name='Applicant',email='driver-new@example.test',employee_id='',
            supervisor_name='Manager',work_area='Vendor',access_reason='Fuel truck duties',password=PASSWORD,
            confirm_password=PASSWORD,account_type='fuel_driver',app_codes=['neogateway'], is_supervisor='1')
        with patch('app.auth.routes.email_service.send_email_verification', return_value={'sent':False}):
            response = self.client.post('/create-account',data=form)
            self.assertEqual(response.status_code,400)
            self.assertIsNone(User.query.filter_by(email=form['email']).first())
            response = self.client.post('/create-account',data={**form,'employee_id':'001234'})
        self.assertEqual(response.status_code,200)
        self.assertIn(b'Verify Your Email',response.data)
        user = User.query.filter_by(email=form['email']).one()
        self.assertEqual(user.employee_id,'001234')
        self.assertTrue(service.requested_driver(user))
        self.assertFalse(service.identity_verified(user))
        self.assertIsNone(db.session.get(NeoScorpionVendorAccount,user.id))
        self.assertEqual(FuelVendorMembership.query.count(),0)
        self.assertFalse(PortalAppAccess.query.filter_by(user_id=user.id,status='approved').count())

    def test_restricted_approval_requires_verified_ups_and_preserves_id(self):
        self.login(self.admin)
        db.session.add(FuelDriverIdentity(user_id=self.target.id,requested_driver=True)); db.session.commit()
        original = self.target.id
        url=f'/admin/users/{original}/account-type'
        response=self.client.post(url,data={'account_type':'vendor','review_token':self.review()})
        self.assertEqual(response.status_code,302)
        self.assertIsNone(db.session.get(NeoScorpionVendorAccount,original))
        self.assertEqual(self.verify().status_code,302)
        response=self.client.post(url,data={'account_type':'vendor','review_token':self.review(tab='access')})
        self.assertEqual(response.status_code,302)
        self.assertIsNotNone(db.session.get(NeoScorpionVendorAccount,original))
        self.assertFalse(service.requested_driver(self.target))
        self.assertEqual(self.target.id,original)
        self.assertFalse(PortalAppAccess.query.filter_by(user_id=original,status='approved',is_active=True).count())
        self.assertEqual(AccountScopedRole.query.count(),0)

    def test_ups_correction_no_conversion_history_and_stale_conflict(self):
        self.restricted()
        prior_id=self.target.id
        token=self.review()
        old_version=self.target.auth_session_version
        self.assertEqual(self.verify(token=token).status_code,302)
        self.assertEqual(self.target.employee_id,'000123')
        self.assertGreater(self.target.auth_session_version,old_version)
        self.assertTrue(service.identity_verified(self.target))
        self.assertIsNotNone(db.session.get(NeoScorpionVendorAccount,prior_id))
        self.assertEqual(self.verify('000124',token=token).status_code,409)
        self.assertEqual(self.target.employee_id,'000123')
        self.assertEqual(AccountSecurityEvent.query.filter_by(subject_user_id=prior_id,action='fuel_driver_identity_verified').count(),1)

    def test_duplicate_case_insensitive_ups_and_permission_boundaries(self):
        self.login(self.admin)
        self.assertEqual(self.verify(self.backup.employee_id.lower()).status_code,400)
        self.assertEqual(self.target.employee_id,'SEC-workspace-target')
        self.login(self.target)
        self.assertEqual(self.client.post(f'/accounts/people/{self.target.id}/fuel-driver-identity',data={'ups_id':'BAD'}).status_code,403)
        self.assertEqual(self.client.get('/accounts/vendor-companies').status_code,403)
        self.assertEqual(self.client.post(f'/accounts/people/{self.target.id}/vendor-company',data={'is_supervisor':'1'}).status_code,403)

    def test_staffing_identity_link_cannot_be_silently_reassigned(self):
        person=StaffingPerson(employee_id=self.target.employee_id,first_name='Historical',last_name='Person',classification='seasonal')
        db.session.add(person);db.session.commit()
        self.login(self.admin)
        old=self.target.employee_id
        self.assertEqual(self.verify('DIFFERENT-PERSON').status_code,400)
        self.assertEqual(self.target.employee_id,old)
        self.assertEqual(StaffingPerson.query.one().employee_id,old)

    def test_invalid_company_input_never_means_membership_revocation(self):
        self.restricted();self.verify();company=self.company()
        self.assertEqual(self.membership(company,False).status_code,302)
        response=self.action('vendor-company',dict(company_id='malformed',is_supervisor='0',verification_reference='Synthetic malformed company',current_password=PASSWORD,confirmed_user_id=self.target.id))
        self.assertEqual(response.status_code,400)
        self.assertEqual(service.current_membership(self.target.id).company_id,company.id)

    def test_conversion_cannot_resurrect_old_supervisor_company_authority(self):
        self.restricted();self.verify();company=self.company()
        self.assertEqual(self.membership(company).status_code,302)
        member_id=service.current_membership(self.target.id).id
        url=f'/admin/users/{self.target.id}/account-type'
        for kind in ('full','vendor'):
            response=self.client.post(url,data={'account_type':kind,'review_token':self.review(tab='security')})
            self.assertEqual(response.status_code,302)
        self.assertIsNone(service.current_membership(self.target.id))
        self.assertIsNotNone(db.session.get(FuelVendorMembership,member_id).ended_at)
        self.assertIsNotNone(db.session.get(NeoScorpionVendorAccount,self.target.id))
        with self.assertRaises(PermissionError):service.require_supervisor(db.session.get(NeoScorpionVendorAccount,self.target.id))
        self.assertEqual(AccountSecurityEvent.query.filter_by(subject_user_id=self.target.id,action='fuel_vendor_membership_closed_by_conversion').count(),1)

    def test_company_supervisor_explicit_audit_revocation_and_read_only_gets(self):
        account=self.restricted()
        self.assertEqual(self.verify().status_code,302)
        company=self.company()
        before=self.target.auth_session_version
        self.assertEqual(self.membership(company).status_code,302)
        self.assertGreater(self.target.auth_session_version,before)
        self.assertTrue(service.require_supervisor(account))
        self.assertEqual(self.membership(company).status_code,409)
        statements=[]
        def record(_c,_cu,s,*_):
            if s.lstrip().upper().startswith(('INSERT','UPDATE','DELETE')): statements.append(s)
        event.listen(db.engine,'before_cursor_execute',record)
        try:
            with patch('app.services.neoscorpion.current_sort_operation',return_value=None):
                payload=service.supervisor_context(account)
            self.assertEqual(payload['supervisor']['rows'],[])
        finally: event.remove(db.engine,'before_cursor_execute',record)
        self.assertEqual(statements,[])
        self.assertEqual(self.membership(None,False).status_code,302)
        with self.assertRaises(PermissionError): service.require_supervisor(account)
        self.assertEqual(FuelVendorMembership.query.count(),1)
        self.assertIsNotNone(FuelVendorMembership.query.one().ended_at)

    def test_wrong_password_and_company_staleness_roll_back(self):
        self.restricted();self.verify();company=self.company()
        data=dict(company_id=company.id,is_supervisor='1',verification_reference='Synthetic attestation',
            current_password='wrong password',confirmed_user_id=self.target.id)
        self.assertEqual(self.action('vendor-company',data).status_code,400)
        self.assertEqual(FuelVendorMembership.query.count(),0)
        token=self.review(tab='access')
        company.is_active=False;company.version+=1;db.session.commit()
        self.assertEqual(self.membership(company,token=token).status_code,400)
        self.assertEqual(FuelVendorMembership.query.count(),0)

    def test_shared_signup_invitation_creates_no_account_or_access(self):
        self.login(self.admin)
        count=User.query.count()
        with patch('app.services.email_service.send_neoapps_signup_invitation',return_value={'sent':True}) as mail:
            response=self.client.post('/accounts/signup-invitation',data={'email':'invite@example.test'})
        self.assertEqual(response.status_code,302)
        mail.assert_called_once_with('invite@example.test')
        self.assertEqual(User.query.count(),count)
        self.assertEqual(AccountSecurityEvent.query.filter_by(action='neoapps_signup_invitation_sent').count(),1)

    def test_schema_additive_idempotent_preserves_security_and_staged_roles_inactive(self):
        before=(self.admin.password_hash,self.admin.auth_session_version,self.target.employee_id)
        db.create_all();db.create_all()
        self.assertEqual(before,(self.admin.password_hash,self.admin.auth_session_version,self.target.employee_id))
        self.assertEqual(AccountScopedRole.query.count(),0)
