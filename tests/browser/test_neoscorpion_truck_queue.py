"""Real five-truck advance planning and independent cross-device verification."""
from datetime import date,datetime,timedelta
from pathlib import Path
import re
import os
import json
import subprocess
import unittest
from unittest.mock import patch
from playwright.sync_api import expect
from app.extensions import db
from app.models import (User,PortalAppAccess,SortDateOperation,SortDateMission,SortDateParkingAssignment,
    NeoScorpionFuelAssignment,NeoScorpionFuelTruck,NeoScorpionSortTruck,NeoScorpionSortFueler,
    NeoScorpionFuelWorkState,NeoScorpionFuelingEvent)
from app.services.access_control import ensure_default_gateway_and_nodes,backfill_default_gateway_node_roles
from app.services.password_policy import set_user_password
from tests.browser import test_mobile_drawer as fixtures


class TruckQueueBrowserTest(unittest.TestCase):
    def test_chromium_webkit_five_advance_trucks_live_handoffs_board_and_mobile(self):
        F=fixtures.MobileDrawerBrowserTest;F.setUpClass()
        F.app.config['LIVE_SCREEN_REFRESH_INTERVAL_MS']=1000
        evidence=Path('instance/browser-evidence/truck-rows');evidence.mkdir(parents=True,exist_ok=True)
        baseline=os.environ.get('NEO_TRUCK_ROWS_CAPTURE_BASELINE')=='1'
        measurements=[]
        if baseline:
            from jinja2 import ChoiceLoader,DictLoader
            before='29b69e59811f698fcb534db89aac19c1ecf993d6'
            names=['_dispatch_truck_lines.html','_dispatch_visit.html','_dispatch_mobile_details.html']
            original={f'neonodes/neoscorpion/{name}':subprocess.check_output(['git','show',f'{before}:app/templates/neonodes/neoscorpion/{name}']).decode('utf-8') for name in names}
            F.app.jinja_env.loader=ChoiceLoader([DictLoader(original),F.app.jinja_env.loader])
            original_css=subprocess.check_output(['git','show',before+':app/static/css/26-neoscorpion.css'])
        try:
            with F.app.app_context():
                g=ensure_default_gateway_and_nodes();d=User.query.filter_by(username='drawer-admin').one()
                u=User(username='queue-fueler',email='queue-fueler@example.test',role='watcher',email_verified_at=datetime.now())
                set_user_password(u,'BrowserFixture123!');db.session.add(u);db.session.flush();uid=u.id
                backfill_default_gateway_node_roles(u,role='operator')
                access=PortalAppAccess.query.filter_by(user_id=uid,app_code='neogateway').one()
                access.status='approved';access.role='operator';access.is_active=True
                op=SortDateOperation(generated_by_user_id=d.id,gateway_id=g.id,gateway_code=g.code,sort_date=date.today(),sort_name='night',window_minutes=60)
                db.session.add(op);db.session.flush();oid=op.id
                db.session.add(NeoScorpionSortFueler(sort_date_operation_id=oid,user_id=uid));cases=[]
                for i in range(2):
                    trucks=[]
                    for number in range(2):
                        t=NeoScorpionFuelTruck(gateway_id=g.id,truck_number=f'Q{i}{number}',capacity_gallons=20000)
                        db.session.add(t);db.session.flush();trucks.append(t.id)
                        db.session.add(NeoScorpionSortTruck(sort_date_operation_id=oid,fuel_truck_id=t.id,status='available',starting_gallons=10000,current_gallons=10000))
                    m=SortDateMission(sort_date_operation_id=oid,sort_date=op.sort_date,gateway_code=g.code,sort_name='night',mission_type='departure',mission_source='manual',flight_number=f'UPS{800+i}',origin=g.code,destination='MSP',assigned_tail_number=f'N49{i+1}UP',planned_datetime_utc=datetime.utcnow()+timedelta(hours=2),timezone='America/Chicago',planned_fuel_load=60000)
                    db.session.add(m);db.session.flush()
                    db.session.add(SortDateParkingAssignment(sort_date_operation_id=oid,tail_number=m.assigned_tail_number,ramp_code='C',position_code=f'{7+i:02}',lane_number=1))
                    a=NeoScorpionFuelAssignment(sort_date_operation_id=oid,sort_date_mission_id=m.id,assigned_fueler_user_id=uid,assigned_truck_id=trucks[0],confirmed_tail_number=m.assigned_tail_number)
                    db.session.add(a);db.session.flush();cases.append((a.id,m.id,trucks))
                db.session.commit()
            with patch('app.services.neoscorpion.current_existing_operational_sort_operations',side_effect=lambda g,now=None:[db.session.get(SortDateOperation,oid)]):
                for engine,(aid,mid,trucks) in zip((F.pw.chromium,F.pw.webkit),cases):
                    browser=engine.launch();errors=[]
                    try:
                        desktop=browser.new_context(viewport={'width':1440,'height':900},service_workers='block')
                        mobile=browser.new_context(viewport={'width':390,'height':844},service_workers='block')
                        if baseline:desktop.route('**/static/css/26-neoscorpion.css*',lambda r:r.fulfill(body=original_css,content_type='text/css'))
                        dispatch=desktop.new_page();page=mobile.new_page()
                        for p in (dispatch,page):p.on('pageerror',lambda error:errors.append(str(error)))
                        F().login(dispatch);F().login(page,'queue-fueler')
                        dispatch.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1','seen')")
                        F().ready(dispatch,'/neoscorpion/fuel-dispatch');F().ready(page,'/neoscorpion/fueler')
                        row=dispatch.locator(f'[data-dispatch-mission-id="{mid}"]')
                        row.locator('[data-neoscorpion-dispatch-details]').click()
                        detail=dispatch.locator(f'#neoscorpion-dispatch-detail-{mid}-1')
                        for position,truck in enumerate((trucks[1],trucks[0],trucks[1],trucks[0]),1):
                            operation=detail.locator('[data-console-section="second-truck"]')
                            if not operation.evaluate('n=>n.open'):operation.locator('summary').click()
                            form=operation.locator('form');form.locator('[name=proposed_truck_id]').select_option(str(truck))
                            form.get_by_role('button',name='PLAN ANOTHER TRUCK',exact=True).click()
                            plans=dispatch.locator(f'[data-truck-lines-mission="{mid}"] [data-truck-line-plan]' if baseline else f'tr[data-truck-lines-mission="{mid}"][data-truck-line-plan]')
                            expect(plans).to_have_count(position,timeout=12000)
                        measurements.append({'engine':engine.name,'view':'four-plans','height':sum(dispatch.locator(f'[data-truck-lines-mission="{mid}"]').evaluate_all('ns=>ns.map(n=>n.getBoundingClientRect().height)'))})
                        dispatch.screenshot(path=str(evidence/f'{"before" if baseline else "after"}-{engine.name}-four-plans.png'),full_page=True)
                        if not baseline:
                            self.assertEqual(dispatch.locator('.scorpion-truck-lines,.scorpion-truck-line-readings').count(),0)
                            for line in dispatch.locator(f'tr[data-truck-lines-mission="{mid}"]').all():
                                self.assertEqual(line.locator(':scope > td').count(),16)
                                self.assertEqual(line.locator('form,[data-visit-evidence]').count(),0)
                                expect(line.locator('small.neoscorpion-dispatch-mobile-only')).to_be_hidden()
                                geometry=line.evaluate("""n=>({cells:Array.from(n.cells,c=>[c.getBoundingClientRect().x,c.getBoundingClientRect().width]),
                                    headers:Array.from(n.closest('table').tHead.rows[0].cells,c=>[c.getBoundingClientRect().x,c.getBoundingClientRect().width])})""")
                                for actual,header in zip(geometry['cells'],geometry['headers']):
                                    self.assertAlmostEqual(actual[0],header[0],delta=1)
                                    self.assertAlmostEqual(actual[1],header[1],delta=1)
                            # Normal and Board use identical table columns.
                            row.locator('[data-neoscorpion-dispatch-details]').click()
                            dispatch.locator('[data-operational-board-toggle]:visible').click()
                            expect(plans).to_have_count(4)
                            dispatch.screenshot(path=str(evidence/f'after-{engine.name}-board-active.png'),full_page=True)
                            for width in (390,375):
                                dispatch.set_viewport_size({'width':width,'height':844})
                                expect(plans.first).to_be_visible()
                                self.assertFalse(dispatch.evaluate('document.documentElement.scrollWidth>innerWidth+1'))
                                self.assertEqual(plans.first.locator(':scope > td:visible').count(),5)
                                self.assertEqual(dispatch.locator('[data-fuel-dispatch-live] button[type=submit]:visible').count(),0)
                                dispatch.screenshot(path=str(evidence/f'after-{engine.name}-active-{width}.png'),full_page=True)
                            plans.first.locator('[data-neoscorpion-mobile-details]').click()
                            expect(detail.locator('.scorpion-mobile-readonly')).to_be_visible()
                            self.assertEqual(detail.locator('.scorpion-mobile-readonly form').count(),0)
                            dispatch.set_viewport_size({'width':1440,'height':900})
                            expect(dispatch.locator('body')).to_have_class(re.compile('operational-board-view'))
                            dispatch.get_by_role('button',name='Exit Board View',exact=True).click()
                        visits=[]
                        for index in range(5):
                            card=page.locator('[data-fuel-data-card]').filter(has=page.locator(f'input[name=assignment_id][value="{aid}"]')).first
                            expect(card).to_have_attribute('data-physical-work-id',re.compile(r'^\d+$'),timeout=12000)
                            wid=card.get_attribute('data-physical-work-id');self.assertNotIn(wid,visits);visits.append(wid)
                            if index:
                                expect(card.locator('[name=remaining_left]')).to_have_value(f'{15+(index-1)*4}.0')
                                expect(card.locator('[name=actual_left]')).to_have_value('')
                            card.locator('[data-truck-on-form] button').click()
                            expect(card.locator('.neoscorpion-truck-on-row')).to_contain_text('CONFIRMED')
                            # Verify the preceding truck while this one is ON.
                            if index:
                                action=detail.locator(f'[data-action-key="verify-{visits[-2]}"]')
                                draft=None
                                if not baseline:
                                    earlier=dispatch.locator(f'tr[data-truck-line-work="{visits[-2]}"]')
                                    expect(earlier.locator('[data-visit-state]')).to_have_text('SUBMITTED',timeout=12000)
                                    if index==1:
                                        tail_section=detail.locator('details[data-console-section="tail-notes"]')
                                        if not tail_section.evaluate('n=>n.open'):tail_section.locator('summary').click()
                                        draft=tail_section.locator('textarea');saved_note=draft.input_value()
                                        draft.fill('Unsent Tail Notes survive visit verification')
                                    if detail.is_visible():row.locator('[data-neoscorpion-dispatch-details]').click()
                                    earlier.get_by_role('button',name='VERIFY',exact=True).click()
                                expect(action).to_be_visible(timeout=12000);action.locator('button').click()
                                expect(dispatch.locator(f'[data-truck-line-work="{visits[-2]}"] [data-visit-state]')).to_contain_text('COMPLETE',timeout=12000)
                                if not baseline and index==1:
                                    expect(draft).to_have_value('Unsent Tail Notes survive visit verification')
                                    earlier.locator('td:last-child button').click()
                                    recorded=detail.locator(f'.scorpion-desktop-console [data-console-visit="{visits[-2]}"]')
                                    expect(recorded.locator('[data-visit-evidence]')).to_have_attribute('open','')
                                    expect(recorded.locator('[role=table]')).to_contain_text('15,000')
                                    dispatch.screenshot(path=str(evidence/f'after-{engine.name}-earlier-complete-next-on.png'),full_page=True)
                                    draft.fill(saved_note)
                            card.locator('[name=apu_running]').select_option('no')
                            for code in ('left','ctr','right'):
                                if not index:card.locator(f'[name=remaining_{code}]').fill('0' if code=='ctr' else '6')
                                card.locator(f'[name=actual_{code}]').fill('0' if code=='ctr' else str(15+index*4))
                            card.locator('[name=transfer_fuel_gallons]').fill(str(100+index))
                            expect(card.locator('[data-fuel-data-status]')).to_have_text('Saved',timeout=15000)
                            card.locator('.neoscorpion-fueler-off-form button').click()
                            expect(card.locator('[data-fueler-submit-form] button')).to_be_enabled()
                            card.locator('[data-fueler-submit-form] button').click()
                            if index<4:expect(card).not_to_have_attribute('data-physical-work-id',wid,timeout=12000)
                        row.get_by_role('button',name='COMPLETE',exact=True).click()
                        expect(row).to_have_class(re.compile('is-complete'),timeout=12000)
                        lines=dispatch.locator(f'[data-truck-lines-mission="{mid}"]')
                        expect(lines.locator('[data-truck-line-work]') if baseline else lines).to_have_count(5 if baseline else 4)
                        self.assertEqual(lines.locator('[data-visit-state]').all_text_contents(),[text for text in lines.locator('[data-visit-state]').all_text_contents() if text.startswith('COMPLETE')])
                        dispatch.locator('[data-operational-board-toggle]:visible').click()
                        if baseline:expect(lines).to_be_visible()
                        else:
                            expect(lines.first).to_be_hidden()
                            self.assertEqual(lines.filter(has=dispatch.locator('[data-visit-state]')).locator('td:visible').count(),0)
                        measurements.append({'engine':engine.name,'view':'five-complete','height':sum(lines.evaluate_all('ns=>ns.map(n=>n.getBoundingClientRect().height)'))})
                        dispatch.screenshot(path=str(evidence/f'{"before" if baseline else "after"}-{engine.name}-board-five-complete.png'),full_page=True)
                        for width in (1920,1440,390,375):
                            dispatch.set_viewport_size({'width':width,'height':844})
                            self.assertFalse(dispatch.evaluate('document.documentElement.scrollWidth>innerWidth+1'))
                            if not baseline:expect(lines.first).to_be_hidden()
                            if width<901:self.assertEqual(dispatch.locator('[data-fuel-dispatch-live] button[type=submit]:visible').count(),0)
                        if not baseline:
                            if not detail.is_visible():row.locator('[data-neoscorpion-mobile-details]').click()
                            history=detail.locator('.scorpion-mobile-readonly [data-console-section="mobile-visits"]')
                            if not history.evaluate('n=>n.open'):history.locator('summary').first.click()
                            self.assertEqual(history.locator('[data-visit-evidence]').count(),5)
                            expect(history.locator('[data-console-visit]').first).to_contain_text('VERIFIED')
                        dispatch.screenshot(path=str(evidence/f'{"before" if baseline else "after"}-{engine.name}-mobile.png'),full_page=True)
                        with F.app.app_context():
                            self.assertEqual(NeoScorpionFuelWorkState.query.filter_by(fuel_assignment_id=aid).count(),5)
                            self.assertEqual(NeoScorpionFuelingEvent.query.filter_by(fuel_assignment_id=aid).count(),5)
                            self.assertIsNotNone(db.session.get(NeoScorpionFuelAssignment,aid).completed_at_utc)
                        if not baseline:
                            # Compare against a genuine existing START UPLIFT
                            # history row, created through the canonical service.
                            from app.services import neoscorpion as fuel
                            with F.app.app_context():
                                actor=User.query.filter_by(username='drawer-admin').one()
                                fuel.start_follow_up_fuel_cycle(ensure_default_gateway_and_nodes(),actor,aid,'uplift','70',uid,trucks[0])
                                db.session.commit()
                            dispatch.set_viewport_size({'width':1440,'height':900})
                            expect(row).to_have_attribute('data-dispatch-row-key',f'{mid}-2',timeout=12000)
                            reference=dispatch.locator(f'tr[data-cycle-history][data-dispatch-sort-group="{mid}"]')
                            expect(reference).to_have_count(1)
                            self.assertEqual(reference.locator(':scope > td').count(),16)
                            expect(dispatch.locator(f'[data-truck-lines-mission="{mid}"]')).to_have_count(0)
                            dispatch.screenshot(path=str(evidence/f'after-{engine.name}-uplift-reference.png'),full_page=True)
                        self.assertEqual(errors,[])
                    finally:browser.close()
            (evidence/f'{"before" if baseline else "after"}-measurements.json').write_text(json.dumps(measurements,indent=2))
        finally:F.tearDownClass()
