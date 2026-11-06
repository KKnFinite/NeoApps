"""One isolated cross-screen workflow for availability, drafts and saved notes."""
import unittest
from datetime import date, datetime, timedelta
from unittest.mock import patch
from playwright.sync_api import expect
from app.extensions import db
from app.models import (User, SortDateOperation, SortDateMission, NeoScorpionSortFueler,
    NeoScorpionSortTruck, NeoScorpionFuelTruck, NeoScorpionFuelAssignment)
from app.services import neoscorpion as fuel
from app.services.access_control import ensure_default_gateway_and_nodes, backfill_default_gateway_node_roles
from app.services.password_policy import set_user_password
from tests.browser import test_mobile_drawer as fixtures


class BreakNotesBrowserTest(unittest.TestCase):
    def test_chromium_and_webkit_break_sync_and_tail_note_indicator(self):
        for engine in ('chromium','webkit'):
            with self.subTest(engine=engine):self.exercise(engine)

    def exercise(self,engine):
        F=fixtures.MobileDrawerBrowserTest;F.setUpClass();browser=None
        F.app.config['LIVE_SCREEN_REFRESH_INTERVAL_MS']=1000
        try:
            with F.app.app_context():
                gateway=ensure_default_gateway_and_nodes();admin=User.query.filter_by(username='drawer-admin').one()
                worker=User(username='break-fueler',email='break-fueler@example.test',role='operator',
                    email_verified_at=datetime.utcnow(),first_name='Break',last_name='Fueler')
                set_user_password(worker,'BrowserFixture123!');db.session.add(worker);db.session.flush()
                backfill_default_gateway_node_roles(worker,role='operator')
                op=SortDateOperation(gateway_id=gateway.id,gateway_code=gateway.code,sort_date=date.today(),
                    sort_name='night',generated_by_user_id=admin.id,window_minutes=60)
                db.session.add(op);db.session.flush();oid,uid=op.id,worker.id
                db.session.add(NeoScorpionSortFueler(sort_date_operation_id=oid,user_id=uid))
                truck=NeoScorpionFuelTruck(gateway_id=gateway.id,truck_number='BREAK-1',capacity_gallons=20000)
                db.session.add(truck);db.session.flush()
                db.session.add(NeoScorpionSortTruck(sort_date_operation_id=oid,fuel_truck_id=truck.id,
                    status='available',starting_gallons=10000,current_gallons=10000))
                ids=[]
                for i in range(2):
                    mission=SortDateMission(sort_date_operation_id=oid,sort_date=op.sort_date,gateway_code=gateway.code,
                        sort_name='night',mission_type='departure',mission_source='manual',flight_number=f'UPS90{i}',
                        assigned_tail_number=f'N49{i}UP',origin=gateway.code,destination='MSP',timezone='America/Chicago',
                        planned_datetime_utc=datetime.utcnow()+timedelta(hours=2),planned_fuel_load=25000)
                    db.session.add(mission);db.session.flush();ids.append(mission.id)
                    if i==0:
                        assignment=NeoScorpionFuelAssignment(sort_date_operation_id=oid,sort_date_mission_id=mission.id,
                            assigned_fueler_user_id=uid,assigned_truck_id=truck.id,confirmed_tail_number=mission.assigned_tail_number)
                        db.session.add(assignment);db.session.flush()
                        fuel.confirm_fueler_truck_on(gateway,worker,assignment.id,expected_cycle=1,
                            expected_tail=mission.assigned_tail_number,expected_truck_id=truck.id)
                        fuel.save_fueler_entry(gateway,worker,dict(assignment_id=assignment.id,
                            remaining_left='6',remaining_ctr='0',remaining_right='6',apu_running='no',notes='Saved initial'))
                db.session.commit()
            with patch('app.services.neoscorpion.current_existing_operational_sort_operations',
                side_effect=lambda gateway,now=None:[db.session.get(SortDateOperation,oid)]):
                browser=getattr(F.pw,engine).launch()
                dispatch=browser.new_context(viewport={'width':1440,'height':900}).new_page()
                mobile=browser.new_context(viewport={'width':390,'height':844}).new_page()
                observer=browser.new_context(viewport={'width':1440,'height':900}).new_page()
                errors=[]
                for p in (dispatch,mobile,observer):p.on('pageerror',lambda e:errors.append(str(e)))
                F().login(dispatch);F().login(observer);F().login(mobile,'break-fueler')
                for p in (dispatch,observer):
                    p.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1','seen')")
                    F().ready(p,'/neoscorpion/fuel-dispatch?assets=open')
                F().ready(mobile,'/neoscorpion/fueler')
                draft=mobile.locator('textarea[name="notes"]').first
                expect(draft).to_have_value('Saved initial');draft.fill('Unsaved draft kept');draft.focus()
                form=dispatch.locator(f'[data-fueler-break-form]:has(input[name="user_id"][value="{uid}"])')
                notice=mobile.locator('[data-fueler-break-notice]')
                for action,paused in (('BREAK',True),('OFF BREAK',False)):
                    with dispatch.expect_response(lambda r:r.request.method=='POST' and '/fuel-dispatch/assets' in r.url) as response:
                        form.get_by_role('button',name=action,exact=True).click()
                    self.assertEqual(response.value.status,200,response.value.text())
                    if paused:expect(notice).to_be_visible(timeout=12000)
                    else:expect(notice).to_be_hidden(timeout=12000)
                    expect(draft).to_have_value('Unsaved draft kept');expect(draft).to_be_focused()
                    for p in (dispatch,observer):
                        option=p.locator(f'[data-dispatch-mission-id="{ids[1]}"] select[name="assigned_fueler_user_id"] option[value="{uid}"]')
                        if paused:
                            expect(option).to_be_disabled(timeout=12000)
                            self.assertEqual(option.evaluate('e=>getComputedStyle(e).color'),'rgb(255, 107, 107)')
                        else:expect(option).to_be_enabled(timeout=12000)
                    expect(form.get_by_role('button',name='OFF BREAK' if paused else 'BREAK',exact=True)).to_be_visible(timeout=12000)
                row=dispatch.locator(f'[data-dispatch-mission-id="{ids[0]}"]')
                row.locator('[data-neoscorpion-dispatch-details]').click()
                details=dispatch.locator(f'#scorpion-details-{ids[0]}-1')
                # Use the actual row's DETAILS target; no parallel editing route.
                details=dispatch.locator('#'+row.locator('[data-neoscorpion-dispatch-details]').get_attribute('aria-controls'))
                section=details.locator('[data-console-section="tail-notes"]')
                section.locator(':scope > summary').click()
                note=section.locator('textarea[name="notes"]');note.fill('Saved aircraft note')
                with dispatch.expect_response(lambda r:'/tail-notes' in r.url and r.request.method=='POST') as response:
                    section.get_by_role('button',name='SAVE',exact=True).click()
                self.assertEqual(response.value.status,200)
                for p in (dispatch,observer):
                    mark=p.locator(f'[data-tail-note-indicator][data-tail-note-tail="N490UP"]')
                    expect(mark).to_be_visible(timeout=12000)
                    self.assertEqual(mark.evaluate('e=>getComputedStyle(e).color'),'rgb(255, 216, 74)')
                    self.assertTrue(mark.evaluate('e=>getComputedStyle(e).fontSize===getComputedStyle(e.parentElement).fontSize'))
                    expect(p.locator('[data-dispatch-tail-change-warning]')).to_have_count(0)
                dispatch.locator('[data-operational-board-toggle]:visible').click()
                expect(row.locator('[data-tail-note-indicator]')).to_be_visible()
                dispatch.screenshot(path=str(F.evidence/f'break-notes-{engine}-board.png'))
                dispatch.locator('[data-operational-board-toggle]:visible').click()
                dispatch.set_viewport_size({'width':390,'height':844})
                expect(row.locator('[data-tail-note-indicator]')).to_be_visible()
                self.assertFalse(dispatch.evaluate('document.documentElement.scrollWidth>innerWidth+1'))
                dispatch.screenshot(path=str(F.evidence/f'break-notes-{engine}-mobile.png'))
                dispatch.set_viewport_size({'width':1440,'height':900})
                with dispatch.expect_response(lambda r:'/tail-notes' in r.url and r.request.method=='POST'):
                    section.locator('[data-tail-note-clear]').click()
                for p in (dispatch,observer):
                    expect(p.locator('[data-tail-note-indicator][data-tail-note-tail="N490UP"]')).to_be_hidden(timeout=12000)
                expect(draft).to_have_value('Unsaved draft kept')
                self.assertEqual(errors,[])
        finally:
            if browser:browser.close()
            F.tearDownClass()
