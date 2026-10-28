"""Real optional inspection forms through the existing Operations transport."""
from pathlib import Path
from datetime import datetime, timezone
from playwright.sync_api import expect
from app.extensions import db
from app.models import NeoSubZeroAuditEntry, SortDateOperation
from tests.browser.test_neosubzero_operations import OperationsBrowserTest


class InspectionBrowserTest(OperationsBrowserTest):
    def test_optional_inspection_drafts_errors_correction_and_mobile(self):
        evidence=Path('instance/browser-evidence/frost-phase10').resolve();evidence.mkdir(parents=True,exist_ok=True)
        for engine in ('chromium','webkit'):
            browser=getattr(self.pw,engine).launch()
            try:
                page=browser.new_page(viewport={'width':1920,'height':900},service_workers='block')
                errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                self.login(page);self.ready(page,f'/neosubzero/operations?mode=outbound&mission={self.flow_id}')
                panel=page.locator('.neosubzero-frost-inspection').first
                panel.locator(':scope > summary').click()
                form=panel.locator('form').first
                self.assertEqual(form.locator('[name=frost_result]').input_value(),'')
                form.locator('[name=frost_result]').select_option('absent')
                form.locator('[name=inspected_local]').fill('2026-10-22T12:00')
                form.locator('[name=inspection_location]').fill('Delta direct inspection')
                form.locator('[name=inspection_notes]').fill('Observed actual surface; isolated fixture')
                form.locator('[name=inspection_confirmed]').check()
                # Actual failed request recovery; no timer-based success guess.
                page.route('**/neosubzero/operations/mutate',lambda route:route.abort('failed'))
                form.get_by_role('button',name='RECORD INSPECTION',exact=True).click()
                expect(page.locator('[data-ops-detail-feedback]')).to_contain_text('Network error')
                expect(form.get_by_role('button')).to_be_enabled()
                expect(form.locator('[name=inspection_notes]')).to_have_value('Observed actual surface; isolated fixture')
                page.unroute('**/neosubzero/operations/mutate')
                # Concurrent save must preserve the original draft/token.
                with self.app.app_context():
                    from app.models import SortDateMission,User
                    from app.services.frost_inspections import record
                    from uuid import uuid4
                    op=db.session.get(SortDateOperation,self.operation_id);mission=db.session.get(SortDateMission,self.flow_id)
                    last=NeoSubZeroAuditEntry.query.filter_by(action='frost_physical_inspection',tail_number=mission.assigned_tail_number).order_by(NeoSubZeroAuditEntry.id.desc()).first()
                    record(op,mission,dict(physical_tail=mission.assigned_tail_number,inspection_request_id=str(uuid4()),
                        frost_result='uncertain',inspection_confirmed='yes',inspected_local='2026-10-22T12:00',
                        inspection_location='Delta actual aircraft',expected_inspection_id=str(last.id) if last else ''),actor_id=User.query.first().id)
                    db.session.commit()
                notes=form.locator('[name=inspection_notes]');notes.focus()
                expect(page.locator('[data-ops-draft-status]')).to_contain_text('LIVE DATA UPDATED',timeout=15000)
                expect(notes).to_be_focused();expect(notes).to_have_value('Observed actual surface; isolated fixture')
                with page.expect_response(lambda r:r.url.endswith('/operations/mutate') and r.request.method=='POST') as pending:
                    form.get_by_role('button',name='RECORD INSPECTION',exact=True).click()
                self.assertEqual(pending.value.status,409)
                expect(page.locator('[data-ops-detail-feedback]')).to_contain_text('Another inspection was saved')
                expect(notes).to_have_value('Observed actual surface; isolated fixture')
                page.once('dialog',lambda dialog:dialog.accept())
                page.locator('[data-ops-reload]').click()
                expect(page.locator('[data-ops-detail-feedback]')).not_to_contain_text('Another inspection was saved')
                panel=page.locator('.neosubzero-frost-inspection').first
                if not panel.evaluate('e=>e.open'):panel.locator(':scope > summary').click()
                form=panel.locator('form').first
                form.locator('[name=frost_result]').select_option('absent')
                form.locator('[name=inspected_local]').fill('2026-10-22T12:00')
                form.locator('[name=inspection_location]').fill('Delta direct inspection')
                form.locator('[name=inspection_confirmed]').check()
                self.submit(page,form.get_by_role('button',name='RECORD INSPECTION',exact=True))
                expect(page.locator('[data-ops-detail-feedback]')).to_contain_text('INSPECTION SAVED')
                panel=page.locator('.neosubzero-frost-inspection').first
                if not panel.evaluate('e=>e.open'):panel.locator(':scope > summary').click()
                expect(panel).to_contain_text('OBSERVED FROST ABSENT')
                correction=panel.locator('details').first
                correction.locator('summary').click()
                form=correction.locator('form');form.locator('[name=frost_result]').select_option('uncertain')
                form.locator('[name=inspected_local]').fill('2026-10-22T12:00');form.locator('[name=inspection_location]').fill('Delta actual aircraft')
                form.locator('[name=correction_reason]').fill('Fixture second review');form.locator('[name=inspection_confirmed]').check()
                self.submit(page,form.get_by_role('button',name='SAVE INSPECTION CORRECTION'))
                expect(page.locator('[data-ops-detail-feedback]')).to_contain_text('INSPECTION SAVED')
                for width,height in ((1920,900),(1920,1080),(1366,768),(3840,2160),(768,1024),(430,900),(390,900),(375,900),(320,900)):
                    page.set_viewport_size(dict(width=width,height=height))
                    panel=page.locator('.neosubzero-frost-inspection').first
                    if not panel.evaluate('e=>e.open'):panel.locator(':scope > summary').click()
                    field=panel.locator('[name=inspection_location]').first
                    field.focus();field.scroll_into_view_if_needed()
                    self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth-innerWidth'),1)
                    bounds=panel.locator('input:not([type=hidden]),select,textarea,button').evaluate_all('es=>es.filter(e=>e.getBoundingClientRect().height).map(e=>({left:e.getBoundingClientRect().left,right:e.getBoundingClientRect().right}))')
                    for box in bounds:self.assertGreaterEqual(box['left'],0);self.assertLessEqual(box['right'],width+1)
                    if (width,height) in ((1920,900),(375,900)):page.screenshot(path=str(evidence/f'inspection-{engine}-{width}.png'))
                self.assertEqual(errors,[])
                page.reload();expect(page.locator('[data-ops-workspace]')).to_have_attribute('data-ready','true')
            finally:browser.close()
