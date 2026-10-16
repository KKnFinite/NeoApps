"""Real desktop/mobile manual Dispatch captures the rendered SPEAR choice."""
from contextlib import ExitStack
from dataclasses import replace
from datetime import date, datetime, timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from playwright.sync_api import expect
from app.extensions import db
from app.models import (NeoScorpionFuelTruck, NeoScorpionSettings, NeoScorpionSortAssetState,
    NeoScorpionSortFueler, NeoScorpionSortTruck, NeoScorpionTailFuelState, SortDateMission,
    SortDateOperation, SortDateParkingAssignment, SortDateTailState, User)
from app.models.neoscorpion_spear_observation import NeoScorpionSpearObservation as Observation
from app.services.access_control import ensure_default_gateway_and_nodes
from tests.browser import test_mobile_drawer as fixtures


class SpearWholeSortBrowserTest(unittest.TestCase):
    def test_active_whole_sort_chromium_webkit_layout_and_live_panel(self):
        Fixture = fixtures.MobileDrawerBrowserTest
        for engine, width in (('chromium',1440),('webkit',390)):
            with self.subTest(engine=engine, width=width):
                Fixture.setUpClass()
                browser = None
                try:
                    with Fixture.app.app_context():
                        gateway=ensure_default_gateway_and_nodes()
                        admin=User.query.filter_by(username='drawer-admin').one()
                        operation=SortDateOperation(generated_by_user_id=admin.id,gateway_id=gateway.id,
                            gateway_code=gateway.code,sort_date=date.today(),sort_name='night',window_minutes=60)
                        db.session.add(operation);db.session.flush()
                        db.session.add_all([NeoScorpionSortFueler(sort_date_operation_id=operation.id,user_id=admin.id),
                            NeoScorpionSettings(gateway_id=gateway.id,spear_learning_capture_enabled=True,spear_whole_sort_mode='active'),
                            NeoScorpionSortAssetState(sort_date_operation_id=operation.id,revision=1)])
                        trucks=[]
                        for number in ('10','11'):
                            truck=NeoScorpionFuelTruck(gateway_id=gateway.id,truck_number=number,capacity_gallons=10000,is_active=True)
                            db.session.add(truck);db.session.flush();trucks.append(truck.id)
                            db.session.add(NeoScorpionSortTruck(sort_date_operation_id=operation.id,fuel_truck_id=truck.id,
                                status='available',starting_gallons=9000,current_gallons=9000))
                        tail='N432UP'
                        mission=SortDateMission(sort_date_operation_id=operation.id,sort_date=operation.sort_date,
                            gateway_code=gateway.code,sort_name='night',mission_type='departure',mission_source='manual',
                            flight_number='UPS0910',origin=gateway.code,destination='SDF',assigned_tail_number=tail,
                            timezone='America/Chicago',planned_source='manual',planned_datetime_utc=datetime.utcnow()+timedelta(hours=2),
                            planned_fuel_load=25000,fuel_status='waiting',departure_status='scheduled')
                        arrival=SortDateMission(sort_date_operation_id=operation.id,sort_date=operation.sort_date,
                            gateway_code=gateway.code,sort_name='night',mission_type='arrival',mission_source='manual',
                            flight_number='ARR0910',origin='SDF',destination=gateway.code,assigned_tail_number=tail,
                            arrival_status='on_ground',actual_block_in_datetime_utc=datetime.utcnow()-timedelta(minutes=10))
                        db.session.add_all([mission,arrival,SortDateTailState(sort_date=operation.sort_date,
                            gateway_code=gateway.code,sort_name='night',tail_number=tail,aircraft_type='757',aircraft_type_source='derived'),
                            NeoScorpionTailFuelState(sort_date_operation_id=operation.id,tail_number=tail,inbound_fuel_lbs=15000),
                            SortDateParkingAssignment(sort_date_operation_id=operation.id,tail_number=tail,position_code='B06',ramp_code='B',lane_number=1)])
                        db.session.add(SortDateMission(sort_date_operation_id=operation.id,sort_date=operation.sort_date,
                            gateway_code=gateway.code,sort_name='night',mission_type='departure',mission_source='manual',
                            flight_number='UPSINCOMPLETE',origin=gateway.code,destination='SDF',assigned_tail_number='N999UP',
                            timezone='America/Chicago',planned_source='manual',planned_datetime_utc=None,
                            planned_fuel_load=None,fuel_status='waiting',departure_status='scheduled'))
                        db.session.commit();opid,mid,uid=operation.id,mission.id,admin.id
                    with ExitStack() as stack:
                        stack.enter_context(patch('app.services.neoscorpion.current_existing_operational_sort_operations',
                            side_effect=lambda gateway,now=None:[db.session.get(SortDateOperation,opid)]))
                        stack.enter_context(patch('app.services.neoscorpion_spear_decisions.learning_vault_status',
                            return_value=SimpleNamespace(configured=True)))
                        from app.services.live_screen_refresh import live_screen_refresh_value as refresh_value
                        stack.enter_context(patch('app.services.neoscorpion.live_screen_refresh_value',
                            side_effect=lambda gateway,key,**kw:replace(refresh_value(gateway,key,**kw),effective_interval_ms=5000)))
                        browser=getattr(Fixture.pw,engine).launch()
                        context=browser.new_context(viewport={'width':width,'height':900})
                        page=context.new_page();errors=[]
                        page.on('pageerror',lambda error:errors.append(str(error)))
                        Fixture().login(page)
                        page.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1','seen')")
                        Fixture().ready(page,'/neoscorpion/fuel-dispatch')
                        whole_sort=page.locator('[data-spear-whole-sort]')
                        expect(whole_sort.locator('summary')).to_contain_text('ACTIVE')
                        self.assertNotIn('SAFE FALLBACK',whole_sort.inner_text())
                        computed=page.locator(f'[data-dispatch-mission-id="{mid}"]').first.locator('td').evaluate_all("cells => cells.filter(c => getComputedStyle(c).display !== 'none').length")
                        self.assertEqual(computed,5 if width < 900 else 16)
                        if engine == 'chromium':
                            from tests import test_neoscorpion_spear as planning
                            from app.services.neoscorpion_spear import SpearSettings
                            top_plan=planning._plan([planning._row(demand=2500)],
                                trucks=(planning._truck(current=600,capacity=4000),))
                            fake={'operation':SimpleNamespace(id=opid),'spear_plan':top_plan,
                                  'spear_settings':SpearSettings(automation_enabled=True)}
                            headers={'X-Requested-With':'XMLHttpRequest',
                                'X-CSRF-Token':page.locator('meta[name="csrf-token"]').get_attribute('content')}
                            with patch('app.neonodes.neoscorpion.routes.fuel_dispatch_context',return_value=fake), \
                                 patch('app.neonodes.neoscorpion.routes.mark_nightly_truck_topping_off') as sent:
                                for mode in ('automatic','manual'):
                                    denied=page.request.post(Fixture.origin+'/neoscorpion/fuel-dispatch/spear-action',
                                        form={'execution_mode':mode,'plan_token':top_plan.token,
                                              'mission_id':str(top_plan.steps[0].mission_id),
                                              'truck_id':str(top_plan.steps[0].truck_id)},headers=headers)
                                    self.assertEqual(denied.status,400)
                                future_step=replace(top_plan.steps[0],action_type='assign',requires_resource_release=True)
                                fake['spear_plan']=replace(top_plan,steps=(future_step,))
                                denied=page.request.post(Fixture.origin+'/neoscorpion/fuel-dispatch/spear-action',
                                    form={'execution_mode':'manual','plan_token':top_plan.token,
                                        'mission_id':str(future_step.mission_id),'truck_id':str(future_step.truck_id)},headers=headers)
                                self.assertEqual(denied.status,400)
                                sent.assert_not_called()
                        self.assertNotIn('SUGGESTED',page.locator('body').inner_text())
                        row=page.locator(f'[data-dispatch-mission-id="{mid}"]').first
                        # Mobile keeps assignment controls in the expanded existing DETAILS.
                        if width > 900:
                            page.locator(f'[data-dispatch-mission-id="{mid}"] [data-neoscorpion-dispatch-details]').click()
                        fueler=page.locator(f'select[form="neoscorpion-dispatch-assignment-{mid}"][name="assigned_fueler_user_id"]').first
                        truck=page.locator(f'select[form="neoscorpion-dispatch-assignment-{mid}"][name="assigned_truck_id"]').first
                        self.assertEqual(fueler.locator('option').filter(has_text='SPEAR Recommended').count(),1, page.locator('[data-fuel-dispatch-panel]').inner_text() if page.locator('[data-fuel-dispatch-panel]').count() else page.locator('body').inner_text())
                        recommended=truck.locator('option').filter(has_text='SPEAR Recommended')
                        expect(recommended).to_have_count(1)
                        recommended_id=int(recommended.get_attribute('value'))
                        chosen=next(t for t in trucks if t!=recommended_id)
                        if width > 900:
                            fueler.select_option(str(uid));truck.select_option(str(chosen))
                            button=page.locator(f'[form="neoscorpion-dispatch-assignment-{mid}"][data-dispatch-assignment-submit]:visible').first
                            endpoint='/fuel-dispatch/assignment'
                        else:
                            chosen=recommended_id
                            button=page.locator('[data-spear-action-form] button:visible').first
                            endpoint='/fuel-dispatch/spear-action'
                        with page.expect_response(lambda r:endpoint in r.url and r.request.method=='POST') as response:
                            button.click()
                        self.assertIn(response.value.status,(200,302))
                        with Fixture.app.app_context():
                            observation=Observation.query.filter_by(kind='decision').one()
                            record=observation.payload['payload']
                            self.assertEqual(record['original_spear']['algorithm_version'],'spear-whole-sort/v2')
                            self.assertEqual(record['original_spear']['recommendation']['truck_id'],recommended_id)
                            self.assertEqual(record['selected']['truck_id'],chosen)
                            self.assertEqual(record['comparison'],'different' if width > 900 else 'matched')
                        page.reload()
                        if width > 900:
                            toggle=page.locator(f'[data-dispatch-mission-id="{mid}"] [data-neoscorpion-dispatch-details]')
                            if toggle.get_attribute('aria-expanded') != 'true':toggle.click()
                            note=page.locator('details').filter(has=page.locator('form[action$="spear-decision-reason"]')).last
                            note.locator('summary').click()
                            note.locator('[name="reason"]').select_option('operational_judgment')
                            note.locator('[name="note"]').fill('<script>plain supplementary text</script>')
                            self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'))
                            note.locator('button[type="submit"]').click()
                            with Fixture.app.app_context():
                                self.assertEqual(Observation.query.filter_by(kind='reason').count(),1)
                        # Real existing polling must replace the panel when an
                        # operational revision changes, without a page reload.
                        page.evaluate('window.spearContinuityToken="kept"')
                        with Fixture.app.app_context():
                            state=NeoScorpionSortAssetState.query.filter_by(sort_date_operation_id=opid).one()
                            state.revision+=1
                            expected_revision=state.revision
                            db.session.commit()
                        expect(page.locator('[data-fuel-dispatch-live]')).to_have_attribute('data-revision',str(expected_revision),timeout=15000)
                        self.assertEqual(page.evaluate('window.spearContinuityToken'),'kept')
                        self.assertEqual(page.request.get(Fixture.origin+'/neoscorpion/fuel-dispatch/live-panel').status,200)
                        self.assertEqual(page.request.get(Fixture.origin+'/neoscorpion/fueler').status,200)
                        self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'))
                        page.screenshot(path=str(Fixture.evidence / f'spear-v2-{engine}-{width}.png'),full_page=True)
                        Fixture().ready(page,'/neoscorpion/fueler')
                        self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'))
                        Fixture().ready(page,'/neoscorpion/settings/spear')
                        expect(page.locator('select[name="whole_sort_mode"]')).to_have_value('active')
                        expect(page.locator('[name="top_off_roundtrip_minutes"]')).to_have_value('40')
                        self.assertFalse(page.locator('[name="use_learned_top_off"]').is_checked())
                        self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'))
                        self.assertEqual(errors,[])
                        context.close()
                finally:
                    if browser:browser.close()
                    Fixture.tearDownClass()
