"""Real OUTBOUND controls and compact layouts in isolated Chromium/WebKit fixtures."""
import unittest
from datetime import datetime, timezone
from pathlib import Path
from playwright.sync_api import expect
from app.extensions import db
from app.models import SortDateOperation, SortDateMission, StaffingPerson, User, NeoSubZeroDepartureDeiceEvent as Event
from app.neonodes.neosubzero.services import mutate_pretreat
from app.services.neosubzero_spray import set_neosubzero_ucc_truck
from app.services.neosubzero_ucc import set_neosubzero_ucc_assignment
from app.services.neosubzero_departure_deice import mutate_departure_deice
from tests.browser import test_neosubzero_operations as fixtures

EVIDENCE=Path('instance/outbound-redesign/browser')


class OutboundRedesignBrowserTest(unittest.TestCase):
    def exercise(self, engine):
        case=fixtures.OperationsBrowserTest();case.setUpClass();browser=None
        EVIDENCE.mkdir(parents=True,exist_ok=True)
        try:
            with case.app.app_context():
                op=db.session.get(SortDateOperation,case.operation_id)
                mission=db.session.get(SortDateMission,case.flow_id)
                set_neosubzero_ucc_truck(op,'Delta',1,'UCC-831')
                set_neosubzero_ucc_assignment(op,'Delta',1,'driver',db.session.get(StaffingPerson,case.people[1]))
                other=db.session.get(SortDateMission,case.missions[-1])
                mutate_departure_deice(op,other,'clear',{},actor_id=User.query.filter_by(username='drawer-admin').one().id)
                db.session.commit()
            browser=getattr(case.pw,engine).launch()
            context=browser.new_context(viewport={'width':1366,'height':768})
            errors=[]
            context.on('page',lambda p:p.on('pageerror',lambda e:errors.append(str(e))))
            page=context.new_page();page.on('dialog',lambda dialog:dialog.accept())
            page.clock.install(time=datetime(2026,10,23,3,10,tzinfo=timezone.utc))
            case.login(page);case.ready(page,'/neosubzero/operations?mode=outbound')
            row=page.locator(f'[data-ops-row][data-mission-id="{case.flow_id}"]')
            actions=row.locator(':scope > [data-ops-quick-actions]')
            def action(key):case.submit(page,actions.locator(f'button[value="{key}"]'))
            expect(row).to_have_attribute('data-status','untreated')
            action('initial_contact');action('reset')
            expect(row).to_have_attribute('data-status','untreated')
            action('initial_contact');action('toggle_configured')
            expect(row).to_have_attribute('data-status','configured')
            expect(actions.get_by_role('button',name='CLEAR CONFIGURED',exact=True)).to_be_visible()
            action('toggle_configured')
            expect(row).to_have_attribute('data-status','planned')
            action('toggle_configured');action('reset')

            with case.app.app_context():
                op=db.session.get(SortDateOperation,case.operation_id)
                mission=db.session.get(SortDateMission,case.flow_id)
                mutate_pretreat(op,mission,'save_treatment',dict(pass1_start='21:30',pass1_end='21:35',pass2_start='21:36',pass2_end='21:40'))
                db.session.commit()
            expect(row).to_have_attribute('data-status','pretreated',timeout=12000)
            expect(row).to_have_css('background-color','rgb(40, 93, 168)')
            self.assertNotIn('UNTREATED',row.inner_text())
            for width,height in ((1366,768),(1920,1080)):
                page.set_viewport_size({'width':width,'height':height})
                for layout in ('visual','list'):
                    case.layout(page,layout)
                    expect(actions.locator('button[value="initial_contact"]')).to_be_visible()
                    self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth-innerWidth'),1)
            action('initial_contact');action('toggle_configured')
            row.locator('[data-ops-open]').click()
            detail=page.locator('[data-ops-detail]')
            expect(detail).to_be_visible()
            self.assertEqual(detail.locator('.neosubzero-mission-facts,.ops-team-controls,[data-open-frost-inspection]').count(),0)
            expect(detail.locator('[data-ops-reload]')).to_be_hidden()
            reason=detail.locator('select[name="reason_for_application"]')
            with page.expect_response(lambda r:r.url.endswith('/operations/mutate') and r.request.method=='POST') as saved:
                reason.select_option('Freezing Rain')
            self.assertEqual(saved.value.status,200,saved.value.text())
            expect(reason).to_have_value('Freezing Rain')
            form=detail.locator('[data-subzero-deice-form]')
            expect(form.locator('[name="treatment_plan"]')).to_be_enabled()
            form.locator('[name="treatment_plan"]').select_option('type_i_type_iv')
            form.locator('[name="pass1_surface_area"]').select_option('wings_only')
            form.locator('[name="pass2_surface_area"]').select_option('wings_tail')
            posts=[]
            page.on('request',lambda r:posts.append(r.url) if r.method=='POST' and r.url.endswith('/operations/mutate') else None)
            form.locator('[name="pass1_start"]').fill('2')
            form.locator('[name="treatment_plan"]').focus()
            self.assertFalse(posts,'Incomplete treatment clocks must not autosave')
            form.locator('[name="pass1_start"]').fill('')
            case.submit(page,form.locator('button[type="submit"]'))
            recorded=len(posts)
            form.locator('[name="pass1_start"]').fill('2')
            detail.locator('[data-ops-pass-stamp="pass1_start"]').click()
            expect(detail.locator('[data-ops-detail-feedback]')).to_contain_text('Save treatment changes')
            self.assertEqual(len(posts),recorded)
            expect(detail.locator('[data-ops-reload]')).to_be_hidden()
            form.locator('[name="pass1_start"]').fill('')
            case.submit(page,form.locator('button[type="submit"]'))
            for width,height in ((1366,768),(1920,1080)):
                page.set_viewport_size({'width':width,'height':height})
                page.screenshot(path=str(EVIDENCE/f'{engine}-after-desktop-{width}.png'),full_page=True)

            for width in (375,390,430):
                page.set_viewport_size({'width':width,'height':844})
                inline=row.locator('[data-ops-inline]')
                expect(inline.locator('[data-subzero-deice-form]')).to_be_visible(timeout=12000)
                for group in inline.locator('[data-subzero-pass]:visible').all():
                    boxes=group.locator(':scope > label').evaluate_all('es=>es.map(e=>{const r=e.getBoundingClientRect();return {y:r.y,right:r.right}})')
                    self.assertLess(max(b['y'] for b in boxes)-min(b['y'] for b in boxes),1,boxes)
                    self.assertTrue(all(b['right']<=width for b in boxes),boxes)
                expect(inline.locator('button[value="reset"]')).to_be_visible()
                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth-innerWidth'),1)
                page.screenshot(path=str(EVIDENCE/f'{engine}-after-mobile-{width}.png'),full_page=True)
            page.set_viewport_size({'width':1366,'height':768})
            expect(detail).to_be_visible(timeout=12000)
            page.locator('[data-ops-close]').click()
            expect(detail).to_be_hidden()
            case.submit(page,actions.locator('[data-ops-pass-stamp="pass1_start"]'))
            expect(row).to_have_attribute('data-status','deicing')
            expect(row.locator('.ops-desktop-outbound .ops-pilot-start')).to_have_count(0)

            # A live peer edit cannot overwrite or silently submit a local draft.
            row.locator('[data-ops-open]').click()
            detail.locator('[data-ops-disclosure="notes"] > summary').click()
            notes=detail.locator('[name="notes"]');notes.fill('Unsaved local note')
            with case.app.app_context():
                op=db.session.get(SortDateOperation,case.operation_id)
                mission=db.session.get(SortDateMission,case.flow_id)
                mutate_departure_deice(op,mission,'save_notes',{'notes':'Peer saved note'},actor_id=User.query.filter_by(username='drawer-admin').one().id)
                db.session.commit()
            expect(detail.locator('[data-ops-draft-status]')).to_contain_text('LIVE DATA UPDATED',timeout=12000)
            expect(notes).to_have_value('Unsaved local note')
            with page.expect_response(lambda r:r.url.endswith('/operations/mutate') and r.request.method=='POST') as stale:
                detail.get_by_role('button',name='SAVE NOTES',exact=True).click()
            self.assertEqual(stale.value.status,409)
            expect(notes).to_have_value('Unsaved local note')
            detail.locator('[data-ops-reload]').click()
            expect(detail.locator('[name="notes"]')).to_have_value('Peer saved note')
            page.locator('[data-ops-close]').click()
            for clock,minute,state in (('pass1_end',15,'deicing'),('pass2_start',16,'deicing'),('pass2_end',20,'fin')):
                page.clock.set_system_time(datetime(2026,10,23,3,minute,tzinfo=timezone.utc))
                case.submit(page,actions.locator(f'[data-ops-pass-stamp="{clock}"]'))
                expect(row).to_have_attribute('data-status',state)
            expect(row.locator('.ops-desktop-outbound .ops-pilot-start')).to_have_text('PILOT START 22:16')
            action('clear')
            expect(row).to_have_attribute('data-status','radio-pending')
            expect(row).to_have_css('background-color','rgb(240, 45, 159)')
            expect(row.locator('.ops-card-readback')).to_contain_text('2216')
            self.assertNotIn('FIN',actions.inner_text())
            order=page.locator('[data-ops-row]').evaluate_all('es=>es.map(e=>e.dataset.status)')
            self.assertLess(order.index('radio-pending'),order.index('gone'))
            page.screenshot(path=str(EVIDENCE/f'{engine}-radio-pending.png'),full_page=True)
            action('radio_complete')
            expect(row).to_have_attribute('data-status','gone')
            expect(row).to_have_css('background-color','rgb(104, 116, 128)')
            expect(actions.locator('button')).to_have_count(0)
            comparison=context.new_page()
            for mode,width,height in (('desktop',1366,768),('mobile',390,844)):
                comparison.set_viewport_size({'width':width,'height':height})
                case.ready(comparison,f'/neosubzero/operations?mode=outbound&mission={case.missions[0]}')
                comparison.screenshot(path=str(EVIDENCE/f'{engine}-after-comparable-{mode}.png'),full_page=True)
            self.assertFalse(errors,errors)
        finally:
            if browser:browser.close()
            case.tearDownClass()

    def test_chromium_outbound_lifecycle_and_layouts(self):self.exercise('chromium')
    def test_webkit_outbound_lifecycle_and_layouts(self):self.exercise('webkit')
