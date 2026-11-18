"""Focused Chromium/WebKit FOB-to-UPLIFT and blank approval/denial workflows."""
from pathlib import Path
from tempfile import mkdtemp
from threading import Thread
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
import logging

from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server
from app import create_app
from app.extensions import db
from app.models import (PortalAppAccess, NeoScorpionFuelAssignment, NeoScorpionFuelWorkState,
    NeoScorpionFuelingEvent, AccountAccessDecision)
from app.services import neoscorpion as fuel
from tests import test_fob_uplift_optional_reasons as contracts
from tests.test_account_security_phase1 import fixture_user, PASSWORD
from tests.browser.neoscorpion_helpers import wait_for_fuel_saved


class FobUpliftBrowserTest(TestCase):
    def test_chromium_webkit_fob_uplift_and_optional_decisions(self):
        evidence = Path('instance/browser-evidence/fob-uplift-reasons').resolve()
        evidence.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as pw:
            for engine in ('chromium', 'webkit'):
                with self.subTest(engine=engine):
                    fixture = Path(mkdtemp(prefix='neo-fob-uplift-')) / 'fixture.db'
                    (evidence / f'{engine}-fixture.txt').write_text(str(fixture), encoding='utf-8')

                    def build(config):
                        config.SQLALCHEMY_DATABASE_URI = 'sqlite:///' + fixture.as_posix()
                        config.CSRF_PROTECT_TESTING = True
                        config.SESSION_COOKIE_SECURE = False
                        config.AUTH_RATE_LIMIT_ENABLED = False
                        config.LIVE_SCREEN_REFRESH_INTERVAL_MS = 0
                        config.FLIGHT_API_ENABLED = False
                        config.GOOGLE_MOTHERBRAIN_IMPORT_ENABLED = False
                        config.GOOGLE_MOTHERBRAIN_READER_ENABLED = False
                        return create_app(config)

                    case = contracts.FobUpliftTest()
                    with patch.object(contracts.visit_fixtures, 'create_app', side_effect=build):
                        case.setUp()
                    app = case.app
                    operation = case._operation()
                    mission, assignment = case._assignment(operation, required=25000)
                    original = case._save_cycle(assignment, remaining=(9, 9, 9), actual=(10, 10, 10), transfer=0).fuel_work_state
                    truck, nightly = case._truck(operation, SimpleNamespace(assigned_truck_id=None), 'BROWSER-UP', 500)
                    owner = fixture_user('fob-browser-owner', platform='grandmaster')
                    target = fixture_user('fob-browser-applicant')
                    access = PortalAppAccess(user_id=target.id, app_code='neostaffing', role='watcher', status='pending', is_active=True)
                    db.session.add(access)
                    db.session.commit()
                    aid, mid, wid, tid, uid, target_id = assignment.id, mission.id, original.id, truck.id, case.fueler.id, target.id
                    dispatcher_email, fueler_email, nightly_id = case.dispatcher.email, case.fueler.email, nightly.id
                    original_facts = fuel._fuel_rollback_values(original)
                    db.session.remove()
                    server = make_server('127.0.0.1', 0, app, threaded=True)
                    worker = Thread(target=server.serve_forever, daemon=True)
                    logging.getLogger('werkzeug').setLevel(logging.ERROR)
                    worker.start()
                    origin = f'http://127.0.0.1:{server.server_port}'
                    browser = getattr(pw, engine).launch()
                    try:
                        with patch('requests.sessions.Session.request', side_effect=AssertionError('External provider forbidden')), patch('app.auth.routes.email_service.send_access_approved', return_value={'sent': False}):
                            desktop = browser.new_context(viewport={'width':1440,'height':900}, service_workers='block')
                            mobile = browser.new_context(viewport={'width':390,'height':844}, service_workers='block')
                            dispatch, page = desktop.new_page(), mobile.new_page()
                            errors = []
                            for p in (dispatch, page):
                                p.on('pageerror', lambda error: errors.append(str(error)))
                            self.login(dispatch, origin, dispatcher_email, 'TestPassword123!')
                            self.login(page, origin, fueler_email, 'TestPassword123!')
                            dispatch.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1','seen')")
                            dispatch.goto(origin+'/neoscorpion/fuel-dispatch')
                            row = dispatch.locator(f'[data-dispatch-mission-id="{mid}"]')
                            with dispatch.expect_response(lambda r: r.request.method=='POST' and '/fuel-dispatch/fuel-on-board' in r.url) as fob_response:
                                row.get_by_role('button', name='FOB', exact=True).click()
                            self.assertIn(fob_response.value.status,(200,302))
                            expect(row).to_have_class(__import__('re').compile('is-complete'))
                            with app.app_context():
                                original_facts = fuel._fuel_rollback_values(db.session.get(NeoScorpionFuelWorkState,wid))
                                self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)
                            row.locator('[data-neoscorpion-dispatch-details]').click()
                            section = dispatch.locator(f'[data-dispatch-sort-group="{mid}"] [data-console-section="start-uplift"]').first
                            section.locator('[data-operation-toggle]').click()
                            section.locator('[name="required_fuel"]').fill('32.0')
                            section.locator('button[type="submit"]').click()
                            row = dispatch.locator(f'[data-dispatch-mission-id="{mid}"]')
                            expect(row).to_have_attribute('data-dispatch-row-key', f'{mid}-2')
                            row.locator('[name="assigned_fueler_user_id"]').select_option(str(uid))
                            row.locator('[name="assigned_truck_id"]').select_option(str(tid))
                            with dispatch.expect_response(lambda r: r.request.method=='POST' and '/fuel-dispatch/assignment' in r.url) as assigned:
                                row.locator('[data-dispatch-assignment-submit]').click()
                            self.assertEqual(assigned.value.status,200)
                            page.goto(origin+'/neoscorpion/fueler')
                            card = page.locator(f'[data-fuel-data-card][data-fuel-assignment-id="{aid}"]').first
                            expect(card.locator('[name="remaining_left"]')).to_have_value('10.0')
                            card.locator('[data-truck-on-form] button').click()
                            expect(card.locator('.neoscorpion-truck-on-row')).to_contain_text('CONFIRMED')
                            card.locator('[name="apu_running"]').select_option('no')
                            for code, value in [('left','12'),('ctr','10'),('right','10')]:
                                card.locator(f'[name="actual_{code}"]').fill(value)
                            card.locator('[name="transfer_fuel_gallons"]').fill('300')
                            card.locator('[name="transfer_fuel_gallons"]').press('Tab')
                            wait_for_fuel_saved(card)
                            card.locator('.neoscorpion-fueler-off-form button').click()
                            expect(card.get_by_text(__import__('re').compile(r'TRUCK OFF .*CONFIRMED'))).to_be_visible()
                            card.locator('[data-fueler-submit-form] button').click()
                            expect(card.locator('[data-fueler-submission-status]')).to_contain_text('READY FOR DISPATCHER COMPLETE')
                            page.screenshot(path=str(evidence/f'{engine}-fueler-submitted.png'), full_page=True)
                            dispatch.reload()
                            row = dispatch.locator(f'[data-dispatch-mission-id="{mid}"]')
                            row.locator('button[formaction$="/fuel-dispatch/complete"]').click()
                            expect(row).to_have_class(__import__('re').compile('is-complete'))
                            dispatch.screenshot(path=str(evidence/f'{engine}-uplift-completed.png'), full_page=True)
                            with app.app_context():
                                event = NeoScorpionFuelingEvent.query.filter_by(fuel_assignment_id=aid).one()
                                self.assertEqual((event.event_type,event.cycle_number,event.transfer_fuel_gallons),('uplift',2,300))
                                self.assertEqual(db.session.get(type(nightly),nightly_id).current_gallons,200)
                                self.assertEqual(fuel._fuel_rollback_values(db.session.get(NeoScorpionFuelWorkState,wid)),original_facts)
                            dispatch = browser.new_context(viewport={'width':1440,'height':900}).new_page()
                            self.login(dispatch, origin, 'fob-browser-owner@example.test', PASSWORD)
                            for outcome in ('denied', 'approved'):
                                dispatch.goto(origin+f'/accounts/people/{target_id}?tab=access')
                                form = dispatch.locator('form[action$="/application"]').filter(has=dispatch.locator('[name="app_code"][value="neostaffing"]'))
                                self.assertFalse(form.locator('[name="reason"]').evaluate('el=>el.required'))
                                form.locator('[name="reason"]').fill('')
                                with dispatch.expect_navigation(wait_until='domcontentloaded'):
                                    form.locator(f'button[value="{outcome}"]').click()
                                dispatch.wait_for_url(lambda url: 'tab=access' in url)
                                self.assertEqual(dispatch.locator('.aa-workspace').count(),1)
                                with app.app_context():
                                    self.assertEqual(PortalAppAccess.query.filter_by(user_id=target_id,app_code='neostaffing').one().status,outcome)
                            dispatch.set_viewport_size({'width':390,'height':844})
                            dispatch.screenshot(path=str(evidence/f'{engine}-optional-decisions-mobile.png'),full_page=True)
                            self.assertFalse(dispatch.evaluate('document.documentElement.scrollWidth>innerWidth+1'))
                            self.assertEqual(errors,[])
                    finally:
                        browser.close()
                        server.shutdown()
                        worker.join(5)
                        db.session.remove()
                        db.engine.dispose()
                        case.context.pop()

    @staticmethod
    def login(page, origin, email, password):
        page.goto(origin+'/login')
        page.locator('[name="email"]').fill(email)
        page.locator('[name="password"]').fill(password)
        page.locator('button[type="submit"]').first.click()
        page.wait_for_url(lambda url:'/login' not in url)
