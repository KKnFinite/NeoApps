"""Compare DUCC TV presentations against the same synthetic Sort."""
from contextlib import closing
from datetime import datetime
from io import BytesIO
from pathlib import Path
import unittest

from PIL import Image, ImageChops, ImageDraw, ImageStat
from playwright.sync_api import expect

from app.extensions import db
from app.models import NeoSubZeroPretreatState, SortDateOperation, SortDateParkingAssignment, StaffingPerson, StaffingUnit
from app.services.neosubzero_spray import set_neosubzero_ucc_truck
from app.services.neosubzero_staffing import DEICE_QUALIFICATION_KEY, set_staffing_person_qualification
from app.services.neosubzero_trucks import manage_truck
from app.services.neosubzero_ucc import set_neosubzero_ucc_assignment
from tests.browser import test_neosubzero_tv_weather as weather_fixture
from tests.browser import test_neosubzero_ucc as fixtures


EVIDENCE = Path('instance/ducc-crew-view/browser')
SIZES = ((1920, 1080), (2560, 1440), (3840, 2160), (1366, 768))
HIDE_SELECTOR = '.ucc-tv-view-selector { visibility: hidden !important; }'
AFTER_PAINT = '() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))'


class DuccTvCrewTest(unittest.TestCase):
    baseline_assets = {}

    def seed(self, case):
        weather_fixture.DuccTvWeatherTest().seed_wallboard(case)
        with case.app.app_context():
            operation = db.session.get(SortDateOperation, case.operation_id)
            fixture = fixtures.NeoSubZeroUccTest()
            fixture.operation = operation
            area_id = db.session.get(StaffingPerson, case.people[0]).work_assignments[0].work_area_unit_id
            area = db.session.get(StaffingUnit, area_id)
            for position in range(1, 5):
                truck = str(8100 + position)
                manage_truck(operation.gateway, 'add_truck', {'truck_number': truck}, actor_id=None)
                set_neosubzero_ucc_truck(operation, 'Alpha', position, truck)
                for role in ('driver', 'flyer'):
                    if (position, role) == (1, 'driver'):
                        continue  # The shared fixture already staffs this slot.
                    person = fixture._person(f'TV-{position}-{role}', 'Pat',
                        ('Montgomery' if role == 'driver' else 'Hernandez-Lopez'), area)
                    set_staffing_person_qualification(person, DEICE_QUALIFICATION_KEY, True)
                    set_neosubzero_ucc_assignment(operation, 'Alpha', position, role, person)

            timestamp = datetime(2026, 10, 23, 5, 15)
            for tail, place in (('NTREATED', 'C05'), ('NNEG', 'A05'), ('NRADIO', 'B05'), ('NWARN', 'D05')):
                mission = fixture._departure('UPS' + tail, tail, 'DEN', 3)
                db.session.add(SortDateParkingAssignment(sort_date_operation_id=operation.id,
                    tail_number=tail, ramp_code=place[0], position_code=place))
                if tail == 'NTREATED':
                    db.session.add(NeoSubZeroPretreatState(sort_date_operation_id=operation.id,
                        tail_number=tail, pass1_started_at_utc=timestamp, pass1_ended_at_utc=timestamp,
                        pass2_started_at_utc=timestamp, pass2_ended_at_utc=timestamp,
                        original_mission_id=mission.id))
                elif tail == 'NNEG':
                    fixture._deice_event(mission, 'negative')
                elif tail == 'NRADIO':
                    mission.actual_block_out_datetime_utc = timestamp
                    event = fixture._deice_event(mission, 'cleared')
                    event.radio_state = 'pending'
                else:
                    fixture._deice_event(mission, 'finished')  # FIN without spray evidence stays in review.
            db.session.commit()

    def assert_pixels(self, before, after, selector=None, *, repainted=False):
        images = [Image.open(BytesIO(data)).convert('RGB') for data in (before, after)]
        self.assertEqual(images[0].size, images[1].size)
        if selector:
            x, y, width, height = (selector[key] for key in ('x', 'y', 'width', 'height'))
            for image in images:
                ImageDraw.Draw(image).rectangle((x - 12, y - 12, x + width + 12, y + height + 12), fill='black')
        difference = ImageChops.difference(*images)
        if repainted:
            # Chromium can rasterize small edges differently after a layer changes.
            self.assertLess(max(ImageStat.Stat(difference).mean), .08, difference.getbbox())
        else:
            self.assertIsNone(difference.getbbox())

    def exercise(self, engine):
        case = fixtures.UccBrowserTest()
        case.setUpClass()
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        try:
            self.seed(case)
            with closing(getattr(case.pw, engine).launch()) as browser:
                context = browser.new_context(viewport={'width': 1920, 'height': 1080}, service_workers='block')
                context.add_init_script('Element.prototype.requestFullscreen = undefined')
                page = context.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                case.login(page)
                case.ready(page, '/neosubzero/ucc')
                page.locator('[data-ucc-tv-enter]').click()
                board = page.locator('.ucc-tv-wallboard')
                ramp = board.get_by_role('button', name='RAMP VIEW')
                crew = board.get_by_role('button', name='CREW VIEW')
                weather = board.locator('.ucc-tv-weather')
                alpha = board.locator('.ucc-tv-ramp').first
                expect(ramp).to_have_attribute('aria-pressed', 'true')
                page.evaluate('window.crewTestSameDocument = true')

                if self.baseline_assets:
                    legacy = context.new_page()

                    def serve_asset(kind, body):
                        return lambda route: route.fulfill(content_type=kind, body=body)

                    for name, (kind, body) in self.baseline_assets.items():
                        legacy.route(f'**/{name}*', serve_asset(kind, body))
                    case.ready(legacy, '/neosubzero/ucc')
                    expect(legacy.locator('.ucc-tv-wallboard')).to_be_visible()
                    page.bring_to_front()

                measure = '''ramp => {
                    const box = node => node.getBoundingClientRect();
                    const pad = ramp.querySelector('.ucc-tv-pad');
                    const crews = Array.from(ramp.querySelectorAll('.ucc-tv-crew-column .ucc-tv-crew'));
                    return {staging:box(pad.parentElement).height, pad:box(pad).toJSON(),
                        crews:Object.fromEntries(crews.map(el => [el.dataset.uccTvPosition, box(el).toJSON()])),
                        truck:parseFloat(getComputedStyle(crews[0].querySelector('strong')).fontSize),
                        overlap:crews.some(el => box(el).bottom > box(pad.parentElement).bottom + 1)};
                }'''
                for width, height in SIZES:
                    with self.subTest(engine=engine, width=width):
                        page.set_viewport_size({'width': width, 'height': height})
                        if ramp.get_attribute('aria-pressed') == 'false':
                            ramp.click()
                        ramp.blur()
                        page.mouse.move(0, 0)
                        base = alpha.evaluate(measure)
                        selector = board.locator('.ucc-tv-view-selector').bounding_box()
                        if self.baseline_assets:
                            legacy.set_viewport_size({'width': width, 'height': height})
                            self.assert_pixels(legacy.screenshot(), page.screenshot(), selector)
                        baseline = page.screenshot(path=str(EVIDENCE / f'{engine}-{width}-ramp.png'))
                        weather_box = weather.bounding_box()
                        original_weather = weather.screenshot(style=HIDE_SELECTOR)
                        parked = alpha.locator('.ucc-tv-stands .ucc-tv-plane.state-negative')
                        parking_height = parked.bounding_box()['height']
                        positions = board.locator('.ucc-tv-stand > .ucc-tv-position').all_text_contents()
                        crew.click()
                        expect(crew).to_have_attribute('aria-pressed', 'true')
                        self.assertTrue(page.evaluate('window.crewTestSameDocument'))
                        crew.blur()
                        page.mouse.move(0, 0)
                        self.assertEqual(weather.bounding_box(), weather_box)
                        self.assert_pixels(original_weather, weather.screenshot(style=HIDE_SELECTOR))
                        expect(board.locator('.ucc-tv-ramp > h2')).to_have_text(['Alpha', 'Bravo', 'Charlie', 'Delta', 'Echo'])
                        enlarged = alpha.evaluate(measure)
                        self.assertGreater(enlarged['staging'], base['staging'] * 1.75)
                        self.assertGreater(enlarged['truck'], base['truck'])
                        self.assertFalse(enlarged['overlap'], enlarged)
                        pad = enlarged['pad']
                        crews = enlarged['crews']
                        for left, right in (('1', '4'), ('2', '3')):
                            self.assertLessEqual(crews[left]['right'], pad['left'])
                            self.assertGreaterEqual(crews[right]['left'], pad['right'])
                        self.assertLess(crews['1']['top'], crews['2']['top'])
                        self.assertLess(crews['4']['top'], crews['3']['top'])
                        expect(alpha.locator('.ucc-tv-pad .state-deicing .ucc-tv-plane-icon')).to_be_visible()
                        expect(alpha.locator('.ucc-tv-pad-status')).to_contain_text('DEIC')
                        expect(alpha.locator('.ucc-tv-pad .ucc-tv-plane-etd')).to_contain_text('ETD ')
                        expect(board.locator('.ucc-tv-pad-idle').last).to_be_visible()
                        self.assertEqual(board.locator('.ucc-tv-stand > .ucc-tv-position').all_text_contents(), positions)
                        compact = alpha.locator('.ucc-tv-compact.state-negative')
                        expect(compact.locator(':scope > *')).to_have_text(['NNEG', 'DEN', compact.locator('time').inner_text()])
                        expect(compact).to_have_css('background-color', 'rgb(195, 72, 84)')
                        self.assertLessEqual(compact.bounding_box()['height'], parking_height * .6)
                        expect(board.locator('.ucc-tv-compact.state-pretreated')).to_have_css('background-color', 'rgb(40, 93, 168)')
                        expect(board.locator('.ucc-tv-pad .state-radio-pending, .ucc-tv-compact.state-radio-pending')).to_be_visible()
                        expect(board.locator('.ucc-tv-remote')).to_contain_text('NREMOTE')
                        expect(board.locator('.ucc-tv-early')).to_contain_text('Early Out Crew')
                        expect(board.locator('.ucc-tv-warnings')).to_contain_text('FIN EVIDENCE REVIEW · NWARN')
                        dimensions = board.locator('.ucc-tv-ramps').evaluate('el => [el.scrollWidth-el.clientWidth, el.scrollHeight-el.clientHeight]')
                        self.assertEqual(dimensions, [0, 0])
                        crew.blur()
                        page.screenshot(path=str(EVIDENCE / f'{engine}-{width}-crew.png'))
                        ramp.click()
                        ramp.blur()
                        page.mouse.move(0, 0)
                        expect(ramp).to_have_attribute('aria-pressed', 'true')
                        page.evaluate(AFTER_PAINT)
                        self.assertEqual(alpha.evaluate(measure), base)
                        self.assert_pixels(baseline, page.screenshot(), selector, repainted=True)

                crew.click()
                theme = board.get_by_role('button', name='Light appearance')
                theme.click()
                theme.blur()
                page.mouse.move(0, 0)
                expect(board).to_have_attribute('data-ucc-tv-theme', 'light')
                expect(crew).to_have_attribute('aria-pressed', 'true')
                page.evaluate(AFTER_PAINT)
                light_box = weather.bounding_box()
                light_weather = weather.screenshot(style=HIDE_SELECTOR)
                ramp.click()
                ramp.blur()
                page.mouse.move(0, 0)
                page.evaluate(AFTER_PAINT)
                self.assertEqual(weather.bounding_box(), light_box)
                self.assert_pixels(light_weather, weather.screenshot(style=HIDE_SELECTOR), repainted=True)
                crew.click()
                crew.focus()
                with case.app.app_context():
                    operation = db.session.get(SortDateOperation, case.operation_id)
                    area = db.session.get(StaffingUnit,
                        db.session.get(StaffingPerson, case.people[0]).work_assignments[0].work_area_unit_id)
                    person = fixtures.NeoSubZeroUccTest()._person('TV-ECHO', 'Casey', 'Travers', area)
                    set_staffing_person_qualification(person, DEICE_QUALIFICATION_KEY, True)
                    set_neosubzero_ucc_assignment(operation, 'Echo', 1, 'driver', person)
                    db.session.commit()
                expect(board.locator('.ucc-tv-ramp').last).to_contain_text('C. Travers', timeout=15000)
                expect(crew).to_be_focused()
                page.reload()
                expect(crew).to_have_attribute('aria-pressed', 'true')
                expect(board).to_have_attribute('data-ucc-tv-theme', 'light')
                self.assertFalse(errors, errors)
        finally:
            case.tearDownClass()

    def test_chromium(self):
        self.exercise('chromium')

    def test_webkit(self):
        self.exercise('webkit')
