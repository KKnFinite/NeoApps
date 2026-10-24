"""Real CSRF-protected uploads, private downloads and status refresh on both engines."""
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from playwright.sync_api import expect
from tests.browser import test_mobile_drawer as fixtures
from tests import test_frost_vault as contracts
from app.extensions import db
from app.models import Gateway,FrostDocument,User,NeoSubZeroSprayRecord
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

    def test_mobile_native_picker_uploads_seasons_and_recovers_errors(self):
        for engine in ('chromium','webkit'):
            browser=getattr(self.f.pw,engine).launch(headless=True)
            try:
                context=browser.new_context(viewport={'width':375,'height':900},is_mobile=True,has_touch=True,
                    service_workers='block')
                page=context.new_page();self.f().login(page);self.f().ready(page,'/neosubzero/frost-vault')
                file=page.get_by_label('IMPORT CSV',exact=True)
                expect(file).to_be_attached(timeout=1500)
                box=file.bounding_box();self.assertGreaterEqual(box['height'],44)
                with page.expect_file_chooser() as chooser:file.tap()
                with page.expect_response(lambda r:r.url.endswith('/frost-vault') and r.request.method=='POST') as invalid:
                    chooser.value.set_files({'name':'broken.csv','mimeType':'text/plain','buffer':b'a,b\n1\n'})
                self.assertEqual(invalid.value.status,400)
                expect(page.locator('[data-frost-feedback]')).to_contain_text('does not match')
                self.assertEqual(file.evaluate('el=>el.files[0].name'),'broken.csv')
                expect(page.locator('[data-frost-submit]')).to_be_enabled()
                for season in ('2025','2026'):
                    raw=contracts.sample(RecordID=engine+season,ApplicationDate='9/04/'+season)
                    season_name=engine+'-'+season+'.csv'
                    with page.expect_file_chooser() as chooser:file.tap()
                    with page.expect_response(lambda r:r.url.endswith('/frost-vault') and r.request.method=='POST') as saved:
                        chooser.value.set_files({'name':season_name,'mimeType':'application/vnd.ms-excel' if season=='2025' else 'text/plain','buffer':raw})
                    self.assertEqual(saved.value.status,200)
                    expect(page.locator('[data-frost-feedback]')).to_contain_text('IMPORTED: '+season_name)
                    card=page.locator('[data-frost-document]').filter(has=page.locator('strong',has_text=season_name))
                    expect(card).to_contain_text('ENCRYPTED STAGING')
                    with self.f.app.app_context():
                        document=FrostDocument.query.filter_by(sha256=contracts.vault.digest(raw)).one()
                        self.assertEqual(contracts.documents.original(document),raw)
                        self.assertEqual(document.imported_by_user_id,User.query.filter_by(username='drawer-admin').one().id)
                        self.assertEqual(NeoSubZeroSprayRecord.query.count(),0)
                        gateway=Gateway.query.filter_by(code='RFD').one();self.assertEqual(drain(gateway.id),1)
                    expect(card.locator('[data-frost-delivery]')).to_have_text('OFF-SITE VERIFIED',timeout=16000)
                # A disconnected iCloud/mobile transfer must recover without
                # clearing the selected file, silently retrying, or inventing success.
                attempts=[]
                def offline(route):
                    if route.request.method=='POST':attempts.append(route.request.url);route.abort()
                    else:route.continue_()
                page.route('**/neosubzero/frost-vault',offline)
                with page.expect_file_chooser() as chooser:file.tap()
                chooser.value.set_files({'name':'retry-season.csv','mimeType':'text/csv','buffer':contracts.sample(RecordID=engine+'-retry')})
                expect(page.locator('[data-frost-feedback]')).to_contain_text('Network error')
                self.assertEqual(file.evaluate('el=>el.files[0].name'),'retry-season.csv')
                self.assertEqual(len(attempts),1)
                expect(file).to_be_enabled();expect(page.locator('[data-frost-submit]')).to_be_enabled()
                page.unroute('**/neosubzero/frost-vault',offline)
                with page.expect_response(lambda r:r.url.endswith('/frost-vault') and r.request.method=='POST') as retry:
                    page.locator('[data-frost-submit]').tap()
                self.assertEqual(retry.value.status,200)
                expect(page.locator('[data-frost-feedback]')).to_contain_text('IMPORTED: retry-season.csv')
                with self.f.app.app_context():
                    gateway=Gateway.query.filter_by(code='RFD').one();self.assertEqual(drain(gateway.id),1)
                for width in (320,375,390,430):
                    page.set_viewport_size({'width':width,'height':900})
                    self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth-innerWidth'),1)
                    self.assertGreaterEqual(file.bounding_box()['height'],44)
                    folder=Path('instance/browser-evidence/frost-mobile-upload');folder.mkdir(parents=True,exist_ok=True)
                    page.screenshot(path=str(folder/f'{engine}-{width}.png'),full_page=True)
                context.close()
            finally:browser.close()

    def test_season_sized_original_through_touch_picker_and_authenticated_route(self):
        source=Path(r'C:\Users\kknfi\.codex\codex-remote-attachments\01a01dc6-8beb-70b2-bfb7-2622e24a2c5f\C4E2E506-A782-4399-8A01-862BC3683F49\1-deicing_log_RFD-2.csv')
        if not source.is_file():self.skipTest('Private supplied season CSV is not committed to the repository')
        raw=source.read_bytes()
        for engine in ('chromium','webkit'):
            browser=getattr(self.f.pw,engine).launch(headless=True)
            try:
                page=browser.new_page(viewport={'width':390,'height':844},is_mobile=True,has_touch=True,service_workers='block')
                self.f().login(page);self.f().ready(page,'/neosubzero/frost-vault')
                with page.expect_file_chooser() as chooser:page.get_by_label('IMPORT CSV',exact=True).tap()
                with page.expect_response(lambda r:r.url.endswith('/frost-vault') and r.request.method=='POST') as saved:
                    chooser.value.set_files(str(source.resolve()))
                self.assertEqual(saved.value.status,200 if engine=='chromium' else 409)
                expect(page.locator('[data-frost-feedback]')).to_contain_text('IMPORTED:' if engine=='chromium' else 'duplicate')
                with self.f.app.app_context():
                    document=FrostDocument.query.filter_by(sha256=contracts.vault.digest(raw)).one()
                    self.assertEqual(document.byte_length,len(raw));self.assertEqual(document.extraction_summary['source_row_count'],3053)
                    self.assertEqual(contracts.documents.original(document),raw)
                    if engine=='chromium':self.assertEqual(drain(document.gateway_id),1)
                    self.assertIsNotNone(document.archived_at_utc)
                    self.assertEqual(contracts.documents.original(document),raw)
                    self.assertEqual(NeoSubZeroSprayRecord.query.count(),0)
            finally:browser.close()

    def test_upload_duplicate_failed_save_download_and_access_in_both_engines(self):
        folder=Path('instance/browser-evidence/frost-vault');folder.mkdir(parents=True,exist_ok=True)
        for engine in ('chromium','webkit'):
            browser=getattr(self.f.pw,engine).launch(headless=True)
            try:
                page=browser.new_page(viewport={'width':375,'height':900},service_workers='block')
                errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                self.f().login(page);self.f().ready(page,'/neosubzero/frost-vault')
                form=page.locator('[data-frost-upload]');file=form.locator('[type=file]')
                with page.expect_response(lambda r:r.url.endswith('/frost-vault') and r.request.method=='POST') as rejected:
                    file.set_input_files({'name':'broken.csv','mimeType':'text/csv','buffer':b'a,b\n1\n'})
                self.assertEqual(rejected.value.status,400)
                expect(form.locator('output')).to_contain_text('does not match')
                self.assertEqual(file.evaluate('el=>el.files[0].name'),'broken.csv')
                expect(form.locator('button')).to_be_enabled()
                raw=contracts.sample(RecordID=engine)
                with page.expect_response(lambda r:r.url.endswith('/frost-vault') and r.request.method=='POST') as saved:
                    file.set_input_files({'name':engine+'-season.csv','mimeType':'text/csv','buffer':raw})
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
                with page.expect_response(lambda r:r.url.endswith('/frost-vault') and r.request.method=='POST') as duplicate:
                    file.set_input_files({'name':'same.csv','mimeType':'text/csv','buffer':raw})
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
