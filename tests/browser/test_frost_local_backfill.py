"""Retired bulk publication remains inaccessible; local evidence is retained."""
from playwright.sync_api import expect
from tests.browser.test_frost_vault import FrostVaultBrowserTest


class LocalBackfillBrowserTest(FrostVaultBrowserTest):
    def test_bulk_publication_retired_without_disabling_csv_import(self):
        for engine in ('chromium','webkit'):
            browser=getattr(self.f.pw,engine).launch(headless=True)
            try:
                page=browser.new_page(viewport={'width':375,'height':900},service_workers='block')
                self.f().login(page);self.f().ready(page,'/neosubzero/frost-vault')
                expect(page.get_by_label('IMPORT CSV',exact=True)).to_be_visible()
                self.assertEqual(page.locator('[data-local-backfill]').count(),0)
                self.assertEqual(page.get_by_role('button',name='PUBLISH LOCAL RESULT',exact=True).count(),0)
                self.assertEqual(page.request.get(self.f.origin+'/neosubzero/frost-vault/backfill/1').status,410)
            finally:browser.close()
