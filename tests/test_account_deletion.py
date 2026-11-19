"""Synthetic deletion, retained evidence and shared People-search contracts."""
from unittest import TestCase
from unittest.mock import patch
from datetime import date

from flask_login import login_user
from sqlalchemy import event

from app.extensions import db
from app.models import (AccountAccessDecision, AccountAccessRequest, AccountSecurityEvent,
    GatewayMembership, GatewayNodeRole, PortalAppAccess, StaffingPerson, User, UserToken,
    SortDateOperation,NeoScorpionSortFueler,AccountScopedRole)
from app.services import account_deletion as service
from app.services.accounts_workspace import new_attempt, verify_review
from app.services.user_tokens import EMAIL_VERIFICATION, PASSWORD_RESET, create_user_token, get_valid_token_record
from tests import test_accounts_workspace as fixture
from tests.html_contracts import document


class AccountDeletionTest(TestCase):
    setUp=fixture.AccountsWorkspaceTest.setUp
    tearDown=fixture.AccountsWorkspaceTest.tearDown
    login=fixture.AccountsWorkspaceTest.login
    review=fixture.AccountsWorkspaceTest.review

    def remove(self,target=None,token=None,**values):
        target=target or self.target
        token=token or self.review(target,'security')
        return self.client.post(f'/accounts/people/{target.id}/delete?tab=security',data=dict(
            review_token=token,confirm_delete='yes',confirmed_user_id=str(target.id),**values))

    def test_deletion_revokes_login_sessions_grants_tokens_and_keeps_identity_history(self):
        self.login(self.admin)
        before_event=AccountSecurityEvent(actor_user_id=self.admin.id,subject_user_id=self.target.id,
            action='historical_test_evidence',details={'retain':'unchanged'})
        person=StaffingPerson(employee_id=self.target.employee_id,first_name='Historical',last_name='Person',classification='seasonal')
        db.session.add_all([before_event,person]);db.session.commit()
        operation=SortDateOperation(gateway_id=GatewayMembership.query.filter_by(user_id=self.target.id).first().gateway_id,
            gateway_code='RFD',sort_date=date(2020,1,1),sort_name='night',generated_by_user_id=self.target.id)
        db.session.add(operation);db.session.commit()
        with self.app.test_request_context():
            login_user(self.target)
            attempt=new_attempt(self.target,'neostaffing','Synthetic request to preserve')
            raw_reset,_=create_user_token(self.target,PASSWORD_RESET)
            raw_verify,_=create_user_token(self.target,EMAIL_VERIFICATION)
            db.session.commit()
        self.login(self.admin)
        prior_version=self.target.auth_session_version
        identity=(self.target.id,self.target.email,self.target.employee_id,self.target.display_name)
        self.assertEqual(self.remove().status_code,302)
        db.session.expire_all()
        target=db.session.get(User,identity[0])
        self.assertEqual((target.id,target.email,target.employee_id,target.display_name),identity)
        self.assertFalse(target.is_active)
        self.assertEqual(target.auth_session_version,prior_version+1)
        self.assertFalse(target.check_password(fixture.PASSWORD))
        self.assertEqual(db.session.get(StaffingPerson,person.id).first_name,'Historical')
        self.assertEqual(db.session.get(SortDateOperation,operation.id).generated_by_user_id,target.id)
        self.assertEqual(db.session.get(AccountSecurityEvent,before_event.id).details,{'retain':'unchanged'})
        self.assertEqual(db.session.get(AccountAccessRequest,attempt.id).state,'withdrawn')
        self.assertEqual(AccountAccessDecision.query.filter_by(request_id=attempt.id,outcome='withdrawn').count(),1)
        for model in (PortalAppAccess,GatewayMembership):
            self.assertFalse(any(row.is_active for row in model.query.filter_by(user_id=target.id)))
        self.assertIsNone(get_valid_token_record(raw_reset,PASSWORD_RESET))
        self.assertIsNone(get_valid_token_record(raw_verify,EMAIL_VERIFICATION))
        deletion=service.receipt(target.id)
        self.assertEqual(deletion.actor_user_id,self.admin.id)
        self.assertTrue(deletion.details['identity_retained_for_history'])
        people=document(self.client.get('/accounts?q='+target.email))
        self.assertEqual(len(people.findall('a',cls='aa-person')),0)
        historical=self.client.get(f'/accounts/people/{target.id}?tab=history')
        self.assertEqual(historical.status_code,200)
        self.assertIn(b'Historical Test Evidence',historical.data)

    def test_self_platform_grandmasters_and_last_administrator_are_protected(self):
        self.login(self.admin)
        for target in (self.admin,self.backup):
            with self.subTest(target=target.id):
                self.assertEqual(self.remove(target,token='forged-protected-account-request').status_code,400)
                self.assertTrue(db.session.get(User,target.id).is_active)
                self.assertIsNone(service.receipt(target.id))

    def test_non_platform_administrator_cannot_delete_even_by_forged_post(self):
        self.login(self.target)
        self.assertEqual(self.client.post(f'/accounts/people/{self.backup.id}/delete',data={
            'confirm_delete':'yes','confirmed_user_id':self.backup.id}).status_code,403)
        self.assertEqual(AccountSecurityEvent.query.filter_by(action=service.ACTION).count(),0)

    def test_confirmation_csrf_and_stale_review_are_required(self):
        self.login(self.admin)
        token=self.review(self.target,'security')
        for fields in ({'review_token':token},{'review_token':token,'confirm_delete':'yes','confirmed_user_id':'999999'}):
            response=self.client.post(f'/accounts/people/{self.target.id}/delete?tab=security',data=fields)
            self.assertEqual(response.status_code,400)
            self.assertIn(b'Confirm DELETE ACCOUNT',response.data)
        self.target.work_area='Changed since review';db.session.commit()
        self.assertEqual(self.remove(token=token).status_code,409)
        self.app.config['CSRF_PROTECT_TESTING']=True
        self.assertEqual(self.client.post(f'/accounts/people/{self.target.id}/delete',data={}).status_code,400)
        self.assertTrue(db.session.get(User,self.target.id).is_active)

    def test_repeat_and_legacy_reactivation_do_not_resurrect_deleted_account(self):
        self.login(self.admin)
        token=self.review(self.target,'security')
        self.assertEqual(self.remove(token=token).status_code,302)
        version=self.target.auth_session_version
        self.assertEqual(self.remove(token=token).status_code,409)
        self.assertEqual(AccountSecurityEvent.query.filter_by(subject_user_id=self.target.id,action=service.ACTION).count(),1)
        self.assertEqual(self.target.auth_session_version,version)
        with self.assertRaisesRegex(ValueError,'deleted'):
            from app.auth.routes import _lock_staff_account
            _lock_staff_account(self.target)
        with self.assertRaisesRegex(ValueError,'deleted'):
            verify_review(self.target,token)
        db.session.rollback()
        self.assertFalse(db.session.get(User,self.target.id).is_active)

    def test_failed_write_rolls_back_deletion_and_evidence(self):
        self.login(self.admin)
        token=self.review(self.target,'security');version=self.target.auth_session_version
        with patch('app.services.user_tokens.revoke_unused_password_reset_tokens',side_effect=ValueError('Synthetic rollback')):
            self.assertEqual(self.remove(token=token).status_code,400)
        self.assertTrue(db.session.get(User,self.target.id).is_active)
        self.assertEqual(self.target.auth_session_version,version)
        self.assertIsNone(service.receipt(self.target.id))

    def test_active_nightly_responsibility_blocks_without_erasing_work(self):
        self.login(self.admin)
        operation=SortDateOperation(gateway_id=GatewayMembership.query.filter_by(user_id=self.target.id).first().gateway_id,
            gateway_code='RFD',sort_date=date(2026,11,18),sort_name='night',generated_by_user_id=self.admin.id)
        db.session.add(operation);db.session.flush()
        fueler=NeoScorpionSortFueler(sort_date_operation_id=operation.id,user_id=self.target.id)
        db.session.add(fueler);db.session.commit()
        with patch.object(service,'_operations',return_value={operation.id:operation}):
            response=self.remove()
        self.assertEqual(response.status_code,400)
        self.assertIn(b'current nightly assets',response.data)
        self.assertIsNotNone(db.session.get(NeoScorpionSortFueler,fueler.id))
        self.assertTrue(self.target.is_active)
        self.assertIsNone(service.receipt(self.target.id))

    def test_deletion_audit_is_immutable(self):
        self.login(self.admin)
        self.assertEqual(self.remove().status_code,302)
        audit=service.receipt(self.target.id)
        audit.details={'changed':'forged'}
        with self.assertRaisesRegex(ValueError,'security history is immutable'):db.session.commit()
        db.session.rollback()
        self.assertTrue(service.receipt(self.target.id).details['identity_retained_for_history'])

    def test_deleted_account_session_is_revoked(self):
        from flask import g
        other=self.app.test_client()
        self.assertEqual(other.post('/login',data={'email':self.target.email,'password':fixture.PASSWORD}).status_code,302)
        self.login(self.admin)
        self.assertEqual(self.remove().status_code,302)
        g.pop('_login_user',None)
        response=other.get('/my-account')
        self.assertEqual(response.status_code,302)
        self.assertIn('/login',response.location)

    def test_node_grandmaster_cannot_delete_accounts(self):
        for grant in GatewayNodeRole.query.join(GatewayMembership).filter(GatewayMembership.user_id==self.target.id):
            grant.role='grandmaster'
        db.session.commit()
        self.login(self.target)
        self.assertEqual(self.client.post(f'/accounts/people/{self.backup.id}/delete',data={
            'confirm_delete':'yes','confirmed_user_id':self.backup.id}).status_code,403)
        self.assertTrue(self.backup.is_active)

    def test_scoped_grants_revoke_without_losing_the_original_audit_receipt(self):
        self.login(self.admin)
        original=AccountSecurityEvent(actor_user_id=self.admin.id,subject_user_id=self.target.id,
            action='original_scope_grant',details={'role':'operator'})
        db.session.add(original);db.session.flush()
        grant=AccountScopedRole(user_id=self.target.id,kind='application',scope_key='app:neostaffing',
            app_code='neostaffing',role='operator',is_active=True,security_event_id=original.id)
        db.session.add(grant);db.session.commit()
        self.assertEqual(self.remove().status_code,302)
        self.assertFalse(grant.is_active)
        self.assertEqual(grant.version,2)
        deletion=service.receipt(self.target.id)
        self.assertEqual(grant.security_event_id,deletion.id)
        self.assertEqual(deletion.details['before']['scope_receipts'][0]['security_event_id'],original.id)
        self.assertEqual(db.session.get(AccountSecurityEvent,original.id).details,{'role':'operator'})

    def test_gets_are_read_only_and_people_search_exists_once_on_every_workspace_page(self):
        self.login(self.admin)
        writes=[]
        def executed(_c,_cursor,statement,*_args):
            if statement.lstrip().upper().startswith(('INSERT','UPDATE','DELETE')):writes.append(statement)
        event.listen(db.engine,'before_cursor_execute',executed)
        try:
            paths=['/accounts','/accounts/requests','/accounts/security']+[
                f'/accounts/people/{self.target.id}?tab={tab}' for tab in ('identity','access','security','history')]
            for path in paths:
                with self.subTest(path=path):
                    page=self.client.get(path)
                    self.assertEqual(page.status_code,200)
                    form=document(page).one('form',role='search')
                    self.assertEqual(form.attrs['action'],'/accounts')
                    self.assertEqual(len(form.findall('input',name='q')),1)
        finally:event.remove(db.engine,'before_cursor_execute',executed)
        self.assertEqual(writes,[])

    def test_search_and_filters_preserve_name_email_and_employee_lookup(self):
        self.login(self.admin)
        for query in (self.target.first_name,self.target.email,self.target.employee_id):
            page=self.client.get('/accounts',query_string=dict(q=query,status='active',type='full'))
            rows=document(page).findall('a',cls='aa-person')
            self.assertTrue(any(row.attrs['href']==f'/accounts/people/{self.target.id}' for row in rows))
            form=document(page).one('form',role='search')
            self.assertEqual(form.one('input',name='status').attrs['value'],'active')
            self.assertEqual(form.one('input',name='type').attrs['value'],'full')
