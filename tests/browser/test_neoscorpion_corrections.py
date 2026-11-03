"""Real Dispatcher correction/reopen controls and Fueler handoff on both engines."""
import re
import unittest
from datetime import date, datetime, timedelta
from unittest.mock import patch
from urllib.parse import urlsplit

from playwright.sync_api import expect
from app.extensions import db
from app.models import (Gateway, User, PortalAppAccess, SortDateOperation, SortDateMission,
    NeoScorpionFuelAssignment, NeoScorpionFuelTruck, NeoScorpionSortTruck,
    NeoScorpionSortFueler, NeoScorpionFuelWorkState, NeoScorpionFuelingEvent,
    NeoScorpionFuelAuditEntry)
from app.services import neoscorpion as fuel
from app.services.access_control import ensure_default_gateway_and_nodes, backfill_default_gateway_node_roles
from app.services.password_policy import set_user_password
from tests.browser import test_mobile_drawer as fixtures
from tests.browser.neoscorpion_helpers import wait_for_fuel_saved


class FuelCorrectionBrowserTest(unittest.TestCase):
    def test_chromium_webkit_correction_reopen_and_immutable_completion(self):
        F = fixtures.MobileDrawerBrowserTest
        F.setUpClass()
        F.app.config['LIVE_SCREEN_REFRESH_INTERVAL_MS'] = 5000
        try:
            with F.app.app_context():
                gateway = ensure_default_gateway_and_nodes()
                dispatcher = User.query.filter_by(username='drawer-admin').one()
                operator = User(username='correction-browser-fueler', email='correction-browser-fueler@example.test',
                    role='watcher', email_verified_at=datetime.now())
                set_user_password(operator, 'BrowserFixture123!')
                db.session.add(operator); db.session.flush()
                backfill_default_gateway_node_roles(operator, role='operator')
                access = PortalAppAccess.query.filter_by(user_id=operator.id, app_code='neogateway').one()
                access.status='approved'; access.role='operator'; access.is_active=True
                operation = SortDateOperation(generated_by_user_id=dispatcher.id, gateway_id=gateway.id,
                    gateway_code=gateway.code, sort_date=date.today(), sort_name='night', window_minutes=60)
                db.session.add(operation); db.session.flush(); oid=operation.id
                db.session.add(NeoScorpionSortFueler(sort_date_operation_id=oid, user_id=operator.id))
                ids=[]
                for index in range(2):
                    truck=NeoScorpionFuelTruck(gateway_id=gateway.id,truck_number=f'B-COR-{index}',capacity_gallons=2000)
                    mission=SortDateMission(sort_date_operation_id=oid,sort_date=operation.sort_date,
                        gateway_code=gateway.code,sort_name='night',mission_type='departure',mission_source='manual',
                        flight_number=f'UPS97{index}',origin=gateway.code,destination='MSP',assigned_tail_number=f'N49{index+1}UP',
                        planned_datetime_utc=datetime.utcnow()+timedelta(hours=2),timezone='America/Chicago',planned_fuel_load=30000)
                    db.session.add_all([truck,mission]);db.session.flush()
                    db.session.add(NeoScorpionSortTruck(sort_date_operation_id=oid,fuel_truck_id=truck.id,
                        status='available',starting_gallons=1000,current_gallons=1000))
                    assignment=NeoScorpionFuelAssignment(sort_date_operation_id=oid,sort_date_mission_id=mission.id,
                        assigned_fueler_user_id=operator.id,confirmed_tail_number=mission.assigned_tail_number)
                    db.session.add(assignment);db.session.flush();ids.append((assignment.id,mission.id,truck.id))
                db.session.commit()
                gid,uid,did=gateway.id,operator.id,dispatcher.id
            with patch('app.services.neoscorpion.current_existing_operational_sort_operations',
                       side_effect=lambda g,now=None:[db.session.get(SortDateOperation,oid)]):
                with F.app.app_context():
                    gateway=db.session.get(Gateway,gid)
                    for aid,mid,tid in ids:
                        fuel.save_dispatch_assignment(gateway,dict(mission_id=mid,
                            expected_assigned_fueler_user_id=uid,expected_assigned_truck_id='',
                            assigned_fueler_user_id=uid,assigned_truck_id=tid))
                    db.session.commit()
                for engine,(aid,mid,tid) in zip((F.pw.chromium,F.pw.webkit),ids):
                    browser=engine.launch();errors=[]
                    try:
                        mobile=browser.new_context(viewport={'width':390,'height':844},service_workers='block')
                        desktop=browser.new_context(viewport={'width':1440,'height':900},service_workers='block')
                        page=mobile.new_page();dispatch=desktop.new_page()
                        for p in (page,dispatch):p.on('pageerror',lambda e:errors.append(str(e)))
                        F().login(page,'correction-browser-fueler');F().login(dispatch)
                        dispatch.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1','seen')")
                        F().ready(page,'/neoscorpion/fueler');F().ready(dispatch,'/neoscorpion/fuel-dispatch')
                        card=page.locator('[data-fuel-data-card]').filter(has=page.locator(f'input[name="assignment_id"][value="{aid}"]')).first
                        row=dispatch.locator(f'[data-dispatch-mission-id="{mid}"]')
                        card.locator('[data-truck-on-form] button').click()
                        card.locator('[name="apu_running"]').select_option('no')
                        for code in ('left','ctr','right'):
                            card.locator(f'[name="remaining_{code}"]').fill('10')
                            card.locator(f'[name="actual_{code}"]').fill('11' if code=='left' else '10')
                        card.locator('[name="transfer_fuel_gallons"]').fill('150')
                        wait_for_fuel_saved(card)
                        with page.expect_response(lambda r:r.request.method=='POST' and urlsplit(r.url).path.endswith('/off')) as response:
                            card.locator('.neoscorpion-fueler-off-form button').click()
                        self.assertEqual(response.value.status,200)
                        expect(card.locator('.scorpion-fueler-finish')).to_contain_text('TRUCK OFF')
                        with F.app.app_context():
                            assignment=db.session.get(NeoScorpionFuelAssignment,aid)
                            wid=assignment.active_work_state_id
                            old_off=db.session.get(NeoScorpionFuelWorkState,wid).off_at_utc
                            self.assertIsNotNone(old_off)

                        def details(section):
                            toggle=row.locator('[data-neoscorpion-dispatch-details]')
                            if toggle.get_attribute('aria-expanded')!='true':toggle.click()
                            detail=dispatch.locator(f'#neoscorpion-dispatch-detail-{mid}-1')
                            control=detail.locator(f'details[data-console-section="management-{section}"]')
                            if not control.evaluate('el=>el.open'):control.locator(':scope > summary').click()
                            return control

                        dispatch.bring_to_front()
                        correction=details(4)
                        correction.locator('[name="correct_actual_left"]').fill('12')
                        correction.locator('[name="correction_reason"]').fill('Verified gauge correction.')
                        with dispatch.expect_navigation(wait_until='domcontentloaded'):
                            with dispatch.expect_response(lambda r:r.request.method=='POST' and r.url.endswith('/correct-actual')) as response:
                                correction.get_by_role('button',name='APPLY CORRECTION',exact=True).click()
                        self.assertEqual(response.value.status,302)
                        with F.app.app_context():
                            work=db.session.get(NeoScorpionFuelWorkState,wid)
                            self.assertEqual(work.off_at_utc,old_off)
                            audit=NeoScorpionFuelAuditEntry.query.filter_by(fuel_work_state_id=wid,action='correct_actual').one()
                            self.assertEqual((audit.old_value,audit.new_value),('11000','12000'))
                            self.assertEqual(audit.changed_by_user_id,did)
                        reopen=details(5)
                        reopen.locator('[name="reopen_reason"]').fill('Resume physical fueling.')
                        with dispatch.expect_navigation(wait_until='domcontentloaded'):
                            with dispatch.expect_response(lambda r:r.request.method=='POST' and r.url.endswith('/reopen-off')) as response:
                                reopen.get_by_role('button',name='REOPEN OFF',exact=True).click()
                        self.assertEqual(response.value.status,302)
                        with F.app.app_context():
                            work=db.session.get(NeoScorpionFuelWorkState,wid)
                            self.assertIsNone(work.off_at_utc)
                            self.assertEqual(NeoScorpionFuelWorkState.query.filter_by(fuel_assignment_id=aid).count(),1)
                            self.assertIn(old_off.isoformat(),NeoScorpionFuelAuditEntry.query.filter_by(
                                fuel_work_state_id=wid,action='reopen_off').one().old_value)
                        page.bring_to_front()
                        expect(card.locator('[name="actual_left"]')).to_have_value(re.compile(r'12(?:\.0+)?'),timeout=15000)
                        card.locator('[name="actual_left"]').fill('13');wait_for_fuel_saved(card)
                        off=card.locator('.neoscorpion-fueler-off-form button');expect(off).to_be_enabled()
                        with page.expect_response(lambda r:r.request.method=='POST' and urlsplit(r.url).path.endswith('/off')) as response:
                            off.click()
                        self.assertEqual(response.value.status,200)
                        submit=card.locator('[data-fueler-submit-form] button');expect(submit).to_be_enabled();submit.click()
                        expect(card.locator('[data-fueler-submission-status]')).to_contain_text('READY FOR DISPATCHER COMPLETE')
                        dispatch.bring_to_front()
                        complete=row.locator('button[formaction$="/fuel-dispatch/complete"]')
                        expect(complete).to_be_visible(timeout=15000);complete.click()
                        expect(row).to_have_class(re.compile('is-complete'),timeout=15000)
                        with F.app.app_context():
                            work=db.session.get(NeoScorpionFuelWorkState,wid)
                            self.assertGreater(work.off_at_utc,old_off)
                            event=NeoScorpionFuelingEvent.query.filter_by(fuel_assignment_id=aid).one()
                            self.assertEqual((event.fuel_work_state_id,event.transfer_fuel_gallons),(wid,150))
                            self.assertEqual(NeoScorpionSortTruck.query.filter_by(
                                sort_date_operation_id=oid,fuel_truck_id=tid).one().current_gallons,850)
                        self.assertEqual(dispatch.locator(f'#neoscorpion-dispatch-detail-{mid}-1 [name="correct_actual_left"]').count(),0)
                        self.assertEqual(errors,[])
                    finally:browser.close()
        finally:F.tearDownClass()
