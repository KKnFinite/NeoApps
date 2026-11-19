"""Small isolated settings workflow; never touches production controls."""
from pathlib import Path
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest import TestCase
from unittest.mock import patch

from playwright.sync_api import expect
from app.extensions import db
from app.models import Gateway, GatewayMembership, GatewayNodeRole, NeoNode, User, SortDateOperation
from app.services import frost_ducc
from tests.browser import test_neosubzero_ucc as fixtures
from tests.test_frost_ducc import store_capture


class IntelligenceBrowserTest(TestCase):
    def test_committed_scores_refresh_without_an_operational_sort_or_page_reload(self):
        from contextlib import ExitStack
        from datetime import time
        from app.services import neosubzero_weather as weather
        from app.services.live_screen_refresh import save_live_screen_refresh_override
        from tests.browser import test_mobile_drawer
        from tests.frost_schedule_fixture import configure
        from tests.test_frost_ducc import AT
        for engine in ('chromium','webkit'):
            with self.subTest(engine=engine):
                case=test_mobile_drawer.MobileDrawerBrowserTest();case.setUpClass();browser=None
                try:
                    with case.app.app_context():
                        gateway=Gateway.query.filter_by(code='RFD').one()
                        configure(gateway,days=('wednesday',),start=time(21),end=time(5))
                        save_live_screen_refresh_override(gateway,'neosubzero.ucc',5)
                        db.session.commit()
                        gateway_id=gateway.id
                    periods=[]
                    for i in range(8):
                        start=AT.replace(minute=0)+timedelta(hours=i)
                        periods.append(dict(startTime=start.isoformat(),endTime=(start+timedelta(hours=1)).isoformat(),
                            temperature=30,temperatureUnit='F',shortForecast='Clear'))
                    snapshot=dict(metar=dict(value=[],stale=False),forecast=dict(value=dict(hourly=dict(properties=dict(periods=periods))),stale=False))
                    context=weather.neosubzero_weather_context;revision=weather.neosubzero_weather_revision
                    with ExitStack() as stack:
                        stack.enter_context(patch.object(weather,'_weather_snapshot',return_value=snapshot))
                        stack.enter_context(patch('app.neonodes.neosubzero.routes.neosubzero_weather_context',
                            side_effect=lambda gw,op:context(gw,op,now=AT+timedelta(minutes=1))))
                        stack.enter_context(patch('app.neonodes.neosubzero.routes.neosubzero_weather_revision',
                            side_effect=lambda **kw:revision(gateway=kw['gateway'],now=AT+timedelta(minutes=1))))
                        browser=getattr(case.pw,engine).launch()
                        admin=browser.new_page();case.login(admin);case.ready(admin,'/neosubzero/settings')
                        form=admin.locator('form[action$="/frost-vault/intelligence-settings"]')
                        form.locator('[name="operational_predictions"]').check()
                        form.locator('[name="confirm_operational"]').check()
                        with admin.expect_navigation():form.get_by_role('button',name='SAVE FROST INTELLIGENCE').click()
                        page=browser.new_page(viewport={'width':1440,'height':1000},service_workers='block')
                        case.login(page,'drawer-watcher');case.ready(page,'/neosubzero/ucc')
                        expect(page.locator('[data-frost-predictions]')).to_have_count(0)
                        expect(page.locator('.neosubzero-weather-current-sort article')).to_have_count(8)
                        with case.app.app_context():
                            self.assertEqual(SortDateOperation.query.count(),0)
                            store_capture(gateway_id)
                        expect(page.locator('[data-frost-predictions]')).to_have_count(4,timeout=20000)
                        expect(page.locator('[data-frost-predictions]').first).to_have_text('FROST 12% / 34%')
                        with case.app.app_context():self.assertEqual(SortDateOperation.query.count(),0)
                        page.screenshot(path=f'instance/frost-preflight-20261118/{engine}-no-operation.png')
                finally:
                    if browser:browser.close()
                    case.tearDownClass()

    def test_authorized_toggle_live_ducc_tv_and_master_readonly(self):
        evidence=Path('instance/browser-evidence/frost-intelligence');evidence.mkdir(parents=True,exist_ok=True)
        for engine in ('chromium','webkit'):
            with self.subTest(engine=engine):
                case=fixtures.UccBrowserTest();case.setUpClass();browser=None;weather_patch=None;tv_refresh=None
                try:
                    at=datetime(2026,10,23,5,20,tzinfo=timezone.utc)
                    with case.app.app_context():
                        watcher=User.query.filter_by(username='drawer-watcher').one()
                        membership=GatewayMembership.query.filter_by(user_id=watcher.id).one()
                        node=NeoNode.query.filter_by(code='subzero').one()
                        GatewayNodeRole.query.filter_by(gateway_membership_id=membership.id,node_id=node.id).one().role='master'
                        db.session.commit()
                        gateway=Gateway.query.filter_by(code='RFD').one()
                        store_capture(gateway.id,at)
                    def weather(gateway,operation):
                        value=deepcopy(case.weather_data)
                        version,scores=frost_ducc.project(gateway,now=at+timedelta(minutes=1))
                        for i,hour in enumerate(value['current_sort']['hours']):
                            hour['start']=at.replace(minute=0)+timedelta(hours=i)
                        frost_ducc.decorate((value['current_sort'],),scores)
                        value['revision']=version
                        return value
                    weather_patch=patch('app.neonodes.neosubzero.routes.neosubzero_weather_context',side_effect=weather)
                    weather_patch.start()
                    # Keep both existing poll paths at a short synthetic interval.
                    tv_refresh=patch('app.neonodes.neosubzero.routes.live_screen_refresh_status',
                        return_value={'live_screen_refresh_interval_ms':1000})
                    tv_refresh.start()
                    case.weather_revision_mock.side_effect=lambda **kw:frost_ducc.revision(kw['gateway'],now=at+timedelta(minutes=1))
                    browser=getattr(case.pw,engine).launch()
                    page=browser.new_page(viewport={'width':1366,'height':900},service_workers='block')
                    case.login(page);case.ready(page,'/neosubzero/settings')
                    form=page.locator('form[action$="/frost-vault/intelligence-settings"]')
                    expect(form.get_by_role('switch')).to_have_count(3)
                    for toggle in form.get_by_role('switch').all():
                        expect(toggle).not_to_be_checked();expect(toggle).to_be_enabled()
                    for width in (1366,390):
                        page.set_viewport_size({'width':width,'height':900})
                        self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth-innerWidth'),1)
                        page.screenshot(path=str(evidence/f'{engine}-{width}.png'))
                    ducc=browser.new_page(viewport={'width':1920,'height':1080},service_workers='block')
                    case.login(ducc);case.ready(ducc,'/neosubzero/ucc')
                    expect(ducc.locator('[data-frost-predictions]')).to_have_count(0)
                    form.locator('[name="operational_predictions"]').check()
                    form.locator('[name="confirm_operational"]').check()
                    with page.expect_navigation():form.get_by_role('button',name='SAVE FROST INTELLIGENCE').click()
                    expect(form.locator('[name="operational_predictions"]')).to_be_checked()
                    expect(form.locator('[name="automatic_retraining"]')).not_to_be_checked()
                    expect(form.locator('[name="learn_from_live_operations"]')).not_to_be_checked()
                    expect(ducc.locator('[data-frost-predictions]')).to_have_count(4,timeout=15000)
                    expect(ducc.locator('[data-frost-predictions]').first).to_have_text('FROST 12% / 34%')
                    ducc.screenshot(path=str(evidence/f'{engine}-ducc-on.png'))
                    ducc.evaluate('document.documentElement.requestFullscreen = undefined')
                    ducc.locator('[data-ucc-tv-enter]').click()
                    tv=ducc.locator('.ucc-tv-wallboard')
                    expect(tv).to_contain_text('FROST 12% / 34%')
                    self.assertLessEqual(ducc.evaluate('document.documentElement.scrollWidth-innerWidth'),1)
                    self.assertLessEqual(ducc.evaluate('document.documentElement.scrollHeight-innerHeight'),1)
                    ducc.screenshot(path=str(evidence/f'{engine}-tv-on.png'))
                    form.locator('[name="operational_predictions"]').uncheck()
                    with page.expect_navigation():form.get_by_role('button',name='SAVE FROST INTELLIGENCE').click()
                    expect(ducc.locator('[data-frost-predictions]')).to_have_count(0,timeout=15000)
                    expect(tv).not_to_contain_text('FROST 12% / 34%')
                    tv.get_by_role('button',name='Exit TV Mode').click()
                    ducc.set_viewport_size({'width':390,'height':844})
                    ducc.locator('[data-ucc-view="ramp"]').click()
                    expect(ducc.locator('[data-ucc-mobile-projection]')).to_be_visible()
                    self.assertLessEqual(ducc.evaluate('document.documentElement.scrollWidth-innerWidth'),1)
                    ducc.screenshot(path=str(evidence/f'{engine}-mobile.png'))
                    other=browser.new_page(viewport={'width':390,'height':844},service_workers='block')
                    case.login(other,'drawer-watcher');case.ready(other,'/neosubzero/settings')
                    controls=other.locator('form[action$="/frost-vault/intelligence-settings"]')
                    for toggle in controls.get_by_role('switch').all():expect(toggle).to_be_disabled()
                    expect(controls.get_by_role('button',name='SAVE FROST INTELLIGENCE')).to_have_count(0)
                    expect(controls.locator('[name="automatic_retraining"]')).not_to_be_checked()
                finally:
                    if browser:browser.close()
                    if weather_patch:weather_patch.stop()
                    if tv_refresh:tv_refresh.stop()
                    case.tearDownClass()
