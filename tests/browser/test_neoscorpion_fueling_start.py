"""Mouse/touch-sized rendered hotfix workflow using real Fueler saves and OFF."""
from datetime import date, datetime, timedelta, timezone
import re
import unittest
from unittest.mock import patch

from playwright.sync_api import expect
from app.extensions import db
from app.models import (NeoScorpionFuelAssignment, NeoScorpionFuelTruck, NeoScorpionFuelWorkState,
    NeoScorpionFuelTankState, NeoScorpionSortTruck, NeoScorpionSettings, NeoScorpionSortAssetState,
    NeoScorpionTailFuelState, SortDateMission, SortDateOperation, SortDateParkingAssignment, SortDateTailState, User)
from app.services import neoscorpion as service
from app.services.access_control import ensure_default_gateway_and_nodes
from tests.browser import test_mobile_drawer as fixtures


class FuelingStartBrowserTest(unittest.TestCase):
    def test_append_only_tail_visits_and_pending_source_controls_desktop_mobile(self):
        fixture=fixtures.MobileDrawerBrowserTest
        fixture.setUpClass(); fixture.app.config["LIVE_SCREEN_REFRESH_INTERVAL_MS"]=1000
        browser=None
        clock=[datetime.now(timezone.utc).replace(tzinfo=None,microsecond=0)]
        class Clock(datetime):
            @classmethod
            def utcnow(cls): return clock[0]
        try:
            with fixture.app.app_context():
                gateway=ensure_default_gateway_and_nodes()
                actor=User.query.filter_by(username="drawer-admin").one()
                operation=SortDateOperation(generated_by_user_id=actor.id,gateway_id=gateway.id,
                    gateway_code=gateway.code,sort_date=date.today(),sort_name="night",window_minutes=60)
                truck=NeoScorpionFuelTruck(gateway_id=gateway.id,truck_number="20",capacity_gallons=20000)
                db.session.add_all([operation,truck]); db.session.flush()
                settings=NeoScorpionSettings(gateway_id=gateway.id,assignment_setup_minutes=8,advance_setup_window_minutes=10)
                selection=NeoScorpionSortTruck(sort_date_operation_id=operation.id,fuel_truck_id=truck.id,
                    status="available",starting_gallons=10000,current_gallons=10000)
                mission=SortDateMission(sort_date_operation_id=operation.id,sort_date=operation.sort_date,
                    gateway_code=gateway.code,sort_name="night",mission_type="departure",mission_source="manual",
                    flight_number="UPS1038",origin=gateway.code,destination="SDF",assigned_tail_number="N491UP",
                    planned_datetime_utc=clock[0]+timedelta(hours=2),timezone="America/Chicago",
                    planned_fuel_load=25000,fuel_status="assigned")
                db.session.add_all([settings,selection,mission,NeoScorpionSortAssetState(sort_date_operation_id=operation.id,revision=1)])
                db.session.flush()
                assignment=NeoScorpionFuelAssignment(sort_date_operation_id=operation.id,sort_date_mission_id=mission.id,
                    assigned_fueler_user_id=actor.id,assigned_truck_id=truck.id,confirmed_tail_number="N491UP",
                    fueler_assigned_at_utc=clock[0]-timedelta(minutes=20),truck_assigned_at_utc=clock[0]-timedelta(minutes=5))
                db.session.add(assignment); db.session.commit()
                operation_id,gateway_id,actor_id,assignment_id,mission_id,truck_id=operation.id,gateway.id,actor.id,assignment.id,mission.id,truck.id
            with patch("app.services.neoscorpion.current_existing_operational_sort_operations",
                side_effect=lambda gateway,now=None:[db.session.get(SortDateOperation,operation_id)]),patch("app.services.neoscorpion.datetime",Clock):
                browser=fixture.pw.chromium.launch()
                page=browser.new_page(viewport={"width":1440,"height":950})
                fixture().login(page)
                page.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1','seen')")
                errors=[]; page.on("pageerror",lambda error:errors.append(str(error)))

                def card(work_id=None):
                    return page.locator(f'[data-physical-work-id="{work_id}"]' if work_id else '[data-fuel-data-card]').first
                def current_id():
                    with fixture.app.app_context():
                        return db.session.get(NeoScorpionFuelAssignment,assignment_id).active_work_state_id
                def save_readings(target,remaining,actual=None,gallons=None):
                    target.locator('[name="apu_running"]').select_option("no")
                    for code in ("left","ctr","right"):
                        target.locator(f'[name="remaining_{code}"]').fill("0" if code=="ctr" else remaining)
                        if actual is not None:
                            target.locator(f'[name="actual_{code}"]').fill("0" if code=="ctr" else actual)
                    if gallons is not None: target.locator('[name="transfer_fuel_gallons"]').fill(gallons)
                    target.locator('[data-fuel-planning-form] button[type="submit"]').click()
                def change_tail(tail):
                    with fixture.app.app_context():
                        db.session.get(SortDateMission,mission_id).assigned_tail_number=tail
                        db.session.commit()
                        service.confirm_assignment_tail(db.session.get(type(gateway),gateway_id),db.session.get(User,actor_id),assignment_id,
                            expected_cycle=1,expected_tail=tail)
                        db.session.commit()

                fixture().ready(page,"/neoscorpion/fueler")
                card().locator('[data-truck-on-form] button').click()
                expect(card().locator('.neoscorpion-truck-on-row')).to_contain_text("CONFIRMED")
                a1=current_id()
                save_readings(card(a1),"6","15","3000")
                expect(card(a1).locator('[data-fuel-data-status]')).to_contain_text("Saved")
                clock[0]+=timedelta(minutes=12)
                card(a1).locator('.neoscorpion-fueler-off-form button').click()
                expect(page.locator('[data-fuel-data-card]')).to_have_count(0)
                change_tail("N492UP")
                fixture().ready(page,"/neoscorpion/fueler")
                b=current_id()
                card(b).locator('[data-truck-on-form] button').click()
                expect(card(b).locator('.neoscorpion-truck-on-row')).to_contain_text("CONFIRMED")
                save_readings(card(b),"7",gallons="1000")
                expect(card(b).locator('[data-fuel-data-status]')).to_contain_text("Saved")
                clock[0]+=timedelta(minutes=3)
                change_tail("N491UP")
                a2=current_id()
                self.assertEqual(len({a1,b,a2}),3)
                for width in (1440,390):
                    page.set_viewport_size({"width":width,"height":844})
                    fixture().ready(page,"/neoscorpion/fueler")
                    expect(card(b)).to_contain_text("PRIOR PHYSICAL VISIT")
                    expect(card(a2).locator('[name="transfer_fuel_gallons"]')).to_have_value("")
                    self.assertFalse(page.evaluate('document.documentElement.scrollWidth>innerWidth+1'))
                card(b).locator('.neoscorpion-fueler-off-form button').click()
                expect(card(b)).to_contain_text("Truck released")
                page.reload()
                card(a2).locator('[data-truck-on-form] button').click()
                expect(card(a2).locator('.neoscorpion-truck-on-row')).to_contain_text("CONFIRMED")
                save_readings(card(a2),"6",gallons="2000")
                expect(card(a2).locator('[data-fuel-data-status]')).to_contain_text("Saved")
                clock[0]+=timedelta(minutes=10)
                card(a2).locator('.neoscorpion-fueler-off-form button').click()
                expect(card(a2)).to_contain_text("Truck released")
                card(a2).locator('.neoscorpion-separate-visit summary').click()
                card(a2).locator('[name=separate_visit_confirmed]').check()
                card(a2).locator('[data-new-visit-form] button').click()
                expect(card(a2)).to_have_count(0)
                a3=current_id()
                self.assertNotIn(a3,{a1,a2,b})
                page.reload()
                expect(page.locator('[data-fuel-data-card]')).to_have_count(3)
                self.assertEqual(page.locator('[id^="apu-manual-"]').evaluate_all(
                    'els=>new Set(els.map(el=>el.id)).size'),3)
                save_readings(card(a2),"6","14")
                expect(card(a2)).to_have_count(0)
                with fixture.app.app_context():
                    self.assertIsNone(db.session.get(NeoScorpionFuelAssignment,assignment_id).completed_at_utc)
                card(a3).locator('[data-truck-on-form] button').click()
                expect(card(a3).locator('.neoscorpion-truck-on-row')).to_contain_text("CONFIRMED")
                save_readings(card(a3),"14","15","500")
                expect(card(a3).locator('[data-fuel-data-status]')).to_contain_text("Saved")
                clock[0]+=timedelta(minutes=2)
                card(a3).locator('.neoscorpion-fueler-off-form button').click()
                expect(page.locator('[data-fuel-data-card]')).to_have_count(1)
                save_readings(card(b),"7","10")
                expect(page.locator('[data-fuel-data-card]')).to_have_count(0)
                page.reload(); expect(page.locator('[data-fuel-data-card]')).to_have_count(0)
                with fixture.app.app_context():
                    from app.models import NeoScorpionFuelingEvent
                    events=NeoScorpionFuelingEvent.query.order_by(NeoScorpionFuelingEvent.id).all()
                    self.assertEqual([(e.fuel_work_state_id,e.transfer_fuel_gallons) for e in events],[(a1,3000),(a2,2000),(a3,500),(b,1000)])
                    self.assertEqual(NeoScorpionSortTruck.query.one().current_gallons,3500)
                    self.assertEqual(db.session.get(NeoScorpionFuelWorkState,a1).physical_transfer_gallons,3000)
                self.assertFalse(errors,errors)
        finally:
            if browser: browser.close()
            fixture.tearDownClass()

    def test_preassignment_travel_dirty_refresh_and_explicit_start_desktop_mobile(self):
        fixture = fixtures.MobileDrawerBrowserTest
        fixture.setUpClass(); fixture.app.config["LIVE_SCREEN_REFRESH_INTERVAL_MS"] = 5000
        browser = None
        clock = [datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)]
        class Clock(datetime):
            @classmethod
            def utcnow(cls):
                return clock[0]
        try:
            with fixture.app.app_context():
                gateway = ensure_default_gateway_and_nodes()
                admin = User.query.filter_by(username="drawer-admin").one()
                other = User.query.filter_by(username="drawer-watcher").one()
                operation = SortDateOperation(generated_by_user_id=admin.id, gateway_id=gateway.id,
                    gateway_code=gateway.code, sort_date=date.today(), sort_name="night", window_minutes=60)
                truck = NeoScorpionFuelTruck(gateway_id=gateway.id, truck_number="20", capacity_gallons=20000)
                db.session.add_all([operation, truck]); db.session.flush()
                db.session.add_all([NeoScorpionSettings(gateway_id=gateway.id,advance_setup_window_minutes=10),
                    NeoScorpionSortTruck(sort_date_operation_id=operation.id, fuel_truck_id=truck.id,
                        status="available", starting_gallons=10000, current_gallons=10000),
                    NeoScorpionSortAssetState(sort_date_operation_id=operation.id, revision=1)])
                missions, assignments, works = [], [], []
                for i, (tail, fueler) in enumerate((("N491UP", other),("N492UP", admin))):
                    mission = SortDateMission(sort_date_operation_id=operation.id, sort_date=operation.sort_date,
                        gateway_code=gateway.code, sort_name="night", mission_type="departure", mission_source="manual",
                        flight_number=f"UPS90{i}", origin=gateway.code, destination="SDF", assigned_tail_number=tail,
                        planned_datetime_utc=clock[0]+timedelta(hours=2), timezone="America/Chicago",
                        planned_fuel_load=52000, fuel_status="assigned")
                    arrival = SortDateMission(sort_date_operation_id=operation.id, sort_date=operation.sort_date,
                        gateway_code=gateway.code, sort_name="night", mission_type="arrival", mission_source="manual",
                        flight_number=f"UPS80{i}", origin="SDF", destination=gateway.code,
                        assigned_tail_number=tail, arrival_status="on_ground",
                        actual_block_in_datetime_utc=clock[0]-timedelta(hours=1))
                    db.session.add_all([mission, arrival]); db.session.flush()
                    assignment = NeoScorpionFuelAssignment(sort_date_operation_id=operation.id,
                        sort_date_mission_id=mission.id, assigned_fueler_user_id=fueler.id, assigned_truck_id=truck.id,
                        confirmed_tail_number=tail, created_at=clock[0]-timedelta(minutes=40),
                        fueler_assigned_at_utc=clock[0]-timedelta(minutes=40),
                        truck_assigned_at_utc=clock[0]-timedelta(minutes=20),
                        transfer_fuel_gallons=1000 if i==0 else None)
                    db.session.add(assignment); db.session.flush()
                    work = NeoScorpionFuelWorkState(fuel_assignment_id=assignment.id, tail_number=tail,
                        on_at_utc=clock[0]-timedelta(minutes=20) if i==0 else None,
                        confirmed_on_at_utc=clock[0]-timedelta(minutes=20) if i==0 else None,
                        apu_running=False, apu_allowance_lbs=0)
                    db.session.add_all([work, NeoScorpionTailFuelState(sort_date_operation_id=operation.id,
                        tail_number=tail, inbound_fuel_lbs=12000), SortDateTailState(sort_date=operation.sort_date,
                        gateway_code=gateway.code, sort_name="night", tail_number=tail, aircraft_type="757", aircraft_type_source="derived"),
                        SortDateParkingAssignment(sort_date_operation_id=operation.id, tail_number=tail,
                            ramp_code="A" if i==0 else "B", position_code="A04" if i==0 else "B06")])
                    db.session.flush()
                    if i == 0:
                        for code, remaining, actual in (("left",12000,26000),("ctr",0,0),("right",0,26000)):
                            db.session.add(NeoScorpionFuelTankState(fuel_work_state_id=work.id, tank_code=code,
                                remaining_lbs=remaining, actual_lbs=actual, created_at=clock[0]-timedelta(minutes=21)))
                    missions.append(mission.id); assignments.append(assignment.id); works.append(work.id)
                db.session.commit(); operation_id, gateway_id, other_id, truck_id = operation.id, gateway.id, other.id, truck.id

            with patch("app.services.neoscorpion.current_existing_operational_sort_operations",
                       side_effect=lambda gateway, now=None:[db.session.get(SortDateOperation,operation_id)]), patch("app.services.neoscorpion.datetime", Clock):
                browser = fixture.pw.chromium.launch()
                page = browser.new_page(viewport={"width":1440,"height":950})
                fixture().login(page)
                page.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1','seen')")
                page.clock.install(time=clock[0].replace(tzinfo=timezone.utc))
                errors=[]; page.on("pageerror",lambda error:errors.append(str(error)))
                for width in (1440,390):
                    page.set_viewport_size({"width":width,"height":950})
                    fixture().ready(page,"/neoscorpion/fuel-dispatch")
                    future=page.locator(f'tr[data-dispatch-mission-id="{missions[1]}"]')
                    expect(future.locator('[data-fuel-status]')).to_have_attribute('data-fuel-status','assigned')
                    expect(future).to_contain_text('TRUCK STILL FUELING')
                    self.assertEqual(future.locator('td:visible').count(),16 if width==1440 else 5)
                    self.assertFalse(page.evaluate('document.documentElement.scrollWidth > innerWidth+1'))
                fixture().ready(page,"/neoscorpion/fueler")
                card=page.locator('[data-fuel-data-card]').first
                expect(card.locator('[data-assigned-truck-status]')).to_have_text('CURRENTLY FUELING ON A04')
                for code, value in (('left','12'),('ctr','0'),('right','0')):
                    card.locator(f'[name="remaining_{code}"]').fill(value)
                save=card.locator('[data-fuel-planning-form] button[type="submit"]')
                save.click()
                expect(card.locator('[data-fuel-data-status]')).to_contain_text('Saved',timeout=15000)
                with fixture.app.app_context():
                    self.assertIsNone(db.session.get(NeoScorpionFuelWorkState,works[1]).on_at_utc)
                reading=card.locator('[name="remaining_left"]')
                original=reading.input_value(); reading.fill('13.5')
                with fixture.app.app_context():
                    service.mark_fueler_off(db.session.get(type(gateway),gateway_id),db.session.get(User,other_id),assignments[0],now_utc=clock[0])
                    db.session.commit()
                page.clock.run_for(6000)
                expect(reading).to_have_value('13.5')
                expect(card.locator('[data-assigned-truck-status]')).to_have_text('CURRENTLY FUELING ON A04')
                reading.fill(original); card.locator('.neoscorpion-fueler-truck').click()
                page.clock.run_for(6000)
                expect(card.locator('[data-assigned-truck-status]')).to_have_text('COMING FROM ALPHA · ~2 MIN',timeout=15000)
                for width in (1440,390):
                    page.set_viewport_size({"width":width,"height":950})
                    fixture().ready(page,"/neoscorpion/fuel-dispatch")
                    future=page.locator(f'tr[data-dispatch-mission-id="{missions[1]}"]')
                    line=future.locator('[data-truck-travel-until]')
                    expect(line).to_have_text('COMING FROM ALPHA · ~2 MIN')
                    self.assertTrue(line.evaluate('el => el.scrollWidth <= el.clientWidth+1'))
                    expect(future.locator('[data-fuel-status]')).to_have_attribute('data-fuel-status','assigned')
                clock[0]+=timedelta(minutes=3)
                page.clock.set_system_time(clock[0].replace(tzinfo=timezone.utc)); page.clock.run_for(1000)
                expect(line).to_have_text('TRUCK WAIT')
                with fixture.app.app_context():
                    self.assertIsNone(db.session.get(NeoScorpionFuelWorkState,works[1]).on_at_utc)
                fixture().ready(page,"/neoscorpion/fueler")
                card=page.locator('[data-fuel-data-card]').first
                expect(card.locator('[data-assigned-truck-status]')).to_have_text('TRUCK WAIT')
                card.locator('[data-truck-on-form] button').click()
                expect(card.locator('.neoscorpion-truck-on-row')).to_contain_text('CONFIRMED',timeout=15000)
                self.assertFalse(page.evaluate('document.documentElement.scrollWidth > innerWidth+1'))
                with fixture.app.app_context():
                    work=db.session.get(NeoScorpionFuelWorkState,works[1])
                    self.assertEqual(work.manual_on_at_utc,clock[0]); self.assertIsNone(work.on_at_utc)
                    self.assertEqual(db.session.get(NeoScorpionFuelWorkState,works[0]).on_at_utc,clock[0]-timedelta(minutes=23))
                for width in (1440,390):
                    page.set_viewport_size({"width":width,"height":950})
                    fixture().ready(page,"/neoscorpion/fuel-dispatch")
                    future=page.locator(f'tr[data-dispatch-mission-id="{missions[1]}"]')
                    expect(future.locator('[data-fuel-status]')).to_have_attribute('data-fuel-status','fueling')
                    expect(future.locator('[data-fueler-off-estimate]')).to_have_text(re.compile(r'^EST OFF \d{2}:\d{2} \(in 20m\) 0m PROF · BASELINE$'))
                    self.assertEqual(future.locator('td:visible').count(),16 if width==1440 else 5)
                    self.assertFalse(page.evaluate('document.documentElement.scrollWidth > innerWidth+1'))
                page.set_viewport_size({"width":390,"height":844})
                fixture().ready(page,"/neoscorpion/fueler")
                card=page.locator('[data-fuel-data-card]').first
                expect(card.locator('.neoscorpion-fueler-mission-head')).to_contain_text('100 PROF OFF')
                for code,value in (('left','26'),('ctr','0'),('right','26')):
                    card.locator(f'[name="actual_{code}"]').fill(value)
                card.locator('[name="transfer_fuel_gallons"]').fill('5970')
                clock[0]+=timedelta(minutes=10)
                card.locator('.neoscorpion-fueler-off-form button').click()
                expect(card).to_contain_text('Truck released. Save outstanding',timeout=15000)
                expect(card.locator('[name="actual_left"]')).to_have_value('26')
                expect(card.locator('[name="transfer_fuel_gallons"]')).to_have_value('5970')
                expect(card.locator('[data-fuel-data-status]')).to_contain_text('Unsaved Fuel Entry kept')
                with fixture.app.app_context():
                    work=db.session.get(NeoScorpionFuelWorkState,works[1])
                    self.assertEqual(work.off_at_utc,clock[0])
                    self.assertIsNone(db.session.get(NeoScorpionFuelAssignment,assignments[1]).completed_at_utc)
                    self.assertFalse(NeoScorpionSortTruck.query.filter_by(sort_date_operation_id=operation_id,fuel_truck_id=truck_id).one().inventory_verified)
                clock[0]+=timedelta(minutes=2)
                card.locator('[data-fuel-planning-form] button[type="submit"]').click()
                expect(page.locator('[data-fuel-data-card]')).to_have_count(0,timeout=15000)
                with fixture.app.app_context():
                    self.assertIsNotNone(db.session.get(NeoScorpionFuelAssignment,assignments[1]).completed_at_utc)
                for width in (1440,390):
                    page.set_viewport_size({"width":width,"height":844})
                    fixture().ready(page,"/neoscorpion/fuel-dispatch")
                    done=page.locator(f'tr[data-dispatch-mission-id="{missions[1]}"]')
                    expect(done.locator('[data-fuel-status]')).to_have_attribute('data-fuel-status','complete')
                    expect(done.locator('[data-fuel-status-secondary]')).to_contain_text('TRUCK OFF')
                    self.assertEqual(done.locator('[data-fueler-off-estimate]').count(),0)
                    fixture().ready(page,f"/neoscorpion/reports/prof?operation_id={operation_id}")
                    expect(page.locator('.neoscorpion-prof-report')).to_contain_text('Fueler formula')
                    self.assertFalse(page.evaluate('document.documentElement.scrollWidth > innerWidth+1'))
                page.context.grant_permissions(["clipboard-read","clipboard-write"])
                page.locator('[data-prof-copy]').click()
                expect(page.locator('[data-prof-copy-status]')).to_have_text('Copied')
                self.assertIn('Fueler formula',page.evaluate('navigator.clipboard.readText()'))
                self.assertFalse(errors,errors)
        finally:
            if browser: browser.close()
            fixture.tearDownClass()
