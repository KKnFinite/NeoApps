"""Phone column contracts, compact clocks and real Scorpion -> Rain live progress."""
import json
import unittest
from datetime import datetime,timedelta
from pathlib import Path
from unittest.mock import patch
from playwright.sync_api import expect
from app.extensions import db
from app.models import (SortDateOperation,SortDateMission,User,NeoScorpionFuelAssignment,
    NeoScorpionFuelTruck,NeoScorpionSortTruck,NeoScorpionSortFueler,NeoScorpionSettings,
    NeoScorpionTailFuelState,SortDateParkingAssignment)
from app.services import neoscorpion as fuel
from tests.browser import test_neorain_compact_overrides as base


class RainBoardCleanupBrowserTest(unittest.TestCase):
    def test_chromium(self):self.exercise('chromium')
    def test_webkit(self):self.exercise('webkit')

    def exercise(self,engine):
        host=base.CompactRainBrowserTest;host.setUpClass();browser=None
        fixture=host.fixture; evidence=Path('instance/browser-evidence/rain-board-cleanup');evidence.mkdir(parents=True,exist_ok=True)
        geometry={};errors=[]
        try:
            host.stack.enter_context(patch('app.services.neoscorpion.current_existing_operational_sort_operations',
                side_effect=lambda gateway,now=None:[db.session.get(SortDateOperation,host.operation_id)]))
            with fixture.app.app_context():
                op=db.session.get(SortDateOperation,host.operation_id);actor=User.query.filter_by(username='drawer-admin').one()
                departure=db.session.get(SortDateMission,host.ids['departure']);arrival=db.session.get(SortDateMission,host.ids['arrival'])
                # Distinct canonical missions even though the physical tail matches.
                departure.assigned_tail_number=arrival.assigned_tail_number='N491UP'
                departure.planned_fuel_load=25000;arrival.arrival_status='arrived'
                departure.ramp_load_completed_at_utc=datetime.utcnow()-timedelta(minutes=20)
                departure.crew_load_completed_at_utc=datetime.utcnow()-timedelta(minutes=10)
                truck=NeoScorpionFuelTruck(gateway_id=op.gateway_id,truck_number='415710',capacity_gallons=20000)
                db.session.add(truck);db.session.flush()
                assignment=NeoScorpionFuelAssignment(sort_date_operation_id=op.id,sort_date_mission_id=departure.id,
                    assigned_fueler_user_id=actor.id,assigned_truck_id=truck.id,confirmed_tail_number='N491UP')
                db.session.add_all([assignment,NeoScorpionSettings(gateway_id=op.gateway_id),
                    NeoScorpionSortTruck(sort_date_operation_id=op.id,fuel_truck_id=truck.id,status='available',starting_gallons=10000,current_gallons=10000),
                    NeoScorpionSortFueler(sort_date_operation_id=op.id,user_id=actor.id),
                    NeoScorpionTailFuelState(sort_date_operation_id=op.id,tail_number='N491UP',inbound_fuel_lbs=12000),
                    SortDateParkingAssignment(sort_date_operation_id=op.id,tail_number='N491UP',ramp_code='C',position_code='C07',lane_number=1)])
                db.session.commit();aid,tid,uid=assignment.id,truck.id,actor.id
            browser=getattr(fixture.pw,engine).launch();page=browser.new_page(viewport={'width':1920,'height':1080})
            page.on('pageerror',lambda error:errors.append(str(error)));fixture().login(page)
            for width in (1920,1440,390,375):
                page.set_viewport_size({'width':width,'height':900 if width>900 else 844});geometry[str(width)]={}
                for board,kind,count in (('inbound','arrival',7),('outbound','departure',8)):
                    fixture().ready(page,'/neorain/'+board)
                    self.assertFalse(page.evaluate('document.documentElement.scrollWidth>innerWidth+1'))
                    root=page.locator(f'[data-neorain-board-page="{board}"]')
                    if width>900:
                        table=root.locator('.neorain-desktop-board');expect(table).to_be_visible()
                        expect(root.locator('.neorain-mobile-board')).to_be_hidden()
                        row=table.locator(f'[data-neorain-{board}-row]').first
                        geometry[str(width)][board]=row.evaluate('el=>({height:el.getBoundingClientRect().height,font:getComputedStyle(el.cells[1]).fontFamily,size:getComputedStyle(el.cells[1]).fontSize})')
                        self.assertEqual(table.locator('thead th').count(),13 if board=='inbound' else 21)
                        if board=='inbound':
                            clocks=row.locator('.neorain-arrival-clocks');expect(clocks).to_be_visible()
                            self.assertNotRegex(clocks.inner_text(),r'\b(?:STA|ETA)\b')
                            boxes=clocks.locator('.rain-override,.neorain-eta').evaluate_all('els=>els.map(el=>{const r=el.getBoundingClientRect();return r.y+r.height/2;})')
                            self.assertLess(max(boxes)-min(boxes),1)
                            self.assertEqual(table.locator('th.neorain-arrival-clocks-heading').evaluate('e=>getComputedStyle(e).textAlign'),'center')
                            sta=clocks.locator('[data-rain-override-time]')
                            with page.expect_response(lambda r:'/neorain/override' in r.url and r.request.method=='POST') as response:sta.fill('0235')
                            self.assertEqual(response.value.status,200);expect(sta).to_have_value('02:35')
                    else:
                        mobile=root.locator('.neorain-mobile-board');expect(mobile).to_be_visible()
                        expected=['FLIGHT','TAIL','ORIGIN','PARKING','ETA','BLOCK-IN','+/-'] if board=='inbound' else ['FLIGHT','TAIL','DEST','PARKING','NEO FUEL','CENTER','BLOCK-OUT','+/-']
                        self.assertEqual(mobile.locator('[role=columnheader]').all_text_contents(),expected)
                        row=mobile.locator('.neorain-mobile-mission-grid');self.assertEqual(row.locator(':scope > *').count(),count)
                        centers=mobile.locator('[role=columnheader]').evaluate_all('els=>els.map(e=>{const b=e.getBoundingClientRect();return b.x+b.width/2})')
                        cells=row.locator(':scope > *').evaluate_all('els=>els.map(e=>{const b=e.getBoundingClientRect();return b.x+b.width/2})')
                        for left,right in zip(centers,cells):self.assertAlmostEqual(left,right,delta=1)
                        row.locator('[data-neorain-mobile-row-open]').click()
                        detail=mobile.locator('[data-neorain-mobile-inline-detail]');expect(detail).to_be_visible()
                        if board=='inbound':expect(detail.locator('[data-rain-override-field=sta]')).to_be_visible()
                        else:
                            expect(detail.locator('[data-rain-override-field=ramp_load_complete]')).to_be_visible()
                            expect(detail.locator('[data-rain-override-field=crew_load_complete]')).to_be_visible()
                            expect(detail.locator('[data-rain-override-field=included]')).to_be_visible()
                        row.locator('[data-neorain-mobile-row-open]').click()
                        field='block_in' if board=='inbound' else 'official_block_out'
                        editor=row.locator(f'[data-rain-override-field={field}] [data-rain-override-time]')
                        editor.scroll_into_view_if_needed()
                        with page.expect_response(lambda r:'/neorain/override' in r.url and r.request.method=='POST') as response:editor.fill('0236')
                        self.assertEqual(response.value.status,200,response.value.text());expect(editor).to_have_value('02:36')
                        self.assertFalse(page.evaluate('document.documentElement.scrollWidth>innerWidth+1'))
                        geometry[str(width)][board]={'height':row.bounding_box()['height'],'columns':count}
                    if width<=900:
                        page.screenshot(path=str(evidence/f'{engine}-{board}-{width}-editing.png'),full_page=True)
                        mobile.evaluate('el=>el.scrollLeft=0')
                    page.screenshot(path=str(evidence/f'{engine}-{board}-{width}.png'),full_page=True)
                if width>900:
                    self.assertAlmostEqual(geometry[str(width)]['inbound']['height'],geometry[str(width)]['outbound']['height'],delta=2)
                    self.assertEqual(geometry[str(width)]['inbound']['size'],geometry[str(width)]['outbound']['size'])
            # Real operational mutations only in the disposable fixture. Rain must
            # observe its exact mission without a manual reload or invented fuel.
            page.set_viewport_size({'width':390,'height':844});fixture().ready(page,'/neorain/outbound')
            cell=page.locator('.neorain-mobile-mission-grid [data-neorain-scorpion-fuel]')
            expect(cell).to_have_text('ASSIGNED')
            def mutate(action):
                with fixture.app.app_context():
                    op=db.session.get(SortDateOperation,host.operation_id);actor=db.session.get(User,uid)
                    if action=='on':fuel.confirm_fueler_truck_on(op.gateway,actor,aid,expected_cycle=1,expected_tail='N491UP',expected_truck_id=tid)
                    elif action=='data':fuel.save_fueler_entry(op.gateway,actor,dict(assignment_id=aid,apu_running='no',remaining_left='6',remaining_ctr='0',remaining_right='6',actual_left='15',actual_ctr='0',actual_right='15',transfer_fuel_gallons='3000'))
                    elif action=='off':fuel.mark_fueler_off(op.gateway,actor,aid)
                    else:
                        row=fuel.fueler_context(op.gateway,actor)['rows'][0]
                        fuel.submit_fueler_assignment(op.gateway,actor,aid,expected=row['edit_baseline'])
                        fuel.complete_fueled_assignment(op.gateway,actor,aid)
                    db.session.commit()
            mutate('on');expect(cell).to_have_text('FUELING',timeout=15000)
            mutate('data');expect(cell).to_have_text('FUELING')
            mutate('off');expect(cell).to_have_text('OFF',timeout=15000)
            mutate('complete');expect(cell).to_have_text('30.0',timeout=15000)
            expect(cell).to_have_attribute('data-scorpion-final','true')
            self.assertEqual(errors,[])
        finally:
            (evidence/f'{engine}-geometry.json').write_text(json.dumps(geometry,indent=2))
            if browser:browser.close()
            host.tearDownClass()
