"""Authenticated private capture drill-down on phone/tablet/desktop engines."""
from unittest.mock import patch
from playwright.sync_api import expect
from tests.browser.test_frost_vault import FrostVaultBrowserTest
from app.extensions import db
from app.models import Gateway
from app.services import frost_auto as auto
from app.services.frost_weather import STATION


class PredictionBrowserTest(FrostVaultBrowserTest):
    def test_verified_history_scores_unavailable_failed_read_and_responsive(self):
        at=auto.stamp(auto.now())
        scores=[dict(hours=h,model_version=f'fixture-{kind}-h{h}',model_sha256='a'*64,
            target_at_utc=at,threshold=.5,classification=True,score=.7,state='SCORED',
            vector=dict(missing=['aircraft_exposure_hours'])) for h in (0,1,3,6) for kind in ('logistic','forest')]
        scores[-1].update(state='UNAVAILABLE',score=None,reason='Provider missing field')
        with self.f.app.app_context():
            gateway=Gateway.query.first()
            with patch.object(auto,'acquire',return_value=dict(station=STATION,cutoff_at_utc=at,
                    observations=[dict(utc=at,temperature_c=-2,dewpoint_c=-3,wind_kt=4,raw_metar='KRFD fixture')],
                    sources=[],native_forecasts=[],native_point=dict(cycle_at_utc=at))),patch.object(auto,'predictions',return_value=scores):
                self.assertEqual(auto.tick(gateway.id),1)
        for engine in ('chromium','webkit'):
            browser=getattr(self.f.pw,engine).launch(headless=True)
            try:
                page=browser.new_page(viewport=dict(width=1920,height=900),service_workers='block')
                self.f().login(page);self.f().ready(page,'/neosubzero/frost-vault')
                section=page.locator('[data-frost-predictions]')
                section.get_by_text('RECENT HOURLY COVERAGE',exact=False).click()
                section.locator('[data-prediction-url]').first.click()
                expect(section.locator('[data-prediction-feedback]')).to_contain_text('Private evidence verified')
                expect(section.locator('.frost-prediction-grid article')).to_have_count(4)
                expect(section).to_contain_text('Logistic: 0.7000')
                expect(section).to_contain_text('Forest: UNAVAILABLE')
                expect(section).to_contain_text('H0 · CURRENT RECOGNITION')
                for width,height in ((1920,900),(1920,1080),(1366,768),(3840,2160),(768,1024),(430,900),(390,900),(375,900),(320,900)):
                    page.set_viewport_size(dict(width=width,height=height))
                    self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth-innerWidth'),1)
                    if (width,height) in ((1920,900),(375,900)):
                        section.scroll_into_view_if_needed()
                        page.screenshot(path=str(self.f.evidence/f'frost-phase17-{engine}-{width}.png'))
                page.route('**/frost-vault/auto-evidence/*',lambda route:route.fulfill(status=503,body='Deferred'))
                section.locator('[data-prediction-url]').first.click()
                expect(section.locator('[data-prediction-feedback]')).to_contain_text('503')
                expect(section).to_contain_text('Logistic: 0.7000')
                denied=browser.new_page();self.f().login(denied,username='drawer-watcher')
                self.assertEqual(denied.request.get(self.f.origin+'/neosubzero/frost-vault/auto-evidence').status,403)
            finally:browser.close()
