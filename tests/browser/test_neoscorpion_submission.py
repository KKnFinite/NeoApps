"""Fueler physical OFF, saved corrections and explicit review handoff in two engines."""
from datetime import date,datetime,timedelta
import unittest
import time
from unittest.mock import patch
from playwright.sync_api import expect
from app.extensions import db
from app.models import (User,SortDateOperation,SortDateMission,NeoScorpionFuelAssignment,NeoScorpionFuelTruck,
    NeoScorpionSortTruck,NeoScorpionSortFueler,NeoScorpionFuelWorkState,NeoScorpionFuelingEvent)
from app.services.access_control import ensure_default_gateway_and_nodes
from tests.browser import test_mobile_drawer as fixtures

class SubmissionBrowserTest(unittest.TestCase):
    def test_sort_rollover_replaces_old_operation_without_cross_sort_controls(self):
        F=fixtures.MobileDrawerBrowserTest;F.setUpClass();F.app.config['LIVE_SCREEN_REFRESH_INTERVAL_MS']=5000
        try:
            with F.app.app_context():
                g=ensure_default_gateway_and_nodes();u=User.query.filter_by(username='drawer-admin').one();operations=[]
                for index in range(2):
                    op=SortDateOperation(generated_by_user_id=u.id,gateway_id=g.id,gateway_code=g.code,
                        sort_date=date.today()+timedelta(days=index),sort_name='night',window_minutes=60)
                    db.session.add(op);db.session.flush()
                    m=SortDateMission(sort_date_operation_id=op.id,sort_date=op.sort_date,gateway_code=g.code,
                        sort_name='night',mission_type='departure',mission_source='manual',flight_number=f'UPS{870+index}',
                        origin=g.code,destination='MSP',assigned_tail_number=f'N49{1+index}UP',
                        planned_datetime_utc=datetime.utcnow()+timedelta(hours=2),timezone='America/Chicago',planned_fuel_load=25000)
                    db.session.add(m);db.session.flush();operations.append((op.id,m.id))
                db.session.commit()
            selected={'operation':operations[0][0]}
            with patch('app.services.neoscorpion.current_existing_operational_sort_operations',
                       side_effect=lambda g,now=None:[db.session.get(SortDateOperation,selected['operation'])]):
                for engine in (F.pw.chromium,F.pw.webkit):
                    selected['operation']=operations[0][0];browser=engine.launch()
                    try:
                        page=browser.new_page(viewport={'width':1440,'height':900});F().login(page)
                        F().ready(page,'/neoscorpion/fuel-dispatch')
                        expect(page.locator(f'[data-dispatch-mission-id="{operations[0][1]}"]')).to_have_count(1)
                        selected['operation']=operations[1][0]
                        expect(page.locator(f'[data-dispatch-mission-id="{operations[1][1]}"]')).to_have_count(1,timeout=10000)
                        expect(page.locator(f'[data-dispatch-mission-id="{operations[0][1]}"]')).to_have_count(0)
                        self.assertEqual(page.locator('[data-fuel-dispatch-live]').get_attribute('data-operation-id'),str(operations[1][0]))
                    finally:browser.close()
        finally:F.tearDownClass()

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
                        submit.click();expect(card.locator('[data-fueler-submission-status]')).to_contain_text('READY FOR DISPATCHER COMPLETE')
                        card.locator('[name="actual_left"]').fill('16');card.locator('[data-fuel-planning-form] button[type="submit"]').click();expect(submit).to_be_enabled()
                        submit.click();expect(card.locator('[data-fueler-submission-status]')).to_contain_text('READY FOR DISPATCHER COMPLETE')
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

    def test_two_accounts_critical_handoff_with_dirty_row_or_open_dialog(self):
        from app.models import PortalAppAccess
        from app.services.access_control import backfill_default_gateway_node_roles
        from app.services.password_policy import set_user_password
        F=fixtures.MobileDrawerBrowserTest;F.setUpClass();F.app.config['LIVE_SCREEN_REFRESH_INTERVAL_MS']=5000
        try:
            with F.app.app_context():
                g=ensure_default_gateway_and_nodes();dispatcher=User.query.filter_by(username='drawer-admin').one()
                user=User(username='audit-fueler',email='audit-fueler@example.test',role='watcher',email_verified_at=datetime.now())
                set_user_password(user,'BrowserFixture123!');db.session.add(user);db.session.flush()
                backfill_default_gateway_node_roles(user,role='operator')
                access=PortalAppAccess.query.filter_by(user_id=user.id,app_code='neogateway').one()
                access.status='approved';access.role='operator';access.is_active=True
                op=SortDateOperation(generated_by_user_id=dispatcher.id,gateway_id=g.id,gateway_code=g.code,sort_date=date.today(),sort_name='night',window_minutes=60)
                db.session.add(op);db.session.flush();oid=op.id
                db.session.add(NeoScorpionSortFueler(sort_date_operation_id=oid,user_id=user.id));ids=[]
                for index in range(5):
                    truck=NeoScorpionFuelTruck(gateway_id=g.id,truck_number=str(800+index),capacity_gallons=20000)
                    m=SortDateMission(sort_date_operation_id=oid,sort_date=op.sort_date,gateway_code=g.code,sort_name='night',mission_type='departure',mission_source='manual',flight_number=f'UPS{880+index}',origin=g.code,destination='MSP',assigned_tail_number='N491UP',planned_datetime_utc=datetime.utcnow()+timedelta(hours=2,minutes=index),timezone='America/Chicago',planned_fuel_load=25000)
                    db.session.add_all([truck,m]);db.session.flush()
                    db.session.add(NeoScorpionSortTruck(sort_date_operation_id=oid,fuel_truck_id=truck.id,status='available',starting_gallons=10000,current_gallons=10000))
                    a=NeoScorpionFuelAssignment(sort_date_operation_id=oid,sort_date_mission_id=m.id,assigned_fueler_user_id=user.id,assigned_truck_id=truck.id,confirmed_tail_number='N491UP')
                    db.session.add(a);db.session.flush();ids.append((a.id,m.id))
                db.session.commit()
            with patch('app.services.neoscorpion.current_existing_operational_sort_operations',side_effect=lambda g,now=None:[db.session.get(SortDateOperation,oid)]):
                for index,(engine,mode) in enumerate((e,m) for e in (F.pw.chromium,F.pw.webkit) for m in ('dirty','dialog')):
                    browser=engine.launch();errors=[]
                    try:
                        mobile=browser.new_context(viewport={'width':390,'height':844},service_workers='block')
                        desktop=browser.new_context(viewport={'width':1440,'height':900},service_workers='block')
                        page=mobile.new_page();dispatch=desktop.new_page()
                        page.on('pageerror',lambda error:errors.append(str(error)));dispatch.on('pageerror',lambda error:errors.append(str(error)))
                        F().login(page,'audit-fueler');F().login(dispatch)
                        dispatch.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1','seen')")
                        F().ready(page,'/neoscorpion/fueler');F().ready(dispatch,'/neoscorpion/fuel-dispatch')
                        aid,mid=ids[index]
                        card=page.locator('[data-fuel-data-card]').filter(has=page.locator(f'input[name="assignment_id"][value="{aid}"]')).first
                        row=dispatch.locator(f'[data-dispatch-mission-id="{mid}"]')
                        draft=dispatch.locator(f'[data-dispatch-mission-id="{ids[4][1]}"] select[name="assigned_fueler_user_id"]')
                        if mode=='dirty':draft.select_option('')
                        else:row.locator('[data-fuel-data-open]').click();expect(dispatch.locator('[data-fuel-data-dialog]')).to_be_visible()
                        if mode=='dialog':dispatch.set_viewport_size({'width':390,'height':844})
                        card.locator('[data-truck-on-form] button').click();expect(card.locator('.neoscorpion-truck-on-row')).to_contain_text('CONFIRMED')
                        page.reload();expect(card.locator('.neoscorpion-truck-on-row')).to_contain_text('CONFIRMED')
                        card.locator('.neoscorpion-fueler-off-form button').click()
                        expect(card.locator('.neoscorpion-truck-on-row')).to_contain_text('TRUCK OFF')
                        card.locator('[name="apu_running"]').select_option('no')
                        for code in ('left','ctr','right'):
                            card.locator(f'[name="remaining_{code}"]').fill('0' if code=='ctr' else '6')
                            card.locator(f'[name="actual_{code}"]').fill('0' if code=='ctr' else '15')
                        card.locator('[name="transfer_fuel_gallons"]').fill('3000')
                        card.locator('[data-fuel-planning-form] button[type="submit"]').click()
                        submit=card.locator('[data-fueler-submit-form] button');expect(submit).to_be_enabled()
                        # Reproduce the old silent mobile dirty-form rejection.
                        card.locator('[name="actual_left"]').fill('16');submit.click()
                        feedback=card.locator('[data-fuel-action-feedback]')
                        expect(feedback).to_contain_text('Save Fuel Entry before COMPLETE ASSIGNMENT')
                        self.assertLess(feedback.bounding_box()['y'],844)
                        with F.app.app_context():
                            a=db.session.get(NeoScorpionFuelAssignment,aid)
                            self.assertIsNone(db.session.get(NeoScorpionFuelWorkState,a.active_work_state_id).fueler_submitted_at_utc)
                        card.locator('[data-fuel-planning-form] button[type="submit"]').click();expect(submit).to_be_enabled()
                        started=time.monotonic();submit.click()
                        expect(card.locator('[data-fuel-action-feedback]')).to_contain_text('SUBMITTED')
                        dispatch.bring_to_front()
                        complete=row.locator('button[formaction$="/fuel-dispatch/complete"]')
                        expect(complete).to_have_count(1,timeout=10000)
                        self.assertLess(time.monotonic()-started,10.5)
                        expect(row.locator('[data-fuel-status]')).to_have_attribute('data-fuel-status','off')
                        if mode=='dirty':expect(draft).to_have_value('')
                        else:dispatch.locator('[data-fuel-data-close]').click()
                        self.assertFalse(dispatch.evaluate('document.documentElement.scrollWidth>innerWidth+1'))
                        self.assertAlmostEqual(row.bounding_box()['height'],73 if mode=='dialog' else 98,delta=1)
                        dispatch.screenshot(path=str(F.evidence/f'lifecycle-{engine.name}-{mode}.png'),full_page=True)
                        if mode=='dialog':dispatch.set_viewport_size({'width':1440,'height':900})
                        # Verification uses its own identity, not the dirty row's form.
                        self.assertEqual(complete.evaluate('b=>b.form.elements.assignment_id.value'),str(aid))
                        complete.click();expect(row).to_have_class(__import__('re').compile('is-complete'),timeout=10000)
                        if mode=='dirty':expect(draft).to_have_value('')
                        self.assertEqual(errors,[])
                    finally:browser.close()
        finally:F.tearDownClass()
