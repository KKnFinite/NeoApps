"""Actual desktop/mobile warning, exact revision ACK and live refresh."""
from datetime import datetime, timedelta
import unittest
from playwright.sync_api import expect
from app.extensions import db
from app.models import SortDateMission, NeoScorpionFuelAssignment, NeoScorpionFuelWorkState, NeoScorpionFuelingEvent, NeoScorpionFuelTruck, Gateway
from app.services.neorain_fuel_authority import set_rain_fuel_data_source
from tests.browser import test_neorain_compact_overrides as fixtures


class FuelReviewBrowserTest(unittest.TestCase):
    def test_initial_unchanged_correction_ack_stale_ack_and_reopening_desktop_mobile(self):
        R=fixtures.CompactRainBrowserTest; R.setUpClass(); F=R.fixture
        try:
            with F.app.app_context():
                gateway=Gateway.query.filter_by(code='RFD').one()
                set_rain_fuel_data_source(gateway,'night','neo')
                mission=db.session.get(SortDateMission,R.ids['departure']);mission.wave='1'
                truck=NeoScorpionFuelTruck(gateway_id=gateway.id,truck_number='92',capacity_gallons=20000)
                a=NeoScorpionFuelAssignment(sort_date_operation_id=R.operation_id,sort_date_mission_id=mission.id,
                    completed_at_utc=datetime(2026,10,16,6))
                db.session.add_all([truck,a]);db.session.flush()
                work=NeoScorpionFuelWorkState(fuel_assignment_id=a.id,tail_number='N123UP')
                db.session.add(work);db.session.flush()
                event=NeoScorpionFuelingEvent(sort_date_operation_id=R.operation_id,fuel_assignment_id=a.id,
                    fuel_work_state_id=work.id,tail_number='N123UP',fuel_truck_id=truck.id,sequence_number=1,
                    neo_fuel_lbs=45000,center_fuel_lbs=1000,created_at=datetime(2026,10,16,6,0,1),updated_at=datetime(2026,10,16,9))
                db.session.add(event);db.session.commit();eid=event.id
            for engine,width in (('chromium',1600),('webkit',390)):
                browser=getattr(F.pw,engine).launch()
                try:
                    context=browser.new_context(viewport={'width':width,'height':900},has_touch=width<900,is_mobile=width<900)
                    page=context.new_page();F().login(page);F().ready(page,'/neorain/outbound')
                    def reveal():
                        if width<900:
                            button=page.locator(f'#mobile-flight-{R.ids["departure"]} [data-neorain-mobile-row-open]').first
                            if button.get_attribute('aria-expanded')!='true':button.click()
                    reveal()
                    warning=page.locator('.neorain-fuel-review-warning:visible')
                    expect(warning).to_have_count(0)
                    with F.app.app_context():
                        event=db.session.get(NeoScorpionFuelingEvent,eid);event.updated_at+=timedelta(hours=1)
                        event.required_fuel_lbs=55000;db.session.commit()
                    page.reload();page.wait_for_load_state('load');reveal();expect(warning).to_have_count(0)
                    with F.app.app_context():
                        event=db.session.get(NeoScorpionFuelingEvent,eid);event.neo_fuel_lbs+=100;db.session.commit()
                    # Existing Rain live polling refreshes the visible row/details.
                    expect(warning).to_have_count(1,timeout=15000)
                    review=page.locator('form:visible').filter(has=page.locator('input[name="fuel_revision"]'))
                    revision=review.locator('[name="fuel_revision"]').input_value()
                    with page.expect_response(lambda r:'/outbound/fuel-reviewed' in r.url and r.request.method=='POST') as ack:
                        review.locator('button').click()
                    self.assertEqual(ack.value.status,302);page.wait_for_load_state('load');reveal();expect(warning).to_have_count(0)
                    with F.app.app_context():
                        event=db.session.get(NeoScorpionFuelingEvent,eid);event.updated_at+=timedelta(minutes=10);db.session.commit()
                    page.reload();page.wait_for_load_state('load');reveal();expect(warning).to_have_count(0)
                    with F.app.app_context():
                        event=db.session.get(NeoScorpionFuelingEvent,eid);event.center_fuel_lbs+=100;db.session.commit()
                    expect(warning).to_have_count(1,timeout=15000)
                    token=page.locator('meta[name="csrf-token"]').get_attribute('content')
                    response=page.request.post(F.origin+'/neorain/outbound/fuel-reviewed',form=dict(
                        mission_id=R.ids['departure'],fuel_revision=revision,csrf_token=token))
                    self.assertEqual(response.status,200)
                    self.assertIn('Fuel data changed before it could be reviewed',response.text())
                    page.reload();page.wait_for_load_state('load');reveal();expect(warning).to_have_count(1)
                    self.assertFalse(page.evaluate('document.documentElement.scrollWidth>innerWidth+1'))
                    page.screenshot(path=str(F.evidence/f'rain-fuel-review-{engine}.png'),full_page=True)
                    # Clear only through the current revision, then verify a fresh page has no warning.
                    page.locator('form:visible').filter(has=page.locator('input[name="fuel_revision"]')).locator('button').click()
                    page.wait_for_load_state('load');reveal();expect(warning).to_have_count(0)
                    context.close()
                finally:browser.close()
        finally:R.tearDownClass()
