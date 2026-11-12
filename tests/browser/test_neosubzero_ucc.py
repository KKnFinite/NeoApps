"""Real UCC views/mutations in isolated SQLite; no external integrations."""
import json
import re
import unittest
from copy import deepcopy
from datetime import datetime
from unittest.mock import patch

from playwright.sync_api import expect

from app.extensions import db
from app.models import (Gateway, NeoSubZeroDepartureDeiceEvent, NeoSubZeroSprayRecord,
                        SortDateOperation, SortDateParkingAssignment, StaffingPerson)
from app.services.neosubzero_staffing import set_staffing_person_qualification, DEICE_QUALIFICATION_KEY
from app.services.neosubzero_ucc import set_neosubzero_ucc_assignment
from tests.browser.test_mobile_drawer import MobileDrawerBrowserTest
from tests.test_neosubzero_ucc import NeoSubZeroUccTest


class UccBrowserTest(unittest.TestCase):
    login = MobileDrawerBrowserTest.login
    ready = MobileDrawerBrowserTest.ready
    @classmethod
    def setUpClass(cls):
        MobileDrawerBrowserTest.setUpClass.__func__(cls)
        with cls.app.app_context():
            fixture = NeoSubZeroUccTest()
            gateway = Gateway.query.filter_by(code="RFD").one()
            from app.services.parking_rules import ensure_parking_settings
            ensure_parking_settings(gateway).include_throat_default=True
            fixture.operation = SortDateOperation(sort_date=datetime(2026, 10, 22).date(), gateway_id=gateway.id, gateway_code="RFD", sort_name="night")
            db.session.add(fixture.operation)
            db.session.flush()
            cls.operation_id = fixture.operation.id
            from app.services.neosubzero_trucks import manage_truck
            manage_truck(gateway,'add_truck',{'truck_number':'UCC-831'},actor_id=None)
            manage_truck(gateway,'add_truck',{'truck_number':'12'},actor_id=None)
            night = fixture._unit("sort", "Night")
            aviation = fixture._unit("operation", "Aviation Services", night)
            deice = fixture._unit("work_area", "Deice", aviation)
            cls.people = []
            for i in range(3):
                person = fixture._person(f"UCC-B{i}", "Christopher" if i == 0 else "Taylor", f"Longname-Deicer-{i}", deice)
                set_staffing_person_qualification(person, DEICE_QUALIFICATION_KEY, True)
                cls.people.append(person.id)
            set_neosubzero_ucc_assignment(fixture.operation, "Alpha", 1, "driver", db.session.get(StaffingPerson, cls.people[0]))
            cls.missions = []
            for i, (ramp, position) in enumerate((("A", "A03"), ("A", "A04"), ("A", "A05"), ("B", "B08"), ("C", "C09"), ("E", "E02"))):
                mission = fixture._departure(f"UPS20{i}", f"N44{i}UP", "SDF", 3)
                cls.missions.append(mission.id)
                db.session.add(SortDateParkingAssignment(sort_date_operation_id=cls.operation_id, tail_number=mission.assigned_tail_number, ramp_code=ramp, position_code=position, lane_number=1))
                event = fixture._deice_event(mission, "deice_planned")
                if i < 3:
                    mission.actual_block_out_datetime_utc = datetime(2026, 10, 23, 5, i)
                if i == 0:
                    event.status, event.configured_at_utc = "configured", datetime(2026, 10, 23, 5, 1)
            db.session.commit()
        cls.current = patch("app.neonodes.neosubzero.routes.current_neosubzero_operation", side_effect=lambda gateway: db.session.get(SortDateOperation, cls.operation_id))
        weather = NeoSubZeroUccTest._weather_context_fixture(None)
        template_hour = weather["current_sort"]["hours"][0]
        weather["current_sort"]["hours"] = []
        for i in range(12):
            hour = deepcopy(template_hour)
            hour["time"] = f"{(18+i)%24:02d}00"
            weather["current_sort"]["hours"].append(hour)
        forecast = weather["future_forecast"][0]
        weather["future_forecast"] = [dict(forecast, date_label=label) for label in (
            "TUE SEP 01", "WED SEP 02", "THU SEP 03", "MON SEP 07")]
        cls.weather = patch("app.neonodes.neosubzero.routes.neosubzero_weather_context", return_value=weather)
        cls.weather_data = weather
        cls.weather_revision = patch("app.neonodes.neosubzero.routes.neosubzero_weather_revision", return_value="weather-r1")
        cls.refresh = patch("app.neonodes.neosubzero.routes.subzero_refresh_status", return_value={"live_screen_refresh_interval_ms":1000})
        cls.weather_revision_mock = cls.weather_revision.start()
        for item in (cls.current, cls.weather, cls.refresh):
            item.start()

    @classmethod
    def tearDownClass(cls):
        for item in (cls.current, cls.weather, cls.weather_revision, cls.refresh):
            item.stop()
        MobileDrawerBrowserTest.tearDownClass.__func__(cls)

    def test_physical_parking_rows_and_recorded_start_in_all_views(self):
        order = ((4, 8), (3, 7), (2, 6), (1, 5))
        ramp_names = ("Alpha", "Bravo", "Charlie", "Delta", "Echo")
        mission_id = self.missions[0]
        browser = self.pw.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1366, "height": 900}, service_workers="block")
            self.login(page)
            self.ready(page, "/neosubzero/ucc")
            alpha = page.locator('[data-ucc-ramp="Alpha"]')
            aircraft = f'[data-ucc-aircraft="{mission_id}"]'
            expect(alpha.locator('[data-ucc-queue-position="1"] ' + aircraft)).to_be_visible()
            expect(alpha.locator("[data-ucc-deice-pad] .ucc-plane")).to_have_count(0)
            for width in (1366, 1920):
                page.set_viewport_size({"width": width, "height": 1080})
                for name in ramp_names:
                    ramp = page.locator(f'[data-ucc-ramp="{name}"]')
                    tops = []
                    for row, (left, right) in enumerate(order, 1):
                        left_box, right_box, queue_box = [
                            ramp.locator(selector).bounding_box() for selector in (
                                f'[data-ucc-parking="{name[0]}{left:02d}"]',
                                f'[data-ucc-parking="{name[0]}{right:02d}"]',
                                f'.ucc-grid-row-{row}.ucc-grid-column-2',
                            )
                        ]
                        self.assertAlmostEqual(left_box["y"], right_box["y"], delta=1)
                        self.assertAlmostEqual(left_box["y"], queue_box["y"], delta=1)
                        tops.append(left_box["y"])
                    gaps = [b - a for a, b in zip(tops, tops[1:])]
                    limit = 120 if width == 1366 else 110
                    self.assertTrue(all(0 < gap < limit for gap in gaps), gaps)
                    self.assertLessEqual(max(gaps) - min(gaps), 1)
                self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth-innerWidth"), 1)

            with self.app.app_context():
                event = NeoSubZeroDepartureDeiceEvent.query.filter_by(sort_date_mission_id=mission_id).one()
                record = NeoSubZeroSprayRecord(
                    sort_date_operation_id=self.operation_id, sort_date_mission_id=mission_id,
                    departure_deice_event_id=event.id, tail_number_snapshot=event.tail_number,
                    pass_number=1, position_number=1, truck_number_snapshot="UCC-831",
                    pass_type="type_i", fluid_name_snapshot="Type I", concentration_percent_snapshot=50,
                    started_at_utc=datetime(2026, 10, 23, 5, 10),
                )
                db.session.add(record)
                db.session.commit()
                record_id = record.id

            def remove_record():
                with self.app.app_context():
                    db.session.delete(db.session.get(NeoSubZeroSprayRecord, record_id))
                    db.session.commit()

            self.addCleanup(remove_record)
            expect(alpha.locator("[data-ucc-deice-pad] " + aircraft)).to_have_class(
                "ucc-plane state-deicing", timeout=10000)
            expect(alpha.locator("[data-ucc-queue-position] " + aircraft)).to_have_count(0)
            page.evaluate("document.documentElement.requestFullscreen = undefined")
            page.locator("[data-ucc-tv-enter]").click()
            tv = page.locator(".ucc-tv-wallboard")
            expect(tv.locator(".ucc-tv-pad .state-deicing")).to_have_count(1)
            expect(tv.locator(".ucc-tv-stands .state-deicing")).to_have_count(0)
            for name, ramp in zip(ramp_names, tv.locator(".ucc-tv-ramp").all()):
                labels = ramp.locator(".ucc-tv-stands .ucc-tv-position").all_text_contents()
                parking_labels = [label for label in labels if label.startswith(name[0])]
                self.assertEqual(parking_labels, [f"{name[0]}{stand:02d}" for row in order for stand in row])
                self.assertFalse(any("MIDDLE" in label for label in labels))
            expect(tv.locator(".ucc-tv-stands .ucc-tv-plane[aria-label*='B08'] .facing-left")).to_be_visible()
            page.screenshot(path=str(self.evidence / "ucc-physical-tv-1080p.png"))
            tv.get_by_role("button", name="Exit TV Mode").click()

            page.set_viewport_size({"width": 390, "height": 844})
            page.locator('[data-ucc-view="ramp"]').click()
            expect(alpha.locator("[data-ucc-deice-pad] " + aircraft)).to_be_visible()
            expect(alpha.locator("[data-ucc-queue-position] " + aircraft)).to_have_count(0)
            previous = float("-inf")
            for left, right in order:
                left_box = alpha.locator(f'[data-ucc-parking="A{left:02d}"]').bounding_box()
                right_box = alpha.locator(f'[data-ucc-parking="A{right:02d}"]').bounding_box()
                self.assertAlmostEqual(left_box["y"], right_box["y"], delta=1)
                self.assertGreater(left_box["y"], previous)
                previous = left_box["y"]
        finally:
            browser.close()

    def test_ucc_physical_board_mobile_staffing_live_and_tv(self):
        measurements = []
        for engine in ("chromium", "webkit"):
            browser = getattr(self.pw, engine).launch()
            context = browser.new_context(viewport={"width":1440, "height":1000})
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            self.login(page)
            self.ready(page, "/neosubzero/ucc")
            for width, height in ((1920,1080), (1366,900), (1024,768), (820,1180), (390,844), (375,812)):
                page.set_viewport_size({"width":width,"height":height})
                expect(page.locator('[data-ucc-ramp="Alpha"]')).to_be_visible()
                geometry = page.evaluate("""() => ({width:innerWidth, overflow:document.documentElement.scrollWidth-innerWidth, ramps:[...document.querySelectorAll('[data-ucc-ramp]')].slice(0,5).map(el=>({name:el.dataset.uccRamp,x:el.getBoundingClientRect().x,y:el.getBoundingClientRect().y,width:el.getBoundingClientRect().width})), fields:[...document.querySelectorAll('.ucc-team[open] input:not([type=hidden]), .ucc-team[open] select')].map(el=>{const r=el.getBoundingClientRect(); return {left:r.left,right:r.right};})})""")
                self.assertLessEqual(geometry["overflow"], 1, geometry)
                self.assertEqual([r["name"] for r in geometry["ramps"]], ["Alpha","Bravo","Charlie","Delta","Echo"])
                if width > 900:
                    self.assertEqual(len({round(r["y"]) for r in geometry["ramps"]}), 1, geometry)
                    self.assertLess(max(r["width"] for r in geometry["ramps"]) - min(r["width"] for r in geometry["ramps"]), 1)
                    expect(page.locator('[data-ucc-parking="C09"] .ucc-plane')).to_be_visible()
                    bounds={code:page.locator(f'[data-ucc-parking="{code}"]').bounding_box() for code in ('A01','A04','A05','A09','A10')}
                    self.assertLess(bounds['A01']['x'],bounds['A10']['x'])
                    self.assertLess(bounds['A10']['x'],bounds['A05']['x'])
                    self.assertLess(bounds['A04']['y'],bounds['A01']['y'])
                    self.assertLess(bounds['A10']['y'],bounds['A09']['y'])
                    self.assertEqual(page.locator('[data-ucc-ramp="Alpha"] [data-ucc-deice-pad]').count(),1)
                    self.assertEqual(page.locator('[data-ucc-ramp="Alpha"] [data-ucc-queue-position]').count(),4)
                    for ramp in page.locator('[data-ucc-ramp]').all()[:5]:
                        one, two, four, three, pad, parking = [ramp.locator(selector).bounding_box() for selector in (
                            '.position-1', '.position-2', '.position-4', '.position-3', '[data-ucc-deice-pad]', '.ucc-parking-grid')]
                        self.assertLessEqual(one['x'] + one['width'], pad['x'])
                        self.assertLessEqual(pad['x'] + pad['width'], four['x'])
                        self.assertAlmostEqual(one['y'], four['y'], delta=1)
                        self.assertAlmostEqual(two['y'], three['y'], delta=1)
                        self.assertLess(two['y'], parking['y'])
                    if width in (1366, 1920):
                        picker = page.locator(".ucc-team-controls:popover-open")
                        left = page.locator('[data-ucc-editor="Alpha-1"]')
                        button = left.locator('[data-ucc-crew-edit="driver"]')
                        expect(left.locator('[data-ucc-crew-line="driver"]')).to_contain_text('D · Longname-Deicer-0')
                        expect(left.locator('[data-ucc-crew-line="driver"]')).not_to_contain_text('Christopher')
                        right = page.locator('[data-ucc-editor="Echo-4"]')
                        expect(right.locator('[data-ucc-crew-form="truck"] select')).to_be_visible()
                        button.click()
                        expect(picker.locator('[data-ucc-crew-form="driver"] option:checked')).to_contain_text('Christopher Longname-Deicer-0')
                        box = picker.bounding_box()
                        self.assertGreater(box["width"], geometry["ramps"][0]["width"])
                        self.assertGreaterEqual(box["x"], 0)
                        self.assertLessEqual(box["x"] + box["width"], width)
                        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth-innerWidth"), 1)
                        page.screenshot(path=str(self.evidence / f"ucc-{engine}-{width}-crew-editor.png"))
                        page.keyboard.press('Escape')
                        expect(picker).to_be_hidden()
                        expect(button).to_have_attribute('aria-expanded', 'false')
                        expect(right.locator('[data-ucc-crew-edit]:visible')).to_have_count(0)
                        self.assertAlmostEqual(page.locator('[data-ucc-ramp="Echo"]').bounding_box()["width"],
                                               geometry["ramps"][-1]["width"], delta=1)
                else:
                    expect(page.locator('.ucc-ramp-diagram').first).to_be_hidden()
                measurements.append({"engine":engine, **geometry})
                page.screenshot(path=str(self.evidence / f"ucc-{engine}-{width}.png"), full_page=True)
                if width == 1920:
                    page.screenshot(path=str(self.evidence / f"ucc-management-{engine}-1920x1080.png"))
            page.set_viewport_size({"width":1440,"height":1000})
            weather_toggle = page.locator('[data-ucc-weather-toggle]')
            current_weather = page.locator('#ucc-weather-current')
            upcoming = page.locator('#ucc-weather-upcoming')
            expect(weather_toggle).to_have_attribute('aria-expanded', 'true')
            expect(upcoming.locator('article:visible')).to_have_count(4)
            alpha = page.locator('[data-ucc-ramp="Alpha"]')
            expanded_top = alpha.evaluate('el => el.getBoundingClientRect().top + scrollY')
            weather_toggle.click()
            expect(current_weather).to_be_hidden()
            expect(upcoming).to_be_hidden()
            self.assertLess(alpha.evaluate('el => el.getBoundingClientRect().top + scrollY'), expanded_top)
            page.reload()
            expect(weather_toggle).to_have_attribute('aria-expanded', 'false')
            expect(current_weather).to_be_hidden()
            expect(upcoming).to_be_hidden()
            self.weather_data["current"]["temperature"] = "29°F" if engine == "chromium" else "30°F"
            self.weather_data["revision"] = self.weather_revision_mock.return_value = f"weather-{engine}"
            expect(current_weather.locator('.neosubzero-weather-primary > strong')).to_have_text(
                self.weather_data["current"]["temperature"], timeout=10000)
            expect(current_weather).to_be_hidden()
            expect(upcoming).to_be_hidden()
            weather_toggle.click()
            expect(current_weather.locator('.neosubzero-weather-current-sort')).to_be_visible()
            expect(upcoming.locator('article:visible')).to_have_count(4)
            slot = page.locator('[data-ucc-editor="Alpha-2"]')
            for role, value in (('truck', '12'), ('driver', str(self.people[2])), ('flyer', str(self.people[1]))):
                control = slot.locator(f'[data-ucc-crew-form="{role}"] select')
                pencil = slot.locator(f'[data-ucc-crew-edit="{role}"]')
                expect(control).to_be_visible()
                expect(pencil).to_be_hidden()
                version = control.locator('xpath=ancestor::form').locator('[name="expected_version"]')
                before_save = version.input_value()
                with page.expect_response(lambda r: r.request.method == 'POST') as saved:
                    control.select_option(value)
                self.assertEqual(saved.value.status, 200)
                expect(slot.locator('[data-ucc-management-save]')).to_have_text('Saved')
                expect(version).not_to_have_value(before_save)
                expect(control).to_have_value(value)
                expect(control).to_be_hidden()
                pencil.click()
                expect(control).to_be_visible()
                assigned_version = version.input_value()
                with page.expect_response(lambda r: r.request.method == 'POST') as cleared:
                    control.select_option('')
                self.assertEqual(cleared.value.status, 200)
                expect(version).not_to_have_value(assigned_version)
                expect(control).to_have_value('')
            self.ready(page, "/neosubzero/ucc")
            plane = page.locator('[data-ucc-parking="C09"] .ucc-plane')
            plane.scroll_into_view_if_needed()
            plane.click()
            expect(page.locator('[data-ucc-info-mission="%s"]' % self.missions[4])).to_be_visible()
            self.assertEqual(page.locator('[data-ucc-info] input[name*="gallons"], [data-ucc-info] button[name="clear"]').count(), 0)
            # Device-local TV preference never changes a second browser context.
            page.evaluate("document.documentElement.requestFullscreen = undefined")
            page.set_viewport_size({"width":2560,"height":1440})
            weather_toggle.click()
            page.locator('[data-ucc-tv-enter]').click()
            expect(page.locator('body')).to_have_class(re.compile('neosubzero-ucc-tv-mode'))
            expect(page.locator('[data-operational-topbar]')).to_be_hidden()
            expect(weather_toggle).to_be_hidden()
            expect(page.locator('.ucc-tv-weather')).to_be_visible()
            page.screenshot(path=str(self.evidence / f"ucc-{engine}-tv.png"), full_page=True)
            other = browser.new_context(viewport={"width":390,"height":844})
            mobile = other.new_page()
            self.login(mobile)
            self.ready(mobile,"/neosubzero/ucc")
            expect(mobile.locator('[data-ucc-weather-toggle]')).to_be_hidden()
            expect(mobile.locator('[data-ucc-crew-edit]:visible')).to_have_count(0)
            expect(mobile.locator('.ucc-pill select:visible').first).to_be_visible()
            mobile.locator('[data-ucc-view="ramp"]').click()
            expect(mobile.locator('[data-ucc-ramp="Alpha"] .ucc-ramp-diagram')).to_be_visible()
            expect(mobile.locator('[data-ucc-crew-edit]:visible')).to_have_count(0)
            mobile.locator('[data-ucc-view="staffing"]').click()
            self.assertFalse(mobile.locator('body').evaluate("el=>el.classList.contains('neosubzero-ucc-tv-mode')"))
            page.get_by_role('button', name='Exit TV Mode').click()
            page.set_viewport_size({"width":1440,"height":1000})
            # Real mobile staffing mutation; desktop receives it without a reload.
            editor = mobile.locator('[data-ucc-editor="Bravo-2"]')
            select = editor.locator('select[name="person_id"]').first
            with mobile.expect_response(lambda r: r.url.endswith('/neosubzero/ucc') and r.request.method == 'POST') as saved:
                select.select_option(str(self.people[1]))
            self.assertEqual(saved.value.status, 200)
            expect(mobile.locator('[data-ucc-feedback]')).to_contain_text('saved')
            expect(page.locator('[data-ucc-editor="Bravo-2"] .ucc-team-controls')).to_contain_text('Longname-Deicer-1',timeout=10000)
            # Stale concurrent editor gets a visible conflict and retains its draft.
            desktop = page.locator('[data-ucc-editor="Delta-3"]')
            desktop_select = desktop.locator('select[name="person_id"]').first
            desktop_select.evaluate('(e,v)=>{e.value=v;e.dispatchEvent(new Event("input",{bubbles:true}))}',str(self.people[2]))
            mobile_editor = mobile.locator('[data-ucc-editor="Delta-3"]')
            mobile_editor.locator('select[name="person_id"]').first.select_option(str(self.people[0]))
            expect(mobile.locator('[data-ucc-feedback]')).to_contain_text('saved')
            # Critical aircraft state keeps refreshing while a staffing draft has focus.
            expect(desktop_select).to_be_visible()
            with self.app.app_context():
                from app.models import NeoSubZeroDepartureDeiceEvent
                event = NeoSubZeroDepartureDeiceEvent.query.filter_by(sort_date_mission_id=self.missions[0]).one()
                event.status = "cleared"
                db.session.commit()
            expect(page.locator('[data-ucc-aircraft="%s"]' % self.missions[0])).to_have_count(0,timeout=10000)
            expect(desktop_select).to_be_visible()
            expect(desktop_select).to_have_value(str(self.people[2]))
            with page.expect_response(lambda r: r.url.endswith('/neosubzero/ucc') and r.request.method == 'POST') as conflict:
                desktop_select.locator('xpath=ancestor::form').evaluate('e=>e.requestSubmit()')
            self.assertEqual(conflict.value.status, 409)
            expect(page.locator('[data-ucc-feedback]')).to_contain_text('changed')
            expect(desktop_select).to_have_attribute("aria-invalid", "true")
            expect(desktop_select).to_have_value(str(self.people[2]))
            expect(page.locator('[data-ucc-current-link]')).to_be_visible()
            # Explicit reload resolves the draft; whole units move and swap on mobile.
            self.ready(page,"/neosubzero/ucc")
            truck = mobile.locator('[data-ucc-editor="Delta-3"] [data-ucc-truck-form]')
            before_truck = truck.locator('[name="expected_version"]').input_value()
            truck.locator('select[name="truck_number"]').select_option('UCC-831')
            expect(mobile.locator('[data-ucc-feedback]')).to_contain_text('truck saved')
            expect(truck.locator('[name="expected_version"]')).not_to_have_value(before_truck)
            # Start the unit command from a fresh roster-wide version, separate from the truck save.
            self.ready(mobile,"/neosubzero/ucc")
            unit_details = mobile.locator('[data-ucc-editor="unit-Delta-3"]')
            unit_details.locator(':scope > summary').click()
            unit = unit_details.locator('form')
            unit.locator('[name="destination_ramp"]').select_option('Echo')
            unit.locator('[name="destination_position"]').select_option('4')
            unit.locator('button[type="submit"]').click()
            expect(mobile.locator('[data-ucc-feedback]')).to_contain_text('unit moved')
            expect(page.locator('[data-ucc-editor="Echo-4"] .ucc-team-controls')).to_contain_text('UCC-831',timeout=10000)
            expect(mobile.locator('[data-ucc-editor="Echo-4"] [name="truck_number"]')).to_have_value('UCC-831')
            self.ready(mobile,"/neosubzero/ucc")
            target = mobile.locator('[data-ucc-editor="Echo-4"]')
            target.locator('[data-ucc-editor="unit-Echo-4"] > summary').click()
            unit = target.locator('[data-ucc-unit-form]')
            unit.locator('[name="destination_ramp"]').select_option('Bravo')
            unit.locator('[name="destination_position"]').select_option('2')
            unit.locator('[name="resolution"]').select_option('swap')
            unit.locator('button[type="submit"]').click()
            expect(mobile.locator('[data-ucc-feedback]')).to_contain_text('unit moved')
            expect(page.locator('[data-ucc-editor="Bravo-2"] .ucc-team-controls')).to_contain_text('UCC-831',timeout=10000)
            self.assertLessEqual(mobile.evaluate('document.documentElement.scrollWidth-innerWidth'),1)
            mobile.screenshot(path=str(self.evidence / f"ucc-{engine}-mobile-staff-editor.png"), full_page=True)
            with patch("app.neonodes.neosubzero.routes.current_neosubzero_operation",return_value=None), patch("app.neonodes.neosubzero.routes.neosubzero_weather_context",return_value={**self.weather_data,"current_sort":None}):
                self.ready(page,"/neosubzero/ucc")
                self.assertEqual(page.locator('[data-ucc-ramp]').count(),6)
                self.assertEqual(page.locator('[data-ucc-parking]').count(),54)
                self.assertEqual(page.locator('[data-ucc-assignment-form]').count(),0)
                expect(page.locator('[data-ucc-ramp="Echo"]')).to_be_visible()
                page.screenshot(path=str(self.evidence / f"ucc-{engine}-off-sort.png"),full_page=True)
                self.ready(mobile,"/neosubzero/ucc")
                expect(mobile.locator('[data-ucc-ramp="Alpha"]')).to_be_hidden()
                expect(mobile.locator('[data-ucc-upcoming]')).to_be_visible()
                self.assertEqual(mobile.locator('[data-ucc-assignment-form]').count(),0)
            # A newly active sort reaches both displays without borrowing future data.
            expect(page.locator('[data-ucc-editor="Alpha-1"]')).to_have_count(1,timeout=10000)
            expect(mobile.locator('[data-ucc-editor="Alpha-1"]')).to_have_count(1,timeout=10000)
            expect(page.locator('.neosubzero-ucc-head-actions > strong')).to_contain_text('NIGHT')
            self.assertEqual(errors, [])
            other.close(); context.close(); browser.close()
            # Restore only isolated fixture staffing for the second engine.
            with self.app.app_context():
                from app.models import NeoSubZeroUccAssignment, StaffingPerson, NeoSubZeroUccTruckAssignment
                for row in NeoSubZeroUccAssignment.query.all():
                    row.person_id = None
                db.session.flush()
                for truck_row in NeoSubZeroUccTruckAssignment.query.all():
                    truck_row.truck_number = ""
                event = NeoSubZeroDepartureDeiceEvent.query.filter_by(sort_date_mission_id=self.missions[0]).one()
                event.status = "configured"
                set_neosubzero_ucc_assignment(db.session.get(SortDateOperation,self.operation_id),"Alpha",1,"driver",db.session.get(StaffingPerson,self.people[0]),assignment=NeoSubZeroUccAssignment.query.filter_by(ramp="Alpha",position_number=1,team_role="driver").one())
                db.session.commit()
        (self.evidence / 'ucc-layout-measurements.json').write_text(json.dumps(measurements,indent=2),encoding='utf-8')
