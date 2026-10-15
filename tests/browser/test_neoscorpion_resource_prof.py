"""Mixed-version PROF report in real Chromium/WebKit at desktop and phone widths."""
import os
import unittest
from datetime import date,datetime,timedelta
from app.extensions import db
from app.models import (User,SortDateOperation,SortDateMission,NeoScorpionFuelTruck,
    NeoScorpionFuelAssignment,NeoScorpionFuelWorkState,NeoScorpionFuelingEvent)
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.neoscorpion_resource_timing import resource_policy,aircraft_evidence
from playwright.sync_api import expect
from tests.browser import test_mobile_drawer as fixtures


class ResourceProfBrowserTest(unittest.TestCase):
    def test_mixed_versions_exact_math_copy_csv_and_responsive_report(self):
        F=fixtures.MobileDrawerBrowserTest;F.setUpClass();browser=None
        try:
            with F.app.app_context():
                gateway=ensure_default_gateway_and_nodes();actor=User.query.filter_by(username='drawer-admin').one()
                other=User.query.filter_by(username='drawer-watcher').one()
                T=datetime(2026,10,15)
                def at(minutes):return T+timedelta(minutes=minutes)
                operation=SortDateOperation(gateway_id=gateway.id,gateway_code=gateway.code,sort_date=date(2026,10,14),sort_name='night',
                    generated_by_user_id=actor.id,window_minutes=60)
                truck=NeoScorpionFuelTruck(gateway_id=gateway.id,truck_number='422808',capacity_gallons=10000)
                other_truck=NeoScorpionFuelTruck(gateway_id=gateway.id,truck_number='OTHER',capacity_gallons=10000)
                db.session.add_all([operation,truck,other_truck]);db.session.flush()
                def make_work(flight,tail,truck_id,fueler_id,on,off):
                    mission=SortDateMission(sort_date_operation_id=operation.id,sort_date=operation.sort_date,gateway_code=gateway.code,
                        sort_name='night',mission_type='departure',mission_source='manual',flight_number=flight,origin=gateway.code,destination='SDF',assigned_tail_number=tail)
                    db.session.add(mission);db.session.flush()
                    assignment=NeoScorpionFuelAssignment(sort_date_operation_id=operation.id,sort_date_mission_id=mission.id)
                    db.session.add(assignment);db.session.flush()
                    work=NeoScorpionFuelWorkState(fuel_assignment_id=assignment.id,tail_number=tail,visit_number=1,physical_sequence_number=1,
                        physical_cycle_number=1,physical_truck_id=truck_id,physical_fueler_user_id=fueler_id,
                        manual_on_at_utc=on,off_at_utc=off,physical_finalized_at_utc=off)
                    db.session.add(work);db.session.flush()
                    return work,assignment
                old,old_assignment=make_work('UPS0900','N490UP',truck.id,actor.id,at(-40),at(-20))
                truck_previous,_=make_work('UPS0901','N491UP',truck.id,other.id,at(0),at(10))
                fueler_previous,_=make_work('UPS0902','N492UP',other_truck.id,actor.id,at(0),at(5))
                current,assignment=make_work('UPS0903','N493UP',truck.id,actor.id,at(30),at(40))
                evidence=resource_policy(work_id=current.id,truck_id=truck.id,fueler_id=actor.id,on=at(30),
                    truck_assigned=at(5),fueler_assigned=at(5),preparation=dict(at=at(16).isoformat(),fueler_id=actor.id,assigned_at=at(5).isoformat()),
                    aircraft=aircraft_evidence(type('Arrival',(),{'actual_block_in_datetime_utc':at(0)})(),None,15),parking='B4',parking_at=at(0),
                    visits=[dict(id=truck_previous.id,truck_id=truck.id,fueler_id=other.id,on=at(0),off=at(10),fueler_release=at(10),parking='A4',on_source='manual',finalized=True),
                            dict(id=fueler_previous.id,truck_id=other_truck.id,fueler_id=actor.id,on=at(0),off=at(5),fueler_release=at(6),parking='B4',on_source='manual',finalized=True)],
                    travel_per_move=2,window=10,setup=8)
                evidence.update(availability_continuity='verified',availability_evidence=[
                    dict(at=at(0).isoformat(),available=True,scope='nightly',status='available',source='fixture_observation',actor_id=actor.id),
                    dict(at=at(0).isoformat(),available=True,scope='master',source='fixture_observation',actor_id=actor.id)])
                for work,owner,version,on,off,allowance in ((old,old_assignment,3,at(-40),at(-20),'8'),(current,assignment,4,at(30),at(40),'0')):
                    facts=dict(version=version,on_source='manual',pump_rate='150',fueler_id=actor.id,truck_id=truck.id,
                        setup_allowance=allowance,fueler_assigned_at=at(-55 if version==3 else 5).isoformat(),truck_assigned_at=at(-50 if version==3 else 5).isoformat(),
                        setup_minutes='8',advance_window=10,exclusions=[],aircraft_type='A300')
                    if version==4:facts['resource_timing']=evidence
                    db.session.add(NeoScorpionFuelingEvent(sort_date_operation_id=operation.id,fuel_assignment_id=owner.id,fuel_work_state_id=work.id,
                        tail_number=work.tail_number,fuel_truck_id=truck.id,fueler_user_id=actor.id,sequence_number=1,
                        event_type='fuel',cycle_number=1,started_at_utc=on,ended_at_utc=off,transfer_fuel_gallons=3000,performance_snapshot=facts))
                db.session.commit();operation_id,truck_id=operation.id,truck.id
            browser=getattr(F.pw,os.environ.get('NEO_PROF_BROWSER','chromium')).launch()
            page=browser.new_page(viewport={'width':1440,'height':900});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            F().login(page);F().ready(page,f'/neoscorpion/reports/prof?operation_id={operation_id}')
            expect(page.locator('.neoscorpion-prof-summary')).to_contain_text('TRUCK PROF 84')
            expect(page.locator('.neoscorpion-prof-summary')).to_contain_text('FUELER PROF 160')
            expect(page.locator('.neoscorpion-prof-job').filter(has_text='UPS0903')).to_contain_text('Truck 73 PROF')
            expect(page.locator('.neoscorpion-prof-job').filter(has_text='UPS0900')).to_contain_text('Truck 100 PROF')
            expect(page.locator('.neoscorpion-prof-report')).to_contain_text('v3 · physical ON→OFF')
            expect(page.locator('.neoscorpion-prof-report')).to_contain_text('v4 · resource-aware')
            page.evaluate("Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>window.__copied=text}})")
            page.get_by_role('button',name='COPY REPORT').click();expect(page.locator('[data-prof-copy-status]')).to_have_text('Copied')
            self.assertEqual(page.evaluate('window.__copied'),page.locator('[data-prof-copy-text]').input_value())
            self.assertIn('Truck adjusted actual min: 30',page.evaluate('window.__copied'))
            with page.expect_download() as download:page.get_by_role('button',name='CSV',exact=True).click()
            with open(download.value.path(),encoding='utf-8-sig') as stream:csv=stream.read()
            self.assertIn('Policy version',csv);self.assertIn('v4',csv);self.assertIn('v3',csv)
            for width,height in ((1440,900),(1920,1080),(390,844),(375,568)):
                page.set_viewport_size({'width':width,'height':height});page.evaluate('window.scrollTo(0,0)')
                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'),width+1)
                self.assertTrue(page.locator('.neoscorpion-prof-job').first.evaluate('(e)=>e.getBoundingClientRect().width<=innerWidth'))
                page.screenshot(path=str(F.evidence/f'resource-prof-{width}.png'),full_page=False)
            page.locator('[name=truck_id]').select_option(str(truck_id))
            with page.expect_navigation():page.get_by_role('button',name='View',exact=True).click()
            expect(page.locator('.neoscorpion-prof-summary')).to_contain_text('TRUCK PROF 84')
            self.assertEqual(errors,[])
            with F.app.app_context():self.assertEqual(NeoScorpionFuelingEvent.query.count(),2)
        finally:
            if browser:browser.close()
            F.tearDownClass()
