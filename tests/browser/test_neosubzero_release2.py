"""Real authorized Release 2 forms, mobile Driver and independent browser updates."""
import unittest
from datetime import datetime
from pathlib import Path
from playwright.sync_api import expect
from app.extensions import db
from app.models import (Gateway,SortDateMission,SortDateOperation,SortDateParkingAssignment,
    NeoSubZeroSprayRecord,NeoSubZeroUccTruckAssignment,NeoSubZeroUccAssignment,StaffingPerson,User)
from app.services.neosubzero_ucc import set_neosubzero_ucc_assignment
from app.services.access_control import backfill_default_gateway_node_roles
from app.services.password_policy import set_user_password
from tests.browser import test_neosubzero_ucc as fixtures


class Release2BrowserTest(unittest.TestCase):
    login=fixtures.UccBrowserTest.login
    tearDownClass=classmethod(fixtures.UccBrowserTest.tearDownClass.__func__)
    @classmethod
    def setUpClass(cls):
        fixtures.UccBrowserTest.setUpClass.__func__(cls)
        with cls.app.app_context():
            user=User(username='r2-driver',email='r2-driver@example.test',employee_id='UCC-B1',role='operator',email_verified_at=datetime.now(),
                first_name='Documented',last_name='Driver')
            set_user_password(user,'BrowserFixture123!');db.session.add(user);db.session.flush()
            backfill_default_gateway_node_roles(user,role='operator');db.session.commit()

    def ready(self,page,path):
        fixtures.UccBrowserTest.ready(self,page,path)
        if '/operations' in path:expect(page.locator('[data-ops-workspace]')).to_have_attribute('data-ready','true')

    def submit(self,page,button):
        previous=button.element_handle()
        with page.expect_response(lambda r:r.url.endswith('/operations/mutate') and r.request.method=='POST') as pending:button.click()
        result=pending.value
        self.assertEqual(result.status,200,result.text());self.assertTrue(result.json()['ok'],result.json())
        # A successful mutation starts a GET/reconciliation. Wait for that actual
        # replacement, not just the earlier success message preceding the GET.
        page.wait_for_function('element=>!element.isConnected',arg=previous)
        expect(page.locator('[data-ops-detail-feedback]')).not_to_contain_text('SAVING')

    def test_participation_team_handoff_clear_corrections_and_driver_mobile(self):
        evidence=Path('instance/browser-evidence/subzero-release2').resolve();evidence.mkdir(parents=True,exist_ok=True)
        for index,engine in enumerate(('chromium','webkit')):
            with self.app.app_context():
                f=fixtures.NeoSubZeroUccTest();f.operation=db.session.get(SortDateOperation,self.operation_id)
                mission=f._departure('UPS-R2-'+engine,'N-R2-'+engine,'DEN',4);mid=mission.id
                db.session.add(SortDateParkingAssignment(sort_date_operation_id=self.operation_id,tail_number=mission.assigned_tail_number,ramp_code='D',position_code='D03'))
                for existing in NeoSubZeroUccTruckAssignment.query.filter_by(sort_date_operation_id=self.operation_id,ramp='Delta').all():db.session.delete(existing)
                slot=NeoSubZeroUccAssignment.query.filter_by(sort_date_operation_id=self.operation_id,ramp='Delta',position_number=1,team_role='driver').one_or_none()
                set_neosubzero_ucc_assignment(f.operation,'Delta',1,'driver',db.session.get(StaffingPerson,self.people[1]),assignment=slot)
                db.session.add(NeoSubZeroUccTruckAssignment(sort_date_operation_id=self.operation_id,ramp='Delta',position_number=1,truck_number='R2-T12'))
                db.session.commit()
            browser=getattr(self.pw,engine).launch()
            context=browser.new_context(viewport={'width':1440,'height':1000},service_workers='block')
            page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            self.login(page);self.ready(page,f'/neosubzero/operations?mode=outbound&mission={mid}')
            pane=page.locator('[data-ops-detail]')
            self.submit(page,pane.locator('button[value="initial_contact"]'))
            treatment=pane.locator('[data-subzero-deice-form]')
            treatment.locator('[name="treatment_plan"]').select_option('type_i_type_iv')
            treatment.locator('[name="pass1_surface_area"]').select_option('wings_tail')
            treatment.locator('[name="pass2_surface_area"]').select_option('entire_aircraft')
            treatment.locator('[name="pass1_start"]').fill('22:00')
            self.submit(page,treatment.locator('button[type="submit"]'))
            expect(page.locator(f'[data-ops-row][data-mission-id="{mid}"]')).to_have_attribute('data-status','deicing')
            pass1=pane.locator('.neosubzero-spray-pass').filter(has=page.locator('header strong',has_text='PASS 1'))
            pass1.locator('summary',has_text='ADD TRUCK').click()
            add=pass1.locator('form').filter(has=page.locator('input[name="action"][value="add_truck"]'))
            add.locator('[name="truck_number"]').fill('R2-T30')
            add.locator('[name="driver_person_id"]').select_option(str(self.people[2]));add.locator('[name="crew_confirmed"]').check()
            self.submit(page,add.locator('button'))
            expect(pass1).to_contain_text('R2-T30')
            treatment.locator('[name="pass1_end"]').fill('22:05')
            self.submit(page,treatment.locator('button[type="submit"]'))
            expect(pass1).to_contain_text('OFFICIAL')
            with self.app.app_context():
                records=NeoSubZeroSprayRecord.query.filter_by(sort_date_mission_id=mid,pass_number=1).all()
                self.assertEqual(len(records),2);self.assertTrue(all(r.gallons is None for r in records))
                r1=next(r for r in records if r.truck_number_snapshot=='R2-T12');rid=r1.id;original_driver=r1.driver_name_snapshot
            team=pane.locator('.ops-team-controls');team.locator(':scope > summary').click()
            truck_form=team.locator('form').filter(has=page.locator('button[value="change_truck"]')).first
            truck_form.locator('[name="truck_number"]').fill('R2-T18')
            self.submit(page,truck_form.locator('button'))
            # Live reconciliation preserves expanded sections. Do not toggle an
            # already-open team closed before selecting its personnel control.
            if not team.evaluate('element=>element.open'):
                team.locator(':scope > summary').click()
            team.locator('summary',has_text='CHANGE DEICERS').last.click()
            crew=team.locator('form').filter(has=page.locator('button[value="change_deicers"]')).first
            crew.locator('[name="driver_person_id"]').select_option(str(self.people[2]))
            self.submit(page,crew.locator('button'))
            expect(pass1).to_contain_text('R2-T12');expect(pass1).to_contain_text(original_driver)
            treatment.locator('[name="pass2_start"]').fill('22:06');treatment.locator('[name="pass2_end"]').fill('22:10')
            self.submit(page,treatment.locator('button[type="submit"]'))
            expect(page.locator(f'[data-ops-row][data-mission-id="{mid}"]')).to_have_attribute('data-status','fin')
            pass2=pane.locator('.neosubzero-spray-pass').filter(has=page.locator('header strong',has_text='PASS 2'))
            expect(pass2).to_contain_text('R2-T18')
            self.submit(page,pane.locator('button[value="clear"]'))
            expect(page.locator(f'[data-ops-row][data-mission-id="{mid}"]')).to_have_attribute('data-status','cleared')
            expect(pane.locator('.ops-readback')).to_contain_text('2206')
            pass2.locator('summary',has_text='ADD TRUCK').click()
            correction=pass2.locator('form').filter(has=page.locator('[name="action"][value="add_truck"]'))
            correction.locator('[name="truck_number"]').fill('R2-T40')
            correction.locator('[name="crew_confirmed"]').check()
            correction.locator('[name="driver_person_id"]').select_option(str(self.people[2]))
            correction.locator('[name="correction_reason"]').fill('Verified second truck contribution after clearance')
            self.submit(page,correction.locator('button'))
            expect(pass2).to_contain_text('R2-T40')
            expect(page.locator(f'[data-ops-row][data-mission-id="{mid}"]')).to_have_attribute('data-status','cleared')
            # Original Driver has no current Delta position, but retains their own snapshot.
            driver_context=browser.new_context(viewport={'width':375,'height':812},service_workers='block')
            driver=driver_context.new_page();self.login(driver,'r2-driver');self.ready(driver,'/neosubzero/deicer-mobile')
            own=driver.locator(f'[data-own-participant="{rid}"]')
            expect(own).to_be_visible();self.assertEqual(driver.locator('[name="truck_number"],button[value="clear"]').count(),0)
            own.locator('[name="gallons"]').fill('17.5')
            with driver.expect_response(lambda r:r.url.endswith('/spray-gallons') and r.request.method=='POST') as saved:own.locator('button').click()
            self.assertEqual(saved.value.status,200)
            expect(driver.locator('[data-evidence-feedback]')).to_contain_text('SAVED')
            expect(own.locator('[name="gallons"]')).to_have_value('17.50')
            expect(pane.locator(f'[data-participant-id="{rid}"] [name="gallons"]')).to_have_value('17.5',timeout=10000)
            # A concurrent measurement cannot overwrite the Driver's draft.
            own.locator('summary').click()
            own.locator('[name="gallons"]').fill('18')
            with self.app.app_context():
                from app.services.neosubzero_spray import set_neosubzero_spray_gallons
                record=db.session.get(NeoSubZeroSprayRecord,rid)
                set_neosubzero_spray_gallons(db.session.get(SortDateOperation,self.operation_id),record.mission,record.departure_deice_event,
                    record.pass_number,record.position_number,'18.5',record=record,
                    user_id=User.query.filter_by(username='drawer-admin').one().id,correction_reason='Concurrent measured correction fixture')
                db.session.commit()
            with driver.expect_response(lambda r:r.url.endswith('/spray-gallons') and r.request.method=='POST') as rejected:own.locator('button').click()
            self.assertEqual(rejected.value.status,409)
            expect(driver.locator('[data-evidence-feedback]')).to_contain_text('changed')
            expect(own.locator('[name="gallons"]')).to_have_value('18');expect(own.locator('button')).to_be_enabled()
            driver.reload();own.locator('summary').click();expect(own.locator('[name="gallons"]')).to_have_value('18')
            driver.locator('.sz-recovery > summary').click()
            driver.once('dialog',lambda dialog:dialog.dismiss());driver.locator('[data-evidence-reload]').click()
            expect(own.locator('[name="gallons"]')).to_have_value('18')
            driver.once('dialog',lambda dialog:dialog.accept());driver.locator('[data-evidence-reload]').click()
            expect(own.locator('[name="gallons"]')).to_have_value('18.50')
            expect(pane.locator(f'[data-participant-id="{rid}"] [name="gallons"]')).to_have_value('18.5',timeout=10000)
            for width,height in ((320,900),(375,900),(390,900),(430,900),(768,1024),(1366,768),(1920,900),(1920,1080),(3840,2160)):
                page.set_viewport_size({'width':width,'height':height})
                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth-innerWidth'),1,dict(engine=engine,width=width))
                self.assertLessEqual(pane.evaluate('e=>e.scrollWidth-e.clientWidth'),1,dict(engine=engine,width=width))
                page.screenshot(path=str(evidence/f'{engine}-operations-{width}.png'))
            driver.screenshot(path=str(evidence/f'{engine}-driver-375.png'),full_page=True)
            self.ready(page,'/neosubzero/deice-log')
            expect(page.locator('body')).to_contain_text('R2-T12')
            expect(page.locator('body')).to_contain_text('18.5')
            page.set_viewport_size({'width':375,'height':900})
            page.locator('details').filter(has=page.locator('form [name="record_id"][value="'+str(rid)+'"]')).first.locator(':scope > summary').click()
            self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth-innerWidth'),1)
            page.screenshot(path=str(evidence/f'{engine}-log.png'),full_page=True)
            self.assertFalse(errors,errors)
            with self.app.app_context():
                r1=db.session.get(NeoSubZeroSprayRecord,rid)
                self.assertEqual(r1.driver_name_snapshot,original_driver)
                self.assertEqual(r1.truck_number_snapshot,'R2-T12')
            driver_context.close();context.close();browser.close()
