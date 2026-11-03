"""Actual three-session Fueler A -> existing Dispatcher swap -> Fueler B."""
import json
from pathlib import Path
from threading import Thread
import unittest
from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server

from app.extensions import db
from app.models import NeoScorpionFuelWorkState,NeoScorpionFuelingEvent
from app.services.neoscorpion_prof import event_math
from tests import test_neoscorpion_fueler_handoff as fixtures
from tests.browser.neoscorpion_helpers import wait_for_fuel_saved

EVIDENCE=Path('instance/fueler-handoff/browser')


class FuelerHandoffBrowserTest(unittest.TestCase):
    def exercise(self,engine,after_off):
        case=fixtures.FuelerHandoffTest();case.setUp();case.prepare()
        app=case.client.application
        app.config.update(SESSION_COOKIE_SECURE=False,LIVE_SCREEN_REFRESH_INTERVAL_MS=0,
            GOOGLE_MOTHERBRAIN_IMPORT_ENABLED=False,GOOGLE_MOTHERBRAIN_READER_ENABLED=False,FLIGHT_API_ENABLED=False)
        b=case.replacement('browser-replacement')
        aid,wid=case.assignment.id,case.work.id
        original_on=case.work.manual_on_at_utc
        b_id=b.id;original_fueler=case.fueler.id
        server=make_server('127.0.0.1',0,app,threaded=True)
        thread=Thread(target=server.serve_forever,daemon=True);thread.start()
        origin=f'http://127.0.0.1:{server.server_port}'
        EVIDENCE.mkdir(parents=True,exist_ok=True)
        errors=[];receipt={'engine':engine,'after_off':after_off}
        try:
            with sync_playwright() as pw:
                browser=getattr(pw,engine).launch()
                try:
                    def login(user,width):
                        context=browser.new_context(viewport={'width':width,'height':900},service_workers='block')
                        page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
                        page.goto(origin+'/login');page.locator('[name=email]').fill(user.email)
                        page.locator('[name=password]').fill('TestPassword123!')
                        page.locator('button[type=submit]').click();page.wait_for_url(lambda url:'/login' not in url)
                        page.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1','seen')")
                        return page
                    a=login(case.fueler,390);d=login(case.dispatcher,1440);replacement=login(b,390)
                    a.goto(origin+'/neoscorpion/fueler')
                    card=a.locator(f'[data-fuel-assignment-id="{aid}"]').first
                    for code,remaining,actual in (('left','6','15'),('ctr','0','0'),('right','6','15')):
                        card.locator('[name=remaining_'+code+']').fill(remaining)
                        card.locator('[name=actual_'+code+']').fill(actual)
                    card.locator('[data-apu-running]').select_option('no')
                    card.locator('[name=transfer_fuel_gallons]').fill('3000')
                    card.locator('[name=notes]').fill('Saved by Fueler A')
                    wait_for_fuel_saved(card)
                    expect(card.locator('[data-actual-total]')).to_have_text('30.0')
                    if after_off:
                        card.get_by_role('button',name='TRUCK OFF',exact=True).click()
                        expect(card.locator('.scorpion-fueler-finish')).to_contain_text('TRUCK OFF')
                    a.screenshot(path=str(EVIDENCE/f'{engine}-a-saved.png'),full_page=True)
                    with app.app_context():
                        w=db.session.get(NeoScorpionFuelWorkState,wid);off=w.off_at_utc
                        self.assertEqual(w.notes,'Saved by Fueler A')
                    d.goto(origin+'/neoscorpion/fuel-dispatch')
                    def details():
                        control=d.locator('[data-neoscorpion-dispatch-details]').first
                        if control.get_attribute('aria-expanded')!='true':control.click()
                        summary=d.get_by_text('SWAP RESOURCES',exact=True)
                        if not summary.evaluate('e=>e.parentElement.open'):summary.click()
                    details()
                    d.get_by_label('Replacement fueler',exact=True).select_option(str(b_id))
                    with d.expect_response(lambda r:'/fuel-dispatch/swap-fueler' in r.url and r.request.method=='POST') as swapped:
                        d.get_by_role('button',name='SWAP FUELER',exact=True).click()
                    self.assertIn(swapped.value.status,(200,302))
                    d.wait_for_url(origin+'/neoscorpion/fuel-dispatch')
                    # A's stale autosave is a denied mutation, with the local draft retained.
                    with a.expect_response(lambda r:r.request.method=='POST' and f'/fueler/assignments/{aid}?' in r.url) as stale:
                        card.locator('[name=notes]').fill('Uncommitted A draft')
                    self.assertIn(stale.value.status,(400,403,409))
                    expect(card.locator('[name=notes]')).to_have_value('Uncommitted A draft')
                    expect(card.locator('[data-fuel-data-status]')).not_to_have_text('Saved')
                    a.screenshot(path=str(EVIDENCE/f'{engine}-a-revoked-draft.png'),full_page=True)
                    replacement.goto(origin+'/neoscorpion/fueler')
                    inherited=replacement.locator(f'[data-fuel-assignment-id="{aid}"]').first
                    expect(inherited.locator('[name=remaining_left]')).to_have_value('6.0')
                    expect(inherited.locator('[name=actual_left]')).to_have_value('15.0')
                    expect(inherited.locator('[name=transfer_fuel_gallons]')).to_have_value('3000')
                    expect(inherited.locator('[name=notes]')).to_have_value('Saved by Fueler A')
                    self.assertEqual(inherited.get_attribute('data-physical-work-id'),str(wid))
                    replacement.screenshot(path=str(EVIDENCE/f'{engine}-b-inherited.png'),full_page=True)
                    inherited.locator('[data-acknowledge-assignment-update]').click()
                    expect(inherited.locator('[data-assignment-update-notice]')).to_have_count(0)
                    expect(inherited.locator('[name=actual_left]')).to_have_value('15.0')
                    inherited.locator('[name=notes]').fill('B continued the same physical visit')
                    wait_for_fuel_saved(inherited)
                    if not after_off:
                        inherited.get_by_role('button',name='TRUCK OFF',exact=True).click()
                        expect(inherited.locator('.scorpion-fueler-finish')).to_contain_text('TRUCK OFF')
                    button=inherited.locator('[data-fueler-submit-form] button')
                    expect(button).to_be_enabled();button.click()
                    expect(inherited.locator('[data-fuel-data-status]')).to_contain_text('SUBMITTED')
                    d.reload();details()
                    expect(d.get_by_role('button',name='SWAP FUELER',exact=True)).to_be_disabled()
                    expect(d.get_by_text('FUELER SWAP locked after COMPLETE ASSIGNMENT.',exact=True)).to_be_visible()
                    d.screenshot(path=str(EVIDENCE/f'{engine}-submission-locked.png'),full_page=True)
                    complete=d.locator('[data-dispatch-verification-form] button')
                    expect(complete).to_be_visible();complete.click()
                    expect(d.locator('[data-fuel-status]').first).to_contain_text('COMPLETE')
                    with app.app_context():
                        w=db.session.get(NeoScorpionFuelWorkState,wid);event=NeoScorpionFuelingEvent.query.one()
                        self.assertEqual(NeoScorpionFuelWorkState.query.count(),1)
                        self.assertEqual(w.manual_on_at_utc,original_on)
                        if after_off:self.assertEqual(w.off_at_utc,off)
                        self.assertEqual(w.physical_fueler_user_id,original_fueler)
                        self.assertEqual(w.fueler_submitted_by_user_id,b_id)
                        self.assertEqual(event.transfer_fuel_gallons,3000)
                        math=event_math(event)
                        self.assertIn('MULTIPLE FUELERS',math['fueler_reasons'])
                        self.assertIsNone(math['truck_prof'])
                        receipt.update(work_rows=1,event_rows=1,original_on_preserved=True,
                            old_actor_preserved=True,submitted_by_replacement=True,prof_reasons=list(math['fueler_reasons']))
                    d.goto(origin+'/neoscorpion/reports/prof')
                    expect(d.get_by_text('MULTIPLE FUELERS',exact=False).first).to_be_visible()
                    d.screenshot(path=str(EVIDENCE/f'{engine}-prof-exclusion.png'),full_page=True)
                    self.assertFalse(errors,errors)
                finally:browser.close()
        finally:
            (EVIDENCE/f'{engine}-receipt.json').write_text(json.dumps(receipt,indent=2))
            server.shutdown();thread.join(timeout=5);case.doCleanups()

    def test_chromium_active_handoff(self):self.exercise('chromium',False)
    def test_webkit_off_handoff(self):self.exercise('webkit',True)
