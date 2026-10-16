"""Real Rain pages: compact controls, autosave/reset and existing live refresh."""
from contextlib import ExitStack
from datetime import date, datetime
import unittest
from unittest.mock import patch

from playwright.sync_api import expect
from tests.browser import test_mobile_drawer as fixtures
from app.extensions import db
from app.models import Gateway, SortDateMission, SortDateOperation


class CompactRainBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = fixtures.MobileDrawerBrowserTest
        cls.fixture.setUpClass()
        cls.fixture.app.config['LIVE_SCREEN_REFRESH_INTERVAL_MS'] = 1000
        with cls.fixture.app.app_context():
            gateway = Gateway.query.filter_by(code='RFD').one()
            operation = SortDateOperation(gateway_id=gateway.id, gateway_code='RFD',
                sort_date=date.today(), sort_name='night')
            db.session.add(operation)
            db.session.flush()
            cls.ids = {}
            for kind in ('arrival', 'departure'):
                mission = SortDateMission(sort_date_operation_id=operation.id,
                    sort_date=operation.sort_date, gateway_code='RFD', sort_name='night',
                    mission_type=kind, mission_source='manual', flight_number='UPS123',
                    origin='SDF' if kind == 'arrival' else 'RFD',
                    destination='RFD' if kind == 'arrival' else 'SDF',
                    assigned_tail_number='N123UP', timezone='America/Chicago',
                    planned_datetime_utc=datetime(2026, 10, 16, 6),
                    planned_source='manual', departure_status='scheduled' if kind == 'departure' else None)
                db.session.add(mission)
                db.session.flush()
                cls.ids[kind] = mission.id
            db.session.commit()
            cls.operation_id = operation.id
        cls.stack = ExitStack()
        cls.stack.enter_context(patch('app.neonodes.neorain.routes.current_neorain_outbound_operation',
            side_effect=lambda gateway: db.session.get(SortDateOperation, cls.operation_id)))

    @classmethod
    def tearDownClass(cls):
        cls.stack.close()
        cls.fixture.tearDownClass()

    cases = (('inbound', 'arrival', 'block_in'), ('outbound', 'departure', 'ramp_load_complete'))
    attrs = {'sta': ('planned_datetime_utc', 'planned_source'),
             'etd': ('eta_datetime_utc', 'eta_source'),
             'block_in': ('actual_block_in_datetime_utc', 'actual_block_in_source'),
             'elmac': ('elmac_completed_at_utc', 'elmac_completed_source'),
             'ramp_load_complete': ('ramp_load_completed_at_utc', 'ramp_load_completed_source'),
             'crew_load_complete': ('crew_load_completed_at_utc', 'crew_load_completed_source'),
             'official_block_out': ('actual_block_out_datetime_utc', 'actual_block_out_source')}

    def upstream(self, kind, field, value):
        # The same canonical import fence used by Google refresh, without external I/O.
        from app.services.neorain_overrides import observe_import
        with self.fixture.app.app_context():
            mission = db.session.get(SortDateMission, self.ids[kind])
            attr, source = self.attrs[field]
            values = {attr: value, source: 'google_rain'}
            if field == 'sta':
                from datetime import timezone
                from zoneinfo import ZoneInfo
                values['planned_datetime_local'] = (value.replace(tzinfo=timezone.utc)
                    .astimezone(ZoneInfo(mission.timezone)).replace(tzinfo=None) if value else None)
            observe_import(mission, field, values)
            for key, item in values.items():
                setattr(mission, key, item)
            db.session.commit()

    def clear(self, kind, field):
        from app.models import User
        from app.services.neorain_overrides import edit_override
        with self.fixture.app.app_context():
            mission = db.session.get(SortDateMission, self.ids[kind])
            operation = db.session.get(SortDateOperation, self.operation_id)
            edit_override(mission, operation, field, None, User.query.filter_by(username='drawer-admin').one(), reset=True)
            db.session.commit()
        self.upstream(kind, field, None)

    def cell(self, page, field):
        return page.locator(f'.rain-override[data-rain-override-field="{field}"]:visible').first

    def input(self, page, field):
        return self.cell(page, field).locator('[data-rain-override-time]')

    def save(self, page, field, value, status=200):
        with page.expect_response(lambda r: '/neorain/override' in r.url and r.request.method == 'POST') as saved:
            self.input(page, field).fill(value)
        self.assertEqual(saved.value.status, status)
        return saved.value.json()

    def reset(self, page, field):
        self.input(page, field).focus()
        with page.expect_response(lambda r: '/neorain/override' in r.url and r.request.method == 'POST') as response:
            page.locator('[data-rain-reset-selected]').click()
        self.assertEqual(response.value.status, 200)
        body = response.value.request.post_data_json
        self.assertEqual(body['action'], 'reset')
        self.assertIsNone(body['value'])
        expect(self.cell(page, field)).not_to_have_attribute('data-rain-override-dirty', 'true')
        return response.value.json()

    def assert_compact(self, page, board):
        root = page.locator(f'[data-neorain-board-page="{board}"]')
        self.assertNotRegex(root.inner_text(), r'UPSTREAM|NEO_ONLY|Use Google')
        self.assertEqual(root.locator('[data-rain-override-open], [data-rain-override-editor], [data-rain-override-reset]').count(), 0)
        self.assertEqual(page.locator('[data-rain-reset-selected]').count(), 1)
        self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'))
        geometry = root.locator('[data-rain-override-time]:visible').evaluate_all('''els => els.map(el => {
            const r=el.getBoundingClientRect(), parent=el.parentElement.getBoundingClientRect();
            return {width:r.width, parent:parent.width, height:r.height, value:el.value};
        })''')
        self.assertTrue(geometry)
        self.assertTrue(all(x['width'] <= min(58, x['parent']) + 1 and x['height'] == 26 for x in geometry), geometry)
        self.assertTrue(all(not x['value'] or (len(x['value']) == 5 and x['value'][2] == ':' and x['value'].replace(':','').isdigit()) for x in geometry))

    def reveal(self, page, field):
        if not page.locator(f'.rain-override[data-rain-override-field="{field}"] [data-rain-override-time]:visible').count():
            page.locator('[data-neorain-mobile-row-open]:visible').first.click()

    def test_clearing_all_fields_on_blur_persists_across_import_navigation_and_live_refresh(self):
        fields = {'inbound':('sta','block_in'),
                  'outbound':('etd','elmac','ramp_load_complete','crew_load_complete','official_block_out')}
        for engine, width in (('chromium',1920), ('webkit',390)):
            browser = getattr(self.fixture.pw, engine).launch(headless=True)
            context = browser.new_context(viewport={'width':width,'height':900},
                has_touch=width < 900, is_mobile=width < 900)
            try:
                page = context.new_page(); self.fixture().login(page)
                errors = []; page.on('pageerror', lambda error: errors.append(str(error)))
                for board, kind, _ in self.cases:
                    for field in fields[board]:
                        with self.subTest(engine=engine, field=field):
                            self.clear(kind, field)
                            self.upstream(kind, field, datetime(2026,10,16,7,45))
                            self.fixture().ready(page, '/neorain/' + board)
                            self.reveal(page, field)
                            expect(self.input(page,field)).to_have_value('02:45')
                            page.evaluate('window.rainClearDocument = true')
                            self.input(page,field).fill('')
                            expect(self.cell(page,field)).to_have_attribute('data-rain-override-dirty','true')
                            with page.expect_response(lambda r:'/neorain/override' in r.url and r.request.method=='POST') as cleared:
                                self.input(page,field).press('Tab')
                            self.assertEqual(cleared.value.request.post_data_json['action'],'clear')
                            self.assertEqual(cleared.value.status,200)
                            self.assertTrue(cleared.value.json()['cleared'])
                            expect(self.cell(page,field)).not_to_have_attribute('data-rain-override-dirty','true')
                            page.wait_for_function('!window.rainClearDocument',timeout=20000)
                            self.reveal(page,field)
                            expect(self.input(page,field)).to_have_value('')
                            # Existing live polling receives a genuine new upstream fact,
                            # but explicit Neo null authority keeps both views empty.
                            revision_url=page.locator(f'[data-neorain-{board}-live]').get_attribute('data-revision-url')
                            with page.expect_response(lambda r:revision_url in r.url) as poll:
                                self.upstream(kind,field,datetime(2026,10,16,8,10))
                            self.assertEqual(poll.value.status,200)
                            # Shielding an import need not change visible board facts
                            # or force a reload. Check the next ordinary poll and reload.
                            page.reload()
                            self.reveal(page,field)
                            expect(self.input(page,field)).to_have_value('')
                            posts=[]
                            def capture(request):
                                if '/neorain/override' in request.url and request.method=='POST':posts.append(request.post_data_json)
                            page.on('request',capture)
                            self.input(page,field).focus();self.input(page,field).press('Tab')
                            page.wait_for_timeout(100)
                            self.assertEqual(posts,[])  # Untouched blank is not another clear.
                            page.remove_listener('request',capture)
                            self.fixture().ready(page, '/neorain/' + ('outbound' if board=='inbound' else 'inbound'))
                            self.fixture().ready(page,'/neorain/'+board)
                            self.reveal(page,field)
                            expect(self.input(page,field)).to_have_value('')
                            self.assertEqual(self.reset(page,field)['value'],'03:10')
                            expect(self.input(page,field)).to_have_value('03:10')
                            self.save(page,field,'0125')
                            expect(self.input(page,field)).to_have_value('01:25')
                    self.assert_compact(page,board)
                    page.screenshot(path=str(self.fixture.evidence / f'rain-clear-{board}-{engine}-{width}.png'),full_page=True)
                self.assertEqual(errors,[])
            finally:
                context.close();browser.close()

    def test_clear_and_newer_draft_survive_queued_save_responses(self):
        browser = self.fixture.pw.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width':390,'height':844})
        try:
            page=context.new_page();self.fixture().login(page)
            for board,kind,field in self.cases:
                self.clear(kind,field);self.fixture().ready(page,'/neorain/'+board)
                pending=[]
                page.route('**/neorain/override',lambda route:pending.append((route,route.fetch())))
                self.input(page,field).fill('1400')
                page.wait_for_timeout(200);self.assertEqual(len(pending),1)
                self.input(page,field).fill('');self.input(page,field).press('Tab')
                first,response=pending[0];first.fulfill(response=response)
                page.wait_for_timeout(200);self.assertEqual(len(pending),2)
                clear,cleared=pending[1]
                self.assertEqual(clear.request.post_data_json['action'],'clear')
                self.assertEqual(clear.request.post_data_json['expected_version'],response.json()['version'])
                self.assertTrue(cleared.json()['cleared'])
                # A new unfinished entry wins over the in-flight clear reply.
                self.input(page,field).fill('12')
                clear.fulfill(response=cleared)
                expect(self.cell(page,field)).to_have_attribute('data-rain-override-version',cleared.json()['version'])
                expect(self.input(page,field)).to_have_value('12')
                expect(self.cell(page,field)).to_have_attribute('data-rain-override-dirty','true')
                page.unroute('**/neorain/override')
                self.save(page,field,'0125')
                expect(self.input(page,field)).to_have_value('01:25')
                page.reload();expect(self.input(page,field)).to_have_value('01:25')
        finally:
            context.close();browser.close()

    def test_desktop_mobile_inline_autosave_reset_and_refresh(self):
        for engine in ('chromium', 'webkit'):
            browser = getattr(self.fixture.pw, engine).launch(headless=True)
            try:
                for width, height in ((1920, 1080), (390, 844), (375, 667)):
                    with self.subTest(engine=engine, width=width):
                        context = browser.new_context(viewport={'width': width, 'height': height})
                        try:
                            page = context.new_page()
                            errors, posts = [], []
                            page.on('pageerror', lambda error: errors.append(str(error)))
                            page.on('request', lambda request: posts.append(request.post_data_json)
                                if '/neorain/override' in request.url and request.method == 'POST' else None)
                            self.fixture().login(page)
                            for board, kind, field in self.cases:
                                self.clear(kind, field)
                                self.fixture().ready(page, '/neorain/' + board)
                                self.assert_compact(page, board)
                                expect(self.input(page, field)).to_have_value('')
                                expect(page.locator('[data-rain-reset-selected]')).to_be_disabled()
                                page.screenshot(path=str(self.fixture.evidence / f'rain-{board}-inline-{engine}-{width}.png'))
                                before = len(posts)
                                self.input(page, field).fill('2460')
                                expect(self.cell(page, field).locator('[data-rain-override-message]')).to_have_text('Invalid time.')
                                self.input(page, field).fill('12')
                                self.input(page, field).fill('')
                                page.wait_for_timeout(200)
                                self.assertEqual(len(posts), before)
                                page.evaluate('window.rainOriginalDocument = true')
                                result = self.save(page, field, '1234')
                                self.assertEqual(result['value'], '12:34')
                                expect(self.input(page, field)).to_have_value('12:34')
                                page.wait_for_function('!window.rainOriginalDocument', timeout=20000)
                                self.assert_compact(page, board)
                                expect(self.input(page, field)).to_have_value('12:34')
                                self.reset(page, field)
                                expect(self.input(page, field)).to_have_value('')
                                if board == 'outbound':
                                    checkbox = self.cell(page, 'included').locator('[data-rain-override-checkbox]')
                                    with page.expect_response(lambda r: '/neorain/override' in r.url and r.request.method == 'POST') as checked:
                                        checkbox.click()
                                    self.assertEqual(checked.value.status, 200)
                                    self.assertNotIn('Include', self.cell(page, 'included').inner_text())
                            self.assertEqual(errors, [])
                        finally:
                            context.close()
            finally:
                browser.close()

    def test_google_refresh_populates_empty_protects_drafts_and_active_override(self):
        browser = self.fixture.pw.chromium.launch(headless=True)
        try:
            for width in (1920, 390):
                context = browser.new_context(viewport={'width': width, 'height': 900})
                try:
                    page = context.new_page()
                    self.fixture().login(page)
                    for board, kind, field in self.cases:
                        with self.subTest(width=width, board=board):
                            self.clear(kind, field)
                            self.fixture().ready(page, '/neorain/' + board)
                            expect(self.input(page, field)).to_have_value('')
                            self.upstream(kind, field, datetime(2026, 10, 16, 7, 45))
                            expect(self.input(page, field)).to_have_value('02:45', timeout=20000)
                            self.input(page, field).fill('12')
                            self.upstream(kind, field, datetime(2026, 10, 16, 8, 10))
                            expect(page.locator(f'[data-neorain-{board}-live]')).to_have_attribute('data-neorain-refresh-deferred', 'true', timeout=20000)
                            expect(self.input(page, field)).to_have_value('12')
                            self.save(page, field, '1234', status=409)
                            expect(self.input(page, field)).to_have_value('1234')
                            # Explicit reload recovers from the genuine stale-version conflict.
                            page.reload()
                            expect(self.input(page, field)).to_have_value('03:10')
                            self.save(page, field, '1234')
                            expect(self.cell(page, field)).not_to_have_attribute('data-rain-override-dirty', 'true')
                            self.upstream(kind, field, datetime(2026, 10, 16, 8, 55))
                            page.reload()
                            expect(self.input(page, field)).to_have_value('12:34')
                            self.input(page, field).fill('')
                            result = self.reset(page, field)
                            self.assertEqual(result['value'], '03:55')
                            expect(self.input(page, field)).to_have_value('03:55')
                            self.assert_compact(page, board)
                finally:
                    context.close()
        finally:
            browser.close()

    def test_inflight_response_preserves_new_partial_and_blank_drafts(self):
        browser = self.fixture.pw.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 390, 'height': 844})
        try:
            page = context.new_page()
            self.fixture().login(page)
            for board, kind, field in self.cases:
                self.clear(kind, field)
                self.fixture().ready(page, '/neorain/' + board)
                for draft in ('12', ''):
                    pending = []
                    page.route('**/neorain/override', lambda route: pending.append((route, route.fetch())))
                    self.input(page, field).fill('1400')
                    page.wait_for_timeout(300)
                    self.assertEqual(len(pending), 1)
                    self.input(page, field).fill(draft)
                    route, response = pending[0]
                    route.fulfill(response=response)
                    expect(self.cell(page, field)).to_have_attribute('data-rain-override-version', response.json()['version'])
                    expect(self.input(page, field)).to_have_value(draft)
                    expect(self.cell(page, field)).to_have_attribute('data-rain-override-dirty', 'true')
                    page.unroute('**/neorain/override')
                    self.reset(page, field)
        finally:
            context.close()
            browser.close()

    def test_hhmm_normalizes_all_fields_and_numeric_keyboard_can_replace_formatted_clock(self):
        fields={'inbound':('sta','block_in'),
                'outbound':('etd','elmac','ramp_load_complete','crew_load_complete','official_block_out')}
        for engine,width in (('chromium',1920),('webkit',390)):
            browser=getattr(self.fixture.pw,engine).launch(headless=True)
            context=browser.new_context(viewport={'width':width,'height':900},has_touch=width<900,is_mobile=width<900)
            try:
                page=context.new_page();self.fixture().login(page)
                for board,kind,target in self.cases:
                    with self.subTest(engine=engine,board=board):
                        self.fixture().ready(page,'/neorain/'+board)
                        for field in fields[board]:
                            locator=page.locator(f'.rain-override[data-rain-override-field="{field}"] [data-rain-override-time]:visible')
                            if not locator.count():
                                page.locator('[data-neorain-mobile-row-open]:visible').first.click()
                            payload=self.save(page,field,'0125')
                            self.assertEqual(payload['value'],'01:25')
                            expect(self.input(page,field)).to_have_value('01:25')
                            mirrors=page.locator(f'.rain-override[data-rain-override-field="{field}"] [data-rain-override-time]')
                            self.assertTrue(all(v=='01:25' for v in mirrors.evaluate_all('els => els.map(el => el.value)')))
                        for raw,formatted in (('0000','00:00'),('0125','01:25'),('1430','14:30'),('2359','23:59')):
                            self.save(page,target,raw)
                            expect(self.input(page,target)).to_have_value(formatted)
                        # Exercise actual typing into a saved five-character value,
                        # including touch focus and the numeric mobile input mode.
                        clock=self.input(page,target)
                        clock.press('Tab')
                        if width<900:clock.tap()
                        else:clock.click()
                        expect(clock).to_have_attribute('inputmode','numeric')
                        with page.expect_response(lambda r:'/neorain/override' in r.url and r.request.method=='POST') as response:
                            clock.press_sequentially('0125',delay=30)
                        self.assertEqual(response.value.request.post_data_json['value'],'0125')
                        expect(clock).to_have_value('01:25')
                        # Caret edits / pasted formatted values still send HHMM to
                        # STA/ETD's existing strict operational parser.
                        self.assertEqual(self.save(page,target,'14:30')['value'],'14:30')
                        expect(clock).to_have_value('14:30')
                        for invalid in ('2400','2360','12:99'):
                            clock.fill(invalid)
                            expect(self.cell(page,target).locator('[data-rain-override-message]')).to_have_text('Invalid time.')
                            expect(clock).to_have_value(invalid)
                        page.reload()
                        expect(self.input(page,target)).to_have_value('14:30')
                        self.assert_compact(page,board)
            finally:
                context.close();browser.close()
