"""Explicit radio confirmation and shared live throat on phone/desktop fixtures."""
import json
import unittest
from datetime import datetime
from pathlib import Path
from playwright.sync_api import expect
from tests.browser import test_neosubzero_ucc as fixtures
from tests.browser import test_neosubzero_operations as ops
from app.extensions import db
from app.models import Gateway,SortDateOperation,SortDateMission,SortDateParkingAssignment,NeoSubZeroDepartureDeiceEvent,NeoSubZeroAuditEntry
from app.services.neosubzero_spray import set_neosubzero_ucc_truck

EVIDENCE=Path('instance/subzero-radio/browser')


class RadioBrowserTest(unittest.TestCase):
    def exercise(self,engine):
        case=fixtures.UccBrowserTest();case.setUpClass();browser=None;measurements=[]
        EVIDENCE.mkdir(parents=True,exist_ok=True)
        try:
            with case.app.app_context():
                operation=db.session.get(SortDateOperation,case.operation_id)
                fixture=fixtures.NeoSubZeroUccTest();fixture.operation=operation
                gone=fixture._departure('UPS-GONE','N-GONE','DEN',1);gone_id=gone.id
                gone.actual_block_out_datetime_utc=datetime.utcnow()
                negative=fixture._departure('UPS-NEG','N-NEG','DEN',2);neg_id=negative.id
                fixture._deice_event(negative,'negative')
                for mission,pos in ((gone,'A07'),(negative,'A08')):
                    db.session.add(SortDateParkingAssignment(sort_date_operation_id=operation.id,tail_number=mission.assigned_tail_number,ramp_code='A',position_code=pos))
                from app.services.neosubzero_trucks import manage_truck
                manage_truck(db.session.get(Gateway,operation.gateway_id),'add_truck',{'truck_number':'RADIO-10'},actor_id=None)
                set_neosubzero_ucc_truck(operation,'Alpha',1,'RADIO-10');db.session.commit()
            browser=getattr(case.pw,engine).launch()
            context=browser.new_context(viewport={'width':1920,'height':900},service_workers='block')
            errors=[]
            def page():
                p=context.new_page();p.on('pageerror',lambda e:errors.append(str(e)));return p
            desktop=page();case.login(desktop);case.ready(desktop,'/neosubzero/ucc')
            phone=page();phone.set_viewport_size({'width':390,'height':844});case.ready(phone,'/neosubzero/ucc?view=ramp&ramp=Alpha')
            coordinator=page();case.ready(coordinator,f'/neosubzero/coordinator?ramp=Alpha&mission={case.missions[0]}')
            treatment=coordinator.locator('[data-subzero-deice-form]')
            treatment.locator('[name="treatment_plan"]').select_option('one_type_i')
            treatment.locator('[name="pass1_surface_area"]').select_option('wings_only')
            treatment.locator('[name="pass1_start"]').fill('22:10')
            with coordinator.expect_navigation():treatment.locator('button[type="submit"]').click()
            treatment.locator('[name="pass1_end"]').fill('22:15')
            with coordinator.expect_navigation():treatment.locator('button[type="submit"]').click()
            with coordinator.expect_navigation():coordinator.locator('button[value="clear"]').click()
            expect(coordinator.locator('[data-radio-script]')).to_contain_text('2210')
            expect(coordinator.locator('button[value="radio_complete"]')).to_be_visible()
            with case.app.app_context():
                event=NeoSubZeroDepartureDeiceEvent.query.filter_by(sort_date_mission_id=case.missions[0]).one()
                event_id=event.id;clear=event.cleared_at_utc;self.assertIsNone(event.radio_completed_at_utc)
                before=NeoSubZeroAuditEntry.query.count()
            for p in (desktop,phone):
                pad=p.locator('[data-ucc-ramp="Alpha"] [data-ucc-deice-pad]')
                expect(pad.locator('.state-radio-pending')).to_have_count(1,timeout=10000)
                expect(pad.locator('[data-ucc-aircraft]')).to_have_attribute('data-ucc-aircraft',str(case.missions[0]))
                self.assertEqual(pad.locator('.state-radio-pending .ucc-plane-icon').evaluate('e=>getComputedStyle(e).color'),'rgb(240, 45, 159)')
                p.screenshot(path=str(EVIDENCE/f'{engine}-ducc-{p.viewport_size["width"]}-pending.png'))
            board=page();ops.OperationsBrowserTest.ready(case,board,'/neosubzero/operations?mode=outbound')
            for width in (320,375,390,430,1366,1920):
                board.set_viewport_size({'width':width,'height':900})
                for layout in ('list','visual'):
                    ops.OperationsBrowserTest.layout(case,board,layout)
                    row=board.locator(f'[data-ops-row][data-mission-id="{case.missions[0]}"]')
                    expect(row).to_be_visible();expect(row).to_have_attribute('data-status','radio-pending')
                    self.assertEqual(row.evaluate('e=>getComputedStyle(e).backgroundColor'),'rgb(240, 45, 159)')
                    expect(board.locator(f'[data-mission-id="{gone_id}"]')).to_have_attribute('data-status','gone')
                    expect(board.locator(f'[data-mission-id="{neg_id}"]')).to_have_attribute('data-status','negative')
                    expect(board.locator(f'[data-mission-id="{case.missions[1]}"]')).to_have_attribute('data-status','planned')
                    states=board.locator('[data-ops-board]').evaluate('b=>[...b.querySelectorAll("[data-ops-row]")].filter(e=>e.getBoundingClientRect().height).map(e=>e.dataset.status)')
                    ranks=[2 if s=='gone' else 1 if s=='radio-pending' else 0 for s in states]
                    # Each visual ramp is independently ordered; this fixture's
                    # mixed states all belong to Alpha. List spans all ramps.
                    if layout=='list':self.assertEqual(ranks,sorted(ranks))
                    geometry=board.evaluate('({width:innerWidth,overflow:document.documentElement.scrollWidth-innerWidth})')
                    self.assertLessEqual(geometry['overflow'],1)
                    measurements.append(dict(engine=engine,layout=layout,**geometry))
                    if width in (390,1920):board.screenshot(path=str(EVIDENCE/f'{engine}-operations-{layout}-{width}-pending.png'))
            # Opening the readback through either screen never confirms it.
            row.locator('a').click();expect(board.locator('[data-radio-script]')).to_contain_text('2210')
            with case.app.app_context():
                event=db.session.get(NeoSubZeroDepartureDeiceEvent,event_id)
                self.assertIsNone(event.radio_completed_at_utc);self.assertEqual(NeoSubZeroAuditEntry.query.count(),before)
            board.locator('[data-ops-close]').click()
            board.locator(f'[data-ops-row][data-mission-id="{case.missions[1]}"] a').click()
            form=board.locator('[data-ops-detail] [data-subzero-deice-form]')
            form.locator('[name="treatment_plan"]').select_option('one_type_i')
            form.locator('[name="pass1_surface_area"]').select_option('wings_only')
            form.locator('[name="pass1_start"]').fill('22:16')
            with board.expect_response(lambda r:r.url.endswith('/operations/mutate') and r.request.method=='POST') as rejected:
                form.locator('button[type="submit"]').click()
            self.assertEqual(rejected.value.status,409)
            expect(form.locator('[name="pass1_start"]')).to_have_value('22:16')
            expect(board.locator('[data-ops-detail-feedback]')).to_contain_text('CLEAR and RADIO COMPLETE before START')
            # Synthetic MotherBrain identity change: the recorded physical tail
            # stays pink at its pad instead of advancing the next aircraft.
            with case.app.app_context():
                source=db.session.get(SortDateMission,case.missions[0])
                old_tail,old_block_out=source.assigned_tail_number,source.actual_block_out_datetime_utc
                source.assigned_tail_number='N-REPLACED';source.actual_block_out_datetime_utc=None;db.session.commit()
            for p in (desktop,phone):
                pad=p.locator('[data-ucc-ramp="Alpha"] [data-ucc-deice-pad]')
                expect(pad.locator('[data-ucc-aircraft]')).to_have_attribute('data-ucc-aircraft',f'radio-{event_id}',timeout=10000)
                expect(pad.locator('.state-radio-pending')).to_contain_text(old_tail)
            phone.locator(f'[data-ucc-aircraft="radio-{event_id}"]').click()
            expect(phone.locator('[data-ucc-info]')).to_contain_text('Mission/tail identity changed')
            phone.screenshot(path=str(EVIDENCE/f'{engine}-ducc-identity-review.png'))
            with case.app.app_context():
                source=db.session.get(SortDateMission,case.missions[0])
                source.assigned_tail_number=old_tail;source.actual_block_out_datetime_utc=old_block_out;db.session.commit()
            expect(phone.locator('[data-ucc-ramp="Alpha"] [data-ucc-deice-pad] [data-ucc-aircraft]')).to_have_attribute('data-ucc-aircraft',str(case.missions[0]),timeout=10000)
            with coordinator.expect_navigation():coordinator.locator('button[value="radio_complete"]').click()
            for p in (desktop,phone):
                pad=p.locator('[data-ucc-ramp="Alpha"] [data-ucc-deice-pad] [data-ucc-aircraft]')
                expect(pad).to_have_count(0,timeout=10000)
                expect(p.locator('[data-ucc-ramp="Alpha"] [data-ucc-queue-position="1"] [data-ucc-aircraft]')).to_have_attribute('data-ucc-aircraft',str(case.missions[1]))
            expect(board.locator(f'[data-ops-row][data-mission-id="{case.missions[0]}"]')).to_have_attribute('data-status','gone',timeout=10000)
            expect(form.locator('[name="pass1_start"]')).to_have_value('22:16')
            for p in (desktop,phone):p.screenshot(path=str(EVIDENCE/f'{engine}-ducc-{p.viewport_size["width"]}-advanced.png'))
            with case.app.app_context():
                event=db.session.get(NeoSubZeroDepartureDeiceEvent,event_id)
                self.assertEqual(event.cleared_at_utc,clear);self.assertEqual(event.radio_state,'complete')
                self.assertIsNotNone(event.radio_completed_by_user_id);self.assertIsNotNone(event.radio_completed_at_utc)
                self.assertEqual(NeoSubZeroAuditEntry.query.filter_by(action='departure_radio_complete').count(),1)
                self.assertEqual(NeoSubZeroDepartureDeiceEvent.query.filter_by(sort_date_mission_id=gone_id).count(),0)
            self.assertFalse(errors,errors)
        finally:
            (EVIDENCE/f'{engine}-measurements.json').write_text(json.dumps(measurements,indent=2))
            if browser:browser.close()
            case.tearDownClass()

    def test_chromium_radio_completion(self):self.exercise('chromium')
    def test_webkit_radio_completion(self):self.exercise('webkit')
