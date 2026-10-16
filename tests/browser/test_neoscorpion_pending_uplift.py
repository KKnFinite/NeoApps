"""Real queued-uplift edits, dirty refresh, promotion and cancellation in both engines."""
from datetime import date,datetime,timedelta
import unittest
from unittest.mock import patch
from playwright.sync_api import expect
from app.extensions import db
from app.models import (User,SortDateOperation,SortDateMission,NeoScorpionFuelAssignment,NeoScorpionFuelTruck,
    NeoScorpionSortTruck,NeoScorpionSortFueler,NeoScorpionSortAssetState,NeoScorpionFuelWorkState,NeoScorpionFuelingEvent)
from app.services import neoscorpion as fuel
from app.services.neoscorpion_assets import record_nightly_operational_change
from app.services.access_control import ensure_default_gateway_and_nodes
from tests.browser import test_mobile_drawer as fixtures

class PendingBrowserTest(unittest.TestCase):
    def test_chromium_webkit_pending_edit_mobile_refresh_and_promotion(self):
        F=fixtures.MobileDrawerBrowserTest;F.setUpClass();F.app.config['LIVE_SCREEN_REFRESH_INTERVAL_MS']=1000
        try:
            with F.app.app_context():
                g=ensure_default_gateway_and_nodes();u=User.query.filter_by(username='drawer-admin').one()
                op=SortDateOperation(generated_by_user_id=u.id,gateway_id=g.id,gateway_code=g.code,sort_date=date.today(),sort_name='night',window_minutes=60)
                db.session.add(op);db.session.flush();oid=op.id;uid=u.id;gid=g.id
                db.session.add(NeoScorpionSortFueler(sort_date_operation_id=oid,user_id=u.id));ids=[]
                for index in range(2):
                    t=NeoScorpionFuelTruck(gateway_id=g.id,truck_number=str(70+index),capacity_gallons=20000)
                    m=SortDateMission(sort_date_operation_id=oid,sort_date=op.sort_date,gateway_code=g.code,sort_name='night',mission_type='departure',mission_source='manual',flight_number=f'UPS{801+index}',origin=g.code,destination='SDF',assigned_tail_number=f'N49{index+1}UP',planned_datetime_utc=datetime.utcnow()+timedelta(hours=2,minutes=index),timezone='America/Chicago',planned_fuel_load=25000)
                    db.session.add_all([t,m]);db.session.flush()
                    db.session.add(NeoScorpionSortTruck(sort_date_operation_id=oid,fuel_truck_id=t.id,status='available',starting_gallons=10000,current_gallons=10000))
                    a=NeoScorpionFuelAssignment(sort_date_operation_id=oid,sort_date_mission_id=m.id,confirmed_tail_number=m.assigned_tail_number)
                    db.session.add(a);db.session.flush();ids.append((a.id,m.id,t.id))
                db.session.commit()
            with patch('app.services.neoscorpion.current_existing_operational_sort_operations',side_effect=lambda g,now=None:[db.session.get(SortDateOperation,oid)]):
                for engine,(aid,mid,tid) in zip((F.pw.chromium,F.pw.webkit),ids):
                    with F.app.app_context():
                        a=db.session.get(NeoScorpionFuelAssignment,aid);a.assigned_fueler_user_id=uid;a.assigned_truck_id=tid;record_nightly_operational_change(NeoScorpionSortAssetState.query.filter_by(sort_date_operation_id=oid).first(),oid);db.session.commit()
                    browser=engine.launch()
                    try:
                        context=browser.new_context(viewport={'width':1440,'height':900});dispatch=context.new_page();F().login(dispatch)
                        dispatch.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1','seen')")
                        page=context.new_page();F().ready(page,'/neoscorpion/fueler')
                        card=page.locator('[data-fuel-data-card]').filter(has=page.locator(f'input[name="assignment_id"][value="{aid}"]')).first
                        card.locator('[data-truck-on-form] button').click();expect(card.locator('.neoscorpion-truck-on-row')).to_contain_text('CONFIRMED')
                        F().ready(dispatch,'/neoscorpion/fuel-dispatch')
                        if engine.name=='webkit':dispatch.set_viewport_size({'width':390,'height':844})
                        else:dispatch.locator(f'[data-dispatch-mission-id="{mid}"] [data-neoscorpion-dispatch-details]').click()
                        dispatch.locator(f'[data-dispatch-sort-group="{mid}"] button[value="create"]:visible').click()
                        pending=dispatch.locator(f'[data-pending-uplift][data-dispatch-sort-group="{mid}"]')
                        expect(pending).to_be_visible();self.assertEqual(pending.evaluate('el=>el.previousElementSibling.dataset.dispatchMissionId'),str(mid))
                        pending.locator('[name="required_fuel"]').fill('40.12');pending.locator('button[value="save"]').click()
                        expect(pending.locator('[name="required_fuel"]')).to_have_value('40.12')
                        expect(pending.locator('[data-lifecycle-status]')).not_to_contain_text('Save Failed')
                        # A revision arriving while a pending field is dirty must not erase it.
                        pending.locator('[name="required_fuel"]').fill('42')
                        with F.app.app_context():record_nightly_operational_change(NeoScorpionSortAssetState.query.filter_by(sort_date_operation_id=oid).first(),oid);db.session.commit()
                        dispatch.wait_for_timeout(1800);expect(pending.locator('[name="required_fuel"]')).to_have_value('42')
                        pending.locator('button[value="save"]').click()
                        expect(pending.locator('[name="required_fuel"]')).to_have_value('42')
                        target=34800 if engine.name=='webkit' else 42000
                        if engine.name=='webkit':
                            pending.locator('[name="required_fuel"]').fill('34.8');pending.locator('button[value="save"]').click()
                            expect(pending.locator('[name="required_fuel"]')).to_have_value('34.8')
                        for width in (1440,600,390):
                            dispatch.set_viewport_size({'width':width,'height':844});expect(pending).to_be_visible()
                            self.assertFalse(dispatch.evaluate('document.documentElement.scrollWidth>innerWidth+1'))
                        with F.app.app_context():
                            self.assertEqual(db.session.get(SortDateMission,mid).planned_fuel_load,25000)
                            self.assertEqual(NeoScorpionFuelingEvent.query.filter_by(fuel_assignment_id=aid).count(),0)
                        page.bring_to_front();card.locator('[name="apu_running"]').select_option('yes' if engine.name=='webkit' else 'no')
                        if engine.name=='webkit':
                            card.locator('[name="apu_source_tank_code"]').select_option('left')
                            card.locator('[name="apu_override_allowance"]').fill('0.56')
                            card.locator('[data-apu-use-manual]').click()
                        for code in ('left','ctr','right'):
                            card.locator(f'[name="remaining_{code}"]').fill('0' if code=='ctr' else '6')
                            card.locator(f'[name="actual_{code}"]').fill('0' if code=='ctr' else ('17.62' if engine.name=='webkit' else '15'))
                        card.locator('[name="transfer_fuel_gallons"]').fill('3000');card.locator('[data-fuel-planning-form] button[type="submit"]').click()
                        expect(card.locator('[data-fuel-data-status]')).to_contain_text('Saved')
                        card.locator('.neoscorpion-fueler-off-form button').click();expect(card.locator('.neoscorpion-truck-on-row')).to_contain_text('TRUCK OFF')
                        card.locator('[data-fueler-submit-form] button').click();expect(card.locator('[data-fueler-submission-status]')).to_have_text('READY FOR DISPATCHER COMPLETE')
                        dispatch.bring_to_front();dispatch.set_viewport_size({'width':1440,'height':900})
                        row=dispatch.locator(f'[data-dispatch-mission-id="{mid}"]');button=row.locator('button[formaction$="/fuel-dispatch/complete"]')
                        expect(button).to_be_visible(timeout=20000)
                        toggle=row.locator('[data-neoscorpion-dispatch-details]')
                        if toggle.get_attribute('aria-expanded')!='true':toggle.click()
                        button.click();expect(pending).to_have_count(0,timeout=20000)
                        row=dispatch.locator(f'[data-dispatch-mission-id="{mid}"]');expect(row).to_have_attribute('data-dispatch-row-key',f'{mid}-2')
                        detail=dispatch.locator(f'#neoscorpion-dispatch-detail-{mid}-2');expect(detail).to_be_visible()
                        with F.app.app_context():
                            a=db.session.get(NeoScorpionFuelAssignment,aid);self.assertEqual(a.current_cycle_number,2)
                            self.assertEqual(db.session.get(SortDateMission,mid).planned_fuel_load,target)
                            work=db.session.get(NeoScorpionFuelWorkState,a.active_work_state_id)
                            self.assertTrue(all(t.actual_lbs is None for t in work.tank_states));self.assertIsNone(work.manual_on_at_utc)
                            self.assertEqual(NeoScorpionFuelingEvent.query.filter_by(fuel_assignment_id=aid).count(),1)
                            if engine.name=='webkit':self.assertEqual(NeoScorpionFuelingEvent.query.filter_by(fuel_assignment_id=aid).one().neo_fuel_lbs,34680)
                        for width in (1440,390):
                            dispatch.set_viewport_size({'width':width,'height':900})
                            expect(row).not_to_contain_text('REVIEW REQUIRED')
                            self.assertFalse(dispatch.evaluate('document.documentElement.scrollWidth>innerWidth+1'))
                            dispatch.screenshot(path=str(F.evidence / f'uplift-apu-{engine.name}-{width}.png'),full_page=True)
                        dispatch.set_viewport_size({'width':1440,'height':900})
                        cancel=detail.locator('form[action$="/cancel-uplift"] button[type="submit"]');expect(cancel).to_be_visible();dispatch.once('dialog',lambda dialog:dialog.accept());cancel.click()
                        expect(row).to_have_class(__import__('re').compile('is-complete'),timeout=20000)
                        self.assertFalse(dispatch.evaluate('document.documentElement.scrollWidth>innerWidth+1'))
                    finally:browser.close()
        finally:F.tearDownClass()
