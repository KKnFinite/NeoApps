"""Authenticated real browser: computed colors, clock, FOB saves, ACK and polling."""
from datetime import date, datetime, timedelta
import unittest
from unittest.mock import patch

from playwright.sync_api import expect

from app.extensions import db
from app.models import (NeoScorpionCallDispatchAck, NeoScorpionFuelAssignment, NeoScorpionFuelTankState,
    NeoScorpionFuelTruck, NeoScorpionFuelWorkState, NeoScorpionSettings, NeoScorpionSortAssetState,
    NeoScorpionSortFueler, NeoScorpionSortTruck, NeoScorpionTailFuelState, SortDateMission,
    SortDateOperation, SortDateParkingAssignment, SortDateTailState, User)
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.neoscorpion_assets import record_nightly_operational_change
from tests.browser import test_mobile_drawer as fixture_module


class FuelingStatusBrowserTest(unittest.TestCase):
    def test_desktop_mobile_styles_clock_live_fob_verification_and_dispatcher_ack(self):
        Fixture = fixture_module.MobileDrawerBrowserTest
        Fixture.setUpClass()
        Fixture.app.config["LIVE_SCREEN_REFRESH_INTERVAL_MS"] = 5000
        browser = None
        try:
            with Fixture.app.app_context():
                gateway = ensure_default_gateway_and_nodes()
                admin = User.query.filter_by(username="drawer-admin").one()
                operation = SortDateOperation(generated_by_user_id=admin.id, gateway_id=gateway.id,
                    gateway_code=gateway.code, sort_date=date.today(), sort_name="night", window_minutes=60)
                db.session.add(operation); db.session.flush()
                truck = NeoScorpionFuelTruck(gateway_id=gateway.id, truck_number="Truck-with-long-operational-label",
                                            capacity_gallons=10000, is_active=True)
                db.session.add(truck); db.session.flush()
                db.session.add_all([NeoScorpionSettings(gateway_id=gateway.id),
                    NeoScorpionSortFueler(sort_date_operation_id=operation.id, user_id=admin.id),
                    NeoScorpionSortTruck(sort_date_operation_id=operation.id, fuel_truck_id=truck.id,
                        status="available", starting_gallons=10000, current_gallons=10000),
                    NeoScorpionSortAssetState(sort_date_operation_id=operation.id, revision=1)])
                ids, assignments = {}, {}
                now = datetime.utcnow()
                for index, stage in enumerate(("pending", "ready", "assigned", "fueling", "off", "fob-ready", "complete", "fob", "review", "risk", "check")):
                    tail = f"N49{index}UP"
                    mission = SortDateMission(sort_date_operation_id=operation.id, sort_date=operation.sort_date,
                        gateway_code=gateway.code, sort_name="night", mission_type="departure", mission_source="manual",
                        flight_number=f"UPS90{index}", origin=gateway.code, destination="SDF",
                        assigned_tail_number=tail, timezone="America/Chicago", planned_source="manual",
                        planned_fuel_load=25000 if stage in {"check", "fob-ready", "fob"} else 50000,
                        planned_datetime_utc=now+timedelta(minutes=31 if stage=="ready" else 10 if stage=="risk" else 180),
                        fuel_status="complete" if stage in {"complete","fob"} else "waiting", departure_status="loading")
                    arrival = SortDateMission(sort_date_operation_id=operation.id, sort_date=operation.sort_date,
                        gateway_code=gateway.code, sort_name="night", mission_type="arrival", mission_source="manual",
                        flight_number=f"UPS80{index}", origin="SDF", destination=gateway.code,
                        assigned_tail_number=tail, arrival_status="scheduled" if stage=="pending" else "on_ground",
                        actual_block_in_datetime_utc=now-timedelta(minutes=15) if stage=="risk" else None)
                    db.session.add_all([mission, arrival,
                        SortDateTailState(sort_date=operation.sort_date, gateway_code=gateway.code, sort_name="night",
                            tail_number=tail, aircraft_type="757", aircraft_type_source="derived"),
                        SortDateParkingAssignment(sort_date_operation_id=operation.id, tail_number=tail,
                            ramp_code="B", position_code=f"B{6+index:02d}", lane_number=1),
                        NeoScorpionTailFuelState(sort_date_operation_id=operation.id, tail_number=tail, inbound_fuel_lbs=30000)])
                    db.session.flush()
                    assignment = NeoScorpionFuelAssignment(sort_date_operation_id=operation.id, sort_date_mission_id=mission.id,
                        assigned_fueler_user_id=admin.id if stage not in {"pending","ready"} else None,
                        assigned_truck_id=truck.id if stage in {"assigned","fueling","off","risk","review"} else None,
                        confirmed_tail_number=tail, completed_at_utc=now if stage=="complete" else None,
                        fuel_on_board_at_utc=now if stage=="fob" else None,
                        review_status="complete" if stage in {"complete","fob"} else "pending",
                        operational_status="hold_review" if stage=="review" else "active",
                        hold_reason="Genuine interruption requiring dispatcher resolution with a very long operational reason" if stage=="review" else None)
                    db.session.add(assignment); db.session.flush()
                    if stage in {"fueling","off","fob-ready","risk"}:
                        work = NeoScorpionFuelWorkState(fuel_assignment_id=assignment.id, tail_number=tail,
                            on_at_utc=now-timedelta(minutes=2), off_at_utc=now if stage=="off" else None,
                            apu_running=False if stage=="fob-ready" else None,
                            apu_allowance_lbs=0 if stage=="fob-ready" else None,
                            automatic_apu_allowance_lbs=0 if stage=="fob-ready" else None)
                        db.session.add(work); db.session.flush()
                        if stage=="fob-ready":
                            for code, lbs in (("left",15000),("ctr",0),("right",15000)):
                                db.session.add(NeoScorpionFuelTankState(fuel_work_state_id=work.id, tank_code=code,
                                    remaining_lbs=lbs, actual_lbs=lbs))
                    ids[stage], assignments[stage] = mission.id, assignment.id
                db.session.commit(); operation_id = operation.id

            with patch("app.services.neoscorpion.current_existing_operational_sort_operations",
                       side_effect=lambda gateway, now=None: [db.session.get(SortDateOperation, operation_id)]):
                browser = Fixture.pw.chromium.launch()
                context = browser.new_context(viewport={"width":1440,"height":950})
                page = context.new_page(); Fixture().login(page)
                page.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1','seen')")
                errors=[]; page.on("pageerror", lambda error: errors.append(str(error)))
                requests=[]; page.on("request",lambda request: requests.append(request.url))
                Fixture().ready(page,"/neoscorpion/fuel-dispatch")
                def row(stage): return page.locator(f'.neoscorpion-dispatch-primary-row[data-dispatch-mission-id="{ids[stage]}"]')
                def status(stage): return row(stage).locator('[data-fuel-status]')
                colors={"pending":"gray","ready":"amber","assigned":"orange","fueling":"yellow","off":"teal",
                        "fob-ready":"teal","complete":"green","fob":"green","review":"red","risk":"red","check":"orange"}
                backgrounds={"gray":"rgb(40, 49, 60)","amber":"rgb(68, 56, 28)","orange":"rgb(73, 43, 27)",
                    "yellow":"rgb(64, 60, 26)","teal":"rgb(22, 59, 59)","green":"rgb(25, 60, 41)","red":"rgb(73, 31, 43)"}
                foregrounds={"gray":"rgb(181, 190, 201)","amber":"rgb(244, 198, 107)","orange":"rgb(255, 177, 118)",
                    "yellow":"rgb(246, 227, 122)","teal":"rgb(119, 221, 211)","green":"rgb(148, 224, 170)","red":"rgb(255, 145, 156)"}
                for stage, color in colors.items():
                    expect(status(stage)).to_have_attribute('data-fuel-status-color',color)
                    self.assertEqual(status(stage).locator('.neoscorpion-fuel-status-chip').evaluate('(el)=>getComputedStyle(el).backgroundColor'), backgrounds[color])
                    self.assertEqual(status(stage).locator('.neoscorpion-fuel-status-chip').evaluate('(el)=>getComputedStyle(el).color'), foregrounds[color])
                self.assertEqual(status('check').locator('.neoscorpion-call-dispatch > strong').evaluate('(el)=>getComputedStyle(el).color'),'rgb(255, 115, 127)')
                expect(status('fueling')).to_contain_text('TIMING UNKNOWN')
                expect(status('risk')).not_to_contain_text('TIMING UNKNOWN')
                expect(status('check')).not_to_contain_text('FOB CHECK')
                expect(status('check').get_by_text('CALL DISPATCH', exact=True)).to_be_visible()
                self.assertIn('20261009-spear-display-v1',page.locator('link[href*="26-neoscorpion.css"]').get_attribute('href'))
                self.assertIn('20261009-fuel-status-fob-v1',page.locator('script[src*="neoscorpion_fuel_status.js"]').get_attribute('src'))

                for width in (1440,390):
                    page.set_viewport_size({"width":width,"height":950})
                    self.assertFalse(page.evaluate('document.documentElement.scrollWidth > innerWidth + 1'))
                    for stage in colors:
                        self.assertLessEqual(status(stage).locator('[data-fuel-status-secondary]').count(),1)
                        chip=status(stage).locator('.neoscorpion-fuel-status-chip')
                        self.assertGreaterEqual(chip.evaluate('el=>parseFloat(getComputedStyle(el).fontSize)'),12 if width==1440 else 10)
                        self.assertGreaterEqual(chip.evaluate('el=>parseFloat(getComputedStyle(el).fontWeight)'),800)
                        self.assertGreaterEqual(chip.evaluate('el=>parseFloat(getComputedStyle(el).paddingTop)'),5)
                        self.assertEqual(chip.evaluate('el=>getComputedStyle(el).whiteSpace'),'nowrap')
                        for secondary in status(stage).locator('[data-fuel-status-secondary]').all():
                            self.assertEqual(secondary.evaluate('el=>getComputedStyle(el).whiteSpace'),'nowrap')
                            self.assertTrue(secondary.evaluate('el=>el.scrollWidth <= el.clientWidth+1'),
                                (stage,width,secondary.evaluate('el=>({text:el.textContent,width:el.clientWidth,scroll:el.scrollWidth,font:getComputedStyle(el).font})')))
                        self.assertTrue(status(stage).evaluate('el=>el.scrollWidth <= el.clientWidth+1'),
                            (stage,width,status(stage).evaluate('el=>({width:el.clientWidth,scroll:el.scrollWidth,children:[...el.children].map(c=>({text:c.textContent,width:c.clientWidth,scroll:c.scrollWidth,font:getComputedStyle(c).font}))})')))
                        self.assertTrue(chip.evaluate('el=>el.scrollWidth <= el.clientWidth+1'),
                            (stage,width,chip.evaluate('el=>({width:el.clientWidth,scroll:el.scrollWidth,css:getComputedStyle(el).font})')))
                    if width == 390:
                        status('check').scroll_into_view_if_needed()
                        # The existing table scrolls locally; reveal STATUS on
                        # mobile and check its actual painted location.
                        self.assertTrue(status('check').evaluate('el=>{const r=el.getBoundingClientRect();return r.left>=0 && r.right<=innerWidth;}'))
                    page.screenshot(path=str(Fixture.evidence/f'fuel-status-{width}.png'),full_page=True)
                expect(status('pending')).to_have_text('PENDING')
                expect(row('pending').locator('[data-fuel-status-secondary]')).to_have_count(0)
                details=page.locator('[data-fuel-status-details]').filter(has_text='Needs Arrival')
                self.assertGreater(details.count(),0)

                # Real ACK POST, CSRF and existing fragment update; no confirmation dialog.
                page.set_viewport_size({"width":1440,"height":950})
                row('check').locator('[data-neoscorpion-dispatch-details]').click()
                page.get_by_role('button',name='Acknowledge CALL DISPATCH',exact=False).click()
                expect(status('check').locator('.neoscorpion-call-dispatch')).to_have_count(0)
                page.reload(wait_until='domcontentloaded')
                expect(status('check').locator('.neoscorpion-call-dispatch')).to_have_count(0)
                with Fixture.app.app_context():
                    ack = NeoScorpionCallDispatchAck.query.filter_by(mission_id=ids['check']).one()
                    self.assertIsNotNone(ack.acknowledged_at_utc)
                    self.assertEqual(ack.alert_values['required_lbs'],25000)

                # Fueler entry form is the existing form, including mobile controls.
                fueler = context.new_page(); fueler.set_viewport_size({"width":390,"height":844})
                Fixture().ready(fueler,'/neoscorpion/fueler')
                card = fueler.locator(f'[data-fuel-assignment-id="{assignments["check"]}"]')
                expect(card.locator('[data-fuel-status]')).to_contain_text('ASSIGNED')
                expect(fueler.locator('.neoscorpion-call-dispatch')).to_have_count(0)
                form = card.locator('[data-fuel-data-form]')
                if form.count()==0: form = card.locator('form[data-fuel-planning-form]')
                def save():
                    form.get_by_role('button',name='Save Fuel Entry',exact=True).click()
                    expect(card.locator('[data-fuel-data-status]')).to_contain_text('Saved')
                for code, value in (("left","15.0"),("ctr","0"),("right","15.0")):
                    form.locator(f'[name="remaining_{code}"]').fill(value)
                save()
                expect(card.locator('[data-fuel-status]')).to_contain_text('FUELING')
                expect(status('check')).to_have_attribute('data-fuel-status','fueling',timeout=20000)
                for code, value in (("left","15.0"),("ctr","0"),("right","15.0")):
                    form.locator(f'[name="actual_{code}"]').fill(value)
                form.locator('[data-apu-running]').select_option('no'); save()
                expect(card.locator('[data-fuel-status]')).to_have_attribute('data-fuel-status','fob-ready')
                expect(status('check')).to_have_attribute('data-fuel-status','fob-ready',timeout=20000)
                expect(status('check').locator('.neoscorpion-call-dispatch')).to_be_visible()
                with Fixture.app.app_context():
                    work = NeoScorpionFuelWorkState.query.filter_by(fuel_assignment_id=assignments['check']).one()
                    self.assertIsNone(work.off_at_utc)
                    self.assertIsNotNone(work.on_at_utc)
                    self.assertIsNone(db.session.get(NeoScorpionFuelAssignment,assignments['check']).assigned_truck_id)
                board=context.new_page(); Fixture().ready(board,'/neoscorpion/fueling-board')
                expect(board.locator(f'[data-board-assignment-id="{assignments["check"]}"] [data-fuel-status]')).to_have_attribute('data-fuel-status','fob-ready')
                expect(board.locator('.neoscorpion-call-dispatch')).to_have_count(0)
                # A local Required autosave must refresh status even after it adopts
                # the new revision; no page navigation or stale FOB button.
                required = row('check').locator('[data-autosave-field="required_fuel"]')
                required.fill('31.0'); required.press('Tab')
                expect(status('check')).to_have_attribute('data-fuel-status','fueling',timeout=20000)
                expect(status('check').locator('.neoscorpion-call-dispatch')).to_have_count(0)
                expect(row('check').get_by_role('button',name='FOB',exact=True)).to_have_count(0)
                self.assertTrue(any('live-panel' in url for url in requests))
                # Clock advance tests urgency without receiving another live response.
                page.route('**/neoscorpion/fuel-dispatch/live-panel', lambda route: route.abort())
                page.route('**/neoscorpion/fuel-dispatch/revision', lambda route: route.abort())
                page.clock.install(time=datetime.utcnow())
                expect(status('ready')).to_have_attribute('data-fuel-status-color','amber')
                page.clock.run_for(120000)
                expect(status('ready')).to_have_attribute('data-fuel-status-color','red')
                expect(status('ready')).to_have_attribute('data-fuel-status','ready')
                expect(status('fueling')).to_have_attribute('data-fuel-status-color','yellow')
                expect(status('complete')).to_have_attribute('data-fuel-status-color','green')
                self.assertFalse(errors)
        finally:
            if browser: browser.close()
            Fixture.tearDownClass()
