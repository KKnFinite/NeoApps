"""Dispatch review placement: no preparation alarm, recovery on both layouts."""
import unittest
from datetime import date, datetime, timedelta
from unittest.mock import patch
from playwright.sync_api import expect
from app.extensions import db
from app.models import (User, SortDateOperation, SortDateMission, NeoScorpionFuelAssignment,
    NeoScorpionFuelTruck, NeoScorpionSortTruck, NeoScorpionSortAssetState)
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.neoscorpion_segments import new_work
from tests.browser import test_mobile_drawer as fixtures


class OutstandingWorkBrowserTest(unittest.TestCase):
    def test_preparation_details_and_mobile_physical_recovery(self):
        Fixture = fixtures.MobileDrawerBrowserTest
        Fixture.setUpClass(); browser = None
        try:
            with Fixture.app.app_context():
                gateway = ensure_default_gateway_and_nodes()
                actor = User.query.filter_by(username='drawer-admin').one()
                now = datetime.utcnow()
                operation = SortDateOperation(generated_by_user_id=actor.id, gateway_id=gateway.id,
                    gateway_code=gateway.code, sort_date=date.today(), sort_name='night', window_minutes=60)
                truck = NeoScorpionFuelTruck(gateway_id=gateway.id, truck_number='20', capacity_gallons=20000)
                db.session.add_all([operation, truck]); db.session.flush()
                db.session.add_all([NeoScorpionSortAssetState(sort_date_operation_id=operation.id, revision=1),
                    NeoScorpionSortTruck(sort_date_operation_id=operation.id, fuel_truck_id=truck.id,
                        status='available', starting_gallons=10000, current_gallons=10000)])
                ids = []
                for index, tail in enumerate(('N438UP', 'N492UP')):
                    mission = SortDateMission(sort_date_operation_id=operation.id, sort_date=operation.sort_date,
                        gateway_code=gateway.code, sort_name='night', mission_type='departure', mission_source='manual',
                        flight_number=f'UPS{634+index}', origin=gateway.code, destination='SDF', assigned_tail_number=tail,
                        timezone='America/Chicago', planned_datetime_utc=now+timedelta(hours=2),
                        planned_fuel_load=25000, fuel_status='assigned')
                    db.session.add(mission); db.session.flush()
                    assignment = NeoScorpionFuelAssignment(sort_date_operation_id=operation.id,
                        sort_date_mission_id=mission.id, assigned_fueler_user_id=actor.id,
                        assigned_truck_id=truck.id, confirmed_tail_number=tail)
                    db.session.add(assignment); db.session.flush()
                    old = new_work(assignment, tail if index == 0 else 'N491UP', actor, now)
                    if index:
                        old.manual_on_at_utc=now; old.manual_on_truck_id=truck.id
                        old.manual_on_cycle_number=1; old.manual_on_by_user_id=actor.id
                    new_work(assignment, tail, actor, now+timedelta(seconds=1))
                    ids.append((mission.id, old.id))
                db.session.commit(); operation_id = operation.id
            with patch('app.services.neoscorpion.current_existing_operational_sort_operations',
                       side_effect=lambda gateway, now=None: [db.session.get(SortDateOperation, operation_id)]):
                browser = Fixture.pw.chromium.launch()
                page = browser.new_page(viewport={'width':1440,'height':900})
                Fixture().login(page)
                page.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1','seen')")
                Fixture().ready(page, '/neoscorpion/fuel-dispatch')
                header = page.locator('[data-dispatch-board-header]')
                expect(header.locator(f'[data-mobile-prior-work="{ids[0][0]}"]')).to_have_count(0)
                page.locator(f'[data-dispatch-mission-id="{ids[0][0]}"] [data-neoscorpion-dispatch-details]').click()
                detail = page.locator(f'#neoscorpion-dispatch-detail-{ids[0][0]}-1')
                expect(detail.locator('[data-outstanding-work]')).to_be_visible()
                expect(detail.locator('summary').filter(has_text='PRIOR PREPARATION N438UP')).to_be_visible()
                self.assertNotIn('ORIGINAL N438UP', page.content())
                page.set_viewport_size({'width':390,'height':844})
                recovery = header.locator(f'[data-mobile-prior-work="{ids[1][0]}"]')
                recovery.locator(':scope > summary').click()
                recovery.locator(f'[data-prior-work-id="{ids[1][1]}"] > summary').click()
                expect(recovery.locator('[data-fuel-data-open]')).to_be_visible()
                self.assertIn(f'physical_work_id={ids[1][1]}', recovery.locator('[data-fuel-data-open]').get_attribute('data-fuel-data-open'))
                self.assertFalse(page.evaluate('document.documentElement.scrollWidth>innerWidth+1'))
                page.screenshot(path=str(Fixture.evidence/'dispatch-prior-work-mobile.png'), full_page=True)
        finally:
            if browser: browser.close()
            Fixture.tearDownClass()
