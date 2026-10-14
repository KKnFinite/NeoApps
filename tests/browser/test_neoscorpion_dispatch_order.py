"""Dispatch presentation smoke plus revision-driven reorder/state preservation."""
import unittest
from datetime import date, datetime, timedelta
from unittest.mock import patch
from playwright.sync_api import expect

from app.extensions import db
from app.models import (User, SortDateOperation, SortDateMission, NeoScorpionFuelAssignment,
                        NeoScorpionSortAssetState, NeoScorpionSortFueler, NeoScorpionFuelTruck,
                        NeoScorpionSortTruck, NeoScorpionFuelCycleHistory)
from app.services.access_control import ensure_default_gateway_and_nodes
from tests.browser import test_mobile_drawer as fixtures


class DispatchOrderBrowserTest(unittest.TestCase):
    def test_desktop_mobile_live_order_preserves_dirty_details_and_scroll(self):
        Fixture=fixtures.MobileDrawerBrowserTest
        Fixture.setUpClass(); browser=None
        Fixture.app.config["LIVE_SCREEN_REFRESH_INTERVAL_MS"]=1000
        try:
            with Fixture.app.app_context():
                gateway=ensure_default_gateway_and_nodes()
                actor=User.query.filter_by(username="drawer-admin").one()
                at=datetime.utcnow().replace(microsecond=0)
                operation=SortDateOperation(generated_by_user_id=actor.id,gateway_id=gateway.id,
                    gateway_code=gateway.code,sort_date=date.today(),sort_name="night",window_minutes=60)
                db.session.add(operation); db.session.flush()
                db.session.add_all([NeoScorpionSortAssetState(sort_date_operation_id=operation.id,revision=1),
                    NeoScorpionSortFueler(sort_date_operation_id=operation.id,user_id=actor.id)])
                truck=NeoScorpionFuelTruck(gateway_id=gateway.id,truck_number="TEST422808",capacity_gallons=10000)
                db.session.add(truck); db.session.flush()
                db.session.add(NeoScorpionSortTruck(sort_date_operation_id=operation.id,fuel_truck_id=truck.id,
                    status="available",starting_gallons=10000,current_gallons=10000))
                ids=[]; assignments=[]
                for i in range(4):
                    mission=SortDateMission(sort_date_operation_id=operation.id,sort_date=operation.sort_date,
                        gateway_code=gateway.code,sort_name="night",mission_type="departure",mission_source="manual",
                        flight_number=f"UPS90{i}",origin=gateway.code,destination="SDF",assigned_tail_number=f"N49{i+1}UP",
                        planned_datetime_utc=at+timedelta(hours=6-i),timezone="America/Chicago",
                        planned_fuel_load=25000,fuel_status="complete" if i==3 else "waiting")
                    db.session.add(mission); db.session.flush()
                    assignment=NeoScorpionFuelAssignment(sort_date_operation_id=operation.id,sort_date_mission_id=mission.id,
                        completed_at_utc=at if i==3 else None,review_status="complete" if i==3 else "pending")
                    db.session.add(assignment); db.session.flush()
                    if i==0:
                        assignment.current_cycle_number=3
                        for cycle,label in ((1,"TAIL SWAP"),(2,"UPLIFT")):
                            db.session.add(NeoScorpionFuelCycleHistory(sort_date_operation_id=operation.id,
                                fuel_assignment_id=assignment.id,mission_id=mission.id,cycle_number=cycle,label=label,
                                snapshot=dict(tail_number="OLDTAIL",arrival_eta="23:00",destination="SDF",
                                    flight_number=mission.flight_number,departure_time="03:00",parking_position="A4",
                                    neo_fuel_display="25.0",actual_total_display="25.0",apu_allowance_display="0.0",
                                    fueler="Historical Fueler",truck="Historical Truck",dispatch_status_label="COMPLETE",
                                    tank_rows=[])))
                    ids.append(mission.id); assignments.append(assignment.id)
                    if i<2:
                        db.session.add(SortDateMission(sort_date_operation_id=operation.id,sort_date=operation.sort_date,
                            gateway_code=gateway.code,sort_name="night",mission_type="arrival",mission_source="manual",
                            flight_number=f"UPS80{i}",origin="SDF",destination=gateway.code,assigned_tail_number=mission.assigned_tail_number,
                            actual_block_in_datetime_utc=at-timedelta(hours=2) if i==0 else None,
                            eta_datetime_utc=at-timedelta(hours=1) if i==1 else None,arrival_status="scheduled"))
                db.session.commit(); operation_id,actor_id,truck_id,gateway_id=operation.id,actor.id,truck.id,gateway.id
            with patch("app.services.neoscorpion.current_existing_operational_sort_operations",
                       side_effect=lambda gateway,now=None:[db.session.get(SortDateOperation,operation_id)]):
                browser=Fixture.pw.chromium.launch()
                page=browser.new_page(viewport={"width":1440,"height":950})
                Fixture().login(page)
                page.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1','seen')")
                Fixture().ready(page,"/neoscorpion/fuel-dispatch")
                errors=[]; page.on("pageerror",lambda error:errors.append(str(error)))
                rows=page.locator('.neoscorpion-dispatch-primary-row[data-dispatch-mission-id]:not([data-cycle-history])')
                order=lambda:rows.evaluate_all('els=>els.map(el=>Number(el.dataset.dispatchMissionId))')
                self.assertEqual(order(),ids)
                sort_etd=page.locator('[data-dispatch-sort-mode="etd"]')
                sort_arrival=page.locator('[data-dispatch-sort-mode="arrival"]')
                pref=page.locator('[data-fuel-dispatch-live]').get_attribute('data-sort-preference-key')
                self.assertTrue(pref.endswith(f'.{gateway_id}.{actor_id}'))
                target=page.locator(f'tr[data-dispatch-mission-id="{ids[0]}"]').first
                toggle=target.locator('[data-neoscorpion-dispatch-details]')
                toggle.click(); detail_id=toggle.get_attribute('aria-controls')
                selection=target.locator('select[name="assigned_fueler_user_id"]')
                truck_selection=target.locator('select[name="assigned_truck_id"]')
                for select in (selection,truck_selection):
                    expect(select.locator('option').first).to_have_text('')
                    expect(select.locator('..').locator('.neoscorpion-dispatch-combobox-trigger')).to_have_text('')
                truck_selection.select_option(str(truck_id))
                expect(truck_selection.locator('..').locator('.neoscorpion-dispatch-combobox-trigger')).to_contain_text('TEST422808')
                truck_selection.select_option('')
                selection.select_option(str(actor_id))
                sort_etd.click()
                self.assertEqual(order(),[ids[2],ids[1],ids[0],ids[3]])
                expect(selection).to_have_value(str(actor_id))
                expect(page.locator(f'#{detail_id}')).to_be_visible()
                self.assertEqual(page.evaluate('(key)=>localStorage.getItem(key)',pref),'etd')
                # All history and DETAILS nodes stay in the same contiguous group.
                groups=page.locator('.neoscorpion-dispatch-table--compact tbody > tr').evaluate_all(
                    'els=>els.map(el=>el.dataset.dispatchSortGroup)')
                self.assertEqual(groups,[str(ids[2])]*2+[str(ids[1])]*2+[str(ids[0])]*6+[str(ids[3])]*2)
                with Fixture.app.app_context():
                    assignment=db.session.get(NeoScorpionFuelAssignment,assignments[0])
                    assignment.completed_at_utc=at+timedelta(minutes=1); assignment.review_status="complete"
                    db.session.get(SortDateMission,ids[0]).fuel_status="complete"
                    NeoScorpionSortAssetState.query.filter_by(sort_date_operation_id=operation_id).one().revision+=1
                    db.session.commit()
                # Wait for a real revision read; dirty input must defer replacement.
                with page.expect_response(lambda r:'/fuel-dispatch/revision' in r.url,timeout=15000):
                    page.wait_for_timeout(1200)
                expect(selection).to_have_value(str(actor_id))
                self.assertEqual(order(),[ids[2],ids[1],ids[0],ids[3]])
                selection.select_option("")
                selection.press("Tab")
                page.evaluate('window.scrollTo(0,150)')
                saved_scroll=page.evaluate('window.scrollY')
                roster=page.locator('.neoscorpion-table-wrap--sticky')
                saved_roster_scroll=roster.evaluate('el=>{el.scrollLeft=123;return el.scrollLeft}')
                self.assertGreater(saved_roster_scroll,0)
                expect(rows.last).to_have_attribute('data-dispatch-mission-id',str(ids[0]),timeout=15000)
                self.assertEqual(order(),[ids[2],ids[1],ids[3],ids[0]])
                expect(sort_etd).to_have_attribute('aria-pressed','true')
                complete_cells=rows.last.locator('td')
                self.assertEqual(complete_cells.nth(9).inner_text(),'')
                self.assertEqual(complete_cells.nth(11).inner_text(),'')
                expect(page.locator(f'#{detail_id}')).to_be_visible()
                max_page_scroll=page.evaluate('Math.max(document.documentElement.scrollHeight-innerHeight,0)')
                self.assertAlmostEqual(page.evaluate('window.scrollY'),min(saved_scroll,max_page_scroll),delta=3)
                self.assertAlmostEqual(roster.evaluate('el=>el.scrollLeft'),saved_roster_scroll,delta=1)
                for width in (1440,390):
                    page.set_viewport_size({"width":width,"height":844})
                    self.assertFalse(page.evaluate('document.documentElement.scrollWidth>innerWidth+1'))
                    self.assertEqual(order(),[ids[2],ids[1],ids[3],ids[0]])
                    sort_arrival.click()
                    self.assertEqual(order(),[ids[1],ids[2],ids[3],ids[0]])
                    sort_etd.click()
                page.reload(); page.wait_for_load_state('networkidle')
                expect(sort_etd).to_have_attribute('aria-pressed','true')
                self.assertEqual(order(),[ids[2],ids[1],ids[3],ids[0]])
                self.assertFalse(errors,errors)
        finally:
            if browser: browser.close()
            Fixture.tearDownClass()
