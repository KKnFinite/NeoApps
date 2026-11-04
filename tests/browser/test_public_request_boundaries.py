"""Real login/vendor sessions, browser-fetched PWA assets and outage isolation."""
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from flask import has_request_context,request
from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from app.extensions import db,login_manager
from app.models import User,NeoScorpionVendorAccount
from app.services import neoscorpion_vendors as vendors
from app.services.access_control import ensure_default_gateway_and_nodes
from tests.browser import test_mobile_drawer as fixture
from tests.test_public_request_boundaries import PUBLIC_PATHS


class PublicAssetsBrowserTest(unittest.TestCase):
    def test_chromium_webkit_login_vendor_revocation_assets_and_database_outage(self):
        F=fixture.MobileDrawerBrowserTest;F.setUpClass();output=Path('instance/w2/browser');output.mkdir(parents=True,exist_ok=True)
        asset_sql=[]
        def sql(_c,_u,statement,*unused):
            if has_request_context() and request.path in PUBLIC_PATHS:asset_sql.append(statement)
        try:
            with F.app.app_context():
                actor=User.query.filter_by(username='drawer-admin').one();gateway=ensure_default_gateway_and_nodes()
                user,token=vendors.invite(gateway,actor,'boundary-vendor@example.test','Synthetic Vendor')
                db.session.commit();vendors.accept_invitation(token,'VendorPassword123!','VendorPassword123!');db.session.commit()
                uid,vid=actor.id,user.id;engine=db.engine
            event.listen(engine,'before_cursor_execute',sql)
            try:
                for engine_name in ('chromium','webkit'):
                    browser=getattr(F.pw,engine_name).launch()
                    try:
                        for identity in ('anonymous','regular','vendor','revoked'):
                            context=browser.new_context(service_workers='block');page=context.new_page();errors=[]
                            page.on('pageerror',lambda e:errors.append(str(e)))
                            if identity!='anonymous':
                                page.goto(F.origin+'/login');page.locator('[name=email]').fill('boundary-vendor@example.test' if identity=='vendor' else 'drawer-admin@example.test')
                                page.locator('[name=password]').fill('VendorPassword123!' if identity=='vendor' else 'BrowserFixture123!')
                                with page.expect_navigation(wait_until='domcontentloaded'):page.locator('button[type="submit"]').first.click()
                                if identity=='vendor':
                                    self.assertEqual(page.goto(F.origin+'/neostaffing/').status,403)
                                if identity=='revoked':
                                    with F.app.app_context():db.session.get(User,uid).auth_session_version+=1;db.session.commit()
                            with patch.object(login_manager,'_user_callback',side_effect=OperationalError('Synthetic DB outage',{},None)):
                                for path in PUBLIC_PATHS:
                                    self.assertEqual(page.goto(F.origin+path).status,200,(engine_name,identity,path))
                            self.assertEqual(asset_sql,[])
                            if identity=='revoked':
                                page.goto(F.origin+'/portal');self.assertIn('/login',page.url)
                            page.screenshot(path=str(output/f'{engine_name}-{identity}-public.png'))
                            self.assertEqual(errors,[]);context.close()
                    finally:browser.close()
            finally:event.remove(engine,'before_cursor_execute',sql)
        finally:F.tearDownClass()
