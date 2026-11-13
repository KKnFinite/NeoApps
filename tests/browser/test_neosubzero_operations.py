"""Production-shaped, isolated Release 1 layouts and actual operational forms."""
import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from playwright.sync_api import expect
from app.extensions import db
from app.models import SortDateMission, SortDateOperation, SortDateParkingAssignment, NeoSubZeroDepartureDeiceEvent, NeoSubZeroPretreatState, SortDateTailState, NeoSubZeroUserPreference
from tests.browser import test_neosubzero_ucc as fixtures


class OperationsBrowserTest(unittest.TestCase):
    login=fixtures.UccBrowserTest.login
    def ready(self, page, path):
        fixtures.UccBrowserTest.ready(self, page, path)
        expect(page.locator('[data-ops-workspace]')).to_have_attribute('data-ready','true')
    tearDownClass=classmethod(fixtures.UccBrowserTest.tearDownClass.__func__)

    def submit(self, page, button):
        with page.expect_response(lambda response: response.url.endswith('/operations/mutate') and response.request.method == 'POST') as pending:
            button.click()
        response=pending.value
        self.assertEqual(response.status,200,response.text())
        self.assertTrue(response.json()['ok'],response.json())

    def layout(self, page, value):
        with page.expect_response(lambda r:r.url.endswith('/operations/preferences') and r.request.method=='POST') as pending:
            page.locator(f'[data-ops-layout="{value}"]').click()
        self.assertEqual(pending.value.status,200,pending.value.text())
        self.assertTrue(pending.value.json()['ok'])
        expect(page.locator(f'[data-ops-layout="{value}"]')).to_be_enabled()

    def ramp(self, page, value):
        # Ramp selection is now mobile-only. Establish its real per-user
        # preference through that UI, then restore the caller's desktop width.
        viewport=page.viewport_size
        if viewport['width']>900:page.set_viewport_size({'width':390,'height':viewport['height']})
        with page.expect_response(lambda r:r.url.endswith('/operations/preferences') and r.request.method=='POST') as pending:
            page.locator('[data-ops-ramp]').select_option(value)
        self.assertEqual(pending.value.status,200,pending.value.text())
        self.assertTrue(pending.value.json()['ok'])
        expect(page.locator('[data-ops-ramp]')).to_be_enabled()
        if viewport['width']>900:page.set_viewport_size(viewport)

    @classmethod
    def setUpClass(cls):
        fixtures.UccBrowserTest.setUpClass.__func__(cls)
        with cls.app.app_context():
            fixture=fixtures.NeoSubZeroUccTest();fixture.operation=db.session.get(SortDateOperation,cls.operation_id)
            mission=fixture._departure('UPS7777','N777UP','DEN',4)
            mission.planned_datetime_utc=datetime(2026,10,23,5);mission.planned_datetime_local=datetime(2026,10,23,0)
            db.session.add(SortDateParkingAssignment(sort_date_operation_id=cls.operation_id,tail_number=mission.assigned_tail_number,ramp_code='D',position_code='D04'))
            inbound=fixture._departure('UPS7100','N441UP','RFD',3)
            inbound.mission_type='arrival';inbound.origin='SDF'
            inbound.eta_datetime_utc=datetime(2026,10,23,2,35)
            inbound.planned_datetime_utc=datetime(2026,10,23,2,30)
            cls.flow_id=mission.id;db.session.commit()

    def seed(self):
        with self.app.app_context():
            NeoSubZeroUserPreference.query.delete();NeoSubZeroPretreatState.query.delete();SortDateTailState.query.delete()
            NeoSubZeroDepartureDeiceEvent.query.filter_by(sort_date_mission_id=self.flow_id).delete()
            for index,mid in enumerate(self.missions):
                mission=db.session.get(SortDateMission,mid);mission.assigned_tail_number=f'N44{index}UP';mission.eta_datetime_utc=datetime(2026,10,23,5,index*5)
                if index == 1:
                    mission.planned_datetime_utc = mission.eta_datetime_utc = datetime(2026,10,23,5,20)
                event=NeoSubZeroDepartureDeiceEvent.query.filter_by(sort_date_mission_id=mid).one()
                event.tail_number=mission.assigned_tail_number
                event.status=('deice_planned','configured','configured','finished','negative','cleared')[index]
                event.treatment_plan='type_i_type_iv' if index in (0,2,5) else 'one_type_i'
                event.pass1_surface_area='wings_tail';event.pass2_surface_area='entire_aircraft' if index in (0,2,5) else None
                event.configured_at_utc=datetime(2026,10,23,4) if index in (1,2,3) else None
                event.pass1_started_at_utc=datetime(2026,10,23,4,1) if index in (2,3,5) else None
                event.pass1_ended_at_utc=datetime(2026,10,23,4,2) if index in (3,5) else None
                event.pass2_started_at_utc=datetime(2026,10,23,4,3) if index==5 else None
                event.pass2_ended_at_utc=datetime(2026,10,23,4,4) if index==5 else None
            state=NeoSubZeroPretreatState(sort_date_operation_id=self.operation_id,tail_number='N440UP',pretreat_planned=True,pass1_surface_area='wings_only',pass2_surface_area='wings_tail',
                pass1_started_at_utc=datetime(2026,10,23,3),pass1_ended_at_utc=datetime(2026,10,23,3,2),pass2_started_at_utc=datetime(2026,10,23,3,3),pass2_ended_at_utc=datetime(2026,10,23,3,4))
            db.session.add(state);op=db.session.get(SortDateOperation,self.operation_id)
            db.session.add(SortDateTailState(sort_date=op.sort_date,gateway_code='RFD',sort_name='night',tail_number='N440UP',pretreat_status=True))
            db.session.commit()

    def test_layouts_preferences_forms_live_drafts_and_tail_identity(self):
        evidence=Path('instance/browser-evidence/subzero-operations').resolve();evidence.mkdir(parents=True,exist_ok=True)
        measurements=[]
        for engine in ('chromium','webkit'):
            self.seed()
            browser=getattr(self.pw,engine).launch();context=browser.new_context(viewport={'width':1440,'height':1000},service_workers='block')
            page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            self.login(page);self.ready(page,'/neosubzero/operations')
            root=page.locator('[data-ops-workspace]')
            for mode in ('outbound','pretreat'):
                self.ready(page,'/neosubzero/operations?mode='+mode)
                expect(page).to_have_title('Deice Dispatch | NeoSub-Zero')
                expect(root.locator('h1')).to_have_text('DEICE DISPATCH')
                if mode == 'pretreat':
                    expect(page.locator('[data-ops-layout]')).to_have_count(0)
                    row = root.locator(f'[data-ops-row][data-mission-id="{self.missions[1]}"]')
                    expect(row.locator('[data-ops-pretreat-location] > *')).to_have_text(['A04', 'ETA 21:35'])
                    expect(row.locator('[data-ops-pretreat-route] > *')).to_have_text(['GT: 2:45', 'SDF · ETD 00:20'])
                    search = page.locator('[data-ops-search]')
                    for query in ('n441up', 'ups201', 'A04'):
                        search.fill(query)
                        expect(page.locator('[data-ops-row]:visible')).to_have_count(1)
                    search.fill('sdf')
                    expect(page.locator('[data-ops-row]:visible').first).to_be_visible()
                    search.fill('not-a-mission')
                    expect(page.locator('[data-ops-search-empty]')).to_be_visible()
                    page.locator('[data-ops-search-clear]').click()
                    expect(search).to_have_value('')
                for width,height in ((320,900),(375,900),(390,900),(430,900),(768,1024),(1366,768),(1920,900),(1920,1080),(3840,2160)):
                    page.set_viewport_size({'width':width,'height':height})
                    page.wait_for_function('(w)=>document.documentElement.clientWidth===w',arg=width)
                    for layout in (('list','visual') if mode == 'outbound' and width > 900 else ('list',)):
                        if mode == 'outbound' and width > 900:
                            self.layout(page,layout)
                        elif width <= 900:
                            expect(page.locator('[data-ops-layout]:visible')).to_have_count(0)
                        expect(root).to_have_attribute('data-layout',layout)
                        if layout=='list':
                            self.ramp(page,'All');expect(root).to_have_attribute('data-ramp','All')
                        if layout=='visual':
                            expect(page.locator('.ops-ramp-column')).to_have_count(5)
                            self.assertEqual(page.locator('.ops-ramp-column h2').all_text_contents(),['ALPHA','BRAVO','CHARLIE','DELTA','ECHO'])
                            self.assertEqual(page.locator('.ops-ramp-column:visible').count(),1 if width<=900 else 5)
                        page.screenshot(path=str(evidence/f'{engine}-{mode}-{layout}-{width}.png'))
                        geometry=page.evaluate('() => ({overflow:document.documentElement.scrollWidth-innerWidth,culprits:[...document.querySelectorAll("main.content *")].filter(e=>e.getBoundingClientRect().right>innerWidth+1 && !e.closest("[hidden]")).slice(0,12).map(e=>({tag:e.tagName,cls:e.className,width:e.getBoundingClientRect().width,right:e.getBoundingClientRect().right}))})')
                        self.assertLessEqual(geometry['overflow'],1,dict(engine=engine,mode=mode,width=width,layout=layout,**geometry))
                        if mode == 'pretreat' and width > 900:
                            grid = page.locator('.ops-pretreat-scroll')
                            cells = row.evaluate("""row => {
                                const selectors = ['.ops-identity','[data-ops-pretreat-location]',
                                    '[data-ops-pretreat-route]','.ops-plan-toggle','.ops-pretreat-config',
                                    '.ops-pretreat-pass:first-of-type','.ops-pretreat-pass:nth-of-type(2)',
                                    '.ops-pretreat-notes summary'];
                                return {height: row.offsetHeight, cells: selectors.map(selector => {
                                    const {left,right,top,bottom} = row.querySelector(selector).getBoundingClientRect();
                                    return {left,right,top,bottom};
                                })};
                            }""")
                            self.assertTrue(60 <= cells['height'] <= 75, cells)
                            self.assertLess(max(c['top'] for c in cells['cells']), min(c['bottom'] for c in cells['cells']), cells)
                            self.assertTrue(all(a['right'] <= b['left'] + 1 for a,b in zip(cells['cells'],cells['cells'][1:])), cells)
                            if width == 1366:
                                grid.evaluate('e => e.scrollLeft = e.scrollWidth')
                                self.assertLessEqual(
                                    row.locator('.ops-identity').evaluate('e => e.getBoundingClientRect().left'),
                                    grid.evaluate('e => e.getBoundingClientRect().left') + 1
                                )
                                page.screenshot(path=str(evidence/f'{engine}-pretreat-scrolled-1366.png'))
                                grid.evaluate('e => e.scrollLeft = 0')
                            if width == 1920:
                                self.assertLessEqual(grid.evaluate('e=>e.scrollWidth-e.clientWidth'),1)
                        # Take one DOM snapshot: a live board replacement must not mix
                        # geometry from different revisions or stale nth locators.
                        visible=page.locator('[data-ops-board]').evaluate('board=>[...board.querySelectorAll("[data-ops-row]")].filter(row=>row.getBoundingClientRect().height>0).map(row=>({right:row.getBoundingClientRect().right,passes:[...row.querySelectorAll("[data-ops-primary-pass]:not([hidden])")].map(element=>{const p=element.getBoundingClientRect();return {width:p.width,height:p.height};}).filter(p=>p.width>0)}))')
                        self.assertTrue(visible)
                        for bounds_row in visible:
                            if mode != 'pretreat' or width <= 900:
                                self.assertLessEqual(bounds_row['right'],width+1)
                            for bounds in bounds_row['passes']:
                                self.assertGreater(bounds['width'],0);self.assertGreater(bounds['height'],0)
                        measurements.append(dict(engine=engine,mode=mode,width=width,layout=layout,overflow=page.evaluate('document.documentElement.scrollWidth-innerWidth')))
                        page.screenshot(path=str(evidence/f'{engine}-{mode}-{layout}-{width}.png'))
            # A shared ramp survives mode changes and reload; mode/layout are user preferences.
            page.set_viewport_size({'width':1440,'height':1000})
            page.locator('[data-ops-mode="outbound"]').click();expect(root).to_have_attribute('data-mode','outbound');expect(root).to_have_attribute('data-ready','true')
            expect(root).to_have_attribute('data-layout','visual')
            self.layout(page,'list');expect(root).to_have_attribute('data-layout','list')
            expect(page.locator('[data-ops-context]')).to_have_count(0)
            page.locator('[data-ops-mode="pretreat"]').click();expect(root).to_have_attribute('data-mode','pretreat');expect(root).to_have_attribute('data-ready','true')
            page.locator('[data-ops-mode="outbound"]').click();expect(root).to_have_attribute('data-mode','outbound');expect(root).to_have_attribute('data-ready','true')
            self.ramp(page,'Bravo');expect(root).to_have_attribute('data-ramp','Bravo')
            other=browser.new_context(viewport={'width':375,'height':900});other_page=other.new_page()
            self.login(other_page,'drawer-watcher');self.ready(other_page,'/neosubzero/operations')
            expect(other_page.locator('[data-ops-workspace]')).to_have_attribute('data-mode','outbound')
            expect(other_page.locator('[data-ops-workspace]')).to_have_attribute('data-ramp','All');other.close()
            page.locator('[data-ops-mode="outbound"]').click();expect(root).to_have_attribute('data-mode','outbound');expect(root).to_have_attribute('data-ramp','Bravo')
            page.reload();expect(root).to_have_attribute('data-ready','true');expect(root).to_have_attribute('data-ramp','Bravo')
            self.ramp(page,'All');expect(root).to_have_attribute('data-ramp','All')
            # Every required color covers the card, not just an accent.
            for status in ('planned','configured','deicing','fin','negative','gone'):
                card=page.locator('[data-ops-row][data-status="'+status+'"]').first
                self.assertNotEqual(card.evaluate('e=>getComputedStyle(e).backgroundColor'),'rgba(0, 0, 0, 0)')
            self.assertEqual(page.locator('[data-ops-row]').last.get_attribute('data-status'),'gone')
            expect(page.locator(f'[data-mission-id="{self.missions[0]}"] .ops-pretreated')).to_be_visible()
            expect(page.locator(f'[data-mission-id="{self.missions[1]}"] .ops-pretreated')).to_have_count(0)
            # Canonical coordinator-capable workflow, with no gallon gate on FIN/CLEAR.
            page.locator(f'[data-ops-row][data-mission-id="{self.flow_id}"] a').click()
            pane=page.locator('[data-ops-detail]');expect(pane).to_be_visible()
            self.submit(page,pane.locator('button[value="initial_contact"]'))
            expect(page.locator('[data-ops-feedback]')).to_contain_text('SAVED')
            expect(pane.locator('select[name="treatment_plan"]')).to_be_visible()
            self.submit(page,pane.locator('button[value="toggle_configured"]'))
            expect(page.locator(f'[data-ops-row][data-mission-id="{self.flow_id}"]')).to_have_attribute('data-status','configured')
            form=pane.locator('[data-subzero-deice-form]');form.locator('[name="treatment_plan"]').select_option('type_i_type_iv')
            form.locator('[name="pass1_surface_area"]').select_option('wings_tail');form.locator('[name="pass2_surface_area"]').select_option('wings_only')
            for name,value in (('pass1_start','22:00'),('pass1_end','22:02'),('pass2_start','22:04'),('pass2_end','22:08')):form.locator('[name="'+name+'"]').fill(value)
            self.submit(page,form.locator('button[type="submit"]'));expect(page.locator(f'[data-ops-row][data-mission-id="{self.flow_id}"]')).to_have_attribute('data-status','fin')
            self.submit(page,pane.locator('button[value="clear"]'));expect(page.locator(f'[data-ops-row][data-mission-id="{self.flow_id}"]')).to_have_attribute('data-status','radio-pending')
            expect(pane.locator('[data-radio-script]')).to_contain_text('2204')
            self.submit(page,pane.locator('button[value="radio_complete"]'));expect(page.locator(f'[data-ops-row][data-mission-id="{self.flow_id}"]')).to_have_attribute('data-status','gone')
            expect(pane.locator('.ops-readback')).to_contain_text('2204')
            page.screenshot(path=str(evidence/f'{engine}-completed-detail.png'))
            pane.locator('[data-ops-close]').click()
            # Draft, caret/focus and primary updates survive a live response and reload.
            page.locator(f'[data-ops-row][data-mission-id="{self.missions[0]}"] a').click()
            field=pane.locator('[name="pass1_start"]');field.fill('21:45');field.focus()
            with self.app.app_context():
                mission=db.session.get(SortDateMission,self.missions[1]);mission.eta_datetime_utc=datetime(2026,10,23,6,40);db.session.commit()
            expect(page.locator(f'[data-ops-row][data-mission-id="{self.missions[1]}"] .ops-location')).to_contain_text('01:40',timeout=10000)
            expect(field).to_have_value('21:45');expect(field).to_be_focused()
            page.reload();self.ready(page,f'/neosubzero/operations?mode=outbound&mission={self.missions[0]}')
            expect(pane.locator('[name="pass1_start"]')).to_have_value('21:45')
            with self.app.app_context():
                mission=db.session.get(SortDateMission,self.missions[0]);mission.assigned_tail_number='N-REPLACED';db.session.commit()
            expect(page.locator(f'[data-ops-row][data-mission-id="{self.missions[0]}"]')).to_have_attribute('data-status','gone',timeout=10000)
            expect(pane.locator('[name="pass1_start"]')).to_have_value('21:45')
            pane.locator('[data-subzero-deice-form] button[type="submit"]').click();expect(page.locator('[data-ops-feedback]')).to_contain_text('identity changed')
            expect(pane.locator('[name="pass1_start"]')).to_have_value('21:45')
            # Actual phone Pretreat editing, failure recovery and independent completion.
            pane.locator('[data-ops-close]').click();page.set_viewport_size({'width':375,'height':900})
            page.locator('[data-ops-mode="pretreat"]').click();expect(root).to_have_attribute('data-mode','pretreat')
            expect(root).to_have_attribute('data-ready','true')
            row = page.locator(f'[data-ops-row][data-mission-id="{self.missions[1]}"]')
            inline = row.locator('[data-ops-inline]')
            expect(inline).to_be_hidden()
            expect(row.locator('.ops-mobile-times')).to_contain_text('ETA')
            row.locator('.ops-pretreat-summary').click()
            expect(pane).to_be_hidden()
            treatment = inline.locator('[data-ops-inline-treatment]')
            notes_form = inline.locator('.ops-pretreat-notes')
            notes_form.locator('summary').click()
            notes = notes_form.locator('[name="notes"]')
            search = page.locator('[data-ops-search]')
            search.fill('ups201 A04')
            expect(page.locator('[data-ops-row]:visible')).to_have_count(1)
            notes.fill('Concurrent creation draft')
            notes.focus()
            with self.app.app_context():
                db.session.add(NeoSubZeroPretreatState(sort_date_operation_id=self.operation_id,tail_number='N441UP',pretreat_planned=True))
                db.session.commit()
            expect(row).to_have_attribute('data-status','planned',timeout=12000)
            expect(notes).to_have_value('Concurrent creation draft')
            expect(notes_form.locator('[name="expected_version"]')).to_have_value('')
            expect(notes).to_be_focused()
            expect(search).to_have_value('ups201 A04')
            expect(page.locator('[data-ops-row]:visible')).to_have_count(1)
            notes.fill('Concurrent creation draft revised')
            with page.expect_response(lambda r:r.url.endswith('/operations/mutate')) as stale:
                notes.press('Tab')
            self.assertEqual(stale.value.status,409)
            expect(inline.locator('[data-ops-inline-feedback]')).to_contain_text('Pretreat changed')
            expect(notes).to_have_value('Concurrent creation draft revised')
            page.once('dialog',lambda dialog:dialog.dismiss());inline.locator('[data-ops-reload-inline]').click()
            expect(notes).to_have_value('Concurrent creation draft revised')
            page.once('dialog',lambda dialog:dialog.accept());inline.locator('[data-ops-reload-inline]').click()
            expect(notes).to_have_value('')
            expect(treatment.locator('[name="reason_for_application"]')).to_have_count(0)
            expect(treatment.locator('[name="pass1_plan"]')).to_have_value('type_i')
            expect(inline.get_by_role('button',name='SAVE',exact=True)).to_have_count(0)
            expect(inline.get_by_text('PLANNING / TREATMENT RECORDS')).to_have_count(0)

            def autosave(action):
                with page.expect_response(lambda r:r.url.endswith('/operations/mutate'), timeout=10000) as pending:
                    action()
                self.assertEqual(pending.value.status,200,pending.value.text())
                expect(inline.locator('[data-ops-inline-feedback]')).to_be_hidden()
            configure = inline.locator('button[value="toggle_configured"]')
            autosave(configure.click)
            expect(configure).to_have_attribute('aria-pressed','true')
            expect(row).to_have_attribute('data-status','configured')
            self.assertEqual(row.locator('.ops-mobile-status').evaluate('e=>getComputedStyle(e).backgroundColor'),'rgb(39, 135, 86)')
            self.assertNotEqual(row.evaluate('e=>getComputedStyle(e).backgroundColor'),'rgb(39, 135, 86)')
            page.set_viewport_size({'width':1366,'height':900})
            self.assertEqual(configure.evaluate('e=>getComputedStyle(e).backgroundColor'),'rgb(39, 135, 86)')
            autosave(configure.click)
            expect(configure).to_have_attribute('aria-pressed','false')
            plan = row.get_by_role('checkbox',name='Pretreat planned for N441UP')
            autosave(plan.uncheck)
            expect(row).to_have_attribute('data-status','untreated')
            autosave(plan.check)
            expect(row).to_have_attribute('data-status','planned')
            if notes.is_visible():
                notes_form.locator('summary').click()
            height = row.evaluate('e => e.offsetHeight')
            notes_form.locator('summary').click()
            expect(notes).to_be_visible()
            notes_form.locator('summary').click()
            self.assertEqual(row.evaluate('e => e.offsetHeight'),height)
            autosave(lambda:treatment.locator('[name="pass1_surface_area"]').select_option('wings_only'))
            autosave(lambda:treatment.locator('[name="pass2_plan"]').select_option('type_iv'))
            autosave(lambda:treatment.locator('[name="pass2_surface_area"]').select_option('entire_aircraft'))
            # A committed edit queued during a save survives reconciliation of the same row.
            page.evaluate("""() => {
                const fetch = window.fetch;
                window.fetch = (url, options) => {
                    if (!String(url).endsWith('/operations/mutate')) return fetch(url, options);
                    window.fetch = fetch;
                    return new Promise(resolve => { window.releasePretreat = () => resolve(fetch(url, options)); });
                };
            }""")
            with page.expect_response(lambda r: 'Queued during a treatment save' in (r.request.post_data or '')) as queued:
                treatment.locator('[name="pass1_surface_area"]').select_option('wings_tail')
                page.wait_for_function('typeof window.releasePretreat === "function"')
                expect(row).to_have_attribute('aria-busy','true')
                notes_form.locator('summary').click()
                notes.fill('Queued during a treatment save')
                notes.press('Tab')
                page.evaluate('window.releasePretreat()')
            self.assertEqual(queued.value.status,200,queued.value.text())
            expect(row).not_to_have_attribute('aria-busy','true')
            expect(notes).to_have_value('Queued during a treatment save')
            page.set_viewport_size({'width':375,'height':900})
            if not inline.is_visible():
                row.locator('.ops-pretreat-summary').click()
            # A controlled operational clock keeps NOW independent of the host time.
            page.evaluate("window.NeoSubZeroDepartureForms.operationalHHMM = () => '2100'")
            start = treatment.locator('[name="pass1_start"]')
            start.fill('24')
            start.press('Tab')
            expect(inline.locator('[data-ops-inline-feedback]')).to_contain_text('Enter a valid time')
            expect(start).to_have_value('24')
            autosave(treatment.get_by_role('button',name='Set Pass 1 start to now').click)
            expect(treatment.locator('[name="pass1_start"]')).to_have_value('21:00')
            for name,value in (('pass1_end','21:02'),('pass2_start','21:04'),('pass2_end','21:08')):
                field = treatment.locator('[name="'+name+'"]')
                field.fill(value)
                autosave(lambda:field.press('Tab'))
            expect(row).to_have_attribute('data-status','fin')
            self.assertEqual(notes_form.locator('[name="expected_version"]').input_value(),
                             treatment.locator('[name="expected_version"]').input_value())
            if not notes.is_visible():
                notes_form.locator('summary').click()
            notes.fill('Mobile pretreat independent notes')
            fail_save=lambda route:route.abort('connectionrefused')
            page.route('**/operations/mutate',fail_save)
            notes.press('Tab')
            expect(inline.locator('[data-ops-inline-feedback]')).to_contain_text('draft is kept')
            expect(notes).to_have_value('Mobile pretreat independent notes')
            page.unroute('**/operations/mutate',fail_save)
            autosave(inline.locator('[data-ops-pretreat-retry]').click)
            page.locator('[data-ops-search-clear]').click()
            expect(search).to_have_value('')
            expect(row.locator('.ops-mobile-status')).to_contain_text('PRETREATED')
            more = inline.get_by_label('More actions for N441UP')
            more.click()
            expect(inline.get_by_role('link',name='Treatment history · N441UP')).to_have_attribute(
                'href','/neosubzero/deice-log?activity=pretreat&tail=N441UP'
            )
            inline.get_by_role('button',name='Correct recorded treatment').click()
            expect(inline.get_by_role('button',name='UNLOCK CORRECTION')).to_be_visible()
            expect(treatment.locator('[name="pass2_end"]')).to_have_attribute('readonly','')
            treatment.locator('[data-ops-disclosure="correction"] > summary').click()
            page.screenshot(path=str(evidence/f'{engine}-mobile-pretreat-detail.png'))
            self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth-innerWidth'),1)
            self.assertLessEqual(inline.evaluate('e=>e.scrollWidth-e.clientWidth'),1)
            with self.app.app_context():
                saved = NeoSubZeroPretreatState.query.filter_by(tail_number='N441UP').one()
                self.assertEqual(saved.reason_for_application, 'Pretreat')
                self.assertEqual([saved.pass_evidence[str(i)]['reason'] for i in (1,2)], ['Pretreat','Pretreat'])
                self.assertEqual(saved.notes, 'Mobile pretreat independent notes')
            page.locator('[data-ops-mode="outbound"]').click();expect(root).to_have_attribute('data-mode','outbound')
            expect(root).to_have_attribute('data-ready','true')
            expect(page.locator(f'[data-ops-row][data-mission-id="{self.missions[1]}"] .ops-mobile-status')).to_contain_text('PRETREATED')
            with self.app.app_context():
                self.assertEqual(NeoSubZeroDepartureDeiceEvent.query.filter_by(sort_date_mission_id=self.missions[1]).one().status,'configured')
            # Shared filter resets on the next canonical operation, not a wall-clock guess.
            self.ramp(page,'Bravo');expect(root).to_have_attribute('data-ramp','Bravo')
            old_operation_id=type(self).operation_id
            with self.app.app_context():
                operation=db.session.get(SortDateOperation,old_operation_id)
                future=SortDateOperation.query.filter_by(gateway_id=operation.gateway_id,sort_date=operation.sort_date+timedelta(days=1),sort_name=operation.sort_name).first()
                if future is None:
                    future=SortDateOperation(gateway_id=operation.gateway_id,gateway_code='RFD',sort_date=operation.sort_date+timedelta(days=1),sort_name=operation.sort_name);db.session.add(future);db.session.commit()
                type(self).operation_id=future.id
            expect(root).to_have_attribute('data-operation-id',str(type(self).operation_id),timeout=10000)
            expect(root).to_have_attribute('data-ramp','All');expect(root).to_have_attribute('data-mode','outbound')
            expect(page.locator('[data-ops-row]')).to_have_count(0)
            expect(page.locator('[data-ops-context]')).to_have_count(0)
            type(self).operation_id=old_operation_id
            self.assertFalse(errors,errors)
            context.close();browser.close()
        (evidence/'measurements.json').write_text(json.dumps(measurements,indent=2))
