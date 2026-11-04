"""Essential Chromium acceptance for canonical mission deltas and live races."""
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from threading import Thread
from uuid import uuid4
import json
import re
import tempfile
import unittest
from unittest.mock import patch

from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server
from app import create_app
from app.extensions import db
from app.models import (GatewayMembership, GatewayNodeRole, NeoNode, NeoScorpionFuelingEvent,
                        NeoScorpionSortAssetState, NeoScorpionSortTruck)
from app.services.live_screen_refresh import live_screen_refresh_value
from tests import test_dispatch_refresh_fragments as fixtures
from tests import test_neoscorpion_dispatch_planning as database_fixture

EVIDENCE = Path('instance/scorpion-phase3-20261104/browser')


class DispatchDeltaBrowserTest(unittest.TestCase):
    def setUp(self):
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        # Local disposable disk avoids network-share SQLite fsync delays.
        # Retain the synthetic file; do not clean up existing test work.
        database = Path(tempfile.gettempdir()) / ('neo-phase3-' + uuid4().hex + '.db')
        def local(config):
            return create_app(type('DeltaBrowserConfig', (config,), dict(
                SQLALCHEMY_DATABASE_URI='sqlite:///' + database.as_posix(),
                SQLALCHEMY_ENGINE_OPTIONS={'connect_args':{'check_same_thread':False, 'timeout':5}})))
        self.case = fixtures.DispatchFragmentsTest()
        with patch.object(database_fixture, 'create_app', local): self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        c = self.case
        c.prepare(); c.save(c.start + timedelta(minutes=10), **c.final_data())
        self.other = c.fixture._mission('UPS222', 'N222UP', 30000, 20)
        for i in range(5): c.fixture._mission('UPS'+str(300+i), 'N'+str(300+i)+'UP', 30000, 30+i)
        self.observer = c.fixture._login_user('delta-observer', 'simulator')
        c.settings.spear_recommendations_enabled = True
        db.session.commit()
        c.client.application.config['SESSION_COOKIE_SECURE'] = False
        self.server = make_server('127.0.0.1', 0, c.client.application, threaded=True)
        self.thread = Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.addCleanup(self.stop_server)
        self.origin = f'http://127.0.0.1:{self.server.server_port}'
        self.responses, self.errors = [], []
        self.enterContext(patch('app.services.neoscorpion.live_screen_refresh_value',side_effect=lambda gateway,key,**kw:
            replace(live_screen_refresh_value(gateway,key,**kw),effective_interval_ms=5000)))
        self.playwright = self.enterContext(sync_playwright())
        self.browser = self.playwright.chromium.launch(); self.addCleanup(self.browser.close)

    def stop_server(self):
        self.server.shutdown(); self.thread.join(timeout=5)

    def open(self, actor):
        context = self.browser.new_context(viewport={'width':1440,'height':950},service_workers='block')
        page = context.new_page()
        script = Path('app/static/js/live_updates.js').read_text(encoding='utf-8') + '''
const original=NeoLiveUpdates;
window.NeoLiveUpdates=Object.freeze({...original,create:options=>{const c=original.create(options);
window.__deltaPoll=options.poll;window.__deltaController=c;return c;}});
'''
        page.route('**/js/live_updates.js*', lambda route: route.fulfill(body=script,content_type='application/javascript'))
        helper=Path('app/static/js/neoscorpion_dispatch_delta.js').read_text(encoding='utf-8').replace(
            "throw new Error('Incomplete Dispatch mission manifest.');",
            "{window.__manifestFailure={rows:Array.from(rows,r=>r.dataset.dispatchMissionId),manifest:Object.keys(manifest)};throw new Error('Incomplete Dispatch mission manifest.');}")
        page.route('**/js/neoscorpion_dispatch_delta.js*',lambda route:route.fulfill(body=helper,content_type='application/javascript'))
        page.add_init_script('''window.__timeOffset=0;const realNow=Date.now;Date.now=()=>realNow()+window.__timeOffset;
const originalFetch=window.fetch;window.fetch=async(...args)=>{
const r=await originalFetch(...args);if(String(args[0]).includes('missions_delta=1') && window.__holdSnapshot){
window.__holdSnapshot=false;await new Promise(resolve=>window.__releaseSnapshot=resolve);}return r;};''')
        page.on('pageerror', lambda error:self.errors.append(str(error)))
        def response(r):
            if '/fuel-dispatch/live-panel' in r.url:
                self.responses.append(dict(url=r.url,status=r.status,bytes=len(r.body())))
        page.on('response', response)
        page.goto(self.origin+'/login'); page.locator('[name=email]').fill(actor.email)
        page.locator('[name=password]').fill('TestPassword123!'); page.locator('button[type=submit]').click()
        page.wait_for_url(lambda url:'/login' not in url)
        page.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1','seen')")
        page.goto(self.origin+'/neoscorpion/fuel-dispatch')
        page.wait_for_function('window.__deltaPoll !== undefined')
        page.evaluate('window.__deltaController.destroy()')
        page.evaluate('window.__deltaPoll()')
        return page

    def poll(self, *pages):
        for page in pages: page.evaluate('window.__timeOffset+=5001;void (window.__pollPromise=window.__deltaPoll())')
        for page in pages: page.evaluate('window.__pollPromise')
        for page in pages:
            failure=page.evaluate('window.__manifestFailure || null')
            if failure:
                (EVIDENCE/'manifest-failure.json').write_text(json.dumps(failure,indent=2),encoding='utf-8')
                self.fail('Manifest mismatch: '+str({k:v for k,v in failure.items() if k!='html'}))

    def primary(self, page, identifier):
        return page.locator(f'.neoscorpion-dispatch-primary-row[data-dispatch-mission-id="{identifier}"]')

    def bump(self):
        state = NeoScorpionSortAssetState.query.filter_by(sort_date_operation_id=self.case.operation.id).one()
        state.revision += 1; db.session.commit()

    def test_two_sessions_completion_draft_add_remove_reorder_and_mobile(self):
        c = self.case
        first = self.open(c.dispatcher); second = self.open(self.observer)
        original_ids=first.locator('.neoscorpion-dispatch-primary-row').evaluate_all('rows=>rows.map(row=>row.dataset.dispatchMissionId)')
        a, b = c.mission.id, self.other.id
        second.locator(f'[data-dispatch-sort-group="{b}"]').first.evaluate('node=>window.__unchangedMission=node')
        self.primary(first,b).locator('[data-neoscorpion-dispatch-details]').click()
        first.locator(f'[data-dispatch-sort-group="{b}"] [data-console-section="tail-notes"] summary').click()
        draft = first.locator(f'[data-dispatch-sort-group="{b}"] [data-tail-note-form] textarea')
        draft.fill('Unsaved independent mission'); draft.focus()
        c.off(); c.submit()
        started = __import__('time').perf_counter(); self.poll(first,second)
        expect(self.primary(first,a).get_by_role('button',name='COMPLETE',exact=True)).to_be_visible()
        latency = (__import__('time').perf_counter()-started)*1000
        expect(draft).to_have_value('Unsaved independent mission'); expect(draft).to_be_focused()
        self.assertTrue(self.primary(second,b).evaluate('node=>node===window.__unchangedMission'))
        first.screenshot(path=str(EVIDENCE/'desktop-dirty-completion.png'),full_page=True)
        draft.fill(''); draft.blur(); self.poll(first)
        second.on('dialog', lambda dialog:dialog.accept())
        self.primary(second,a).get_by_role('button',name='COMPLETE',exact=True).click()
        expect(self.primary(second,a)).to_have_class(re.compile('is-complete'))
        db.session.expire_all()
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)
        self.assertEqual(db.session.get(NeoScorpionSortTruck,c.selection.id).current_gallons,7000)
        self.poll(first)
        # Synthetic upstream mission edits also publish the existing operation
        # revision, as canonical MotherBrain mutations do in production.
        added = c.fixture._mission('UPSNEW','N888UP',30000,50); self.bump(); added_id=added.id
        self.poll(first); expect(self.primary(first,added_id)).to_have_count(1)
        self.other.planned_datetime_utc += timedelta(hours=2); self.bump(); self.poll(first)
        self.assertNotEqual(first.locator('.neoscorpion-dispatch-primary-row').first.get_attribute('data-dispatch-mission-id'),str(b))
        db.session.delete(added); self.bump(); self.poll(first)
        expect(self.primary(first,added_id)).to_have_count(0)
        self.assertEqual(set(first.locator('.neoscorpion-dispatch-primary-row').evaluate_all(
            'rows=>rows.map(row=>row.dataset.dispatchMissionId)')),set(original_ids))
        first.set_viewport_size({'width':390,'height':844})
        self.assertTrue(first.evaluate('document.documentElement.scrollWidth <= innerWidth+1'))
        first.screenshot(path=str(EVIDENCE/'mobile-mission-delta.png'),full_page=True)
        before=len(self.responses); self.poll(first)
        self.assertEqual(len(self.responses),before)
        self.assertEqual(self.errors,[])
        self.assertNotIn('LIVE REFRESH:',first.locator('[data-spear-fleet-status]').inner_text())
        (EVIDENCE/'two-session-receipt.json').write_text(json.dumps(dict(completion_visible_ms=round(latency,2),
            unchanged_node_preserved=True,draft_preserved=True,event_rows=1,current_gallons=7000,
            added_removed_reordered=True,mobile_no_overflow=True,responses=self.responses,errors=self.errors),indent=2),encoding='utf-8')

    def test_dialog_in_transit_fallback_and_permission_change_preserve_draft(self):
        c = self.case; page=self.open(c.dispatcher); a=c.mission.id
        page.evaluate('window.__oldPanel=document.querySelector("[data-fuel-dispatch-live] > .neoscorpion-panel");window.__holdSnapshot=true')
        self.bump(); page.evaluate('window.__timeOffset+=5001;void (window.__pollPromise=window.__deltaPoll())')
        page.wait_for_function('window.__releaseSnapshot !== undefined')
        self.primary(page,a).locator('[data-fuel-data-open]').click()
        page.wait_for_function('window.NeoScorpionFuelData.isOpen()')
        page.evaluate('window.__releaseSnapshot()'); page.evaluate('window.__pollPromise')
        self.assertTrue(page.evaluate('window.NeoScorpionFuelData.isOpen()'))
        self.assertTrue(page.evaluate('window.__oldPanel===document.querySelector("[data-fuel-dispatch-live] > .neoscorpion-panel")'))
        page.get_by_role('button',name='Close Fueler Data').click(); self.poll(page)
        # A missing transport marker must fail atomically, then recover through
        # the existing next revision/critical fallback. No mutation is replayed.
        page.evaluate('''const w=document.createTreeWalker(document.querySelector('[data-fuel-dispatch-live]'),128);
let n;while(n=w.nextNode()){if(n.nodeValue.startsWith('neo-dispatch:assets:')){n.remove();break;}}''')
        self.bump(); self.poll(page); self.poll(page)
        self.assertGreater(page.evaluate('Object.keys(NeoScorpionDispatchDelta.fingerprints(document.querySelector("[data-fuel-dispatch-live]")).full).length'),0)
        self.primary(page,a).locator('[data-neoscorpion-dispatch-details]').click()
        page.locator(f'[data-dispatch-sort-group="{a}"] [data-console-section="tail-notes"] summary').click()
        draft=page.locator(f'[data-dispatch-sort-group="{a}"] [data-tail-note-form] textarea')
        draft.fill('Retain on authority change'); draft.focus()
        membership=GatewayMembership.query.filter_by(user_id=c.dispatcher.id,gateway_id=c.gateway.id).one()
        node=NeoNode.query.filter_by(code='scorpion').one()
        grant=GatewayNodeRole.query.filter_by(gateway_membership_id=membership.id,node_id=node.id).one()
        grant.role='watcher'; db.session.commit(); self.poll(page)
        expect(draft).to_have_value('Retain on authority change')
        self.assertTrue(self.primary(page,a).locator('[data-dispatch-required-fuel]').is_disabled())
        self.assertEqual(self.errors,[])
        page.screenshot(path=str(EVIDENCE/'permission-draft-retained.png'),full_page=True)
        (EVIDENCE/'race-receipt.json').write_text(json.dumps(dict(dialog_preserved=True,atomic_fallback=True,
            revoked_controls_disabled=True,draft_preserved=True,errors=self.errors),indent=2),encoding='utf-8')
