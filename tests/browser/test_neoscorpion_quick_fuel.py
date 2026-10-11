"""Quick Fuel uses the live Fueler grid without creating Dispatch work."""

import unittest

from playwright.sync_api import expect

from app.extensions import db
from app.models import NeoScorpionFuelAssignment, NeoScorpionFuelingEvent, SortDateMission
from tests.browser.test_mobile_drawer import MobileDrawerBrowserTest as _Fixture


class QuickFuelBrowserTest(unittest.TestCase):
    def test_desktop_mobile_calculation_tail_reset_and_no_operational_writes(self):
        _Fixture.setUpClass()
        browser = None
        try:
            browser = _Fixture.pw.chromium.launch()
            for width, height in ((1440, 900), (390, 844)):
                with self.subTest(width=width):
                    context = browser.new_context(viewport={"width": width, "height": height})
                    page = context.new_page()
                    _Fixture.login(_Fixture, page, "drawer-watcher")
                    page.goto(_Fixture.origin + "/neoscorpion")
                    expect(page.locator('[data-node-dashboard-tile="quick-fuel"]')).to_have_count(1)
                    response = page.goto(_Fixture.origin + "/neoscorpion/quick-fuel")
                    self.assertEqual(response.status, 200)
                    expect(page.locator('label:has([name="required_fuel"])')).to_contain_text("Fuel Load")
                    expect(page.locator('dt', has_text="Target Onboard")).to_have_count(1)
                    expect(page.locator('a[href="/neoscorpion/quick-fuel"]')).not_to_have_count(0)
                    expect(page.locator("[data-quick-tanks]")).to_be_hidden()
                    expect(page.locator("[data-quick-controls]")).to_be_hidden()
                    page.locator('[name="tail_number"]').fill("N456UP")
                    expect(page.locator("[data-quick-aircraft]")).to_contain_text("B757")
                    expect(page.locator("[data-quick-tank-code]")).to_have_count(3)
                    page.locator('[name="required_fuel"]').fill("30.0")
                    expect(page.locator('[data-quick-total="required"]')).to_have_text("30.0")
                    expect(page.locator('[data-quick-total="fuel_load"]')).to_have_text("—")
                    expect(page.locator('[data-quick-total="neo_fuel"]')).to_have_text("—")
                    page.locator('[name="apu_running"]').select_option("no")
                    for code in ("left", "ctr", "right"):
                        page.locator(f'[name="remaining_{code}"]').fill("5.0")
                    expect(page.locator('[data-quick-total="planned"]')).to_have_text("30.0")
                    expect(page.locator('[data-quick-total="actual"]')).to_have_text("—")
                    for code in ("left", "ctr", "right"):
                        page.locator(f'[name="actual_{code}"]').fill("10.0")
                    expect(page.locator('[data-quick-total="remaining"]')).to_have_text("15.0")
                    expect(page.locator('[data-quick-total="planned"]')).to_have_text("30.0")
                    expect(page.locator('[data-quick-total="neo_fuel"]')).to_have_text("30.0")
                    expect(page.locator('[data-quick-total="actual"]')).to_have_text("30.0")
                    expect(page.locator('[data-quick-total="fuel_load"]')).to_have_text("30.0")
                    expect(page.locator('[data-quick-total="estimated_gallons"]')).to_have_text("2239")
                    page.locator('[name="apu_running"]').select_option("yes")
                    expect(page.locator("[data-quick-apu-source]")).to_be_visible()
                    page.locator('[name="apu_source_tank_code"]').select_option("ctr")
                    expect(page.locator("[data-quick-issues]")).to_contain_text("departure time")
                    expect(page.locator('[data-quick-total="planned"]')).to_have_text("—")
                    page.locator('[name="apu_override_enabled"]').select_option("1")
                    page.locator('[name="apu_override_allowance"]').fill("0.5")
                    expect(page.locator('[data-quick-total="fuel_load"]')).to_have_text("30.5")
                    expect(page.locator('[data-quick-total="required"]')).to_have_text("30.0")
                    expect(page.locator('[data-quick-total="neo_fuel"]')).to_have_text("29.5")
                    page.locator('[name="tail_number"]').fill("N123UP")
                    expect(page.locator("[data-quick-aircraft]")).to_contain_text("A300")
                    expect(page.locator("[data-quick-tank-code]")).to_have_count(6)
                    expect(page.locator('[name="required_fuel"]')).to_have_value("")
                    expect(page.locator('[data-quick-total="planned"]')).to_have_text("—")
                    page.locator("[data-quick-clear]").click()
                    expect(page.locator("[data-quick-tank-code]")).to_have_count(0)
                    expect(page.locator('[name="tail_number"]')).to_have_value("")
                    self.assertFalse(page.evaluate("document.documentElement.scrollWidth > innerWidth + 1"))
                    context.close()
            with _Fixture.app.app_context():
                self.assertEqual(NeoScorpionFuelAssignment.query.count(), 0)
                self.assertEqual(NeoScorpionFuelingEvent.query.count(), 0)
                self.assertEqual(SortDateMission.query.count(), 0)
        finally:
            if browser:
                browser.close()
            _Fixture.tearDownClass()
