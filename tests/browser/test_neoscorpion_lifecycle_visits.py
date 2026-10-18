"""Real mobile visit controls across canonical MotherBrain A→B→A tail swaps."""
from datetime import date,datetime,timedelta
import unittest
from unittest.mock import patch
from playwright.sync_api import expect
from app.extensions import db
from app.models import (User,SortDateOperation,SortDateMission,NeoScorpionFuelAssignment,NeoScorpionFuelTruck,
    NeoScorpionSortTruck,NeoScorpionSortFueler,NeoScorpionFuelWorkState,NeoScorpionFuelingEvent)
from app.services.access_control import ensure_default_gateway_and_nodes
from app.neomotherbrain.routes import entity_version
from tests.browser import test_mobile_drawer as fixtures


class VisitLifecycleBrowserTest(unittest.TestCase):
    def test_chromium_webkit_actual_visit_controls_and_original_verification(self):
        F=fixtures.MobileDrawerBrowserTest;F.setUpClass();F.app.config['LIVE_SCREEN_REFRESH_INTERVAL_MS']=5000
        try:
            with F.app.app_context():
                g=ensure_default_gateway_and_nodes();u=User.query.filter_by(username='drawer-admin').one()
                op=SortDateOperation(generated_by_user_id=u.id,gateway_id=g.id,gateway_code=g.code,sort_date=date.today(),sort_name='night',window_minutes=60)
                db.session.add(op);db.session.flush();oid=op.id
                db.session.add(NeoScorpionSortFueler(sort_date_operation_id=oid,user_id=u.id));ids=[]
                for index in range(2):
                    t=NeoScorpionFuelTruck(gateway_id=g.id,truck_number=str(950+index),capacity_gallons=20000)
                    m=SortDateMission(sort_date_operation_id=oid,sort_date=op.sort_date,gateway_code=g.code,sort_name='night',mission_type='departure',mission_source='manual',flight_number=f'UPS{990+index}',origin=g.code,destination='MSP',assigned_tail_number=f'N49{1+index*2}UP',planned_datetime_utc=datetime.utcnow()+timedelta(hours=2),timezone='America/Chicago',planned_fuel_load=25000)
                    db.session.add_all([t,m]);db.session.flush()
                    db.session.add(NeoScorpionSortTruck(sort_date_operation_id=oid,fuel_truck_id=t.id,status='available',starting_gallons=10000,current_gallons=10000))
                    a=NeoScorpionFuelAssignment(sort_date_operation_id=oid,sort_date_mission_id=m.id,assigned_fueler_user_id=u.id,assigned_truck_id=t.id,confirmed_tail_number=m.assigned_tail_number)
                    db.session.add(a);db.session.flush();ids.append((a.id,m.id,t.id))
                db.session.commit()
            with patch('app.services.neoscorpion.current_existing_operational_sort_operations',side_effect=lambda g,now=None:[db.session.get(SortDateOperation,oid)]):
                for index,(engine,(aid,mid,tid)) in enumerate(zip((F.pw.chromium,F.pw.webkit),ids)):
                    tail_a,tail_b=f'N49{1+index*2}UP',f'N49{2+index*2}UP'
                    browser=engine.launch();errors=[]
                    try:
                        context=browser.new_context(service_workers='block');p=context.new_page();F().login(p)
                        p.set_viewport_size({'width':390,'height':844})
                        d=context.new_page();d.set_viewport_size({'width':1440,'height':900})
                        d.on('pageerror',lambda e:errors.append(str(e)));p.on('pageerror',lambda e:errors.append(str(e)))
                        F().ready(p,'/neoscorpion/fueler');F().ready(d,'/neoscorpion/fuel-dispatch')
                        d.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1','seen');document.querySelector('[data-spear-splash]')?.setAttribute('hidden','')")
                        row=d.locator(f'[data-dispatch-mission-id="{mid}"]')
                        def active_card():
                            with F.app.app_context():wid=db.session.get(NeoScorpionFuelAssignment,aid).active_work_state_id
                            return p.locator(f'[data-fuel-data-card][data-physical-work-id="{wid}"]') if wid else p.locator(f'[data-fuel-data-card][data-fuel-assignment-id="{aid}"]').first
                        def save(card,remaining,actual,transfer):
                            card.locator('[name="apu_running"]').select_option('no')
                            for code in ('left','ctr','right'):
                                if remaining is not None:card.locator(f'[name="remaining_{code}"]').fill('0' if code=='ctr' else str(remaining))
                                if actual is not None:card.locator(f'[name="actual_{code}"]').fill('0' if code=='ctr' else str(actual))
                            card.locator('[name="transfer_fuel_gallons"]').fill(str(transfer))
                            card.locator('[data-fuel-planning-form] button[type="submit"]').click()
                            expect(card.locator('[data-fuel-data-status]')).to_contain_text('Saved')
                        def verify_current():
                            d.bring_to_front();button=row.locator('button[formaction$="/fuel-dispatch/complete"]')
                            expect(button).to_have_count(1,timeout=10000);button.click()
                            expect(row).to_have_class(__import__('re').compile('is-complete'),timeout=10000)
                        def swap(tail):
                            with F.app.app_context():version=entity_version(db.session.get(SortDateMission,mid))
                            token=d.locator('meta[name="csrf-token"]').get_attribute('content')
                            response=d.request.post(F.origin+f'/motherbrain/operations/{oid}/missions/{mid}/tail-swap',
                                form=dict(replacement_tail=tail,expected_version=version,csrf_token=token,confirm_tail_swap='1'),
                                headers={'Accept':'application/json','X-Requested-With':'XMLHttpRequest'})
                            self.assertEqual(response.status,200,response.text());self.assertTrue(response.json()['ok'])
                            F().ready(d,'/neoscorpion/fuel-dispatch')
                            toggle=row.locator('[data-neoscorpion-dispatch-details]')
                            if toggle.get_attribute('aria-expanded')!='true':toggle.click()
                            form=d.locator(f'#neoscorpion-dispatch-detail-{mid}-1 form[action$="/confirm-tail"]')
                            if not form.locator('..').evaluate('el=>el.open'):form.locator('..').locator(':scope > summary').click()
                            with d.expect_response(lambda response:response.request.method=='POST' and response.url.endswith('/confirm-tail')) as acknowledgment:
                                form.locator('button[type="submit"]').click()
                            self.assertEqual(acknowledgment.value.status,302)  # existing native action commits then redirects
                            p.bring_to_front()
                            with F.app.app_context():
                                assigned=db.session.get(NeoScorpionFuelAssignment,aid)
                                self.assertEqual(assigned.confirmed_tail_number,tail)
                                wid=assigned.active_work_state_id
                            expect(p.locator(f'[data-physical-work-id="{wid}"]')).to_have_count(1,timeout=10000)
                        card=active_card();card.locator('[data-truck-on-form] button').click()
                        expect(card.locator('.neoscorpion-truck-on-row')).to_contain_text('CONFIRMED')
                        card=active_card();first_id=int(card.get_attribute('data-physical-work-id'))
                        save(card,6,15,3000);card.locator('.neoscorpion-fueler-off-form button').click()
                        card.locator('[data-fueler-submit-form] button').click();expect(card.locator('[data-fueler-submission-status]')).to_contain_text('SUBMITTED')
                        verify_current();swap(tail_b)
                        card=active_card()
                        self.assertEqual(card.locator('[data-truck-on-form] button').count(),1,card.inner_text())
                        card.locator('[data-truck-on-form] button').click();expect(card.locator('.neoscorpion-truck-on-row')).to_contain_text('CONFIRMED')
                        card=active_card();b_id=int(card.get_attribute('data-physical-work-id'));save(card,7,None,1000)
                        swap(tail_a);a=active_card();last_id=int(a.get_attribute('data-physical-work-id'))
                        self.assertEqual(len({first_id,b_id,last_id}),3)
                        a.locator('[data-truck-on-form] button').click();expect(a.locator('[data-fuel-data-status]')).to_contain_text('TRUCK STILL FUELING')
                        b=p.locator(f'[data-physical-work-id="{b_id}"]');b.locator('.neoscorpion-fueler-off-form button').click()
                        expect(b.locator('.neoscorpion-truck-on-row')).to_contain_text('TRUCK OFF')
                        a.locator('[data-truck-on-form] button').click();expect(a.locator('.neoscorpion-truck-on-row')).to_contain_text('CONFIRMED')
                        save(a,6,14,2000);a.locator('.neoscorpion-fueler-off-form button').click();a.locator('[data-fueler-submit-form] button').click()
                        expect(a.locator('[data-fueler-submission-status]')).to_contain_text('SUBMITTED');verify_current()
                        p.bring_to_front();save(b,None,10,1000);b.locator('[data-fueler-submit-form] button').click()
                        expect(b.locator('[data-fueler-submission-status]')).to_contain_text('SUBMITTED')
                        F().ready(d,'/neoscorpion/fuel-dispatch')
                        if row.locator('[data-neoscorpion-dispatch-details]').get_attribute('aria-expanded')!='true':row.locator('[data-neoscorpion-dispatch-details]').click()
                        # Original-tail verification is directly actionable in
                        # the console, not buried in the retired prior-work drawer.
                        prior=d.locator(f'#neoscorpion-dispatch-detail-{mid}-1 [data-action-key="verify-{b_id}"]')
                        expect(prior).to_be_visible()
                        expect(prior.locator('[name="physical_work_id"]')).to_have_value(str(b_id))
                        with F.app.app_context():
                            source=db.session.get(NeoScorpionFuelWorkState,b_id)
                            sequence,receipt=source.physical_sequence_number,source.fueler_submission_version
                        expect(prior.locator('[name="physical_sequence"]')).to_have_value(str(sequence))
                        expect(prior.locator('[name="expected_submission_version"]')).to_have_value(str(receipt))
                        prior.locator('[data-dispatch-verification-form] button').click()
                        expect(prior).to_have_count(0,timeout=12000)
                        with F.app.app_context():
                            events=NeoScorpionFuelingEvent.query.filter_by(fuel_assignment_id=aid).order_by(NeoScorpionFuelingEvent.id).all()
                            self.assertEqual([(e.fuel_work_state_id,e.tail_number,e.transfer_fuel_gallons) for e in events],[(first_id,tail_a,3000),(last_id,tail_a,2000),(b_id,tail_b,1000)])
                            self.assertEqual(NeoScorpionSortTruck.query.filter_by(fuel_truck_id=tid).one().current_gallons,4000)
                        self.assertEqual(errors,[])
                        self.assertFalse(p.evaluate('document.documentElement.scrollWidth>innerWidth+1'))
                    finally:browser.close()
        finally:F.tearDownClass()
