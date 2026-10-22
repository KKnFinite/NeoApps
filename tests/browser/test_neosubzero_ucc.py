"""Real UCC views/mutations in isolated SQLite; no external integrations."""
import json
import re
import unittest
from copy import deepcopy
from datetime import datetime
from unittest.mock import patch

from playwright.sync_api import expect

from app.extensions import db
from app.models import Gateway, SortDateOperation, SortDateParkingAssignment, StaffingPerson
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
        cls.weather = patch("app.neonodes.neosubzero.routes.neosubzero_weather_context", return_value=weather)
        cls.weather_data = weather
        cls.weather_revision = patch("app.neonodes.neosubzero.routes.neosubzero_weather_revision", return_value="weather-r1")
        cls.refresh = patch("app.neonodes.neosubzero.routes.subzero_refresh_status", return_value={"live_screen_refresh_interval_ms":1000})
        for item in (cls.current, cls.weather, cls.weather_revision, cls.refresh):
            item.start()

    @classmethod
    def tearDownClass(cls):
        for item in (cls.current, cls.weather, cls.weather_revision, cls.refresh):
            item.stop()
        MobileDrawerBrowserTest.tearDownClass.__func__(cls)

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
            for width, height in ((1920,1080), (1440,1000), (1024,768), (820,1180), (390,844), (375,812)):
                page.set_viewport_size({"width":width,"height":height})
                expect(page.locator('[data-ucc-ramp="Alpha"]')).to_be_visible()
                geometry = page.evaluate("""() => ({width:innerWidth, overflow:document.documentElement.scrollWidth-innerWidth, ramps:[...document.querySelectorAll('[data-ucc-ramp]')].slice(0,5).map(el=>({name:el.dataset.uccRamp,x:el.getBoundingClientRect().x,y:el.getBoundingClientRect().y})), fields:[...document.querySelectorAll('.ucc-team[open] input:not([type=hidden]), .ucc-team[open] select')].map(el=>{const r=el.getBoundingClientRect(); return {left:r.left,right:r.right};})})""")
                self.assertLessEqual(geometry["overflow"], 1, geometry)
                self.assertEqual([r["name"] for r in geometry["ramps"]], ["Alpha","Bravo","Charlie","Delta","Echo"])
                if width > 900:
                    self.assertEqual(len({round(r["y"]) for r in geometry["ramps"]}), 1, geometry)
                    expect(page.locator('[data-ucc-parking="C09"] .ucc-plane')).to_be_visible()
                    bounds={code:page.locator(f'[data-ucc-parking="{code}"]').bounding_box() for code in ('A01','A04','A05','A09','A10')}
                    self.assertLess(bounds['A01']['x'],bounds['A10']['x'])
                    self.assertLess(bounds['A10']['x'],bounds['A05']['x'])
                    self.assertLess(bounds['A01']['y'],bounds['A04']['y'])
                    self.assertLess(bounds['A10']['y'],bounds['A09']['y'])
                    self.assertEqual(page.locator('[data-ucc-ramp="Alpha"] [data-ucc-deice-pad]').count(),1)
                    self.assertEqual(page.locator('[data-ucc-ramp="Alpha"] [data-ucc-queue-position]').count(),2)
                else:
                    expect(page.locator('.ucc-ramp-diagram').first).to_be_hidden()
                measurements.append({"engine":engine, **geometry})
                page.screenshot(path=str(self.evidence / f"ucc-{engine}-{width}.png"), full_page=True)
            page.set_viewport_size({"width":1440,"height":1000})
            plane = page.locator('[data-ucc-parking="C09"] .ucc-plane')
            plane.click()
            expect(page.locator('[data-ucc-info-mission="%s"]' % self.missions[4])).to_be_visible()
            self.assertEqual(page.locator('[data-ucc-info] input[name*="gallons"], [data-ucc-info] button[name="clear"]').count(), 0)
            # Device-local TV preference never changes a second browser context.
            page.evaluate("document.documentElement.requestFullscreen = undefined")
            page.set_viewport_size({"width":2560,"height":1440})
            page.locator('[data-ucc-tv-enter]').click()
            expect(page.locator('body')).to_have_class(re.compile('neosubzero-ucc-tv-mode'))
            expect(page.locator('[data-operational-topbar]')).to_be_hidden()
            page.screenshot(path=str(self.evidence / f"ucc-{engine}-tv.png"), full_page=True)
            other = browser.new_context(viewport={"width":390,"height":844})
            mobile = other.new_page()
            self.login(mobile)
            self.ready(mobile,"/neosubzero/ucc")
            self.assertFalse(mobile.locator('body').evaluate("el=>el.classList.contains('neosubzero-ucc-tv-mode')"))
            page.locator('[data-ucc-tv-exit]').click()
            page.set_viewport_size({"width":1440,"height":1000})
            # Real mobile staffing mutation; desktop receives it without a reload.
            editor = mobile.locator('[data-ucc-editor="Bravo-2"]')
            editor.locator(':scope > summary').click()
            select = editor.locator('select[name="person_id"]').first
            select.select_option(str(self.people[1]))
            with mobile.expect_response(lambda r: r.url.endswith('/neosubzero/ucc') and r.request.method == 'POST') as saved:
                select.locator('xpath=ancestor::form').locator('button[type="submit"]').click()
            self.assertEqual(saved.value.status, 200)
            expect(mobile.locator('[data-ucc-feedback]')).to_contain_text('saved')
            expect(page.locator('[data-ucc-editor="Bravo-2"] > summary')).to_contain_text('Longname-Deicer-1',timeout=10000)
            # Stale concurrent editor gets a visible conflict and retains its draft.
            desktop = page.locator('[data-ucc-editor="Delta-3"]')
            desktop.locator(':scope > summary').click()
            desktop_select = desktop.locator('select[name="person_id"]').first
            desktop_select.select_option(str(self.people[2]))
            mobile_editor = mobile.locator('[data-ucc-editor="Delta-3"]')
            mobile_editor.locator(':scope > summary').click()
            mobile_editor.locator('select[name="person_id"]').first.select_option(str(self.people[0]))
            mobile_editor.locator('[data-ucc-assignment-form]').first.locator('button[type="submit"]').click()
            expect(mobile.locator('[data-ucc-feedback]')).to_contain_text('saved')
            # Critical aircraft state keeps refreshing while a staffing draft has focus.
            desktop_select.focus()
            with self.app.app_context():
                from app.models import NeoSubZeroDepartureDeiceEvent
                event = NeoSubZeroDepartureDeiceEvent.query.filter_by(sort_date_mission_id=self.missions[0]).one()
                event.status = "cleared"
                db.session.commit()
            expect(page.locator('[data-ucc-aircraft="%s"]' % self.missions[0])).to_have_count(0,timeout=10000)
            expect(desktop_select).to_be_focused()
            expect(desktop_select).to_have_value(str(self.people[2]))
            with page.expect_response(lambda r: r.url.endswith('/neosubzero/ucc') and r.request.method == 'POST') as conflict:
                desktop_select.locator('xpath=ancestor::form').locator('button[type="submit"]').click()
            self.assertEqual(conflict.value.status, 409)
            expect(page.locator('[data-ucc-feedback]')).to_contain_text('changed')
            expect(desktop_select).to_have_value(str(self.people[2]))
            expect(page.locator('[data-ucc-current-link]')).to_be_visible()
            # Explicit reload resolves the draft; whole units move and swap on mobile.
            self.ready(page,"/neosubzero/ucc")
            truck = mobile.locator('[data-ucc-editor="Delta-3"] [data-ucc-truck-form]')
            truck.locator('input[name="truck_number"]').fill('UCC-831')
            truck.locator('button[type="submit"]').click()
            expect(mobile.locator('[data-ucc-feedback]')).to_contain_text('truck saved')
            unit_details = mobile.locator('[data-ucc-editor="unit-Delta-3"]')
            unit_details.locator(':scope > summary').click()
            unit = unit_details.locator('form')
            unit.locator('[name="destination_ramp"]').select_option('Echo')
            unit.locator('[name="destination_position"]').select_option('4')
            unit.locator('button[type="submit"]').click()
            expect(mobile.locator('[data-ucc-feedback]')).to_contain_text('unit moved')
            expect(page.locator('[data-ucc-editor="Echo-4"] > summary')).to_contain_text('UCC-831',timeout=10000)
            target = mobile.locator('[data-ucc-editor="Echo-4"]')
            target.locator(':scope > summary').click()
            target.locator('[data-ucc-editor="unit-Echo-4"] > summary').click()
            unit = target.locator('[data-ucc-unit-form]')
            unit.locator('[name="destination_ramp"]').select_option('Bravo')
            unit.locator('[name="destination_position"]').select_option('2')
            unit.locator('[name="resolution"]').select_option('swap')
            unit.locator('button[type="submit"]').click()
            expect(mobile.locator('[data-ucc-feedback]')).to_contain_text('unit moved')
            expect(page.locator('[data-ucc-editor="Bravo-2"] > summary')).to_contain_text('UCC-831',timeout=10000)
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
