from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from unittest import TestCase

from playwright.sync_api import expect

from app.extensions import db
from app.models import (Gateway, NeoScorpionFuelAssignment, NeoScorpionSortFueler, NeoScorpionVendorAccount,
                        SortDateMission, SortDateOperation, User)
from app.services.neoscorpion_assets import set_nightly_fueler_break
from tests.browser import test_mobile_drawer as fixtures
from tests.browser.test_security_headers import enforced_csp
from tests.test_account_security_phase1 import fixture_user
from tests.test_accounts_view_as import VIEW_AS, supervisor_fixture


class AccountsViewAsBrowserTest(TestCase):
    def test_desktop_and_mobile_preview_uses_read_only_live_routes(self):
        fixture = fixtures.MobileDrawerBrowserTest()
        with enforced_csp():
            fixture.setUpClass()
            try:
                fixture.app.config['LIVE_SCREEN_REFRESH_INTERVAL_MS'] = 5000
                with fixture.app.app_context():
                    gateway = Gateway.query.filter_by(code='RFD').one()
                    actor = User.query.filter_by(username='drawer-admin').one()
                    supervisor_id = supervisor_fixture(gateway, actor).id
                    driver_id = fixture_user('browser-driver').id
                    db.session.add(NeoScorpionVendorAccount(user_id=driver_id, gateway_id=gateway.id,
                                      enabled=True, invited_by_user_id=actor.id))
                    fueler_id = fixture_user('browser-fueler', nodes={'scorpion': 'operator'}).id
                    local_now = datetime(2026, 8, 18, 22)
                    from tests.test_neorain_sort_refresh import configure_sort_window
                    configure_sort_window(gateway, local_now.date())
                    fixture.app.config['CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE'] = local_now
                    operation = SortDateOperation(sort_date=local_now.date(), gateway_id=gateway.id,
                        gateway_code=gateway.code, sort_name='night', window_minutes=60, generated_by_user_id=actor.id)
                    db.session.add(operation)
                    db.session.flush()
                    operation_id = operation.id
                    mission = SortDateMission(sort_date_operation_id=operation_id, sort_date=operation.sort_date,
                        gateway_code=gateway.code, sort_name='night', mission_type='departure', mission_source='manual',
                        flight_number='UPS501', origin='RFD', destination='SDF', assigned_tail_number='N501UP',
                        planned_datetime_utc=datetime.utcnow() + timedelta(minutes=30))
                    db.session.add(mission)
                    db.session.flush()
                    db.session.add_all([
                        NeoScorpionFuelAssignment(sort_date_operation_id=operation_id, sort_date_mission_id=mission.id,
                                                   assigned_fueler_user_id=fueler_id, confirmed_tail_number='N501UP'),
                        NeoScorpionSortFueler(sort_date_operation_id=operation_id, user_id=fueler_id),
                    ])
                    db.session.commit()
                for engine, width, height in [('chromium', 1440, 900), ('webkit', 390, 844)]:
                    with self.subTest(engine=engine):
                        browser = getattr(fixture.pw, engine).launch()
                        try:
                            page = browser.new_page(viewport={'width': width, 'height': height})
                            page.clock.install(time=local_now.replace(tzinfo=ZoneInfo('America/Chicago')))
                            self.exercise(fixture, page, driver_id, supervisor_id, fueler_id, operation_id)
                        finally:
                            browser.close()
            finally:
                fixture.tearDownClass()

    def exercise(self, fixture, page, driver_id, supervisor_id, fueler_id, operation_id):
        fixture.login(page)
        page.goto(fixture.origin + '/accounts')
        page.get_by_role('link', name='View As').click()
        expect(page).to_have_url(fixture.origin + VIEW_AS)
        for label, key, subject, evidence in [
            ('Fuel Truck Driver', 'fuel-truck-driver', driver_id, 'AWAITING DISPATCHER APPROVAL'),
            ('Vendor Supervisor', 'vendor-supervisor', supervisor_id, 'SUPERVISOR VIEW'),
            ('NeoScorpion Fueler', 'fueler', fueler_id, 'N501UP'),
        ]:
            if subject:
                page.locator(f'form[action="{VIEW_AS}/{key}"] select').select_option(str(subject))
            page.get_by_role('button', name=f'Open {label} preview').click()
            expect(page.get_by_text('PREVIEW MODE', exact=True)).to_be_visible()
            expect(page.get_by_text(evidence, exact=True).first).to_be_visible()
            expect(page.locator('form')).to_have_count(0)
            self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'))
            if key in ('vendor-supervisor', 'fuel-truck-driver'):
                expect(page.locator('[data-vendor-sort-missions] tr[data-mission-id]')).to_have_count(1)
                expect(page.locator('[data-vendor-sort-missions]')).to_contain_text('UPS501')
            if key == 'fueler':
                remaining = page.locator('[data-fueler-remaining]')
                expect(remaining).to_have_text('1 fueling assignments remaining')
                expect(remaining).to_be_visible()
                self.assertTrue(remaining.evaluate('el => el === el.parentElement.lastElementChild'))
                with fixture.app.app_context():
                    operation = db.session.get(SortDateOperation, operation_id)
                    unassigned = SortDateMission(sort_date_operation_id=operation.id, sort_date=operation.sort_date,
                        gateway_code=operation.gateway_code, sort_name=operation.sort_name, mission_type='departure',
                        mission_source='manual', flight_number='UPS502', origin='RFD', destination='SDF')
                    db.session.add(unassigned)
                    db.session.commit()
                    unassigned_id = unassigned.id
                expect(remaining).to_have_text('2 fueling assignments remaining', timeout=25000)
                expect(page.get_by_text('YOU ARE ON BREAK')).to_be_hidden()
                with fixture.app.app_context():
                    operation = db.session.get(SortDateOperation, operation_id)
                    set_nightly_fueler_break(operation, fueler_id, '1', '0')
                    db.session.commit()
                expect(page.get_by_text('YOU ARE ON BREAK')).to_be_visible(timeout=25000)
                with fixture.app.app_context():
                    operation = db.session.get(SortDateOperation, operation_id)
                    set_nightly_fueler_break(operation, fueler_id, '0', '1')
                    db.session.get(SortDateMission, unassigned_id).departure_status = 'cancelled'
                    db.session.commit()
                expect(remaining).to_have_text('1 fueling assignments remaining', timeout=25000)
            page.get_by_role('link', name='Exit preview').click()
            expect(page).to_have_url(fixture.origin + VIEW_AS)
