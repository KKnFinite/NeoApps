"""Authenticated Chromium/WebKit scorecard, real filters and compact disclosures."""
from datetime import date, datetime, timedelta
from unittest import TestCase
from playwright.sync_api import expect
from app.extensions import db
from app.models import (User, SortDateOperation, SortDateMission, NeoScorpionFuelTruck,
    NeoScorpionFuelAssignment, NeoScorpionFuelWorkState, NeoScorpionFuelingEvent, NeoScorpionTopOffEvent)
from app.models.neoscorpion_spear_observation import NeoScorpionSpearObservation as Observation
from app.services.access_control import ensure_default_gateway_and_nodes
from tests.browser import test_mobile_drawer as fixture
from tests.test_neoscorpion_spear_scorecard import evidence_event, decision


class ScorecardBrowserTest(TestCase):
    def exercise(self, engine):
        F=fixture.MobileDrawerBrowserTest;F.setUpClass();browser=None
        try:
            with F.app.app_context():
                F.app.config['SECURITY_HEADERS_CSP_ALLOW_NON_PRODUCTION']=True
                gateway=ensure_default_gateway_and_nodes();actor=User.query.filter_by(username='drawer-admin').one()
                operation=SortDateOperation(gateway_id=gateway.id,gateway_code=gateway.code,sort_date=date(2026,10,17),
                    sort_name='night',window_minutes=60,generated_by_user_id=actor.id)
                truck=NeoScorpionFuelTruck(gateway_id=gateway.id,truck_number='SCORE-42',capacity_gallons=10000)
                db.session.add_all([operation,truck]);db.session.flush()
                mission=SortDateMission(sort_date_operation_id=operation.id,sort_date=operation.sort_date,
                    gateway_code=gateway.code,sort_name='night',mission_type='departure',mission_source='manual',
                    flight_number='UPS0556',origin=gateway.code,destination='MSP',assigned_tail_number='N168UP',
                    timezone='America/Chicago')
                db.session.add(mission);db.session.flush()
                assignment=NeoScorpionFuelAssignment(sort_date_operation_id=operation.id,sort_date_mission_id=mission.id)
                db.session.add(assignment);db.session.flush()
                at=datetime(2026,10,18,4,55)
                for index,tail in enumerate(('N168UP','N438UP','N168UP')):
                    start=at+timedelta(minutes=index*30)
                    work=NeoScorpionFuelWorkState(fuel_assignment_id=assignment.id,tail_number=tail,visit_number=index+1,
                        physical_sequence_number=index+1,physical_cycle_number=1,physical_truck_id=truck.id,
                        physical_fueler_user_id=actor.id,manual_on_at_utc=start,off_at_utc=start+timedelta(minutes=20),
                        physical_finalized_at_utc=start+timedelta(minutes=25))
                    db.session.add(work);db.session.flush()
                    facts=dict(evidence_event().performance_snapshot,fueler_id=actor.id,truck_id=truck.id)
                    error=(5,-3,0)[index]
                    facts['forecasts']=[dict(observed_at=start.isoformat(),likely=(work.off_at_utc-timedelta(minutes=error)).isoformat(),source='BASELINE')]
                    if index==2:facts['forecasts']=[]
                    event=NeoScorpionFuelingEvent(sort_date_operation_id=operation.id,fuel_assignment_id=assignment.id,
                        fuel_work_state_id=work.id,tail_number=tail,fuel_truck_id=truck.id,fueler_user_id=actor.id,
                        sequence_number=index+1,cycle_number=1,started_at_utc=start,ended_at_utc=work.off_at_utc,
                        transfer_fuel_gallons=1000,required_fuel_lbs=30000,performance_snapshot=facts)
                    db.session.add(event);db.session.flush()
                    if index==0:
                        rec=decision(event);rec.update(gateway_id=gateway.id,operation_id=operation.id,mission_id=mission.id)
                        rec['payload']['original_spear']['gateway_id']=gateway.id
                        rec['payload']['original_spear']['scope']['mission_id']=mission.id
                        out=dict(rec,id='b'*32,record_type='outcome',payload=dict(decision_id=rec['id'],
                            event_ids=[event.id],qualified=True,exclusions=[],comparison_conclusion='observed_only'))
                        for record in (rec,out):
                            db.session.add(Observation(id=record['id'],kind=record['record_type'],event_key=record['id']*2,
                                gateway_id=gateway.id,operation_id=operation.id,mission_id=mission.id,
                                assignment_id=assignment.id,cycle_number=1,payload=record))
                sent=NeoScorpionTopOffEvent(sort_date_operation_id=operation.id,fuel_truck_id=truck.id,kind='sent',
                    occurred_at_utc=at+timedelta(hours=3),gallons=4000,inventory_verified=True,inventory_epoch=1,
                    spear_predicted_return_at_utc=at+timedelta(hours=3,minutes=30),spear_roundtrip_policy={'minutes':30})
                db.session.add(sent);db.session.flush()
                db.session.add(NeoScorpionTopOffEvent(sort_date_operation_id=operation.id,fuel_truck_id=truck.id,
                    kind='returned',sent_event_id=sent.id,occurred_at_utc=at+timedelta(hours=3,minutes=40),
                    gallons=9000,inventory_verified=True,inventory_epoch=2))
                db.session.commit();op_id,truck_id,fueler_id=operation.id,truck.id,actor.id
            browser=getattr(F.pw,engine).launch()
            page=browser.new_page(viewport={'width':1440,'height':900});errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            F().login(page);F().ready(page,'/neoscorpion/settings/spear')
            page.get_by_role('link',name='INTELLIGENCE SCORECARD',exact=True).click()
            page.wait_for_url('**/spear/scorecard')
            page.locator('[name=operation_id]').select_option(str(op_id))
            with page.expect_navigation():page.get_by_role('button',name='View',exact=True).click()
            summary=page.locator('.spear-score-summary').first
            expect(summary).to_contain_text('2 / 3');expect(summary).to_contain_text('4.0 min')
            expect(page.locator('.spear-score-summary').nth(1)).to_contain_text('10.0 min')
            for label in ('Fueling evidence','Dispatcher decisions vs SPEAR'):
                page.locator('summary').filter(has_text=label).click()
            expect(page.locator('[data-score-off]')).to_have_count(3)
            expect(page.locator('body')).to_contain_text('observed LATE')
            self.assertEqual(page.locator('.spear-scorecard form[method=post]').count(),0)
            for width,height in ((1440,900),(1920,1080),(390,844),(375,568)):
                page.set_viewport_size({'width':width,'height':height})
                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'),width+1,
                    page.evaluate("[...document.querySelectorAll('.spear-scorecard *')].filter(e=>e.getBoundingClientRect().right>innerWidth).map(e=>[e.tagName,e.className,e.textContent.slice(0,70)]).slice(0,12)"))
                for box in page.locator('.spear-scorecard select').all():
                    self.assertLessEqual(box.bounding_box()['x']+box.bounding_box()['width'],width)
                page.evaluate('window.scrollTo(0,0)')
                page.screenshot(path=str(F.evidence/f'scorecard-{engine}-{width}.png'),full_page=True)
            page.locator('[name=truck_id]').select_option(str(truck_id))
            page.locator('[name=fueler_id]').select_option(str(fueler_id))
            with page.expect_navigation():page.get_by_role('button',name='View',exact=True).click()
            expect(page.locator('body')).to_contain_text('TOP OFF has no reliable Fueler attribution')
            expect(page.locator('.spear-score-summary').first).to_contain_text('2 / 3')
            page.reload();expect(page.locator('[name=truck_id]')).to_have_value(str(truck_id))
            self.assertEqual(errors,[])
            with F.app.app_context():
                self.assertEqual(NeoScorpionFuelingEvent.query.count(),3)
                self.assertEqual(NeoScorpionTopOffEvent.query.count(),2)
                self.assertEqual(Observation.query.count(),2)
        finally:
            if browser:browser.close()
            F.tearDownClass()

    def test_chromium(self):self.exercise('chromium')
    def test_webkit(self):self.exercise('webkit')
