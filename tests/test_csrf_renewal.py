"""Expiry renewal remains bound to the authenticated session and original page."""
from types import SimpleNamespace
from unittest.mock import patch
import time
import unittest
from flask import jsonify, g
from flask_login import current_user
from app.extensions import db
from app.models import PortalAppAccess
from tests import test_csrf_protection as fixtures


class CsrfRenewalTest(unittest.TestCase):
    _user=fixtures.CsrfProtectionTest._user
    _login=fixtures.CsrfProtectionTest._login
    _csrf_token=fixtures.CsrfProtectionTest._csrf_token
    tearDown=fixtures.CsrfProtectionTest.tearDown

    def setUp(self):
        fixtures.CsrfProtectionTest.setUp(self)
        self.executions=0
        def mutate():
            if not current_user.is_authenticated:return jsonify(error='Sign in'),401
            self.executions+=1
            return jsonify(ok=True)
        self.app.add_url_rule('/_csrf-mutate',view_func=mutate,methods=['POST','PUT','PATCH','DELETE'])
        self.now=int(time.time())
        self.clock=patch('app.services.csrf.time',SimpleNamespace(time=lambda:self.now))
        self.clock.start();self.addCleanup(self.clock.stop)
        self.user=self._user('renewal-user');db.session.commit();self._login(self.user)
        self.token=self._csrf_token(self.client.get('/access-pending'))

    def renew(self,token=None,**headers):
        return self.client.get('/csrf-token',headers={'X-CSRF-Renewal':'1','X-CSRF-Token':token or self.token,**headers})

    def test_expired_page_renews_without_changing_session_nonce(self):
        from app.services.csrf import CSRF_SESSION_NONCE_KEY
        with self.client.session_transaction() as session:nonce=session[CSRF_SESSION_NONCE_KEY]
        self.now+=3*3600
        rejected=self.client.post('/_csrf-mutate',json={},headers={'X-CSRF-Token':self.token})
        self.assertEqual(rejected.status_code,400)
        self.assertEqual(rejected.headers['X-NeoApps-CSRF-Rejected'],'1')
        self.assertEqual(rejected.json['code'],'csrf_rejected');self.assertFalse(rejected.json['request_executed'])
        self.assertEqual(self.executions,0)
        renewed=self.renew();self.assertEqual(renewed.status_code,200)
        self.assertEqual(renewed.headers['Cache-Control'],'no-store')
        self.assertEqual(renewed.headers['Cross-Origin-Resource-Policy'],'same-origin')
        self.assertEqual(renewed.json['expires_in'],7200)
        with self.client.session_transaction() as session:self.assertEqual(session[CSRF_SESSION_NONCE_KEY],nonce)
        saved=self.client.post('/_csrf-mutate',json={},headers={'X-CSRF-Token':renewed.json['csrf_token']})
        self.assertEqual(saved.status_code,200);self.assertEqual(self.executions,1)

    def test_independent_tabs_and_overnight_renewal(self):
        self.now+=14*3600
        first=self.renew().json['csrf_token'];self.now+=60
        second=self.renew().json['csrf_token']
        for token in (first,second):
            self.assertEqual(self.client.post('/_csrf-mutate',json={},headers={'X-CSRF-Token':token}).status_code,200)
        self.assertEqual(self.executions,2)

    def test_renewal_requires_original_signed_session_proof(self):
        self.assertEqual(self.client.get('/csrf-token',headers={'X-CSRF-Renewal':'1'}).status_code,403)
        self.assertEqual(self.renew('invalid').status_code,403)
        self.assertEqual(self.renew(self.token[:-1]+'x').status_code,403)
        self.assertEqual(self.client.get('/csrf-token',headers={'X-CSRF-Token':self.token}).status_code,403)

    def test_cross_origin_or_cross_site_cannot_renew(self):
        for headers in ({'Origin':'https://evil.example'},{'Sec-Fetch-Site':'cross-site'},
                        {'Sec-Fetch-Site':'same-site'},{'Sec-Fetch-Site':'none'}):
            self.assertEqual(self.renew(**headers).status_code,403)

    def test_anonymous_session_cannot_renew(self):
        client=self.app.test_client();g.pop('_login_user',None)
        self.assertEqual(client.get('/csrf-token',headers={'X-CSRF-Renewal':'1','X-CSRF-Token':self.token}).status_code,401)

    def test_logout_and_relogin_do_not_reauthorize_old_page(self):
        self.client.post('/logout',data={'csrf_token':self.token})
        self.assertEqual(self.renew().status_code,401)
        self._login(self.user)
        self.assertEqual(self.renew().status_code,403)

    def test_password_change_session_invalidation_prevents_renewal(self):
        self.user.auth_session_version+=1;db.session.commit()
        result=self.renew()
        self.assertIn(result.status_code,(302,401))
        if result.status_code==302:self.assertIn('/login',result.location)
        self.assertEqual(self.renew().status_code,401)

    def test_renewal_does_not_grant_revoked_application_access(self):
        from app.services.permission_rules import user_can
        db.session.add(PortalAppAccess(user_id=self.user.id,app_code='neostaffing',status='approved',role='master',is_active=False))
        db.session.commit();self.now+=3*3600
        self.assertEqual(self.renew().status_code,200)
        self.assertFalse(user_can('neostaffing.people.edit',self.user))
        self.assertEqual(self.client.get('/neostaffing/people').status_code,302)

    def test_fresh_token_still_cannot_cross_sessions(self):
        other=self.app.test_client()
        token=self._csrf_token(self.client.get('/access-pending'));g.pop('_login_user',None)
        self.assertEqual(other.post('/_csrf-mutate',json={},headers={'X-CSRF-Token':token}).status_code,400)

    def test_future_proof_is_rejected_and_renewal_is_read_only(self):
        self.now+=3600;future=self._csrf_token(self.client.get('/access-pending'));self.now-=3600
        self.assertEqual(self.renew(future).status_code,403)
        self.assertEqual(self.client.post('/csrf-token',json={},headers={'X-CSRF-Token':self.token}).status_code,405)

    def test_vendor_can_renew_without_gaining_operational_authority(self):
        from app.models import NeoScorpionVendorAccount
        account=NeoScorpionVendorAccount(user_id=self.user.id,gateway_id=self.gateway.id,
            invited_by_user_id=self.user.id,enabled=True)
        db.session.add(account);db.session.commit();self.now+=3*3600
        self.assertEqual(self.renew().status_code,200)
        self.assertEqual(self.client.get('/motherbrain').status_code,403)
        self.assertEqual(self.client.get('/neoscorpion/driver/my-truck').status_code,403)

    def test_renewal_performs_no_database_writes(self):
        from sqlalchemy import event
        statements=[]
        def capture(_connection,_cursor,statement,*_args):statements.append(statement.lstrip().upper())
        self.now+=3*3600
        event.listen(db.engine,'before_cursor_execute',capture)
        try:self.assertEqual(self.renew().status_code,200)
        finally:event.remove(db.engine,'before_cursor_execute',capture)
        self.assertFalse([sql for sql in statements if sql.startswith(('INSERT','UPDATE','DELETE'))])

    def test_required_password_change_still_blocks_operational_mutations(self):
        self.user.password_reset_required=True;db.session.commit();self.now+=3*3600
        renewed=self.renew();self.assertEqual(renewed.status_code,200)
        response=self.client.post('/_csrf-mutate',json={},headers={'X-CSRF-Token':renewed.json['csrf_token']})
        self.assertEqual(response.status_code,302);self.assertIn('/change-password',response.location)
        self.assertEqual(self.executions,0)
