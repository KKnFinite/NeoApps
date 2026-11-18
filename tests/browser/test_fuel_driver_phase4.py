"""Synthetic desktop/mobile shared signup, approval, claim and Supervisor View."""
from datetime import datetime, date, timedelta
from pathlib import Path
from threading import Thread
from unittest import TestCase
from unittest.mock import patch
import tempfile
import logging
from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server
from flask_login import login_user
from app import create_app
from app.extensions import db
from app.models import User, Gateway, SortDateOperation, SortDateMission, NeoScorpionFuelAssignment, NeoScorpionFuelTruck, NeoScorpionSortTruck, NeoScorpionVendorAccount, NeoScorpionVendorNight, NeoScorpionTruckDriver, NeoScorpionFuelingEvent, FuelVendorCompany
from app.services import fuel_driver_accounts as accounts
from app.services.accounts_workspace import review_token
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.permission_rules import ensure_default_permission_rules
from app.services.account_foundation_schema import ensure_account_history_guards
from tests.test_account_security_phase1 import fixture_user, PASSWORD
from tests.browser import test_accounts_workspace as workspace


class FuelDriverPhase4BrowserTest(TestCase):
    def test_chromium_webkit_desktop_mobile(self):
        evidence=Path('instance/browser-evidence/accounts-phase4').resolve();evidence.mkdir(parents=True,exist_ok=True)
        fixture=Path(tempfile.mkdtemp(prefix='neoapps-phase4-'))/'fixture.db'
        (evidence/'latest-fixture-path.txt').write_text(str(fixture),encoding='utf-8')
        app=create_app(type('Config',(),dict(SECRET_KEY='phase4-browser',TESTING=True,AUTO_BOOTSTRAP_DATABASE=False,
            CSRF_PROTECT_TESTING=True,SESSION_COOKIE_SECURE=False,AUTH_RATE_LIMIT_ENABLED=False,
            SQLALCHEMY_DATABASE_URI='sqlite:///'+fixture.as_posix(),SQLALCHEMY_TRACK_MODIFICATIONS=False,
            FLIGHT_API_ENABLED=False,GOOGLE_MOTHERBRAIN_IMPORT_ENABLED=False,GOOGLE_MOTHERBRAIN_READER_ENABLED=False,
            LIVE_SCREEN_REFRESH_INTERVAL_MS=0)))
        with app.app_context():
            db.create_all();ensure_account_history_guards(db.session.connection());gateway=ensure_default_gateway_and_nodes();ensure_default_permission_rules()
            admin=fixture_user('phase4-owner',platform='grandmaster',nodes={'scorpion':'master'})
            operation=SortDateOperation(gateway_id=gateway.id,gateway_code=gateway.code,sort_date=date.today(),sort_name='night')
            truck=NeoScorpionFuelTruck(gateway_id=gateway.id,truck_number='TEST-44',capacity_gallons=10000)
            db.session.add_all([operation,truck]);db.session.flush()
            db.session.add(NeoScorpionSortTruck(sort_date_operation_id=operation.id,fuel_truck_id=truck.id,status='available',starting_gallons=6000,current_gallons=6000))
            mission=SortDateMission(sort_date_operation_id=operation.id,gateway_code=gateway.code,sort_date=operation.sort_date,
                sort_name='night',mission_type='departure',mission_source='manual',flight_number='FIX0044',origin=gateway.code,
                destination='SDF',assigned_tail_number='N444UP',planned_datetime_utc=datetime.utcnow()+timedelta(hours=2),timezone='UTC')
            db.session.add(mission);db.session.flush()
            db.session.add(NeoScorpionFuelAssignment(sort_date_operation_id=operation.id,sort_date_mission_id=mission.id,assigned_truck_id=truck.id,review_status='assigned'))
            db.session.commit();op_id,truck_id,admin_id,gateway_id=operation.id,truck.id,admin.id,gateway.id
        server=make_server('127.0.0.1',0,app,threaded=True);worker=Thread(target=server.serve_forever,daemon=True)
        logging.getLogger('werkzeug').setLevel(logging.ERROR);worker.start();origin=f'http://127.0.0.1:{server.server_port}'
        tokens={}
        def capture(user,token):tokens[user.email]=token;return {'sent':False}
        def current(_gateway):return db.session.get(SortDateOperation,op_id)
        try:
            with patch('app.services.neoscorpion.current_sort_operation',side_effect=current),patch('app.auth.routes.email_service.send_email_verification',side_effect=capture),patch('app.auth.routes.email_service.send_access_approved',return_value={'sent':False}),patch('requests.sessions.Session.request',side_effect=AssertionError('External provider forbidden')),sync_playwright() as pw:
                for engine in ('chromium','webkit'):
                    browser=getattr(pw,engine).launch()
                    try:
                        for width in (1280,390):
                            with self.subTest(engine=engine,width=width):
                                prefix=f'{engine}-{width}';email=prefix+'@example.test'
                                context=browser.new_context(viewport={'width':width,'height':900 if width==1280 else 844})
                                page=context.new_page();page.set_default_timeout(12000);errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                                page.goto(origin+'/create-account?account_type=fuel_driver')
                                self.assertTrue(page.locator('[name=employee_id]').evaluate('(el)=>el.required'))
                                for key,value in dict(first_name=prefix,last_name='Driver',email=email,employee_id='ID-'+prefix,supervisor_name='Manager',work_area='Vendor',access_reason='Synthetic fuel truck duties',password=PASSWORD,confirm_password=PASSWORD).items():page.locator(f'[name={key}]').fill(value)
                                page.get_by_role('button',name='Create account & request access').click();expect(page.get_by_role('heading',name='Verify Your Email',exact=True)).to_be_visible()
                                page.goto(origin+'/verify-email/'+tokens[email])
                                with app.app_context():target=User.query.filter_by(email=email).one().id
                                workspace.AccountsWorkspaceBrowserTest.login(page,origin,'phase4-owner@example.test')
                                page.goto(origin+f'/accounts/people/{target}')
                                form=page.locator('form[action$="/fuel-driver-identity"]');form.locator('[name=verification_reference]').fill('Synthetic checked UPS identity')
                                with page.expect_navigation(wait_until='domcontentloaded'):form.get_by_role('button',name='Verify & save UPS ID').click()
                                page.goto(origin+f'/accounts/people/{target}?tab=access')
                                page.once('dialog',lambda d:d.accept())
                                with page.expect_navigation(wait_until='domcontentloaded'):page.get_by_role('button',name='Approve Fuel Truck Driver account').click()
                                workspace.AccountsWorkspaceBrowserTest.login(page,origin,email)
                                expect(page.get_by_text('AWAITING DISPATCHER APPROVAL',exact=True)).to_be_visible()
                                self.assertEqual(page.get_by_text('N444UP',exact=True).count(),0)
                                page.locator('[name=truck_id]').select_option(str(truck_id))
                                with page.expect_navigation(wait_until='domcontentloaded'):page.get_by_role('button',name='REQUEST TRUCK',exact=True).click()
                                expect(page.get_by_text('CLAIM · TRUCK TEST-44 · PENDING',exact=True)).to_be_visible()
                                page.screenshot(path=str(evidence/f'pending-{prefix}.png'),full_page=True)
                                admincontext=browser.new_context(viewport={'width':1280,'height':900});review=admincontext.new_page()
                                workspace.AccountsWorkspaceBrowserTest.login(review,origin,'phase4-owner@example.test')
                                self.assertEqual(review.request.post(origin+f'/accounts/people/{target}/vendor-company',data={'company_id':'1'}).status,400)
                                self.assertEqual(review.goto(origin+'/neoscorpion/driver/manage').status,200)
                                claim=review.locator('article').filter(has=review.locator('[name=request_id]')).filter(has_text=prefix)
                                with review.expect_navigation(wait_until='domcontentloaded'):claim.get_by_role('button',name='APPROVE FOR SORT',exact=True).click()
                                # Existing visibility hook invokes canonical polling without a new poll system.
                                page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
                                expect(page.get_by_role('heading',name='TRUCK TEST-44',exact=True)).to_be_visible(timeout=15000)
                                self.assertFalse(page.evaluate('document.documentElement.scrollWidth>innerWidth+1'))
                                page.screenshot(path=str(evidence/f'approved-{prefix}.png'),full_page=True)
                                with app.test_request_context():
                                    login_user(db.session.get(User,admin_id))
                                    company=accounts.create_company(gateway_id,'Company '+prefix,'Synthetic company verification');db.session.commit()
                                    driver=db.session.get(User,target)
                                    accounts.assign_company(target,review_token(driver),company.id,False,'Synthetic driver affiliation',PASSWORD,target);db.session.commit()
                                    supervisor=fixture_user('super-'+prefix)
                                    db.session.add(NeoScorpionVendorAccount(user_id=supervisor.id,gateway_id=gateway_id,enabled=True,invited_by_user_id=admin_id));db.session.commit()
                                    accounts.verify_ups_id(supervisor.id,review_token(supervisor),'SUP-'+prefix,'Synthetic supervisor identity');db.session.commit()
                                    accounts.assign_company(supervisor.id,review_token(supervisor),company.id,True,'Synthetic supervisor affiliation',PASSWORD,supervisor.id);db.session.commit()
                                    supervisor_id=supervisor.id
                                    company_id=company.id
                                workspace.AccountsWorkspaceBrowserTest.login(page,origin,'super-'+prefix+'@example.test')
                                page.get_by_role('link',name='SUPERVISOR VIEW',exact=True).click()
                                expect(page.get_by_role('heading',name='SUPERVISOR VIEW',exact=True)).to_be_visible()
                                expect(page.get_by_text('N444UP',exact=False)).to_be_visible()
                                self.assertEqual(page.locator('[name=truck_id]').count(),0)
                                self.assertFalse(page.evaluate('document.documentElement.scrollWidth>innerWidth+1'))
                                page.screenshot(path=str(evidence/f'supervisor-{prefix}.png'),full_page=True)
                                # Revoke with the same canonical service, then check the already-open screen.
                                with app.test_request_context():
                                    login_user(db.session.get(User,admin_id))
                                    company=db.session.get(FuelVendorCompany,company_id)
                                    accounts.set_company_active(company.id,False,company.version,'Synthetic company revocation',PASSWORD);db.session.commit()
                                page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
                                expect(page.get_by_role('link',name='SUPERVISOR VIEW',exact=True)).to_be_visible(timeout=15000)
                                self.assertEqual(page.get_by_text('N444UP',exact=False).count(),0)
                                self.assertEqual(errors,[])
                                # Disconnect through canonical Dispatcher service between independent scenarios.
                                from app.services import neoscorpion_vendors as vendors
                                with app.app_context():
                                    vendors.set_participation(db.session.get(Gateway,gateway_id),db.session.get(SortDateOperation,op_id),db.session.get(User,admin_id),target,'revoked',1);db.session.commit()
                                    self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)
                                    self.assertEqual(NeoScorpionSortTruck.query.filter_by(sort_date_operation_id=op_id,fuel_truck_id=truck_id).one().current_gallons,6000)
                                context.close();admincontext.close()
                    finally:browser.close()
        finally:
            server.shutdown();worker.join(5)
            with app.app_context():db.session.remove();db.engine.dispose()
