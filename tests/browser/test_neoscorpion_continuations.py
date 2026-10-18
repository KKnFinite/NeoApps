"""Real desktop Dispatch and mobile Fueler handoff before source verification."""
from datetime import date,datetime,timedelta
import re
import time
import unittest
from unittest.mock import patch
from playwright.sync_api import expect
from app.extensions import db
from app.models import (User,PortalAppAccess,SortDateOperation,SortDateMission,SortDateParkingAssignment,NeoScorpionFuelAssignment,
    NeoScorpionFuelTruck,NeoScorpionSortTruck,NeoScorpionSortFueler,NeoScorpionFuelWorkState,NeoScorpionFuelingEvent)
from app.services.access_control import ensure_default_gateway_and_nodes,backfill_default_gateway_node_roles
from app.services.password_policy import set_user_password
from tests.browser import test_mobile_drawer as fixtures


class ContinuationBrowserTest(unittest.TestCase):
    def test_chromium_webkit_two_trucks_independent_verification_and_mobile_readonly(self):
        F=fixtures.MobileDrawerBrowserTest;F.setUpClass();F.app.config['LIVE_SCREEN_REFRESH_INTERVAL_MS']=5000
        try:
            with F.app.app_context():
                g=ensure_default_gateway_and_nodes();d=User.query.filter_by(username='drawer-admin').one()
                u=User(username='handoff-fueler',email='handoff-fueler@example.test',role='watcher',email_verified_at=datetime.now())
                set_user_password(u,'BrowserFixture123!');db.session.add(u);db.session.flush();uid=u.id
                backfill_default_gateway_node_roles(u,role='operator')
                access=PortalAppAccess.query.filter_by(user_id=u.id,app_code='neogateway').one()
                access.status='approved';access.role='operator';access.is_active=True
                op=SortDateOperation(generated_by_user_id=d.id,gateway_id=g.id,gateway_code=g.code,
                    sort_date=date.today(),sort_name='night',window_minutes=60)
                db.session.add(op);db.session.flush();oid=op.id
                db.session.add(NeoScorpionSortFueler(sort_date_operation_id=oid,user_id=u.id));cases=[]
                for i in range(2):
                    trucks=[]
                    for n in range(2):
                        t=NeoScorpionFuelTruck(gateway_id=g.id,truck_number=f'{i+1}0{n}',capacity_gallons=20000)
                        db.session.add(t);db.session.flush();trucks.append(t.id)
                        db.session.add(NeoScorpionSortTruck(sort_date_operation_id=oid,fuel_truck_id=t.id,
                            status='available',starting_gallons=10000,current_gallons=10000))
                    m=SortDateMission(sort_date_operation_id=oid,sort_date=op.sort_date,gateway_code=g.code,sort_name='night',
                        mission_type='departure',mission_source='manual',flight_number=f'UPS{700+i}',origin=g.code,destination='MSP',
                        assigned_tail_number=f'N49{i+1}UP',planned_datetime_utc=datetime.utcnow()+timedelta(hours=2,minutes=i),
                        timezone='America/Chicago',planned_fuel_load=40000)
                    db.session.add(m);db.session.flush()
                    db.session.add(SortDateParkingAssignment(sort_date_operation_id=oid,tail_number=m.assigned_tail_number,
                        ramp_code='C',position_code=f'{7+i:02}',lane_number=1))
                    a=NeoScorpionFuelAssignment(sort_date_operation_id=oid,sort_date_mission_id=m.id,
                        assigned_fueler_user_id=u.id,assigned_truck_id=trucks[0],confirmed_tail_number=m.assigned_tail_number)
                    db.session.add(a);db.session.flush();cases.append((a.id,m.id,trucks[1]))
                db.session.commit()
            with patch('app.services.neoscorpion.current_existing_operational_sort_operations',
                    side_effect=lambda g,now=None:[db.session.get(SortDateOperation,oid)]):
                for engine,(aid,mid,tid) in zip((F.pw.chromium,F.pw.webkit),cases):
                    browser=engine.launch();errors=[]
                    try:
                        mobile=browser.new_context(viewport={'width':390,'height':844},service_workers='block')
                        desktop=browser.new_context(viewport={'width':1440,'height':900},service_workers='block')
                        page=mobile.new_page();dispatch=desktop.new_page()
                        for p in (page,dispatch):p.on('pageerror',lambda e:errors.append(str(e)))
                        F().login(page,'handoff-fueler');F().login(dispatch)
                        dispatch.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1','seen')")
                        F().ready(page,'/neoscorpion/fueler');F().ready(dispatch,'/neoscorpion/fuel-dispatch')
                        row=dispatch.locator(f'[data-dispatch-mission-id="{mid}"]');row.locator('[data-neoscorpion-dispatch-details]').click()
                        detail=dispatch.locator(f'#neoscorpion-dispatch-detail-{mid}-1')
                        form=detail.locator('[data-pending-uplift-form]');form.locator('[name="proposed_truck_id"]').select_option(str(tid))
                        form.get_by_role('button',name='PLAN SECOND TRUCK',exact=True).click()
                        expect(detail).to_contain_text('NEXT TRUCK — PLANNED')
                        expect(row.locator('[name="inbound_fuel"]')).to_be_visible(timeout=12000)
                        # The live panel can detach a resolved element between
                        # locator resolution and evaluation. Re-resolve a zero
                        # rectangle; retain the actual 98px/alignment assertions.
                        for attempt in range(20):
                            inbound,required,geometry=row.evaluate("r=>[r.querySelector('[name=\"inbound_fuel\"]'),r.querySelector('[name=\"required_fuel\"]'),r].map(n=>{const b=n.getBoundingClientRect();return {y:b.y,width:b.width,height:b.height};})")
                            if geometry['height'] and inbound['width'] and required['width']:break
                            time.sleep(.1)
                        self.assertAlmostEqual(inbound['y'],required['y'],delta=1)
                        self.assertAlmostEqual(inbound['width'],required['width'],delta=1)
                        self.assertAlmostEqual(geometry['height'],98,delta=1)
                        card=page.locator('[data-fuel-data-card]').filter(has=page.locator(f'input[name="assignment_id"][value="{aid}"]')).first
                        expect(card).to_have_attribute('data-physical-work-id',re.compile(r'^\d+$'),timeout=12000)
                        card.locator('[data-truck-on-form] button').click();expect(card.locator('.neoscorpion-truck-on-row')).to_contain_text('CONFIRMED')
                        card.locator('.neoscorpion-fueler-off-form button').click();expect(card).to_contain_text('TRUCK OFF')
                        card.locator('[name="apu_running"]').select_option('no')
                        for code in ('left','ctr','right'):
                            card.locator(f'[name="remaining_{code}"]').fill('0' if code=='ctr' else '6')
                            card.locator(f'[name="actual_{code}"]').fill('0' if code=='ctr' else '15')
                        card.locator('[name="transfer_fuel_gallons"]').fill('3000')
                        card.locator('[data-fuel-planning-form] button[type="submit"]').click()
                        expect(card.locator('[data-fueler-submit-form] button')).to_be_enabled()
                        first_id=card.get_attribute('data-physical-work-id')
                        # Keep another mission dirty: source verification must
                        # arrive independently through the critical live patch.
                        other=dispatch.locator(f'[data-dispatch-mission-id="{cases[1 if mid==cases[0][1] else 0][1]}"]')
                        draft=other.locator('select[name="assigned_fueler_user_id"]')
                        if draft.count():draft.select_option('')
                        card.locator('[data-fueler-submit-form] button').click()
                        expect(card.locator('[data-fuel-action-feedback]')).to_contain_text('NEXT TRUCK ACTIVATED')
                        expect(card.locator('[data-truck-on-form] button')).to_be_enabled()
                        self.assertNotEqual(card.get_attribute('data-physical-work-id'),first_id)
                        for code in ('left','ctr','right'):expect(card.locator(f'[name="actual_{code}"]')).to_have_value('')
                        card.locator('[data-truck-on-form] button').click();expect(card).to_contain_text('CONFIRMED')
                        if engine.name=='webkit':
                            row.locator('[data-fuel-data-open]').click();expect(dispatch.locator('[data-fuel-data-dialog]')).to_be_visible()
                        page.reload()
                        source_card=page.locator(f'[data-fuel-data-card][data-physical-work-id="{first_id}"]')
                        source_card.locator('[name="actual_left"]').fill('16')
                        source_card.locator('[data-fuel-planning-form] button[type="submit"]').click()
                        expect(source_card.locator('[data-fueler-submit-form] button')).to_be_enabled()
                        source_card.locator('[data-fueler-submit-form] button').click()
                        expect(source_card.locator('[data-fuel-action-feedback]')).to_contain_text('SUBMITTED')
                        with F.app.app_context():child_id=db.session.get(NeoScorpionFuelAssignment,aid).active_work_state_id
                        expect(page.locator(f'[data-fuel-data-card][data-physical-work-id="{child_id}"]')).to_have_count(1)
                        expect(detail.locator('[data-action-key^="dependency-"]')).to_have_count(1,timeout=12000)
                        if engine.name=='webkit':dispatch.locator('[data-fuel-data-close]').click()
                        card.locator('[name="actual_left"]').fill('18')
                        card.locator('[data-fuel-planning-form] button[type="submit"]').click()
                        expect(card.locator('[data-fuel-data-status]')).to_contain_text('Saved')
                        row.locator('[name="required_fuel"]').fill('41');row.locator('[name="required_fuel"]').press('Tab')
                        try:expect(detail.locator('[data-action-key]')).to_have_count(3,timeout=12000)
                        except AssertionError as exc:
                            raise AssertionError(dict(actions=detail.locator('[data-action-key]').evaluate_all('items=>items.map(n=>[n.dataset.actionKey,n.textContent])'),
                                load=row.locator('[name="required_fuel"]').evaluate('n=>[n.value,n.dataset.savedValue,n.parentNode.textContent]'))) from exc
                        plan_review=detail.locator('[data-action-key^="plan-"]')
                        plan_review.get_by_role('button',name='CANCEL PLANNED TRUCK',exact=True).click()
                        expect(detail.locator('[data-action-key]')).to_have_count(2,timeout=12000)
                        dependency=detail.locator('[data-action-key^="dependency-"]')
                        dependency.locator('[name="reason"]').fill('Reviewed measured Remaining on the next physical visit')
                        dependency.locator('[name="confirm_remaining_verified"]').check()
                        dependency.get_by_role('button',name='ACCEPT VERIFIED HANDOFF',exact=True).click()
                        expect(detail.locator('[data-action-key]')).to_have_count(1,timeout=12000)
                        verify=detail.locator(f'[data-action-key="verify-{first_id}"] button').first
                        expect(verify).to_be_visible(timeout=12000)
                        dispatch.screenshot(path=str(F.evidence/f'continuation-{engine.name}-desktop.png'),full_page=True)
                        verify.click();expect(detail.locator(f'[data-action-key="verify-{first_id}"]')).to_have_count(0,timeout=12000)
                        if draft.count():expect(draft).to_have_value('')
                        with F.app.app_context():
                            a=db.session.get(NeoScorpionFuelAssignment,aid);self.assertIsNone(a.completed_at_utc)
                            self.assertIsNotNone(db.session.get(NeoScorpionFuelWorkState,a.active_work_state_id).manual_on_at_utc)
                            self.assertEqual(NeoScorpionFuelingEvent.query.filter_by(fuel_assignment_id=aid).count(),1)
                        # Explicitly abandon only this synthetic unrelated draft.
                        if draft.count():draft.select_option(str(uid))
                        card.locator('[name="apu_running"]').select_option('no')
                        for code in ('left','ctr','right'):card.locator(f'[name="actual_{code}"]').fill('0' if code=='ctr' else '21')
                        card.locator('[name="transfer_fuel_gallons"]').fill('2000')
                        card.locator('[data-fuel-planning-form] button[type="submit"]').click()
                        card.locator('.neoscorpion-fueler-off-form button').click()
                        card.locator('[data-fueler-submit-form] button').click()
                        complete=row.locator('button[formaction$="/fuel-dispatch/complete"]')
                        expect(complete).to_be_visible(timeout=12000);complete.click()
                        expect(row).to_have_class(re.compile('is-complete'),timeout=12000)
                        stats=dispatch.locator('[data-dispatch-collapse="statistics"]')
                        if stats.get_attribute('aria-expanded')=='false':stats.click();expect(dispatch.locator('[data-dispatch-section="statistics"]')).to_be_visible()
                        stats.click();expect(dispatch.locator('[data-dispatch-section="statistics"]')).to_be_hidden()
                        trucks=dispatch.locator('[data-dispatch-collapse="trucks"]')
                        if trucks.get_attribute('aria-expanded')=='false':trucks.click();expect(dispatch.locator('[data-dispatch-section="trucks"]')).to_be_visible()
                        trucks.click();expect(dispatch.locator('[data-dispatch-section="trucks"]')).to_be_hidden()
                        expect(dispatch.locator('[data-dispatch-preference-status]').first).to_have_text('SAVED')
                        dispatch.locator('[data-operational-board-toggle]:visible').click()
                        expect(dispatch.locator('[data-dispatch-section="trucks"]')).to_be_visible()
                        dispatch.screenshot(path=str(F.evidence/f'continuation-{engine.name}-board.png'),full_page=True)
                        dispatch.locator('[data-operational-board-toggle]:visible').click()
                        expect(dispatch.locator('[data-dispatch-section="trucks"]')).to_be_hidden()
                        dispatch.reload()
                        expect(dispatch.locator('[data-dispatch-section="statistics"]')).to_be_hidden()
                        expect(dispatch.locator('[data-dispatch-section="trucks"]')).to_be_hidden()
                        for width in (1920,1440,390,375):
                            dispatch.set_viewport_size({'width':width,'height':568 if width==375 else 900})
                            self.assertFalse(dispatch.evaluate('document.documentElement.scrollWidth>innerWidth+1'))
                            if width<901:
                                self.assertEqual(dispatch.locator('[data-fuel-dispatch-live] button[type="submit"]:visible').count(),0)
                                self.assertEqual(row.locator('td:visible').count(),5)
                        self.assertEqual(errors,[])
                        dispatch.screenshot(path=str(F.evidence/f'continuation-{engine.name}-mobile.png'),full_page=True)
                    finally:browser.close()
        finally:F.tearDownClass()
