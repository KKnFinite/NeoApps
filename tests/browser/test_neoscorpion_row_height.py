"""Real shared sizing changes, unchanged secondary rows and natural expansion."""
from datetime import date, datetime, timedelta
import unittest
from unittest.mock import patch
from playwright.sync_api import expect
from app.extensions import db
from app.models import (User, SortDateOperation, SortDateMission, SortDateParkingAssignment,
    NeoScorpionFuelAssignment, NeoScorpionTailFuelState, NeoScorpionSettings, NeoScorpionFuelCycleHistory)
from app.services.access_control import ensure_default_gateway_and_nodes, backfill_default_gateway_node_roles
from app.services.password_policy import set_user_password
from tests.browser import test_mobile_drawer as fixtures


class RowHeightBrowserTest(unittest.TestCase):
    def test_chromium_shared_row_heights(self):self.exercise('chromium')
    def test_webkit_shared_row_heights(self):self.exercise('webkit')

    def exercise(self,engine):
        F=fixtures.MobileDrawerBrowserTest;F.setUpClass();browser=None
        F.app.config['LIVE_SCREEN_REFRESH_INTERVAL_MS']=1000
        try:
            with F.app.app_context():
                g=ensure_default_gateway_and_nodes();admin=User.query.filter_by(username='drawer-admin').one()
                observer=User(username='height-observer',email='height-observer@example.test',role='grandmaster',
                              email_verified_at=datetime.utcnow())
                set_user_password(observer,'BrowserFixture123!');db.session.add(observer);db.session.flush()
                backfill_default_gateway_node_roles(observer,role='grandmaster')
                op=SortDateOperation(gateway_id=g.id,gateway_code=g.code,generated_by_user_id=admin.id,
                    sort_date=date.today(),sort_name='night',window_minutes=60)
                db.session.add(op);db.session.flush();oid=op.id;ids=[]
                db.session.add(NeoScorpionSettings(gateway_id=g.id))
                for i in range(6):
                    tail=f'N{491+i}UP';state=('arrived','on_ground','scheduled')[i%3]
                    m=SortDateMission(sort_date_operation_id=oid,sort_date=op.sort_date,gateway_code=g.code,
                        sort_name='night',mission_type='departure',mission_source='manual',flight_number=f'UPS90{i}',
                        assigned_tail_number=tail,origin=g.code,destination='MSP',timezone='America/Chicago',
                        planned_datetime_utc=datetime.utcnow()+timedelta(hours=3,minutes=i),planned_fuel_load=25000)
                    db.session.add(m);db.session.flush();ids.append(m.id)
                    a=NeoScorpionFuelAssignment(sort_date_operation_id=oid,sort_date_mission_id=m.id,
                        confirmed_tail_number=tail,current_cycle_number=2 if i==0 else 1)
                    db.session.add(a);db.session.flush()
                    if i==0:
                        db.session.add(NeoScorpionFuelCycleHistory(sort_date_operation_id=oid,fuel_assignment_id=a.id,
                            mission_id=m.id,cycle_number=1,label='FUEL',snapshot=dict(tail_number=tail,
                            flight_number=m.flight_number,destination='MSP',parking_position='C07',tank_rows=[])))
                    db.session.add(NeoScorpionTailFuelState(sort_date_operation_id=oid,tail_number=tail,inbound_fuel_lbs=12000))
                    db.session.add(SortDateParkingAssignment(sort_date_operation_id=oid,tail_number=tail,
                        ramp_code='C',position_code=f'{7+i:02}',lane_number=1))
                    db.session.add(SortDateMission(sort_date_operation_id=oid,sort_date=op.sort_date,gateway_code=g.code,
                        sort_name='night',mission_type='arrival',mission_source='manual',flight_number=f'UPS80{i}',
                        assigned_tail_number=tail,origin='SDF',destination=g.code,arrival_status=state,
                        eta_datetime_utc=datetime.utcnow()-timedelta(hours=1),
                        actual_block_in_datetime_utc=datetime.utcnow()-timedelta(minutes=30) if state=='arrived' else None))
                db.session.commit()
            with patch('app.services.neoscorpion.current_existing_operational_sort_operations',
                       side_effect=lambda gateway,now=None:[db.session.get(SortDateOperation,oid)]):
                browser=getattr(F.pw,engine).launch();errors=[]
                normal=browser.new_context(viewport={'width':1440,'height':900})
                board=browser.new_context(viewport={'width':1920,'height':1080})
                page=normal.new_page();other=board.new_page();settings=normal.new_page()
                for p in (page,other,settings):p.on('pageerror',lambda e:errors.append(str(e)))
                F().login(page);F().login(other,'height-observer')
                for p in (page,other):
                    p.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1','seen')")
                    F().ready(p,'/neoscorpion/fuel-dispatch')
                other.locator('[data-operational-board-toggle]:visible').click()
                row=lambda i,p=page:p.locator(f'.neoscorpion-dispatch-primary-row[data-dispatch-mission-id="{ids[i]}"]')
                row(0).locator('[data-neoscorpion-dispatch-details]').click()
                detail=page.locator('#'+row(0).locator('[data-neoscorpion-dispatch-details]').get_attribute('aria-controls'))
                draft=detail.locator('[data-tail-note-form] textarea');draft.fill('Preserve the Dispatcher draft during shared sizing changes')
                F().ready(settings,'/neoscorpion/settings')
                control=settings.locator('[name="mission_row_height_percent"]')
                expect(control).to_have_value('100')
                expect(control.locator('option[value="100"]')).to_contain_text('Restore Default')
                for value in ('75','115','150','85','100'):
                    page.set_viewport_size({'width':1440,'height':900})
                    other.set_viewport_size({'width':1920,'height':1080})
                    draft.focus()
                    detail_height=detail.bounding_box()['height']
                    history_height=page.locator('[data-cycle-history]').bounding_box()['height']
                    control.select_option(value)
                    control.locator('xpath=ancestor::form').get_by_role('button',name='Save Settings',exact=True).click()
                    expect(control).to_have_value(value)
                    for p in (page,other):
                        expect(p.locator('[data-dispatch-preferences]')).to_have_attribute('data-mission-row-height-percent',value,timeout=12000)
                        expect(p.locator('[data-dispatch-preferences]')).to_have_attribute('data-ready-assignment-pulse','1')
                    expect(draft).to_have_value('Preserve the Dispatcher draft during shared sizing changes')
                    expect(draft).to_be_focused()
                    self.assertAlmostEqual(detail.bounding_box()['height'],detail_height,delta=1)
                    self.assertAlmostEqual(page.locator('[data-cycle-history]').bounding_box()['height'],history_height,delta=1)
                    for p,desktop_width in ((page,1440),(other,1920)):
                        for width,height in ((desktop_width,900),(390,844),(375,568)):
                            p.set_viewport_size({'width':width,'height':height})
                            target=(73 if width<761 else 98)*int(value)/100
                            heights=[row(i,p).bounding_box()['height'] for i in range(3)]
                            self.assertTrue(all(h>=target-1 for h in heights),(value,width,heights,target))
                            self.assertLessEqual(max(heights)-min(heights),1,(value,width,heights))
                            self.assertFalse(p.evaluate('document.documentElement.scrollWidth>innerWidth+1'))
                            for i,color in ((0,'rgb(143, 255, 168)'),(1,'rgb(255, 240, 176)')):
                                self.assertEqual(row(i,p).locator('.neoscorpion-dispatch-flight > strong').evaluate('e=>getComputedStyle(e).color'),color)
                            # All visible text/controls must remain inside their
                            # own row even at the most compact requested size.
                            self.assertTrue(row(0,p).evaluate("""r=>{
                                const box=r.getBoundingClientRect();
                                return [...r.querySelectorAll('td > strong,td > small,input,select,button')].filter(e=>e.getClientRects().length)
                                    .every(e=>{const b=e.getBoundingClientRect();return b.top>=box.top-1&&b.bottom<=box.bottom+1;});
                            }"""),(value,width))
                            if value=='100':self.assertAlmostEqual(heights[0],73 if width<761 else 98,delta=1)
                            if value in ('75','150'):
                                row(0,p).scroll_into_view_if_needed()
                                p.screenshot(path=str(F.evidence/f'row-height-{engine}-{"board" if p==other else "normal"}-{value}-{width}.png'))
                page.set_viewport_size({'width':1440,'height':900})
                # Native table sizing is a minimum: genuine longer operational
                # information may grow a row instead of being clipped/hidden.
                note=row(1).locator('[data-fuel-status-secondary]');before=row(1).bounding_box()['height']
                previous=note.inner_text()
                note.evaluate("e=>e.textContent='HOLD — REQUIRED AIRCRAFT FUEL VERIFICATION AND DISPATCHER REVIEW BEFORE RESUMING THIS PHYSICAL VISIT'")
                self.assertGreater(row(1).bounding_box()['height'],before)
                self.assertLessEqual(note.bounding_box()['y']+note.bounding_box()['height'],row(1).bounding_box()['y']+row(1).bounding_box()['height'])
                note.evaluate('(e,text)=>e.textContent=text',previous)
                other.reload()
                expect(other.locator('[data-dispatch-preferences]')).to_have_attribute('data-mission-row-height-percent','100')
                self.assertEqual(errors,[])
        finally:
            if browser:browser.close()
            F.tearDownClass()
