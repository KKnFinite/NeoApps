"""Authenticated Driver/report workflows, real save failures and responsive layouts."""
import json
import unittest
from datetime import datetime
from pathlib import Path
from playwright.sync_api import expect
from tests.browser import test_neosubzero_ucc as fixtures
from app.extensions import db
from app.models import (User, SortDateOperation, Gateway, StaffingPerson, NeoSubZeroUccTruckAssignment,
                        NeoSubZeroUccAssignment, NeoSubZeroSprayRecord, SortDateParkingAssignment)
from app.services.access_control import backfill_default_gateway_node_roles
from app.services.password_policy import set_user_password
from app.services.neosubzero_ucc import set_neosubzero_ucc_assignment
from app.services.neosubzero_departure_deice import mutate_departure_deice
from app.services.neosubzero_spray import set_neosubzero_spray_gallons


class Release3BrowserTest(unittest.TestCase):
    login=fixtures.UccBrowserTest.login
    ready=fixtures.UccBrowserTest.ready
    tearDownClass=classmethod(fixtures.UccBrowserTest.tearDownClass.__func__)

    @classmethod
    def setUpClass(cls):
        fixtures.UccBrowserTest.setUpClass.__func__(cls)
        with cls.app.app_context():
            user=User(username='r3-driver',email='r3-driver@example.test',employee_id='UCC-B1',role='operator',email_verified_at=datetime.now())
            set_user_password(user,'BrowserFixture123!');db.session.add(user);db.session.flush()
            backfill_default_gateway_node_roles(user,role='operator');db.session.commit()

    def test_driver_reporting_live_drafts_copy_and_responsive(self):
        evidence=Path('instance/browser-evidence/subzero-release3').resolve();evidence.mkdir(parents=True,exist_ok=True)
        measurements=[]
        for engine in ('chromium','webkit'):
            with self.app.app_context():
                op=db.session.get(SortDateOperation,self.operation_id)
                f=fixtures.NeoSubZeroUccTest();f.operation=op
                mission=f._departure('UPS-R3-'+engine,'N-R3-'+engine,'DEN',4);mid=mission.id
                db.session.add(SortDateParkingAssignment(sort_date_operation_id=op.id,tail_number=mission.assigned_tail_number,ramp_code='D',position_code='D03'))
                for slot in NeoSubZeroUccTruckAssignment.query.filter_by(sort_date_operation_id=op.id,ramp='Delta').all():db.session.delete(slot)
                slot=NeoSubZeroUccAssignment.query.filter_by(sort_date_operation_id=op.id,ramp='Delta',position_number=1,team_role='driver').one_or_none()
                set_neosubzero_ucc_assignment(op,'Delta',1,'driver',db.session.get(StaffingPerson,self.people[1]),assignment=slot)
                db.session.add(NeoSubZeroUccTruckAssignment(sort_date_operation_id=op.id,ramp='Delta',position_number=1,truck_number='R3-T12'))
                event=mutate_departure_deice(op,mission,'initial_contact',{})
                values=dict(treatment_plan='type_i_type_iv',pass1_surface_area='wings_tail',pass2_surface_area='entire_aircraft',pass1_start='2200')
                mutate_departure_deice(op,mission,'save_treatment',values,event=event)
                values.update(pass1_end='2205',pass2_start='2206',pass2_end='2209')
                mutate_departure_deice(op,mission,'save_treatment',values,event=event);db.session.commit()
                record=NeoSubZeroSprayRecord.query.filter_by(departure_deice_event_id=event.id,pass_number=1).one();rid=record.id
            browser=getattr(self.pw,engine).launch()
            context=browser.new_context(viewport={'width':375,'height':812},service_workers='block')
            page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            self.login(page,'r3-driver');self.ready(page,'/neosubzero/deicer-mobile')
            expect(page.locator('.neosubzero-head')).to_contain_text('TRUCK R3-T12')
            card=page.locator(f'[data-driver-aircraft="{mid}"]')
            expect(card).to_have_class('sz-aircraft state-fin');expect(card).to_contain_text('TYPE IV')
            expect(card).to_contain_text('ENTIRE AIRCRAFT');expect(card).to_contain_text('WINGS + TAIL')
            own=page.locator(f'[data-own-participant="{rid}"]')
            own.locator('[name="gallons"]').fill('17.5')
            page.route('**/neosubzero/spray-gallons',lambda route:route.abort('failed'))
            own.get_by_role('button',name='SAVE GALLONS').click()
            expect(page.locator('[data-evidence-feedback]')).not_to_contain_text('SAVING')
            expect(own.locator('[name="gallons"]')).to_have_value('17.5');expect(own.locator('button')).to_be_enabled()
            page.unroute('**/neosubzero/spray-gallons')
            with page.expect_response(lambda r:r.url.endswith('/spray-gallons') and r.request.method=='POST') as saved:own.locator('button').click()
            self.assertEqual(saved.value.status,200)
            expect(page.locator('[data-evidence-feedback]')).to_contain_text('SAVED')
            # Editing a saved contribution is explicit and touch-friendly.
            own.locator('summary').click();own.locator('[name="gallons"]').fill('18')
            with self.app.app_context():
                r=db.session.get(NeoSubZeroSprayRecord,rid)
                set_neosubzero_spray_gallons(r.operation,r.mission,r.departure_deice_event,1,r.position_number,'18.5',record=r,correction_reason='Concurrent fixture')
                db.session.commit()
            with page.expect_response(lambda r:r.url.endswith('/spray-gallons') and r.request.method=='POST') as rejected:own.locator('button').click()
            self.assertEqual(rejected.value.status,409);expect(own.locator('[name="gallons"]')).to_have_value('18')
            expect(page.locator('[data-evidence-feedback]')).to_contain_text('changed')
            page.reload();own.locator('summary').click();expect(own.locator('[name="gallons"]')).to_have_value('18')
            page.locator('.sz-recovery > summary').click();page.once('dialog',lambda d:d.dismiss());page.locator('[data-evidence-reload]').click()
            expect(own.locator('[name="gallons"]')).to_have_value('18')
            page.once('dialog',lambda d:d.accept());page.locator('[data-evidence-reload]').click()
            expect(page.locator('[data-evidence-feedback]')).to_be_hidden()
            for width in (320,375,390,430,768,1024,1440,1920):
                page.set_viewport_size({'width':width,'height':900})
                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth-innerWidth'),1,dict(engine=engine,screen='driver',width=width))
                measurements.append(dict(engine=engine,screen='driver',width=width,card_height=card.bounding_box()['height']))
                page.screenshot(path=str(evidence/f'{engine}-driver-{width}.png'),full_page=True)
            # Separate authenticated Coordinator context; Drivers cannot mutate plans.
            admin=browser.new_context(viewport={'width':1440,'height':1000},service_workers='block')
            log=admin.new_page();self.login(log);self.ready(log,'/neosubzero/deice-log')
            log.locator('[name="tail"]').fill('N-R3-'+engine);log.get_by_role('button',name='VIEW',exact=True).click()
            expect(log.locator('[data-report-pass]')).to_have_count(2)
            awaitable=log.locator('#sz-report-copy').text_content()
            if engine=='chromium':
                admin.grant_permissions(['clipboard-read','clipboard-write'])
                log.get_by_role('button',name='COPY REPORT',exact=True).click()
                expect(log.locator('[data-spray-copy-feedback]')).to_contain_text('COPIED')
                self.assertEqual(log.evaluate('navigator.clipboard.readText()').replace('\r\n','\n'),awaitable)
            else:
                log.evaluate("Object.defineProperty(navigator,'clipboard',{value:undefined,configurable:true});document.execCommand=()=>false")
                log.get_by_role('button',name='COPY REPORT',exact=True).click()
                expect(log.get_by_label('Report copy fallback')).to_have_value(awaitable)
            # Copy a single pass without leaking the other pass.
            log.evaluate("window.copied='';Object.defineProperty(navigator,'clipboard',{value:{writeText:async t=>{window.copied=t}},configurable:true})")
            log.get_by_role('button',name='COPY PASS',exact=True).first.click()
            expect(log.locator('[data-spray-copy-feedback]')).to_contain_text('COPIED')
            self.assertEqual(log.evaluate('window.copied'),log.locator('#sz-pass-1').text_content())
            for width in (320,375,390,430,768,1024,1440,1920):
                log.set_viewport_size({'width':width,'height':900})
                self.assertLessEqual(log.evaluate('document.documentElement.scrollWidth-innerWidth'),1,dict(engine=engine,screen='log',width=width))
                log.screenshot(path=str(evidence/f'{engine}-log-{width}.png'),full_page=True)
            # Normal polling updates the report after a Driver correction, including
            # when COPY's button still has focus. No manual page reload.
            with self.app.app_context():
                r=db.session.get(NeoSubZeroSprayRecord,rid)
                set_neosubzero_spray_gallons(r.operation,r.mission,r.departure_deice_event,1,r.position_number,'19.25',record=r,correction_reason='Live fixture')
                db.session.commit()
            expect(log.locator('[data-report-pass]').first).to_contain_text('19.25',timeout=10000)
            self.assertFalse(errors,errors)
            admin.close();context.close();browser.close()
        (evidence/'measurements.json').write_text(json.dumps(measurements,indent=2),encoding='utf-8')
