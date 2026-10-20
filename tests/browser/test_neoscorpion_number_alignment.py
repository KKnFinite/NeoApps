"""Painted numeric alignment across real Dispatch rows, not an empty HTML mock."""
import json
import os
import subprocess
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from jinja2 import ChoiceLoader, DictLoader
from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server
from app.extensions import db
from app.models import (NeoScorpionFuelWorkState, NeoScorpionFuelTankState,
                        SortDateMission, SortDateParkingAssignment)
from app.services import neoscorpion as fuel
from tests import test_neoscorpion_dispatch_details as cases

BASELINE = 'df424b2428e03b4d1fd0522649d118cff900782d'
EVIDENCE = Path('instance/browser-evidence/number-alignment')

MEASURE = """rows => rows.map(row => {
    const b=row.getBoundingClientRect();
    const selectors=['.neoscorpion-dispatch-inbound input,.neoscorpion-dispatch-inbound strong',
        '.neoscorpion-dispatch-required input,.neoscorpion-dispatch-required span.neoscorpion-dispatch-size-large',
        '[data-dispatch-neo-fuel]','[data-dispatch-actual]',
        '.neoscorpion-dispatch-transfer-estimate strong'];
    const values=selectors.map(selector=>{
        const e=row.querySelector(selector), r=(e.closest('.neoscorpion-dispatch-number')||e).getBoundingClientRect(),s=getComputedStyle(e);
        const probe=document.createElement('i');Object.assign(probe.style,{display:'inline-block',width:'0px',height:'0px',padding:'0px',margin:'0px',verticalAlign:'baseline'});
        let host=e;
        if(e.tagName==='INPUT') {
            host=document.createElement('span');const box=e.getBoundingClientRect();
            Object.assign(host.style,{position:'fixed',visibility:'hidden',display:'block',boxSizing:'border-box',top:box.top+'px',left:box.left+'px',
                width:box.width+'px',height:box.height+'px',font:s.font,lineHeight:s.lineHeight,whiteSpace:'pre',
                paddingTop:s.paddingTop,paddingBottom:s.paddingBottom,borderTop:s.borderTop,borderBottom:s.borderBottom});
            host.textContent=e.value||'0';document.body.append(host);
        }
        host.append(probe);const baseline=probe.getBoundingClientRect().top-b.top;probe.remove();
        if(host!==e)host.remove();
        let text=null;
        if(e.tagName!=='INPUT') {const range=document.createRange();range.selectNodeContents(e);
            const t=range.getBoundingClientRect();text={top:t.top-b.top,bottom:t.bottom-b.top};}
        return {top:r.top-b.top,height:r.height,center:r.top-b.top+r.height/2,baseline,
            font:s.fontSize,lineHeight:s.lineHeight,text,value:e.value??e.textContent.trim()};
    });
    const overflows=[...row.querySelectorAll('input,[data-autosave-status],.neoscorpion-dispatch-secondary,.neoscorpion-dispatch-center-fuel,[data-dispatch-apu-effective]')]
        .filter(e=>e.getClientRects().length&&!e.closest('details:not([open])')).filter(e=>{const r=e.getBoundingClientRect(),c=e.closest('td').getBoundingClientRect();
            return r.top<c.top-1||r.bottom>c.bottom+1||r.left<c.left-1||r.right>c.right+1;}).map(e=>e.outerHTML);
    for(const e of row.querySelectorAll('.neoscorpion-dispatch-number-stack strong,.neoscorpion-dispatch-number-stack small')) {
        if(!e.getClientRects().length||e.closest('details:not([open])'))continue;
        const c=e.closest('td').getBoundingClientRect(),range=document.createRange();range.selectNodeContents(e);
        for(const r of range.getClientRects()) if(r.left<c.left-1||r.right>c.right+1||r.top<c.top-1||r.bottom>c.bottom+1)
            overflows.push(e.outerHTML);
    }
    return {mission:row.dataset.dispatchMissionId,height:b.height,values,overflows};
})"""


class NumberAlignmentBrowserTest(unittest.TestCase):
    def test_chromium_numbers(self): self.exercise('chromium')
    def test_webkit_numbers(self): self.exercise('webkit')

    def exercise(self, engine):
        case=cases.DetailsTest();case.setUp();app=case.client.application
        app.config.update(LIVE_SCREEN_REFRESH_INTERVAL_MS=0,GOOGLE_MOTHERBRAIN_IMPORT_ENABLED=False,
                          GOOGLE_MOTHERBRAIN_READER_ENABLED=False,FLIGHT_API_ENABLED=False)
        # One genuinely completed row, alongside editable blank/populated rows,
        # measured FOB, an A300 center reading and an editable follow-up cycle.
        case.prepare();case.save(case.start+timedelta(minutes=1),**case.final_data());case.off();case.submit()
        fuel.complete_fueled_assignment(case.gateway,case.dispatcher,case.assignment.id);db.session.commit()
        ids=[case.mission.id]
        for index in range(4):
            tail=('N492UP','N164UP','N165UP','N495UP')[index]
            mission=case.fixture._mission(f'UPS91{index}',tail,None if index==0 else 40000,index+2)
            mission.planned_datetime_utc=datetime.utcnow()+timedelta(hours=3,minutes=index)
            case.fixture._tail_fuel(mission,inbound_lbs=None if index==0 else 12000)
            assignment=case.fixture._assignment(mission)
            db.session.add(SortDateParkingAssignment(sort_date_operation_id=case.operation.id,
                tail_number=tail,ramp_code='C',position_code=f'{index+1:02}',lane_number=1))
            if index in (1,2):
                db.session.add(SortDateMission(sort_date_operation_id=case.operation.id,sort_date=case.operation.sort_date,
                    gateway_code=case.gateway.code,sort_name='night',mission_type='arrival',mission_source='manual',
                    flight_number=f'UPS81{index}',assigned_tail_number=tail,origin='SDF',destination=case.gateway.code,
                    arrival_status='arrived' if index==1 else 'on_ground',
                    actual_block_in_datetime_utc=datetime.utcnow()-timedelta(hours=1) if index==1 else None))
            if index>=2:
                assignment.current_cycle_number=2 if index==3 else 1
                work=NeoScorpionFuelWorkState(fuel_assignment_id=assignment.id,tail_number=tail,
                    visit_number=1,physical_cycle_number=assignment.current_cycle_number,
                    apu_running=True,apu_allowance_lbs=500,automatic_apu_allowance_lbs=500,
                    apu_source_tank_code='l_in' if index==2 else 'left')
                db.session.add(work);db.session.flush();assignment.active_work_state_id=work.id
                for code,_label in fuel.tank_layout_for_tail(tail):
                    measured=code not in ('ctr','tt')
                    db.session.add(NeoScorpionFuelTankState(fuel_work_state_id=work.id,tank_code=code,
                        remaining_lbs=(3000 if index==2 else 6000) if measured else 0,
                        actual_lbs=(7500 if index==2 else 15000) if measured else (400 if code=='ctr' else 0)))
            ids.append(mission.id)
        db.session.commit()
        css=subprocess.check_output(['git','show',BASELINE+':app/static/css/26-neoscorpion.css']).decode()
        loader=app.jinja_env.loader
        original={'neonodes/neoscorpion/_fuel_dispatch_panel.html':subprocess.check_output(['git','show',
            BASELINE+':app/templates/neonodes/neoscorpion/_fuel_dispatch_panel.html']).decode()}
        server=make_server('127.0.0.1',0,app,threaded=True)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        origin=f'http://127.0.0.1:{server.server_port}'
        EVIDENCE.mkdir(parents=True,exist_ok=True);metrics=[];errors=[]
        try:
            with sync_playwright() as pw:
                browser=getattr(pw,engine).launch()
                try:
                    context=browser.new_context(viewport={'width':1440,'height':1000},service_workers='block')
                    page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
                    page.goto(origin+'/login');page.locator('[name=email]').fill(case.dispatcher.email)
                    page.locator('[name=password]').fill('TestPassword123!');page.locator('button[type=submit]').click()
                    page.wait_for_url(lambda u:'/login' not in u)
                    page.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1','seen')")
                    for phase in (('before',) if os.getenv('NEO_NUMBERS_BASELINE_ONLY') else ('before','after')):
                        app.jinja_env.loader=ChoiceLoader([DictLoader(original),loader]) if phase=='before' else loader
                        app.jinja_env.cache.clear()
                        if phase=='before':page.route('**/css/26-neoscorpion.css*',lambda r:r.fulfill(body=css,content_type='text/css'))
                        else:page.unroute('**/css/26-neoscorpion.css*')
                        for percent in (75,100,150):
                            case.settings.mission_row_height_percent=percent;db.session.commit()
                            page.goto(origin+'/neoscorpion/fuel-dispatch');page.evaluate('document.fonts.ready')
                            for width in (1920,1440,1280,1024):
                                page.set_viewport_size({'width':width,'height':1000})
                                for board in (False,True):
                                    if page.locator('body').evaluate('e=>e.classList.contains("operational-board-view")')!=board:
                                        page.locator('[data-operational-board-toggle]:visible').click()
                                    for feedback in ('','Saving...','Saved'):
                                        page.locator('[data-autosave-status]').evaluate_all('(nodes,text)=>nodes.forEach((e,i)=>e.textContent=i%2?text:"")',feedback)
                                        rows=page.locator('.neoscorpion-dispatch-primary-row')
                                        result=rows.evaluate_all(MEASURE)
                                        metrics.append(dict(phase=phase,percent=percent,width=width,board=board,feedback=feedback,rows=result))
                                        if phase=='after':
                                            self.assertEqual(len(result),5)
                                            self.assertFalse(page.evaluate('document.documentElement.scrollWidth>innerWidth+1'))
                                            for row in result:
                                                self.assertFalse(row['overflows'],row)
                                                values=row['values']
                                                self.assertLessEqual(abs(values[0]['center']-values[1]['center']),1,row)
                                                self.assertLessEqual(abs(values[0]['baseline']-values[1]['baseline']),1,row)
                                                # All primary numeric line boxes share the same rail.
                                                self.assertLessEqual(max(v['center'] for v in values)-min(v['center'] for v in values),1,row)
                                                self.assertLessEqual(max(v['baseline'] for v in values)-min(v['baseline'] for v in values),1,row)
                                                if percent==100:self.assertAlmostEqual(row['height'],98,delta=1)
                                            for mid,color in ((ids[2],'rgb(143, 255, 168)'),(ids[3],'rgb(255, 240, 176)')):
                                                target=page.locator(f'[data-dispatch-mission-id="{mid}"]')
                                                self.assertEqual(target.locator('.neoscorpion-dispatch-flight > strong').evaluate('e=>getComputedStyle(e).color'),color)
                                                self.assertEqual(target.locator('.neoscorpion-dispatch-flight').evaluate('e=>getComputedStyle(e,"::after").animationName'),'neoscorpion-assignment-accent-pulse')
                                            expect(page.locator(f'[data-dispatch-mission-id="{ids[2]}"] .neoscorpion-dispatch-center-fuel')).to_have_text('CF -')
                                            expect(page.locator(f'[data-dispatch-mission-id="{ids[3]}"] .neoscorpion-dispatch-center-fuel')).to_have_text('CF 400')
                                            self.assertEqual(page.locator(f'[data-dispatch-mission-id="{ids[0]}"] .is-neo-fuel-excess').evaluate('e=>getComputedStyle(e).color'),'rgb(255, 98, 107)')
                                        if percent==100 and width in (1440,1024) and feedback=='Saved':
                                            page.locator('.neoscorpion-dispatch-table--compact').screenshot(path=str(EVIDENCE/f'{engine}-{phase}-{width}-{"board" if board else "normal"}.png'))
                    if not os.getenv('NEO_NUMBERS_BASELINE_ONLY'):
                        page.set_viewport_size({'width':1440,'height':1000})
                        page.goto(origin+'/neoscorpion/fuel-dispatch');page.evaluate('document.fonts.ready')
                        apu=page.locator(f'[data-dispatch-mission-id="{ids[3]}"] [data-dispatch-apu-editor]')
                        apu.locator('summary').click()
                        allowance=apu.locator('[name=apu_override_allowance]')
                        expect(allowance).to_have_attribute('data-original-value','')
                        allowance.fill('2.0')
                        expect(allowance).to_be_visible()
                        # The editor remains a usable anchored popover, not a
                        # clipped or row-height-affecting numeric slot.
                        self.assertEqual(apu.locator('[data-dispatch-assignment-submit]').count(),1)
                        apu.get_by_role('button',name='CANCEL',exact=True).click()
                        expect(apu).not_to_have_attribute('open','')
                        apu.locator('summary').click();expect(allowance).to_have_value('')
                        allowance.fill('0.6');apu.get_by_role('button',name='SAVE',exact=True).click()
                        expect(apu).not_to_have_attribute('open','',timeout=12000)
                        expect(page.locator(f'[data-dispatch-mission-id="{ids[3]}"] [data-dispatch-apu-effective]')).to_have_text('APU: 0.6')
                        # Real autosave plus live replacement, while another input stays dirty/focused.
                        app.config['LIVE_SCREEN_REFRESH_INTERVAL_MS']=1000
                        page.reload()
                        editable=page.locator(f'[data-dispatch-mission-id="{ids[2]}"]')
                        field=editable.locator('[name=inbound_fuel]');field.fill('13')
                        field.press('Tab')
                        expect(field).to_have_attribute('data-saved-value','13.0',timeout=12000)
                        draft=page.locator(f'[data-dispatch-mission-id="{ids[1]}"] [name=inbound_fuel]')
                        draft.fill('incomplete');draft.focus()
                        case.settings.mission_row_height_percent=115
                        from app.models import NeoScorpionSortAssetState
                        state=NeoScorpionSortAssetState.query.filter_by(sort_date_operation_id=case.operation.id).one()
                        state.revision+=1;db.session.commit()
                        expect(page.locator('[data-dispatch-preferences]')).to_have_attribute('data-mission-row-height-percent','115',timeout=15000)
                        expect(draft).to_have_value('incomplete');expect(draft).to_be_focused()
                        for row in page.locator('.neoscorpion-dispatch-primary-row').evaluate_all(MEASURE):
                            self.assertLessEqual(max(v['baseline'] for v in row['values'])-min(v['baseline'] for v in row['values']),1,row)
                        for width in (390,375):
                            page.set_viewport_size({'width':width,'height':844})
                            self.assertFalse(page.evaluate('document.documentElement.scrollWidth>innerWidth+1'))
                            self.assertEqual(page.locator('.neoscorpion-dispatch-primary-row').first.locator('td:visible').count(),5)
                            self.assertEqual(page.locator('.neoscorpion-dispatch-primary-row input:visible').count(),0)
                            page.screenshot(path=str(EVIDENCE/f'{engine}-mobile-{width}.png'))
                        self.assertEqual(errors,[])
                finally:browser.close()
        finally:
            app.jinja_env.loader=loader;app.jinja_env.cache.clear()
            (EVIDENCE/f'measurements-{engine}.json').write_text(json.dumps(metrics,indent=2))
            server.shutdown();thread.join(timeout=5);case.doCleanups()
