"""Authenticated compact-model UI and retired bulk workflow, both engines."""
from unittest.mock import patch
from playwright.sync_api import expect
from tests.browser.test_frost_vault import FrostVaultBrowserTest
from tests.test_frost_dataset import local_manifest
from tests.test_frost_models import candidate
from app.services import frost_model_registry as registry,frost_vault as vault


class LocalArchitectureBrowserTest(FrostVaultBrowserTest):
    def test_retirement_button_preserves_original_and_requires_real_csrf(self):
        from app.extensions import db
        from app.models import Gateway,User,FrostHistoryTask,FrostDocumentAudit
        from app.services import frost_documents as documents,frost_history as history
        from tests.test_frost_vault import sample
        with self.f.app.app_context():
            gateway=Gateway.query.first();user=User.query.filter_by(username='drawer-admin').one()
            documents.stage(gateway,user,'retirement-fixture.csv',sample())
            db.session.commit();documents.drain(gateway.id);history.discover(gateway.id)
        browser=self.f.pw.chromium.launch(headless=True)
        try:
            page=browser.new_page(viewport={'width':1920,'height':900},service_workers='block')
            self.f().login(page);self.f().ready(page,'/neosubzero/frost-vault')
            panel=page.locator('[data-frost-models]');panel.locator('summary').first.click()
            with page.expect_response(lambda r:r.url.endswith('/models') and r.request.method=='POST') as saved:
                panel.get_by_role('button',name='RETIRE LEGACY PUBLICATION JOBS',exact=True).click()
            self.assertEqual(saved.value.status,200);expect(panel).to_contain_text('RETIRED 1 legacy')
            page.reload();expect(page.get_by_label('IMPORT CSV',exact=True)).to_be_visible()
            self.assertEqual(page.get_by_role('button',name='RETIRE LEGACY PUBLICATION JOBS',exact=True).count(),0)
            with self.f.app.app_context():
                self.assertEqual(FrostHistoryTask.query.filter_by(kind='partition',status='local_only').count(),1)
                self.assertEqual(FrostDocumentAudit.query.filter_by(action='historical_publication_retired').count(),1)
        finally:browser.close()

    def test_local_manifest_candidate_and_no_bulk_publication_controls(self):
        for engine in ('chromium','webkit'):
            browser=getattr(self.f.pw,engine).launch(headless=True)
            try:
                page=browser.new_page(viewport={'width':1920,'height':900},service_workers='block')
                self.f().login(page);self.f().ready(page,'/neosubzero/frost-vault')
                self.assertEqual(page.get_by_role('button',name='PUBLISH LOCAL RESULT',exact=True).count(),0)
                panel=page.locator('[data-frost-models]');panel.locator('summary').first.click()
                expect(panel).to_contain_text('LOCAL DATA READY')
                form=panel.locator('form:has(input[name="model"])').first
                artifact=candidate();manifest=local_manifest();artifact['model_version']='local-browser-'+engine
                artifact['local_dataset_manifest']=manifest;artifact['dataset']['fingerprint']=manifest['fingerprint']
                with patch.object(registry,'source_catalog',return_value=manifest['source_catalog']):
                    form.locator('[type=file]').set_input_files({'name':'candidate.json','mimeType':'application/json','buffer':vault.canonical(artifact)})
                    with page.expect_response(lambda r:r.url.endswith('/models') and r.request.method=='POST') as saved:form.get_by_role('button',name='IMPORT CANDIDATE',exact=True).click()
                    self.assertEqual(saved.value.status,200)
                    page.reload();panel=page.locator('[data-frost-models]');panel.locator('summary').first.click()
                    expect(panel.get_by_role('button',name='APPROVE MODEL',exact=True).first).to_be_enabled()
                    expect(panel.locator('[data-model-approved]')).to_have_text('NONE')
                    for width in (320,375,390,430,768,1366,1920):
                        page.set_viewport_size({'width':width,'height':900})
                        self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth-innerWidth'),1)
                        page.screenshot(path=str(self.f.evidence/f'frost-local-architecture-{engine}-{width}.png'),full_page=True)
                # An actual request proves that the retired endpoint cannot publish even a valid file.
                response=page.request.get(self.f.origin+'/neosubzero/frost-vault/backfill/1')
                self.assertEqual(response.status,410)
            finally:browser.close()
