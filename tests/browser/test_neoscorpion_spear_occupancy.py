"""Actual Dispatch recommendations and live release/blocker transitions."""
from datetime import date, datetime, timedelta
import unittest
from unittest.mock import patch
from playwright.sync_api import expect

from app.extensions import db
from app.models import (NeoScorpionFuelAssignment, NeoScorpionFuelTruck, NeoScorpionFuelWorkState,
    NeoScorpionSettings, NeoScorpionSortAssetState, NeoScorpionSortFueler, NeoScorpionSortTruck,
    NeoScorpionSpearAuditEntry, NeoScorpionTailFuelState, SortDateMission, SortDateOperation,
    SortDateParkingAssignment, SortDateTailState, User)
from app.services.access_control import ensure_default_gateway_and_nodes, backfill_default_gateway_node_roles
from app.services.neoscorpion_assets import record_nightly_operational_change
from tests.browser import test_mobile_drawer as fixture_module


class SpearOccupancyBrowserTest(unittest.TestCase):
    def test_real_current_sort_available_resources_blockers_release_and_live_refresh(self):
        Fixture=fixture_module.MobileDrawerBrowserTest; Fixture.setUpClass()
        Fixture.app.config['LIVE_SCREEN_REFRESH_INTERVAL_MS']=5000
        browser=None
        try:
            with Fixture.app.app_context():
                gateway=ensure_default_gateway_and_nodes(); admin=User.query.filter_by(username='drawer-admin').one()
                free=User(username='resource-free',email='resource-free@example.test',first_name='Free',last_name='Fueler',role='grandmaster',
                    password_hash=admin.password_hash,email_verified_at=datetime.utcnow())
                db.session.add(free); db.session.flush(); backfill_default_gateway_node_roles(free,role='grandmaster')
                operation=SortDateOperation(generated_by_user_id=admin.id,gateway_id=gateway.id,gateway_code=gateway.code,
                    sort_date=date.today(),sort_name='night',window_minutes=60)
                db.session.add(operation); db.session.flush()
                trucks=[]
                for number in ('BUSY','FREE-A','FREE-B'):
                    truck=NeoScorpionFuelTruck(gateway_id=gateway.id,truck_number=number,capacity_gallons=10000,is_active=True)
                    db.session.add(truck); db.session.flush(); trucks.append(truck.id)
                    db.session.add(NeoScorpionSortTruck(sort_date_operation_id=operation.id,fuel_truck_id=truck.id,
                        status='available',starting_gallons=9000,current_gallons=9000))
                db.session.add_all([NeoScorpionSortFueler(sort_date_operation_id=operation.id,user_id=admin.id),
                    NeoScorpionSortFueler(sort_date_operation_id=operation.id,user_id=free.id),
                    NeoScorpionSettings(gateway_id=gateway.id,spear_automation_enabled=False,spear_live_calibration_mode='observe'),
                    NeoScorpionSortAssetState(sort_date_operation_id=operation.id,revision=1)])
                missions=[]
                for index in (1,2):
                    tail=f'N{index}OCCUPIED'
                    mission=SortDateMission(sort_date_operation_id=operation.id,sort_date=operation.sort_date,gateway_code=gateway.code,
                        sort_name='night',mission_type='departure',mission_source='manual',flight_number=f'UPS-OCC-{index}',
                        origin=gateway.code,destination='SDF',assigned_tail_number=tail,timezone='America/Chicago',planned_source='manual',
                        planned_datetime_utc=datetime.utcnow()+timedelta(hours=index+1),planned_fuel_load=25000 if index==1 else None,
                        fuel_status='waiting',departure_status='scheduled')
                    arrival=SortDateMission(sort_date_operation_id=operation.id,sort_date=operation.sort_date,gateway_code=gateway.code,
                        sort_name='night',mission_type='arrival',mission_source='manual',flight_number=f'ARR-OCC-{index}',
                        origin='SDF',destination=gateway.code,assigned_tail_number=tail,arrival_status='on_ground')
                    db.session.add_all([mission,arrival,SortDateTailState(sort_date=operation.sort_date,gateway_code=gateway.code,
                        sort_name='night',tail_number=tail,aircraft_type='757',aircraft_type_source='derived'),
                        NeoScorpionTailFuelState(sort_date_operation_id=operation.id,tail_number=tail,inbound_fuel_lbs=12000),
                        SortDateParkingAssignment(sort_date_operation_id=operation.id,tail_number=tail,ramp_code='B',position_code=f'B{5+index:02d}',lane_number=1)])
                    db.session.flush(); missions.append(mission.id)
                owner=NeoScorpionFuelAssignment(sort_date_operation_id=operation.id,sort_date_mission_id=missions[1],
                    assigned_fueler_user_id=admin.id,assigned_truck_id=trucks[0],confirmed_tail_number='N2OCCUPIED',
                    operational_status='active',review_status='pending')
                db.session.add(owner); db.session.commit()
                opid,ownerid,freeid=operation.id,owner.id,free.id

            with patch('app.services.neoscorpion.current_existing_operational_sort_operations',
                side_effect=lambda gateway,now=None:[db.session.get(SortDateOperation,opid)]):
                browser=Fixture.pw.chromium.launch(); page=browser.new_page(viewport={'width':1440,'height':900})
                Fixture().login(page); page.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1','seen')")
                requests=[]; errors=[]
                page.on('request',lambda request:requests.append((request.method,request.url)))
                page.on('pageerror',lambda error:errors.append(str(error)))
                Fixture().ready(page,'/neoscorpion/fuel-dispatch')
                row=page.locator(f'.neoscorpion-dispatch-primary-row[data-dispatch-mission-id="{missions[0]}"]')
                expect(row.locator('.neoscorpion-dispatch-recommendation')).to_have_count(2)
                expect(row.locator('.neoscorpion-dispatch-recommendation').last).to_contain_text('FREE-')
                expect(row.locator('.neoscorpion-dispatch-recommendation').first).to_contain_text('Free Fueler')
                busy_option=row.locator(f'select[name="assigned_truck_id"] option[value="{trucks[0]}"]').first
                self.assertNotIn('Recommended',busy_option.text_content())
                def change(action):
                    with Fixture.app.app_context():
                        action()
                        record_nightly_operational_change(NeoScorpionSortAssetState.query.filter_by(sort_date_operation_id=opid).one(),opid)
                        db.session.commit()
                change(lambda:NeoScorpionSortFueler.query.filter_by(sort_date_operation_id=opid,user_id=freeid).delete())
                expect(row.locator('.is-unplanned')).to_contain_text('NO AVAILABLE FUELER',timeout=20000)
                def restore_fueler_disable_free_trucks():
                    db.session.add(NeoScorpionSortFueler(sort_date_operation_id=opid,user_id=freeid))
                    for tid in trucks[1:]:
                        NeoScorpionSortTruck.query.filter_by(sort_date_operation_id=opid,fuel_truck_id=tid).one().status='unavailable_oos'
                change(restore_fueler_disable_free_trucks)
                expect(row.locator('.is-unplanned')).to_contain_text('NO AVAILABLE TRUCK',timeout=20000)
                change(lambda:db.session.add(NeoScorpionFuelWorkState(fuel_assignment_id=ownerid,tail_number='N2OCCUPIED',
                    on_at_utc=datetime.utcnow()-timedelta(minutes=20),off_at_utc=datetime.utcnow())))
                expect(row.locator('.neoscorpion-dispatch-recommendation').last).to_have_text('SPEAR → BUSY',timeout=20000)
                expect(row.locator('.is-unplanned')).to_have_count(0)
                for width in (1440,390):
                    page.set_viewport_size({'width':width,'height':900})
                    self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'),width+1)
                    page.screenshot(path=str(Fixture.evidence/f'spear-current-availability-{width}.png'),full_page=True)
                with Fixture.app.app_context():
                    self.assertEqual(NeoScorpionFuelAssignment.query.count(),1)
                    self.assertEqual(NeoScorpionSpearAuditEntry.query.count(),0)
                    self.assertFalse(NeoScorpionSettings.query.one().spear_automation_enabled)
                    self.assertEqual(NeoScorpionSettings.query.one().spear_live_calibration_mode,'observe')
                self.assertFalse(errors)
                self.assertTrue(any('live-panel' in url for _,url in requests))
                self.assertFalse(any(method=='POST' for method,_ in requests))
        finally:
            if browser: browser.close()
            Fixture.tearDownClass()
