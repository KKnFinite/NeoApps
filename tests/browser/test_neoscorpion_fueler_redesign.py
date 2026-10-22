"""Production-shaped Fueler workspace, isolated from operational records."""
from datetime import date, datetime, timedelta
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from playwright.sync_api import expect
from tests.browser.neoscorpion_helpers import wait_for_fuel_saved
from app.extensions import db
from app.models import (User, SortDateOperation, SortDateMission, SortDateParkingAssignment,
    NeoScorpionFuelAssignment, NeoScorpionFuelTruck, NeoScorpionSortTruck,
    NeoScorpionSortFueler, NeoScorpionTailFuelState, NeoScorpionFuelWorkState, NeoScorpionFuelingEvent)
from app.services.access_control import ensure_default_gateway_and_nodes
from tests.browser import test_mobile_drawer as fixtures


class FuelerRedesignBrowserTest(unittest.TestCase):
    def test_workspaces_and_physical_workflow(self):
        F=fixtures.MobileDrawerBrowserTest;F.setUpClass()
        F.app.config['LIVE_SCREEN_REFRESH_INTERVAL_MS']=5000
        evidence=Path('instance/browser-evidence/fueler-redesign');evidence.mkdir(parents=True,exist_ok=True)
        baseline=os.environ.get('NEO_FUELER_CAPTURE_BASELINE')=='1'
        try:
            with F.app.app_context():
                gateway=ensure_default_gateway_and_nodes();user=User.query.filter_by(username='drawer-admin').one()
                operation=SortDateOperation(generated_by_user_id=user.id,gateway_id=gateway.id,
                    gateway_code=gateway.code,sort_date=date.today(),sort_name='night',window_minutes=60)
                db.session.add(operation);db.session.flush();oid=operation.id;cases=[]
                db.session.add(NeoScorpionSortFueler(sort_date_operation_id=oid,user_id=user.id))
                for index,tail in enumerate(('N124UP','N491UP')):
                    truck=NeoScorpionFuelTruck(gateway_id=gateway.id,truck_number=f'FUELER-{index}',capacity_gallons=20000)
                    mission=SortDateMission(sort_date_operation_id=oid,sort_date=operation.sort_date,gateway_code=gateway.code,
                        sort_name='night',mission_type='departure',mission_source='manual',flight_number=f'UPS{950+index}',
                        origin=gateway.code,destination='MHR',assigned_tail_number=tail,planned_fuel_load=25000,
                        planned_datetime_utc=datetime.utcnow()+timedelta(hours=2),timezone='America/Chicago')
                    db.session.add_all([truck,mission]);db.session.flush()
                    db.session.add_all([NeoScorpionSortTruck(sort_date_operation_id=oid,fuel_truck_id=truck.id,status='available',
                        starting_gallons=10000,current_gallons=10000,inventory_verified=True),
                        NeoScorpionTailFuelState(sort_date_operation_id=oid,tail_number=tail,inbound_fuel_lbs=12000),
                        SortDateParkingAssignment(sort_date_operation_id=oid,tail_number=tail,ramp_code='C',position_code=f'{6+index:02}',lane_number=1)])
                    assignment=NeoScorpionFuelAssignment(sort_date_operation_id=oid,sort_date_mission_id=mission.id,
                        assigned_fueler_user_id=user.id,assigned_truck_id=truck.id,confirmed_tail_number=tail)
                    db.session.add(assignment);db.session.flush();cases.append((assignment.id,mission.id))
                db.session.commit()
            measurements=[]
            with patch('app.services.neoscorpion.current_existing_operational_sort_operations',
                    side_effect=lambda gateway,now=None:[db.session.get(SortDateOperation,oid)]):
                for engine in ('chromium','webkit'):
                    browser=getattr(F.pw,engine).launch()
                    try:
                        page=browser.new_page(viewport={'width':390,'height':844},service_workers='block')
                        errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                        F().login(page);F().ready(page,'/neoscorpion/fueler')
                        for width in (375,390,768,1440):
                            page.set_viewport_size({'width':width,'height':900 if width>700 else 844})
                            card=page.locator('[data-fuel-data-card]').first
                            measurements.append({'engine':engine,'width':width,'height':card.bounding_box()['height']})
                            page.screenshot(path=str(evidence/f'{"before" if baseline else "after"}-{engine}-{width}.png'),full_page=True)
                            self.assertFalse(page.evaluate('document.documentElement.scrollWidth>innerWidth+1'))
                        if not baseline:
                            page.set_viewport_size({'width':390,'height':844})
                            aid,mid=cases[0 if engine=='chromium' else 1]
                            card=page.locator(f'[data-fuel-assignment-id="{aid}"]')
                            self.assertEqual(card.get_by_role('button',name='Save Fuel Entry',exact=True).count(),0)
                            self.assertEqual(card.locator('[data-fuel-status]').count(),0)
                            expect(card.locator('[data-fueler-submit-form] button')).to_be_disabled()
                            card.locator('[data-truck-on-form] button').click()
                            expect(card.locator('.neoscorpion-truck-on-row')).to_contain_text('CONFIRMED')
                            wid=card.get_attribute('data-physical-work-id')
                            remaining=card.locator('[data-fuel-reading="remaining"]')
                            actual=card.locator('[data-fuel-reading="actual"]')
                            for index in range(remaining.count()):
                                code=remaining.nth(index).get_attribute('data-tank-code')
                                wing=code in ('left','right','l_out','r_out')
                                remaining.nth(index).fill('6' if wing else '0')
                                actual.nth(index).fill('14' if wing else '0')
                            card.locator('[data-apu-running]').select_option('no')
                            card.locator('[name=transfer_fuel_gallons]').fill('2000')
                            card.locator('[name=notes]').fill('Independent visit notes')
                            expect(card.locator('[data-fuel-data-status]')).to_have_text('Saved',timeout=15000)
                            expect(card.locator('[name=notes]')).to_have_value('Independent visit notes')
                            with F.app.app_context():
                                work=db.session.get(NeoScorpionFuelWorkState,int(wid))
                                self.assertEqual(work.notes,'Independent visit notes')
                                self.assertEqual(work.physical_transfer_gallons,2000)
                            # Manual draft is not applied by unrelated autosaves.
                            card.locator('[data-apu-running]').select_option('yes')
                            card.locator('[data-apu-source]').select_option('l_out' if engine=='chromium' else 'left')
                            expect(card.locator('[data-fuel-data-status]')).to_have_text('Saved',timeout=15000)
                            card.locator('[data-apu-override-value]').fill('0.5')
                            card.locator('[name=notes]').fill('Notes while APU remains a draft')
                            expect(card.locator('[data-fuel-data-status]')).to_contain_text('Manual APU draft',timeout=15000)
                            with F.app.app_context():self.assertFalse(db.session.get(NeoScorpionFuelWorkState,int(wid)).apu_override_enabled)
                            card.locator('[data-apu-use-manual]').click()
                            expect(card.locator('[data-fuel-data-status]')).to_have_text('Saved',timeout=15000)
                            with F.app.app_context():self.assertEqual(db.session.get(NeoScorpionFuelWorkState,int(wid)).apu_override_allowance_lbs,500)
                            ordered=card.evaluate("""c=>['.scorpion-fueler-mission','.neoscorpion-truck-on-row',
                                '.neoscorpion-fuel-tank-grid','.scorpion-fueler-apu','[name=transfer_fuel_gallons]',
                                '[name=notes]','.scorpion-fueler-finish','[data-fueler-submit-form]']
                                .map(s=>c.querySelector(s).getBoundingClientRect().top)""")
                            self.assertEqual(ordered,sorted(ordered))
                            page.evaluate("document.activeElement?.blur();scrollTo({top:0,behavior:'instant'})")
                            page.screenshot(path=str(evidence/f'active-apu-{engine}-390.png'),full_page=True)
                            card.locator('[data-apu-use-recommended]').click()
                            expect(card.locator('[data-fuel-data-status]')).to_have_text('Saved',timeout=15000)
                            card.locator('[data-apu-running]').select_option('no')
                            expect(card.locator('[data-fuel-data-status]')).to_have_text('Saved',timeout=15000)
                            # An outage keeps drafts, clears Saving, and never
                            # resends until the operator makes another edit.
                            endpoint=f'**/neoscorpion/fueler/assignments/{aid}*'
                            page.route(endpoint,lambda r:r.fulfill(status=503,json={'ok':False,'error':'Synthetic outage'}),times=1)
                            card.locator('[name=notes]').fill('Retained outage draft')
                            expect(card.locator('[data-fuel-data-status]')).to_contain_text('Autosave failed',timeout=10000)
                            expect(card.locator('[name=notes]')).to_have_value('Retained outage draft')
                            page.wait_for_timeout(900)
                            with F.app.app_context():self.assertNotEqual(db.session.get(NeoScorpionFuelWorkState,int(wid)).notes,'Retained outage draft')
                            page.unroute(endpoint)
                            card.locator('[name=notes]').fill('Intentional retry after outage')
                            expect(card.locator('[data-fuel-data-status]')).to_have_text('Saved',timeout=10000)
                            # A second authorized operator changes the same
                            # field before this request reaches the lock.
                            def concurrent(route):
                                from app.services import neoscorpion as fuel
                                with F.app.app_context():
                                    actor=User.query.filter_by(username='drawer-admin').one()
                                    fuel.save_fueler_entry(ensure_default_gateway_and_nodes(),actor,
                                        {'assignment_id':aid,'notes':'Other operator committed'})
                                    db.session.commit()
                                route.continue_()
                            page.route(endpoint,concurrent,times=1)
                            card.locator('[name=notes]').fill('My conflicting draft')
                            expect(card.locator('[data-fuel-data-status]')).to_contain_text('DATA CHANGED',timeout=10000)
                            expect(card.locator('[name=notes]')).to_have_value('My conflicting draft')
                            page.wait_for_timeout(900)
                            with F.app.app_context():self.assertEqual(db.session.get(NeoScorpionFuelWorkState,int(wid)).notes,'Other operator committed')
                            page.unroute(endpoint)
                            card.locator('[name=notes]').fill('Reviewed new intentional value')
                            expect(card.locator('[data-fuel-data-status]')).to_have_text('Saved',timeout=10000)
                            # A slow save must not disable typing or replace a
                            # newer draft with the older response.
                            from app.neonodes.neoscorpion import routes
                            import time
                            original=routes.save_fueler_entry
                            def slow(*args,**kwargs):time.sleep(.5);return original(*args,**kwargs)
                            with patch.object(routes,'save_fueler_entry',side_effect=slow):
                                card.locator('[name=notes]').fill('Older in-flight value')
                                expect(card.locator('[data-fuel-data-status]')).to_have_text('Saving…',timeout=10000)
                                card.locator('[name=notes]').fill('Newer value during save')
                                expect(card.locator('[data-fuel-data-status]')).to_have_text('Saved',timeout=15000)
                            expect(card.locator('[name=notes]')).to_be_focused()
                            with F.app.app_context():self.assertEqual(db.session.get(NeoScorpionFuelWorkState,int(wid)).notes,'Newer value during save')
                            wait_for_fuel_saved(card)
                            # Early OFF resumes the same physical visit and data.
                            card.locator('.neoscorpion-fueler-off-form button').click()
                            expect(card.locator('[data-resume-fueling-form]')).to_be_visible()
                            card.locator('[data-resume-fueling-form] button').click()
                            expect(card.locator('.neoscorpion-truck-on-row')).to_contain_text('CONFIRMED')
                            self.assertEqual(card.get_attribute('data-physical-work-id'),wid)
                            expect(card.locator('[name=transfer_fuel_gallons]')).to_have_value('2000')
                            card.locator('.neoscorpion-fueler-off-form button').click()
                            expect(card.locator('[data-fueler-submit-form] button')).to_be_enabled(timeout=10000)
                            # Optional measurements remain independent; SKIP
                            # never dirties mandatory entry or blocks completion.
                            optional=card.locator('.scorpion-optional-verification')
                            optional.locator('summary').click()
                            optional.locator('[name=gallons]').fill('9000')
                            optional.get_by_role('button',name='SKIP',exact=True).click()
                            expect(card.locator('[data-fueler-submit-form] button')).to_be_enabled()
                            optional.locator('summary').click()
                            with page.expect_response(lambda r:r.request.method=='POST' and '/fuel-reading' in r.url) as measured:
                                optional.get_by_role('button',name='SUBMIT READING',exact=True).click()
                            self.assertEqual(measured.value.status,302)
                            page.wait_for_url('**/neoscorpion/fueler')
                            expect(card.locator('.scorpion-optional-verification')).to_have_count(0)
                            page.evaluate("document.activeElement?.blur();scrollTo({top:0,behavior:'instant'})")
                            page.screenshot(path=str(evidence/f'off-ready-{engine}-390.png'),full_page=True)
                            card.locator('[data-fueler-submit-form] button').click()
                            expect(card.locator('[data-fueler-submission-status]')).to_contain_text('SUBMITTED',timeout=10000)
                            dispatch=browser.new_page(viewport={'width':1440,'height':900},service_workers='block')
                            F().login(dispatch);dispatch.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1','seen')")
                            F().ready(dispatch,'/neoscorpion/fuel-dispatch')
                            row=dispatch.locator(f'[data-dispatch-mission-id="{mid}"]')
                            with dispatch.expect_response(lambda r:r.request.method=='POST' and '/complete' in r.url) as verification:
                                row.get_by_role('button',name='COMPLETE',exact=True).click()
                            self.assertEqual(verification.value.status,200,verification.value.text())
                            with F.app.app_context():
                                self.assertEqual(NeoScorpionFuelingEvent.query.filter_by(fuel_assignment_id=aid).count(),1)
                                self.assertIsNotNone(db.session.get(NeoScorpionFuelWorkState,int(wid)).physical_finalized_at_utc)
                            self.assertEqual(errors,[])
                    finally:browser.close()
            (evidence/f'{"before" if baseline else "after"}-measurements.json').write_text(json.dumps(measurements,indent=2))
        finally:F.tearDownClass()
