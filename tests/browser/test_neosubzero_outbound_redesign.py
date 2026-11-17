"""Real OUTBOUND controls and compact layouts in isolated Chromium/WebKit fixtures."""
import unittest
from datetime import datetime, timezone
from pathlib import Path
from playwright.sync_api import expect
from app.extensions import db
from app.models import SortDateOperation, SortDateMission, StaffingPerson, User, NeoSubZeroDepartureDeiceEvent as Event, NeoSubZeroAuditEntry as Audit
from app.services.access_control import backfill_default_gateway_node_roles
from app.services.password_policy import set_user_password
from app.neonodes.neosubzero.services import mutate_pretreat
from app.services.neosubzero_spray import set_neosubzero_ucc_truck
from app.services.neosubzero_ucc import set_neosubzero_ucc_assignment
from app.services.neosubzero_departure_deice import mutate_departure_deice
from tests.browser import test_neosubzero_operations as fixtures

EVIDENCE=Path('instance/outbound-inline/browser')


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
                peer=User(username='inline-peer',email='inline-peer@example.test',role='grandmaster',email_verified_at=datetime.now(),first_name='Peer',last_name='Coordinator')
                set_user_password(peer,'BrowserFixture123!');db.session.add(peer);db.session.flush()
                backfill_default_gateway_node_roles(peer,role='grandmaster');peer_id=peer.id
                db.session.commit()
            browser=getattr(case.pw,engine).launch()
            context=browser.new_context(viewport={'width':1366,'height':768})
            errors=[]
            context.on('page',lambda p:p.on('pageerror',lambda e:errors.append(str(e))))
            dialogs=[]
            page=context.new_page();page.on('dialog',lambda dialog:(dialogs.append(dialog.type),dialog.accept()))
            page.clock.install(time=datetime(2026,10,23,3,10,tzinfo=timezone.utc))
            case.login(page);case.ready(page,'/neosubzero/operations?mode=outbound')
            row=page.locator(f'[data-ops-row][data-mission-id="{case.flow_id}"]')
            actions=row.locator('.ops-mission-summary [data-ops-quick-actions]')
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
            row.locator('[data-ops-accordion] > summary').click()
            detail=row.locator('[data-ops-inline]')
            expect(detail).to_be_visible()
            self.assertEqual(detail.locator('.neosubzero-mission-facts,.ops-team-controls,[data-open-frost-inspection]').count(),0)
            expect(detail.locator('[data-ops-reload-inline]')).to_be_hidden()
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
            actions.locator('[data-ops-pass-stamp="pass1_start"]').click()
            expect(detail.locator('[data-ops-inline-feedback]')).to_contain_text('Save treatment changes')
            self.assertEqual(len(posts),recorded)
            expect(detail.locator('[data-ops-reload-inline]')).to_be_hidden()
            form.locator('[name="pass1_start"]').fill('')
            case.submit(page,form.locator('button[type="submit"]'))
            for width,height in ((1366,768),(1920,1080)):
                page.set_viewport_size({'width':width,'height':height})
                for layout in ('visual','list'):
                    case.layout(page,layout)
                    expect(detail).to_be_visible()
                    summary=row.locator('[data-ops-accordion] > summary')
                    self.assertGreaterEqual(detail.bounding_box()['y'],summary.bounding_box()['y']+summary.bounding_box()['height']-1)
                    self.assertEqual(detail.locator('.ops-identity,.ops-location,.ops-status').count(),0)
                    self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth-innerWidth'),1)
                    page.screenshot(path=str(EVIDENCE/f'{engine}-after-{layout}-{width}.png'),full_page=True)
                    summary.click();expect(detail).to_be_hidden()
                    expect(actions.locator('button[value="reset"]')).to_be_visible()
                    summary.click();expect(detail).to_be_visible()

            for width in (375,390,430):
                page.set_viewport_size({'width':width,'height':844})
                inline=row.locator('[data-ops-inline]')
                expect(inline.locator('[data-subzero-deice-form]')).to_be_visible(timeout=12000)
                for group in inline.locator('[data-subzero-pass]:visible').all():
                    boxes=group.locator(':scope > label').evaluate_all('es=>es.map(e=>{const r=e.getBoundingClientRect();return {y:r.y,right:r.right}})')
                    self.assertLess(max(b['y'] for b in boxes)-min(b['y'] for b in boxes),1,boxes)
                    self.assertTrue(all(b['right']<=width for b in boxes),boxes)
                expect(actions.locator('button[value="reset"]')).to_be_visible()
                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth-innerWidth'),1)
                page.screenshot(path=str(EVIDENCE/f'{engine}-after-mobile-{width}.png'),full_page=True)
            page.set_viewport_size({'width':1366,'height':768})
            expect(detail).to_be_visible(timeout=12000)
            row.locator('[data-ops-accordion] > summary').click()
            expect(detail).to_be_hidden()
            case.submit(page,actions.locator('[data-ops-pass-stamp="pass1_start"]'))
            expect(row).to_have_attribute('data-status','deicing')
            expect(row.locator('.ops-mission-summary .ops-pilot-start')).to_have_count(0)

            # A live peer edit cannot overwrite or silently submit a local draft.
            row.locator('[data-ops-accordion] > summary').click()
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
            detail.locator('[data-ops-reload-inline]').click()
            expect(detail.locator('[name="notes"]')).to_have_value('Peer saved note')
            row.locator('[data-ops-accordion] > summary').click()
            for clock,minute,state in (('pass1_end',15,'deicing'),('pass2_start',16,'deicing'),('pass2_end',20,'fin')):
                page.clock.set_system_time(datetime(2026,10,23,3,minute,tzinfo=timezone.utc))
                case.submit(page,actions.locator(f'[data-ops-pass-stamp="{clock}"]'))
                expect(row).to_have_attribute('data-status',state)
            expect(row.locator('.ops-mission-summary .ops-pilot-start')).to_have_text('PILOT START 22:16')
            action('clear')
            expect(row).to_have_attribute('data-status','radio-pending')
            expect(row).to_have_css('background-color','rgb(240, 45, 159)')
            expect(actions.get_by_role('button',name='RADIO',exact=True)).to_be_visible()
            expect(row.locator('[data-radio-script]:visible')).to_have_count(0)
            expect(actions.locator('button[value="radio_complete"]')).to_have_count(0)
            before_radio_dialogs=len(dialogs)
            actions.get_by_role('button',name='RADIO',exact=True).click()
            expect(detail.locator('[data-radio-script]')).to_contain_text('2216')
            row.locator('[data-ops-accordion] > summary').click()
            expect(detail).to_be_hidden()
            expect(row).to_have_attribute('data-status','radio-pending')
            actions.get_by_role('button',name='RADIO',exact=True).click()
            expect(detail.locator('[data-radio-script]')).to_be_visible()
            for width,layout in ((1366,'list'),(1920,'visual'),(390,'list')):
                page.set_viewport_size({'width':max(width,1366),'height':844})
                case.layout(page,layout)
                page.set_viewport_size({'width':width,'height':844})
                expect(detail.locator('[data-radio-script]')).to_be_visible()
                self.assertEqual(detail.locator('[data-ops-radio-section] button').count(),1)
                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth-innerWidth'),1)
                page.screenshot(path=str(EVIDENCE/f'{engine}-radio-{layout}-{width}.png'),full_page=True)
            self.assertNotIn('FIN',actions.inner_text())
            order=page.locator('[data-ops-row]').evaluate_all('es=>es.map(e=>e.dataset.status)')
            self.assertLess(order.index('radio-pending'),order.index('gone'))
            page.screenshot(path=str(EVIDENCE/f'{engine}-radio-pending.png'),full_page=True)
            stale=detail.locator('[data-ops-radio-section] form').evaluate('form=>Object.fromEntries(new FormData(form))')
            stale.update(target='/neosubzero/departure-deice/mutate',mode='outbound',action='radio_complete')
            peer_context=browser.new_context(viewport={'width':1366,'height':768})
            try:
                peer_page=peer_context.new_page();case.login(peer_page,'inline-peer')
                case.ready(peer_page,f'/neosubzero/operations?mode=outbound&mission={case.flow_id}')
                peer_row=peer_page.locator(f'[data-ops-row][data-mission-id="{case.flow_id}"]')
                expect(peer_row.locator('[data-radio-script]')).to_contain_text('2216')
                case.submit(peer_page,peer_row.locator('[data-ops-radio-section] button[value="radio_complete"]'))
                expect(peer_row.locator('[data-ops-inline]')).to_be_hidden()
            finally:peer_context.close()
            expect(detail).to_be_hidden()
            expect(row).to_have_attribute('data-status','gone')
            expect(row).to_have_css('background-color','rgb(104, 116, 128)')
            expect(actions.locator('button')).to_have_count(0)
            self.assertEqual(page.request.post(case.origin+'/neosubzero/operations/mutate',form=stale).status,409)
            self.assertEqual(len(dialogs),before_radio_dialogs,'RADIO uses the inline section, not a popup confirmation')
            with case.app.app_context():
                event=db.session.get(Event,int(stale['event_id']))
                self.assertEqual(event.radio_completed_by_user_id,peer_id)
                self.assertIsNotNone(event.radio_completed_at_utc)
                self.assertEqual(Audit.query.filter_by(event_id=event.id,action='departure_radio_complete').count(),1)
            comparison=context.new_page()
            for mode,width,height in (('desktop',1366,768),('mobile',390,844)):
                comparison.set_viewport_size({'width':width,'height':height})
                case.ready(comparison,f'/neosubzero/operations?mode=outbound&mission={case.missions[0]}')
                comparison.screenshot(path=str(EVIDENCE/f'{engine}-after-comparable-{mode}.png'),full_page=True)
            self.assertEqual(page.locator('[data-ops-detail],.ops-detail,[role="dialog"]').count(),0)
            self.assertEqual(len(context.pages),2)
            self.assertFalse(errors,errors)
        finally:
            if browser:browser.close()
            case.tearDownClass()

    def test_chromium_outbound_lifecycle_and_layouts(self):self.exercise('chromium')
    def test_webkit_outbound_lifecycle_and_layouts(self):self.exercise('webkit')

    def test_tail_change_keeps_original_draft_under_current_mission(self):
        for engine in ('chromium','webkit'):
            case=fixtures.OperationsBrowserTest();case.setUpClass();browser=None
            try:
                browser=getattr(case.pw,engine).launch()
                page=browser.new_page(viewport={'width':1366,'height':768})
                page.on('dialog',lambda dialog:dialog.accept())
                case.login(page);case.ready(page,f'/neosubzero/operations?mode=outbound&mission={case.missions[0]}')
                row=page.locator(f'[data-ops-row][data-mission-id="{case.missions[0]}"]')
                inline=row.locator('[data-ops-inline]');form=inline.locator('[data-subzero-deice-form]')
                form.locator('[name="treatment_plan"]').select_option('one_type_i')
                form.locator('[name="pass1_surface_area"]').select_option('wings_only')
                draft=form.locator('[name="pass1_start"]');draft.fill('21:45');draft.focus()
                with case.app.app_context():
                    mission=db.session.get(SortDateMission,case.missions[0]);mission.assigned_tail_number='N-REPLACED';db.session.commit()
                expect(row.locator('.ops-identity strong')).to_have_text('N-REPLACED',timeout=12000)
                expect(draft).to_have_value('21:45')
                expect(inline).to_have_attribute('data-tail','N440UP')
                expect(inline.locator('[data-ops-inline-feedback]')).to_contain_text('identity or eligibility changed')
                with page.expect_response(lambda r:r.url.endswith('/operations/mutate') and r.request.method=='POST') as rejected:
                    form.locator('button[type="submit"]').click()
                self.assertEqual(rejected.value.status,409)
                expect(draft).to_have_value('21:45')
                inline.locator('[data-ops-reload-inline]').click()
                expect(inline).to_have_attribute('data-tail','N-REPLACED')
                expect(inline.locator('[data-subzero-deice-form]')).to_have_count(0)
                self.assertEqual(page.locator('[data-ops-detail],.ops-detail,[role="dialog"]').count(),0)
            finally:
                if browser:browser.close()
                case.tearDownClass()
