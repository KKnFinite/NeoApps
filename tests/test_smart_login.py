"""Canonical app access drives landing selection; Portal never advertises Staffing grants."""
import unittest
from flask import g
from app.extensions import db
from app.models import PortalAppAccess, GatewayMembership
from tests import test_auth_account_flows as fixtures


class SmartLoginTest(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.AuthAccountFlowsTest()
        self.f.setUp()
        self.addCleanup(self.f.tearDown)

    def user(self, name, gateway=False, staffing=None):
        f = self.f
        if gateway:
            user, _ = f._approved_user(name, name+'@example.com')
        else:
            user = f._user(name, verified=True)
        if staffing:
            db.session.add(PortalAppAccess(user_id=user.id, app_code='neostaffing',
                role='watcher', status=staffing[0], is_active=staffing[1]))
        db.session.commit()
        g.pop('_login_user', None)
        return user

    def test_one_or_multiple_effective_apps_and_authenticated_login_get(self):
        for name, gateway, staffing, destination in (
            ('gateway', True, None, '/rfd'),
            ('staffing', False, ('approved',True), '/neostaffing'),
            ('both', True, ('approved',True), '/portal'),
            ('none', False, None, '/access-pending'),
        ):
            with self.subTest(name=name):
                user = self.user(name,gateway,staffing)
                client = self.f.app.test_client()
                response = client.post('/login?next=https://evil.invalid', data={'email':user.email,'password':'TestPassword123!'})
                self.assertEqual(response.location,destination)
                self.assertEqual(client.get('/login').location,destination)
                self.assertEqual(client.get(destination).status_code,200)

    def test_staffing_pending_denied_inactive_missing_do_not_count_or_render(self):
        for name, grant in (('pending',('pending',True)),('denied',('denied',True)),
                            ('inactive',('approved',False)),('missing',None)):
            with self.subTest(name=name):
                user = self.user(name, True, grant)
                client = self.f.app.test_client()
                self.assertEqual(client.post('/login',data={'email':user.email,'password':'TestPassword123!'}).location,'/rfd')
                response = client.get('/portal')
                self.assertEqual(response.status_code,200)
                self.assertNotIn(b'data-portal-app="neostaffing"',response.data)
                self.assertNotIn(b'name="app_code" value="neostaffing"',response.data)
                self.assertIn(b'data-portal-app="neogateway"',response.data)
                self.assertEqual(client.get('/neostaffing').location,'/portal')

    def test_revoked_gateway_authority_does_not_create_redirect_loop(self):
        user = self.user('revoked_gateway',True)
        db.session.add(PortalAppAccess(user_id=user.id,app_code='neogateway',role='watcher',status='approved',is_active=True))
        GatewayMembership.query.filter_by(user_id=user.id).one().status='denied'
        db.session.commit()
        response = self.f.client.post('/login',data={'email':user.email,'password':'TestPassword123!'})
        self.assertEqual(response.location,'/access-pending')
        self.assertEqual(self.f.client.get('/access-pending').status_code,200)
        self.assertNotIn(b'data-portal-app="neostaffing"',self.f.client.get('/portal').data)

    def test_password_change_keeps_the_same_app_routing(self):
        user = self.user('forced',False,('approved',True))
        user.password_reset_required=True
        db.session.commit()
        self.assertEqual(self.f._login(user.username).location,'/change-password')
        response=self.f.client.post('/change-password',data={'password':'violet river lantern','confirm_password':'violet river lantern'})
        self.assertEqual(response.location,'/neostaffing')
