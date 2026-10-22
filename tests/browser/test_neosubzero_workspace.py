"""Whole-node geometry audit using isolated current-sort operational fixtures."""
import json
import os
import re
import unittest
from datetime import datetime
from pathlib import Path
from playwright.sync_api import expect
from tests.browser import test_neosubzero_ucc as fixture_module
from app.extensions import db
from app.models import User,StaffingPerson,SortDateMission,SortDateOperation,SortDateTailState,NeoSubZeroDepartureDeiceEvent,NeoSubZeroSprayRecord


class SubZeroWorkspaceBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture_module.UccBrowserTest.setUpClass.__func__(cls)
        with cls.app.app_context():
            actor=User.query.filter_by(username='drawer-admin').one()
            actor.employee_id=db.session.get(StaffingPerson,cls.people[0]).employee_id
            # The reused helper predates this October sort; keep screenshots
            # production-shaped rather than displaying artificial +52-day times.
            for index,mid in enumerate(cls.missions):
                mission=db.session.get(SortDateMission,mid)
                mission.planned_datetime_utc=datetime(2026,10,23,8,index)
                mission.planned_datetime_local=datetime(2026,10,23,3,index)
            event=NeoSubZeroDepartureDeiceEvent.query.filter_by(sort_date_mission_id=cls.missions[0]).one()
            db.session.add(NeoSubZeroSprayRecord(sort_date_operation_id=cls.operation_id,sort_date_mission_id=cls.missions[0],
                departure_deice_event_id=event.id,pass_number=1,position_number=1,gallons=30,truck_number_snapshot='DEICE-831',
                pass_type='type_i',fluid_name_snapshot='Type I',concentration_percent_snapshot=50,surface_area='wings_tail',
                started_at_utc=datetime(2026,10,23,5,1),ended_at_utc=datetime(2026,10,23,5,8),
                driver_name_snapshot='Christopher Longname-Deicer-0',notes='Recorded fixture evidence. '*12))
            db.session.commit()
    tearDownClass=classmethod(fixture_module.UccBrowserTest.tearDownClass.__func__)
    login=fixture_module.UccBrowserTest.login
    ready=fixture_module.UccBrowserTest.ready

    def test_all_screens_use_the_available_workspace(self):
        phase=os.environ.get('SUBZERO_LAYOUT_PHASE','after')
        evidence=Path('instance/browser-evidence/subzero-workspace');evidence.mkdir(parents=True,exist_ok=True)
        results=[]
        for engine in ('chromium','webkit'):
            browser=getattr(self.pw,engine).launch()
            context=browser.new_context(viewport={'width':1440,'height':1000},service_workers='block')
            page=context.new_page();errors=[]
            if phase=='before':
                # Reproduce the original cascade against the same populated fixtures.
                page.route('**/neosubzero_workspace.css*',lambda route:route.fulfill(content_type='text/css',body=''))
            page.on('pageerror',lambda error:errors.append(str(error)))
            self.login(page)
            for screen in ('ucc','outbound','pretreat','coordinator','deicer-mobile','deice-log','callouts','qualifications','settings'):
                with self.subTest(engine=engine,screen=screen):self.ready(page,'/neosubzero/'+screen)
                expect(page.locator('.neosubzero-shell')).to_be_visible()
                for width in (320,375,430,768,1024,1440,1920,2560):
                    page.set_viewport_size({'width':width,'height':1000})
                    # WebKit publishes innerWidth before its layout viewport
                    # catches up. Measure the committed layout, not the old frame.
                    page.wait_for_function('(w)=>document.documentElement.clientWidth===w && document.querySelector("main.content").getBoundingClientRect().width===w',arg=width)
                    for collapsed in ((False,True) if width>900 else (False,)):
                        if width>900:
                            actual=page.evaluate("document.body.classList.contains('operational-sidebar-collapsed')")
                            if actual!=collapsed:page.locator('[data-operational-sidebar-toggle]').click()
                        measurement=page.evaluate("""() => {
                          const rect=s=>{const r=document.querySelector(s).getBoundingClientRect();return {x:r.x,right:r.right,width:r.width}};
                          const shell=rect('.neosubzero-shell'), content=rect('main.content');
                          const sidebar=document.querySelector('[data-operational-sidebar]');
                          const edge=sidebar && getComputedStyle(sidebar).display!=='none' ? sidebar.getBoundingClientRect().right : 0;
                          return {shell,content,sidebar:edge,left:shell.x-edge,right:innerWidth-shell.right,
                            overflow:document.documentElement.scrollWidth-innerWidth,
                            culprits:[...document.querySelectorAll('main.content *')].filter(e=>e.getBoundingClientRect().right>innerWidth+1 && getComputedStyle(e).position!=='fixed').slice(0,8).map(e=>e.className)};
                        }""")
                        results.append(dict(engine=engine,screen=screen,viewport=width,collapsed=collapsed,**measurement))
                        if width in (375,1440,1920) and not collapsed:
                            page.screenshot(path=str(evidence/f'{phase}-{engine}-{screen}-{width}.png'))
                if screen=='ucc':
                    page.set_viewport_size({'width':1920,'height':1080})
                    page.locator('[data-ucc-tv-enter]').click()
                    expect(page.locator('body')).to_have_class(re.compile('neosubzero-ucc-tv-mode'))
                    page.screenshot(path=str(evidence/f'{phase}-{engine}-tv.png'))
                    results.append(dict(engine=engine,screen='tv',viewport=1920,collapsed=False,**page.evaluate("""() => {const r=document.querySelector('.neosubzero-shell').getBoundingClientRect();return {left:r.left,right:innerWidth-r.right,overflow:document.documentElement.scrollWidth-innerWidth,shell:{width:r.width}}}""")))
                    page.locator('[data-ucc-tv-exit]').click()
            self.assertFalse(errors,errors)
            context.close();browser.close()
        (evidence/f'{phase}-measurements.json').write_text(json.dumps(results,indent=2))
        if phase=='after':
            for row in results:
                self.assertLessEqual(row['overflow'],1,row)
                self.assertLessEqual(row['left'],25,row)
                self.assertLessEqual(row['right'],25,row)

    def test_remote_and_motherbrain_use_canonical_live_parking(self):
        with self.app.app_context():
            fixture=fixture_module.NeoSubZeroUccTest()
            fixture.operation=db.session.get(SortDateOperation,self.operation_id)
            mission=fixture._departure('UPS7771','N-REMOTE-LIVE','SDF',4)
            mission.planned_datetime_utc=datetime(2026,10,23,8)
            mission.planned_datetime_local=datetime(2026,10,23,3)
            fixture._deice_event(mission,'deice_planned');mid=mission.id
            state=SortDateTailState(sort_date=fixture.operation.sort_date,gateway_code='RFD',sort_name='night',tail_number=mission.assigned_tail_number)
            motherbrain=SortDateTailState(sort_date=fixture.operation.sort_date,gateway_code='RFD',sort_name='night',tail_number='N-MOTHERBRAIN',parking_position='MotherBrain')
            db.session.add_all([state,motherbrain]);db.session.commit();sid=state.id
        evidence=Path('instance/browser-evidence/subzero-workspace')
        for engine in ('chromium','webkit'):
            browser=getattr(self.pw,engine).launch()
            page=browser.new_page(viewport={'width':1440,'height':1000})
            self.login(page);self.ready(page,'/neosubzero/ucc')
            remote=page.locator('[data-ucc-ramp="Remote"]')
            expect(remote).to_be_visible()
            expect(remote.locator('[data-ucc-aircraft]')).to_have_count(0)
            expect(page.locator('[data-ucc-location-exceptions]')).to_contain_text('N-MOTHERBRAIN')
            expect(page.locator('[data-ucc-ramp="MotherBrain"]')).to_have_count(0)
            with self.app.app_context():
                db.session.get(SortDateTailState,sid).parking_position='REMOTE';db.session.commit()
            expect(remote).to_contain_text('N-REMOTE-LIVE',timeout=10000)
            expect(remote).to_contain_text('UPS7771')
            self.assertEqual(page.locator('[data-ucc-ramp]').evaluate_all('(els)=>els.slice(0,5).map(el=>el.dataset.uccRamp)'),['Alpha','Bravo','Charlie','Delta','Echo'])
            remote.locator(f'[data-ucc-aircraft="{mid}"]').click()
            expect(page.locator('[data-ucc-info]')).to_contain_text('N-REMOTE-LIVE')
            remote.screenshot(path=str(evidence/f'{engine}-remote-live.png'))
            with self.app.app_context():
                db.session.get(SortDateTailState,sid).parking_position=None;db.session.commit()
            expect(remote.locator('[data-ucc-aircraft]')).to_have_count(0,timeout=10000)
            expect(remote).to_be_visible()
            expect(page.locator('[data-ucc-location-exceptions]')).to_contain_text('N-MOTHERBRAIN')
            browser.close()
