"""Measured canonical handoff -> visible COMPLETE, with a different dirty row."""
import json
import time
import unittest
from datetime import timedelta
from pathlib import Path
from threading import Thread
from urllib.parse import urlsplit, parse_qsl, urlencode, urlunsplit
from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server
from app.extensions import db
from tests import test_neoscorpion_fueler_submission as fixtures


class CriticalLatencyBrowserTest(unittest.TestCase):
    def exercise(self, engine):
        metrics=[]
        for mode in ('before','after'):
            case=fixtures.SubmissionTest();case.setUp()
            app=case.client.application
            app.config.update(LIVE_SCREEN_REFRESH_INTERVAL_MS=5000,GOOGLE_MOTHERBRAIN_IMPORT_ENABLED=False,
                GOOGLE_MOTHERBRAIN_READER_ENABLED=False,FLIGHT_API_ENABLED=False)
            case.prepare()
            other=case.fixture._mission('UPS-DRAFT','N492UP',25000,1)
            db.session.commit();other_id=other.id;mission_id=case.mission.id
            server=make_server('127.0.0.1',0,app,threaded=True)
            thread=Thread(target=server.serve_forever,daemon=True);thread.start()
            origin=f'http://127.0.0.1:{server.server_port}'
            try:
                with sync_playwright() as pw:
                    browser=getattr(pw,engine).launch()
                    try:
                        page=browser.new_page(viewport={'width':1920,'height':900},service_workers='block')
                        if mode=='before':
                            def legacy(route):
                                parts=urlsplit(route.request.url)
                                args=[(k,v) for k,v in parse_qsl(parts.query) if k not in ('delta','fingerprints','operation_id')]
                                route.continue_(url=urlunsplit(parts._replace(query=urlencode(args))))
                            page.route('**/fuel-dispatch/live-panel?*completion_only*',legacy)
                        page.goto(origin+'/login');page.locator('[name=email]').fill(case.dispatcher.email)
                        page.locator('[name=password]').fill('TestPassword123!');page.locator('button[type=submit]').click()
                        page.wait_for_url(lambda u:'/login' not in u)
                        page.goto(origin+'/neoscorpion/fuel-dispatch')
                        draft=page.locator(f'[data-dispatch-mission-id="{other_id}"] [name=required_fuel]')
                        draft.fill('24.0')
                        button=page.locator(f'[data-dispatch-mission-id="{mission_id}"] button[formaction$="/complete"]')
                        self.assertEqual(button.count(),0)
                        full=[]
                        page.on('request',lambda r:full.append(r.url) if '/fuel-dispatch/live-panel' in r.url and 'completion_only' not in r.url else None)
                        with page.expect_response(lambda r:'/fuel-dispatch/live-panel' in r.url and 'completion_only' in r.url,timeout=15000) as critical:
                            case.save(case.start+timedelta(minutes=10),**case.final_data());case.off();case.submit()
                            committed=time.perf_counter()
                        expect(button).to_be_enabled(timeout=15000)
                        latency=time.perf_counter()-committed
                        expect(draft).to_have_value('24.0')
                        self.assertEqual(full,[])
                        metrics.append(dict(engine=engine,mode=mode,visible_complete_seconds=latency,
                            critical_bytes=len(critical.value.body()),full_panel_gets=len(full)))
                    finally:browser.close()
            finally:
                server.shutdown();thread.join(5);case.doCleanups()
        Path(f'instance/task13-latency-{engine}.json').write_text(json.dumps(metrics),encoding='utf8')

    def test_chromium(self):self.exercise('chromium')
    def test_webkit(self):self.exercise('webkit')
