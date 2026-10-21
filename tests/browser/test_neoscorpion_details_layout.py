"""Measured baseline/current DETAILS on real rendered operational states."""
import json
import os
import re
from pathlib import Path
import subprocess
import threading
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from jinja2 import ChoiceLoader, DictLoader
from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server
from app.extensions import db
from app.models import NeoScorpionFuelAssignment, NeoScorpionFuelWorkState, NeoScorpionTailNote, NeoScorpionFuelingEvent
from app.services import neoscorpion as fuel
from tests import test_neoscorpion_dispatch_details as cases

BASELINE = 'e40d7f413d184bdd584ddee9adfff8f3d6340c3b'
EVIDENCE = Path('instance/browser-evidence/details-layout')
PARTIALS = ('_dispatch_details.html','_dispatch_operations.html','_dispatch_secondary.html',
            '_dispatch_action_center.html','_dispatch_work_timeline.html','_dispatch_visit.html')


class DetailsLayoutBrowserTest(unittest.TestCase):
    def exercise(self, engine):
        case=cases.DetailsTest();case.setUp()
        app=case.client.application
        app.config.update(LIVE_SCREEN_REFRESH_INTERVAL_MS=0,GOOGLE_MOTHERBRAIN_IMPORT_ENABLED=False,
                          GOOGLE_MOTHERBRAIN_READER_ENABLED=False,FLIGHT_API_ENABLED=False)
        loader=app.jinja_env.loader
        originals={'neonodes/neoscorpion/'+name:subprocess.check_output(['git','show',
            BASELINE+':app/templates/neonodes/neoscorpion/'+name]).decode() for name in PARTIALS}
        css=subprocess.check_output(['git','show',BASELINE+':app/static/css/26-neoscorpion.css']).decode()
        server=make_server('127.0.0.1',0,app,threaded=True)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        origin=f'http://127.0.0.1:{server.server_port}'
        EVIDENCE.mkdir(parents=True,exist_ok=True)
        metrics=[];errors=[]
        try:
            with sync_playwright() as pw:
                browser=getattr(pw,engine).launch()
                try:
                    context=browser.new_context(viewport={'width':1440,'height':1000},service_workers='block')
                    page=context.new_page()
                    page.on('pageerror',lambda e:errors.append(str(e)))
                    page.goto(origin+'/login')
                    page.locator('[name=email]').fill(case.dispatcher.email)
                    page.locator('[name=password]').fill('TestPassword123!')
                    page.locator('button[type=submit]').click()
                    page.wait_for_url(lambda u:'/login' not in u)
                    page.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1','seen')")
                    def measure(state):
                        phases=('before',) if os.getenv('NEO_DETAILS_BASELINE_ONLY') else ('before','after')
                        for phase in phases:
                            app.jinja_env.loader=ChoiceLoader([DictLoader(originals),loader]) if phase=='before' else loader
                            app.jinja_env.cache.clear()
                            if phase=='before':page.route('**/css/26-neoscorpion.css*',lambda r:r.fulfill(body=css,content_type='text/css'))
                            else:page.unroute('**/css/26-neoscorpion.css*')
                            page.set_viewport_size({'width':1440,'height':1000})
                            page.goto(origin+'/neoscorpion/fuel-dispatch')
                            page.evaluate('document.fonts.ready')
                            row=page.locator(f'[data-dispatch-mission-id="{case.mission.id}"]')
                            toggle=row.locator('[data-neoscorpion-dispatch-details]')
                            if toggle.get_attribute('aria-expanded')!='true':toggle.click()
                            detail=page.locator('#'+row.locator('[data-neoscorpion-dispatch-details]').get_attribute('aria-controls'))
                            console=detail.locator('.scorpion-desktop-console')
                            expect(console).to_be_visible()
                            for width in (1920,1440,1280,1024):
                                page.set_viewport_size({'width':width,'height':1000})
                                for board in (False,True):
                                    page.locator('body').evaluate('(e,on)=>e.classList.toggle("operational-board-view",on)',board)
                                    geometry=console.evaluate("e=>{const b=e.getBoundingClientRect();return {height:b.height,width:b.width,display:getComputedStyle(e).display,columns:getComputedStyle(e).gridTemplateColumns,overflow:e.scrollWidth>e.clientWidth+1,parts:[...e.querySelectorAll(':scope >*,.scorpion-console-workspace >*,.scorpion-console-facts-column >*')].map(n=>({name:n.className||n.dataset.detailsRegion,height:n.getBoundingClientRect().height,y:n.getBoundingClientRect().top-b.top,rows:getComputedStyle(n).gridTemplateRows}))};}")
                                    metrics.append(dict(engine=engine,state=state,phase=phase,viewport=width,board=board,**geometry))
                                    self.assertFalse(geometry['overflow'],metrics[-1])
                                    self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'),width+1)
                                    if width in (1440,1024):
                                        console.screenshot(path=str(EVIDENCE/f'{engine}-{state}-{phase}-{width}-{"board" if board else "dispatch"}.png'))
                            page.locator('body').evaluate('e=>e.classList.remove("operational-board-view")')
                            if phase=='after' and state=='active':
                                swap=console.locator('[data-console-section="management-2"]')
                                swap.locator('summary').click()
                                expect(swap.locator('[name=replacement_fueler_user_id]')).to_be_visible()
                                expect(swap.locator('[name=replacement_truck_id]')).to_be_visible()
                                console.screenshot(path=str(EVIDENCE/f'{engine}-resource-swap-controls.png'))
                    case.mission.planned_fuel_load=40000;db.session.commit();measure('no-actions')
                    case.mission.planned_fuel_load=25000;db.session.commit();measure('one-review')
                    case.mission.planned_fuel_load=40000;db.session.commit();case.prepare();measure('active')
                    plan=case.plan();case.save(case.start+timedelta(minutes=1),**case.final_data());case.off();case.submit()
                    measure('multiple-visits')
                    child=db.session.get(NeoScorpionFuelWorkState,case.assignment.active_work_state_id)
                    child.notes='Truck B: verify panel access with ramp team. Truck A handoff readings retained.'
                    case.work.notes='Truck A: final measured tanks verified before handoff.'
                    db.session.add(NeoScorpionTailNote(gateway_id=case.gateway.id,tail_number=case.mission.assigned_tail_number,
                        notes='Aircraft note: coordinate parking access and confirm crew fuel request.',version=1,
                        updated_by_user_id=case.dispatcher.id,updated_at_utc=datetime.utcnow()))
                    db.session.commit();measure('visits-notes')
                    if not os.getenv('NEO_DETAILS_BASELINE_ONLY'):
                        before={(m['state'],m['viewport'],m['board']):m for m in metrics if m['phase']=='before'}
                        for m in metrics:
                            if m['phase']!='after':continue
                            old=before[(m['state'],m['viewport'],m['board'])]
                            m['reduction_percent']=round(100*(old['height']-m['height'])/old['height'],1)
                            self.assertEqual(m['display'],'grid')
                            # Measure against the actual pre-change production
                            # layout, not the much older stacked console.
                            if m['viewport'] >= 1440:
                                self.assertLessEqual(m['height'],old['height']+45,m)
                        # Real board toggle, expanded controls, two authenticated
                        # operator contexts and a dirty/focused Tail Notes draft.
                        app.config['LIVE_SCREEN_REFRESH_INTERVAL_MS']=1000
                        # A production roster has work below the edited mission.
                        # Avoid testing an impossible absolute scroll position
                        # beyond the end of a one-row document after reflow.
                        for index in range(4):
                            extra=case.fixture._mission(f'UPS99{index}','N492UP',40000,index+2)
                            extra.planned_datetime_utc=case.mission.planned_datetime_utc+timedelta(hours=index+1)
                        db.session.commit()
                        page.set_viewport_size({'width':1440,'height':1000})
                        page.goto(origin+'/neoscorpion/fuel-dispatch')
                        row=page.locator(f'[data-dispatch-mission-id="{case.mission.id}"]')
                        toggle=row.locator('[data-neoscorpion-dispatch-details]')
                        if toggle.get_attribute('aria-expanded')!='true':toggle.click()
                        detail=page.locator('#'+toggle.get_attribute('aria-controls'))
                        console=detail.locator('.scorpion-desktop-console')
                        board=page.locator('[data-operational-board-toggle]:visible')
                        board.click();expect(page.locator('body')).to_have_class(re.compile('operational-board-view'))
                        expect(console).to_be_visible()
                        board.click();expect(page.locator('body')).not_to_have_class(re.compile('operational-board-view'))
                        regions=console.locator(':scope > [data-console-region]')
                        self.assertEqual(regions.count(),4)
                        ys=regions.evaluate_all('es=>es.filter(e=>!e.hidden).map(e=>e.getBoundingClientRect().top)')
                        self.assertLessEqual(max(ys)-min(ys),2)
                        for key in ('tail-notes','second-truck','start-uplift','start-defuel','spear','management-4',f'source-{case.work.id}'):
                            disclosure=console.locator(f'[data-console-section="{key}"]')
                            if not disclosure.is_visible():continue
                            if not disclosure.evaluate('e=>e.open'):disclosure.locator('summary').click()
                        for width in (1440,1024):
                            page.set_viewport_size({'width':width,'height':1000})
                            clipped=console.evaluate("""e=>{const b=e.getBoundingClientRect();return [...e.querySelectorAll('input:not([type=hidden]),select,textarea,button')].filter(n=>{const r=n.getBoundingClientRect();return r.width&&r.height&&(r.left<b.left-1||r.right>b.right+1);}).map(n=>n.outerHTML.slice(0,180));}""")
                            self.assertEqual(clipped,[])
                            console.screenshot(path=str(EVIDENCE/f'{engine}-expanded-forms-{width}.png'))
                        page.set_viewport_size({'width':1440,'height':1000})
                        note=console.locator('[data-tail-note-form] textarea')
                        note.fill('Unsaved dispatcher note — preserve focus and exact draft through physical OFF.')
                        note.focus();note.evaluate('e=>e.setSelectionRange(8,18)')
                        page.evaluate('()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))')
                        scroll_before=page.evaluate('scrollY')
                        mobile=browser.new_context(viewport={'width':390,'height':844},service_workers='block')
                        fueler=mobile.new_page();fueler.on('pageerror',lambda e:errors.append(str(e)))
                        fueler.goto(origin+'/login');fueler.locator('[name=email]').fill(case.fueler.email)
                        fueler.locator('[name=password]').fill('TestPassword123!');fueler.locator('button[type=submit]').click()
                        fueler.wait_for_url(lambda u:'/login' not in u);fueler.goto(origin+'/neoscorpion/fueler')
                        card=fueler.locator(f'[data-fuel-data-card][data-physical-work-id="{child.id}"]')
                        card.locator('[data-truck-on-form] button').click()
                        expect(card.locator('.neoscorpion-truck-on-row')).to_contain_text('CONFIRMED')
                        card.locator('.neoscorpion-fueler-off-form button').click()
                        expect(console.locator(f'[data-console-visit="{child.id}"] [data-milestone="off"] time')).not_to_have_text('—',timeout=10000)
                        expect(note).to_have_value('Unsaved dispatcher note — preserve focus and exact draft through physical OFF.')
                        expect(note).to_be_focused();self.assertEqual(note.evaluate('e=>[e.selectionStart,e.selectionEnd]'),[8,18])
                        self.assertLessEqual(abs(page.evaluate('scrollY')-scroll_before),25,
                            dict(before=scroll_before,after=page.evaluate('scrollY'),note=note.bounding_box()))
                        for key in ('tail-notes','second-truck','spear'):
                            disclosure=console.locator(f'[data-console-section="{key}"]')
                            if disclosure.is_visible():self.assertTrue(disclosure.evaluate('e=>e.open'))
                        expect(console.locator(f'[data-action-key="verify-{case.work.id}"]')).to_be_visible()
                        console.locator('[data-tail-note-form]').get_by_role('button',name='SAVE',exact=True).click()
                        expect(console.locator('[data-tail-note-status]')).to_contain_text('SAVED',timeout=10000)
                        console.locator(f'[data-action-key="verify-{case.work.id}"] button').click()
                        expect(console.locator(f'[data-action-key="verify-{case.work.id}"]')).to_have_count(0,timeout=10000)
                        history=console.locator('[data-console-section="history"]')
                        expect(history).to_have_count(1,timeout=10000)
                        self.assertFalse(history.evaluate('e=>e.open'))
                        db.session.expire_all()
                        self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)
                        self.assertIsNone(db.session.get(NeoScorpionFuelAssignment,case.assignment.id).completed_at_utc)
                        self.assertEqual(case.selection.current_gallons,7000)
                        for width in (390,375):
                            page.set_viewport_size({'width':width,'height':844})
                            expect(detail.locator('.scorpion-mobile-readonly')).to_be_visible()
                            expect(console).to_be_hidden()
                            self.assertEqual(row.locator('td:visible').count(),5)
                            self.assertEqual(detail.locator('button[type=submit]:visible,input:visible,select:visible,textarea:visible').count(),0)
                            self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'),width+1)
                            detail.screenshot(path=str(EVIDENCE/f'{engine}-mobile-readonly-{width}.png'))
                        # Finish the second real visit, then render the completed
                        # mission with its independently verified earlier truck.
                        fuel.save_fueler_entry(case.gateway,case.fueler,dict(assignment_id=case.assignment.id,
                            apu_running='no',actual_left='21',actual_ctr='0',actual_right='21',transfer_fuel_gallons='2000'))
                        db.session.commit();case.submit()
                        fuel.complete_fueled_assignment(case.gateway,case.dispatcher,case.assignment.id);db.session.commit()
                        measure('completed')
                    self.assertEqual(errors,[])
                finally:browser.close()
        finally:
            (EVIDENCE/f'measurements-{engine}.json').write_text(json.dumps(metrics,indent=2))
            app.jinja_env.loader=loader;app.jinja_env.cache.clear()
            server.shutdown();thread.join(timeout=5);case.doCleanups()
        reductions=[m['reduction_percent'] for m in metrics if 'reduction_percent' in m]
        print(f'{engine}: {len(reductions)} before/after comparisons; minimum reduction {min(reductions) if reductions else "baseline only"}%')

    def test_chromium(self):self.exercise('chromium')
    def test_webkit(self):self.exercise('webkit')
