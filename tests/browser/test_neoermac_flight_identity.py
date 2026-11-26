"""Native lineup choices and real pull saves across desktop/mobile live refresh."""
from contextlib import ExitStack
from datetime import datetime, time
from zoneinfo import ZoneInfo
import unittest
from unittest.mock import patch
from playwright.sync_api import expect
from app.extensions import db
from app.models import User, NeoErmacDoorPull, SortDateMission
from app.services.access_control import ensure_default_gateway_and_nodes
from tests.browser import test_mobile_drawer as browser_fixture
from tests import test_neoermac_routes as route_fixture
from tests import test_neoermac_flight_identity as cases


class FlightIdentityBrowserTest(unittest.TestCase):
    def test_native_editor_door_pulls_and_live_refresh_desktop_mobile(self):
        Fixture=browser_fixture.MobileDrawerBrowserTest
        for engine,width in (('chromium',1440),('webkit',390)):
            with self.subTest(engine=engine):
                Fixture.setUpClass(); browser=None
                try:
                    with Fixture.app.app_context():
                        c=cases.FlightIdentityTest(); c.f=route_fixture.NeoErmacRoutesTest()
                        c.gateway=c.f.gateway=ensure_default_gateway_and_nodes()
                        c.f.operation_creator=User.query.filter_by(username='drawer-admin').one()
                        a=c.add_flight('UPS101','N101UP','A01',time(23,40),time(0,10))
                        b=c.add_flight('UPS202','N202UP','B06',time(0,20),time(0,40))
                        from tests.test_neorain_sort_refresh import configure_sort_window
                        _, opening = configure_sort_window(c.gateway, a.sort_date)
                        physical_now = opening.replace(tzinfo=ZoneInfo('America/Chicago'))
                        Fixture.app.config['CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE'] = physical_now
                        from app.services.neoermac_building_lineup import get_building_lineup_rows
                        get_building_lineup_rows(c.gateway)
                        db.session.commit(); aid,bid,opid,mastera,masterb=a.id,b.id,a.sort_date_operation_id,a.master_flight_schedule_id,b.master_flight_schedule_id
                    with ExitStack() as stack:
                        from app.models import SortDateOperation
                        for target in ('app.neonodes.neoermac.routes.current_door_view_operation',
                                       'app.services.neoermac_door_view.current_door_view_operation',
                                       'app.services.neoermac_building_lineup.current_operational_sort_operation',
                                       'app.services.neoermac_dashboard.current_operational_sort_operation'):
                            stack.enter_context(patch(target,side_effect=lambda gateway:db.session.get(SortDateOperation,opid)))
                        from app.services.live_screen_refresh import live_screen_refresh_value
                        from dataclasses import replace
                        stack.enter_context(patch('app.services.neoermac_live_refresh.live_screen_refresh_value',
                            side_effect=lambda gateway,key:replace(live_screen_refresh_value(gateway,key),effective_interval_ms=1500)))
                        browser=getattr(Fixture.pw,engine).launch()
                        context=browser.new_context(viewport={'width':width,'height':900},has_touch=width<900,is_mobile=width<900)
                        page=context.new_page(); errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                        page.clock.install(time=physical_now)
                        Fixture().login(page);Fixture().ready(page,'/neoermac/building-lineup')
                        select=page.locator('[name="lineup_runout_9_west_destination_1"]')
                        self.assertIn('UPS101',select.inner_text());self.assertIn('UPS202',select.inner_text())
                        with page.expect_response(lambda r:'/building-lineup/destination' in r.url and r.request.method=='POST') as saved:
                            select.select_option(str(masterb))
                        self.assertEqual(saved.value.status,200)
                        expect(select).to_have_value(str(masterb))
                        slot=select.locator('..');expect(slot.locator('[data-pull-time-key="pure"]')).to_have_text('00:20')
                        # Another live editor's canonical flight selection must propagate.
                        with Fixture.app.app_context():
                            from app.services.neoermac_building_lineup import save_building_lineup_destination,lineup_original
                            save_building_lineup_destination(ensure_default_gateway_and_nodes(),select.get_attribute('name'),str(mastera),
                                expected_original=lineup_original(ensure_default_gateway_and_nodes(),select.get_attribute('name'),str(masterb)))
                            db.session.commit()
                        select.blur()
                        expect(select).to_have_value(str(mastera),timeout=12000)
                        with page.expect_response(lambda r:'/building-lineup/destination' in r.url and r.request.method=='POST'):
                            select.select_option(str(masterb))
                        Fixture().ready(page,'/neoermac/door-view?door=D32')
                        card=page.locator('[data-door-destination-card]')
                        expect(card).to_have_count(1);expect(card.locator('[data-pull-mission]')).to_have_value(str(bid))
                        expect(card.locator('.neoermac-door-destination-title')).to_have_text('2nd LAX')
                        expect(card.locator('[data-door-tail]')).to_have_text('N202UP')
                        expect(card.locator('[data-door-parking]')).to_have_text('B06')
                        expect(card.locator('[data-door-planned="pure"]')).to_have_text('00:20')
                        page.evaluate('window.ermacContinuity="kept"')
                        with Fixture.app.app_context():
                            early=db.session.get(SortDateMission,aid);early.departure_status='departed';db.session.commit()
                        page.wait_for_timeout(2200)
                        expect(card.locator('[data-pull-mission]')).to_have_value(str(bid))
                        actual=card.locator('[data-hhmm-input][data-pull-key="pure"]')
                        with page.expect_response(lambda r:'/pull-autosave' in r.url and r.request.method=='POST') as saved:
                            actual.fill('0025');actual.blur()
                        self.assertEqual(saved.value.status,200)
                        with Fixture.app.app_context():
                            self.assertEqual(NeoErmacDoorPull.query.one().sort_date_mission_id,bid)
                            self.assertIsNone(db.session.get(SortDateMission,aid).actual_pure_pull_time_local)
                        self.assertEqual(page.evaluate('window.ermacContinuity'),'kept')
                        self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'))
                        page.screenshot(path=str(Fixture.evidence/f'ermac-flight-identity-{engine}.png'),full_page=True)
                        self.assertEqual(errors,[])
                        context.close()
                finally:
                    if browser:browser.close()
                    Fixture.tearDownClass()

    def test_outbound_actuals_update_in_place_only_during_physical_sort(self):
        from dataclasses import replace
        from datetime import timedelta
        from itertools import product
        from app.services.live_screen_refresh import live_screen_refresh_value
        from app.services.neoermac_building_lineup import get_building_lineup_rows
        from tests.test_neorain_sort_refresh import configure_sort_window

        fixture = browser_fixture.MobileDrawerBrowserTest
        fixture.setUpClass()
        self.addCleanup(fixture.tearDownClass)
        with fixture.app.app_context():
            c = cases.FlightIdentityTest()
            c.f = route_fixture.NeoErmacRoutesTest()
            c.gateway = c.f.gateway = ensure_default_gateway_and_nodes()
            c.f.operation_creator = User.query.filter_by(username="drawer-admin").one()
            a = c.add_flight("UPS101", "N101UP", "A01", time(23, 40), time(0, 10))
            b = c.add_flight("UPS202", "N202UP", "B06", time(0, 20), time(0, 40))
            _, opening = configure_sort_window(c.gateway, a.sort_date)
            physical_now = opening.replace(tzinfo=ZoneInfo("America/Chicago")) + timedelta(hours=4)
            fixture.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = physical_now
            c.row = next(row for row in get_building_lineup_rows(c.gateway) if row.runout_key == "runout_9")
            c.assign(a, c.row, "west_destination_2")
            c.assign(b, c.row, "west_destination_1")
            c.assign(b, c.row, "east_destination_1")
            db.session.add_all([
                NeoErmacDoorPull(
                    gateway_id=c.gateway.id, sort_date_operation_id=b.sort_date_operation_id,
                    sort_date_mission_id=b.id, destination="LAX", door="D32",
                    actual_pure_pull_time_local=time(23, 59), no_mix_pull=True,
                ),
                NeoErmacDoorPull(
                    gateway_id=c.gateway.id, sort_date_operation_id=b.sort_date_operation_id,
                    sort_date_mission_id=b.id, destination="LAX", door="D29",
                ),
            ])
            b.actual_pure_pull_time_local = time(23, 59)
            db.session.commit()
            aid, bid = a.id, b.id

        def correct_east(actual, no_mix_pull):
            with fixture.app.app_context():
                east = NeoErmacDoorPull.query.filter_by(sort_date_mission_id=bid, door="D29").one()
                east.actual_pure_pull_time_local = actual
                east.no_mix_pull = no_mix_pull
                db.session.commit()

        with patch(
            "app.services.neoermac_live_refresh.live_screen_refresh_value",
            side_effect=lambda gateway, key: replace(live_screen_refresh_value(gateway, key), effective_interval_ms=1000),
        ):
            for engine in ("chromium", "webkit"):
                with getattr(fixture.pw, engine).launch() as browser:
                    for width, scheme in product((1440, 390), ("light", "dark")):
                        with self.subTest(engine=engine, width=width, color_scheme=scheme):
                            correct_east(None, False)
                            context = browser.new_context(
                                viewport={"width": width, "height": 900},
                                has_touch=width < 900, is_mobile=width < 900, color_scheme=scheme,
                            )
                            try:
                                page = context.new_page()
                                errors = []
                                requests = []
                                page.on("pageerror", lambda error: errors.append(str(error)))
                                page.on("request", lambda req: requests.append(req.url)
                                        if "/neoermac/view-outbound/state" in req.url else None)
                                page.clock.install(time=physical_now)
                                fixture().login(page)
                                fixture().ready(page, "/neoermac/view-outbound")
                                layout = "data-neoermac-outbound-row" if width > 900 else "data-neoermac-outbound-mobile-row"
                                row = page.locator(f'[{layout}][data-outbound-key="m{bid}"]')
                                unaffected = page.locator(f'[{layout}][data-outbound-key="m{aid}"]')
                                actuals = row.locator(".neoermac-outbound-actual")
                                expect(row).to_be_visible()
                                expect(actuals).to_have_text(["", ""])
                                unaffected.evaluate('(node) => node.dataset.testUpdated = "retained"')
                                path = fixture.evidence / f"ermac-outbound-{engine}-{width}-{scheme}"
                                page.screenshot(path=str(path) + "-pending.png")
                                correct_east(time(0, 4), False)
                                expect(actuals).to_have_text(["00:04", ""], timeout=10000)
                                correct_east(time(0, 4), True)
                                expect(actuals).to_have_text(["00:04", "NO MIX PULL"], timeout=10000)
                                expect(unaffected).to_have_attribute("data-test-updated", "retained")
                                page.screenshot(path=str(path) + "-complete.png")
                                correct_east(None, True)
                                expect(actuals).to_have_text(["", "NO MIX PULL"], timeout=10000)
                                self.assertTrue(page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"))
                                self.assertTrue(requests)
                                outside = physical_now + timedelta(hours=5)
                                page.clock.set_system_time(outside)
                                page.clock.fast_forward(3000)
                                before = len(requests)
                                page.clock.fast_forward(30000)
                                self.assertEqual(len(requests), before)
                                self.assertEqual(errors, [])
                            finally:
                                context.close()
