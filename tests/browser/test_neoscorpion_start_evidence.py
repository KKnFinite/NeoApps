"""Actual UI: preparation, busy rejection, real OFF, travel, explicit new ON."""
import unittest
from datetime import date,datetime,timedelta
from pathlib import Path
from unittest.mock import patch
from playwright.sync_api import expect
from app.extensions import db
from app.models import (User,PortalAppAccess,SortDateOperation,SortDateMission,SortDateParkingAssignment,
    NeoScorpionFuelAssignment,NeoScorpionFuelTruck,NeoScorpionSortTruck,NeoScorpionSortFueler,
    NeoScorpionFuelWorkState,NeoScorpionFuelingEvent)
from app.services.access_control import ensure_default_gateway_and_nodes,backfill_default_gateway_node_roles
from app.services.password_policy import set_user_password
from tests.browser import test_mobile_drawer as fixtures
from tests.browser.neoscorpion_helpers import wait_for_fuel_saved


class StartEvidenceBrowserTest(unittest.TestCase):
    def test_chromium_webkit_busy_release_travel_and_explicit_physical_start(self):
        F=fixtures.MobileDrawerBrowserTest;F.setUpClass();evidence=Path('instance/r03i/browser');evidence.mkdir(parents=True,exist_ok=True)
        try:
            F.app.config['LIVE_SCREEN_REFRESH_INTERVAL_MS']=5000
            with F.app.app_context():
                gateway=ensure_default_gateway_and_nodes();admin=User.query.filter_by(username='drawer-admin').one()
                operation=SortDateOperation(generated_by_user_id=admin.id,gateway_id=gateway.id,gateway_code=gateway.code,
                    sort_date=date.today(),sort_name='night',window_minutes=60)
                db.session.add(operation);db.session.flush();oid=operation.id;cases=[]
                for index in range(2):
                    name=f'r03i-fueler-{index}';user=User(username=name,email=name+'@example.test',role='watcher',email_verified_at=datetime.now())
                    set_user_password(user,'BrowserFixture123!');db.session.add(user);db.session.flush();backfill_default_gateway_node_roles(user,role='operator')
                    access=PortalAppAccess.query.filter_by(user_id=user.id,app_code='neogateway').one();access.status='approved';access.is_active=True;access.role='operator'
                    db.session.add(NeoScorpionSortFueler(sort_date_operation_id=oid,user_id=user.id))
                    truck=NeoScorpionFuelTruck(gateway_id=gateway.id,truck_number=f'I-{index}',capacity_gallons=20000)
                    db.session.add(truck);db.session.flush()
                    db.session.add(NeoScorpionSortTruck(sort_date_operation_id=oid,fuel_truck_id=truck.id,status='available',starting_gallons=10000,current_gallons=10000))
                    missions=[]
                    for number,(ramp,position) in enumerate((('A',f'A0{4+index}'),('B',f'B0{6+index}'))):
                        tail=f'N{491+index*2+number}UP'
                        mission=SortDateMission(sort_date_operation_id=oid,sort_date=operation.sort_date,gateway_code=gateway.code,sort_name='night',
                            mission_type='departure',mission_source='manual',flight_number=f'UPS-I{index}{number}',origin=gateway.code,destination='MSP',
                            assigned_tail_number=tail,planned_datetime_utc=datetime.utcnow()+timedelta(hours=2+number),timezone='America/Chicago',planned_fuel_load=52000)
                        db.session.add(mission);db.session.flush()
                        assignment=NeoScorpionFuelAssignment(sort_date_operation_id=oid,sort_date_mission_id=mission.id,confirmed_tail_number=tail)
                        db.session.add_all([assignment,SortDateParkingAssignment(sort_date_operation_id=oid,tail_number=tail,ramp_code=ramp,position_code=position)])
                        db.session.flush();missions.append((mission.id,assignment.id))
                    cases.append((name,user.id,truck.id,missions))
                db.session.commit()
            with patch('app.services.neoscorpion.current_existing_operational_sort_operations',side_effect=lambda g,now=None:[db.session.get(SortDateOperation,oid)]):
                for engine,(name,uid,tid,missions) in zip((F.pw.chromium,F.pw.webkit),cases):
                    browser=engine.launch();errors=[]
                    try:
                        desktop=browser.new_context(viewport={'width':1440,'height':900},service_workers='block')
                        mobile=browser.new_context(viewport={'width':390,'height':844},service_workers='block')
                        dispatch=desktop.new_page();page=mobile.new_page()
                        for p in (dispatch,page):p.on('pageerror',lambda err:errors.append(str(err)))
                        F().login(dispatch);F().login(page,name);dispatch.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1','seen')")
                        F().ready(dispatch,'/neoscorpion/fuel-dispatch')
                        for mid,aid in missions:
                            row=dispatch.locator(f'[data-dispatch-mission-id="{mid}"]')
                            row.locator('[name="assigned_fueler_user_id"]').select_option(str(uid));row.locator('[name="assigned_truck_id"]').select_option(str(tid))
                            with dispatch.expect_response(lambda r:r.request.method=='POST' and '/fuel-dispatch/assignment' in r.url) as saved:row.locator('[data-dispatch-assignment-submit]').click()
                            self.assertEqual(saved.value.status,200)
                        F().ready(page,'/neoscorpion/fueler');aid,bid=missions[0][1],missions[1][1]
                        first=page.locator(f'[data-fuel-data-card][data-fuel-assignment-id="{aid}"]').first
                        second=page.locator(f'[data-fuel-data-card][data-fuel-assignment-id="{bid}"]').first
                        second.locator('[name="apu_running"]').select_option('no')
                        for code in ('left','ctr','right'):second.locator(f'[name="remaining_{code}"]').fill('12' if code=='left' else '0')
                        second.locator('[name="remaining_right"]').press('Tab');wait_for_fuel_saved(second)
                        with F.app.app_context():
                            w=db.session.get(NeoScorpionFuelWorkState,db.session.get(NeoScorpionFuelAssignment,bid).active_work_state_id);wid=w.id
                            self.assertEqual((w.manual_on_at_utc,w.on_at_utc,w.off_at_utc),(None,)*3)
                        first.locator('[data-truck-on-form] button').click();expect(first.locator('.neoscorpion-truck-on-row')).to_contain_text('CONFIRMED')
                        with page.expect_response(lambda r:r.request.method=='POST' and r.url.endswith(f'/assignments/{bid}/on')) as blocked:second.locator('[data-truck-on-form] button').click()
                        self.assertEqual(blocked.value.status,400);self.assertIn('TRUCK STILL FUELING',blocked.value.json()['error'])
                        with F.app.app_context():self.assertIsNone(db.session.get(NeoScorpionFuelWorkState,wid).manual_on_at_utc)
                        F().ready(dispatch,'/neoscorpion/fuel-dispatch')
                        expect(dispatch.locator(f'[data-dispatch-mission-id="{missions[0][0]}"] [data-fuel-status]').first).to_have_attribute('data-fuel-status','fueling')
                        dispatch.screenshot(path=str(evidence/f'{engine.name}-dispatch-busy.png'),full_page=True)
                        first.locator('.neoscorpion-fueler-off-form button').click();expect(first).to_contain_text('TRUCK OFF')
                        # The deliberately rejected ON retains its local error
                        # feedback. Read a fresh projection, preserving saved data.
                        F().ready(page,'/neoscorpion/fueler')
                        expect(second.locator('[data-assigned-truck-status]')).to_contain_text('COMING FROM ALPHA',timeout=15000)
                        page.screenshot(path=str(evidence/f'{engine.name}-travel-before-on.png'),full_page=True)
                        with F.app.app_context():
                            self.assertIsNone(db.session.get(NeoScorpionFuelWorkState,wid).manual_on_at_utc)
                            released=db.session.get(NeoScorpionFuelWorkState,db.session.get(NeoScorpionFuelAssignment,aid).active_work_state_id)
                            off=released.off_at_utc;self.assertIsNotNone(off)
                        second.locator('[data-truck-on-form] button').click();expect(second.locator('.neoscorpion-truck-on-row')).to_contain_text('CONFIRMED')
                        with F.app.app_context():
                            assignment=db.session.get(NeoScorpionFuelAssignment,bid);w=db.session.get(NeoScorpionFuelWorkState,wid)
                            self.assertEqual(assignment.active_work_state_id,wid);self.assertEqual(w.manual_on_by_user_id,uid)
                            self.assertIsNotNone(w.manual_on_at_utc);self.assertIsNone(w.on_at_utc);self.assertIsNone(w.off_at_utc)
                            self.assertEqual(db.session.get(NeoScorpionFuelWorkState,released.id).off_at_utc,off)
                            self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)
                            self.assertEqual(NeoScorpionSortTruck.query.filter_by(fuel_truck_id=tid).one().current_gallons,10000)
                        self.assertEqual(errors,[])
                    finally:browser.close()
        finally:F.tearDownClass()
