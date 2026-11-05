"""Two real visible Driver screens, scoped hints and canonical live routing."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from threading import Thread
from unittest.mock import patch
from uuid import uuid4
from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server
from app import create_app
from app.extensions import db
from app.models import LiveScreenRefreshSetting, User
from app.services.neosektor_live_counts import NeoSektorOperationalStateBundle as Bundle
from tests import test_neosektor_poll_queries as cases
from tests import test_neosektor_routes as fixtures


class SektorPerformanceBrowserTest(unittest.TestCase):
    def setUp(self):
        self.evidence=Path('instance/sektor-ermac-performance-20261104/browser');self.evidence.mkdir(parents=True,exist_ok=True)
        database=Path(tempfile.gettempdir())/('sektor-perf-'+uuid4().hex+'.db')
        def local(config):
            return create_app(type('SektorBrowserConfig',(config,),dict(
                SQLALCHEMY_DATABASE_URI='sqlite:///'+database.as_posix(),SESSION_COOKIE_SECURE=False,
                SQLALCHEMY_ENGINE_OPTIONS={'connect_args':{'check_same_thread':False,'timeout':5}})))
        self.case=cases.PollQueriesTest()
        with patch.object(fixtures,'create_app',local):self.case.setUp()
        self.addCleanup(self.case.tearDown);c=self.case
        Bundle.load(c.fixture.gateway,sort_date=c.day,include_routing=True)
        db.session.add(LiveScreenRefreshSetting(gateway_id=c.fixture.gateway.id,screen_key='neosektor.driver_routing',interval_seconds=5))
        db.session.commit();self.email=User.query.filter_by(username='sektor_simulator_user').one().email
        self.server=make_server('127.0.0.1',0,c.fixture.app,threaded=True)
        self.thread=Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.stop)
        self.origin=f'http://127.0.0.1:{self.server.server_port}'
        self.pw=self.enterContext(sync_playwright());self.browser=self.pw.chromium.launch();self.addCleanup(self.browser.close)

    def stop(self):self.server.shutdown();self.thread.join(5)

    def test_same_session_hints_keep_safety_poll_and_live_changes_desktop_mobile(self):
        results={};errors=[]
        for shared in (False,True):
            context=self.browser.new_context(viewport={'width':1440,'height':950},service_workers='block')
            try:
                if not shared:
                    source=Path('app/static/js/neosektor_routing_watch.js').read_text(encoding='utf-8').replace(
                        'window.NeoLiveRevision?.revision || window.NeoLiveRevision?.fetch || fetch','fetch')
                    context.route('**/js/neosektor_routing_watch.js*',lambda route:route.fulfill(body=source,content_type='application/javascript'))
                first=context.new_page();first.goto(self.origin+'/login')
                first.locator('[name=email]').fill(self.email);first.locator('[name=password]').fill('TestPassword123!')
                first.locator('button[type=submit]').click();first.wait_for_url(lambda url:'/login' not in url)
                pages=[first,context.new_page()];responses=[]
                for page in pages:
                    page.on('pageerror',lambda e:errors.append(str(e)))
                    page.on('response',lambda r:responses.append(dict(path=r.url.split(self.origin)[-1],status=r.status,bytes=len(r.body())))
                        if '/driver-routing/version?' in r.url or '/driver-routing/state?' in r.url else None)
                    page.goto(self.origin+'/neosektor/driver-routing')
                    self.assertFalse(page.evaluate('document.hidden'))
                responses.clear();first.wait_for_timeout(6500)
                hints=[r for r in responses if '/version?' in r['path']];states=[r for r in responses if '/state?' in r['path']]
                results['shared' if shared else 'reference']=dict(hint_requests=len(hints),hint_bytes=sum(r['bytes'] for r in hints),
                    safety_requests=len(states),responses=responses)
                self.assertGreaterEqual(len(states),2)  # Five-second safety reconciliation remains active in both tabs.
                if shared:
                    started=time.perf_counter()
                    response=self.case.fixture.client.post('/neosektor/tunnel-conductor/settings',json={'first_override':'west'})
                    self.assertEqual(response.status_code,200)
                    for page in pages:
                        expect(page.locator('[data-driver-wave="first"]')).to_have_attribute('data-direction','west',timeout=8000)
                    results['live_change_visible_ms']=round((time.perf_counter()-started)*1000,2)
                    pages[0].close()  # Losing the first hint participant must not stop the remaining tab.
                    response=self.case.fixture.client.post('/neosektor/tunnel-conductor/settings',json={'first_override':'east'})
                    self.assertEqual(response.status_code,200)
                    expect(pages[1].locator('[data-driver-wave="first"]')).to_have_attribute('data-direction','east',timeout=8000)
                    pages[1].set_viewport_size({'width':390,'height':844})
                    self.assertTrue(pages[1].evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
                    pages[1].screenshot(path=str(self.evidence/'driver-mobile.png'),full_page=True)
            finally:context.close()
        (self.evidence/'routing-receipt.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
        self.assertLessEqual(results['shared']['hint_requests'],results['reference']['hint_requests']-2)
        self.assertLess(results['shared']['hint_bytes'],results['reference']['hint_bytes'])
        self.assertEqual(errors,[]);results['errors']=errors
        (self.evidence/'routing-receipt.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
