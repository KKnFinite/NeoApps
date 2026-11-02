"""Two real authenticated tabs, Chromium/WebKit; no provider or production I/O."""
import json
import subprocess
import time
import unittest
from pathlib import Path
from tests.browser import test_mobile_drawer as fixtures


class MultiTabRevisionBrowserTest(unittest.TestCase):
    def test_equivalent_visible_tabs_and_owner_close(self):
        fixture=fixtures.MobileDrawerBrowserTest
        fixture.setUpClass()
        fixture.app.config['LIVE_SCREEN_REFRESH_INTERVAL_MS']=5000
        metrics=[]
        try:
            for engine in ('chromium','webkit'):
                browser=getattr(fixture.pw,engine).launch()
                try:
                    for mode in ('before','after'):
                        context=browser.new_context(viewport={'width':1920,'height':900})
                        requests=[]; errors=[]; responses=[]
                        if mode=='before':
                            context.route('**/js/live_revision.js*',lambda route:route.fulfill(content_type='application/javascript',body='/* Baseline: independent polling. */'))
                        pages=[context.new_page(),context.new_page()]
                        fixture().login(pages[0])
                        for page in pages:
                            page.on('request',lambda request:requests.append((time.perf_counter(),request.url)) if '/fuel-dispatch/revision' in request.url else None)
                            page.on('response',lambda response:responses.append(response) if '/fuel-dispatch/revision' in response.url else None)
                            page.on('pageerror',lambda error:errors.append(str(error)))
                            page.goto(fixture.origin+'/neoscorpion/fuel-dispatch')
                            self.assertFalse(page.evaluate('document.hidden'))
                        visible=sum(not page.evaluate('document.hidden') for page in pages)
                        # Three configured refresh opportunities, with no sort,
                        # mutations, browser clock hacks or operational payload sharing.
                        pages[1].wait_for_timeout(16200)
                        count=len(requests)
                        response_bytes=sum(len(response.body()) for response in responses)
                        # Browser-reported serialized request/response sizes;
                        # no guessed compression or fixed header allowance.
                        wire_bytes=sum(sum(response.request.sizes().values()) for response in responses)
                        if mode=='before':self.assertGreaterEqual(count,3)
                        else:
                            self.assertLessEqual(count,4)
                            pages[0].close()
                            previous=len(requests)
                            pages[1].wait_for_timeout(5600)
                            self.assertGreater(len(requests),previous)
                        if mode=='after' and engine=='webkit':
                            # Headless WebKit may expose only its front page as
                            # visible. Exercise native cross-tab messaging too,
                            # without falsifying document visibility semantics.
                            first=context.new_page();first.goto(fixture.origin+'/neoscorpion/fuel-dispatch')
                            start=len(requests)
                            pages[1].evaluate("async()=>await NeoLiveRevision.revision('/neoscorpion/fuel-dispatch/revision?native_probe=1',{},5000,'none')")
                            first.evaluate("async()=>await NeoLiveRevision.revision('/neoscorpion/fuel-dispatch/revision?native_probe=1',{},5000,'none')")
                            self.assertEqual(len(requests)-start,1)
                        self.assertFalse(errors,errors)
                        metrics.append(dict(engine=engine,mode=mode,visible_tabs=visible,revision_gets=count,response_bytes=response_bytes,http_bytes=wire_bytes,window_seconds=16.2))
                        print(json.dumps(metrics[-1]),flush=True)
                        context.close()
                finally:browser.close()
            Path('instance/task13-browser-metrics.json').write_text(json.dumps(metrics),encoding='utf8')
        finally:fixture.tearDownClass()
