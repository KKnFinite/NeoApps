"""Actual authenticated download -> local computation -> CSRF publish, both engines."""
import io
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from playwright.sync_api import expect
from tests.browser.test_frost_vault import FrostVaultBrowserTest
from tests.test_frost_history import weather_csv
from tests.test_frost_vault import sample
from app.extensions import db
from app.models import Gateway,User,NeoSubZeroSprayRecord
from app.services import frost_documents as documents,frost_history as history
from scripts.frost_backfill import compute


class LocalBackfillBrowserTest(FrostVaultBrowserTest):
    def test_download_local_calculation_publish_and_failed_file_retention(self):
        for engine in ('chromium','webkit'):
            with self.f.app.app_context():
                gateway=Gateway.query.first();user=User.query.filter_by(username='drawer-admin').one()
                documents.stage(gateway,user,'local-'+engine+'.csv',sample(RecordID=engine,Reason='F'))
                db.session.commit();documents.drain(gateway.id);history.discover(gateway.id)
            browser=getattr(self.f.pw,engine).launch(headless=True)
            try:
                page=browser.new_page(viewport={'width':1920,'height':900},service_workers='block')
                self.f().login(page);self.f().ready(page,'/neosubzero/frost-vault')
                panel=page.locator('[data-local-backfill]');panel.locator('summary').click()
                expect(panel).to_contain_text('never Render')
                for width in (320,375,390,430,768,1920):
                    page.set_viewport_size({'width':width,'height':900})
                    self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth-innerWidth'),1)
                    page.screenshot(path=str(self.f.evidence/f'frost-local-{engine}-{width}.png'),full_page=True)
                with page.expect_download() as download:panel.locator('[data-backfill-month]').first.click()
                envelope=json.loads(Path(download.value.path()).read_bytes())
                file=panel.locator('[type=file]');button=panel.get_by_role('button',name='PUBLISH LOCAL RESULT',exact=True)
                file.set_input_files({'name':'bad.zip','mimeType':'application/zip','buffer':b'broken'})
                with page.expect_response(lambda r:r.url.endswith('/backfill/publish') and r.request.method=='POST') as failed:button.click()
                self.assertEqual(failed.value.status,409);expect(panel.locator('output')).to_contain_text('retained')
                self.assertEqual(file.evaluate('el=>el.files[0].name'),'bad.zip');expect(button).to_be_enabled()
                with tempfile.TemporaryDirectory() as folder,patch('app.services.frost_weather.fetch',side_effect=lambda start,end:(weather_csv(start,end),'https://fixture.invalid')):
                    result=compute(envelope,folder)
                    bad=Path(folder)/'bad.zip';bad.write_bytes(b'broken')
                    file.set_input_files([str(result),str(bad)])
                    with page.expect_response(lambda r:r.url.endswith('/backfill/publish') and r.request.method=='POST') as partial:button.click()
                    self.assertEqual(partial.value.status,409)
                    expect(panel.locator('output')).to_contain_text('Already published:')
                    self.assertEqual(file.evaluate('el=>el.files.length'),2);expect(button).to_be_enabled()
                    file.set_input_files([str(result),str(result)])
                    with page.expect_response(lambda r:r.url.endswith('/backfill/publish') and r.request.method=='POST') as published:button.click()
                    self.assertEqual(published.value.status,200)
                expect(panel.locator('output')).to_contain_text('LOCAL RESULT PUBLISHED')
                page.reload();expect(page.locator('[data-local-backfill] summary')).to_contain_text('0 MONTHS PENDING')
                with self.f.app.app_context():self.assertEqual(NeoSubZeroSprayRecord.query.count(),0)
            finally:browser.close()
