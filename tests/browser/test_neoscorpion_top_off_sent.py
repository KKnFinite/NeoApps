"""The truck-card sent time comes from the committed TOP OFF action."""

from datetime import date
import re
import unittest
from unittest.mock import patch

from playwright.sync_api import expect

from app.extensions import db
from app.models import (NeoScorpionFuelTruck, NeoScorpionSortAssetState,
                        NeoScorpionSortTruck, NeoScorpionTopOffEvent, SortDateOperation, User)
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.time_display import format_local_hhmm
from app.services.gateway_matrix import gateway_timezone
from tests.browser.test_mobile_drawer import MobileDrawerBrowserTest as _Fixture


class TopOffSentBrowserTest(unittest.TestCase):
    def test_top_off_and_return_settle_despite_drafts_errors_and_lost_acknowledgement(self):
        import time
        _Fixture.setUpClass()
        _Fixture.app.config['LIVE_SCREEN_REFRESH_INTERVAL_MS']=5000
        browsers=[]
        try:
            with _Fixture.app.app_context():
                gateway=ensure_default_gateway_and_nodes()
                admin=User.query.filter_by(username='drawer-admin').one()
                operation=SortDateOperation(generated_by_user_id=admin.id,gateway_id=gateway.id,
                    gateway_code=gateway.code,sort_date=date.today(),sort_name='night',window_minutes=60)
                db.session.add(operation);db.session.flush();oid=operation.id
                truck=NeoScorpionFuelTruck(gateway_id=gateway.id,truck_number='SETTLE',capacity_gallons=20000)
                db.session.add(truck);db.session.flush();tid=truck.id
                db.session.add_all([NeoScorpionSortTruck(sort_date_operation_id=oid,fuel_truck_id=tid,
                    status='available',starting_gallons=10000,current_gallons=10000,inventory_verified=True),
                    NeoScorpionSortAssetState(sort_date_operation_id=oid,revision=1)])
                db.session.commit()
            with patch('app.services.neoscorpion.current_existing_operational_sort_operations',
                    side_effect=lambda gateway,now=None:[db.session.get(SortDateOperation,oid)]):
                for engine in ('chromium','webkit'):
                    browser=getattr(_Fixture.pw,engine).launch();browsers.append(browser)
                    print('TOP OFF browser:',engine,flush=True)
                    page=browser.new_page(viewport={'width':1440,'height':900},service_workers='block');errors=[]
                    page.on('pageerror',lambda e:errors.append(str(e)))
                    _Fixture().login(page)
                    page.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1','seen')")
                    _Fixture().ready(page,'/neoscorpion/fuel-dispatch?assets=open')
                    page.evaluate("""() => {
                        window.truckFeedback=[];
                        new MutationObserver(() => document.querySelectorAll('[data-dispatch-truck-card-status]').forEach(
                            el => window.truckFeedback.push(el.textContent))).observe(
                                document.querySelector('[data-fuel-dispatch-live]'),{subtree:true,childList:true,characterData:true});
                    }""")
                    card=page.locator(f'[data-dispatch-truck-id="{tid}"]')
                    draft=page.locator(f'#nightly-truck-{tid} [name="name"]')
                    draft.fill('Keep this unsaved driver draft')
                    endpoint='**/neoscorpion/fuel-dispatch/assets'
                    # Slow successful POST; full refresh is blocked by a real dirty
                    # workspace. Both status and the next card action must update.
                    def slow(route):
                        response=route.fetch();time.sleep(.4);route.fulfill(response=response)
                    page.route(endpoint,slow,times=1)
                    card.get_by_role('button',name='TOP OFF',exact=True).click()
                    expect(card.locator('[data-dispatch-truck-card-status]')).to_contain_text('Saving')
                    expect(card.get_by_role('button',name='RETURN',exact=True)).to_be_visible(timeout=15000)
                    expect(card.locator('.neoscorpion-truck-activity')).to_contain_text('TOPPING OFF')
                    expect(draft).to_have_value('Keep this unsaved driver draft')
                    print('TOP OFF settled',engine,flush=True)
                    page.unroute(endpoint)
                    # Explicit server rejection must clear Saving and permit a
                    # corrected request without losing its gallon input.
                    card.locator('[name="current_gallons"]').fill('12000')
                    page.route(endpoint,lambda r:r.fulfill(status=400,json={'ok':False,'error':'Refresh truck state'}))
                    with page.expect_response(lambda r:r.request.method=='POST' and '/fuel-dispatch/assets' in r.url) as rejected:
                        card.get_by_role('button',name='RETURN',exact=True).click()
                    self.assertEqual(rejected.value.status,400)
                    expect(card.locator('[data-dispatch-truck-card-status]')).to_contain_text('Save Failed: Refresh truck state')
                    expect(card.get_by_role('button',name='RETURN',exact=True)).to_be_enabled()
                    expect(card.locator('[name="current_gallons"]')).to_have_value('12000')
                    page.unroute(endpoint)
                    # Board View uses the same durable actions. Commit RETURN,
                    # then lose the response: the read-only refresh recovers it.
                    page.locator('[data-operational-board-toggle]:visible').click()
                    def lost_ack(route):
                        response=route.fetch();self.assertEqual(response.status,200);route.abort('failed')
                    page.route(endpoint,lost_ack)
                    card.get_by_role('button',name='RETURN',exact=True).click()
                    page.wait_for_function("window.truckFeedback.some(text=>text.includes('RESULT UNKNOWN'))")
                    expect(card.get_by_role('button',name='TOP OFF',exact=True)).to_be_visible(timeout=15000)
                    expect(card).to_have_attribute('data-truck-operational-status','available')
                    expect(card.locator('.neoscorpion-truck-activity')).not_to_contain_text('TOPPING OFF')
                    expect(card.locator('.neoscorpion-truck-visual-values')).to_contain_text('12,000')
                    expect(draft).to_have_value('Keep this unsaved driver draft')
                    print('RETURN settled',engine,flush=True)
                    page.unroute(endpoint)
                    page.screenshot(path=str(_Fixture.evidence/f'top-off-settled-{engine}.png'),full_page=True)
                    page.set_viewport_size({'width':375,'height':812})
                    self.assertFalse(page.evaluate('document.documentElement.scrollWidth>innerWidth+1'))
                    # Mobile Dispatch remains read-only.
                    expect(card.get_by_role('button',name='TOP OFF',exact=True)).not_to_be_visible()
                    self.assertEqual(errors,[])
                    page.close();browser.close();browsers.remove(browser)
            with _Fixture.app.app_context():
                db.session.expire_all()
                self.assertEqual(NeoScorpionTopOffEvent.query.filter_by(sort_date_operation_id=oid).count(),4)
                selection=NeoScorpionSortTruck.query.filter_by(sort_date_operation_id=oid,fuel_truck_id=tid).one()
                self.assertEqual(selection.current_gallons,12000)
                self.assertEqual(selection.inventory_epoch,2)
        finally:
            for browser in browsers:browser.close()
            _Fixture.tearDownClass()

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
                expect(card.locator(".neoscorpion-truck-top-off-sent").filter(has_text=re.compile(r'^TOP OFF SENT'))).to_have_text(
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
                expect(card.locator(".neoscorpion-truck-top-off-sent").filter(has_text=re.compile(r'^TOP OFF SENT'))).to_have_text(expected)
                page.reload(wait_until="domcontentloaded")
                expect(card.locator(".neoscorpion-truck-top-off-sent").filter(has_text=re.compile(r'^TOP OFF SENT'))).to_have_text(expected)
                page.set_viewport_size({"width": 390, "height": 844})
                expect(card.locator(".neoscorpion-truck-top-off-sent").filter(has_text=re.compile(r'^TOP OFF SENT'))).to_have_text(expected)
                self.assertEqual(card.locator(".neoscorpion-truck-top-off-sent").filter(has_text=re.compile(r'^TOP OFF SENT')).evaluate(
                    "el => getComputedStyle(el).gridColumnEnd"), "-1")
                self.assertFalse(page.evaluate("document.documentElement.scrollWidth > innerWidth + 1"))
        finally:
            if browser:
                browser.close()
            _Fixture.tearDownClass()
