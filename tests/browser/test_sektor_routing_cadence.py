"""One real Driver workflow with the production cadence guard enabled."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from threading import Thread
from unittest.mock import patch
from uuid import uuid4

from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server
from app import create_app
from app.extensions import db
from app.models import LiveScreenRefreshSetting, User
from app.services.live_refresh_guard import reset_live_refresh_guard_for_testing
from app.services.neosektor_live_counts import NeoSektorOperationalStateBundle as Bundle
from tests import test_neosektor_poll_queries as cases
from tests import test_neosektor_routes as fixtures


class RoutingCadenceBrowserTest(unittest.TestCase):
    def test_committed_change_respects_cadence_and_remains_live(self):
        evidence = Path('instance/refresh-efficiency-20261105')
        evidence.mkdir(parents=True, exist_ok=True)
        database = Path(tempfile.gettempdir()) / ('sektor-cadence-' + uuid4().hex + '.db')

        def local(config):
            return create_app(type('RoutingCadenceConfig', (config,), dict(
                SQLALCHEMY_DATABASE_URI='sqlite:///' + database.as_posix(),
                SESSION_COOKIE_SECURE=False, TEST_LIVE_REFRESH_RATE_LIMIT_ENABLED=True,
                SQLALCHEMY_ENGINE_OPTIONS={'connect_args': {'check_same_thread': False, 'timeout': 5}},
            )))

        case = cases.PollQueriesTest()
        with patch.object(fixtures, 'create_app', local):
            case.setUp()
        self.addCleanup(case.tearDown)
        Bundle.load(case.fixture.gateway, sort_date=case.day, include_routing=True)
        db.session.add(LiveScreenRefreshSetting(gateway_id=case.fixture.gateway.id,
            screen_key='neosektor.driver_routing', interval_seconds=5))
        db.session.commit()
        email = User.query.filter_by(username='sektor_simulator_user').one().email
        server = make_server('127.0.0.1', 0, case.fixture.app, threaded=True)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), thread.join(5)))
        self.addCleanup(reset_live_refresh_guard_for_testing)
        origin = f'http://127.0.0.1:{server.server_port}'
        results = {}
        with sync_playwright() as pw, pw.chromium.launch() as browser:
            for reference in (True, False):
                reset_live_refresh_guard_for_testing()
                with browser.new_context(viewport={'width':390,'height':844}, service_workers='block') as context:
                    if reference:
                        source = Path('app/static/js/neosektor_routing_watch.js').read_text(encoding='utf-8').replace(
                            'requestRefresh ? await requestRefresh() : await refresh()', 'await refresh()')
                        context.route('**/js/neosektor_routing_watch.js*', lambda route: route.fulfill(
                            body=source, content_type='application/javascript'))
                    context.add_init_script("""{
                        let live;
                        Object.defineProperty(window, 'NeoLiveUpdates', {
                            get: () => live,
                            set(value) {
                                live = {...value, create: options => {
                                    const controller = value.create(options);
                                    if (options.poll.name === 'refreshState') window.cadenceController = controller;
                                    return controller;
                                }};
                            }
                        });
                    }""")
                    page = context.new_page()
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.goto(origin+'/login')
                    page.locator('[name=email]').fill(email)
                    page.locator('[name=password]').fill('TestPassword123!')
                    page.locator('button[type=submit]').click()
                    page.wait_for_url(lambda url: '/login' not in url)
                    page.goto(origin+'/neosektor/driver-routing')
                    requests = []
                    page.on('response', lambda response: requests.append(dict(
                        status=response.status,
                        # The real transport deliberately cancels a 429 body.
                        # Flask's Content-Length measures that generated body;
                        # successful canonical bodies are actually read here.
                        bytes=int(response.headers['content-length']) if response.status==429 else len(response.body()),
                        at=time.perf_counter()))
                        if '/driver-routing/state?' in response.url else None)
                    self.assertTrue(page.evaluate('cadenceController.refreshNow()'))
                    target = 'west' if reference else 'east'
                    started = time.perf_counter()
                    response = case.fixture.client.post('/neosektor/tunnel-conductor/settings', json={'first_override':target})
                    self.assertEqual(response.status_code, 200)
                    try:
                        expect(page.locator('[data-driver-wave="first"]')).to_have_attribute('data-direction', target, timeout=6500)
                    except AssertionError:
                        (evidence/'browser-debug.json').write_text(json.dumps(dict(
                            post=response.json, responses=requests, errors=errors,
                            controller=page.evaluate('({enabled:cadenceController.enabled, interval:cadenceController.intervalMs, running:cadenceController.running, retryAt:cadenceController.retryAt, now:Date.now()})'),
                        ),indent=2),encoding='utf-8')
                        raise
                    latency = round((time.perf_counter()-started)*1000, 2)
                    page.wait_for_timeout(max(0, 5600-latency))
                    self.assertFalse(page.evaluate('document.documentElement.scrollWidth>innerWidth+1'))
                    name = 'before' if reference else 'after'
                    page.screenshot(path=str(evidence/(name+'-driver-mobile.png')), full_page=True)
                    results[name] = dict(requests=len(requests), throttled=sum(r['status']==429 for r in requests),
                        bytes=sum(r['bytes'] for r in requests), latency_ms=latency,
                        responses=[dict(status=r['status'],bytes=r['bytes']) for r in requests], errors=errors)
                    self.assertEqual(errors, [])
        (evidence/'browser-cadence.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
        self.assertGreater(results['before']['throttled'], 0)
        self.assertEqual(results['after']['throttled'], 0)
        self.assertEqual(results['after']['requests'], 2)
        self.assertLess(results['after']['requests'], results['before']['requests'])
        self.assertLess(results['after']['bytes'], results['before']['bytes'])
        self.assertLess(results['after']['latency_ms'], 6500)
