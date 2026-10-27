"""Authenticated model administration; all records are isolated fixtures."""
import io
import unittest
from unittest.mock import patch
from playwright.sync_api import expect
from tests.browser.test_frost_vault import FrostVaultBrowserTest
from tests.test_frost_models import candidate,synthetic
from app.services import frost_model_registry as registry,frost_vault as vault


class FrostModelsBrowserTest(FrostVaultBrowserTest):
    def test_model_admin_drafts_permissions_and_responsive_layouts(self):
        manifest=synthetic()['manifest']
        for engine in ('chromium','webkit'):
            browser=getattr(self.f.pw,engine).launch(headless=True)
            try:
                page=browser.new_page(viewport={'width':1920,'height':900},service_workers='block')
                self.f().login(page);self.f().ready(page,'/neosubzero/frost-vault')
                panel=page.locator('[data-frost-models]');panel.locator('summary').first.click()
                expect(panel).to_contain_text('Training and backtesting run on the local workstation only')
                expect(panel.locator('[data-model-approved]')).to_have_text('NONE')
                self.assertEqual(panel.get_by_role('button',name='CALCULATE APPROVED FORECAST').count(),0)
                form=panel.locator('[data-model-form]').first;field=form.locator('[type=file]');output=form.locator('output')
                self.assertEqual(form.evaluate('el=>typeof el.action'),'object')
                field.set_input_files({'name':'bad.json','mimeType':'application/json','buffer':b'{"untrusted":"not a model"}'})
                with page.expect_response(lambda r:r.url.endswith('/frost-vault/models') and r.request.method=='POST') as failed:
                    form.get_by_role('button',name='IMPORT CANDIDATE',exact=True).click()
                self.assertEqual(failed.value.status,400);expect(output).to_contain_text('retained')
                self.assertEqual(field.evaluate('el=>el.files[0].name'),'bad.json')
                expect(form.get_by_role('button',name='IMPORT CANDIDATE',exact=True)).to_be_enabled()
                artifact=candidate();artifact['model_version']='browser-'+engine
                with patch.object(registry,'dataset_manifest',return_value=manifest):
                    field.set_input_files({'name':'model.json','mimeType':'application/json','buffer':vault.canonical(artifact)})
                    with page.expect_response(lambda r:r.url.endswith('/frost-vault/models') and r.request.method=='POST') as saved:
                        form.get_by_role('button',name='IMPORT CANDIDATE',exact=True).click()
                    self.assertEqual(saved.value.status,200)
                expect(output).to_contain_text('recorded')
                page.reload();expect(page.locator('[data-frost-models]')).to_contain_text('browser-'+engine)
                panel=page.locator('[data-frost-models]');panel.locator('summary').first.click()
                expect(panel.get_by_role('button',name='APPROVE MODEL',exact=True).first).to_be_disabled() # actual Phase 5 fixture is incomplete
                review=panel.locator('.frost-model-candidate details').first
                review.locator('summary').click()
                expect(review).to_contain_text('Untouched test')
                expect(review).to_contain_text('treatment-cohort metrics, not physical frost accuracy')
                for width,height in ((1920,900),(1920,1080),(1366,768),(3840,2160),(768,1024),(430,900),(375,900),(320,900)):
                    page.set_viewport_size({'width':width,'height':height})
                    self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth-innerWidth'),1)
                    inputs=panel.locator('input:not([type=hidden])')
                    for index in range(inputs.count()):
                        box=inputs.nth(index).bounding_box()
                        if box:self.assertGreaterEqual(box['x'],0);self.assertLessEqual(box['x']+box['width'],width+1)
                    page.screenshot(path=str(self.f.evidence/f'frost-models-{engine}-{width}x{height}.png'),full_page=True)
            finally:browser.close()

    def test_actual_local_cli_authentication_and_csrf_protected_api(self):
        from scripts.frost_local import Remote
        # The fixture disables outbound Python integrations globally. Temporarily
        # allow the CLI's explicitly selected loopback origin, never production.
        self.f.external.stop()
        try:
            remote=Remote(self.f.origin,'drawer-admin@example.test','BrowserFixture123!')
            try:
                import json
                result=json.loads(remote.request('GET','/neosubzero/frost-vault/dataset-manifest'))
                self.assertEqual(result['manifest']['source_origin'],'authenticated_frost_vault')
                self.assertTrue(result['csrf_token'])
                response=remote.session.post(self.f.origin+'/neosubzero/frost-vault/models',data={'action':'import'},timeout=10)
                self.assertEqual(response.status_code,400) # missing CSRF, not a model submission
                response.close()
            finally:remote.close()
            with self.assertRaisesRegex(ValueError,'403'):
                Remote(self.f.origin,'drawer-watcher@example.test','BrowserFixture123!')
        finally:self.f.external.start()
