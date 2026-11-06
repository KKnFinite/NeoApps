"""One focused real-browser autosave boundary and retained-draft check."""
import unittest
from threading import Thread

from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server
from tests import test_neoscorpion_fueler_submission as fixtures
from tests.browser.neoscorpion_helpers import wait_for_fuel_saved
from app.extensions import db
from app.models import NeoScorpionFuelAssignment, NeoScorpionFuelWorkState


class TransferFuelBrowserTest(unittest.TestCase):
    def test_invalid_autosave_stays_local_and_cached_client_400_retains_draft(self):
        case=fixtures.SubmissionTest();case.setUp();case.prepare()
        self.addCleanup(case.doCleanups)
        app=case.client.application
        app.config.update(SESSION_COOKIE_SECURE=False,LIVE_SCREEN_REFRESH_INTERVAL_MS=0,
            GOOGLE_MOTHERBRAIN_IMPORT_ENABLED=False,GOOGLE_MOTHERBRAIN_READER_ENABLED=False,FLIGHT_API_ENABLED=False)
        aid,wid=case.assignment.id,case.work.id
        server=make_server('127.0.0.1',0,app,threaded=True)
        thread=Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with sync_playwright() as pw:
                browser=pw.chromium.launch()
                try:
                    page=browser.new_page(viewport={'width':390,'height':844},service_workers='block')
                    origin=f'http://127.0.0.1:{server.server_port}'
                    page.goto(origin+'/login')
                    page.locator('[name=email]').fill(case.fueler.email)
                    page.locator('[name=password]').fill('TestPassword123!')
                    page.locator('button[type=submit]').click()
                    page.wait_for_url(lambda url:'/login' not in url)
                    page.goto(origin+'/neoscorpion/fueler')
                    card=page.locator(f'[data-fuel-assignment-id="{aid}"]').first
                    field=card.locator('[name=transfer_fuel_gallons]')
                    self.assertEqual(field.evaluate('e=>e.type'),'text')
                    self.assertIsNone(field.get_attribute('title'))
                    self.assertEqual(field.get_attribute('placeholder'),'Gallons')
                    pattern=field.get_attribute('pattern')
                    posts=[]
                    page.on('request',lambda request: posts.append(request) if request.method=='POST' and '/fueler/assignments/' in request.url else None)
                    for raw in ('17001','985925558080','-1','1.5','bad'):
                        field.fill(raw)
                        expect(card.locator('[data-fuel-data-status]')).to_have_text('Autosave pending · complete the indicated APU source / input.')
                        expect(field).to_have_value(raw)
                        self.assertFalse(field.evaluate('e=>e.validity.valid'))
                        self.assertEqual(posts,[])
                        self.assertTrue(page.evaluate('window.NeoScorpionFuelData.hasDirty(document.querySelector("[data-fuel-autosave]"))'))
                    for raw in ('0','9999','17000',''):
                        field.fill(raw)
                        wait_for_fuel_saved(card)
                        expect(field).to_have_value(raw)
                        with app.app_context():
                            expected=int(raw) if raw else None
                            self.assertEqual(db.session.get(NeoScorpionFuelAssignment,aid).transfer_fuel_gallons,expected)
                            self.assertEqual(db.session.get(NeoScorpionFuelWorkState,wid).physical_transfer_gallons,expected)
                    # Simulate an already-open client that predates the HTML
                    # constraint. The server rejects it, and existing JS keeps
                    # the exact draft instead of replacing the Fueler card.
                    field.evaluate('e=>e.removeAttribute("pattern")')
                    with page.expect_response(lambda r:r.request.method=='POST' and '/fueler/assignments/' in r.url) as rejected:
                        field.fill('985925558080')
                    self.assertEqual(rejected.value.status,400)
                    expect(field).to_have_value('985925558080')
                    expect(card.locator('[data-fuel-data-status]')).to_contain_text('Draft retained')
                    field.evaluate('(e,pattern)=>e.setAttribute("pattern",pattern)',pattern)
                    field.fill('3000');wait_for_fuel_saved(card)
                    expect(field).to_have_value('3000')
                finally:
                    browser.close()
        finally:
            server.shutdown();thread.join(timeout=5)
