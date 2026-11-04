"""Essential Chromium proof: small SPEAR refresh and draft-safe handoff."""
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from threading import Thread
import json
import time
import unittest
from unittest.mock import patch

from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server
from app.extensions import db
from app.models import NeoScorpionSortAssetState
from app.services import neoscorpion as fuel
from app.services.live_screen_refresh import live_screen_refresh_value
from tests import test_dispatch_refresh_fragments as fixtures


class NeoScorpionBandwidthBrowserTest(unittest.TestCase):
    def test_chromium_spear_refresh_and_dirty_dispatch_completion(self):
        case=fixtures.DispatchFragmentsTest();case.setUp();self.addCleanup(case.doCleanups)
        case.prepare();case.save(case.start+timedelta(minutes=10),**case.final_data())
        case.settings.spear_recommendations_enabled=True;db.session.commit()
        app=case.client.application;app.config['SESSION_COOKIE_SECURE']=False
        server=make_server('127.0.0.1',0,app,threaded=True)
        thread=Thread(target=server.serve_forever,daemon=True);thread.start()
        origin=f'http://127.0.0.1:{server.server_port}'
        evidence=Path('instance/scorpion-performance-20261104/browser');evidence.mkdir(parents=True,exist_ok=True)
        errors=[];requests=[]
        # Capture the actual controller's poll for deterministic scheduling; no
        # replacement refresh implementation, timing change or application hook.
        wrapper='''
const originalLive=NeoLiveUpdates;
window.NeoLiveUpdates=Object.freeze({...originalLive,create:options=>{const result=originalLive.create(options);
window.__scorpionPoll=options.poll;window.__scorpionController=result;return result;}});
'''
        script=Path('app/static/js/live_updates.js').read_text()+wrapper
        try:
            with patch('app.services.neoscorpion.live_screen_refresh_value',side_effect=lambda gateway,key,**kw:
                       replace(live_screen_refresh_value(gateway,key,**kw),effective_interval_ms=5000)),sync_playwright() as pw:
                browser=pw.chromium.launch()
                context=browser.new_context(viewport={'width':1440,'height':950},service_workers='block')
                page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
                page.route('**/js/live_updates.js*',lambda route:route.fulfill(body=script,content_type='application/javascript'))
                page.add_init_script('''window.__timeOffset=0;const originalNow=Date.now;Date.now=()=>originalNow()+window.__timeOffset;''')
                page.on('request',lambda r:requests.append(r.url) if 'live-panel' in r.url else None)
                page.goto(origin+'/login');page.locator('[name=email]').fill(case.dispatcher.email)
                page.locator('[name=password]').fill('TestPassword123!');page.locator('button[type=submit]').click()
                page.wait_for_url(lambda url:'/login' not in url)
                page.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1','seen')")
                page.goto(origin+'/neoscorpion/fuel-dispatch')
                page.wait_for_function('window.__scorpionPoll !== undefined')
                page.evaluate('window.__scorpionController.destroy()')
                page.evaluate('window.__scorpionPoll()') # Establish current tail/revision fingerprint.
                row=page.locator(f'.neoscorpion-dispatch-primary-row[data-dispatch-mission-id="{case.mission.id}"]')
                row.evaluate('e=>{window.__originalOperationalRow=e;}')
                before=len(requests)
                started=time.perf_counter()
                page.evaluate('window.__timeOffset+=120001;window.__scorpionPoll()')
                spear_ms=round((time.perf_counter()-started)*1000,2)
                timed=requests[before:]
                self.assertEqual(len(timed),1,timed);self.assertIn('spear_only=1',timed[0])
                self.assertTrue(row.evaluate('e=>e===window.__originalOperationalRow'))
                expect(row.get_by_role('button',name='COMPLETE',exact=True)).to_have_count(0)
                details=row.locator('[data-neoscorpion-dispatch-details]');details.click()
                panel=page.locator('#'+details.get_attribute('aria-controls'))
                notes=panel.locator('[data-console-section="tail-notes"]');notes.locator('summary').click()
                draft=notes.locator('textarea');draft.fill('Unsaved Dispatcher notes survive SPEAR and completion');draft.focus()
                # Canonical, isolated Fueler OFF and COMPLETE ASSIGNMENT. GET
                # refreshes must reveal Dispatcher eligibility without posting.
                case.off();case.submit()
                before=len(requests)
                started=time.perf_counter()
                page.evaluate('window.__timeOffset+=5001;window.__scorpionPoll()')
                completion_ms=round((time.perf_counter()-started)*1000,2)
                changed=requests[before:]
                self.assertEqual(len(changed),1,changed);self.assertIn('completion_only=1',changed[0])
                expect(row.get_by_role('button',name='COMPLETE',exact=True)).to_be_visible()
                expect(draft).to_have_value('Unsaved Dispatcher notes survive SPEAR and completion')
                expect(draft).to_be_focused()
                page.screenshot(path=str(evidence/'dispatch-draft-and-complete.png'),full_page=True)
                # Clearing the draft permits one full refresh, never a critical
                # plus full duplicate. A subsequent unchanged poll downloads none.
                draft.fill('');draft.blur();before=len(requests)
                page.evaluate('window.__timeOffset+=5001;window.__scorpionPoll()')
                clean=requests[before:];self.assertEqual(len(clean),1,clean)
                self.assertNotIn('completion_only',clean[0]);self.assertNotIn('spear_only',clean[0])
                before=len(requests);page.evaluate('window.__timeOffset+=5001;window.__scorpionPoll()')
                self.assertEqual(requests[before:],[])
                page.set_viewport_size({'width':390,'height':844})
                self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth+1'))
                page.screenshot(path=str(evidence/'dispatch-mobile.png'),full_page=True)
                page.goto(origin+'/neoscorpion/hanzo')
                page.wait_for_function('window.__scorpionPoll !== undefined')
                page.evaluate('window.__scorpionController.destroy();window.__hanzoDocument=123')
                for _ in range(2):
                    state=NeoScorpionSortAssetState.query.filter_by(sort_date_operation_id=case.operation.id).one()
                    state.revision+=1;db.session.commit()
                    before=len(requests)
                    page.evaluate('window.__timeOffset+=5001;window.__scorpionPoll()')
                    self.assertEqual(len(requests[before:]),1)
                    self.assertIn('/hanzo/live-panel',requests[-1])
                    self.assertEqual(page.evaluate('window.__hanzoDocument'),123)
                page.screenshot(path=str(evidence/'hanzo-mobile.png'),full_page=True)
                receipt=dict(timed_spear_requests=timed,dirty_completion_requests=changed,clean_refresh_requests=clean,
                    unchanged_panel_requests=0,spear_refresh_ms=spear_ms,completion_visible_ms=completion_ms,
                    hanzo_document_preserved=True,javascript_errors=errors)
                (evidence/'receipt.json').write_text(json.dumps(receipt,indent=2))
                self.assertEqual(errors,[])
                browser.close()
        finally:server.shutdown();thread.join(timeout=5)
