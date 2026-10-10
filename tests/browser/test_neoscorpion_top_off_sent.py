"""The truck-card sent time comes from the committed TOP OFF action."""

from datetime import date
import re
import unittest
from unittest.mock import patch

from playwright.sync_api import expect

from app.extensions import db
from app.models import (NeoScorpionFuelTruck, NeoScorpionSortAssetState,
                        NeoScorpionSortTruck, SortDateOperation, User)
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.time_display import format_local_hhmm
from app.services.gateway_matrix import gateway_timezone
from tests.browser.test_mobile_drawer import MobileDrawerBrowserTest as _Fixture


class TopOffSentBrowserTest(unittest.TestCase):
    def test_truck_card_shows_persisted_gateway_local_military_time_after_refresh(self):
        _Fixture.setUpClass()
        _Fixture.app.config["LIVE_SCREEN_REFRESH_INTERVAL_MS"] = 5000
        browser = None
        try:
            with _Fixture.app.app_context():
                gateway = ensure_default_gateway_and_nodes()
                admin = User.query.filter_by(username="drawer-admin").one()
                operation = SortDateOperation(
                    generated_by_user_id=admin.id, gateway_id=gateway.id,
                    gateway_code=gateway.code, sort_date=date.today(),
                    sort_name="night", window_minutes=60,
                )
                truck = NeoScorpionFuelTruck(
                    gateway_id=gateway.id, truck_number="20", capacity_gallons=20_000,
                )
                db.session.add_all([operation, truck])
                db.session.flush()
                db.session.add_all([
                    NeoScorpionSortTruck(
                        sort_date_operation_id=operation.id, fuel_truck_id=truck.id,
                        status="available", starting_gallons=10_000, current_gallons=10_000,
                    ),
                    NeoScorpionSortAssetState(sort_date_operation_id=operation.id, revision=1),
                ])
                db.session.commit()
                operation_id, truck_id = operation.id, truck.id

            with patch("app.services.neoscorpion.current_existing_operational_sort_operations",
                       side_effect=lambda gateway, now=None: [db.session.get(SortDateOperation, operation_id)]):
                browser = _Fixture.pw.chromium.launch()
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                _Fixture().login(page)
                page.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1', 'seen')")
                _Fixture().ready(page, "/neoscorpion/fuel-dispatch")
                card = page.locator(".neoscorpion-truck-visual-card").first
                expect(card.get_by_role("button", name="TOP OFF", exact=True)).to_be_visible()
                card.get_by_role("button", name="TOP OFF", exact=True).click()
                expect(card.locator(".neoscorpion-truck-top-off-sent")).to_have_text(
                    re.compile(r"^TOP OFF SENT \d{2}:\d{2}$"), timeout=20000,
                )
                with _Fixture.app.app_context():
                    db.session.expire_all()
                    selection = NeoScorpionSortTruck.query.filter_by(
                        sort_date_operation_id=operation_id, fuel_truck_id=truck_id,
                    ).one()
                    self.assertIsNotNone(selection.top_off_sent_at_utc)
                    expected = "TOP OFF SENT " + format_local_hhmm(
                        selection.top_off_sent_at_utc, gateway_timezone(selection.sort_date_operation.gateway),
                    )
                expect(card.locator(".neoscorpion-truck-top-off-sent")).to_have_text(expected)
                page.reload(wait_until="domcontentloaded")
                expect(card.locator(".neoscorpion-truck-top-off-sent")).to_have_text(expected)
                page.set_viewport_size({"width": 390, "height": 844})
                expect(card.locator(".neoscorpion-truck-top-off-sent")).to_have_text(expected)
                self.assertEqual(card.locator(".neoscorpion-truck-top-off-sent").evaluate(
                    "el => getComputedStyle(el).gridColumnEnd"), "-1")
                self.assertFalse(page.evaluate("document.documentElement.scrollWidth > innerWidth + 1"))
        finally:
            if browser:
                browser.close()
            _Fixture.tearDownClass()
