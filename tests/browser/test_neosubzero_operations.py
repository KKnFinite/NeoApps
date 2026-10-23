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
        with page.expect_response(lambda r:r.url.endswith('/operations/preferences') and r.request.method=='POST') as pending:
            page.locator('[data-ops-ramp]').select_option(value)
        self.assertEqual(pending.value.status,200,pending.value.text())
        self.assertTrue(pending.value.json()['ok'])
        expect(page.locator('[data-ops-ramp]')).to_be_enabled()

    @classmethod
    def setUpClass(cls):
        fixtures.UccBrowserTest.setUpClass.__func__(cls)
        with cls.app.app_context():
            fixture=fixtures.NeoSubZeroUccTest();fixture.operation=db.session.get(SortDateOperation,cls.operation_id)
            mission=fixture._departure('UPS7777','N777UP','DEN',4)
            mission.planned_datetime_utc=datetime(2026,10,23,5);mission.planned_datetime_local=datetime(2026,10,23,0)
            db.session.add(SortDateParkingAssignment(sort_date_operation_id=cls.operation_id,tail_number=mission.assigned_tail_number,ramp_code='D',position_code='D04'))
            cls.flow_id=mission.id;db.session.commit()

    def seed(self):
        with self.app.app_context():
            NeoSubZeroUserPreference.query.delete();NeoSubZeroPretreatState.query.delete();SortDateTailState.query.delete()
            NeoSubZeroDepartureDeiceEvent.query.filter_by(sort_date_mission_id=self.flow_id).delete()
            for index,mid in enumerate(self.missions):
                mission=db.session.get(SortDateMission,mid);mission.assigned_tail_number=f'N44{index}UP';mission.eta_datetime_utc=datetime(2026,10,23,5,index*5)
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
                for width in (320,375,430,768,1024,1440,1920,2560):
                    page.set_viewport_size({'width':width,'height':1000})
                    page.wait_for_function('(w)=>document.documentElement.clientWidth===w',arg=width)
                    for layout in ('list','visual'):
                        self.layout(page,layout)
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
                        # Take one DOM snapshot: a live board replacement must not mix
                        # geometry from different revisions or stale nth locators.
                        visible=page.locator('[data-ops-board]').evaluate('board=>[...board.querySelectorAll("[data-ops-row]")].filter(row=>row.getBoundingClientRect().height>0).map(row=>({right:row.getBoundingClientRect().right,passes:[1,2].map(n=>{const p=row.querySelector(`[data-ops-primary-pass="${n}"]`).getBoundingClientRect();return {width:p.width,height:p.height};})}))')
                        self.assertTrue(visible)
                        for row in visible:
                            self.assertLessEqual(row['right'],width+1)
                            for bounds in row['passes']:
                                self.assertGreater(bounds['width'],0);self.assertGreater(bounds['height'],0)
                        measurements.append(dict(engine=engine,mode=mode,width=width,layout=layout,overflow=page.evaluate('document.documentElement.scrollWidth-innerWidth')))
                        page.screenshot(path=str(evidence/f'{engine}-{mode}-{layout}-{width}.png'))
            # A shared ramp survives mode changes and reload; mode/layout are user preferences.
            page.set_viewport_size({'width':1440,'height':1000});self.layout(page,'list');expect(root).to_have_attribute('data-layout','list')
            page.locator('[data-ops-mode="outbound"]').click();expect(root).to_have_attribute('data-mode','outbound');expect(root).to_have_attribute('data-ready','true')
            page.locator('[data-ops-context] summary').click()
            page.locator('[data-ops-context] [name="notes"]').fill('Context draft survives modes')
            page.locator('[data-ops-mode="pretreat"]').click();expect(root).to_have_attribute('data-mode','pretreat');expect(root).to_have_attribute('data-ready','true')
            page.locator('[data-ops-mode="outbound"]').click();expect(root).to_have_attribute('data-mode','outbound');expect(root).to_have_attribute('data-ready','true')
            page.locator('[data-ops-context] summary').click()
            expect(page.locator('[data-ops-context] [name="notes"]')).to_have_value('Context draft survives modes')
            self.submit(page,page.locator('[data-ops-context] button[type="submit"]'))
            expect(page.locator('[data-ops-feedback]')).to_contain_text('SAVED')
            page.locator('[data-ops-context] summary').click()
            self.ramp(page,'Bravo');expect(root).to_have_attribute('data-ramp','Bravo')
            other=browser.new_context(viewport={'width':375,'height':900});other_page=other.new_page()
            self.login(other_page,'drawer-watcher');self.ready(other_page,'/neosubzero/operations')
            expect(other_page.locator('[data-ops-workspace]')).to_have_attribute('data-mode','outbound')
            expect(other_page.locator('[data-ops-workspace]')).to_have_attribute('data-ramp','All');other.close()
            page.locator('[data-ops-mode="outbound"]').click();expect(root).to_have_attribute('data-mode','outbound');expect(root).to_have_attribute('data-ramp','Bravo')
            page.reload();expect(root).to_have_attribute('data-ready','true');expect(root).to_have_attribute('data-ramp','Bravo')
            self.ramp(page,'All');expect(root).to_have_attribute('data-ramp','All')
            # Every required color covers the card, not just an accent.
            for status in ('planned','configured','deicing','fin','negative','cleared'):
                card=page.locator('[data-ops-row][data-status="'+status+'"]').first
                self.assertNotEqual(card.evaluate('e=>getComputedStyle(e).backgroundColor'),'rgba(0, 0, 0, 0)')
            self.assertEqual(page.locator('[data-ops-row]').last.get_attribute('data-status'),'cleared')
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
            self.submit(page,pane.locator('button[value="clear"]'));expect(page.locator(f'[data-ops-row][data-mission-id="{self.flow_id}"]')).to_have_attribute('data-status','cleared')
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
            expect(page.locator(f'[data-ops-row][data-mission-id="{self.missions[0]}"]')).to_have_attribute('data-status','review',timeout=10000)
            expect(pane.locator('[name="pass1_start"]')).to_have_value('21:45')
            pane.locator('[data-subzero-deice-form] button[type="submit"]').click();expect(page.locator('[data-ops-feedback]')).to_contain_text('identity changed')
            expect(pane.locator('[name="pass1_start"]')).to_have_value('21:45')
            # Actual phone Pretreat editing, failure recovery and independent completion.
            pane.locator('[data-ops-close]').click();page.set_viewport_size({'width':375,'height':900})
            page.locator('[data-ops-mode="pretreat"]').click();expect(root).to_have_attribute('data-mode','pretreat')
            expect(root).to_have_attribute('data-ready','true')
            page.locator(f'[data-ops-row][data-mission-id="{self.missions[1]}"] a').click()
            treatment=pane.locator('.neosubzero-treatment-form')
            treatment.locator('[name="notes"]').fill('Concurrent creation draft')
            with self.app.app_context():
                db.session.add(NeoSubZeroPretreatState(sort_date_operation_id=self.operation_id,tail_number='N441UP',pretreat_planned=True));db.session.commit()
            expect(page.locator(f'[data-ops-row][data-mission-id="{self.missions[1]}"]')).to_have_attribute('data-status','planned',timeout=10000)
            expect(treatment.locator('[name="notes"]')).to_have_value('Concurrent creation draft')
            expect(treatment.locator('[name="expected_version"]')).to_have_value('')
            page.reload();self.ready(page,f'/neosubzero/operations?mode=pretreat&mission={self.missions[1]}')
            expect(treatment.locator('[name="notes"]')).to_have_value('Concurrent creation draft')
            with page.expect_response(lambda r:r.url.endswith('/operations/mutate')) as stale:
                treatment.locator('button[type="submit"]').click()
            self.assertEqual(stale.value.status,409)
            expect(pane.locator('[data-ops-detail-feedback]')).to_contain_text('Pretreat changed')
            expect(treatment.locator('[name="notes"]')).to_have_value('Concurrent creation draft')
            page.once('dialog',lambda dialog:dialog.dismiss());pane.locator('[data-ops-reload]').click()
            expect(treatment.locator('[name="notes"]')).to_have_value('Concurrent creation draft')
            page.once('dialog',lambda dialog:dialog.accept());pane.locator('[data-ops-reload]').click()
            expect(treatment.locator('[name="notes"]')).to_have_value('')
            treatment=pane.locator('.neosubzero-treatment-form')
            treatment.locator('[name="pass1_surface_area"]').select_option('wings_only')
            treatment.locator('[name="pass2_surface_area"]').select_option('entire_aircraft')
            for name,value in (('pass1_start','21:00'),('pass1_end','21:02'),('pass2_start','21:04'),('pass2_end','21:08')):treatment.locator('[name="'+name+'"]').fill(value)
            treatment.locator('[name="notes"]').fill('Mobile pretreat independent notes')
            fail_save=lambda route:route.abort('connectionrefused')
            page.route('**/operations/mutate',fail_save)
            treatment.locator('button[type="submit"]').click()
            expect(pane.locator('[data-ops-detail-feedback]')).to_contain_text('draft is kept')
            expect(pane.locator('[data-ops-detail-feedback]')).to_be_visible()
            expect(treatment.locator('[name="notes"]')).to_have_value('Mobile pretreat independent notes')
            expect(treatment.locator('button[type="submit"]')).to_be_enabled()
            page.unroute('**/operations/mutate',fail_save)
            self.submit(page,treatment.locator('button[type="submit"]'))
            expect(page.locator(f'[data-ops-row][data-mission-id="{self.missions[1]}"] .ops-status')).to_contain_text('PRETREATED')
            page.screenshot(path=str(evidence/f'{engine}-mobile-pretreat-detail.png'))
            self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth-innerWidth'),1)
            self.assertLessEqual(pane.evaluate('e=>e.scrollWidth-e.clientWidth'),1)
            pane.locator('[data-ops-close]').click();page.locator('[data-ops-mode="outbound"]').click();expect(root).to_have_attribute('data-mode','outbound')
            expect(root).to_have_attribute('data-ready','true')
            expect(page.locator(f'[data-ops-row][data-mission-id="{self.missions[1]}"] .ops-pretreated')).to_be_visible()
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
            expect(page.locator('[data-ops-context] [name="notes"]')).to_have_value('')
            type(self).operation_id=old_operation_id
            self.assertFalse(errors,errors)
            context.close();browser.close()
        (evidence/'measurements.json').write_text(json.dumps(measurements,indent=2))
