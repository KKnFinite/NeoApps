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
                free=User(username='resource-free',email='resource-free@example.test',first_name='DANIEL',last_name='',role='grandmaster',
                    password_hash=admin.password_hash,email_verified_at=datetime.utcnow())
                db.session.add(free); db.session.flush(); backfill_default_gateway_node_roles(free,role='grandmaster')
                operation=SortDateOperation(generated_by_user_id=admin.id,gateway_id=gateway.id,gateway_code=gateway.code,
                    sort_date=date.today(),sort_name='night',window_minutes=60)
                db.session.add(operation); db.session.flush()
                trucks=[]
                for number in ('BUSY','10','11'):
                    truck=NeoScorpionFuelTruck(gateway_id=gateway.id,truck_number=number,capacity_gallons=10000,is_active=True)
                    db.session.add(truck); db.session.flush(); trucks.append(truck.id)
                    db.session.add(NeoScorpionSortTruck(sort_date_operation_id=operation.id,fuel_truck_id=truck.id,
                        status='available',starting_gallons=9000,current_gallons=9000))
                db.session.add_all([NeoScorpionSortFueler(sort_date_operation_id=operation.id,user_id=admin.id),
                    NeoScorpionSortFueler(sort_date_operation_id=operation.id,user_id=free.id),
                    NeoScorpionSettings(gateway_id=gateway.id,spear_automation_enabled=False,spear_live_calibration_mode='observe'),
                    NeoScorpionSortAssetState(sort_date_operation_id=operation.id,revision=1)])
                missions=[]
                for index in (1,2,3):
                    tail='N432UP' if index==1 else f'N{index}OCCUPIED'
                    mission=SortDateMission(sort_date_operation_id=operation.id,sort_date=operation.sort_date,gateway_code=gateway.code,
                        sort_name='night',mission_type='departure',mission_source='manual',flight_number='UPS0910' if index==1 else f'UPS-OCC-{index}',
                        origin=gateway.code,destination='SDF',assigned_tail_number=tail,timezone='America/Chicago',planned_source='manual',
                        planned_datetime_utc=None if index==3 else datetime.utcnow()+timedelta(hours=index+1),planned_fuel_load=None if index==2 else 25000,
                        fuel_status='waiting',departure_status='scheduled')
                    arrival=SortDateMission(sort_date_operation_id=operation.id,sort_date=operation.sort_date,gateway_code=gateway.code,
                        sort_name='night',mission_type='arrival',mission_source='manual',flight_number=f'ARR-OCC-{index}',
                        origin='SDF',destination=gateway.code,assigned_tail_number=tail,arrival_status='on_ground',actual_block_in_datetime_utc=datetime.utcnow()-timedelta(minutes=10))
                    db.session.add_all([mission,arrival,SortDateTailState(sort_date=operation.sort_date,gateway_code=gateway.code,
                        sort_name='night',tail_number=tail,aircraft_type='757',aircraft_type_source='derived'),
                        NeoScorpionTailFuelState(sort_date_operation_id=operation.id,tail_number=tail,inbound_fuel_lbs=30000 if index==3 else 12000),
                        SortDateParkingAssignment(sort_date_operation_id=operation.id,tail_number=tail,ramp_code={1:'E',2:'B',3:'D'}[index],position_code={1:'E03',2:'B07',3:'D07'}[index],lane_number=1)])
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
                expect(row.locator('.neoscorpion-dispatch-recommendation').last).to_contain_text('10')
                expect(row.locator('.neoscorpion-dispatch-recommendation').first).to_contain_text('DANIEL')
                expect(row.locator('[data-spear-tail-indicator]')).to_have_count(1)
                expect(row.locator('[data-spear-tail-indicator]')).to_have_text('SPEAR · READY')
                self.assertNotIn('COVERED',row.locator('.neoscorpion-dispatch-tail').inner_text())
                expect(row.locator('xpath=following-sibling::tr[1]')).to_contain_text('COVERED')
                card=page.locator('.neoscorpion-truck-spear').filter(has_text='FLIGHT UPS0910').first
                for text in ('PARKING E03','TRUCK 10','FUELER DANIEL'): expect(card).to_contain_text(text)
                self.assertNotIn('COVERED',card.inner_text())
                expect(card.locator('button')).to_have_text('ASSIGN')
                self.assertIn('/fuel-dispatch/spear-action',card.locator('form').get_attribute('action'))
                expect(card.locator('xpath=ancestor::article[1]').locator('[data-dispatch-truck-card-form] button')).to_have_text('TOP OFF')
                def verify_compact():
                    for indicator in page.locator('[data-spear-tail-indicator]').all():
                        styles=indicator.evaluate("el=>{const s=getComputedStyle(el);return {whiteSpace:s.whiteSpace,color:s.color,font:parseFloat(s.fontSize),height:el.getBoundingClientRect().height,text:el.textContent,width:el.clientWidth,content:el.scrollWidth,clipped:el.scrollWidth>el.clientWidth+1};}")
                        self.assertEqual(styles['whiteSpace'],'nowrap')
                        self.assertEqual(styles['color'],'rgb(103, 197, 242)')
                        self.assertLessEqual(styles['font'],8.5)
                        self.assertLessEqual(styles['height'],10)
                        self.assertFalse(styles['clipped'],styles)
                    for status in page.locator('[data-fuel-status]').all():
                        self.assertLessEqual(status.locator('[data-fuel-status-secondary]').count(),1)
                    for item in page.locator('.neoscorpion-truck-spear > strong,.neoscorpion-truck-spear > span,.neoscorpion-truck-spear > small,.neoscorpion-dispatch-primary-row .neoscorpion-dispatch-recommendation').all():
                        self.assertEqual(item.evaluate('el=>getComputedStyle(el).color'),'rgb(103, 197, 242)')
                    self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'),page.viewport_size['width']+1,
                        page.evaluate("[...document.querySelectorAll('header,section,article,form,button,h1,h2')].filter(el=>{const r=el.getBoundingClientRect();return r.width&&r.right>innerWidth+1;}).map(el=>({tag:el.tagName,class:el.className,text:el.textContent.slice(0,80),right:el.getBoundingClientRect().right}))"))
                for width in (1440,390):
                    page.set_viewport_size({'width':width,'height':900}); verify_compact()
                    page.screenshot(path=str(Fixture.evidence/f'spear-display-ready-{width}.png'),full_page=True)
                for path in ('/neoscorpion/fuel-dispatch','/neoscorpion/fuel-dispatch/live-panel'):
                    self.assertEqual(page.request.get(Fixture.origin+path).status,200)
                busy_option=row.locator(f'select[name="assigned_truck_id"] option[value="{trucks[0]}"]').first
                self.assertNotIn('Recommended',busy_option.text_content())
                def change(action):
                    with Fixture.app.app_context():
                        action()
                        record_nightly_operational_change(NeoScorpionSortAssetState.query.filter_by(sort_date_operation_id=opid).one(),opid)
                        db.session.commit()
                change(lambda:NeoScorpionSortFueler.query.filter_by(sort_date_operation_id=opid,user_id=freeid).delete())
                expect(row.locator('.is-unplanned')).to_contain_text('SPEAR · NO FUELER',timeout=20000)
                for width in (1440,390,320):
                    page.set_viewport_size({'width':width,'height':900}); verify_compact()
                def restore_fueler_disable_free_trucks():
                    db.session.add(NeoScorpionSortFueler(sort_date_operation_id=opid,user_id=freeid))
                    for tid in trucks[1:]:
                        NeoScorpionSortTruck.query.filter_by(sort_date_operation_id=opid,fuel_truck_id=tid).one().status='unavailable_oos'
                change(restore_fueler_disable_free_trucks)
                expect(row.locator('.is-unplanned')).to_contain_text('SPEAR · NO TRUCK',timeout=20000)
                for width in (1440,390,320):
                    page.set_viewport_size({'width':width,'height':900}); verify_compact()
                    expect(row.locator('xpath=following-sibling::tr[1]')).to_contain_text('NO AVAILABLE TRUCK — active assignment')
                    page.screenshot(path=str(Fixture.evidence/f'spear-display-no-truck-{width}.png'),full_page=True)
                change(lambda:db.session.add(NeoScorpionFuelWorkState(fuel_assignment_id=ownerid,tail_number='N2OCCUPIED',
                    on_at_utc=datetime.utcnow()-timedelta(minutes=20),off_at_utc=datetime.utcnow())))
                expect(row.locator('.neoscorpion-dispatch-recommendation').last).to_have_text('SPEAR → BUSY',timeout=20000)
                expect(row.locator('.is-unplanned')).to_have_count(0)
                for width in (1440,390):
                    page.set_viewport_size({'width':width,'height':900})
                    verify_compact()
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
