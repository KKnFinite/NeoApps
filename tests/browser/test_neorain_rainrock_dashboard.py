"""Actual autosave, shared live values, image clipboard and complete PNG exports."""
import json
import re
from datetime import timedelta
from pathlib import Path
from threading import Thread
from unittest import TestCase
from unittest.mock import patch

from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server
from PIL import Image
from app.extensions import db
from app.models import SortDateMission, NeoRainDelayInfo
from tests import test_neorain_rainrock_dashboard as unit

EVIDENCE=Path('instance/browser-evidence/rainrock-dashboard')


class RainrockDashboardBrowserTest(TestCase):
    def test_chromium(self):self.exercise('chromium')
    def test_webkit(self):self.exercise('webkit')

    def exercise(self,engine):
        case=unit.RainrockDashboardTest();case.setUp();self.addCleanup(case.doCleanups)
        app=case.app;app.config['RAINROCK_REFRESH_INTERVAL_MS']=1000
        op=case.operation;actor=case.f.actor
        for side in ('arrival','departure'):
            for i in range(1,7):
                m=SortDateMission(sort_date_operation_id=op.id,sort_date=op.sort_date,gateway_code='RFD',sort_name='night',
                    mission_type=side,mission_source='manual',wave='1',flight_number=('UPS2' if side=='arrival' else 'UPS3')+str(100+i),
                    assigned_tail_number='N'+str(700+i)+'UP',origin='PHX' if side=='arrival' else 'RFD',
                    destination='RFD' if side=='arrival' else 'MSP',timezone='America/Chicago',
                    planned_datetime_utc=case.arrival.planned_datetime_utc+timedelta(minutes=20*i))
                if i!=6:
                    setattr(m,'actual_block_in_datetime_utc' if side=='arrival' else 'actual_block_out_datetime_utc',
                        m.planned_datetime_utc+timedelta(minutes=(-2 if i%2 else 12)))
                db.session.add(m);db.session.flush()
                if i%2==0:db.session.add(NeoRainDelayInfo(sort_date_mission_id=m.id,minutes=i,code='WX',notes='Weather / full remark '+str(i)))
        db.session.commit();operation_id=op.id
        server=make_server('127.0.0.1',0,app,threaded=True);worker=Thread(target=server.serve_forever,daemon=True);worker.start()
        origin=f'http://127.0.0.1:{server.server_port}';url=origin+'/neorain/rainrock?operation_id='+str(operation_id)
        EVIDENCE.mkdir(parents=True,exist_ok=True);metrics=[];errors=[]
        google=patch('app.services.neorain_reports.snapshot_current_google',return_value='Google fixture snapshot');google.start()
        def login(page):
            page.goto(origin+'/login');page.locator('[name=email]').fill(actor.email)
            page.locator('[name=password]').fill('TestPassword123!');page.locator('button[type=submit]').click()
            page.wait_for_url(lambda u:'/login' not in u)
        def screenshot(page,label):
            page.evaluate('document.fonts.ready');page.screenshot(path=str(EVIDENCE/f'{engine}-{label}.png'),full_page=True)
        def assert_layout(page,width):
            self.assertFalse(page.evaluate('document.documentElement.scrollWidth>innerWidth+1'))
            for table in page.locator('.rainrock-flight-table').all():
                cells=table.locator('[data-rainrock-flight]').first.locator('td').evaluate_all('els=>els.map(e=>({top:e.getBoundingClientRect().top,display:getComputedStyle(e).display}))')
                self.assertEqual(len(cells),6);self.assertLess(max(c['top'] for c in cells)-min(c['top'] for c in cells),1)
                self.assertTrue(all(c['display']=='table-cell' for c in cells))
                if width<700:self.assertTrue(table.locator('..').evaluate('e=>e.scrollWidth>e.clientWidth'))
            self.assertEqual(page.locator('[data-rainrock-field]').count(),10)
            self.assertEqual(page.locator('[data-rainrock-report] input,[data-rainrock-report] button').count(),0)
            self.assertEqual(page.locator('[data-rainrock-flight]').count(),14)
            metrics.append(dict(width=width,summaryTops=page.locator('.rainrock-summary').evaluate_all('es=>es.map(e=>e.getBoundingClientRect().top)'),overflow=False))
        try:
            with sync_playwright() as pw:
                browser=getattr(pw,engine).launch()
                try:
                    args=dict(viewport={'width':1440,'height':900},service_workers='block',accept_downloads=True)
                    if engine=='chromium':args['permissions']=['clipboard-read','clipboard-write']
                    context=browser.new_context(**args);page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)));login(page);page.goto(url)
                    values=dict(planned_volume='119805',actual_volume='117540',smalls_percent='89.4',planned_fph='75000',actual_fph='72850',
                        briefing_ramp_staffing_planned='316',briefing_ramp_staffing_actual='320',briefing_hub_staffing_planned='500',
                        briefing_hub_staffing_actual='510',note_1='RFD: All work safely finished.\nComplete notes are visible.')
                    for key,value in values.items():page.locator(f'[data-rainrock-field][name="{key}"]').fill(value)
                    page.locator('h1').click()
                    expect(page.locator('[data-rainrock-value="actual_fph"]')).to_have_text('72,850')
                    expect(page.locator('[data-rainrock-save-status]')).to_have_text('Auto-saves')
                    # Cross-session canonical values, not a duplicated Rainrock record.
                    other=browser.new_context(service_workers='block',**({'permissions':['clipboard-read','clipboard-write']} if engine=='chromium' else {}));brief=other.new_page();login(brief)
                    brief.goto(origin+'/neorain/daily-briefing?operation_id='+str(operation_id))
                    expect(brief.locator('#briefing-text')).to_contain_text('Ramp Staffing: 320/316')
                    expect(brief.locator('#briefing-text')).to_contain_text('Volume Actual: 117,540')
                    if engine=='chromium':
                        brief.locator('#copy-briefing').click()
                        expect(brief.locator('#copy-briefing-status')).to_have_text('Copied.')
                        self.assertEqual(brief.evaluate('navigator.clipboard.readText()').replace('\r\n','\n'),brief.locator('#briefing-text').inner_text())
                    brief.locator('[data-briefing-field][name="actual_volume"]').fill('118000');brief.locator('h1').click()
                    expect(page.locator('[data-rainrock-value="actual_volume"]')).to_have_text('118,000',timeout=12000)
                    # Real concurrent edit: preserve the local draft/version,
                    # show the conflict beside it, and recover only that field.
                    note=page.locator('[data-rainrock-field][name="note_1"]');note.fill('Keep my unsaved local note');note.focus()
                    remote=other.request.get(origin+'/neorain/rainrock/state?operation_id='+str(operation_id)).json()
                    version=remote['fields']['note_1']['version']
                    saved=other.request.post(origin+'/neorain/rainrock/inputs?operation_id='+str(operation_id),
                        data={'values':{'note_1':'Other operator’s saved note'},'versions':{'note_1':version}})
                    self.assertEqual(saved.status,200)
                    expect(page.locator('[data-field-status="note_1"]')).to_contain_text('changed',timeout=8000)
                    expect(note).to_have_value('Keep my unsaved local note');expect(note).to_be_focused()
                    expect(page.locator('[data-rainrock-report]')).to_contain_text('Other operator’s saved note',timeout=8000)
                    page.locator('[data-rainrock-use-saved="note_1"]').click()
                    expect(note).to_have_value('Other operator’s saved note')
                    expect(page.locator('[data-field-status="note_1"]')).to_have_text('')
                    note.fill(values['note_1']);page.locator('h1').click()
                    expect(page.locator('[data-rainrock-save-status]')).to_have_text('Auto-saves')
                    for width,height in ((1920,1080),(1440,900),(390,844),(375,568)):
                        page.set_viewport_size({'width':width,'height':height});assert_layout(page,width);screenshot(page,str(width))
                    with page.expect_download(timeout=20000) as d:page.locator('[data-rainrock-download]').click()
                    mobile_path=EVIDENCE/f'{engine}-mobile-export.png';d.value.save_as(mobile_path)
                    with Image.open(mobile_path) as img:
                        self.assertEqual(img.width,2400);mobile_size=img.size
                    page.set_viewport_size({'width':1440,'height':900})
                    # Native image clipboard: no fake clipboard implementation.
                    page.locator('[data-rainrock-field][name="actual_fph"]').fill('73000')
                    if engine=='chromium':
                        page.locator('[data-rainrock-copy]').click()
                        expect(page.locator('[data-rainrock-copy-status]')).to_have_text('Image copied — ready to paste into your email.',timeout=20000)
                        info=page.evaluate('async()=>{const items=await navigator.clipboard.read();const blob=await items[0].getType("image/png");const image=await createImageBitmap(blob);return {types:items[0].types,width:image.width,height:image.height,size:blob.size}}')
                        self.assertIn('image/png',info['types']);self.assertEqual(info['width'],2400);metrics.append(dict(clipboard=info))
                    else:
                        downloads=[];page.on('download',lambda d:downloads.append(d))
                        page.locator('[data-rainrock-copy]').click()
                        expect(page.locator('[data-rainrock-copy-status]')).to_contain_text(re.compile('Image copied|Complete PNG downloaded'),timeout=20000)
                        metrics.append(dict(nativeClipboardOutcome=page.locator('[data-rainrock-copy-status]').inner_text()))
                    expect(page.locator('[data-rainrock-value="actual_fph"]')).to_have_text('73,000')
                    with page.expect_download(timeout=20000) as d:page.locator('[data-rainrock-download]').click()
                    path=EVIDENCE/f'{engine}-report-export.png';d.value.save_as(path)
                    with Image.open(path) as img:
                        self.assertEqual(img.width,2400);self.assertGreater(img.height,700)
                        self.assertEqual(img.size,mobile_size)
                    # Unsupported clipboard must download a real complete PNG.
                    page.evaluate('Object.defineProperty(navigator,"clipboard",{configurable:true,value:undefined})')
                    with page.expect_download(timeout=20000) as d:page.locator('[data-rainrock-copy]').click()
                    d.value.save_as(EVIDENCE/f'{engine}-fallback.png')
                    expect(page.locator('[data-rainrock-copy-status]')).to_contain_text('Complete PNG downloaded.')
                    # An isolated long report, with a real final delay at the end.
                    with app.app_context():
                        for i in range(100):
                            m=SortDateMission(sort_date_operation_id=operation_id,sort_date=op.sort_date,gateway_code='RFD',sort_name='night',
                                mission_type='departure',mission_source='manual',wave='1',flight_number='UPS'+str(5000+i),
                                origin='RFD',destination='MSP',assigned_tail_number='N'+str(500+i)+'UP',timezone='America/Chicago',
                                planned_datetime_utc=case.departure.planned_datetime_utc+timedelta(minutes=i))
                            db.session.add(m);db.session.flush()
                            db.session.add(NeoRainDelayInfo(sort_date_mission_id=m.id,minutes=2,code='WX',
                                notes=('FINAL REPORT MARKER' if i==99 else 'Full operational detail')+' — '+('Long retained remark. '*10)))
                        db.session.commit()
                    page.goto(url);expect(page.locator('[data-rainrock-flight]')).to_have_count(114)
                    with page.expect_download(timeout=45000) as d:page.locator('[data-rainrock-download]').click()
                    long_path=EVIDENCE/f'{engine}-long-report.png';d.value.save_as(long_path)
                    with Image.open(long_path) as img:
                        self.assertEqual(img.width,2400);self.assertGreater(img.height,9000)
                        self.assertGreater(img.crop((0,img.height-150,img.width,img.height)).convert('L').getextrema()[1],150)
                        metrics.append(dict(longImage=dict(width=img.width,height=img.height)))
                    expect(page.locator('.rainrock-delay').filter(has_text='FINAL REPORT MARKER')).to_have_count(1)
                    self.assertEqual(errors,[])
                    (EVIDENCE/f'{engine}-measurements.json').write_text(json.dumps(metrics,indent=2))
                    other.close();context.close()
                finally:browser.close()
        finally:google.stop();server.shutdown();server.server_close();worker.join(timeout=5)
