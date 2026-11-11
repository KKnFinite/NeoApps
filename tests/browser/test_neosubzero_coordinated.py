"""Focused real controls and one shared physical projection at phone/TV widths."""
import json
import re
import unittest
from pathlib import Path

from playwright.sync_api import expect
from app.extensions import db
from app.models import (SortDateOperation, SortDateParkingAssignment, StaffingUnit,
                        NeoSubZeroDepartureDeiceEvent, NeoSubZeroAuditEntry, StaffingPersonQualification)
from tests.browser import test_neosubzero_ucc as fixtures

EVIDENCE=Path('instance/ducc-coordinated/browser')


class CoordinatedDuccBrowserTest(unittest.TestCase):
    def exercise(self, engine):
        case=fixtures.UccBrowserTest();case.setUpClass();browser=None;measurements=[]
        EVIDENCE.mkdir(parents=True,exist_ok=True)
        try:
            with case.app.app_context():
                f=fixtures.NeoSubZeroUccTest();f.operation=db.session.get(SortDateOperation,case.operation_id)
                area=f._unit('work_area','Ramp',StaffingUnit.query.filter_by(name='Aviation Services').one())
                employee=f._person('DUCC-QUAL','Qualified','Callout',area);employee_id=employee.id
                remote=f._departure('UPS-REMOTE','N-REMOTE','SDF',3)
                db.session.add(SortDateParkingAssignment(sort_date_operation_id=case.operation_id,tail_number=remote.assigned_tail_number,ramp_code='R',position_code='R01'))
                db.session.commit()
            browser=getattr(case.pw,engine).launch()
            context=browser.new_context(viewport={'width':1920,'height':1080},service_workers='block')
            page=context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.on('dialog',lambda dialog:dialog.accept())
            case.login(page)
            # The existing employee modal edits the same qualification as SubZero.
            case.ready(page,'/neostaffing/people')
            page.evaluate('(id)=>document.dispatchEvent(new CustomEvent("neostaffing:edit-employee",{detail:{personId:id}}))',employee_id)
            editor=page.locator('[data-employee-editor]')
            expect(editor).to_be_visible()
            expect(editor.locator('[name="deice_trained"]')).to_have_value('no')
            editor.locator('[name="deice_trained"]').select_option('yes')
            with page.expect_response(lambda r:'/people/' in r.url and '/update' in r.url and r.request.method=='POST') as saved, page.expect_navigation():
                editor.locator('button[type="submit"]').click()
            self.assertEqual(saved.value.status,200)
            with case.app.app_context():
                self.assertTrue(StaffingPersonQualification.query.filter_by(person_id=employee_id,qualification_key='deice').one().active)
            case.ready(page,'/neosubzero/callouts')
            candidate=page.locator('tr').filter(has=page.locator(f'input[name="person_id"][value="{employee_id}"]'))
            expect(candidate.get_by_role('button',name='ADD CALLOUT')).to_be_visible()
            with page.expect_navigation(): candidate.get_by_role('button',name='ADD CALLOUT').click()
            case.ready(page,'/neosubzero/truck-management')
            add=page.locator('form').filter(has=page.locator('input[name="action"][value="add_truck"]'))
            add.locator('[name="truck_number"]').fill(' tv-101 ')
            with page.expect_navigation(): add.get_by_role('button',name='ADD TRUCK').click()
            expect(page.locator('.subzero-truck-registry')).to_contain_text('TV-101')
            case.ready(page,'/neosubzero/ucc')
            team=page.locator('[data-ucc-ramp="Alpha"] .position-1')
            with page.expect_response(lambda r:r.url.endswith('/neosubzero/truck') and r.request.method=='POST') as assigned:
                team.locator('[name="truck_number"]').select_option('TV-101')
            self.assertEqual(assigned.value.status,200)
            expect(page.locator('[data-ucc-feedback]')).to_contain_text('truck saved')
            expect(team.locator('[data-ucc-assignment-form][data-current-person-id="%s"]' % case.people[0])).to_have_count(1)
            with page.expect_response(lambda r:r.url.endswith('/neosubzero/ucc') and r.request.method=='POST') as assigned:
                team.locator('[name="team_role"][value="flyer"]').locator('xpath=ancestor::form').locator('select').select_option(str(employee_id))
            self.assertEqual(assigned.value.status,200)
            expect(page.locator('[data-ucc-feedback]')).to_contain_text('saved')
            expect(team.locator('.ucc-legacy-save:visible')).to_have_count(0)
            expect(page.locator('[data-ucc-ramp="Alpha"] [data-ucc-queue-position]')).to_have_count(4)
            expect(page.locator('[data-ucc-ramp="Alpha"] [data-ucc-deice-pad] .ucc-pad-outline')).to_be_visible()
            palette=page.evaluate('''()=>{
                const root=document.querySelector('[data-subzero-ucc]');const results={};
                for(const state of ['deice-planned','configured','deicing','finished','radio-pending','negative','gone','normal']){
                    const card=document.createElement('button');card.className='ucc-plane state-'+state;
                    const icon=document.createElement('span');icon.className='ucc-plane-icon';card.append(icon);root.append(card);
                    results[state]=[getComputedStyle(icon).color,getComputedStyle(card).backgroundColor];card.remove();
                }return results;
            }''')
            for state,color in {'deice-planned':'rgb(212, 165, 54)','configured':'rgb(39, 135, 86)','deicing':'rgb(217, 237, 240)','finished':'rgb(40, 93, 168)','radio-pending':'rgb(240, 45, 159)','negative':'rgb(195, 72, 84)','gone':'rgb(104, 116, 128)'}.items():
                self.assertEqual(palette[state],[color,'rgb(9, 23, 33)'])
            # Desktop geometry, orientation, flat dark cards, and a true center gap.
            for width,height in ((1024,768),(1440,900),(1920,1080)):
                page.set_viewport_size({'width':width,'height':height})
                page.wait_for_function('document.fonts.status==="loaded"')
                geometry=page.evaluate('''() => {
                    const box=e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height,right:r.right,bottom:r.bottom}};
                    const main=[...document.querySelectorAll('[data-ucc-ramp]')].slice(0,5);
                    return {width:innerWidth,height:innerHeight,overflow:document.documentElement.scrollWidth-innerWidth,
                      ramps:main.map(r=>({box:box(r),crews:[1,2,3,4].map(n=>box(r.querySelector('.position-'+n))),pad:box(r.querySelector('[data-ucc-deice-pad]'))}))};
                }''')
                if geometry['overflow'] > 1:
                    page.screenshot(path=str(EVIDENCE/f'{engine}-overflow-{width}.png'),full_page=True)
                    geometry['overflow_elements']=page.evaluate('''()=>[...document.querySelectorAll('body *')].filter(e=>e.getBoundingClientRect().right>innerWidth+1).slice(0,25).map(e=>({tag:e.tagName,cls:e.className,right:e.getBoundingClientRect().right}))''')
                self.assertLessEqual(geometry['overflow'],1,geometry)
                self.assertLess(max(r['box']['w'] for r in geometry['ramps'])-min(r['box']['w'] for r in geometry['ramps']),1)
                for r in geometry['ramps']:
                    c=r['crews']; self.assertLess(c[0]['y'],c[1]['y']);self.assertLess(c[3]['y'],c[2]['y'])
                    self.assertLessEqual(c[0]['right'],r['pad']['x']+1); self.assertLessEqual(r['pad']['right'],c[3]['x']+1)
                measurements.append(dict(engine=engine,mode='desktop',**geometry))
                if width==1920:page.screenshot(path=str(EVIDENCE/f'{engine}-desktop.png'),full_page=True)
            # TV is read only, uses the whole screen, and retains relevant Remote.
            page.evaluate('document.documentElement.requestFullscreen=undefined')
            for width,height in ((1920,1080),(2560,1440)):
                page.set_viewport_size({'width':width,'height':height})
                page.locator('[data-ucc-tv-enter]').click()
                expect(page.locator('body')).to_have_class(re.compile('neosubzero-ucc-tv-mode'))
                expect(page.locator('[data-ucc-ramp="Remote"]')).to_be_visible()
                expect(team.locator('.ucc-team-controls')).to_be_hidden()
                expect(page.locator('[data-operational-topbar]')).to_be_hidden()
                page.wait_for_function('(()=>{const r=document.querySelector("[data-subzero-ucc]").getBoundingClientRect();return r.right<=innerWidth+1&&r.bottom<=innerHeight+1})()')
                measurements.append(dict(engine=engine,mode='tv',width=width,height=height))
                page.screenshot(path=str(EVIDENCE/f'{engine}-tv-{width}.png'))
                page.locator('[data-ucc-tv-exit]').click()
            phone=context.new_page();phone.on('pageerror',lambda e:errors.append(str(e)))
            for width in (320,375,390,430):
                phone.set_viewport_size({'width':width,'height':844})
                case.ready(phone,'/neosubzero/ucc?view=ramp&ramp=Alpha')
                expect(phone.locator('[data-ucc-ramp="Alpha"] [data-ucc-deice-pad]')).to_be_visible()
                expect(phone.locator('[data-ucc-ramp="Bravo"]')).to_be_hidden()
                self.assertLessEqual(phone.evaluate('document.documentElement.scrollWidth-innerWidth'),1)
                measurements.append(dict(engine=engine,mode='mobile',width=width))
                if width==390:phone.screenshot(path=str(EVIDENCE/f'{engine}-mobile.png'),full_page=True)
            # Existing spray/CLEAR controls, explicit UNDO, then real readback.
            coordinator=context.new_page();coordinator.on('dialog',lambda d:d.accept())
            case.ready(coordinator,f'/neosubzero/coordinator?ramp=Alpha&mission={case.missions[0]}')
            form=coordinator.locator('[data-subzero-deice-form]')
            form.locator('[name="treatment_plan"]').select_option('one_type_i')
            form.locator('[name="pass1_surface_area"]').select_option('wings_only')
            form.locator('[name="pass1_start"]').fill('22:10')
            with coordinator.expect_navigation():form.locator('button[type="submit"]').click()
            form.locator('[name="pass1_end"]').fill('22:15')
            with coordinator.expect_navigation():form.locator('button[type="submit"]').click()
            with coordinator.expect_navigation():coordinator.locator('button[value="clear"]').click()
            pad=phone.locator('[data-ucc-ramp="Alpha"] [data-ucc-deice-pad]')
            expect(pad.locator('.state-radio-pending')).to_be_visible(timeout=10000)
            self.assertEqual(pad.locator('.ucc-plane-icon').evaluate('e=>getComputedStyle(e).color'),'rgb(240, 45, 159)')
            self.assertEqual(pad.locator('.ucc-plane').evaluate('e=>getComputedStyle(e).backgroundColor'),'rgb(9, 23, 33)')
            coordinator.locator('.subzero-undo-clear summary').click()
            coordinator.locator('.subzero-undo-clear [name="correction_reason"]').fill('Mistaken CLEAR during synthetic verification')
            with coordinator.expect_navigation():coordinator.locator('button[value="undo_clear"]').click()
            expect(pad.locator('.state-finished')).to_be_visible(timeout=10000)
            with coordinator.expect_navigation():coordinator.locator('button[value="clear"]').click()
            with coordinator.expect_navigation():coordinator.locator('button[value="radio_complete"]').click()
            expect(pad.locator('[data-ucc-aircraft]')).to_have_count(0,timeout=10000)
            expect(phone.locator('[data-ucc-ramp="Alpha"] [data-ucc-queue-position="1"] [data-ucc-aircraft]')).to_have_attribute('data-ucc-aircraft',str(case.missions[1]))
            with case.app.app_context():
                self.assertEqual(NeoSubZeroAuditEntry.query.filter_by(action='departure_undo_clear').count(),1)
                self.assertEqual(NeoSubZeroAuditEntry.query.filter_by(action='departure_radio_complete').count(),1)
                event=NeoSubZeroDepartureDeiceEvent.query.filter_by(sort_date_mission_id=case.missions[0]).one()
                self.assertIsNotNone(event.pass1_started_at_utc);self.assertIsNotNone(event.pass1_ended_at_utc)
            self.assertFalse(errors,errors)
        finally:
            (EVIDENCE/f'{engine}-measurements.json').write_text(json.dumps(measurements,indent=2))
            if browser:browser.close()
            case.tearDownClass()

    def test_chromium_coordinated_ducc(self):self.exercise('chromium')
    def test_webkit_coordinated_ducc(self):self.exercise('webkit')
