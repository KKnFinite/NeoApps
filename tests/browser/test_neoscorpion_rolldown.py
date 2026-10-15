"""Real authenticated report, COPY/CSV, historical selection and mobile bounds."""
import os
import unittest
from datetime import date,datetime,timedelta
from app.extensions import db
from app.models import (User,SortDateOperation,SortDateMission,NeoScorpionFuelTruck,NeoScorpionSortTruck,
    NeoScorpionFuelAssignment,NeoScorpionFuelWorkState,NeoScorpionFuelingEvent,NeoScorpionTopOffEvent)
from app.services.access_control import ensure_default_gateway_and_nodes
from playwright.sync_api import expect
from tests.browser import test_mobile_drawer as fixtures


class RolldownBrowserTest(unittest.TestCase):
    def test_report_readonly_history_copy_csv_and_responsive_layout(self):
        F=fixtures.MobileDrawerBrowserTest
        F.setUpClass();browser=None
        try:
            with F.app.app_context():
                gateway=ensure_default_gateway_and_nodes();actor=User.query.filter_by(username='drawer-admin').one()
                operation=SortDateOperation(gateway_id=gateway.id,gateway_code=gateway.code,sort_date=date.today()-timedelta(days=1),
                    sort_name='night',window_minutes=60,generated_by_user_id=actor.id)
                truck=NeoScorpionFuelTruck(gateway_id=gateway.id,truck_number='422808',capacity_gallons=10000)
                db.session.add_all([operation,truck]);db.session.flush()
                db.session.add(NeoScorpionSortTruck(sort_date_operation_id=operation.id,fuel_truck_id=truck.id,
                    status='available',starting_gallons=8000,current_gallons=3000))
                mission=SortDateMission(sort_date_operation_id=operation.id,sort_date=operation.sort_date,gateway_code=gateway.code,
                    sort_name='night',mission_type='departure',mission_source='manual',flight_number='UPS1038',origin=gateway.code,
                    destination='SDF',assigned_tail_number='N491UP',timezone='America/Chicago')
                db.session.add(mission);db.session.flush()
                assignment=NeoScorpionFuelAssignment(sort_date_operation_id=operation.id,sort_date_mission_id=mission.id)
                db.session.add(assignment);db.session.flush()
                at=datetime(2026,10,15,4,50)
                for index,(tail,gallons) in enumerate((('N491UP',3000),('N492UP',500),('N491UP',2000))):
                    start=at+timedelta(minutes=index*20)
                    work=NeoScorpionFuelWorkState(fuel_assignment_id=assignment.id,tail_number=tail,visit_number=index+1,
                        physical_sequence_number=index+1,physical_cycle_number=1,physical_truck_id=truck.id,
                        physical_fueler_user_id=actor.id,manual_on_at_utc=start,off_at_utc=start+timedelta(minutes=10),
                        physical_transfer_gallons=gallons,physical_finalized_at_utc=start+timedelta(minutes=15))
                    db.session.add(work);db.session.flush()
                    db.session.add(NeoScorpionFuelingEvent(sort_date_operation_id=operation.id,fuel_assignment_id=assignment.id,
                        fuel_work_state_id=work.id,tail_number=tail,fuel_truck_id=truck.id,sequence_number=index+1,
                        started_at_utc=start,ended_at_utc=work.off_at_utc,transfer_fuel_gallons=gallons,
                        fueler_user_id=actor.id,created_at=start+timedelta(minutes=15)))
                sent=NeoScorpionTopOffEvent(sort_date_operation_id=operation.id,fuel_truck_id=truck.id,kind='sent',
                    occurred_at_utc=at+timedelta(hours=2),gallons=2500,inventory_verified=True,inventory_epoch=0,actor_user_id=actor.id)
                db.session.add(sent);db.session.flush()
                db.session.add(NeoScorpionTopOffEvent(sort_date_operation_id=operation.id,fuel_truck_id=truck.id,kind='returned',sent_event_id=sent.id,
                    occurred_at_utc=at+timedelta(hours=2,minutes=20),gallons=9000,inventory_verified=True,inventory_epoch=1,actor_user_id=actor.id))
                db.session.commit();operation_id,truck_id=operation.id,truck.id
            browser=getattr(F.pw,os.environ.get('NEO_ROLLDOWN_BROWSER','chromium')).launch()
            context=browser.new_context(viewport={'width':1440,'height':900});page=context.new_page();errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            F().login(page)
            F().ready(page,'/neoscorpion/reports')
            page.get_by_role('link',name='Truck Rolldown',exact=False).click()
            page.wait_for_url('**/reports/truck-rolldown')
            page.locator('[name=operation_id]').select_option(str(operation_id))
            with page.expect_navigation():page.get_by_role('button',name='View',exact=True).click()
            expect(page.locator('.rolldown-event').filter(has_text='UPLIFT')).to_have_count(0)
            expect(page.locator('.rolldown-event').filter(has_text='FUEL POSTED')).to_have_count(3)
            expect(page.locator('[data-truck-summary]')).to_contain_text('5,500')
            self.assertEqual(page.locator('.neoscorpion-rolldown .neoscorpion-panel form[method=post]').count(),0)
            page.evaluate("Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>window.__copied=text}})")
            page.get_by_role('button',name='COPY REPORT').click()
            expect(page.locator('[data-prof-copy-status]')).to_have_text('Copied')
            self.assertEqual(page.evaluate('window.__copied'),page.locator('[data-prof-copy-text]').input_value())
            with page.expect_download() as download:
                page.get_by_role('button',name='CSV',exact=True).click()
            content=open(download.value.path(),encoding='utf-8-sig').read()
            self.assertIn('N491UP',content);self.assertIn('N492UP',content);self.assertIn('TOP OFF RETURNED',content)
            for width,height in ((1440,900),(1920,1080),(390,844),(375,568)):
                page.set_viewport_size({'width':width,'height':height})
                page.evaluate('window.scrollTo(0,0)')
                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'),width+1)
                self.assertTrue(page.locator('.rolldown-event').first.evaluate('(e)=>e.getBoundingClientRect().width<=innerWidth'))
                page.screenshot(path=str(F.evidence/f'rolldown-{width}.png'),full_page=False)
            page.locator('[name=truck_id]').select_option(str(truck_id))
            with page.expect_navigation():page.get_by_role('button',name='View',exact=True).click()
            expect(page.locator('[data-truck-summary]')).to_have_count(1)
            self.assertNotIn('Fleet totals',page.locator('body').inner_text())
            self.assertEqual(errors,[])
            with F.app.app_context():
                self.assertEqual(NeoScorpionFuelingEvent.query.count(),3)
                self.assertEqual(NeoScorpionTopOffEvent.query.count(),2)
                self.assertEqual(NeoScorpionSortTruck.query.one().current_gallons,3000)
        finally:
            if browser:browser.close()
            F.tearDownClass()
