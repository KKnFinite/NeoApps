"""Actual two-level clicks, profile persistence, paint and live replacement."""
import re
import unittest
from datetime import date,datetime,timedelta
from unittest.mock import patch
from playwright.sync_api import expect
from app.extensions import db
from app.models import (User,SortDateOperation,SortDateMission,NeoScorpionFuelAssignment,
    NeoScorpionSortAssetState,NeoScorpionFuelCycleHistory,NeoScorpionFuelTruck,NeoScorpionSortTruck,NeoScorpionSortFueler)
from app.services.access_control import ensure_default_gateway_and_nodes
from tests.browser import test_mobile_drawer as fixtures


class DispatchProfileBrowserTest(unittest.TestCase):
    def test_chromium_profile_order_color_refresh_and_board(self):self.exercise('chromium')
    def test_webkit_profile_order_color_refresh_and_board(self):self.exercise('webkit')

    def exercise(self,engine):
        F=fixtures.MobileDrawerBrowserTest;F.setUpClass();browser=None
        F.app.config['LIVE_SCREEN_REFRESH_INTERVAL_MS']=1000
        try:
            with F.app.app_context():
                gateway=ensure_default_gateway_and_nodes();actor=User.query.filter_by(username='drawer-admin').one()
                at=datetime.utcnow().replace(microsecond=0)
                op=SortDateOperation(gateway_id=gateway.id,generated_by_user_id=actor.id,gateway_code=gateway.code,
                    sort_date=date.today(),sort_name='night',window_minutes=60)
                db.session.add(op);db.session.flush();op_id=op.id
                db.session.add(NeoScorpionSortAssetState(sort_date_operation_id=op.id,revision=1))
                db.session.add(NeoScorpionSortFueler(sort_date_operation_id=op.id,user_id=actor.id))
                actor_id=actor.id
                truck=NeoScorpionFuelTruck(gateway_id=gateway.id,truck_number='422808',capacity_gallons=10000)
                db.session.add(truck);db.session.flush()
                db.session.add(NeoScorpionSortTruck(sort_date_operation_id=op.id,fuel_truck_id=truck.id,
                    status='available',starting_gallons=8000,current_gallons=8000))
                ids=[];arrival_ids=[]
                # ETD order 0,2,1; arrival order 1,0,2. Complete: ETD 3,4 / arrival 4,3.
                for i,(etd,arr,status) in enumerate(((0,2,'scheduled'),(2,1,'arrived'),(1,3,'on_ground'),(3,5,'arrived'),(4,4,'arrived'))):
                    tail=f'N{490+i}UP'
                    mission=SortDateMission(sort_date_operation_id=op.id,sort_date=op.sort_date,gateway_code=gateway.code,
                        sort_name='night',mission_type='departure',mission_source='manual',flight_number=f'UPS90{i}',
                        assigned_tail_number=tail,origin=gateway.code,destination='SDF',timezone='America/Chicago',
                        planned_datetime_utc=at+timedelta(hours=etd+1),planned_fuel_load=25000,fuel_status='complete' if i>=3 else 'waiting')
                    db.session.add(mission);db.session.flush();ids.append(mission.id)
                    assignment=NeoScorpionFuelAssignment(sort_date_operation_id=op.id,sort_date_mission_id=mission.id,
                        review_status='complete' if i>=3 else 'pending',completed_at_utc=at if i>=3 else None)
                    db.session.add(assignment);db.session.flush()
                    if i==0:
                        assignment.current_cycle_number=2
                        db.session.add(NeoScorpionFuelCycleHistory(sort_date_operation_id=op.id,fuel_assignment_id=assignment.id,
                            mission_id=mission.id,cycle_number=1,label='TAIL SWAP',snapshot=dict(tail_number='OLDTAIL',tank_rows=[])))
                    arrival=SortDateMission(sort_date_operation_id=op.id,sort_date=op.sort_date,gateway_code=gateway.code,
                        sort_name='night',mission_type='arrival',mission_source='manual',flight_number=f'UPS80{i}',
                        assigned_tail_number=tail,origin='SDF',destination=gateway.code,arrival_status=status,
                        eta_datetime_utc=at-timedelta(hours=6)+timedelta(minutes=arr),
                        actual_block_in_datetime_utc=at-timedelta(hours=6)+timedelta(minutes=arr) if status=='arrived' else None)
                    db.session.add(arrival);db.session.flush();arrival_ids.append(arrival.id)
                db.session.commit()
            with patch('app.services.neoscorpion.current_existing_operational_sort_operations',
                       side_effect=lambda gateway,now=None:[db.session.get(SortDateOperation,op_id)]):
                browser=getattr(F.pw,engine).launch();page=browser.new_page(viewport={'width':1440,'height':900},service_workers='block')
                errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                F().login(page);page.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1','seen')")
                F().ready(page,'/neoscorpion/fuel-dispatch')
                rows=page.locator('tr[data-dispatch-mission-id]:not([data-cycle-history])')
                order=lambda:rows.evaluate_all('els=>els.map(el=>Number(el.dataset.dispatchMissionId))')
                row=lambda i:page.locator(f'tr[data-dispatch-mission-id="{ids[i]}"]:not([data-cycle-history])')
                def select(selector):
                    page.locator(selector).click()
                    expect(page.locator('[data-dispatch-preference-status]').first).to_have_text('SAVED')
                self.assertEqual(order(),[ids[2],ids[1],ids[0],ids[3],ids[4]])
                for i,color,weight in ((0,'rgb(213, 216, 222)','400'),(1,'rgb(143, 255, 168)','700'),(2,'rgb(255, 240, 176)','700')):
                    for text in row(i).locator('.neoscorpion-dispatch-arrival-text').all():
                        self.assertEqual(text.evaluate('el=>getComputedStyle(el).color'),color)
                        self.assertEqual(text.evaluate('el=>getComputedStyle(el).fontWeight'),weight)
                select('[data-dispatch-arrived-first="0"]')
                self.assertEqual(order(),[ids[0],ids[2],ids[1],ids[3],ids[4]])
                row(0).locator('[data-neoscorpion-dispatch-details]').click()
                editor=row(0).locator('select[name=assigned_fueler_user_id]');editor.select_option(str(actor_id))
                select('[data-dispatch-sort-mode="arrival"]')
                self.assertEqual(order(),[ids[1],ids[0],ids[2],ids[4],ids[3]])
                expect(editor).to_have_value(str(actor_id))
                detail=page.locator('#'+row(0).locator('[data-neoscorpion-dispatch-details]').get_attribute('aria-controls'))
                expect(detail).to_be_visible()
                groups=page.locator('.neoscorpion-dispatch-table--compact tbody > tr').evaluate_all('els=>els.map(el=>el.dataset.dispatchSortGroup)')
                self.assertEqual(groups,[str(ids[1])]*2+[str(ids[0])]*4+[str(ids[2])]*2+[str(ids[4])]*2+[str(ids[3])]*2)
                editor.select_option('');editor.press('Tab')
                # Restore on a second authenticated device with no browser preference storage.
                device=browser.new_context();other=device.new_page();F().login(other)
                F().ready(other,'/neoscorpion/fuel-dispatch')
                expect(other.locator('[data-dispatch-arrived-first="0"]')).to_have_attribute('aria-pressed','true')
                expect(other.locator('[data-dispatch-sort-mode="arrival"]')).to_have_attribute('aria-pressed','true')
                device.close()
                select('[data-dispatch-arrived-first="1"]')
                self.assertEqual(order(),[ids[1],ids[2],ids[0],ids[4],ids[3]])
                with F.app.app_context():
                    arrival=db.session.get(SortDateMission,arrival_ids[0]);arrival.arrival_status='on_ground'
                    NeoScorpionSortAssetState.query.filter_by(sort_date_operation_id=op_id).one().revision+=1;db.session.commit()
                expect(row(0)).to_have_class(re.compile('is-on-ground'),timeout=15000)
                self.assertEqual(order(),[ids[1],ids[0],ids[2],ids[4],ids[3]])
                expect(detail).to_be_visible()
                self.assertEqual(page.locator('[data-dispatch-preferences]').count(),1)
                self.assertEqual(row(0).locator('.neoscorpion-dispatch-tail > strong').evaluate('el=>getComputedStyle(el).color'),'rgb(255, 240, 176)')
                with F.app.app_context():
                    arrival=db.session.get(SortDateMission,arrival_ids[0]);arrival.arrival_status='arrived'
                    arrival.actual_block_in_datetime_utc=arrival.eta_datetime_utc
                    NeoScorpionSortAssetState.query.filter_by(sort_date_operation_id=op_id).one().revision+=1;db.session.commit()
                expect(row(0)).to_have_class(re.compile('is-arrived'),timeout=15000)
                self.assertEqual(row(0).locator('.neoscorpion-dispatch-tail > strong').evaluate('el=>getComputedStyle(el).color'),'rgb(143, 255, 168)')
                self.assertEqual(order(),[ids[1],ids[0],ids[2],ids[4],ids[3]])
                self.assertEqual(row(3).evaluate('el=>getComputedStyle(el).backgroundColor'),'rgb(9, 10, 12)')
                self.assertEqual(row(3).evaluate('el=>getComputedStyle(el).getPropertyValue("--arrival-text").trim()'),'')
                # READY/ETD urgency must not recolor arrival text, including reduced motion.
                page.emulate_media(reduced_motion='reduce')
                row(0).evaluate("el=>el.classList.add('is-ready-to-assign','is-assignment-alert-red')")
                self.assertEqual(row(0).locator('.neoscorpion-dispatch-flight > strong').evaluate('el=>getComputedStyle(el).color'),'rgb(143, 255, 168)')
                for width,height in ((1440,900),(1920,1080),(390,844),(375,568)):
                    page.set_viewport_size({'width':width,'height':height})
                    self.assertFalse(page.evaluate('document.documentElement.scrollWidth>innerWidth+1'))
                    self.assertEqual(row(1).evaluate('el=>Math.round(el.getBoundingClientRect().height)'),73 if width<=760 else 98)
                    self.assertEqual(rows.first.evaluate('el=>Array.from(el.children).filter(td=>getComputedStyle(td).display!=="none").length'),5 if width<=760 else 16)
                    page.screenshot(path=str(F.evidence/f'dispatch-profile-{engine}-{width}.png'))
                page.set_viewport_size({'width':1440,'height':900})
                board=page.locator('[data-operational-board-toggle]:visible')
                board.first.click()
                expect(page.locator('body')).to_have_class(re.compile('operational-board-view'))
                expect(page.locator('.neoscorpion-truck-visual-card').first).to_be_visible()
                self.assertLessEqual(page.locator('.neoscorpion-truck-visual-panel').bounding_box()['height'],290)
                self.assertEqual(page.locator('[data-dispatch-preferences]').count(),1)
                self.assertFalse(page.evaluate('document.documentElement.scrollWidth>innerWidth+1'))
                page.route('**/fuel-dispatch/preferences',lambda route:route.fulfill(status=503,content_type='application/json',body='{"ok":false,"error":"Synthetic save outage"}'))
                page.locator('[data-dispatch-sort-mode="etd"]').click()
                expect(page.locator('[data-dispatch-preference-status]').first).to_contain_text('NOT SAVED')
                self.assertFalse(errors,errors)
        finally:
            if browser:browser.close()
            F.tearDownClass()
