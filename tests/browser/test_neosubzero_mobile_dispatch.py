"""Unified mobile Dispatch and the shared TV/Ramp projection in both engines."""
import unittest
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect

from app.extensions import db
from app.models import NeoSubZeroAuditEntry, NeoSubZeroDepartureDeiceEvent, NeoSubZeroPretreatState, SortDateMission, SortDateOperation, SortDateParkingAssignment, SortDateTailState
from app.neonodes.neosubzero.services import mutate_pretreat
from app.services.neosubzero_spray import set_neosubzero_ucc_truck
from tests.browser import test_neosubzero_operations as fixtures
from tests import test_neosubzero_ucc


class MobileDispatchBrowserTest(unittest.TestCase):
    def exercise_status_and_fluid_colors(self, engine):
        case = fixtures.OperationsBrowserTest()
        case.setUpClass()
        self.addCleanup(case.tearDownClass)
        with case.app.app_context():
            for mission_id in case.missions[:2]:
                mission = db.session.get(SortDateMission, mission_id)
                mission.actual_block_out_datetime_utc = None
                NeoSubZeroDepartureDeiceEvent.query.filter_by(sort_date_mission_id=mission_id).delete()
            db.session.commit()

        browser = getattr(case.pw, engine).launch()
        self.addCleanup(browser.close)
        page = browser.new_page(viewport={"width": 390, "height": 844}, has_touch=True)
        case.login(page)
        case.ready(page, "/neosubzero/operations?mode=outbound")
        row = page.locator(f'[data-ops-row][data-mission-id="{case.missions[0]}"]')
        summary = row.locator(".ops-mobile-outbound > summary")
        status = summary.locator(".ops-mobile-status")
        badge = status.locator(".ops-mobile-pretreated")
        untreated = page.locator(f'[data-ops-row][data-mission-id="{case.missions[1]}"] .ops-mobile-status')
        expect(status).to_have_text("")
        expect(untreated).to_have_text("")
        height = summary.evaluate("e => e.offsetHeight")

        with case.app.app_context():
            operation = db.session.get(SortDateOperation, case.operation_id)
            mission = db.session.get(SortDateMission, case.missions[0])
            fields = ("pass1_start", "pass1_end", "pass2_start", "pass2_end")
            mutate_pretreat(operation, mission, "save_treatment", dict.fromkeys(fields, "21:00"))
            db.session.flush()
            SortDateTailState.query.filter_by(tail_number=mission.assigned_tail_number).one().pretreat_status = False
            db.session.commit()

        expect(status).to_have_text("PRETREATED", timeout=12000)
        expect(badge).to_have_css("font-weight", "800")
        expect(badge).to_have_css("color", "rgb(167, 210, 255)")
        self.assertEqual(summary.evaluate("e => e.offsetHeight"), height)
        evidence = Path("instance/browser-evidence/subzero-mobile-outbound-status")
        evidence.mkdir(parents=True, exist_ok=True)
        for width in (375, 390, 430):
            page.set_viewport_size({"width": width, "height": 844})
            expect(badge).to_be_visible()
            expect(untreated).to_have_text("")
            self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth - innerWidth"), 1)
            page.screenshot(path=str(evidence / f"{engine}-after-{width}.png"))

        with case.app.app_context():
            builder = test_neosubzero_ucc.NeoSubZeroUccTest()
            builder.operation = db.session.get(SortDateOperation, case.operation_id)
            mission = db.session.get(SortDateMission, case.missions[0])
            event = builder._deice_event(mission, "configured", configured_at=datetime(2026, 10, 23, 4))
            event.treatment_plan = "two_type_i"
            event_id = event.id
            db.session.commit()
        expect(status.locator("span")).to_have_text("CONFIGURED", timeout=12000)
        expect(badge).to_have_count(1)
        self.assertEqual(summary.evaluate("e => e.offsetHeight"), height)
        summary.click()
        form = row.locator(".ops-mobile-outbound [data-subzero-deice-form]")
        pass1 = form.locator('[data-subzero-pass="1"]')
        pass2 = form.locator('[data-subzero-pass="2"]')
        expect(pass1).to_have_css("background-color", "rgb(58, 42, 36)")
        expect(pass2).to_have_css("background-color", "rgb(58, 42, 36)")
        form.locator('[name="treatment_plan"]').select_option("type_i_type_iv")
        expect(pass2).to_have_css("background-color", "rgb(24, 53, 47)")
        expect(pass2.locator("legend")).to_contain_text("TYPE IV")
        page.screenshot(path=str(evidence / f"{engine}-fluid-types-430.png"))
        page.once("dialog", lambda dialog: dialog.accept())
        # Navigation starts a clean synthetic session after the unsaved plan preview.
        page.goto(page.url.split("&mission=")[0])
        expect(page.locator('[data-ops-workspace]')).to_have_attribute("data-ready", "true")
        for label, change in (
            ("DEICING", {"pass1_started_at_utc": datetime(2026, 10, 23, 4, 1)}),
            ("NEGATIVE DEICE", {"status": "negative", "pass1_started_at_utc": None}),
            ("CLEARED · RADIO PENDING", {"status": "cleared", "radio_state": "pending"}),
        ):
            with case.app.app_context():
                event = db.session.get(NeoSubZeroDepartureDeiceEvent, event_id)
                for field, value in change.items():
                    setattr(event, field, value)
                db.session.commit()
            expect(status.locator("span")).to_have_text(label, timeout=12000)
            expect(badge).to_have_count(1)

    def test_status_and_fluid_colors_chromium(self):
        self.exercise_status_and_fluid_colors("chromium")

    def test_status_and_fluid_colors_webkit(self):
        self.exercise_status_and_fluid_colors("webkit")

    def exercise(self, engine):
        case = fixtures.OperationsBrowserTest()
        case.setUpClass()
        self.addCleanup(case.tearDownClass)
        case.seed()
        with case.app.app_context():
            operation = db.session.get(SortDateOperation, case.operation_id)
            set_neosubzero_ucc_truck(operation, 'Alpha', 1, '12')
            remote = db.session.get(SortDateMission, case.missions[4])
            parking = SortDateParkingAssignment.query.filter_by(
                sort_date_operation_id=operation.id, tail_number=remote.assigned_tail_number,
            ).one()
            parking.ramp_code, parking.position_code = 'Remote', 'R01'
            db.session.commit()

        browser = getattr(case.pw, engine).launch()
        self.addCleanup(browser.close)
        page = browser.new_page(viewport={'width': 390, 'height': 844}, has_touch=True, service_workers="block")
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        case.login(page)
        evidence = Path('instance/browser-evidence/subzero-mobile-dispatch').resolve()
        evidence.mkdir(parents=True, exist_ok=True)

        def mission(mission_id):
            return page.locator(f'[data-ops-row][data-mission-id="{mission_id}"] [data-ops-accordion]')

        def no_overflow():
            self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth - innerWidth'), 1)

        def mutate(control, status=200):
            with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/operations/mutate')) as pending:
                control.click()
            self.assertEqual(pending.value.status, status, pending.value.text())
            if status == 200:
                expect(page.get_by_text('SAVING…', exact=True)).to_have_count(0)
            return pending.value.json()

        case.ready(page, '/neosubzero/operations?mode=pretreat')
        pretreat = mission(case.missions[1])
        for width in (375, 390, 430):
            page.set_viewport_size({'width': width, 'height': 844})
            expect(pretreat.locator('summary').first).to_contain_text('ETA 21:35')
            expect(pretreat.locator('summary').first).to_contain_text('GT: 2:45')
            expect(pretreat.locator('summary').first).to_contain_text('A04')
            expect(pretreat.locator('summary').first).to_contain_text('UPS201')
            expect(page.locator('[data-ops-layout]:visible')).to_have_count(0)
            self.assertEqual(page.locator('[data-ops-ramp] option').all_text_contents()[1:7],
                             ['ALPHA', 'BRAVO', 'CHARLIE', 'DELTA', 'ECHO', 'REMOTE'])
            no_overflow()
        page.set_viewport_size({'width': 390, 'height': 844})
        page.screenshot(path=str(evidence / f'{engine}-pretreat-collapsed.png'))
        planned = page.locator(f'[data-ops-row][data-mission-id="{case.missions[1]}"] [data-ops-quick-toggle]')
        reject = lambda route: route.fulfill(status=409, json={'ok':False, 'error':'Planning changed. Reload current values.'})
        page.route('**/operations/mutate', reject)
        mutate(planned, 409)
        expect(planned).not_to_be_checked()
        expect(pretreat.locator('[data-ops-inline]')).to_be_hidden()
        page.unroute('**/operations/mutate', reject)
        mutate(planned)
        expect(planned).to_be_checked()
        expect(pretreat.locator('.ops-mobile-status')).to_have_text('PLANNED')
        expect(pretreat.locator('[data-ops-inline]')).to_be_hidden()
        with case.app.app_context():
            state = NeoSubZeroPretreatState.query.filter_by(tail_number='N441UP').one()
            self.assertTrue(state.pretreat_planned)
            self.assertIsNone(state.pass1_started_at_utc)
        pretreat.locator(':scope > summary').click()
        expect(pretreat.locator('[data-ops-inline]')).to_be_visible()
        expect(pretreat.locator('[data-ops-collapse-inline]')).to_be_visible()
        pretreat.screenshot(path=str(evidence / f'{engine}-pretreat-expanded.png'))
        for width in (375, 390, 430):
            page.set_viewport_size({'width': width, 'height': 844})
            no_overflow()
        other = mission(case.missions[2])
        other.locator(':scope > summary').click()
        expect(pretreat.locator('[data-ops-inline]')).to_be_hidden()
        other.locator('[data-ops-collapse-inline]').click()
        expect(other.locator('[data-ops-inline]')).to_be_hidden()

        case.ready(page, '/neosubzero/operations?mode=outbound')
        outbound = mission(case.flow_id)
        for width in (375, 390, 430):
            page.set_viewport_size({'width': width, 'height': 844})
            expect(outbound.locator('summary').first).to_contain_text('UPS7777')
            expect(outbound.locator('summary').first).to_contain_text('DEN')
            expect(outbound.locator('summary').first).to_contain_text('ETD')
            expect(page.locator('[data-ops-layout]:visible')).to_have_count(0)
            no_overflow()
        page.set_viewport_size({'width': 390, 'height': 844})
        page.screenshot(path=str(evidence / f'{engine}-outbound-collapsed.png'))
        alpha = mission(case.missions[0])
        alpha.locator(':scope > summary').click()
        expect(alpha.locator('[data-ops-mobile-crews]')).to_contain_text('1 · 12')
        expect(alpha.locator('[data-ops-mobile-crews]')).to_contain_text('Christopher Longname-Deicer-0')
        mutate(alpha.get_by_role('switch', name='FROST PRESENT'))
        expect(alpha.get_by_text('FROST PRESENT · RECORDED')).to_be_visible()
        outbound.locator(':scope > summary').click()
        expect(alpha.locator('[data-ops-inline]')).to_be_hidden()
        mutate(outbound.get_by_role('button', name='INITIAL CONTACT', exact=True))
        treatment = outbound.locator('[data-subzero-deice-form]:visible')
        expect(treatment).to_be_visible()
        expect(page.locator('[data-ops-detail]')).to_be_hidden()
        frost = outbound.get_by_role('switch', name='FROST PRESENT')
        expect(frost).not_to_be_checked()
        with case.app.app_context():
            event = NeoSubZeroDepartureDeiceEvent.query.filter_by(sort_date_mission_id=case.flow_id).one()
            self.assertIsNone(event.configured_at_utc)
            self.assertEqual(NeoSubZeroAuditEntry.query.filter_by(event_id=event.id, action='departure_frost_present').count(), 0)
        page.once('dialog', lambda dialog: dialog.accept())
        mutate(outbound.get_by_role('button', name='NERVOUS CAPTAIN'))
        expect(outbound.get_by_text('NERVOUS CAPTAIN · RECORDED')).to_be_visible()
        mutate(outbound.get_by_role('button', name='CONFIGURED', exact=True))
        expect(outbound.locator('.ops-mobile-status')).to_contain_text('CONFIGURED')
        treatment.locator('[name="treatment_plan"]').select_option('one_type_i')
        treatment.locator('[name="pass1_surface_area"]').select_option('wings_tail')
        treatment.locator('[data-subzero-deice-stamp="pass1_start"]').click()
        start = treatment.locator('[name="pass1_start"]')
        expect(start).not_to_have_value('')
        start.fill('22:05')
        start.focus()
        with case.app.app_context():
            changed = db.session.get(SortDateMission, case.flow_id)
            changed.eta_datetime_utc = datetime(2026, 10, 23, 5, 40)
            db.session.commit()
        expect(outbound.locator('summary').first).to_contain_text('00:40', timeout=12000)
        expect(start).to_be_focused()
        expect(start).to_have_value('22:05')
        outbound.screenshot(path=str(evidence / f'{engine}-outbound-expanded.png'))
        for width in (375, 390, 430):
            page.set_viewport_size({'width': width, 'height': 844})
            no_overflow()
        page.route('**/operations/mutate', reject)
        mutate(treatment.get_by_role('button', name='SAVE', exact=True), 409)
        expect(outbound.locator('[data-ops-inline-feedback]')).to_contain_text('Planning changed')
        expect(start).to_have_value('22:05')
        page.unroute('**/operations/mutate', reject)
        mutate(treatment.get_by_role('button', name='SAVE', exact=True))
        expect(outbound.get_by_role('button', name='NEGATIVE DEICE')).to_have_count(0)
        expect(outbound.get_by_role('button', name='CLEAR', exact=True)).to_have_count(0)
        treatment.locator('[name="pass1_end"]').fill('22:10')
        mutate(treatment.get_by_role('button', name='SAVE', exact=True))
        expect(outbound.get_by_role('button', name='CLEAR', exact=True)).to_be_visible()
        expect(outbound.locator('[data-ops-correction-field]')).not_to_be_visible()
        outbound.get_by_text('TREATMENT RECORDS / CORRECTIONS', exact=True).click()
        expect(outbound.locator('[data-ops-correction-field]')).not_to_be_visible()
        outbound.get_by_role('button', name='CORRECT RECORDED TREATMENT').click()
        expect(outbound.locator('[data-ops-correction-field]')).to_be_visible()
        self.assertTrue(outbound.locator('[data-ops-correction-field] input').get_attribute('required') is not None)
        outbound.get_by_text('TREATMENT RECORDS / CORRECTIONS', exact=True).click()
        page.reload()
        expect(outbound.locator('[data-ops-inline-feedback]')).to_contain_text('RESTORED UNSAVED DRAFT')
        page.once('dialog', lambda dialog: dialog.accept())
        outbound.locator('[data-ops-reload-inline]').click()
        expect(outbound.locator('[data-ops-inline-feedback]')).to_be_hidden()
        mutate(outbound.get_by_role('button', name='CLEAR', exact=True))
        expect(outbound.locator('[data-radio-script]')).to_be_visible()
        mutate(outbound.get_by_role('button', name='RADIO COMPLETE', exact=True))
        expect(outbound.locator('.ops-mobile-status')).to_contain_text('RADIO COMPLETE')
        with case.app.app_context():
            event = NeoSubZeroDepartureDeiceEvent.query.filter_by(sort_date_mission_id=case.flow_id).one()
            self.assertEqual(event.pass_evidence['1']['reason'], 'Nervous Captain')
            self.assertIsNotNone(event.radio_completed_by_user_id)

        case.ready(page, f'/neosubzero/coordinator?ramp=Delta&mission={case.flow_id}')
        self.assertEqual(urlsplit(page.url).path, '/neosubzero/operations')
        self.assertEqual(parse_qs(urlsplit(page.url).query)['mission'], [str(case.flow_id)])
        expect(page.locator('[data-ops-ramp]')).to_have_value('Delta')
        expect(outbound.locator('[data-ops-inline]')).to_be_visible()
        expect(page.locator('[data-mobile-drawer] a[href*="/coordinator"]')).to_have_count(0)

        page.goto(case.origin + '/neosubzero/ucc?view=ramp&ramp=Alpha')
        mobile = page.locator('[data-ucc-mobile-projection]')
        expect(mobile.locator('.ucc-tv-pad')).to_contain_text('N442UP')
        expect(mobile.locator('.ucc-tv-crew')).to_contain_text('12')
        expect(mobile.locator('.ucc-tv-crew')).to_contain_text('C. Longname-Deicer-0')
        for width in (375, 390, 430):
            page.set_viewport_size({'width': width, 'height': 844})
            no_overflow()
            self.assertEqual(mobile.locator('.ucc-tv-stands > .ucc-tv-stand').count(), 12)
        page.set_viewport_size({'width': 390, 'height': 844})
        mobile.screenshot(path=str(evidence / f'{engine}-ducc-ramp-mobile.png'))
        page.get_by_role('button', name='REMOTE', exact=True).click()
        expect(mobile.locator('.ucc-mobile-remote')).to_be_visible()
        expect(mobile.locator('.ucc-tv-pad')).to_have_count(0)
        with case.app.app_context():
            operation = db.session.get(SortDateOperation, case.operation_id)
            set_neosubzero_ucc_truck(operation, 'Remote', 1, 'UCC-831')
            db.session.commit()
        expect(mobile.locator('.ucc-tv-crew')).to_contain_text('UCC-831', timeout=15000)
        expect(page.locator('[data-ucc-ramp-select="Remote"]')).to_have_attribute('aria-pressed','true')

        page.set_viewport_size({'width': 1920, 'height': 1080})
        page.goto(case.origin + '/neosubzero/ucc')
        page.locator('[data-ucc-tv-enter]').click()
        tv = page.locator('.ucc-tv-ramp').filter(has=page.get_by_role('heading', name='Alpha', exact=True))
        expect(tv.locator('.ucc-tv-pad')).to_contain_text('N442UP')
        expect(tv.locator('.ucc-tv-crew')).to_contain_text('C. Longname-Deicer-0')
        tv.screenshot(path=str(evidence / f'{engine}-ducc-ramp-tv.png'))
        self.assertEqual(errors, [])

    def test_chromium(self):
        self.exercise('chromium')

    def test_webkit(self):
        self.exercise('webkit')
