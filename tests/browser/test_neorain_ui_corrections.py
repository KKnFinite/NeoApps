"""Visual and computed-style checks for Rain reports and flight table corrections."""
from datetime import datetime
import json
import unittest
from playwright.sync_api import expect
from app.extensions import db
from app.models import SortDateMission, NeoRainDelayInfo
from tests.browser import test_neorain_compact_overrides as rain


class RainUICorrectionsBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rain.CompactRainBrowserTest.setUpClass()
        cls.f=rain.CompactRainBrowserTest
        with cls.f.fixture.app.app_context():
            operation=db.session.get(__import__('app.models',fromlist=['SortDateOperation']).SortDateOperation,cls.f.operation_id)
            for mid in cls.f.ids.values():
                current=db.session.get(SortDateMission,mid)
                current.planned_datetime_local=datetime(2026,10,16,1)
                current.wave='1'
            mission=SortDateMission(sort_date_operation_id=operation.id,sort_date=operation.sort_date,
                gateway_code=operation.gateway_code,sort_name=operation.sort_name,mission_type='departure',
                mission_source='manual',wave='1',flight_number='UPS07831',origin=operation.gateway_code,destination='LAX',
                timezone='America/Chicago',assigned_tail_number='N7831UP',departure_status='departed',
                planned_datetime_local=datetime(2026,10,16,1),planned_datetime_utc=datetime(2026,10,16,6),actual_block_out_datetime_utc=datetime(2026,10,16,6,11))
            db.session.add(mission);db.session.flush()
            db.session.add(NeoRainDelayInfo(sort_date_mission_id=mission.id,minutes=11,code='RS',
                notes='LATE INBOUND AIRCRAFT AND EXTENDED WEATHER RESTRICTIONS'))
            db.session.commit()

    @classmethod
    def tearDownClass(cls):cls.f.tearDownClass()

    def test_report_typography_tables_and_effective_etd(self):
        evidence=self.f.fixture.evidence
        metrics={}
        for engine,width in (('chromium',1920),('chromium',1450),('webkit',390)):
            browser=getattr(self.f.fixture.pw,engine).launch()
            context=browser.new_context(viewport={'width':width,'height':1000 if width>900 else 844},
                has_touch=width<900,is_mobile=width<900,service_workers='block')
            page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            try:
                self.f.fixture().login(page)
                for board in ('rainrock','daily-briefing','inbound','outbound'):
                    path='/neorain/'+board
                    if board in ('rainrock','daily-briefing'):path+='?operation_id='+str(self.f.operation_id)
                    self.f.fixture().ready(page,path)
                    page.evaluate("document.fonts.load('14px NeoFontPlain')")
                    if board=='outbound' and width<900:
                        page.locator(f'.neorain-mobile-mission[data-neorain-mission-id="{self.f.ids["departure"]}"] [data-neorain-mobile-row-open]').first.click()
                    root=page.locator('.rain-report' if board in ('rainrock','daily-briefing') else '[data-neorain-board-page]')
                    metrics[f'{engine}-{width}-{board}']=root.evaluate("""root=>({height:root.getBoundingClientRect().height,
                        texts:[...root.querySelectorAll('p,pre,h2,td,th,input,output,summary')].filter(el=>el.getClientRects().length)
                            .slice(0,40).map(el=>({tag:el.tagName,text:(el.value||el.textContent).slice(0,80),font:getComputedStyle(el).fontFamily,
                                size:getComputedStyle(el).fontSize,line:getComputedStyle(el).lineHeight,height:el.getBoundingClientRect().height}))})""")
                    page.screenshot(path=str(evidence/(f'rain-ui-{board}-{engine}-{width}-'+'after.png')),full_page=True)
                    self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
                    fonts=root.locator('p,pre,h2,td,th,input,output,summary').evaluate_all("els=>els.filter(el=>el.getClientRects().length).map(el=>getComputedStyle(el).fontFamily)")
                    self.assertTrue(fonts);self.assertTrue(all(font.startswith('NeoFontPlain') for font in fonts),fonts)
                    if board=='daily-briefing':
                        text=page.locator('#briefing-text').text_content()
                        page.evaluate("navigator.clipboard.writeText=async text=>{window.copiedBriefing=text;}")
                        page.locator('#copy-briefing').click();expect(page.locator('#copy-briefing-status')).to_have_text('Copied.')
                        self.assertEqual(page.evaluate('window.copiedBriefing'),text)
                        self.assertIn('RFD-LAX UPS7831 N7831UP +11 RS',text)
                        line=page.locator('#briefing-text').evaluate('el=>parseFloat(getComputedStyle(el).lineHeight)')
                        self.assertLessEqual(line,22)
                    if board=='rainrock':
                        expect(page.locator('[data-report-volume-unavailable]')).to_be_visible()
                        expect(page.locator('[data-report-staffing-unavailable]')).to_be_visible()
                    if board=='outbound':self.check_outbound(page,width)
                self.assertEqual(errors,[])
            finally:context.close();browser.close()
        (evidence/'rain-ui-after.json').write_text(json.dumps(metrics,indent=2),encoding='utf-8')

    def check_outbound(self,page,width):
        mid=self.f.ids['departure']
        row=page.locator(f'[data-neorain-outbound-row][data-neorain-mission-id="{mid}"]:visible').first
        cell=row.locator('[data-label="STD/ETD"]') if width>1440 else row.locator('dl > div').filter(has=page.locator('dt',has_text='STD/ETD')).locator('dd')
        field=cell.locator('[data-rain-override-time]')
        expect(field).to_have_count(1);self.assertEqual(cell.locator('small[aria-label="STD"]').count(),0)
        expect(field).to_have_value('01:00')
        if width>1440:
            sizes=row.locator('[data-label="Flight #"], [data-label="Tail"], [data-label="Destination"], [data-label="Status"], [data-label="Load Planner"]').evaluate_all('els=>els.map(el=>getComputedStyle(el).fontSize)')
            self.assertEqual(len(set(sizes)),1,sizes)
            cells=row.locator('td').evaluate_all('els=>els.map(el=>el.getBoundingClientRect().x)')
            heads=page.locator('.neorain-desktop-board thead th').evaluate_all('els=>els.map(el=>el.getBoundingClientRect().x)')
            self.assertTrue(all(abs(a-b)<1 for a,b in zip(cells,heads)))
            self.assertLess(row.bounding_box()['height'],48)
            for label in ('Status','Load Planner'):
                el=row.locator(f'[data-label="{label}"]')
                self.assertEqual(el.evaluate('el=>getComputedStyle(el).overflowWrap'),'normal')
                self.assertTrue(el.evaluate('el=>el.scrollWidth<=el.clientWidth+1'))
            overflow=page.locator('.neorain-desktop-board th').evaluate_all('els=>els.filter(el=>el.scrollWidth>el.clientWidth+1).map(el=>({text:el.textContent,width:el.clientWidth,scroll:el.scrollWidth,font:getComputedStyle(el).fontSize}))')
            self.assertEqual(overflow,[])
            self.assertTrue(row.locator('[data-label="Tail"]').evaluate('el=>el.scrollWidth<=el.clientWidth+1'))
            meal=page.locator('.neorain-desktop-board th').filter(has_text=__import__('re').compile('^MEAL$'))
            self.assertEqual(meal.evaluate('el=>getComputedStyle(el).whiteSpace'),'nowrap')
        with page.expect_response(lambda r:'/neorain/override' in r.url and r.request.method=='POST') as saved:field.fill('0125')
        self.assertEqual(saved.value.status,200);expect(field).to_have_value('01:25')
        with self.f.fixture.app.app_context():
            mission=db.session.get(SortDateMission,mid)
            self.assertEqual(mission.planned_datetime_utc,datetime(2026,10,16,6))
        field.focus()
        with page.expect_response(lambda r:'/neorain/override' in r.url and r.request.method=='POST') as reset:
            page.locator('[data-rain-reset-selected]').click()
        self.assertEqual(reset.value.status,200);expect(field).to_have_value('01:00')

    def test_std_etd_google_refresh_preserves_drafts_and_neo_authority(self):
        from app.services.neorain_overrides import observe_import
        def upstream(value):
            with self.f.fixture.app.app_context():
                mission=db.session.get(SortDateMission,self.f.ids['departure'])
                values={'eta_datetime_utc':value,'eta_source':'google_rain'}
                observe_import(mission,'etd',values)
                for key,item in values.items():setattr(mission,key,item)
                db.session.commit()
        for engine,width in (('chromium',1920),('webkit',390)):
            browser=getattr(self.f.fixture.pw,engine).launch()
            context=browser.new_context(viewport={'width':width,'height':900},service_workers='block',has_touch=width<900,is_mobile=width<900)
            page=context.new_page()
            try:
                self.f.fixture().login(page);self.f.fixture().ready(page,'/neorain/outbound')
                def clock():
                    article=page.locator(f'.neorain-mobile-mission[data-neorain-mission-id="{self.f.ids["departure"]}"]')
                    if width<900 and article.locator('[data-neorain-mobile-inline-detail]').is_hidden():
                        article.locator('[data-neorain-mobile-row-open]').first.click()
                    row=article if width<900 else page.locator(f'.neorain-desktop-board tr[data-neorain-mission-id="{self.f.ids["departure"]}"]')
                    return row.locator('[data-rain-override-field="etd"] [data-rain-override-time]')
                upstream(datetime(2026,10,16,6,40))
                expect(clock()).to_have_value('01:40',timeout=20000)
                page.wait_for_load_state('load')
                clock().fill('01')
                expect(clock().locator('..')).to_have_attribute('data-rain-override-dirty','true')
                upstream(datetime(2026,10,16,6,45))
                expect(page.locator('[data-neorain-outbound-live]')).to_have_attribute('data-neorain-refresh-deferred','true',timeout=20000)
                expect(clock()).to_have_value('01')
                with page.expect_response(lambda r:'/neorain/override' in r.url and r.request.method=='POST') as stale:clock().fill('0125')
                self.assertEqual(stale.value.status,409);expect(clock()).to_have_value('0125')
                page.reload();expect(clock()).to_have_value('01:45')
                with page.expect_response(lambda r:'/neorain/override' in r.url and r.request.method=='POST') as saved:clock().fill('0125')
                self.assertEqual(saved.value.status,200);expect(clock()).to_have_value('01:25')
                upstream(datetime(2026,10,16,6,50));page.reload();expect(clock()).to_have_value('01:25')
                clock().fill('');clock().focus()
                with page.expect_response(lambda r:'/neorain/override' in r.url and r.request.method=='POST') as reset:
                    page.locator('[data-rain-reset-selected]').click()
                self.assertEqual(reset.value.status,200);expect(clock()).to_have_value('01:50')
                upstream(None);expect(clock()).to_have_value('01:00',timeout=20000)
                with self.f.fixture.app.app_context():
                    mission=db.session.get(SortDateMission,self.f.ids['departure'])
                    self.assertEqual(mission.planned_datetime_local,datetime(2026,10,16,1))
                    self.assertEqual(mission.planned_datetime_utc,datetime(2026,10,16,6))
            finally:context.close();browser.close()
