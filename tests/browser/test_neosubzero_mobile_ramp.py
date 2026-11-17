"""Shared desktop geometry on mobile; actual Deice Dispatch START/END/CLEAR routes."""
import json
import unittest
from datetime import datetime
from pathlib import Path
from playwright.sync_api import expect
from app.extensions import db
from app.models import Gateway,SortDateMission,SortDateOperation,SortDateParkingAssignment,NeoSubZeroDepartureDeiceEvent
from app.services.neosubzero_spray import set_neosubzero_ucc_truck
from tests.browser import test_neosubzero_ucc as fixtures
from tests.browser import test_neosubzero_operations as ops

EVIDENCE=Path('instance/ducc-ramp/browser')


class MobileRampBrowserTest(unittest.TestCase):
    def exercise(self,engine):
        case=fixtures.UccBrowserTest();case.setUpClass()
        EVIDENCE.mkdir(parents=True,exist_ok=True)
        browser=None;measurements=[]
        try:
            with case.app.app_context():
                operation=db.session.get(SortDateOperation,case.operation_id)
                # Synthetic canonical Block-Out inputs begin empty, then change
                # below. The UI must use the normal server projection, not a
                # client movement simulation or fabricated treatment event.
                for mid in case.missions[:3]:
                    mission=db.session.get(SortDateMission,mid)
                    mission.actual_block_out_datetime_utc=None
                fixture=fixtures.NeoSubZeroUccTest();fixture.operation=operation
                extra=fixture._departure('UPS9994','N9994UP','DEN',4)
                db.session.add(SortDateParkingAssignment(sort_date_operation_id=operation.id,
                    tail_number=extra.assigned_tail_number,ramp_code='A',position_code='A06'))
                fixture._deice_event(extra,'deice_planned');extra_id=extra.id
                from app.services.neosubzero_trucks import manage_truck
                manage_truck(db.session.get(Gateway,operation.gateway_id),'add_truck',{'truck_number':'MOBILE-10'},actor_id=None)
                set_neosubzero_ucc_truck(operation,'Alpha',1,'MOBILE-10')
                db.session.commit()
            browser=getattr(case.pw,engine).launch()
            context=browser.new_context(viewport={'width':390,'height':844},service_workers='block')
            page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            case.login(page);case.ready(page,'/neosubzero/ucc')
            expect(page.locator('[data-ucc-view="staffing"]')).to_have_attribute('aria-pressed','true')
            expect(page.locator('[data-ucc-ramp="Alpha"] .ucc-ramp-diagram')).to_be_hidden()
            expect(page.locator('.neosubzero-weather')).to_be_visible()
            expect(page.locator('.neosubzero-weather-ambient,[data-weather-motion-toggle]')).to_have_count(0)
            # A real staffing draft survives switching mode and critical refresh.
            editor=page.locator('[data-ucc-editor="Alpha-1"]')
            draft=editor.locator('[name="truck_number"]');draft.evaluate('(e)=>{e.value="UCC-831";e.dispatchEvent(new Event("input",{bubbles:true}))}')
            page.locator('[data-ucc-view="ramp"]').click()
            root=page.locator('[data-subzero-ucc]')
            alpha=page.locator('[data-ucc-ramp="Alpha"]')
            expect(alpha.locator('[data-ucc-parking="A03"] [data-ucc-aircraft]')).to_have_attribute('data-ucc-aircraft',str(case.missions[0]))
            for width in (320,375,390,430):
                page.set_viewport_size({'width':width,'height':844})
                expect(alpha.locator('.ucc-ramp-diagram')).to_be_visible()
                visible=page.locator('[data-ucc-ramp]:visible')
                expect(visible).to_have_count(1)
                for selector in ('.neosubzero-weather','[data-ucc-upcoming]','.neosubzero-ucc-fluid','.ucc-team'):
                    expect(page.locator(selector+':visible')).to_have_count(0)
                geometry=page.evaluate("""() => ({width:innerWidth,overflow:document.documentElement.scrollWidth-innerWidth,
                  labels:[...document.querySelectorAll('.is-mobile-selected .ucc-plane strong')].map(e=>({text:e.textContent,
                    width:e.getBoundingClientRect().width,scroll:e.scrollWidth,font:parseFloat(getComputedStyle(e).fontSize)}))})""")
                self.assertLessEqual(geometry['overflow'],1)
                self.assertTrue(all(x['scroll']<=x['width']+1 and x['font']>=12 for x in geometry['labels']),geometry)
                measurements.append(geometry)
                page.screenshot(path=str(EVIDENCE/f'{engine}-{width}-parking.png'),full_page=True)
            page.locator('[data-ucc-ramp-select="Bravo"]').click()
            expect(page.locator('[data-ucc-ramp="Bravo"]')).to_be_visible()
            page.locator('[data-ucc-ramp="Bravo"] [data-ucc-aircraft]').first.click()
            expect(page.locator('[data-ucc-info]')).to_be_visible()
            expect(page.locator('[data-ucc-info]')).to_contain_text('B08')
            page.locator('[data-ucc-ramp-select="Remote"]').click()
            expect(page.locator('[data-ucc-info]')).to_be_hidden()
            expect(page.locator('[data-ucc-ramp="Remote"]')).to_be_visible()
            expect(page.locator('[data-ucc-ramp="Remote"] [data-ucc-deice-pad]')).to_have_count(0)
            page.locator('[data-ucc-ramp-select="Alpha"]').click()
            # Authoritative Block-Out changes arrive through the normal revision
            # endpoint. Four aircraft fill the four waiting spaces; no spray is inferred.
            with case.app.app_context():
                for index,mid in enumerate([*case.missions[:3],extra_id]):
                    mission=db.session.get(SortDateMission,mid)
                    mission.actual_block_out_datetime_utc=datetime(2026,10,23,5,index)
                    mission.actual_block_out_source='manual'
                db.session.commit()
            expect(alpha.locator('[data-ucc-deice-pad] [data-ucc-aircraft]')).to_have_count(0,timeout=10000)
            expect(alpha.locator('[data-ucc-queue-position="1"] [data-ucc-aircraft]')).to_have_attribute('data-ucc-aircraft',str(case.missions[0]))
            expect(alpha.locator('[data-ucc-queue-position="2"] [data-ucc-aircraft]')).to_have_attribute('data-ucc-aircraft',str(case.missions[1]))
            expect(alpha.locator('[data-ucc-queue-position="3"] [data-ucc-aircraft]')).to_have_attribute('data-ucc-aircraft',str(case.missions[2]))
            expect(alpha.locator('[data-ucc-queue-position="4"] [data-ucc-aircraft]')).to_have_attribute('data-ucc-aircraft',str(extra_id))
            expect(alpha.locator('.ucc-queue-exception')).to_have_count(0)
            expect(root).to_have_attribute('data-ucc-mobile-view','ramp')
            expect(root).to_have_attribute('data-ucc-selected-ramp','Alpha')
            page.screenshot(path=str(EVIDENCE/f'{engine}-queued.png'),full_page=True)
            # The DUCC shortcut opens the selected ramp in Deice Dispatch.
            dispatch = context.new_page()
            case.ready(dispatch, '/neosubzero/ucc?view=ramp&ramp=Bravo')
            dispatch.locator('[data-ucc-back-coordinator]').click()
            expect(dispatch.locator('[data-ops-ramp]')).to_have_value('Bravo')
            ops.OperationsBrowserTest.ready(case, dispatch, f'/neosubzero/coordinator?ramp=Alpha&mission={case.missions[0]}')
            expect(dispatch.locator('[data-ops-ramp]')).to_have_value('Alpha')
            editor = dispatch.locator(f'[data-ops-row][data-mission-id="{case.missions[0]}"] [data-ops-inline]')
            treatment=editor.locator('[data-subzero-deice-form]')
            treatment.locator('[name="treatment_plan"]').select_option('one_type_i')
            treatment.locator('[name="pass1_surface_area"]').select_option('wings_only')
            treatment.locator('[name="pass1_start"]').fill('22:10')
            ops.OperationsBrowserTest.submit(case, dispatch, treatment.locator('button[type="submit"]'))
            expect(alpha.locator('[data-ucc-deice-pad] .state-deicing')).to_have_count(1,timeout=10000)
            page.screenshot(path=str(EVIDENCE/f'{engine}-deicing.png'),full_page=True)
            treatment=editor.locator('[data-subzero-deice-form]')
            treatment.locator('[name="pass1_end"]').fill('22:15')
            ops.OperationsBrowserTest.submit(case, dispatch, treatment.locator('button[type="submit"]'))
            expect(alpha.locator('[data-ucc-deice-pad] .state-finished')).to_have_count(1,timeout=10000)
            ops.OperationsBrowserTest.submit(case, dispatch, editor.locator('button[name="action"][value="clear"]'))
            expect(alpha.locator('[data-ucc-deice-pad] .state-radio-pending')).to_have_count(1,timeout=10000)
            expect(alpha.locator('[data-ucc-deice-pad] [data-ucc-aircraft]')).to_have_attribute('data-ucc-aircraft',str(case.missions[0]))
            ops.OperationsBrowserTest.submit(case, dispatch, editor.locator('button[name="action"][value="radio_complete"]'))
            expect(alpha.locator('[data-ucc-aircraft="%s"]'%case.missions[0])).to_have_count(0,timeout=10000)
            expect(alpha.locator('[data-ucc-deice-pad] [data-ucc-aircraft]')).to_have_count(0)
            expect(alpha.locator('[data-ucc-queue-position="1"] [data-ucc-aircraft]')).to_have_attribute('data-ucc-aircraft',str(case.missions[1]))
            expect(alpha.locator('[data-ucc-queue-position="2"] [data-ucc-aircraft]')).to_have_attribute('data-ucc-aircraft',str(case.missions[2]))
            expect(alpha.locator('[data-ucc-queue-position="3"] [data-ucc-aircraft]')).to_have_attribute('data-ucc-aircraft',str(extra_id))
            page.screenshot(path=str(EVIDENCE/f'{engine}-clear-advanced.png'),full_page=True)
            page.locator('[data-ucc-view="staffing"]').click()
            expect(draft).to_have_value('UCC-831')
            page.locator('[data-ucc-view="ramp"]').click()
            page.locator('[data-ucc-ramp-select="Echo"]').click()
            page.reload()
            expect(root).to_have_attribute('data-ucc-mobile-view','ramp')
            expect(root).to_have_attribute('data-ucc-selected-ramp','Echo')
            page.set_viewport_size({'width':1920,'height':900})
            expect(page.locator('[data-ucc-ramp]:visible')).to_have_count(6)
            expect(page.locator('.ucc-mobile-controls')).to_be_hidden()
            expect(page.locator('.neosubzero-weather')).to_be_visible()
            page.screenshot(path=str(EVIDENCE/f'{engine}-desktop.png'),full_page=True)
            viewer=browser.new_context(viewport={'width':375,'height':844},service_workers='block')
            read=viewer.new_page();case.login(read,'drawer-watcher');case.ready(read,'/neosubzero/ucc?view=ramp&ramp=Alpha')
            read.locator('[data-ucc-queue-position="1"] [data-ucc-aircraft]').click()
            expect(read.locator('[data-ucc-info]')).to_be_visible()
            expect(read.locator('[data-ucc-queue-form], [data-ucc-drag-assignee]')).to_have_count(0)
            with case.app.app_context():
                event=NeoSubZeroDepartureDeiceEvent.query.filter_by(sort_date_mission_id=case.missions[0]).one()
                self.assertEqual(event.status,'cleared');self.assertIsNotNone(event.pass1_started_at_utc)
                self.assertIsNotNone(event.pass1_ended_at_utc)
                self.assertIsNotNone(event.radio_completed_at_utc)
            self.assertFalse(errors,errors)
        finally:
            (EVIDENCE/f'{engine}-measurements.json').write_text(json.dumps(measurements,indent=2))
            if browser:browser.close()
            case.tearDownClass()

    def test_chromium_mobile_ramp(self):self.exercise('chromium')
    def test_webkit_mobile_ramp(self):self.exercise('webkit')
