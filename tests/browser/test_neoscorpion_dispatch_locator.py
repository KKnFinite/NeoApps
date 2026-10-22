"""Real Dispatch/Board locator and TOP OFF paint; no production connections."""
import json
import os
import re
import unittest
from datetime import date, datetime, timedelta
from unittest.mock import patch

from playwright.sync_api import expect
from app.extensions import db
from app.models import (User, SortDateOperation, SortDateMission, NeoScorpionFuelAssignment,
    NeoScorpionFuelCycleHistory, NeoScorpionFuelTruck, NeoScorpionSortTruck,
    NeoScorpionTopOffEvent, NeoScorpionSortAssetState, SortDateParkingAssignment)
from app.services.access_control import ensure_default_gateway_and_nodes
from tests.browser import test_mobile_drawer as fixture_module


class DispatchLocatorBrowserTest(unittest.TestCase):
    def test_chromium(self): self.exercise('chromium')
    def test_webkit(self): self.exercise('webkit')

    def exercise(self, engine):
        Fixture = fixture_module.MobileDrawerBrowserTest
        Fixture.setUpClass()
        Fixture.app.config['LIVE_SCREEN_REFRESH_INTERVAL_MS'] = 1000
        browser = None
        baseline = os.environ.get('SCORPION_LOCATOR_BASELINE') == '1'
        metrics = []
        try:
            with Fixture.app.app_context():
                gateway = ensure_default_gateway_and_nodes()
                actor = User.query.filter_by(username='drawer-admin').one()
                at = datetime.utcnow().replace(microsecond=0)
                op = SortDateOperation(gateway_id=gateway.id, gateway_code=gateway.code,
                    generated_by_user_id=actor.id, sort_date=date.today(), sort_name='night', window_minutes=60)
                db.session.add(op); db.session.flush(); oid = op.id
                db.session.add(NeoScorpionSortAssetState(sort_date_operation_id=oid, revision=1))
                truck = NeoScorpionFuelTruck(gateway_id=gateway.id, truck_number='415710', capacity_gallons=20000)
                db.session.add(truck); db.session.flush(); tid = truck.id
                sent_at = at - timedelta(hours=1)
                db.session.add(NeoScorpionSortTruck(sort_date_operation_id=oid, fuel_truck_id=tid,
                    status='topping_off', starting_gallons=8000, current_gallons=8000,
                    inventory_verified=True, top_off_sent_at_utc=sent_at))
                db.session.add(NeoScorpionTopOffEvent(sort_date_operation_id=oid, fuel_truck_id=tid,
                    kind='sent', occurred_at_utc=sent_at, inventory_verified=True, inventory_epoch=0,
                    spear_predicted_return_at_utc=at-timedelta(minutes=15)))
                ids = []
                for i in range(3):
                    mission = SortDateMission(sort_date_operation_id=oid, sort_date=op.sort_date,
                        gateway_code=gateway.code, sort_name='night', mission_type='departure',
                        mission_source='manual', flight_number=f'UPS90{i}', assigned_tail_number=f'N49{i}UP',
                        origin=gateway.code, destination='SDF' if i < 2 else 'MSP',
                        timezone='America/Chicago', planned_datetime_utc=at+timedelta(hours=i+1),
                        planned_fuel_load=25000)
                    db.session.add(mission); db.session.flush(); ids.append(mission.id)
                    db.session.add(SortDateParkingAssignment(sort_date_operation_id=oid,
                        tail_number=mission.assigned_tail_number,ramp_code='C',position_code=f'{7+i:02}',lane_number=1))
                    assignment = NeoScorpionFuelAssignment(sort_date_operation_id=oid,
                        sort_date_mission_id=mission.id, current_cycle_number=2 if i == 0 else 1)
                    db.session.add(assignment); db.session.flush()
                    if i == 0:
                        db.session.add(NeoScorpionFuelCycleHistory(sort_date_operation_id=oid,
                            fuel_assignment_id=assignment.id, mission_id=mission.id, cycle_number=1,
                            label='UPLIFT', snapshot=dict(tail_number='N490UP', tank_rows=[])))
                db.session.commit()
            with patch('app.services.neoscorpion.current_existing_operational_sort_operations',
                       side_effect=lambda gateway, now=None: [db.session.get(SortDateOperation, oid)]):
                browser = getattr(Fixture.pw, engine).launch()
                page = browser.new_page(viewport={'width':1440, 'height':1000}, service_workers='block')
                errors = []; page.on('pageerror', lambda e: errors.append(str(e)))
                Fixture().login(page)
                page.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1','seen')")
                Fixture().ready(page, '/neoscorpion/fuel-dispatch')
                toolbar = page.locator('.neoscorpion-dispatch-sort')
                card = page.locator(f'[data-dispatch-truck-id="{tid}"]')
                for board in (False, True):
                    if board: page.locator('[data-operational-board-toggle]:visible').click()
                    for width in (320,375,390,430,768,1024,1440):
                        page.set_viewport_size({'width':width,'height':1000})
                        toolbar.scroll_into_view_if_needed()
                        box = toolbar.evaluate("""el => ({width:innerWidth, height:el.getBoundingClientRect().height,
                            overflow:document.documentElement.scrollWidth-innerWidth,
                            children:[...el.children].filter(e=>e.getBoundingClientRect().width).map(e=>{
                                const r=e.getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom};})})""")
                        box['board'] = board; metrics.append(box)
                        if not baseline:
                            expect(page.locator('[data-dispatch-search]')).to_be_visible()
                            page.locator('[data-dispatch-search]').fill('n490')
                            expect(page.locator('.neoscorpion-dispatch-primary-row.is-search-match')).to_have_count(1)
                            page.locator('[data-dispatch-search-clear]').click()
                            self.assertLessEqual(box['overflow'],1)
                            self.assertLessEqual(box['height'],32)
                            self.assertTrue(all(c['right'] <= width+1 for c in box['children']))
                            self.assertLessEqual(max(c['bottom'] for c in box['children'])-min(c['top'] for c in box['children']),28.5)
                        if width in (375,768,1440):
                            toolbar.screenshot(path=str(Fixture.evidence/f'locator-{engine}-{board}-{width}-{"before" if baseline else "after"}.png'))
                    page.set_viewport_size({'width':1440,'height':1000})
                if baseline:
                    card.screenshot(path=str(Fixture.evidence/f'top-off-{engine}-before.png'))
                    return
                search = page.locator('[data-dispatch-search]')
                rows = page.locator('.neoscorpion-dispatch-primary-row')
                order = rows.evaluate_all('els=>els.map(e=>e.dataset.dispatchMissionId)')
                for query, count in (('n490',1),('uPs90',3),('sdf',2),('MSP',1),('C07',1),('25.0',3),('pending',3),('no matches',0)):
                    search.fill(query)
                    expect(page.locator('.neoscorpion-dispatch-primary-row.is-search-match')).to_have_count(count)
                    expect(page.locator('.neoscorpion-dispatch-primary-row.is-search-dim')).to_have_count(3-count)
                    self.assertEqual(rows.evaluate_all('els=>els.map(e=>e.dataset.dispatchMissionId)'),order)
                search.fill('N490')
                page.locator('.neoscorpion-table-wrap--dispatch').screenshot(path=str(Fixture.evidence/f'locator-{engine}-matching-group.png'))
                group = page.locator(f'tr[data-dispatch-sort-group="{ids[0]}"]')
                self.assertGreaterEqual(group.count(),4)
                self.assertTrue(group.evaluate_all("els=>els.every(e=>e.classList.contains('is-search-match'))"))
                # Dimmed missions remain interactive; details may open normally.
                dim = page.locator('.neoscorpion-dispatch-primary-row.is-search-dim').first
                dim.locator('[data-neoscorpion-dispatch-details]').click()
                detail_id = dim.locator('[data-neoscorpion-dispatch-details]').get_attribute('aria-controls')
                expect(page.locator('#'+detail_id)).to_be_visible()
                page.locator('[data-dispatch-sort-mode="arrival"]').click()
                expect(search).to_have_value('N490')
                search.focus()
                with Fixture.app.app_context():
                    db.session.get(SortDateMission,ids[0]).destination='DEN'
                    db.session.query(NeoScorpionSortAssetState).filter_by(sort_date_operation_id=oid).one().revision += 1
                    db.session.commit()
                expect(page.locator(f'.neoscorpion-dispatch-primary-row[data-dispatch-mission-id="{ids[0]}"]')).to_contain_text('DEN',timeout=20000)
                expect(search).to_be_focused(); expect(search).to_have_value('N490')
                expect(page.locator('#'+detail_id)).to_be_visible()
                page.locator('[data-dispatch-search-clear]').click()
                expect(search).to_have_value('')
                expect(page.locator('tr.is-search-dim, tr.is-search-match')).to_have_count(0)
                expect(card).to_have_class(re.compile('is-top-off-overdue'))
                card.scroll_into_view_if_needed()
                # Re-estimation, missing/invalid values and crossing ETA without
                # a poll/reload. These change DOM fixture data only, never history.
                saved_eta = card.get_attribute('data-top-off-return-utc')
                card.evaluate("""el=>{el.dataset.topOffReturnUtc=new Date(Date.now()+60000).toISOString();
                    document.dispatchEvent(new Event('visibilitychange'));}""")
                expect(card).not_to_have_class(re.compile('is-top-off-overdue'))
                for invalid in ('', 'invalidZ', '23:59'):
                    card.evaluate("""(el,value)=>{el.dataset.topOffReturnUtc=value;
                        document.dispatchEvent(new Event('visibilitychange'));}""",invalid)
                    expect(card).not_to_have_class(re.compile('is-top-off-overdue'))
                card.evaluate("""el=>{el.dataset.topOffReturnUtc=new Date(Date.now()+300).toISOString();
                    document.dispatchEvent(new Event('visibilitychange'));}""")
                expect(card).not_to_have_class(re.compile('is-top-off-overdue'))
                expect(card).to_have_class(re.compile('is-top-off-overdue'),timeout=3000)
                card.evaluate("""(el,value)=>{el.dataset.topOffReturnUtc=value;
                    document.dispatchEvent(new Event('visibilitychange'));}""",saved_eta)
                for view in ('board','normal'):
                    if view == 'normal': page.locator('[data-operational-board-toggle]:visible').click()
                    for time, label in ((0,'normal'),(1500,'yellow')):
                        paint = card.evaluate("""async (el,time)=>{const a=el.getAnimations().find(a=>a.animationName==='scorpion-top-off-overdue');
                            await a.ready;a.pause();await a.ready;a.currentTime=time+a.effect.getTiming().delay+3000;
                            await new Promise(requestAnimationFrame);
                            await new Promise(requestAnimationFrame);
                            return {color:getComputedStyle(el).backgroundColor,currentTime:a.currentTime,
                                timing:a.effect.getComputedTiming(),frames:a.effect.getKeyframes(),state:a.playState};}""",time)
                        metrics.append({'pulse':label,'view':view,**paint})
                        card.screenshot(path=str(Fixture.evidence/f'top-off-{engine}-{view}-{label}.png'))
                    self.assertNotEqual(metrics[-1]['color'],metrics[-2]['color'])
                    self.assertEqual(metrics[-2]['color'],'rgba(0, 0, 0, 0)')
                card.evaluate("el=>el.classList.add('is-short')")
                self.assertEqual(card.evaluate("el=>getComputedStyle(el).animationName"),'none')
                card.evaluate("el=>el.classList.remove('is-short')")
                page.emulate_media(reduced_motion='reduce')
                self.assertEqual(card.evaluate("el=>getComputedStyle(el).animationName"),'none')
                self.assertNotEqual(card.evaluate("el=>getComputedStyle(el).backgroundColor"),'rgba(0, 0, 0, 0)')
                page.emulate_media(reduced_motion='no-preference')
                # Real RETURN uses the existing route, and removes warning promptly.
                card.locator('[name="current_gallons"]').fill('12000')
                card.get_by_role('button',name='RETURN',exact=True).click()
                expect(card).to_have_attribute('data-truck-operational-status','available',timeout=20000)
                expect(card).not_to_have_class(re.compile('is-top-off-overdue'))
                page.reload(); expect(page.locator('[data-dispatch-search]')).to_have_value('')
                expect(card).not_to_have_class(re.compile('is-top-off-overdue'))
                self.assertEqual(errors,[])
        finally:
            Fixture.evidence.joinpath(f'locator-{engine}-{"before" if baseline else "after"}.json').write_text(json.dumps(metrics,indent=2))
            if browser: browser.close()
            Fixture.tearDownClass()
