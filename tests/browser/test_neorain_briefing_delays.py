"""Real browser projections, native copy/fallback and sort-scoped live refresh."""
from datetime import timedelta
from pathlib import Path
from threading import Thread
from unittest import TestCase

from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

from app.extensions import db
from app.models import NeoRainDelayInfo, NeoRainReportEntry, SortDateOperation
from app.services.neorain_daily_briefing import briefing_context, format_briefing
from tests import test_neorain_daily_briefing as unit


class BriefingDelayBrowserTest(TestCase):
    def test_chromium(self):
        self.exercise('chromium')

    def test_webkit(self):
        self.exercise('webkit')

    def exercise(self, engine):
        case = unit.DailyBriefingTest(); case.setUp(); self.addCleanup(case.doCleanups)
        app, op, actor = case.app, case.operation, case.f.actor
        app.config['NEORAIN_BRIEFING_REFRESH_MS'] = 1000
        note = 'Complete aircraft-specific delay note. ' * 35 + 'END OF NOTES <script>not executable</script>'
        db.session.add_all([
            NeoRainDelayInfo(sort_date_mission_id=case.arrival.id, minutes=10, code='RS', notes='Arrival only'),
            NeoRainDelayInfo(sort_date_mission_id=case.departure.id, minutes=5, code='GL', notes=note),
        ])
        other = case.f.fixture._mission(op, 'UPS777', 'DEN', planned=case.departure.planned_datetime_utc,
                                        tail=case.departure.assigned_tail_number)
        other.actual_block_out_datetime_utc = other.planned_datetime_utc+timedelta(minutes=7)
        old = SortDateOperation(gateway_id=case.f.gateway.id, gateway_code='RFD',
            sort_date=op.sort_date-timedelta(days=1), sort_name='night')
        db.session.add(old); db.session.flush()
        previous = case.f.fixture._mission(old, case.departure.flight_number, 'DEN',
            planned=case.departure.planned_datetime_utc-timedelta(days=1), tail=case.departure.assigned_tail_number)
        previous.actual_block_out_datetime_utc = previous.planned_datetime_utc+timedelta(minutes=9)
        db.session.add(NeoRainDelayInfo(sort_date_mission_id=previous.id, minutes=9, code='MX', notes='OTHER SORT ONLY'))
        db.session.commit()
        opid, oldid, departure_id = op.id, old.id, case.departure.id
        context = briefing_context(op)
        concise = format_briefing(context)
        detailed = format_briefing(context, include_delay_details=True)
        evidence = Path('instance/browser-evidence/briefing-delays'); evidence.mkdir(parents=True, exist_ok=True)
        server = make_server('127.0.0.1', 0, app, threaded=True)
        worker = Thread(target=server.serve_forever, daemon=True); worker.start()
        origin = f'http://127.0.0.1:{server.server_port}'
        try:
            with sync_playwright() as pw:
                browser = getattr(pw, engine).launch()
                try:
                    options = {'service_workers':'block', 'viewport':{'width':1440, 'height':900}}
                    if engine == 'chromium': options['permissions'] = ['clipboard-read', 'clipboard-write']
                    browser_context = browser.new_context(**options)
                    page = browser_context.new_page(); errors = []; requests = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.goto(origin+'/login')
                    page.locator('[name=email]').fill(actor.email)
                    page.locator('[name=password]').fill('TestPassword123!')
                    page.locator('button[type=submit]').click()
                    page.wait_for_url(lambda url:'/login' not in url)
                    page.goto(origin+f'/neorain/daily-briefing?operation_id={opid}')
                    page.on('request', lambda request: requests.append((request.method, request.url)))
                    preview = page.locator('#briefing-text'); toggle = page.locator('#briefing-delay-details')
                    expect(toggle).not_to_be_checked(); expect(preview).to_have_text(concise, use_inner_text=False)
                    # Observe the real clipboard calls, without replacing their
                    # implementation. Chromium also reads the native clipboard.
                    page.evaluate("""() => {
                        window.nativeCopies=[];window.fallbackCopies=[];
                        if(navigator.clipboard?.writeText){
                            const write=navigator.clipboard.writeText.bind(navigator.clipboard);
                            navigator.clipboard.writeText=text=>{window.nativeCopies.push(text);return write(text);};
                        }
                        document.addEventListener('copy',()=>window.fallbackCopies.push(document.activeElement.value));
                    }""")
                    def copy_and_check(fallback=False):
                        textareas_before = page.locator('textarea').count()
                        page.locator('#copy-briefing').click()
                        expect(page.locator('#copy-briefing-status')).to_have_text('Copied.')
                        shown = preview.text_content()
                        if fallback:
                            self.assertEqual(page.evaluate('window.fallbackCopies.at(-1)'), shown)
                            self.assertEqual(page.locator('textarea').count(), textareas_before)
                        else:
                            self.assertEqual(page.evaluate('window.nativeCopies.at(-1) || window.fallbackCopies.at(-1)'), shown)
                        if engine == 'chromium' and not fallback:
                            self.assertEqual(page.evaluate('navigator.clipboard.readText()').replace('\r\n','\n'), shown)
                    for width in (1920, 1440, 390, 375):
                        page.set_viewport_size({'width':width, 'height':900})
                        for enabled in (True, False, True, False):
                            toggle.set_checked(enabled)
                            self.assertEqual(preview.text_content(), detailed if enabled else concise)
                            expect(page.locator('[data-briefing-delay-mode]')).to_have_text('ON' if enabled else 'OFF')
                            copy_and_check()
                        self.assertFalse(page.evaluate('document.documentElement.scrollWidth>innerWidth+1'))
                        for element in page.locator('.briefing-copy-controls > *').all():
                            box = element.bounding_box()
                            self.assertLessEqual(box['x']+box['width'], width+1)
                            self.assertGreaterEqual(box['x'], -1)
                        toggle.check(); page.evaluate('document.fonts.ready')
                        page.screenshot(path=str(evidence/f'{engine}-{width}.png'), full_page=True)
                    self.assertFalse(any(method != 'GET' for method, _ in requests), requests)
                    self.assertEqual(NeoRainReportEntry.query.count(), 0)
                    self.assertNotIn('OTHER SORT ONLY', preview.text_content())
                    self.assertIn('RFD-DEN UPS777 N948UP +7\n', preview.text_content())
                    # Actual state endpoint refresh retains selected ON and
                    # includes a newly saved canonical delay for this mission.
                    db.session.add(NeoRainDelayInfo(sort_date_mission_id=departure_id, minutes=0, code='TD', notes='LIVE NEW ENTRY'))
                    db.session.commit()
                    expect(preview).to_contain_text('11 RS, 5 GL, 0 TD', timeout=10000)
                    expect(preview).to_contain_text('LIVE NEW ENTRY')
                    expect(toggle).to_be_checked(); copy_and_check()
                    # Force the API-unavailable path; execCommand itself is
                    # real and must acknowledge a successful plain-text copy.
                    page.evaluate("Object.defineProperty(navigator,'clipboard',{configurable:true,value:undefined})")
                    for enabled in (False, True, False):
                        toggle.set_checked(enabled); copy_and_check(fallback=True)
                    page.goto(origin+f'/neorain/daily-briefing?operation_id={oldid}')
                    expect(toggle).not_to_be_checked(); toggle.check()
                    expect(preview).to_contain_text('9 MX - OTHER SORT ONLY')
                    self.assertNotIn('Late inbound', preview.text_content())
                    self.assertNotIn('LIVE NEW ENTRY', preview.text_content())
                    self.assertFalse(errors, errors)
                    browser_context.close()
                finally:
                    browser.close()
        finally:
            server.shutdown(); worker.join(timeout=5)
