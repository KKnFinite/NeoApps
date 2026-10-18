"""Actual Dispatch status, advance assignment and same-stage live updates."""
from datetime import date, datetime, timedelta
import unittest
from unittest.mock import patch

from playwright.sync_api import expect
from app.extensions import db
from app.models import (SortDateOperation, SortDateMission, SortDateParkingAssignment, User,
    NeoScorpionFuelAssignment, NeoScorpionFuelTruck, NeoScorpionSortTruck,
    NeoScorpionSortFueler, NeoScorpionTailFuelState)
from app.services import neoscorpion as fuel
from app.services.access_control import ensure_default_gateway_and_nodes
from tests.browser import test_mobile_drawer as fixtures


class StatusBrowserTest(unittest.TestCase):
    def test_chromium_webkit_pending_ready_assigned_and_live_waiting(self):
        F=fixtures.MobileDrawerBrowserTest;F.setUpClass();F.app.config['LIVE_SCREEN_REFRESH_INTERVAL_MS']=5000
        try:
            with F.app.app_context():
                gateway=ensure_default_gateway_and_nodes();user=User.query.filter_by(username='drawer-admin').one()
                uid=user.id;gid=gateway.id
                op=SortDateOperation(generated_by_user_id=uid,gateway_id=gid,gateway_code=gateway.code,
                    sort_date=date.today(),sort_name='night',window_minutes=60)
                db.session.add(op);db.session.flush();oid=op.id
                db.session.add(NeoScorpionSortFueler(sort_date_operation_id=oid,user_id=uid))
                cases=[]
                for i in range(2):
                    truck=NeoScorpionFuelTruck(gateway_id=gid,truck_number=f'STATUS-{i}',capacity_gallons=10000)
                    db.session.add(truck);db.session.flush()
                    db.session.add(NeoScorpionSortTruck(sort_date_operation_id=oid,fuel_truck_id=truck.id,
                        status='available',starting_gallons=9000,current_gallons=9000))
                    missions=[]
                    for j,tail in enumerate(f'N{491+i*3+n}UP' for n in range(3)):
                        mission=SortDateMission(sort_date_operation_id=oid,sort_date=op.sort_date,gateway_code=gateway.code,
                            sort_name='night',mission_type='departure',mission_source='manual',
                            flight_number=f'UPS{800+i*10+j}',origin=gateway.code,destination='MSP',
                            assigned_tail_number=tail,planned_datetime_utc=datetime.utcnow()+timedelta(hours=3,minutes=j),
                            timezone='America/Chicago',planned_fuel_load=None if j==0 else 25000)
                        db.session.add(mission);db.session.flush()
                        a=NeoScorpionFuelAssignment(sort_date_operation_id=oid,sort_date_mission_id=mission.id,
                            confirmed_tail_number=tail,assigned_fueler_user_id=uid if j==2 else None,
                            assigned_truck_id=truck.id if j==2 else None)
                        db.session.add(a);db.session.flush();missions.append((mission.id,a.id))
                        if j!=0:db.session.add(NeoScorpionTailFuelState(sort_date_operation_id=oid,tail_number=tail,inbound_fuel_lbs=12000))
                    cases.append((missions,truck.id,tail))
                for n,tail in enumerate(f'N{491+i}UP' for i in range(6)):
                    db.session.add(SortDateParkingAssignment(sort_date_operation_id=oid,tail_number=tail,
                        ramp_code='C',position_code=f'{7+n:02}',lane_number=1))
                db.session.commit()
            with patch('app.services.neoscorpion.current_existing_operational_sort_operations',
                       side_effect=lambda g,now=None:[db.session.get(SortDateOperation,oid)]):
                for engine,(missions,tid,busy_tail) in zip((F.pw.chromium,F.pw.webkit),cases):
                    browser=engine.launch();errors=[]
                    try:
                        page=browser.new_page(viewport={'width':1440,'height':900})
                        page.on('pageerror',lambda e:errors.append(str(e)))
                        F().login(page);page.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1','seen')")
                        F().ready(page,'/neoscorpion/fuel-dispatch')
                        pending=page.locator(f'[data-dispatch-mission-id="{missions[0][0]}"]')
                        ready=page.locator(f'[data-dispatch-mission-id="{missions[1][0]}"]')
                        status=lambda row:row.locator('[data-fuel-status]')
                        expect(status(pending)).to_have_attribute('data-fuel-status','pending')
                        expect(status(ready)).to_have_attribute('data-fuel-status','ready')
                        expect(ready).to_have_class(__import__('re').compile('is-ready-to-assign'))
                        expect(page.locator('[data-dispatch-completion-blocker]')).to_have_count(0)
                        expect(pending.locator('[data-dispatch-verification-form]')).to_have_count(0)
                        # Actual Dispatcher assignment is allowed before mission data exists.
                        pending.locator('select[name="assigned_fueler_user_id"]').select_option(str(uid))
                        pending.locator('select[name="assigned_truck_id"]').select_option(str(tid))
                        pending.locator('[data-dispatch-assignment-submit]').click()
                        expect(status(pending)).to_have_attribute('data-fuel-status','assigned',timeout=12000)
                        pending.locator('[data-dispatch-action-open]').click()
                        details=page.locator(f'#neoscorpion-dispatch-detail-{missions[0][0]}-1')
                        expect(details.locator('[data-action-key="mission-data"]')).to_contain_text('Required Fuel Load')
                        # Another genuine physical occupation may coexist with this preassignment.
                        with F.app.app_context():
                            fuel.confirm_fueler_truck_on(db.session.get(type(gateway),gid),db.session.get(User,uid),missions[2][1],
                                expected_cycle=1,expected_tail=busy_tail,expected_truck_id=tid)
                            db.session.commit()
                        # Keep a different row dirty; the fast status patch must still update.
                        ready.locator('select[name="assigned_fueler_user_id"]').select_option(str(uid))
                        pending.locator('[name="inbound_fuel"]').fill('12');pending.locator('[name="inbound_fuel"]').press('Tab')
                        expect(pending.locator('[name="inbound_fuel"]')).to_have_attribute('data-saved-value','12.0',timeout=12000)
                        pending.locator('[name="required_fuel"]').fill('25');pending.locator('[name="required_fuel"]').press('Tab')
                        try:
                            expect(pending.locator('[name="required_fuel"]')).to_have_attribute('data-saved-value','25.0',timeout=12000)
                            expect(status(pending).locator('[data-fuel-status-secondary]')).to_have_text('WAITING FOR TRUCK',timeout=12000)
                        except AssertionError as exc:
                            with F.app.app_context():
                                live=next(r for r in fuel.fuel_dispatch_context(db.session.get(type(gateway),gid))['rows'] if r['mission'].id==missions[0][0])
                                server=dict(status=live['dispatch_status_key'],secondary=live['fuel_status_secondary'],missing=live['mission_data_missing'])
                            raise AssertionError(dict(engine=engine.name,errors=errors,server=server,
                                fields=pending.locator('[data-dispatch-autosave]').evaluate_all('xs=>xs.map(x=>[x.name,x.value,x.dataset.savedValue,x.parentNode.textContent])'),
                                live_error=page.locator('[data-spear-fleet-status]').all_text_contents())) from exc
                        expect(status(pending)).to_have_attribute('data-fuel-status','assigned')
                        expect(details.locator('[data-action-key="mission-data"]')).to_have_count(0)
                        expect(details.locator('.scorpion-mobile-readonly')).not_to_contain_text('Missing:')
                        expect(ready.locator('select[name="assigned_fueler_user_id"]')).to_have_value(str(uid))
                        expect(page.locator(f'[data-dispatch-truck-id="{tid}"] .neoscorpion-truck-activity')).to_contain_text('FUELING',timeout=12000)
                        self.assertEqual(status(pending).locator('[data-fuel-status-secondary]').count(),1)
                        self.assertFalse(pending.evaluate("r=>r.classList.contains('is-ready-to-assign')"))
                        for width in (1920,1440,390,375):
                            page.set_viewport_size({'width':width,'height':844 if width<500 else 900})
                            self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
                            self.assertAlmostEqual(pending.bounding_box()['height'],73 if width<500 else 98,delta=1)
                            note=status(pending).locator('[data-fuel-status-secondary]')
                            cell=pending.locator('.neoscorpion-dispatch-status-cell')
                            note_box=note.bounding_box();cell_box=cell.bounding_box()
                            self.assertGreaterEqual(note_box['x'],cell_box['x']-4)
                            self.assertLessEqual(note_box['x']+note_box['width'],cell_box['x']+cell_box['width']+4)
                            if width<500:
                                self.assertEqual(pending.locator('td:visible').count(),5)
                                self.assertEqual(pending.locator('button[type="submit"]:visible,input:visible,select:visible').count(),0)
                                for card in page.locator('[data-dispatch-truck-id]').all():
                                    for label in card.locator(':scope > .neoscorpion-truck-driver-name, :scope > .neoscorpion-calculation-warning').all():
                                        self.assertGreater(label.bounding_box()['width'],card.bounding_box()['width']*.8)
                        pending.scroll_into_view_if_needed()
                        page.screenshot(path=str(F.evidence/f'status-{engine.name}-mobile.png'),full_page=False)
                        page.set_viewport_size({'width':1440,'height':900})
                        pending.scroll_into_view_if_needed()
                        page.screenshot(path=str(F.evidence/f'status-{engine.name}-desktop.png'),full_page=False)
                        self.assertEqual(errors,[])
                        with F.app.app_context():
                            fuel.mark_fueler_off(db.session.get(type(gateway),gid),db.session.get(User,uid),missions[2][1])
                            db.session.commit()
                    finally:browser.close()
        finally:F.tearDownClass()
