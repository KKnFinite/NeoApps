"""Production-shaped, isolated Release 1 layouts and actual operational forms."""
import json
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from playwright.sync_api import expect
from app.extensions import db
from app.models import (NeoSubZeroAuditEntry, NeoSubZeroDepartureDeiceEvent, NeoSubZeroPretreatState,
                        NeoSubZeroSprayRecord, NeoSubZeroUccTruckAssignment, NeoSubZeroUserPreference,
                        SortDateMission, SortDateOperation, SortDateParkingAssignment, SortDateTailState)
from tests.browser import test_neosubzero_ucc as fixtures


class OperationsBrowserTest(unittest.TestCase):
    login=fixtures.UccBrowserTest.login
    def ready(self, page, path):
        fixtures.UccBrowserTest.ready(self, page, path)
        expect(page.locator('[data-ops-workspace]')).to_have_attribute('data-ready','true')
    tearDownClass=classmethod(fixtures.UccBrowserTest.tearDownClass.__func__)

    def submit(self, page, button):
        submitted_form = button.evaluate_handle('button => button.form').as_element()
        hidden_form = submitted_form.get_attribute('hidden') is not None
        with page.expect_response(lambda response: response.url.endswith('/operations/mutate') and response.request.method == 'POST') as pending:
            button.click()
        response=pending.value
        self.assertEqual(response.status,200,response.text())
        self.assertTrue(response.json()['ok'],response.json())
        if hidden_form:
            page.wait_for_function('form => !form.isConnected', arg=submitted_form)
        else:
            submitted_form.wait_for_element_state('hidden')

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
            from tests.test_neorain_sort_refresh import configure_sort_window
            _, opening = configure_sort_window(fixture.operation.gateway, fixture.operation.sort_date)
            from app.services.live_screen_refresh import save_live_screen_refresh_override
            from app.neonodes.neosubzero import routes
            for screen in (routes.OUTBOUND_REFRESH_KEY, routes.PRETREAT_REFRESH_KEY):
                save_live_screen_refresh_override(fixture.operation.gateway, screen, '5')
            cls.sort_now = opening.replace(tzinfo=ZoneInfo('America/Chicago')) + timedelta(minutes=70)
            cls.app.config['CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE'] = cls.sort_now
            mission=fixture._departure('UPS7777','N777UP','DEN',4)
            mission.planned_datetime_utc=datetime(2026,10,23,5);mission.planned_datetime_local=datetime(2026,10,23,0)
            db.session.add(SortDateParkingAssignment(sort_date_operation_id=cls.operation_id,tail_number=mission.assigned_tail_number,ramp_code='D',position_code='D04'))
            inbound=fixture._departure('UPS7100','N441UP','RFD',3)
            inbound.mission_type='arrival';inbound.origin='SDF'
            inbound.eta_datetime_utc=datetime(2026,10,23,2,35)
            inbound.planned_datetime_utc=datetime(2026,10,23,2,30)
            cls.inbound_id=inbound.id
            cls.flow_id=mission.id;db.session.commit()

    def seed(self):
        with self.app.app_context():
            inbound=db.session.get(SortDateMission,self.inbound_id)
            inbound.arrival_status='scheduled'
            NeoSubZeroSprayRecord.query.filter(NeoSubZeroSprayRecord.pretreat_state_id.is_not(None)).delete()
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

    def test_recorded_pretreatment_visible_only_on_its_aircraft_in_both_dispatch_layouts(self):
        self.seed()
        for engine in ('chromium', 'webkit'):
            with self.subTest(engine=engine):
                browser = getattr(self.pw, engine).launch()
                try:
                    page = browser.new_page(viewport={'width': 1440, 'height': 900})
                    page.clock.install(time=self.sort_now)
                    self.login(page)
                    self.ready(page, '/neosubzero/operations?mode=outbound')
                    for layout in ('list', 'visual'):
                        self.layout(page, layout)
                        treated = page.locator(f'[data-ops-row][data-mission-id="{self.missions[0]}"]')
                        adjacent = page.locator(f'[data-ops-row][data-mission-id="{self.missions[1]}"]')
                        expect(treated.locator('.ops-pretreated:visible')).to_have_text('PRETREATED')
                        expect(adjacent.locator('.ops-pretreated')).to_have_count(0)
                finally:
                    browser.close()

    def test_layouts_preferences_forms_live_drafts_and_tail_identity(self):
        evidence=Path('instance/browser-evidence/subzero-operations').resolve();evidence.mkdir(parents=True,exist_ok=True)
        measurements=[]
        for engine in ('chromium','webkit'):
            self.seed()
            browser=getattr(self.pw,engine).launch();context=browser.new_context(viewport={'width':1440,'height':1000},service_workers='block')
            page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            page.clock.install(time=self.sort_now)
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
            expect(page.locator(f'[data-mission-id="{self.missions[0]}"] .ops-pretreated:visible')).to_have_count(1)
            expect(page.locator(f'[data-mission-id="{self.missions[1]}"] .ops-pretreated')).to_have_count(0)
            # Canonical coordinator-capable workflow, with no gallon gate on FIN/CLEAR.
            page.locator(f'[data-ops-row][data-mission-id="{self.flow_id}"] [data-ops-accordion] > summary').click()
            pane=page.locator('[data-ops-inline]:visible');expect(pane).to_be_visible()
            self.submit(page,page.locator(f'[data-ops-row][data-mission-id="{self.flow_id}"] button[value="initial_contact"]'))
            expect(page.locator('[data-ops-feedback]')).to_contain_text('SAVED')
            expect(pane.locator('select[name="treatment_plan"]')).to_be_visible()
            self.submit(page,page.locator(f'[data-ops-row][data-mission-id="{self.flow_id}"] button[value="toggle_configured"]'))
            expect(page.locator(f'[data-ops-row][data-mission-id="{self.flow_id}"]')).to_have_attribute('data-status','configured')
            form=pane.locator('[data-subzero-deice-form]');form.locator('[name="treatment_plan"]').select_option('type_i_type_iv')
            form.locator('[name="pass1_surface_area"]').select_option('wings_tail');form.locator('[name="pass2_surface_area"]').select_option('wings_only')
            for name,value in (('pass1_start','22:00'),('pass1_end','22:02'),('pass2_start','22:04'),('pass2_end','22:08')):form.locator('[name="'+name+'"]').fill(value)
            self.submit(page,form.locator('button[type="submit"]'));expect(page.locator(f'[data-ops-row][data-mission-id="{self.flow_id}"]')).to_have_attribute('data-status','fin')
            self.submit(page,page.locator(f'[data-ops-row][data-mission-id="{self.flow_id}"] button[value="clear"]'));expect(page.locator(f'[data-ops-row][data-mission-id="{self.flow_id}"]')).to_have_attribute('data-status','radio-pending')
            expect(pane.locator('[data-radio-script]')).to_contain_text('2204')
            self.submit(page,pane.locator('button[value="radio_complete"]'));expect(page.locator(f'[data-ops-row][data-mission-id="{self.flow_id}"]')).to_have_attribute('data-status','gone')
            expect(page.locator('[data-ops-row][data-status="radio-pending"]')).to_have_count(0)
            page.screenshot(path=str(evidence/f'{engine}-completed-detail.png'))
            # Radio confirmation collapsed the mission.
            # Draft, caret/focus and primary updates survive a live response and reload.
            page.locator(f'[data-ops-row][data-mission-id="{self.missions[0]}"] [data-ops-accordion] > summary').click()
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
            pane.locator('[data-subzero-deice-form] button[type="submit"]').click();expect(pane.locator('[data-ops-inline-feedback]')).to_contain_text('identity changed')
            expect(pane.locator('[name="pass1_start"]')).to_have_value('21:45')
            # Actual phone Pretreat editing, failure recovery and independent completion.
            pane.locator('[data-ops-collapse-inline]').click();page.set_viewport_size({'width':375,'height':900})
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
            scroll = page.evaluate('window.scrollY')
            main_requests = []
            def watch_navigation(request):
                if request.is_navigation_request():
                    main_requests.append(request.url)
            page.on('request', watch_navigation)
            with self.app.app_context():
                db.session.get(SortDateMission,self.inbound_id).arrival_status='arrived'
                db.session.commit()
            arrival = row.locator('.ops-mobile-times')
            expect(arrival.locator('.ops-pretreat-arrived')).to_have_text('ARRIVED',timeout=12000)
            expect(arrival.locator('.ops-pretreat-arrived')).to_have_css('color','rgb(143, 255, 168)')
            expect(arrival.locator('strong')).to_have_text('A04')
            expect(arrival).to_contain_text('GT: 2:45')
            expect(arrival).not_to_contain_text('ETA')
            expect(notes).to_be_focused()
            expect(notes).to_have_value('Concurrent creation draft')
            expect(search).to_have_value('ups201 A04')
            self.assertAlmostEqual(page.evaluate('window.scrollY'),scroll,delta=1)
            self.assertEqual(main_requests, [])
            page.remove_listener('request', watch_navigation)
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
            expect(treatment.locator('select, [data-subzero-stamp]')).to_have_count(0)
            expect(treatment.locator('[data-pass="1"] .ops-pretreat-treatment')).to_contain_text('Type I')
            expect(treatment.locator('[data-pass="2"] .ops-pretreat-treatment')).to_contain_text('Type IV')
            expect(treatment.get_by_text('Wings + Tail',exact=True)).to_have_count(2)
            expect(inline.get_by_role('button',name='SAVE',exact=True)).to_have_count(0)
            expect(inline.get_by_text('PLANNING / TREATMENT RECORDS')).to_have_count(0)

            def autosave(action):
                with page.expect_response(lambda r:r.url.endswith('/operations/mutate'), timeout=10000) as pending:
                    action()
                self.assertEqual(pending.value.status,200,pending.value.text())
                expect(inline.locator('[data-ops-inline-feedback]')).to_be_hidden()
            def background(locator):
                return locator.evaluate("""e => {
                    const canvas = document.createElement('canvas');
                    const context = canvas.getContext('2d');
                    context.fillStyle = getComputedStyle(e).backgroundColor;
                    context.fillRect(0,0,1,1);
                    return [...context.getImageData(0,0,1,1).data].slice(0,3);
                }""")
            configure = inline.locator('button[value="toggle_configured"]')
            autosave(configure.click)
            expect(configure).to_have_attribute('aria-pressed','true')
            expect(row).to_have_attribute('data-status','configured')
            summary = row.locator('.ops-mobile-summary')
            green = background(summary)
            self.assertGreater(green[1],green[2])
            self.assertNotEqual(background(row),green)
            page.set_viewport_size({'width':1366,'height':900})
            desktop_arrival = row.locator('[data-ops-pretreat-location]')
            expect(desktop_arrival.locator('strong')).to_have_text('A04')
            expect(desktop_arrival.locator('.ops-pretreat-arrived')).to_have_css('color','rgb(143, 255, 168)')
            with self.app.app_context():
                db.session.get(SortDateMission,self.inbound_id).arrival_status='on_ground'
                db.session.commit()
            expect(desktop_arrival).to_contain_text('ETA 21:35',timeout=12000)
            with self.app.app_context():
                db.session.get(SortDateMission,self.inbound_id).arrival_status='unloaded'
                db.session.commit()
            expect(desktop_arrival.locator('.ops-pretreat-arrived')).to_have_text('ARRIVED',timeout=12000)
            self.assertEqual(background(row.locator('.ops-identity')),green)
            autosave(configure.click)
            expect(configure).to_have_attribute('aria-pressed','false')
            plan = row.get_by_role('checkbox',name='Pretreat planned for N441UP')
            autosave(plan.uncheck)
            expect(row).to_have_attribute('data-status','untreated')
            autosave(plan.check)
            expect(row).to_have_attribute('data-status','planned')
            yellow = background(row.locator('.ops-identity'))
            self.assertGreater(yellow[0],yellow[1])
            self.assertGreater(yellow[1],yellow[2])
            self.assertEqual(row.evaluate('e=>getComputedStyle(e).borderLeftWidth'),'1px')
            self.assertEqual(background(treatment.locator('[data-pass="1"]')),[58,42,36])
            self.assertEqual(background(treatment.locator('[data-pass="2"]')),[24,53,47])
            if notes.is_visible():
                notes_form.locator('summary').click()
            height = row.evaluate('e => e.offsetHeight')
            notes_form.locator('summary').click()
            expect(notes).to_be_visible()
            notes_form.locator('summary').click()
            self.assertEqual(row.evaluate('e => e.offsetHeight'),height)
            start = treatment.locator('[name="pass1_start"]')
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
                start.fill('21:00')
                start.press('Tab')
                page.wait_for_function('typeof window.releasePretreat === "function"')
                expect(row).to_have_attribute('aria-busy','true')
                notes_form.locator('summary').click()
                notes.fill('Queued during a treatment save')
                notes.press('Tab')
                page.evaluate('window.releasePretreat()')
            self.assertEqual(queued.value.status,200,queued.value.text())
            expect(row).not_to_have_attribute('aria-busy','true')
            expect(notes).to_have_value('Queued during a treatment save')
            expect(configure).to_have_attribute('aria-pressed','true')
            expect(row).to_have_attribute('data-status','configured')
            expect(row.locator('.ops-pretreat-state')).to_have_text('IN PROGRESS')
            page.set_viewport_size({'width':375,'height':900})
            if not inline.is_visible():
                row.locator('.ops-pretreat-summary').click()
            end = treatment.locator('[name="pass1_end"]')
            end.focus()
            end.fill('24')
            end.evaluate('field => field.blur()')
            expect(inline.locator('[data-ops-inline-feedback]')).to_contain_text('Enter a valid time')
            expect(end).to_have_value('24')
            for name,value in (('pass1_end','21:00'),('pass2_start','21:00'),('pass2_end','21:00')):
                field = treatment.locator('[name="'+name+'"]')
                def commit_time():
                    field.fill(value)
                    field.evaluate('field => field.blur()')
                autosave(commit_time)
            expect(row).to_have_attribute('data-status','fin')
            blue = background(summary)
            self.assertGreater(blue[2],blue[1])
            self.assertNotEqual(blue,green)
            expect(configure).to_have_attribute('aria-pressed','true')
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
            inline.get_by_label('Trucks for N441UP').click()
            expect(inline.get_by_role('link',name='Treatment history · N441UP')).to_have_count(0)
            inline.get_by_text('Recorded treatment',exact=True).click()
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
                self.assertEqual(saved.configured_at_utc,saved.pass1_started_at_utc)
                self.assertEqual(saved.pass1_started_at_utc,saved.pass2_ended_at_utc)
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

    def test_pretreat_participant_autosave_and_full_width_notes(self):
        for engine, width in (('chromium', 1440), ('webkit', 375)):
            with self.subTest(engine=engine):
                self.seed()
                with self.app.app_context():
                    audit_id_before = db.session.query(db.func.max(NeoSubZeroAuditEntry.id)).scalar() or 0
                browser = getattr(self.pw, engine).launch()
                self.addCleanup(browser.close)
                page = browser.new_page(viewport={'width':width, 'height':900}, service_workers='block')
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                self.login(page)
                self.ready(page, f'/neosubzero/pretreat?mission={self.flow_id}')
                row = page.locator(f'[data-ops-row][data-mission-id="{self.flow_id}"]')
                inline = row.locator('[data-ops-inline]')
                feedback = inline.locator('[data-ops-inline-feedback]')
                pass1, pass2 = (row.locator(f'[data-ops-participant-pass="{number}"]') for number in (1, 2))

                def save(edit, status=200):
                    with page.expect_response(lambda r: r.url.endswith('/operations/mutate')) as pending:
                        edit()
                    self.assertEqual(pending.value.status, status, pending.value.text())
                    expect(row).not_to_have_attribute('aria-busy', 'true')
                    if status == 200:
                        expect(feedback).to_be_hidden()

                def edit(field, value):
                    def blur():
                        field.fill(value)
                        field.blur()
                    return blur

                def add(section, truck):
                    section.get_by_role('button', name='+ ADD TRUCK').click()
                    save(lambda: section.locator('[data-ops-new-participant]:visible select[name="truck_number"]').select_option(truck))
                    return section.locator('.ops-pretreat-participant:visible').filter(
                        has=page.locator(f'select[name="truck_number"] option[value="{truck}"][selected]'))

                inline.get_by_label('Trucks for N777UP').click()
                expect(pass1.get_by_role('heading')).to_have_text('PASS 1 — TYPE I')
                expect(pass2.get_by_role('heading')).to_have_text('PASS 2 — TYPE IV')
                first = add(pass1, '12')
                expect(first.get_by_label('Gallons')).to_be_disabled()
                save(lambda: first.get_by_label('Driver').select_option(str(self.people[0])))
                save(edit(first.get_by_label('Concentration percent'), '43'))
                second = add(pass1, 'UCC-831')
                iv_first = add(pass2, '12')
                expect(iv_first.get_by_label('Concentration percent')).to_have_value('100')
                expect(iv_first.get_by_label('Concentration percent')).to_be_disabled()

                for field, value in (('pass1_start', '21:00'), ('pass1_end', '21:03')):
                    save(edit(row.locator(f'[name="{field}"]'), value))
                with self.app.app_context():
                    self.assertEqual(NeoSubZeroSprayRecord.query.filter(NeoSubZeroSprayRecord.official_at_utc.is_not(None)).count(), 0)
                save(first.get_by_role('button', name='Record actual').click)
                expect(first.get_by_label('Concentration percent')).to_have_value('43')
                save(edit(first.get_by_label('Gallons'), '8.25'))
                for field, value in (('pass2_start', '21:04'), ('pass2_end', '21:07')):
                    save(edit(row.locator(f'[name="{field}"]'), value))
                expect(row).to_have_attribute('data-status', 'fin')
                expect(second.get_by_role('button', name='Record actual')).to_be_visible()
                iv_second = add(pass2, 'UCC-831')
                expect(iv_second.get_by_label('Driver')).to_have_value('')
                save(edit(iv_second.get_by_label('Gallons'), '3.25'))
                save(lambda: iv_second.get_by_label('Flyer').select_option(str(self.people[2])))

                save(edit(first.get_by_label('Gallons'), '9.50'), status=409)
                expect(first.get_by_label('Correction reason')).to_be_visible()
                first.get_by_label('Correction reason').fill('Read calibrated meter')
                save(first.get_by_role('button', name='Apply').click)
                expect(first.get_by_label('Gallons')).to_have_value('9.5')
                expect(first.get_by_label('Correction reason')).to_be_hidden()

                notes_summary = inline.get_by_label('Notes for N777UP')
                notes_summary.click()
                notes = inline.locator('textarea[name="notes"]')
                expect(notes).to_be_visible()
                base = row if width > 900 else inline
                self.assertGreater(notes.bounding_box()['width'] / base.bounding_box()['width'], .90)
                save(edit(notes, 'Synthetic PRETREAT notes'))
                notes_summary.click()
                expect(notes).to_be_hidden()
                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth-innerWidth'), 1)
                expect(pass1.locator('.ops-pretreat-participant:visible')).to_have_count(2)
                expect(pass2.locator('.ops-pretreat-participant:visible')).to_have_count(2)
                self.assertFalse(errors, errors)

                with self.app.app_context():
                    state = NeoSubZeroPretreatState.query.filter_by(tail_number='N777UP').one()
                    self.assertEqual(state.notes, 'Synthetic PRETREAT notes')
                    record = NeoSubZeroSprayRecord.query.filter_by(pretreat_state_id=state.id, pass_number=1, truck_number_snapshot='12').one()
                    self.assertEqual(record.reason_for_application, 'Pretreat')
                    self.assertEqual(record.driver_person_id, self.people[0])
                    audit = NeoSubZeroAuditEntry.query.filter_by(
                        spray_record_id=record.id, action='pretreat_participation_correction'
                    ).filter(NeoSubZeroAuditEntry.id > audit_id_before).one()
                    self.assertEqual((audit.reason, audit.before_values['gallons'], audit.after_values['gallons']),
                                     ('Read calibrated meter', '8.25', '9.50'))
                    from app.services.neosubzero_reporting import actual_passes
                    report = actual_passes(state.operation, activity='pretreat', tail='N777UP')
                    self.assertEqual([[truck.truck_number_snapshot for truck in item['records']] for item in report], [['12'], ['UCC-831']])
                    self.assertEqual(report[0]['concentration'], 43)
                    self.assertEqual(report[1]['concentration'], 100)
                    self.assertEqual(NeoSubZeroUccTruckAssignment.query.filter_by(sort_date_operation_id=self.operation_id).count(), 0)
                response = page.goto(self.origin + '/neosubzero/deice-log?activity=pretreat&tail=N777UP')
                self.assertEqual(response.status, 200)
                expect(page.locator('[data-report-pass]')).to_have_count(2)
                expect(page.locator('[data-report-pass]').first.locator('.sz-contribution')).to_contain_text('9.50 · 43%')
                browser.close()
