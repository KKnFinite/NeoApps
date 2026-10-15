"""Actual driver selection, tank refresh and mobile read-only rendering."""
import json
import unittest
from datetime import date,datetime,timedelta
from unittest.mock import patch
from playwright.sync_api import expect
from app.extensions import db
from app.models import (User,SortDateOperation,SortDateMission,NeoScorpionFuelAssignment,
    NeoScorpionFuelTruck,NeoScorpionSortTruck,NeoScorpionSortAssetState,NeoScorpionSettings)
from app.services import neoscorpion as service
from app.services.access_control import ensure_default_gateway_and_nodes
from tests.browser import test_mobile_drawer as fixtures


class DriverAndTanksBrowserTest(unittest.TestCase):
    def test_driver_selection_canonical_fueler_updates_and_responsive_boards(self):
        Fixture=fixtures.MobileDrawerBrowserTest
        Fixture.setUpClass(); browser=None
        Fixture.app.config['LIVE_SCREEN_REFRESH_INTERVAL_MS']=5000
        try:
            with Fixture.app.app_context():
                gateway=ensure_default_gateway_and_nodes()
                actor=User.query.filter_by(username='drawer-admin').one()
                at=datetime.utcnow().replace(microsecond=0)-timedelta(minutes=10)
                operation=SortDateOperation(generated_by_user_id=actor.id,gateway_id=gateway.id,
                    gateway_code=gateway.code,sort_date=date.today(),sort_name='night',window_minutes=60)
                truck=NeoScorpionFuelTruck(gateway_id=gateway.id,truck_number='422808',capacity_gallons=20000)
                db.session.add_all([operation,truck]); db.session.flush()
                db.session.add_all([NeoScorpionSortAssetState(sort_date_operation_id=operation.id,revision=1),
                    NeoScorpionSortTruck(sort_date_operation_id=operation.id,fuel_truck_id=truck.id,status='available',starting_gallons=10000,current_gallons=10000),
                    NeoScorpionSettings(gateway_id=gateway.id)])
                mission=SortDateMission(sort_date_operation_id=operation.id,sort_date=operation.sort_date,
                    gateway_code=gateway.code,sort_name='night',mission_type='departure',mission_source='manual',
                    flight_number='UPS1038',origin=gateway.code,destination='SDF',assigned_tail_number='N491UP',
                    planned_datetime_utc=at+timedelta(hours=2),timezone='America/Chicago',planned_fuel_load=25000,fuel_status='assigned')
                db.session.add(mission); db.session.flush()
                assignment=NeoScorpionFuelAssignment(sort_date_operation_id=operation.id,sort_date_mission_id=mission.id,
                    assigned_fueler_user_id=actor.id,assigned_truck_id=truck.id,review_status='assigned')
                db.session.add(assignment); db.session.commit()
                operation_id,assignment_id,truck_id,actor_id,gateway_id=operation.id,assignment.id,truck.id,actor.id,gateway.id
            with patch('app.services.neoscorpion.current_existing_operational_sort_operations',
                       side_effect=lambda gateway,now=None:[db.session.get(SortDateOperation,operation_id)]):
                with Fixture.app.app_context():
                    service.confirm_fueler_truck_on(db.session.get(type(gateway),gateway_id),db.session.get(User,actor_id),assignment_id,
                        expected_cycle=1,expected_tail='N491UP',expected_truck_id=truck_id,now_utc=at)
                    db.session.commit()
                browser=Fixture.pw.chromium.launch()
                page=browser.new_context(viewport={'width':1440,'height':950}).new_page()
                errors=[]; page.on('pageerror',lambda error:errors.append(str(error)))
                Fixture().login(page); Fixture().ready(page,'/neoscorpion/truck-driver')
                page.locator('[data-driver-form]:not([data-driver-management]) select[name=truck_id]').select_option(str(truck_id))
                page.get_by_role('button',name='SELECT TRUCK',exact=True).click()
                expect(page.locator('[data-selected-driver-truck]')).to_have_text('TRUCK 422808',timeout=15000)
                expect(page.locator('[data-driver-mission]')).to_have_attribute('data-physical-work-id',str(1))
                self.assertEqual(page.locator('[data-driver-mission] input').count(),0)
                board=page.context.new_page()
                board.set_viewport_size({'width':390,'height':844})
                Fixture().ready(board,'/neoscorpion/fueling-board')
                expect(board.locator('[data-tank-code=left] [role=cell]').first).to_have_text('-')
                self.assertEqual(page.locator('.neoscorpion-driver-management').count(),0)
                # Save through the actual authenticated Fueler endpoint.
                with Fixture.app.app_context():
                    context=service.fueler_context(db.session.get(type(gateway),gateway_id),db.session.get(User,actor_id))
                    expected=context['rows'][0]['edit_baseline']
                response=page.request.post(Fixture.origin+f'/neoscorpion/fueler/assignments/{assignment_id}',
                    form=dict(csrf_token=page.locator('input[name=csrf_token]').first.get_attribute('value'),
                              expected=json.dumps(expected),apu_running='no',remaining_left='6',remaining_ctr='0',remaining_right='6',
                              actual_left='15',actual_ctr='0',actual_right='15',transfer_fuel_gallons='3000'),headers={'Accept':'application/json'})
                self.assertEqual(response.status,200,response.text())
                for screen in (page,board):
                    expect(screen.locator('[data-tank-code=left] [role=cell]').last).to_have_text('15.0',timeout=15000)
                    expect(screen.locator('[data-tank-code=ctr] [role=cell]').last).not_to_have_text('-')
                for screen in (page,board):
                    for width in (1440,390,375):
                        screen.set_viewport_size({'width':width,'height':844})
                        self.assertFalse(screen.evaluate('document.documentElement.scrollWidth>innerWidth+1'))
                        tank=screen.locator('.neoscorpion-readonly-tanks')
                        self.assertEqual(tank.locator('[role=columnheader]').all_text_contents(),['Tank','Remaining','Planned','Actual'])
                        self.assertTrue(tank.evaluate('el=>Array.from(el.querySelectorAll("[role=cell],[role=columnheader]")).every(c=>c.getBoundingClientRect().right<=el.getBoundingClientRect().right+1)'))
                        self.assertTrue(screen.locator('.neoscorpion-fuel-status-chip').evaluate('el=>el.scrollWidth<=el.clientWidth+1'))
                        screen.screenshot(path=str(Fixture.evidence/f'driver-tanks-{screen==page}-{width}.png'),full_page=True)
                page.reload(); expect(page.locator('[data-selected-driver-truck]')).to_have_text('TRUCK 422808')
                Fixture().ready(page,'/neoscorpion/driver/manage')
                manage=page.locator('[data-vendor-driver-assignment]').first
                manage.locator('[name=user_id]').select_option('')
                manage.get_by_role('button',name='SAVE DRIVER').click()
                Fixture().ready(page,'/neoscorpion/truck-driver')
                expect(page.locator('[data-selected-driver-truck]')).to_have_text('SELECT YOUR TRUCK',timeout=15000)
                Fixture().ready(page,'/neoscorpion/driver/manage')
                manage=page.locator('[data-vendor-driver-assignment]').first
                manage.locator('[name=user_id]').select_option(str(actor_id))
                manage.get_by_role('button',name='SAVE DRIVER').click()
                Fixture().ready(page,'/neoscorpion/truck-driver')
                expect(page.locator('[data-selected-driver-truck]')).to_have_text('TRUCK 422808',timeout=15000)
                device=browser.new_context(viewport={'width':390,'height':844}).new_page()
                Fixture().login(device); Fixture().ready(device,'/neoscorpion/truck-driver')
                expect(device.locator('[data-selected-driver-truck]')).to_have_text('TRUCK 422808')
                page.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1','seen')")
                dispatch=page.context.new_page(); Fixture().ready(dispatch,'/neoscorpion/fuel-dispatch')
                for width,height in ((1440,98),(390,73)):
                    dispatch.set_viewport_size({'width':width,'height':844})
                    row=dispatch.locator('.neoscorpion-dispatch-primary-row').first
                    self.assertEqual(row.evaluate('el=>getComputedStyle(el.closest("table")).getPropertyValue("--dispatch-row-height").trim()'),f'{height}px')
                    # Native table rows may grow for existing full-data content.
                    # The added upper truck badge must not change that geometry.
                    actual_height=row.bounding_box()['height']
                    self.assertGreaterEqual(actual_height,height)
                    baseline_height=dispatch.locator('.neoscorpion-truck-driver-name').first.evaluate('el=>{const parent=el.parentNode,next=el.nextSibling;el.remove();const h=document.querySelector(".neoscorpion-dispatch-primary-row").getBoundingClientRect().height;parent.insertBefore(el,next);return h;}')
                    self.assertEqual(actual_height,baseline_height)
                    expect(dispatch.locator('.neoscorpion-truck-driver-name')).to_contain_text('DRIVER:')
                self.assertFalse(errors,errors)
        finally:
            if browser: browser.close()
            Fixture.tearDownClass()
