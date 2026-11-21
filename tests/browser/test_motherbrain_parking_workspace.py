"""Authenticated parking workspaces with isolated Sort records."""
import unittest
from datetime import date, time

from playwright.sync_api import expect

from app.extensions import db
from app.models import Gateway, MasterFlightSchedule, SortDateOperation, SortDateParkingAssignment
from tests import test_google_motherbrain_parking as parking
from tests.browser import test_mobile_drawer as fixture


class ParkingWorkspaceBrowserTest(unittest.TestCase):
    def test_responsive_workspaces(self):
        env = fixture.MobileDrawerBrowserTest
        env.setUpClass()
        self.addCleanup(env.tearDownClass)
        with env.app.app_context():
            data = parking.GoogleMotherBrainParkingTest()
            data.gateway = Gateway.query.filter_by(code="RFD").one()
            data.operation = SortDateOperation(gateway_id=data.gateway.id, gateway_code="RFD",
                                              sort_name="night", sort_date=date(2026, 8, 7))
            db.session.add(data.operation)
            db.session.flush()
            for index, position in enumerate(("A01", "A06", "B04", None, None, None), start=401):
                tail = f"N{index}UP"
                data._add_tail(tail)
                if position:
                    data._park(tail, position)
            data._park("N991UP", "E01")  # Parked tail without a linked mission.
            for inbound in (True, False):
                db.session.add(MasterFlightSchedule(gateway_id=data.gateway.id, gateway_code="RFD",
                    mission_type="arrival" if inbound else "departure", sort_name="night",
                    flight_number="UPS701" if inbound else "UPS401", origin="EWR" if inbound else "RFD",
                    destination="RFD" if inbound else "SDF", active=True,
                    active_days="fri", planned_time_local=time(22) if inbound else time(2)))
            db.session.commit()
            op_id = data.operation.id

        for engine, width, height in (("chromium", 1440, 900), ("webkit", 390, 844), ("webkit", 820, 1180)):
            with self.subTest(engine=engine, width=width):
                browser = getattr(env.pw, engine).launch()
                try:
                    page = browser.new_page(viewport={"width": width, "height": height},
                                            is_mobile=engine == "webkit", has_touch=engine == "webkit")
                    page.set_default_timeout(9000)
                    page.add_init_script("""Object.defineProperty(window, 'NeoLiveUpdates', {
                        configurable: true, set(api) {
                            Object.defineProperty(window, 'NeoLiveUpdates', {value: {...api, create(options) {
                                const controller = api.create(options);
                                if (document.querySelector('[data-parking-plan]')) window.parkingPoll = controller.poll;
                                return controller;
                            }}, configurable: true});
                        }
                    });""")
                    env().login(page)
                    for name, path in (("plan", f"/motherbrain/parking-plan/{op_id}"),
                                       ("rules", f"/motherbrain/parking-rules?operation_id={op_id}")):
                        response = page.goto(env.origin + path, wait_until="domcontentloaded")
                        self.assertEqual(response.status, 200)
                        page.evaluate("document.fonts.ready")
                        page.screenshot(path=str(env.evidence / f"{name}-{width}.png"))
                        expect(page.locator("main")).to_be_visible()
                        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), width)
                        if name == "plan":
                            self.check_plan(page, width, env, op_id)
                        else:
                            self.check_rules(page, width)
                finally:
                    browser.close()

    def check_plan(self, page, width, env, op_id):
        ramps = page.locator("#parking-ramps")
        expect(ramps).to_be_visible()
        self.assertLess(ramps.bounding_box()["y"], page.viewport_size["height"])
        self.assertLess(ramps.bounding_box()["y"], page.locator("#parking-optimizer").bounding_box()["y"])
        jump = page.locator(".parking-status-jump").first
        expect(jump).to_be_visible()
        jump.click()
        self.assertTrue(page.locator(":target").is_visible())
        page.locator(".mb-jumps a[href='#parking-unparked']").click()
        if width == 1440:
            card = page.locator("#PARKING-TAIL-N404UP")
            lane = page.locator('[data-parking-slot-id="slot:A:A02:1"]')
            card.drag_to(lane)
            expect(lane).to_have_attribute("data-occupied-tail", "N404UP")
            card.drag_to(page.locator("#parking-unparked h2"))
            expect(lane).to_have_attribute("data-occupied-tail", "")

        if width == 390:
            page.locator(".mb-parking-mobile-tools summary").click()
            form = page.locator(".parking-mobile-hot-note-controls")
            form.locator("[name=tail_number]").select_option("N401UP")
            note = form.locator("[name=note]")
            note.fill("Synthetic parking note")
            with env.app.app_context():
                data = parking.GoogleMotherBrainParkingTest()
                data.operation = db.session.get(SortDateOperation, op_id)
                temporary = data._park("N406UP", "B01")
                db.session.commit()
                parking_id = temporary.id
            try:
                page.evaluate("window.parkingPoll()")
                expect(page.locator("[data-parking-plan]")).to_have_attribute("data-parking-reconcile-deferred", "true")
                expect(note).to_have_value("Synthetic parking note")
                form.get_by_role("button").click()
                page.locator(".mb-jumps a[href='#parking-unparked']").click()
                page.locator(".mb-parking-mobile-tools summary").click()
                form.locator("[name=tail_number]").select_option("N401UP")
                expect(note).to_have_value("Synthetic parking note")
                form.locator("[name=is_hot]").select_option("1")
                form.get_by_role("button").click()
                expect(page.locator("#PARKING-TAIL-N401UP")).to_have_attribute("data-tail-operational-status", "hot")
                page.locator(".mb-jumps a[href='#parking-unparked']").click()
                page.locator(".mb-parking-mobile-tools summary").click()
                form.locator("[name=tail_number]").select_option("N401UP")
                expect(note).to_have_value("Synthetic parking note")
                form.locator("[name=is_hot]").select_option("0")
                note.fill("")
                form.get_by_role("button").click()
            finally:
                with env.app.app_context():
                    db.session.delete(db.session.get(SortDateParkingAssignment, parking_id))
                    db.session.commit()

            page.locator(".mb-jumps a[href='#parking-unparked']").click()
            page.locator(".mb-parking-mobile-tools summary").click()
            assign = page.locator(".parking-mobile-assign-controls")
            lane = page.locator('[data-parking-slot-id="slot:A:A03:1"]')
            assign.locator("[name=tail_number]").select_option("N405UP")
            assign.locator("[name=position_code]").select_option("A03")
            assign.get_by_role("button").click()
            expect(lane).to_have_attribute("data-occupied-tail", "N405UP")
            assign.locator("[name=tail_number]").select_option("N404UP")
            page.once("dialog", lambda dialog: dialog.accept())
            assign.get_by_role("button").click()
            expect(lane).to_have_attribute("data-occupied-tail", "N404UP")
            remove = page.locator(".parking-mobile-remove-controls")
            remove.locator("[name=tail_number]").select_option("N404UP")
            remove.get_by_role("button").click()
            expect(lane).to_have_attribute("data-occupied-tail", "")

    def check_rules(self, page, width):
        for group in ("flight-rules", "physical-restrictions", "optimizer-configuration"):
            page.locator(f".mb-jumps a[href='#{group}']").click()
            expect(page.locator(":target")).to_have_attribute("id", group)
        page.locator(".mb-parking-diagnostics").last.locator("summary").click()
        expect(page.get_by_role("heading", name="HARD RULES", exact=True)).to_be_visible()
        if width == 1440:
            new_rule = page.locator("#parking-rule-new-arrival_parking_preference")
            new_rule.locator("select").first.select_option(index=1)
            new_rule.locator("select").last.select_option("A")
            new_rule.locator("input[placeholder='OPTIONAL NOTE']").fill("Synthetic preferred ramp")
            new_rule.get_by_role("button", name="SAVE").click()
            saved = page.locator("form[data-parking-rule-row='arrival_parking_preference']").filter(
                has=page.locator("input[name='rule_ids']"))
            expect(saved.locator("input[name^='note_']")).to_have_value("Synthetic preferred ramp")
            saved.locator("select").last.select_option("B")
            saved.get_by_role("button", name="SAVE").click()
            expect(saved.locator("select").last).to_have_value("B")
            saved.locator("input[name^='delete_rule_']").check()
            saved.get_by_role("button", name="SAVE").click()
            expect(saved).to_have_count(0)
