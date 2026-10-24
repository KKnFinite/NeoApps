"""Real CSRF-protected uploads, private downloads and status refresh on both engines."""
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from playwright.sync_api import expect
from tests.browser import test_mobile_drawer as fixtures
from tests import test_frost_vault as contracts
from app.extensions import db
from app.models import Gateway,FrostDocument
from app.services.frost_documents import drain


class FrostVaultBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.f=fixtures.MobileDrawerBrowserTest;cls.f.setUpClass()
        cls.store=contracts.MemoryR2()
        cls.patches=[patch('app.services.neoscorpion_learning_vault._require_r2_config',return_value={'bucket':'isolated'}),
            patch('app.services.neoscorpion_learning_vault._r2_client',return_value=cls.store),
            patch('app.neonodes.neosubzero.frost_routes.learning_vault_status',return_value=SimpleNamespace(configured=True))]
        for item in cls.patches:item.start()

    @classmethod
    def tearDownClass(cls):
        for item in reversed(cls.patches):item.stop()
        cls.f.tearDownClass()

    def test_upload_duplicate_failed_save_download_and_access_in_both_engines(self):
        folder=Path('instance/browser-evidence/frost-vault');folder.mkdir(parents=True,exist_ok=True)
        for engine in ('chromium','webkit'):
            browser=getattr(self.f.pw,engine).launch(headless=True)
            try:
                page=browser.new_page(viewport={'width':375,'height':900},service_workers='block')
                errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                self.f().login(page);self.f().ready(page,'/neosubzero/frost-vault')
                form=page.locator('[data-frost-upload]');file=form.locator('[type=file]')
                file.set_input_files({'name':'broken.csv','mimeType':'text/csv','buffer':b'a,b\n1\n'})
                with page.expect_response(lambda r:r.url.endswith('/frost-vault') and r.request.method=='POST') as rejected:
                    form.locator('button').click()
                self.assertEqual(rejected.value.status,400)
                expect(form.locator('output')).to_contain_text('does not match')
                self.assertEqual(file.evaluate('el=>el.files[0].name'),'broken.csv')
                expect(form.locator('button')).to_be_enabled()
                raw=contracts.sample(RecordID=engine)
                file.set_input_files({'name':engine+'-season.csv','mimeType':'text/csv','buffer':raw})
                with page.expect_response(lambda r:r.url.endswith('/frost-vault') and r.request.method=='POST') as saved:
                    form.locator('button').click()
                self.assertEqual(saved.value.status,200)
                # Successful import navigates to the catalog; Chromium may discard
                # the fetch response body at that boundary. Read persisted identity.
                import hashlib
                with self.f.app.app_context():
                    identifier=FrostDocument.query.filter_by(sha256=hashlib.sha256(raw).hexdigest()).one().id
                card=page.locator(f'[data-frost-document="{identifier}"]')
                expect(card).to_contain_text('ENCRYPTED STAGING')
                with page.expect_download() as downloaded:card.get_by_role('link',name='DOWNLOAD ORIGINAL').click()
                self.assertEqual(Path(downloaded.value.path()).read_bytes(),raw)
                with self.f.app.app_context():
                    gateway=Gateway.query.filter_by(code='RFD').one()
                    self.assertEqual(drain(gateway.id),1)
                expect(card.locator('[data-frost-delivery]')).to_have_text('OFF-SITE VERIFIED',timeout=16000)
                expect(card.locator('[data-frost-extracted]')).to_be_visible()
                with page.expect_download() as extracted:card.get_by_role('link',name='EXTRACTED EVIDENCE').click()
                import json
                content=json.loads(Path(extracted.value.path()).read_text(encoding='utf-8'))
                self.assertEqual(content['rows'][0]['raw']['RecordID'],engine)
                self.assertFalse(content['training_eligible'])
                file.set_input_files({'name':'same.csv','mimeType':'text/csv','buffer':raw})
                with page.expect_response(lambda r:r.url.endswith('/frost-vault') and r.request.method=='POST') as duplicate:
                    form.locator('button').click()
                self.assertEqual(duplicate.value.status,409)
                expect(form.locator('output')).to_contain_text('duplicate')
                with self.f.app.app_context():self.assertEqual(FrostDocument.query.filter_by(id=identifier).count(),1)
                for width in (320,375,390,768,1440):
                    page.set_viewport_size({'width':width,'height':950})
                    self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth-innerWidth'),1)
                    page.screenshot(path=str(folder/f'{engine}-{width}.png'),full_page=True)
                denied=browser.new_context();watcher=denied.new_page()
                self.f().login(watcher,'drawer-watcher')
                for path in ('/neosubzero/frost-vault',f'/neosubzero/frost-vault/documents/{identifier}'):
                    response=watcher.goto(self.f.origin+path);self.assertEqual(response.status,403)
                csrf=page.request.post(self.f.origin+'/neosubzero/frost-vault',data={'document':'not-a-file'})
                self.assertEqual(csrf.status,400)
                self.assertFalse(errors,errors);denied.close()
            finally:browser.close()
