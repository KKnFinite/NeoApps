"""Authenticated AUTO status, bounded private registration, mobile CSV regression."""
from pathlib import Path
from unittest.mock import patch
from playwright.sync_api import expect
from tests.browser.test_frost_vault import FrostVaultBrowserTest
from app.extensions import db
from app.models import Gateway,FrostModelArtifact,FrostModelRelease
from app.services import frost_auto as auto
from app.services.frost_weather import STATION


class AutoBrowserTest(FrostVaultBrowserTest):
    def test_auto_status_preserves_file_focus_research_only_and_responsive(self):
        from tests.test_frost_phase12 import ProspectiveTest
        helper=ProspectiveTest();helper.setUp();self.addCleanup(helper.doCleanups)
        self.enterContext(patch.object(auto,'frozen_models',return_value=helper.baseline['models']))
        with self.f.app.app_context():
            gateway=Gateway.query.first()
            with patch.object(auto,'acquire',return_value=dict(station=STATION,cutoff_at_utc=auto.stamp(auto.now()),
                    observations=[],sources=[],native_forecasts=[])):
                self.assertEqual(auto.tick(gateway.id),1)
        entry=auto.frozen_models()[0]
        raw=(helper.frozen.parent/'artifacts'/(entry['sha256']+'.json')).read_bytes()
        for engine in ('chromium','webkit'):
            browser=getattr(self.f.pw,engine).launch(headless=True)
            try:
                page=browser.new_page(viewport=dict(width=1920,height=900),service_workers='block')
                self.f().login(page);self.f().ready(page,'/neosubzero/frost-vault')
                expect(page.locator('[data-auto-field="state"]')).to_have_text('AUTOMATIC COLLECTION ACTIVE')
                expect(page.locator('[data-auto-field="archived"]')).to_have_text('1')
                expect(page.locator('[data-auto-field="unavailable"]')).to_have_text('8')
                expect(page.locator('[data-model-approved]')).to_have_text('NONE')
                expect(page.get_by_role('heading',name='DEPARTURE CORRELATION',exact=True)).to_be_attached()
                expect(page.locator('[data-departure-field="state"]')).to_contain_text('NO DEPARTURES RECORDED')
                page.get_by_text('REGISTER EXACT FROZEN RESEARCH JSON',exact=True).click()
                form=page.locator('form[action$="/research-models"]')
                field=form.get_by_label('Frozen research JSON')
                field.set_input_files(dict(name='altered.json',mimeType='application/json',buffer=raw+b' '))
                form.get_by_role('button',name='REGISTER RESEARCH ARTIFACT',exact=True).click();expect(form.locator('output')).to_contain_text('not one of the eight exact')
                self.assertEqual(field.evaluate('el=>el.files.length'),1)
                field.set_input_files(dict(name='frozen.json',mimeType='application/json',buffer=raw))
                form.get_by_role('button',name='REGISTER RESEARCH ARTIFACT',exact=True).click();expect(form.locator('output')).to_contain_text('NOT approved',timeout=15000)
                for width,height in ((1920,900),(1920,1080),(1366,768),(3840,2160),(768,1024),(430,900),(390,900),(375,900),(320,900)):
                    page.set_viewport_size(dict(width=width,height=height))
                    self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth-innerWidth'),1)
                    if (width,height) in ((1920,900),(375,900)):
                        page.locator('#frost-auto-title').scroll_into_view_if_needed()
                        page.screenshot(path=str(self.f.evidence/f'frost-phase13-{engine}-{width}.png'))
                        page.locator('#frost-departures-title').scroll_into_view_if_needed()
                        page.screenshot(path=str(self.f.evidence/f'frost-phase15-{engine}-{width}.png'))
                field.focus()
                response=page.request.get(self.f.origin+'/neosubzero/frost-vault/status')
                self.assertEqual(response.status,200);self.assertEqual(response.json()['auto_collection']['scored'],0)
                self.assertTrue(field.evaluate('el=>el===document.activeElement'))
                watcher=browser.new_page();self.f().login(watcher,username='drawer-watcher')
                self.assertEqual(watcher.request.get(self.f.origin+'/neosubzero/frost-vault/auto-evidence').status,403)
            finally:browser.close()
        with self.f.app.app_context():
            self.assertEqual(FrostModelArtifact.query.count(),0);self.assertEqual(FrostModelRelease.query.count(),0)
