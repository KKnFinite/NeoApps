"""Real inline edit/reset UI, source import and immutable authority in both engines."""
from datetime import date, datetime, timedelta
import unittest
from unittest.mock import patch

from playwright.sync_api import expect

from app.extensions import db
from app.models import Gateway, SortDateMission, SortDateOperation, NeoRainMissionOverride
from app.services.google_rain_live_milestones import apply_google_rain_departure_milestones
from tests.browser import test_mobile_drawer as fixtures


class RainLoadAuthorityBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = fixtures.MobileDrawerBrowserTest
        cls.fixture.setUpClass()
        cls.fixture.app.config['SECURITY_HEADERS_CSP_ALLOW_NON_PRODUCTION'] = True

    @classmethod
    def tearDownClass(cls):
        cls.fixture.tearDownClass()

    def exercise(self, engine):
        day = date(2026, 8, 10) + timedelta(days=engine == 'webkit')
        actual = datetime.combine(day + timedelta(days=1), datetime.min.time()).replace(hour=7)
        with self.fixture.app.app_context():
            gateway = Gateway.query.filter_by(code='RFD').one()
            operation = SortDateOperation(gateway_id=gateway.id, gateway_code='RFD', sort_date=day, sort_name='night')
            db.session.add(operation); db.session.flush()
            mission = SortDateMission(sort_date_operation_id=operation.id, gateway_code='RFD', sort_date=day,
                sort_name='night', mission_type='departure', mission_source='manual', flight_number='UPS0910',
                origin='RFD', destination='LAX', assigned_tail_number='N123UP', timezone='America/Chicago',
                planned_datetime_utc=actual.replace(minute=24), departure_status='scheduled',
                ramp_load_completed_at_utc=actual, ramp_load_completed_source='unknown')
            db.session.add(mission); db.session.commit()
            oid, mid = operation.id, mission.id
        def incoming(value):
            with self.fixture.app.app_context():
                result = apply_google_rain_departure_milestones(db.session.get(SortDateOperation, oid),
                    rows=[dict(sheet_row=4, flight_number='UPS0910', ramp_load_complete=value)])
                db.session.commit()
                self.assertEqual(result['applied_count'], 1)
        browser = getattr(self.fixture.pw, engine).launch(headless=True)
        context = browser.new_context(viewport={'width':1920 if engine == 'chromium' else 390,'height':900})
        try:
            page = context.new_page(); page.set_default_timeout(10000)
            errors=[]; page.on('pageerror',lambda error: errors.append(str(error)))
            self.fixture().login(page)
            with patch('app.neonodes.neorain.routes.current_neorain_outbound_operation',
                       side_effect=lambda gateway:db.session.get(SortDateOperation,oid)):
                incoming('2:30')
                self.fixture().ready(page,'/neorain/outbound')
                clock = page.locator(f'[data-rain-override-mission="{mid}"][data-rain-override-field="ramp_load_complete"] [data-rain-override-time]:visible').first
                def open_details():
                    if not clock.count():
                        page.locator('[data-neorain-mobile-row-open]:visible').first.click()
                open_details()
                expect(clock).to_have_value('02:30')
                with page.expect_response(lambda r:'/neorain/override' in r.url and r.request.method=='POST') as response:
                    clock.fill('0200')
                self.assertEqual(response.value.status,200)
                incoming('2:45'); page.reload()
                open_details()
                expect(clock).to_have_value('02:00')
                with self.fixture.app.app_context():
                    row = NeoRainMissionOverride.query.filter_by(sort_date_mission_id=mid,field='ramp_load_complete').one()
                    self.assertTrue(row.active)
                    self.assertEqual(db.session.get(SortDateMission,mid).ramp_load_completed_source,'neorain')
                clock.click()
                with page.expect_response(lambda r:'/neorain/override' in r.url and r.request.method=='POST') as response:
                    page.locator('[data-rain-reset-selected]:visible').click()
                self.assertEqual(response.value.status,200)
                expect(clock).to_have_value('02:45')
                incoming('2:50'); page.reload()
                open_details()
                expect(clock).to_have_value('02:50')
                self.assertEqual(errors,[])
        finally:
            context.close(); browser.close()

    def test_chromium(self):self.exercise('chromium')
    def test_webkit(self):self.exercise('webkit')
