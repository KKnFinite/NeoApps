"""Authenticated research receipts; no candidate approval or production data."""
from playwright.sync_api import expect
from tests.browser.test_frost_vault import FrostVaultBrowserTest


class ForecastBrowserTest(FrostVaultBrowserTest):
    def test_research_only_receipt_responsive_and_existing_upload_focus(self):
        for engine in ('chromium','webkit'):
            browser=getattr(self.f.pw,engine).launch(headless=True)
            try:
                page=browser.new_page(viewport={'width':1920,'height':900},service_workers='block')
                self.f().login(page);self.f().ready(page,'/neosubzero/frost-vault')
                panel=page.locator('[data-frost-research]')
                panel.locator(':scope > summary').click()
                expect(panel).to_contain_text('RESEARCH / NOT OPERATIONAL')
                expect(panel).to_contain_text('ABOVE-FREEZING WEAKNESS')
                expect(panel).to_contain_text('15.38%')
                expect(panel).to_contain_text('41.18%')
                expect(panel).to_contain_text('35.29%')
                expect(panel).to_contain_text('iem-rfd-routine-special-v2')
                expect(panel).to_contain_text('1126 / 84894 observations changed')
                expect(panel).to_contain_text('rfd-treatment-h1-20261027-v4')
                archive=panel.locator('details')
                archive.locator('summary').click()
                expect(archive).to_be_visible()
                expect(archive).to_contain_text('rfd-treatment-h1-20261027-v1')
                archive.locator('summary').click()
                expect(panel).to_contain_text('70 incomplete 12-hour-window records')
                self.assertEqual(panel.get_by_role('button').count(),0)
                field=page.get_by_label('IMPORT CSV',exact=True);field.focus()
                page.wait_for_function("document.querySelector('[data-frost-vault]') !== null")
                self.assertTrue(field.evaluate('el=>el===document.activeElement'))
                for width,height in ((1920,900),(1920,1080),(1366,768),(3840,2160),(1024,768),(768,1024),(430,900),(390,900),(375,900),(320,900)):
                    page.set_viewport_size({'width':width,'height':height})
                    self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth-innerWidth'),1)
                    boxes=panel.locator('section').all()
                    for box in boxes:
                        bounds=box.bounding_box()
                        if bounds:self.assertGreaterEqual(bounds['x'],0);self.assertLessEqual(bounds['x']+bounds['width'],width+1)
                    panel.scroll_into_view_if_needed()
                    if (width,height) in ((1920,900),(375,900)):
                        page.screenshot(path=str(self.f.evidence/f'frost-phase8-{engine}-{width}x{height}-viewport.png'))
                    page.screenshot(path=str(self.f.evidence/f'frost-forecast-{engine}-{width}x{height}.png'),full_page=True)
                response=page.request.get(self.f.origin+'/neosubzero/frost-vault/forecast-validation')
                self.assertEqual(response.status,200)
                self.assertFalse(response.json()['operationally_validated'])
                expect(page.locator('[data-model-approved]')).to_have_text('NONE')
                page.reload();expect(page.get_by_label('IMPORT CSV',exact=True)).to_be_visible()
                watcher=browser.new_page(service_workers='block')
                self.f().login(watcher,username='drawer-watcher')
                self.assertEqual(watcher.request.get(self.f.origin+'/neosubzero/frost-vault/forecast-validation').status,403)
            finally:browser.close()
