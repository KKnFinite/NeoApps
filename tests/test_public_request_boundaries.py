"""Exact public GET/HEAD resources never resolve authentication or vendor data."""
import unittest
from unittest.mock import patch
from flask import g
from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from app import PUBLIC_ASSET_ENDPOINTS, _is_public_asset_request
from app.extensions import db, login_manager
from app.models import User,Gateway,NeoScorpionVendorAccount
from app.services.password_policy import set_user_password
from tests import test_local_launch_navigation as fixture

PUBLIC_PATHS=('/static/css/base.css','/manifest.webmanifest','/manifest/neosektor.webmanifest',
    '/service-worker.js','/apple-touch-icon.png','/apple-touch-icon-precomposed.png',
    '/favicon-32x32.png','/favicon-16x16.png','/favicon.ico')


class PublicRequestBoundaryTest(unittest.TestCase):
    setUp=fixture.LocalLaunchNavigationTest.setUp
    tearDown=fixture.LocalLaunchNavigationTest.tearDown
    _asset_test_login=fixture.LocalLaunchNavigationTest._asset_test_login

    def identities(self):
        self._asset_test_login()
        actor=User.query.filter_by(username='Kessler').one()
        vendor=User(username='boundary-vendor',email='vendor@example.test',role='watcher',is_active=True)
        set_user_password(vendor,'FixturePassword123!');db.session.add(vendor);db.session.flush()
        db.session.add(NeoScorpionVendorAccount(user_id=vendor.id,gateway_id=Gateway.query.one().id,
            invited_by_user_id=actor.id));db.session.commit()
        return actor.id,vendor.id

    def test_all_public_handlers_all_session_states_and_database_outage(self):
        uid,vid=self.identities()
        with self.client.session_transaction() as cookie:valid_cookie=dict(cookie)
        for label,identity in (('anonymous',None),('regular',uid),('vendor',vid),('revoked',uid),('disabled-vendor',vid)):
            if label=='revoked':db.session.get(User,uid).auth_session_version+=1;db.session.commit()
            if label=='disabled-vendor':db.session.get(NeoScorpionVendorAccount,vid).enabled=False;db.session.commit()
            client=self.app.test_client()
            if identity:
                with client.session_transaction() as cookie:cookie.update(valid_cookie);cookie['_user_id']=str(identity)
            for method in ('GET','HEAD'):
                for path in (*PUBLIC_PATHS,'/manifest/missing.webmanifest','/static/missing-w2.png'):
                    with self.subTest(identity=label,method=method,path=path):
                        g.pop('_login_user',None);statements=[]
                        def sql(_c,_u,s,*a):statements.append(s)
                        event.listen(db.engine,'before_cursor_execute',sql)
                        try:
                            with patch.object(login_manager,'_user_callback',side_effect=OperationalError('Unavailable synthetic user DB',{},None)) as loader, patch(
                                'app.services.neoscorpion_vendors.vendor_account',side_effect=AssertionError('Public asset resolved vendor')):
                                response=client.open(path,method=method)
                                loader.assert_not_called()
                                self.assertEqual(response.status_code,404 if 'missing' in path else 200)
                                self.assertEqual(statements,[])
                                self.assertEqual(response.headers['X-Content-Type-Options'],'nosniff')
                                response.close()
                        finally:event.remove(db.engine,'before_cursor_execute',sql)

    def test_exact_allowlist_does_not_cover_private_qr_unsafe_or_similar_paths(self):
        self.assertEqual(PUBLIC_ASSET_ENDPOINTS,frozenset({'static','pwa_manifest','pwa_manifest_by_key',
            'service_worker','apple_touch_icon','apple_touch_icon_precomposed','favicon_32','favicon_16','favicon_ico'}))
        for path,method in (('/share/neoapps-qr.svg','GET'),('/manifest.webmanifest','POST'),
                ('/service-worker.js','DELETE'),('/private/favicon.ico','GET'),('/neosektor/live-counts/state','GET')):
            with self.subTest(path=path,method=method),self.app.test_request_context(path,method=method):
                self.assertFalse(_is_public_asset_request())
        self.identities();g.pop('_login_user',None)
        with patch.object(login_manager,'_user_callback',side_effect=OperationalError('Unavailable synthetic user DB',{},None)) as loader:
            with self.assertRaises(OperationalError):self.client.get('/share/neoapps-qr.svg')
            loader.assert_called_once()

    def test_terminal_clients_stop_before_identity_but_supported_requests_do_not(self):
        self.identities()
        from app.services.flight_api import FLIGHT_API_AUTO_POLL_CLIENT_HEADER,FLIGHT_API_AUTO_POLL_CLIENT_VERSION
        from app.services.google_motherbrain_live_poll_execution import GOOGLE_LIVE_POLL_HEARTBEAT_CLIENT_HEADER,GOOGLE_LIVE_POLL_HEARTBEAT_CLIENT_VERSION
        for path,headers,version_key,version in (
            ('/motherbrain/flight-api-auto-poll/check',{},FLIGHT_API_AUTO_POLL_CLIENT_HEADER,FLIGHT_API_AUTO_POLL_CLIENT_VERSION),
            ('/motherbrain/google-live-poll/execute',{'X-CSRF-Token':'stale'},GOOGLE_LIVE_POLL_HEARTBEAT_CLIENT_HEADER,GOOGLE_LIVE_POLL_HEARTBEAT_CLIENT_VERSION)):
            g.pop('_login_user',None)
            with self.subTest(path=path),patch.object(login_manager,'_user_callback',side_effect=OperationalError('Unavailable synthetic DB',{},None)) as loader,patch(
                    'app.services.neoscorpion_vendors.vendor_account',side_effect=AssertionError('Terminal client loaded vendor')):
                rejected=self.client.post(path,headers=headers)
                self.assertEqual(rejected.status_code,410);loader.assert_not_called()
            # A current protocol cannot use the terminal fast path as authority.
            g.pop('_login_user',None)
            with patch.object(login_manager,'_user_callback',side_effect=OperationalError('Unavailable synthetic DB',{},None)) as loader:
                with self.assertRaises(OperationalError):self.client.post(path,headers={**headers,version_key:version})
                loader.assert_called_once()
