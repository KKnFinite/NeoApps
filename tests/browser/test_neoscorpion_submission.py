"""Fueler physical OFF, saved corrections and explicit review handoff in two engines."""
from datetime import date,datetime,timedelta
import unittest
from unittest.mock import patch
from playwright.sync_api import expect
from app.extensions import db
from app.models import (User,SortDateOperation,SortDateMission,NeoScorpionFuelAssignment,NeoScorpionFuelTruck,
    NeoScorpionSortTruck,NeoScorpionSortFueler,NeoScorpionFuelWorkState,NeoScorpionFuelingEvent)
from app.services.access_control import ensure_default_gateway_and_nodes
from tests.browser import test_mobile_drawer as fixtures

class SubmissionBrowserTest(unittest.TestCase):
    def test_chromium_webkit_preparation_off_handoff_and_correction(self):
        F=fixtures.MobileDrawerBrowserTest;F.setUpClass();F.app.config['LIVE_SCREEN_REFRESH_INTERVAL_MS']=1000
        try:
            with F.app.app_context():
                gateway=ensure_default_gateway_and_nodes();u=User.query.filter_by(username='drawer-admin').one()
                op=SortDateOperation(generated_by_user_id=u.id,gateway_id=gateway.id,gateway_code=gateway.code,sort_date=date.today(),sort_name='night',window_minutes=60)
                db.session.add(op);db.session.flush();oid=op.id
                db.session.add(NeoScorpionSortFueler(sort_date_operation_id=oid,user_id=u.id))
                ids=[]
                for index in range(2):
                    truck=NeoScorpionFuelTruck(gateway_id=gateway.id,truck_number=str(50+index),capacity_gallons=20000)
                    m=SortDateMission(sort_date_operation_id=oid,sort_date=op.sort_date,gateway_code=gateway.code,sort_name='night',mission_type='departure',mission_source='manual',flight_number=f'UPS{801+index}',origin=gateway.code,destination='SDF',assigned_tail_number=f'N49{index+1}UP',planned_datetime_utc=datetime.utcnow()+timedelta(hours=2),timezone='America/Chicago',planned_fuel_load=25000)
                    db.session.add_all([truck,m]);db.session.flush()
                    db.session.add(NeoScorpionSortTruck(sort_date_operation_id=oid,fuel_truck_id=truck.id,status='available',starting_gallons=10000,current_gallons=10000))
                    a=NeoScorpionFuelAssignment(sort_date_operation_id=oid,sort_date_mission_id=m.id,assigned_fueler_user_id=u.id,assigned_truck_id=truck.id,confirmed_tail_number=m.assigned_tail_number)
                    db.session.add(a);db.session.flush();ids.append((a.id,m.id))
                db.session.commit()
            with patch('app.services.neoscorpion.current_existing_operational_sort_operations',side_effect=lambda g,now=None:[db.session.get(SortDateOperation,oid)]):
                for engine,(aid,mid) in zip((F.pw.chromium,F.pw.webkit),ids):
                    browser=engine.launch()
                    try:
                        context=browser.new_context(viewport={'width':1440,'height':900});page=context.new_page();F().login(page)
                        page.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1','seen')")
                        F().ready(page,'/neoscorpion/fueler')
                        card=page.locator(f'[data-fuel-data-card][data-assignment-id="{aid}"]')
                        if card.count()==0:card=page.locator('[data-fuel-data-card]').filter(has=page.locator(f'input[name="assignment_id"][value="{aid}"]')).first
                        off=card.locator('.neoscorpion-fueler-off-form button');submit=card.locator('[data-fueler-submit-form] button')
                        expect(off).to_be_visible();expect(off).to_be_disabled();expect(submit).to_be_disabled()
                        card.locator('[name="apu_running"]').select_option('no')
                        for code in ('left','ctr','right'):card.locator(f'[name="remaining_{code}"]').fill('0' if code=='ctr' else '6')
                        card.locator('[data-fuel-planning-form] button[type="submit"]').click()
                        expect(card.locator('[data-fuel-data-status]')).to_contain_text('Saved');expect(off).to_be_disabled()
                        with F.app.app_context():
                            work=NeoScorpionFuelWorkState.query.filter_by(fuel_assignment_id=aid).one();self.assertIsNone(work.manual_on_at_utc);self.assertIsNone(work.on_at_utc)
                        dispatch=context.new_page();F().ready(dispatch,'/neoscorpion/fuel-dispatch')
                        row=dispatch.locator(f'[data-dispatch-mission-id="{mid}"]')
                        expect(row.locator('select[name="assigned_fueler_user_id"]')).to_have_count(1)
                        expect(row.locator('select[name="assigned_truck_id"]')).to_have_count(1)
                        page.bring_to_front();card.locator('[data-truck-on-form] button').click()
                        expect(card.locator('.neoscorpion-truck-on-row')).to_contain_text('CONFIRMED');expect(off).to_be_enabled()
                        for _ in range(2):
                            card.locator('[data-fuel-planning-form] button[type="submit"]').click();expect(off).to_be_visible();expect(off).to_be_enabled()
                        off.click();expect(card.locator('.neoscorpion-truck-on-row')).to_contain_text('TRUCK OFF');expect(submit).to_be_disabled()
                        for code in ('left','ctr','right'):card.locator(f'[name="actual_{code}"]').fill('0' if code=='ctr' else '15')
                        card.locator('[name="transfer_fuel_gallons"]').fill('3000')
                        card.locator('[data-fuel-planning-form] button[type="submit"]').click();expect(submit).to_be_enabled()
                        submit.click();expect(card.locator('[data-fueler-submission-status]')).to_have_text('READY FOR DISPATCHER COMPLETE')
                        card.locator('[name="actual_left"]').fill('16');card.locator('[data-fuel-planning-form] button[type="submit"]').click();expect(submit).to_be_enabled()
                        submit.click();expect(card.locator('[data-fueler-submission-status]')).to_have_text('READY FOR DISPATCHER COMPLETE')
                        for width in (1440,390):
                            page.set_viewport_size({'width':width,'height':844});expect(submit).to_be_visible();self.assertFalse(page.evaluate('document.documentElement.scrollWidth>innerWidth+1'))
                        dispatch.bring_to_front();expect(row.locator('select[name="assigned_fueler_user_id"]')).to_have_count(0,timeout=20000)
                        expect(row.locator('button[formaction$="/fuel-dispatch/complete"]')).to_be_visible(timeout=20000)
                        dispatch.locator(f'[data-dispatch-mission-id="{mid}"] [data-neoscorpion-dispatch-details]').click()
                        expect(dispatch.locator(f'#neoscorpion-dispatch-detail-{mid}-1 [data-dispatch-fueler-submission]')).to_have_text('READY FOR DISPATCHER COMPLETE')
                        row.locator('button[formaction$="/fuel-dispatch/complete"]').click()
                        expect(row).to_have_class(__import__('re').compile('is-complete'),timeout=20000)
                        with F.app.app_context():self.assertEqual(NeoScorpionFuelingEvent.query.filter_by(fuel_assignment_id=aid).count(),1)
                    finally:browser.close()
        finally:F.tearDownClass()
