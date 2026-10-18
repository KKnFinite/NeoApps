"""Actual animation, arrival paint and cross-device shared-setting refresh."""
from datetime import date, datetime, timedelta
import unittest
from unittest.mock import patch
from playwright.sync_api import expect
from app.extensions import db
from app.models import (User, SortDateOperation, SortDateMission, SortDateParkingAssignment,
    NeoScorpionFuelAssignment, NeoScorpionTailFuelState, NeoScorpionFuelTruck,
    NeoScorpionSortTruck, NeoScorpionSortFueler, NeoScorpionSettings)
from app.services.access_control import ensure_default_gateway_and_nodes, backfill_default_gateway_node_roles
from app.services.password_policy import set_user_password
from tests.browser import test_mobile_drawer as fixtures


class ReadyPulseBrowserTest(unittest.TestCase):
    def test_chromium_shared_pulse_and_arrival_colors(self):self.exercise('chromium')
    def test_webkit_shared_pulse_and_arrival_colors(self):self.exercise('webkit')

    def exercise(self, engine):
        F=fixtures.MobileDrawerBrowserTest;F.setUpClass();browser=None
        F.app.config['LIVE_SCREEN_REFRESH_INTERVAL_MS']=1000
        try:
            with F.app.app_context():
                g=ensure_default_gateway_and_nodes();admin=User.query.filter_by(username='drawer-admin').one()
                admin.first_name='YOGI';admin.last_name=''
                observer=User(username='pulse-observer',email='pulse-observer@example.test',role='grandmaster',
                              email_verified_at=datetime.utcnow())
                set_user_password(observer,'BrowserFixture123!');db.session.add(observer);db.session.flush()
                backfill_default_gateway_node_roles(observer,role='grandmaster')
                op=SortDateOperation(gateway_id=g.id,gateway_code=g.code,generated_by_user_id=admin.id,
                    sort_date=date.today(),sort_name='night',window_minutes=60)
                db.session.add(op);db.session.flush();oid=op.id;uid=admin.id;ids=[]
                db.session.add(NeoScorpionSettings(gateway_id=g.id))
                truck=NeoScorpionFuelTruck(gateway_id=g.id,truck_number='PULSE-1',capacity_gallons=10000)
                db.session.add(truck);db.session.flush();tid=truck.id
                db.session.add(NeoScorpionSortTruck(sort_date_operation_id=oid,fuel_truck_id=tid,
                    starting_gallons=9000,current_gallons=9000,status='available'))
                db.session.add(NeoScorpionSortFueler(sort_date_operation_id=oid,user_id=uid))
                now=datetime.utcnow()
                for i,state in enumerate(('arrived','on_ground','scheduled','scheduled')):
                    tail=f'N{491+i}UP'
                    mission=SortDateMission(sort_date_operation_id=oid,sort_date=op.sort_date,gateway_code=g.code,
                        sort_name='night',mission_type='departure',mission_source='manual',flight_number=f'UPS90{i}',
                        assigned_tail_number=tail,origin=g.code,destination='MSP',timezone='America/Chicago',
                        planned_datetime_utc=now+timedelta(minutes=5 if i<2 else 120),
                        planned_fuel_load=25000 if i<3 else None)
                    db.session.add(mission);db.session.flush();ids.append(mission.id)
                    db.session.add(NeoScorpionFuelAssignment(sort_date_operation_id=oid,sort_date_mission_id=mission.id,
                        confirmed_tail_number=tail))
                    db.session.add(NeoScorpionTailFuelState(sort_date_operation_id=oid,tail_number=tail,inbound_fuel_lbs=12000))
                    db.session.add(SortDateParkingAssignment(sort_date_operation_id=oid,tail_number=tail,
                        ramp_code='C',position_code=f'{7+i:02}',lane_number=1))
                    db.session.add(SortDateMission(sort_date_operation_id=oid,sort_date=op.sort_date,gateway_code=g.code,
                        sort_name='night',mission_type='arrival',mission_source='manual',flight_number=f'UPS80{i}',
                        assigned_tail_number=tail,origin='SDF',destination=g.code,arrival_status=state,
                        eta_datetime_utc=now-timedelta(hours=1),
                        actual_block_in_datetime_utc=now-timedelta(minutes=30) if state=='arrived' else None))
                db.session.commit()
            with patch('app.services.neoscorpion.current_existing_operational_sort_operations',
                       side_effect=lambda gateway,now=None:[db.session.get(SortDateOperation,oid)]):
                browser=getattr(F.pw,engine).launch();errors=[]
                first=browser.new_context(viewport={'width':1440,'height':900})
                second=browser.new_context(viewport={'width':1440,'height':900})
                page=first.new_page();other=second.new_page();settings=first.new_page()
                for p in (page,other,settings):p.on('pageerror',lambda e:errors.append(str(e)))
                F().login(page);F().login(other,'pulse-observer')
                for p in (page,other):
                    p.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1','seen')")
                    F().ready(p,'/neoscorpion/fuel-dispatch')
                row=lambda i,p=page:p.locator(f'.neoscorpion-dispatch-primary-row[data-dispatch-mission-id="{ids[i]}"]')
                cells='.neoscorpion-dispatch-flight, .neoscorpion-dispatch-etd-parking'
                animations=lambda r:r.evaluate("r=>r.getAnimations({subtree:true}).filter(a=>a.animationName==='neoscorpion-assignment-accent-pulse').length")
                expected=('rgb(143, 255, 168)','rgb(255, 240, 176)','rgb(213, 216, 222)')
                for board in (False,True):
                    if board:page.locator('[data-operational-board-toggle]:visible').click()
                    for i in range(3):
                        row(i).scroll_into_view_if_needed()
                        expect(row(i)).to_be_visible()
                        expect(row(i).locator('[data-fuel-status]')).to_have_attribute('data-fuel-status','ready')
                        sample=row(i).evaluate("""async r=>{
                            await new Promise(requestAnimationFrame);await new Promise(requestAnimationFrame);
                            const animations=r.getAnimations({subtree:true}).filter(a=>a.animationName==='neoscorpion-assignment-accent-pulse');
                            const cells=Array.from(r.querySelectorAll('.neoscorpion-dispatch-flight,.neoscorpion-dispatch-etd-parking'));
                            const samples=[];
                            for(const time of [0,1150,2200]){
                                animations.forEach(a=>{a.pause();a.currentTime=time;});
                                await new Promise(requestAnimationFrame);await new Promise(requestAnimationFrame);
                                samples.push({colors:cells.flatMap(c=>Array.from(c.querySelectorAll('.neoscorpion-dispatch-arrival-text'),n=>getComputedStyle(n).color)),
                                    opacity:cells.map(c=>Number(getComputedStyle(c,'::after').opacity))});
                            }
                            animations.forEach(a=>a.play());return {count:animations.length,samples};
                        }""")
                        self.assertEqual(sample['count'],2)
                        for phase in sample['samples']:self.assertEqual(phase['colors'],[expected[i]]*4)
                        self.assertLess(sample['samples'][0]['opacity'][0],.2)
                        self.assertGreater(sample['samples'][1]['opacity'][0],.95)
                    self.assertEqual(animations(row(3)),0)
                    for width,height in ((1440,900),(1920,1080),(390,844),(375,568)):
                        page.set_viewport_size({'width':width,'height':height})
                        self.assertFalse(page.evaluate('document.documentElement.scrollWidth>innerWidth+1'))
                        height_actual=row(0).bounding_box()['height']
                        # Turning only the accent off must not change geometry.
                        preferences=page.locator('[data-dispatch-preferences]')
                        preferences.evaluate("p=>p.dataset.readyAssignmentPulse='0'")
                        self.assertAlmostEqual(row(0).bounding_box()['height'],height_actual,delta=.1)
                        preferences.evaluate("p=>p.dataset.readyAssignmentPulse='1'")
                        self.assertAlmostEqual(height_actual,73 if width<761 else 98,delta=1)
                    page.set_viewport_size({'width':1440,'height':900})
                    row(0).scroll_into_view_if_needed()
                    page.screenshot(path=str(F.evidence/f'ready-pulse-{engine}-{"board" if board else "normal"}.png'))
                # Urgency is independent of arrival paint, including reduced motion.
                expect(row(0).locator('[data-fuel-status]')).to_have_attribute('data-fuel-status-color','red')
                page.emulate_media(reduced_motion='reduce')
                self.assertEqual(animations(row(0)),0)
                self.assertEqual(row(0).locator(cells).first.locator('strong').evaluate('e=>getComputedStyle(e).color'),expected[0])
                page.emulate_media(reduced_motion='no-preference')
                # Hold a real unsaved assignment draft so only the critical patch
                # can apply the shared switch. It must not replace/focus the draft.
                page.locator('[data-operational-board-toggle]:visible').click()
                draft=row(3).locator('[name="assigned_fueler_user_id"]');draft.select_option(str(uid));draft.focus()
                F().ready(settings,'/neoscorpion/settings')
                select=settings.locator('[name="ready_assignment_pulse_enabled"]')
                expect(select).to_have_value('1')
                for value in ('0','1'):
                    select.select_option(value)
                    select.locator('xpath=ancestor::form').get_by_role('button',name='Save Settings',exact=True).click()
                    expect(select).to_have_value(value)
                    for p in (page,other):
                        expect(p.locator('[data-dispatch-preferences]')).to_have_attribute('data-ready-assignment-pulse',value,timeout=12000)
                    self.assertEqual(animations(row(0)),2 if value=='1' else 0)
                    self.assertEqual(animations(row(0,other)),2 if value=='1' else 0)
                    expect(draft).to_have_value(str(uid));expect(draft).to_be_focused()
                # The second authenticated device makes a real assignment. Both
                # views must stop the accent when canonical READY becomes ASSIGNED.
                stale_draft=row(0).locator('[name="assigned_fueler_user_id"]')
                stale_draft.select_option(str(uid));stale_draft.focus()
                target=row(0,other)
                target.locator('[name="assigned_fueler_user_id"]').select_option(str(uid))
                target.locator('[name="assigned_truck_id"]').select_option(str(tid))
                target.locator('[data-dispatch-assignment-submit]').click()
                expect(target.locator('[data-fuel-status]')).to_have_attribute('data-fuel-status','assigned',timeout=12000)
                expect(row(0).locator('[data-dispatch-completion-slot]')).to_have_text('VISIT CHANGED',timeout=12000)
                for p in (page,other):self.assertEqual(animations(row(0,p)),0)
                expect(stale_draft).to_have_value(str(uid))
                expect(draft).to_have_value(str(uid))
                other.reload();expect(other.locator('[data-dispatch-preferences]')).to_have_attribute('data-ready-assignment-pulse','1')
                self.assertEqual(errors,[])
        finally:
            if browser:browser.close()
            F.tearDownClass()
