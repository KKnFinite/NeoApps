"""Actual Dispatcher assignment/handoff/follow-up and read-only Truck Driver UI."""
import re
import unittest
from datetime import date,datetime,timedelta
from pathlib import Path
from unittest.mock import patch
from playwright.sync_api import expect
from app.extensions import db
from app.models import (User,PortalAppAccess,SortDateOperation,SortDateMission,NeoScorpionFuelAssignment,
    NeoScorpionFuelTruck,NeoScorpionSortTruck,NeoScorpionSortFueler,NeoScorpionFuelWorkState,NeoScorpionFuelingEvent)
from app.services.access_control import ensure_default_gateway_and_nodes,backfill_default_gateway_node_roles
from app.services.password_policy import set_user_password
from tests.browser import test_mobile_drawer as fixtures
from tests.browser.neoscorpion_helpers import wait_for_fuel_saved
from tests.neoscorpion_reactivation_workflow import event_snapshot

EVIDENCE=Path('instance/r03g/browser')


class ReactivationBrowserTest(unittest.TestCase):
    def test_chromium_webkit_complete_followup_and_driver_history(self):
        F=fixtures.MobileDrawerBrowserTest;F.setUpClass();F.app.config['LIVE_SCREEN_REFRESH_INTERVAL_MS']=5000
        EVIDENCE.mkdir(parents=True,exist_ok=True)
        try:
            with F.app.app_context():
                gateway=ensure_default_gateway_and_nodes();admin=User.query.filter_by(username='drawer-admin').one()
                users=[]
                for name in ('r03g-fueler','r03g-driver'):
                    user=User(username=name,email=name+'@example.test',role='watcher',email_verified_at=datetime.now())
                    set_user_password(user,'BrowserFixture123!');db.session.add(user);db.session.flush()
                    backfill_default_gateway_node_roles(user,role='operator')
                    access=PortalAppAccess.query.filter_by(user_id=user.id,app_code='neogateway').one()
                    access.status='approved';access.role='operator';access.is_active=True;users.append(user.id)
                operation=SortDateOperation(generated_by_user_id=admin.id,gateway_id=gateway.id,gateway_code=gateway.code,
                    sort_date=date.today(),sort_name='night',window_minutes=60)
                db.session.add(operation);db.session.flush();oid=operation.id
                db.session.add(NeoScorpionSortFueler(sort_date_operation_id=oid,user_id=users[0]));cases=[]
                for index in range(2):
                    truck=NeoScorpionFuelTruck(gateway_id=gateway.id,truck_number=f'G-{20+index}',capacity_gallons=20000)
                    db.session.add(truck);db.session.flush()
                    db.session.add(NeoScorpionSortTruck(sort_date_operation_id=oid,fuel_truck_id=truck.id,status='available',starting_gallons=10000,current_gallons=10000))
                    missions=[]
                    for number in range(3):
                        mission=SortDateMission(sort_date_operation_id=oid,sort_date=operation.sort_date,gateway_code=gateway.code,
                            sort_name='night',mission_type='departure',mission_source='manual',flight_number=f'UPS-G{index}{number}',
                            origin=gateway.code,destination='MSP',assigned_tail_number=f'N{491+index if number==0 else 590+index*3+number}UP',
                            planned_datetime_utc=datetime.utcnow()+timedelta(hours=2+number),timezone='America/Chicago',planned_fuel_load=30000)
                        db.session.add(mission);db.session.flush()
                        assignment=NeoScorpionFuelAssignment(sort_date_operation_id=oid,sort_date_mission_id=mission.id,confirmed_tail_number=mission.assigned_tail_number)
                        db.session.add(assignment);db.session.flush();missions.append((mission.id,assignment.id))
                    cases.append((truck.id,missions))
                db.session.commit()
            with patch('app.services.neoscorpion.current_existing_operational_sort_operations',side_effect=lambda g,now=None:[db.session.get(SortDateOperation,oid)]):
                for engine,(tid,missions) in zip((F.pw.chromium,F.pw.webkit),cases):
                    browser=engine.launch();errors=[]
                    try:
                        desktop=browser.new_context(viewport={'width':1440,'height':900},service_workers='block')
                        mobile=browser.new_context(viewport={'width':390,'height':844},service_workers='block')
                        driver_context=browser.new_context(viewport={'width':390,'height':844},service_workers='block')
                        dispatch=desktop.new_page();page=mobile.new_page();driver=driver_context.new_page()
                        for p in (dispatch,page,driver):p.on('pageerror',lambda e:errors.append(str(e)))
                        F().login(dispatch);F().login(page,'r03g-fueler');F().login(driver,'r03g-driver')
                        dispatch.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1','seen')")
                        F().ready(dispatch,'/neoscorpion/fuel-dispatch')
                        for index,(mid,aid) in enumerate(missions):
                            row=dispatch.locator(f'[data-dispatch-mission-id="{mid}"]')
                            if index==0:row.locator('[name="assigned_fueler_user_id"]').select_option(str(users[0]))
                            row.locator('[name="assigned_truck_id"]').select_option(str(tid))
                            with dispatch.expect_response(lambda r:r.request.method=='POST' and '/fuel-dispatch/assignment' in r.url) as saved:
                                row.locator('[data-dispatch-assignment-submit]').click()
                            self.assertEqual(saved.value.status,200)
                            expect(row.locator('[name="expected_assigned_truck_id"]')).to_have_value(str(tid),timeout=15000)
                        mid,aid=missions[0];row=dispatch.locator(f'[data-dispatch-mission-id="{mid}"]')
                        F().ready(driver,'/neoscorpion/truck-driver')
                        selection=driver.locator('[data-driver-form]').first
                        selection.locator('[data-driver-truck]').select_option(str(tid))
                        with driver.expect_response(lambda r:r.request.method=='POST' and '/truck-driver/assignment' in r.url) as chosen:
                            selection.get_by_role('button',name='SELECT TRUCK',exact=True).click()
                        self.assertEqual(chosen.value.status,200)
                        expect(driver.locator('[data-board-work-key="driver-upcoming"] [data-driver-mission]')).to_have_count(3,timeout=15000)
                        F().ready(page,'/neoscorpion/fueler')
                        card=page.locator(f'[data-fuel-data-card][data-fuel-assignment-id="{aid}"]').first
                        card.locator('[data-truck-on-form] button').click()
                        expect(card.locator('.neoscorpion-truck-on-row')).to_contain_text('CONFIRMED')
                        wid=card.get_attribute('data-physical-work-id')
                        expect(driver.locator(f'[data-board-work-key="driver-current"] [data-physical-work-id="{wid}"]')).to_have_count(1,timeout=15000)
                        card.locator('[name="apu_running"]').select_option('no')
                        for code in ('left','ctr','right'):
                            card.locator(f'[name="remaining_{code}"]').fill('0' if code=='ctr' else '6')
                            card.locator(f'[name="actual_{code}"]').fill('0' if code=='ctr' else '15')
                        card.locator('[name="transfer_fuel_gallons"]').fill('3000')
                        card.locator('[name="transfer_fuel_gallons"]').press('Tab');wait_for_fuel_saved(card)
                        card.locator('.neoscorpion-fueler-off-form button').click();expect(card).to_contain_text('TRUCK OFF')
                        previous=driver.locator(f'[data-board-work-key="driver-previous"] [data-physical-work-id="{wid}"]')
                        expect(previous).to_be_visible(timeout=15000);expect(previous).to_contain_text('awaits final data / Dispatcher COMPLETE')
                        center=previous.locator('[data-tank-code="ctr"] [role="cell"]').last
                        self.assertTrue(center.inner_text().startswith('0'))
                        expect(previous.locator('input,select,textarea')).to_have_count(0)
                        with F.app.app_context():
                            self.assertIsNone(db.session.get(NeoScorpionFuelAssignment,aid).completed_at_utc)
                            self.assertEqual(NeoScorpionFuelingEvent.query.filter_by(fuel_assignment_id=aid).count(),0)
                        card.locator('[data-fueler-submit-form] button').click()
                        expect(card.locator('[data-fueler-submission-status]')).to_contain_text('READY FOR DISPATCHER COMPLETE')
                        with F.app.app_context():version=db.session.get(NeoScorpionFuelWorkState,int(wid)).fueler_submission_version
                        expect(row.locator('[name="expected_submission_version"]')).to_have_value(str(version),timeout=15000)
                        row.locator('button[formaction$="/fuel-dispatch/complete"]').click()
                        expect(row).to_have_class(re.compile('is-complete'),timeout=15000)
                        with F.app.app_context():
                            closed=NeoScorpionFuelingEvent.query.filter_by(fuel_assignment_id=aid).one();original=event_snapshot(closed)
                            self.assertEqual(closed.fuel_work_state_id,int(wid))
                            self.assertEqual(NeoScorpionSortTruck.query.filter_by(fuel_truck_id=tid).one().current_gallons,7000)
                        row.locator('[data-neoscorpion-dispatch-details]').click()
                        detail=dispatch.locator(f'#neoscorpion-dispatch-detail-{mid}-1')
                        detail.locator('[data-console-section="start-uplift"] > button').click()
                        form=detail.locator('[data-cycle-start]').filter(has=dispatch.locator('[name="cycle_type"][value="uplift"]'))
                        form.locator('[name="required_fuel"]').fill('35')
                        with dispatch.expect_navigation():form.get_by_role('button',name='START UPLIFT',exact=True).click()
                        current=dispatch.locator(f'.neoscorpion-dispatch-primary-row[data-dispatch-mission-id="{mid}"]')
                        expect(current).not_to_have_class(re.compile('is-complete'))
                        with F.app.app_context():
                            assignment=db.session.get(NeoScorpionFuelAssignment,aid)
                            fresh=db.session.get(NeoScorpionFuelWorkState,assignment.active_work_state_id)
                            self.assertEqual(assignment.current_cycle_number,2);self.assertNotEqual(fresh.id,int(wid))
                            self.assertIsNone(fresh.on_at_utc);self.assertIsNone(fresh.fueler_submitted_at_utc)
                            self.assertTrue(all(t.actual_lbs is None for t in fresh.tank_states))
                            self.assertEqual(event_snapshot(NeoScorpionFuelingEvent.query.filter_by(fuel_assignment_id=aid).one()),original)
                            self.assertEqual(NeoScorpionSortTruck.query.filter_by(fuel_truck_id=tid).one().current_gallons,7000)
                        driver.screenshot(path=str(EVIDENCE/f'{engine.name}-driver-previous.png'),full_page=True)
                        dispatch.screenshot(path=str(EVIDENCE/f'{engine.name}-reactivated.png'),full_page=True)
                        self.assertFalse(errors,errors)
                    finally:browser.close()
        finally:F.tearDownClass()
