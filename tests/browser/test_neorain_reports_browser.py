"""Local responsive smoke check for the two new NeoRain report surfaces."""

from datetime import date, datetime
from tempfile import TemporaryDirectory
from threading import Thread
from unittest import TestCase
from unittest.mock import patch

from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

from app import create_app
from app.extensions import db
from app.models import Gateway, NeoRainDelayInfo, SortDateMission, SortDateOperation, User
from app.services.access_control import backfill_default_gateway_node_roles
from app.services.password_policy import set_user_password
from app.services.permission_rules import ensure_default_permission_rules


class NeoRainReportBrowserTest(TestCase):
    def test_desktop_and_phone_widths_and_copy(self):
        with TemporaryDirectory() as directory:
            config = type("BrowserConfig", (), {
                "SECRET_KEY": "test", "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///" + directory.replace("\\", "/") + "/rain-report.db",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
                "LIVE_SCREEN_REFRESH_INTERVAL_MS": 5000,
            })
            app = create_app(config)
            with app.app_context():
                db.create_all()
                ensure_default_permission_rules()
                gateway = Gateway(code="RFD", name="RFD")
                db.session.add(gateway)
                db.session.flush()
                operation = SortDateOperation(gateway_id=gateway.id, gateway_code="RFD",
                                              sort_date=date(2026, 10, 10), sort_name="Night")
                db.session.add(operation)
                db.session.flush()
                departure = SortDateMission(
                    sort_date_operation_id=operation.id, sort_date=operation.sort_date,
                    gateway_code="RFD", sort_name="Night", mission_type="departure",
                    mission_source="master", wave="1", flight_number="UPS07831",
                    origin="RFD", destination="SDF", timezone="America/Chicago",
                    planned_datetime_utc=datetime(2026, 10, 11, 1, 0),
                    actual_block_out_datetime_utc=datetime(2026, 10, 11, 1, 11),
                    departure_status="departed", assigned_tail_number="N7831UP",
                )
                db.session.add(departure)
                db.session.flush()
                db.session.add(NeoRainDelayInfo(
                    sort_date_mission_id=departure.id, minutes=11, code="RS",
                    notes="MULTIPLE LATE INBOUND AIRCRAFT AND EXTENDED WEATHER RESTRICTION",
                ))
                db.session.add(NeoRainDelayInfo(sort_date_mission_id=departure.id,
                    minutes=0, code="TD", notes="SECOND OUTBOUND REMARK"))
                arrival = SortDateMission(sort_date_operation_id=operation.id,
                    sort_date=operation.sort_date, gateway_code="RFD", sort_name="Night",
                    mission_type="arrival", mission_source="manual", wave="1",
                    flight_number="UPS1487", origin="SDF", destination="RFD", timezone="America/Chicago",
                    planned_datetime_utc=datetime(2026,10,11,1),
                    actual_block_in_datetime_utc=datetime(2026,10,11,0,50),
                    arrival_status="arrived", late_metrics_included_override=False)
                db.session.add(arrival)
                db.session.flush()
                db.session.add(NeoRainDelayInfo(sort_date_mission_id=arrival.id,
                    minutes=5, code="RS", notes="EARLY EXCLUDED INBOUND REMARK"))
                user = User(username="rain_browser", email="rain_browser@example.test",
                            first_name="Rain", last_name="Browser", full_name="Rain Browser",
                            employee_id="RAIN-BROWSER", email_verified_at=datetime.utcnow(),
                            role="simulator", is_active=True)
                set_user_password(user, "TestPassword123!")
                db.session.add(user)
                db.session.flush()
                backfill_default_gateway_node_roles(user, role="simulator")
                db.session.commit()
                operation_id = operation.id
            server = make_server("127.0.0.1", 0, app, threaded=True)
            worker = Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(headless=True)
                    try:
                        for width, height in ((1440, 900), (1920, 1080), (390, 844), (375, 568)):
                            context = browser.new_context(viewport={"width": width, "height": height}, permissions=["clipboard-read", "clipboard-write"])
                            page = context.new_page()
                            base = f"http://127.0.0.1:{server.server_port}"
                            page.goto(base + "/login")
                            page.locator('[name="email"]').fill("rain_browser@example.test")
                            page.locator('[name="password"]').fill("TestPassword123!")
                            page.locator('button[type="submit"]').click()
                            page.goto(base + f"/neorain/rainrock?operation_id={operation_id}")
                            self.assertEqual(page.locator("h1").first.inner_text(), "RAINROCK")
                            self.assertTrue(page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth"))
                            expect(page.locator('.rain-report-delay').filter(has_text='EARLY EXCLUDED')).to_be_visible()
                            expect(page.locator('.rain-report-delay').filter(has_text='SECOND OUTBOUND')).to_be_visible()
                            self.assertEqual(page.locator('.rain-report-matrix').count(), 2)
                            self.assertEqual(page.locator('.rain-report-grid .rain-report-metric').count(), 5)
                            self.assertTrue(page.locator('.rain-report-panel').first.evaluate("el => getComputedStyle(el).backgroundColor === 'rgba(0, 0, 0, 0)'"))
                            if width > 900:
                                sidebar_width = page.evaluate("parseFloat(getComputedStyle(document.body).getPropertyValue('--operational-sidebar-width')) || 220")
                                self.assertLessEqual(page.locator('.rain-report').bounding_box()['x'], sidebar_width + 20)
                                cells = page.locator('.rain-report-metric').all()
                                tops = [cell.bounding_box()['y'] for cell in cells]
                                self.assertLess(max(tops)-min(tops), 1)
                            for cell in page.locator('.rain-report-matrix td').all():
                                self.assertTrue(cell.evaluate('el => el.scrollWidth <= el.clientWidth + 1'))
                            self.assertTrue(page.evaluate("""[...document.querySelectorAll('.rain-report input, .rain-report button, .rain-report select')].every(el => { const r = el.getBoundingClientRect(); return r.left >= -1 && r.right <= innerWidth + 1; })"""))
                            if width < 700:
                                self.assertEqual(page.locator(".rain-report-table tr").first.evaluate("el => getComputedStyle(el).display"), "block")
                            page.goto(base + f"/neorain/daily-briefing?operation_id={operation_id}")
                            self.assertEqual(page.locator("h1").first.inner_text(), "DAILY BRIEFING")
                            self.assertTrue(page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth"))
                            self.assertTrue(page.evaluate("""[...document.querySelectorAll('.rain-report button, .rain-report select')].every(el => { const r = el.getBoundingClientRect(); return r.left >= -1 && r.right <= innerWidth + 1; })"""))
                            page.locator("#copy-briefing").click()
                            expect(page.locator("#copy-briefing-status")).to_have_text("Copied.")
                            self.assertEqual(page.evaluate("navigator.clipboard.readText()").replace("\r\n", "\n"),
                                             page.locator("#briefing-text").inner_text())
                            with patch('app.neonodes.neorain.services.current_neorain_outbound_operation',
                                       side_effect=lambda gateway: db.session.get(SortDateOperation, operation_id)), \
                                 patch('app.neonodes.neorain.routes.current_neorain_outbound_operation',
                                       side_effect=lambda gateway: db.session.get(SortDateOperation, operation_id)), \
                                 patch('app.neonodes.neorain.mission_routes.current_neorain_outbound_operation',
                                       side_effect=lambda gateway: db.session.get(SortDateOperation, operation_id)), \
                                 patch('app.neonodes.neorain.routes.neorain_inbound_refresh_status',
                                       return_value={'auto_refresh_enabled': True, 'live_screen_refresh_interval_ms': 5000}):
                                page.goto(base + '/neorain/inbound')
                                errors=[]
                                page.on('pageerror', lambda error: errors.append(str(error)))
                                sta=page.locator('[data-rain-override-field="sta"]:visible').first
                                sta.locator('[data-rain-override-open]').click()
                                clock=sta.locator('[data-rain-override-time]')
                                for raw,display in (('0000','00:00'),('0235','02:35'),('2359','23:59')):
                                    with page.expect_response(lambda response: '/neorain/override' in response.url and response.request.method=='POST') as result:
                                        clock.fill(raw)
                                    self.assertEqual(result.value.status,200,result.value.text())
                                    expect(sta.locator('[data-rain-override-readout]')).to_have_text('STA '+display)
                                    expect(sta.locator('[data-rain-override-message]')).to_contain_text('SAVED')
                                clock.fill('2460')
                                expect(sta.locator('[data-rain-override-message]')).to_have_text('Invalid time.')
                                expect(sta.locator('[data-rain-override-readout]')).to_have_text('STA 23:59')
                                clock.fill('023')
                                expect(sta).to_have_attribute('data-rain-override-dirty','true')
                                with page.expect_response(lambda response: '/neorain/override' in response.url and response.request.method=='POST') as result:
                                    sta.locator('[data-rain-override-reset]').click()
                                self.assertEqual(result.value.status,200)
                                expect(sta.locator('[data-rain-override-source]')).to_have_text('UPSTREAM')
                                self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
                                crew = page.locator('[data-neorain-crew-collapse]')
                                self.assertFalse(crew.evaluate('el => el.open'))
                                crew.locator('summary').click()
                                self.assertTrue(crew.evaluate('el => el.open'))
                                page.reload()
                                self.assertTrue(crew.evaluate('el => el.open'))
                                page.locator('[data-neorain-board-collapse]').evaluate('el => el.click()')
                                self.assertTrue(page.locator('[data-neorain-board-support]').evaluate('el => el.hidden'))
                                page.locator('[data-neorain-board-collapse]').evaluate('el => el.click()')
                                self.assertTrue(crew.evaluate('el => el.open'))
                                self.assertEqual(page.locator('[data-neorain-late-summary] > div').count(), 3)
                                overflow = page.evaluate('''() => [...document.querySelectorAll('body *')]
                                    .filter(el => el.getBoundingClientRect().right > innerWidth + 1)
                                    .slice(0, 12).map(el => ({tag:el.tagName,cls:el.className,text:el.textContent,
                                      parent:el.parentElement.className,
                                      right:el.getBoundingClientRect().right,width:innerWidth}))''')
                                self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'), str(overflow))
                                printer = crew.locator('input[name="printer_number"]').last
                                printer.fill('UNSAVED-PRINTER')
                                self.assertEqual(crew.get_attribute('data-neorain-crew-dirty'), 'true')
                                page.locator('[data-neorain-board-collapse]').evaluate('el => el.click()')
                                page.locator('[data-neorain-board-collapse]').evaluate('el => el.click()')
                                self.assertEqual(printer.input_value(), 'UNSAVED-PRINTER')
                                page.route('**/neorain/inbound/revision?*', lambda route: route.fulfill(
                                    json={'changed': True, 'revision': 'crew-refresh-fixture', 'refresh': {}}))
                                expect(page.locator('[data-neorain-inbound-live]')).to_have_attribute(
                                    'data-neorain-refresh-deferred', 'true', timeout=15000)
                                self.assertEqual(printer.input_value(), 'UNSAVED-PRINTER')
                                self.assertEqual(errors,[])
                                page.unroute('**/neorain/inbound/revision?*')
                                page.goto(base+'/neorain/outbound')
                                self.assertEqual(errors,[],f'Width {width}: {errors}')
                                if page.locator('[data-neorain-mobile-board="outbound"]').is_visible():
                                    page.locator('[data-neorain-mobile-row-open]').first.click()
                                etd=page.locator('[data-rain-override-field="etd"]:visible').first
                                self.assertGreater(page.locator('[data-rain-override-field="etd"]:visible').count(),0,f'Width {width}, errors {errors}, details '+str(page.locator('[data-neorain-mobile-inline-detail]').evaluate_all('els=>els.map(e=>({hidden:e.hidden,text:e.textContent}))')))
                                self.assertEqual(etd.locator('[data-rain-override-open]').count(),1,etd.inner_text())
                                etd.locator('[data-rain-override-open]').click()
                                with page.expect_response(lambda response:'/neorain/override' in response.url and response.request.method=='POST') as result:
                                    etd.locator('[data-rain-override-time]').fill('0235')
                                self.assertEqual(result.value.status,200,result.value.text())
                                expect(etd.locator('[data-rain-override-readout]')).to_have_text('ETD 02:35')
                                expect(etd.locator('[data-rain-override-source]')).to_contain_text('NEO_ONLY')
                                self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
                                self.assertEqual(errors,[])
                                page.goto(base+'/neorain/inbound/missions/new')
                                self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
                                page.locator('[name="flight_number"]').fill('UPS900'+str(width))
                                page.locator('[name="origin"]').fill('SDF')
                                page.locator('[name="assigned_tail_number"]').fill('N438UP')
                                page.locator('[name="planned_time_local_hour"]').fill('02')
                                page.locator('[name="planned_time_local_minute"]').fill('35')
                                page.locator('form.operation-form button[type="submit"]').last.click()
                                expect(page).to_have_url(base+'/neorain/inbound')
                                expect(page.locator('[data-neorain-inbound-row]').filter(has_text='UPS900'+str(width)).first).to_be_attached()
                                if width in (1920,390):
                                    page.evaluate('document.fonts.ready')
                                    page.wait_for_timeout(350)
                                    page.screenshot(path=f'instance/neorain-{width}-validation.png',full_page=True)
                            context.close()
                    finally:
                        browser.close()
            finally:
                server.shutdown()
                worker.join(timeout=5)
                with app.app_context():
                    db.session.remove()
                    db.engine.dispose()
