"""Native lineup choices and real pull saves across desktop/mobile live refresh."""
from contextlib import ExitStack
from datetime import datetime, time
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
