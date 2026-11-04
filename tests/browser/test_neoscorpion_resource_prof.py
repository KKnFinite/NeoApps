"""Canonical v4 report and explicitly unverified v3 imports on both browsers."""
import copy
import unittest
from datetime import date, datetime, timedelta

from app.extensions import db
from app.models import (User, SortDateOperation, SortDateMission, NeoScorpionFuelTruck,
    NeoScorpionFuelAssignment, NeoScorpionFuelWorkState, NeoScorpionFuelingEvent)
from app.services.access_control import ensure_default_gateway_and_nodes
from tests.neoscorpion_evidence_fixture import FuelCycle, transfer_readings
from playwright.sync_api import expect
from tests.browser import test_mobile_drawer as fixtures


class ResourceProfBrowserTest(unittest.TestCase):
    def test_mixed_versions_exact_math_copy_csv_and_responsive_report(self):
        F = fixtures.MobileDrawerBrowserTest
        F.setUpClass()
        try:
            with F.app.app_context():
                gateway = ensure_default_gateway_and_nodes()
                actor = User.query.filter_by(username='drawer-admin').one()
                operation = SortDateOperation(gateway_id=gateway.id, gateway_code=gateway.code,
                    sort_date=date(2026, 10, 14), sort_name='night', generated_by_user_id=actor.id, window_minutes=360)
                truck = NeoScorpionFuelTruck(gateway_id=gateway.id, truck_number='422808', capacity_gallons=10000)
                db.session.add_all([operation, truck]); db.session.commit()
                for index, (tail, minutes) in enumerate((('N491UP', 15), ('N492UP', 30))):
                    start = datetime(2026, 10, 15, 2, 0) + timedelta(minutes=index*30)
                    cycle = FuelCycle.create(gateway, operation, actor, actor, truck,
                        flight=f'UPS090{index}', tail=tail)
                    cycle.record_arrival(start-timedelta(minutes=5))
                    cycle.assign(start-timedelta(minutes=1), current_gallons=5000)
                    cycle.park(start-timedelta(minutes=1), 'A', f'A0{index+1}')
                    cycle.save(start-timedelta(minutes=1), apu_running='no', remaining_left='6', remaining_ctr='0', remaining_right='6')
                    cycle.finish(start, start+timedelta(minutes=minutes), **transfer_readings(gallons='1500'))
                # A controlled legacy import has raw times and an old policy but
                # lacks participation evidence. It stays visible and excluded.
                mission = SortDateMission(sort_date_operation_id=operation.id, sort_date=operation.sort_date,
                    gateway_code=gateway.code, sort_name='night', mission_type='departure', mission_source='manual',
                    flight_number='UPS-LEGACY', origin=gateway.code, destination='SDF', assigned_tail_number='N490UP')
                db.session.add(mission); db.session.flush()
                owner = NeoScorpionFuelAssignment(sort_date_operation_id=operation.id, sort_date_mission_id=mission.id)
                db.session.add(owner); db.session.flush()
                work = NeoScorpionFuelWorkState(fuel_assignment_id=owner.id, tail_number='N490UP')
                db.session.add(work); db.session.flush()
                db.session.add(NeoScorpionFuelingEvent(sort_date_operation_id=operation.id, fuel_assignment_id=owner.id,
                    fuel_work_state_id=work.id, tail_number='N490UP', fuel_truck_id=truck.id, fueler_user_id=actor.id,
                    sequence_number=1, cycle_number=1, event_type='fuel', transfer_fuel_gallons=3000,
                    started_at_utc=datetime(2026,10,15,0), ended_at_utc=datetime(2026,10,15,0,20),
                    performance_snapshot=dict(version=3, on_source='manual', pump_rate='150', setup_allowance='8',
                        fueler_id=actor.id, truck_id=truck.id, aircraft_type='B757', exclusions=[])))
                db.session.commit()
                operation_id, truck_id = operation.id, truck.id
                snapshots = [copy.deepcopy(e.performance_snapshot) for e in NeoScorpionFuelingEvent.query.order_by(NeoScorpionFuelingEvent.id)]
            for engine in ('chromium', 'webkit'):
                with self.subTest(engine=engine):
                    browser = getattr(F.pw, engine).launch()
                    try:
                        page = browser.new_page(viewport={'width':1440,'height':900})
                        errors=[]; page.on('pageerror',lambda e:errors.append(str(e)))
                        F().login(page); F().ready(page,f'/neoscorpion/reports/prof?operation_id={operation_id}')
                        expect(page.get_by_role('banner').filter(has_text='REPORTS').first).to_be_visible()
                        summary=page.locator('.neoscorpion-prof-summary')
                        expect(summary).to_contain_text('FUELER PROF 67')
                        expect(summary).to_contain_text('TRUCK PROF 16')
                        expect(page.locator('.neoscorpion-prof-job').filter(has_text='UPS0901')).to_contain_text('Truck 16 PROF')
                        expect(page.locator('.neoscorpion-prof-job').filter(has_text='UPS-LEGACY')).to_contain_text('PARTICIPATION UNVERIFIED')
                        page.evaluate("Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>window.__copied=text}})")
                        page.get_by_role('button',name='COPY REPORT').click()
                        expect(page.locator('[data-prof-copy-status]')).to_have_text('Copied')
                        self.assertEqual(page.evaluate('window.__copied'),page.locator('[data-prof-copy-text]').input_value())
                        self.assertIn('PARTICIPATION UNVERIFIED',page.evaluate('window.__copied'))
                        with page.expect_download() as download: page.get_by_role('button',name='CSV',exact=True).click()
                        with open(download.value.path(),encoding='utf-8-sig') as stream: csv=stream.read()
                        for value in ('Policy version','v4','v3','PARTICIPATION UNVERIFIED'): self.assertIn(value,csv)
                        for width,height in ((1440,900),(1920,1080),(390,844),(375,568)):
                            page.set_viewport_size({'width':width,'height':height}); page.evaluate('window.scrollTo(0,0)')
                            expect(page.get_by_role('banner').filter(has_text='REPORTS').first).to_be_visible()
                            self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'),width+1)
                            self.assertTrue(page.locator('.neoscorpion-prof-job').first.evaluate('(e)=>e.getBoundingClientRect().width<=innerWidth'))
                            page.screenshot(path=str(F.evidence/f'w3-prof-{engine}-{width}.png'),full_page=False)
                        page.locator('[name=truck_id]').select_option(str(truck_id))
                        with page.expect_navigation(): page.get_by_role('button',name='View',exact=True).click()
                        expect(summary).to_contain_text('TRUCK PROF 16')
                        self.assertEqual(errors,[])
                    finally:
                        browser.close()
            with F.app.app_context():
                self.assertEqual([e.performance_snapshot for e in NeoScorpionFuelingEvent.query.order_by(NeoScorpionFuelingEvent.id)],snapshots)
        finally:
            F.tearDownClass()
