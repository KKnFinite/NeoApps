"""R03J actual controls, remote OFF, dirty live updates and completion boundary."""
import json
import re
import unittest
from datetime import date,datetime,timedelta,timezone
from pathlib import Path
from urllib.parse import urlsplit
from unittest.mock import patch
from zoneinfo import ZoneInfo
from playwright.sync_api import expect
from app.extensions import db
from app.models import (User,PortalAppAccess,SortDateOperation,SortDateMission,NeoScorpionFuelAssignment,
    NeoScorpionFuelTruck,NeoScorpionSortTruck,NeoScorpionSortFueler,NeoScorpionFuelWorkState,NeoScorpionFuelingEvent)
from app.services.access_control import ensure_default_gateway_and_nodes,backfill_default_gateway_node_roles
from app.services.password_policy import set_user_password
from tests.browser import test_mobile_drawer as fixtures
from tests.browser.neoscorpion_helpers import wait_for_fuel_saved


class OffPresentationBrowserTest(unittest.TestCase):
    def test_chromium_webkit_off_eligibility_dirty_refresh_and_completed_history(self):
        F=fixtures.MobileDrawerBrowserTest;F.setUpClass();evidence=Path('instance/r03j/browser');evidence.mkdir(parents=True,exist_ok=True)
        try:
            F.app.config['LIVE_SCREEN_REFRESH_INTERVAL_MS']=5000
            with F.app.app_context():
                gateway=ensure_default_gateway_and_nodes();admin=User.query.filter_by(username='drawer-admin').one();did=admin.id
                operation=SortDateOperation(generated_by_user_id=did,gateway_id=gateway.id,gateway_code=gateway.code,sort_date=date.today(),sort_name='night',window_minutes=60)
                db.session.add(operation);db.session.flush();oid=operation.id;cases=[]
                for index in range(2):
                    name=f'r03j-fueler-{index}';user=User(username=name,email=name+'@example.test',role='watcher',email_verified_at=datetime.now())
                    set_user_password(user,'BrowserFixture123!');db.session.add(user);db.session.flush();backfill_default_gateway_node_roles(user,role='operator')
                    access=PortalAppAccess.query.filter_by(user_id=user.id,app_code='neogateway').one();access.status='approved';access.is_active=True;access.role='operator'
                    db.session.add(NeoScorpionSortFueler(sort_date_operation_id=oid,user_id=user.id))
                    truck=NeoScorpionFuelTruck(gateway_id=gateway.id,truck_number=f'J-{index}',capacity_gallons=20000)
                    db.session.add(truck);db.session.flush();db.session.add(NeoScorpionSortTruck(sort_date_operation_id=oid,fuel_truck_id=truck.id,status='available',starting_gallons=10000,current_gallons=10000))
                    missions=[]
                    for number in range(2):
                        mission=SortDateMission(sort_date_operation_id=oid,sort_date=operation.sort_date,gateway_code=gateway.code,sort_name='night',mission_type='departure',mission_source='manual',
                            flight_number=f'UPS-J{index}{number}',origin=gateway.code,destination='MSP',assigned_tail_number=f'N{491+index*2+number}UP',
                            planned_datetime_utc=datetime.utcnow()+timedelta(hours=2+number),timezone='America/Chicago',planned_fuel_load=32000 if number==0 else 30000)
                        db.session.add(mission);db.session.flush();assignment=NeoScorpionFuelAssignment(sort_date_operation_id=oid,sort_date_mission_id=mission.id,confirmed_tail_number=mission.assigned_tail_number)
                        db.session.add(assignment);db.session.flush();missions.append((mission.id,assignment.id))
                    cases.append((name,user.id,truck.id,missions))
                db.session.commit()
            with patch('app.services.neoscorpion.current_existing_operational_sort_operations',side_effect=lambda g,now=None:[db.session.get(SortDateOperation,oid)]):
                for engine,(name,uid,tid,missions) in zip((F.pw.chromium,F.pw.webkit),cases):
                    browser=engine.launch();errors=[]
                    try:
                        desktop=browser.new_context(viewport={'width':1440,'height':900},service_workers='block');mobile=browser.new_context(viewport={'width':390,'height':844},service_workers='block')
                        dispatch=desktop.new_page();page=mobile.new_page()
                        for p in (dispatch,page):p.on('pageerror',lambda err:errors.append(str(err)))
                        F().login(dispatch);F().login(page,name);dispatch.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1','seen')")
                        F().ready(dispatch,'/neoscorpion/fuel-dispatch')
                        for number,(mid,aid) in enumerate(missions):
                            row=dispatch.locator(f'[data-dispatch-mission-id="{mid}"]');row.locator('[name="assigned_fueler_user_id"]').select_option(str(uid))
                            if number==0:row.locator('[name="assigned_truck_id"]').select_option(str(tid))
                            with dispatch.expect_response(lambda r:r.request.method=='POST' and '/fuel-dispatch/assignment' in r.url) as saved:row.locator('[data-dispatch-assignment-submit]').click()
                            self.assertEqual(saved.value.status,200)
                        F().ready(page,'/neoscorpion/fueler');mid,aid=missions[0];fob_id=missions[1][1]
                        card=page.locator(f'[data-fuel-data-card][data-fuel-assignment-id="{aid}"]').first
                        fob=page.locator(f'[data-fuel-data-card][data-fuel-assignment-id="{fob_id}"]').first
                        expect(fob.locator('.neoscorpion-fueler-off-form button')).to_be_disabled()
                        expect(fob.locator('.neoscorpion-fueler-off-form button')).to_have_attribute('aria-disabled','true')
                        expect(fob.locator('.neoscorpion-fueler-off-form button')).to_have_attribute('title',re.compile('No physical truck.*Dispatcher Fuel On Board'))
                        expect(card.locator('.neoscorpion-fueler-off-form button')).to_be_disabled()
                        expect(card.locator('.neoscorpion-fueler-off-form button')).to_have_attribute('title',re.compile('Confirm physical TRUCK ON'))
                        card.locator('[name="apu_running"]').select_option('no')
                        for code in ('left','ctr','right'):
                            card.locator(f'[name="remaining_{code}"]').fill('10');card.locator(f'[name="actual_{code}"]').fill('11' if code=='left' else '10')
                        card.locator('[name="actual_right"]').press('Tab');wait_for_fuel_saved(card)
                        expect(card.locator('.neoscorpion-fueler-off-form button')).to_be_disabled()
                        card.locator('[data-truck-on-form] button').click();expect(card.locator('.neoscorpion-fueler-off-form button')).to_be_enabled()
                        wid=int(card.get_attribute('data-physical-work-id'))
                        page.screenshot(path=str(evidence/f'{engine.name}-eligible-off.png'),full_page=True)
                        # Force one definite failed autosave: the draft stays only
                        # in this Fueler's DOM, never in another user's request.
                        data_path=f'/neoscorpion/fueler/assignments/{aid}'
                        def unavailable(route):
                            if route.request.method=='POST' and urlsplit(route.request.url).path==data_path:route.fulfill(status=503,content_type='application/json',body=json.dumps(dict(ok=False,error='Synthetic autosave unavailable; draft retained')))
                            else:route.continue_()
                        page.route('**/neoscorpion/fueler/assignments/**',unavailable)
                        with page.expect_response(lambda r:r.request.method=='POST' and urlsplit(r.url).path==data_path) as failed:
                            card.locator('[name="actual_left"]').fill('12');card.locator('[name="actual_left"]').press('Tab')
                        self.assertEqual(failed.value.status,503)
                        expect(card.locator('[data-fuel-data-status]').first).to_contain_text('Synthetic autosave unavailable')
                        row=dispatch.locator(f'[data-dispatch-mission-id="{mid}"]');row.locator('.neoscorpion-fuel-data-cell [data-fuel-data-open]').click()
                        dialog=dispatch.locator('[data-fuel-data-dialog]');expect(dialog).to_be_visible()
                        preview=dialog.locator('[data-fuel-data-card]');expect(preview.locator('.neoscorpion-fueler-off-form button')).to_be_enabled()
                        revision_url=page.locator('[data-fuel-assignments-live]').get_attribute('data-revision-url');revision_path=urlsplit(revision_url).path
                        before_revision=int(page.locator('[data-fuel-assignments-live]').get_attribute('data-revision'))
                        with page.expect_response(lambda r:r.request.method=='GET' and urlsplit(r.url).path==revision_path,timeout=15000) as polled:
                            preview.locator('.neoscorpion-fueler-off-form button').click()
                        expect(preview.locator('.scorpion-fueler-finish')).to_contain_text('CONFIRMED')
                        observed=polled.value
                        for _ in range(3):
                            if observed.json()['revision']>before_revision:break
                            observed=page.wait_for_event('response',predicate=lambda r:r.request.method=='GET' and urlsplit(r.url).path==revision_path,timeout=15000)
                        self.assertGreater(observed.json()['revision'],before_revision)
                        expect(card.locator('[name="actual_left"]')).to_have_value('12')
                        with F.app.app_context():
                            work=db.session.get(NeoScorpionFuelWorkState,wid);off=work.off_at_utc
                            self.assertIsNotNone(off);self.assertEqual(work.off_by_user_id,did)
                            self.assertEqual(next(t.actual_lbs for t in work.tank_states if t.tank_code=='left'),11000)
                            self.assertEqual(NeoScorpionFuelingEvent.query.filter_by(fuel_assignment_id=aid).count(),0)
                        page.unroute('**/neoscorpion/fueler/assignments/**',unavailable)
                        card.locator('[name="actual_left"]').fill('12.0');card.locator('[name="actual_left"]').press('Tab');wait_for_fuel_saved(card)
                        expect(card.locator('[name="actual_left"]')).to_have_value('12.0')
                        expect(card.locator('.neoscorpion-fueler-off-form')).to_have_count(0)
                        local=off.replace(tzinfo=timezone.utc).astimezone(ZoneInfo('America/Chicago')).strftime('%H:%M')
                        expect(card.locator('.scorpion-fueler-finish')).to_contain_text(f'TRUCK OFF {local} · CONFIRMED')
                        page.screenshot(path=str(evidence/f'{engine.name}-off-draft-preserved.png'),full_page=True)
                        card.locator('[name="transfer_fuel_gallons"]').fill('300');card.locator('[name="transfer_fuel_gallons"]').press('Tab');wait_for_fuel_saved(card)
                        expect(card.locator('[data-fueler-submit-form] button')).to_be_enabled()
                        card.locator('[data-fueler-submit-form] button').click();expect(card.locator('[data-fueler-submission-status]')).to_contain_text('READY FOR DISPATCHER COMPLETE')
                        with F.app.app_context():version=db.session.get(NeoScorpionFuelWorkState,wid).fueler_submission_version
                        dialog.locator('[data-fuel-data-close]').click()
                        expect(row.locator('[name="expected_submission_version"]')).to_have_value(str(version),timeout=15000)
                        row.locator('button[formaction$="/fuel-dispatch/complete"]').click();expect(row).to_have_class(re.compile('is-complete'),timeout=15000)
                        dispatch.screenshot(path=str(evidence/f'{engine.name}-dispatcher-complete.png'),full_page=True)
                        with F.app.app_context():
                            work=db.session.get(NeoScorpionFuelWorkState,wid)
                            self.assertEqual((work.off_at_utc,work.off_by_user_id),(off,did))
                            self.assertEqual(next(t.actual_lbs for t in work.tank_states if t.tank_code=='left'),12000)
                            self.assertEqual(NeoScorpionFuelingEvent.query.filter_by(fuel_assignment_id=aid).one().fuel_work_state_id,wid)
                            self.assertEqual(NeoScorpionSortTruck.query.filter_by(fuel_truck_id=tid).one().current_gallons,9700)
                        self.assertEqual(errors,[])
                    finally:browser.close()
        finally:F.tearDownClass()
