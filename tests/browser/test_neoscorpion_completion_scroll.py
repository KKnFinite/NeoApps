"""Actual COMPLETE verification and remote completion must not follow the row."""
import unittest
from datetime import date, datetime, timedelta
from unittest.mock import patch
from playwright.sync_api import expect
from app.extensions import db
from app.models import (User, SortDateOperation, SortDateMission, NeoScorpionFuelAssignment,
    NeoScorpionFuelTruck, NeoScorpionSortTruck, NeoScorpionSortFueler, NeoScorpionSortAssetState)
from app.services import neoscorpion as service
from app.services.access_control import ensure_default_gateway_and_nodes
from tests.browser import test_mobile_drawer as fixtures


class CompletionScrollBrowserTest(unittest.TestCase):
    def test_native_completion_both_sorts_and_mobile_live_completion(self):
        Fixture=fixtures.MobileDrawerBrowserTest
        Fixture.setUpClass(); browser=None
        Fixture.app.config['LIVE_SCREEN_REFRESH_INTERVAL_MS']=1000
        try:
            with Fixture.app.app_context():
                gateway=ensure_default_gateway_and_nodes(); actor=User.query.filter_by(username='drawer-admin').one()
                now=datetime.utcnow()
                operation=SortDateOperation(generated_by_user_id=actor.id,gateway_id=gateway.id,
                    gateway_code=gateway.code,sort_date=date.today(),sort_name='night',window_minutes=60)
                truck=NeoScorpionFuelTruck(gateway_id=gateway.id,truck_number='20',capacity_gallons=20000)
                db.session.add_all([operation,truck]); db.session.flush()
                db.session.add_all([NeoScorpionSortAssetState(sort_date_operation_id=operation.id,revision=1),
                    NeoScorpionSortFueler(sort_date_operation_id=operation.id,user_id=actor.id),
                    NeoScorpionSortTruck(sort_date_operation_id=operation.id,fuel_truck_id=truck.id,
                        status='available',starting_gallons=15000,current_gallons=15000)])
                ids=[]; targets=[]
                for i in range(16):
                    tail=f'N{480+i}UP'
                    mission=SortDateMission(sort_date_operation_id=operation.id,sort_date=operation.sort_date,
                        gateway_code=gateway.code,sort_name='night',mission_type='departure',mission_source='manual',
                        flight_number=f'UPS{600+i}',origin=gateway.code,destination='SDF',assigned_tail_number=tail,
                        timezone='America/Chicago',planned_datetime_utc=now+timedelta(hours=20-i),
                        planned_fuel_load=25000,fuel_status='assigned')
                    db.session.add(mission); db.session.flush(); ids.append(mission.id)
                    db.session.add(SortDateMission(sort_date_operation_id=operation.id,sort_date=operation.sort_date,
                        gateway_code=gateway.code,sort_name='night',mission_type='arrival',mission_source='manual',
                        flight_number=f'UPS{800+i}',origin='SDF',destination=gateway.code,assigned_tail_number=tail,
                        eta_datetime_utc=now-timedelta(hours=1)+timedelta(minutes=i),arrival_status='scheduled'))
                    if i in (6,8,10):
                        assignment=NeoScorpionFuelAssignment(sort_date_operation_id=operation.id,
                            sort_date_mission_id=mission.id,confirmed_tail_number=tail,
                            assigned_fueler_user_id=actor.id,assigned_truck_id=truck.id,review_status='assigned',
                            created_at=now-timedelta(hours=1))
                        db.session.add(assignment); db.session.flush(); targets.append((mission.id,assignment.id,tail))
                db.session.commit(); op_id,actor_id,gateway_id,truck_id=operation.id,actor.id,gateway.id,truck.id
            with patch('app.services.neoscorpion.current_existing_operational_sort_operations',
                       side_effect=lambda gateway,now=None:[db.session.get(SortDateOperation,op_id)]):
                with Fixture.app.app_context():
                    gateway=ensure_default_gateway_and_nodes(); actor=db.session.get(User,actor_id)
                    for index,(mid,aid,tail) in enumerate(targets):
                        at=now-timedelta(minutes=30-index*8)
                        service.confirm_fueler_truck_on(gateway,actor,aid,expected_cycle=1,expected_tail=tail,
                            expected_truck_id=truck_id,now_utc=at)
                        service.save_fueler_entry(gateway,actor,dict(assignment_id=aid,apu_running='no',
                            remaining_left='6',remaining_ctr='0',remaining_right='6',actual_left='15',
                            actual_ctr='0',actual_right='15',transfer_fuel_gallons='3000'),now_utc=at+timedelta(minutes=1))
                        service.mark_fueler_off(gateway,actor,aid,now_utc=at+timedelta(minutes=6))
                        db.session.commit()
                        baseline=service.fueler_context(gateway,actor,assignment_id=aid)['rows'][0]['edit_baseline']
                        service.submit_fueler_assignment(gateway,actor,aid,expected=baseline,now_utc=at+timedelta(minutes=6,seconds=30))
                        db.session.commit()
                browser=Fixture.pw.chromium.launch()
                page=browser.new_page(viewport={'width':1440,'height':900})
                page.add_init_script("window.__dispatchSaved=sessionStorage.getItem('neoapps.neoscorpion.fuel-dispatch.scroll.v3')")
                errors=[]; page.on('pageerror',lambda error:errors.append(str(error)))
                Fixture().login(page); page.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1','seen')")
                Fixture().ready(page,'/neoscorpion/fuel-dispatch')
                for index,mode in enumerate(('arrival','etd','arrival')):
                    mid,aid,tail=targets[index]
                    if index==2: page.set_viewport_size({'width':390,'height':844})
                    page.locator(f'[data-dispatch-sort-mode="{mode}"]').click()
                    page.wait_for_timeout(100)  # finish intentional-sort frame restoration
                    row=page.locator(f'tr[data-dispatch-mission-id="{mid}"]')
                    wrap=page.locator('.neoscorpion-table-wrap--sticky')
                    wrap.scroll_into_view_if_needed()
                    # Keep the real click below the existing truck-control strip,
                    # not at its overlapping lower edge. Still midway in a long roster.
                    wrap.evaluate('(el,id)=>{const row=el.querySelector(`[data-dispatch-mission-id="${id}"]`);const offset=el.querySelector("thead").getBoundingClientRect().height+120;el.scrollTop+=row.getBoundingClientRect().top-el.getBoundingClientRect().top-offset;el.scrollLeft=50;}',mid)
                    before=wrap.evaluate('el=>({top:el.scrollTop,left:el.scrollLeft})')
                    if index<2:
                        page.screenshot(path=str(Fixture.evidence/f'completion-profile-before-{index}.png'))
                        box=row.get_by_role('button',name='COMPLETE',exact=True).bounding_box()
                        self.assertIsNotNone(box)
                        self.assertTrue(0 < box['y']+box['height']/2 < 900)
                        self.assertTrue(0 < box['x']+box['width']/2 < 1440)
                        self.assertEqual(page.evaluate('(p)=>document.elementFromPoint(p.x+p.width/2,p.y+p.height/2)?.textContent.trim()',box),'COMPLETE')
                        with page.expect_response(lambda r:r.request.method=='POST' and r.url.endswith('/fuel-dispatch/complete')):
                            page.mouse.click(box['x']+box['width']/2,box['y']+box['height']/2)
                    else:
                        # Another authorized dispatcher completes: exercise live
                        # replacement on mobile (its action column is hidden).
                        token=page.locator('input[name=csrf_token]').first.get_attribute('value')
                        response=page.request.post(Fixture.origin+'/neoscorpion/fuel-dispatch/complete',
                            form={'assignment_id':str(aid),'csrf_token':token})
                        self.assertEqual(response.status,200)
                    expect(row).to_have_class(__import__('re').compile('is-complete'),timeout=15000)
                    page.wait_for_timeout(200)
                    after=wrap.evaluate('el=>({top:el.scrollTop,left:el.scrollLeft})')
                    self.assertAlmostEqual(after['top'],before['top'],delta=3,
                        msg=str((index, errors, page.evaluate('window.__dispatchSaved'))))
                    self.assertAlmostEqual(after['left'],before['left'],delta=1)
                    if index<2:
                        self.assertTrue(row.evaluate('el=>el.getBoundingClientRect().top>el.closest(".neoscorpion-table-wrap--sticky").getBoundingClientRect().bottom'))
                    # At mobile index 10, the bottom can naturally be visible;
                    # the exact scroll position still must not follow completion.
                    self.assertTrue(wrap.evaluate('el=>Array.from(el.querySelectorAll("tr[data-dispatch-mission-id]:not(.is-complete)")).some(r=>r.getBoundingClientRect().bottom>el.getBoundingClientRect().top&&r.getBoundingClientRect().top<el.getBoundingClientRect().bottom)'))
                    states=wrap.locator('tr[data-dispatch-mission-id]').evaluate_all('rows=>rows.map(r=>r.classList.contains("is-complete"))')
                    self.assertEqual(states,sorted(states))
                    self.assertFalse(page.evaluate('!!document.activeElement.closest(".is-complete")'))
                    page.reload(); page.wait_for_timeout(150)
                    self.assertAlmostEqual(wrap.evaluate('el=>el.scrollTop'),after['top'],delta=3)
                    self.assertFalse(page.evaluate('document.documentElement.scrollWidth>innerWidth+1'))
                self.assertFalse(errors,errors)
        finally:
            if browser: browser.close()
            Fixture.tearDownClass()
