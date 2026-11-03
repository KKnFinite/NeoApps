"""Actual Fueler measurements -> authorized Dispatcher no-transfer FOB, both engines."""
import unittest
from datetime import date,datetime,timedelta
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import expect
from app.extensions import db
from app.models import (User,PortalAppAccess,SortDateOperation,SortDateMission,NeoScorpionFuelAssignment,
    NeoScorpionSortFueler,NeoScorpionFuelWorkState,NeoScorpionFuelingEvent,NeoScorpionTruckInventoryAudit)
from app.services.access_control import ensure_default_gateway_and_nodes,backfill_default_gateway_node_roles
from app.services.password_policy import set_user_password
from tests.browser import test_mobile_drawer as fixtures
from tests.browser.neoscorpion_helpers import wait_for_fuel_saved


class FobEvidenceBrowserTest(unittest.TestCase):
    def test_chromium_webkit_measured_truckless_fob_has_no_physical_milestones(self):
        F=fixtures.MobileDrawerBrowserTest;F.setUpClass()
        evidence=Path('instance/r03h/browser');evidence.mkdir(parents=True,exist_ok=True)
        try:
            F.app.config['LIVE_SCREEN_REFRESH_INTERVAL_MS']=5000
            with F.app.app_context():
                gateway=ensure_default_gateway_and_nodes();admin=User.query.filter_by(username='drawer-admin').one()
                user=User(username='r03h-fueler',email='r03h-fueler@example.test',role='watcher',email_verified_at=datetime.now())
                set_user_password(user,'BrowserFixture123!');db.session.add(user);db.session.flush()
                backfill_default_gateway_node_roles(user,role='operator')
                access=PortalAppAccess.query.filter_by(user_id=user.id,app_code='neogateway').one()
                access.status='approved';access.role='operator';access.is_active=True;uid=user.id
                operation=SortDateOperation(generated_by_user_id=admin.id,gateway_id=gateway.id,gateway_code=gateway.code,
                    sort_date=date.today(),sort_name='night',window_minutes=60)
                db.session.add(operation);db.session.flush();oid=operation.id
                db.session.add(NeoScorpionSortFueler(sort_date_operation_id=oid,user_id=uid));cases=[]
                for index in range(2):
                    mission=SortDateMission(sort_date_operation_id=oid,sort_date=operation.sort_date,gateway_code=gateway.code,
                        sort_name='night',mission_type='departure',mission_source='manual',flight_number=f'UPS-H{index}',
                        origin=gateway.code,destination='MSP',assigned_tail_number=f'N{491+index}UP',
                        planned_datetime_utc=datetime.utcnow()+timedelta(hours=2),timezone='America/Chicago',planned_fuel_load=20000)
                    db.session.add(mission);db.session.flush()
                    assignment=NeoScorpionFuelAssignment(sort_date_operation_id=oid,sort_date_mission_id=mission.id,
                        confirmed_tail_number=mission.assigned_tail_number)
                    db.session.add(assignment);db.session.flush();cases.append((mission.id,assignment.id))
                db.session.commit()
            with patch('app.services.neoscorpion.current_existing_operational_sort_operations',side_effect=lambda g,now=None:[db.session.get(SortDateOperation,oid)]):
                for engine,(mid,aid) in zip((F.pw.chromium,F.pw.webkit),cases):
                    browser=engine.launch();errors=[]
                    try:
                        desktop=browser.new_context(viewport={'width':1440,'height':900},service_workers='block')
                        mobile=browser.new_context(viewport={'width':390,'height':844},service_workers='block')
                        dispatch=desktop.new_page();page=mobile.new_page()
                        for p in (dispatch,page):p.on('pageerror',lambda err:errors.append(str(err)))
                        F().login(dispatch);F().login(page,'r03h-fueler')
                        dispatch.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1','seen')")
                        F().ready(dispatch,'/neoscorpion/fuel-dispatch')
                        row=dispatch.locator(f'[data-dispatch-mission-id="{mid}"]')
                        row.locator('[name="assigned_fueler_user_id"]').select_option(str(uid))
                        with dispatch.expect_response(lambda r:r.request.method=='POST' and '/fuel-dispatch/assignment' in r.url) as assigned:
                            row.locator('[data-dispatch-assignment-submit]').click()
                        self.assertEqual(assigned.value.status,200)
                        F().ready(page,'/neoscorpion/fueler')
                        card=page.locator(f'[data-fuel-data-card][data-fuel-assignment-id="{aid}"]').first
                        card.locator('[name="apu_running"]').select_option('no')
                        for code in ('left','ctr','right'):
                            card.locator(f'[name="remaining_{code}"]').fill('0' if code=='ctr' else '10')
                            card.locator(f'[name="actual_{code}"]').fill('0' if code=='ctr' else '10')
                        card.locator('[name="actual_right"]').press('Tab');wait_for_fuel_saved(card)
                        with F.app.app_context():
                            work=db.session.get(NeoScorpionFuelWorkState,db.session.get(NeoScorpionFuelAssignment,aid).active_work_state_id)
                            wid=work.id
                            self.assertEqual(sum(t.actual_lbs for t in work.tank_states),20000)
                            self.assertEqual((work.on_at_utc,work.manual_on_at_utc,work.confirmed_on_at_utc,work.off_at_utc),(None,)*4)
                        page.screenshot(path=str(evidence/f'{engine.name}-fueler-measured-no-on.png'),full_page=True)
                        # The real Dispatcher route owns the explicit FOB decision.
                        F().ready(dispatch,'/neoscorpion/fuel-dispatch')
                        row=dispatch.locator(f'[data-dispatch-mission-id="{mid}"]')
                        button=row.get_by_role('button',name='FOB',exact=True);expect(button).to_be_enabled()
                        with dispatch.expect_response(lambda r:r.request.method=='POST' and '/fuel-dispatch/fuel-on-board' in r.url) as verified:
                            button.click()
                        self.assertIn(verified.value.status,(200,302))
                        expect(dispatch.locator(f'[data-dispatch-mission-id="{mid}"]')).to_contain_text('FOB')
                        dispatch.screenshot(path=str(evidence/f'{engine.name}-dispatcher-fob.png'),full_page=True)
                        with F.app.app_context():
                            assignment=db.session.get(NeoScorpionFuelAssignment,aid);work=db.session.get(NeoScorpionFuelWorkState,wid)
                            self.assertIsNotNone(assignment.fuel_on_board_at_utc)
                            self.assertEqual(assignment.fuel_on_board_by_user_id,User.query.filter_by(username='drawer-admin').one().id)
                            self.assertEqual(assignment.active_work_state_id,wid)
                            self.assertEqual((work.on_at_utc,work.manual_on_at_utc,work.confirmed_on_at_utc,work.off_at_utc),(None,)*4)
                            self.assertEqual(work.physical_transfer_gallons,0)
                            self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)
                            self.assertEqual(NeoScorpionTruckInventoryAudit.query.count(),0)
                        self.assertEqual(errors,[])
                    finally:browser.close()
        finally:F.tearDownClass()
