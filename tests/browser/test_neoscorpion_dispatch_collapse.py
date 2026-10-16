"""Persisted independent Dispatch collapse controls in real desktop/mobile layouts."""
from contextlib import ExitStack
from dataclasses import replace
from datetime import date, datetime, timedelta
import unittest
from unittest.mock import patch
from playwright.sync_api import expect
from app.extensions import db
from app.models import (User, SortDateOperation, SortDateMission, NeoScorpionSortAssetState,
    NeoScorpionFuelTruck, NeoScorpionSortTruck, NeoScorpionSortFueler, NeoScorpionDispatchPreference)
from app.services.access_control import ensure_default_gateway_and_nodes
from tests.browser import test_mobile_drawer as fixtures


class DispatchCollapseBrowserTest(unittest.TestCase):
    def test_normal_board_mobile_persistence_live_refresh_and_errors(self):
        Fixture=fixtures.MobileDrawerBrowserTest
        for engine,width in (('chromium',1440),('webkit',390)):
            with self.subTest(engine=engine):
                Fixture.setUpClass();browser=None
                try:
                    with Fixture.app.app_context():
                        gateway=ensure_default_gateway_and_nodes();user=User.query.filter_by(username='drawer-admin').one()
                        operation=SortDateOperation(gateway_id=gateway.id,gateway_code=gateway.code,sort_date=date.today(),
                            sort_name='night',generated_by_user_id=user.id,window_minutes=60)
                        db.session.add(operation);db.session.flush()
                        truck=NeoScorpionFuelTruck(gateway_id=gateway.id,truck_number='10',capacity_gallons=10000,is_active=True)
                        db.session.add(truck);db.session.flush()
                        db.session.add_all([NeoScorpionSortAssetState(sort_date_operation_id=operation.id,revision=1),
                            NeoScorpionSortFueler(sort_date_operation_id=operation.id,user_id=user.id),
                            NeoScorpionSortTruck(sort_date_operation_id=operation.id,fuel_truck_id=truck.id,
                                status='available',starting_gallons=9000,current_gallons=9000)])
                        mission=SortDateMission(sort_date_operation_id=operation.id,sort_date=operation.sort_date,
                            gateway_code=gateway.code,sort_name='night',mission_type='departure',mission_source='manual',
                            flight_number='UPS0910',origin=gateway.code,destination='SDF',assigned_tail_number='N432UP',
                            timezone='America/Chicago',planned_source='manual',planned_datetime_utc=datetime.utcnow()+timedelta(hours=2),
                            planned_fuel_load=25000,fuel_status='waiting',departure_status='scheduled')
                        db.session.add(mission);db.session.commit();opid,mid=operation.id,mission.id
                    with ExitStack() as stack:
                        stack.enter_context(patch('app.services.neoscorpion.current_existing_operational_sort_operations',
                            side_effect=lambda gateway,now=None:[db.session.get(SortDateOperation,opid)]))
                        from app.services.live_screen_refresh import live_screen_refresh_value
                        stack.enter_context(patch('app.services.neoscorpion.live_screen_refresh_value',
                            side_effect=lambda gateway,key,**kw:replace(live_screen_refresh_value(gateway,key,**kw),effective_interval_ms=5000)))
                        browser=getattr(Fixture.pw,engine).launch();context=browser.new_context(viewport={'width':width,'height':900},has_touch=width<900,is_mobile=width<900)
                        page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                        Fixture().login(page);page.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1','seen')")
                        Fixture().ready(page,'/neoscorpion/fuel-dispatch')
                        trucks=page.locator('[data-dispatch-collapse="trucks"]');spear=page.locator('[data-dispatch-collapse="spear"]')
                        cards=page.locator('[data-dispatch-section="trucks"]');data=page.locator('[data-dispatch-section="spear"]')
                        expect(cards).to_be_visible();expect(data).to_be_visible()
                        def toggle(button):
                            with page.expect_response(lambda r:'/fuel-dispatch/preferences' in r.url and r.request.method=='POST') as response:button.click()
                            self.assertEqual(response.value.status,200)
                        height=page.locator('.neoscorpion-truck-visual-panel').bounding_box()['height']
                        toggle(trucks);expect(cards).to_be_hidden();expect(data).to_be_visible()
                        self.assertLess(page.locator('.neoscorpion-truck-visual-panel').bounding_box()['height'],height-50)
                        toggle(spear);expect(data).to_be_hidden();expect(page.locator('[data-fueling-performance]')).to_be_visible()
                        toggle(trucks);expect(cards).to_be_visible();expect(data).to_be_hidden()
                        # Editing and open DETAILS survive presentation changes without replacing nodes.
                        if width>900:
                            detail=page.locator(f'[data-dispatch-mission-id="{mid}"] [data-neoscorpion-dispatch-details]')
                            detail.click();expect(detail).to_have_attribute('aria-expanded','true')
                            select=page.locator(f'select[form="neoscorpion-dispatch-assignment-{mid}"][name="assigned_fueler_user_id"]').first
                            select.select_option(index=1);value=select.input_value()
                            toggle(trucks);expect(select).to_have_value(value);expect(detail).to_have_attribute('aria-expanded','true')
                            # Restore the pristine choice before testing canonical live-panel replacement.
                            select.select_option(index=0)
                            # A single shared truck presentation serves Board View.
                            page.locator('[data-operational-board-toggle]:visible').first.click()
                            expect(page.locator('body')).to_have_class(__import__('re').compile('operational-board-view'))
                            expect(cards).to_be_hidden()
                            before=page.locator('.neoscorpion-table-wrap--sticky').bounding_box()['height']
                            toggle(trucks);expect(cards).to_be_visible()
                            expanded=page.locator('.neoscorpion-table-wrap--sticky').bounding_box()['height']
                            self.assertGreater(before,expanded+50)
                            toggle(trucks);expect(cards).to_be_hidden()
                            page.locator('.operational-board-exit').click();expect(cards).to_be_hidden()
                        else:
                            toggle(trucks);expect(cards).to_be_hidden()
                        # Existing revision poll replaces the panel but cannot reopen hidden bodies.
                        page.evaluate('window.collapseContinuity="kept"')
                        with Fixture.app.app_context():
                            state=NeoScorpionSortAssetState.query.filter_by(sort_date_operation_id=opid).one();state.revision+=1;rev=state.revision;db.session.commit()
                        expect(page.locator('[data-fuel-dispatch-live]')).to_have_attribute('data-revision',str(rev),timeout=15000)
                        expect(cards).to_be_hidden();expect(data).to_be_hidden()
                        self.assertEqual(page.evaluate('window.collapseContinuity'),'kept')
                        page.reload();expect(cards).to_be_hidden();expect(data).to_be_hidden()
                        # Fresh device/session after login has no local preference storage.
                        other=browser.new_context(viewport={'width':width,'height':900});otherpage=other.new_page()
                        Fixture().login(otherpage);otherpage.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1','seen')")
                        Fixture().ready(otherpage,'/neoscorpion/fuel-dispatch')
                        expect(otherpage.locator('[data-dispatch-section="trucks"]')).to_be_hidden()
                        expect(otherpage.locator('[data-dispatch-section="spear"]')).to_be_hidden();other.close()
                        # Failed persistence is compact, visible, and not silently retried.
                        with patch('app.services.neoscorpion_dispatch_preferences.save_dispatch_preferences',side_effect=ValueError('Test persistence failure')):
                            with page.expect_response(lambda r:'/fuel-dispatch/preferences' in r.url and r.request.method=='POST') as failed:
                                trucks.click()
                            self.assertEqual(failed.value.status,400)
                        expect(cards).to_be_visible()
                        expect(page.locator('.neoscorpion-truck-visual-panel [data-dispatch-preference-status]')).to_contain_text('NOT SAVED')
                        toggle(trucks);expect(cards).to_be_hidden()
                        expect(page.locator('[data-fueling-performance]')).to_be_visible()
                        visible=page.locator('.neoscorpion-dispatch-primary-row td').evaluate_all("cells=>cells.filter(c=>getComputedStyle(c).display!=='none').length")
                        self.assertEqual(visible,16 if width>900 else 5)
                        self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
                        page.screenshot(path=str(Fixture.evidence/f'scorpion-collapse-{engine}.png'),full_page=True)
                        self.assertEqual(errors,[])
                        with Fixture.app.app_context():
                            pref=NeoScorpionDispatchPreference.query.one();self.assertTrue(pref.truck_cards_collapsed);self.assertTrue(pref.spear_data_collapsed)
                        context.close()
                finally:
                    if browser:browser.close()
                    Fixture.tearDownClass()
