"""Focused Hanzo OFF display and real revision-refresh regression."""
import unittest
from datetime import date, datetime
from unittest.mock import patch

from playwright.sync_api import expect
from tests.browser import test_mobile_drawer as fixtures
from app.extensions import db
from app.models import (User, SortDateOperation, SortDateMission,
                        NeoScorpionFuelAssignment, NeoScorpionFuelWorkState,
                        NeoScorpionSortAssetState)
from app.services.access_control import ensure_default_gateway_and_nodes


class HanzoOffDisplayBrowserTest(unittest.TestCase):
    def test_desktop_mobile_and_revision_refresh(self):
        fixture = fixtures.MobileDrawerBrowserTest
        fixture.setUpClass()
        try:
            fixture.app.config['LIVE_SCREEN_REFRESH_INTERVAL_MS'] = 5000
            with fixture.app.app_context():
                gateway = ensure_default_gateway_and_nodes()
                user = User.query.filter_by(username='drawer-admin').one()
                operation = SortDateOperation(generated_by_user_id=user.id,
                    gateway_id=gateway.id, gateway_code=gateway.code,
                    sort_date=date(2026, 8, 17), sort_name='night', window_minutes=60)
                db.session.add(operation)
                db.session.flush()
                mission = SortDateMission(sort_date_operation_id=operation.id,
                    sort_date=operation.sort_date, gateway_code=gateway.code, sort_name='night',
                    mission_type='departure', mission_source='manual', flight_number='UPS1501',
                    origin=gateway.code, destination='SDF', timezone='America/Chicago',
                    planned_datetime_local=datetime(2026, 8, 17, 23),
                    planned_datetime_utc=datetime(2026, 8, 18, 4), planned_source='manual',
                    assigned_tail_number='N412UP', tail_source='manual',
                    planned_fuel_load=50000, fuel_status='waiting', departure_status='loading')
                db.session.add(mission)
                db.session.flush()
                assignment = NeoScorpionFuelAssignment(sort_date_operation_id=operation.id,
                    sort_date_mission_id=mission.id, assigned_fueler_user_id=user.id)
                db.session.add(assignment)
                db.session.add(NeoScorpionSortAssetState(sort_date_operation_id=operation.id, revision=1))
                db.session.commit()
                operation_id, assignment_id = operation.id, assignment.id
            forecast = [datetime(2026, 8, 18, 7, 45)]
            def predict(row, *args, **kwargs):
                work = row.get('fuel_work_state')
                value = None if work and work.off_at_utc else forecast[0]
                return dict(likely=value, goal=value, difference=0, source='BASELINE')
            with patch('app.services.neoscorpion.current_existing_operational_sort_operations',
                       side_effect=lambda gateway, now=None: [db.session.get(SortDateOperation, operation_id)]), \
                 patch('app.services.neoscorpion.physical_off_forecast', side_effect=predict):
                browser = fixture.pw.chromium.launch()
                try:
                    page = browser.new_page(viewport=dict(width=1440, height=1000))
                    fixture().login(page)
                    fixture().ready(page, '/neoscorpion/hanzo')
                    expect(page.locator('[data-hanzo-est-off]')).to_have_text('02:45')
                    expect(page.locator('[data-hanzo-actual-off]')).to_have_text('-')
                    for width in (1440, 390):
                        page.set_viewport_size(dict(width=width, height=844))
                        expect(page.locator('[data-hanzo-actual-off]')).to_be_visible()
                        self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
                        self.assertTrue(page.locator('.neoscorpion-hanzo-fueling').evaluate('''el => {
                            const values = el.querySelectorAll('dd');
                            return getComputedStyle(values[3]).fontSize === getComputedStyle(values[5]).fontSize;
                        }'''))
                    forecast[0] = datetime(2026, 8, 18, 7, 50)
                    with fixture.app.app_context():
                        NeoScorpionSortAssetState.query.filter_by(sort_date_operation_id=operation_id).one().revision += 1
                        db.session.commit()
                    expect(page.locator('[data-hanzo-est-off]')).to_have_text('02:50', timeout=15000)
                    with fixture.app.app_context():
                        db.session.add(NeoScorpionFuelWorkState(fuel_assignment_id=assignment_id,
                            tail_number='N412UP', off_at_utc=datetime(2026, 8, 18, 7, 40)))
                        NeoScorpionSortAssetState.query.filter_by(sort_date_operation_id=operation_id).one().revision += 1
                        db.session.commit()
                    expect(page.locator('[data-hanzo-actual-off]')).to_have_text('02:40', timeout=15000)
                    expect(page.locator('[data-hanzo-est-off]')).to_have_text('-')
                finally:
                    browser.close()
        finally:
            fixture.tearDownClass()
