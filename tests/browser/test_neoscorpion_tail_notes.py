"""Real notes editing, refresh safety and measured normal/Board View geometry."""
from contextlib import ExitStack
from dataclasses import replace
from datetime import date, datetime, timedelta
import unittest
from unittest.mock import patch
from playwright.sync_api import expect
from app.extensions import db
from app.models import (User, SortDateOperation, SortDateMission, NeoScorpionSortAssetState,
    NeoScorpionFuelTruck, NeoScorpionSortTruck, NeoScorpionSortFueler, NeoScorpionFuelAssignment)
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.neoscorpion_tail_notes import save_tail_note
from tests.browser import test_mobile_drawer as fixtures


class TailNotesBrowserTest(unittest.TestCase):
    def test_notes_live_drafts_conflict_and_measured_layouts(self):
        F = fixtures.MobileDrawerBrowserTest; F.setUpClass(); browser = None
        try:
            with F.app.app_context():
                gateway = ensure_default_gateway_and_nodes(); user = User.query.filter_by(username='drawer-admin').one()
                op = SortDateOperation(gateway_id=gateway.id, gateway_code=gateway.code, sort_date=date.today(),
                    sort_name='night', generated_by_user_id=user.id, window_minutes=60)
                db.session.add(op); db.session.flush()
                db.session.add_all([NeoScorpionSortAssetState(sort_date_operation_id=op.id, revision=1),
                    NeoScorpionSortFueler(sort_date_operation_id=op.id, user_id=user.id)])
                trucks=[]
                for number in ('10','20','30','40','50'):
                    truck=NeoScorpionFuelTruck(gateway_id=gateway.id,truck_number=number,capacity_gallons=10000,is_active=True)
                    db.session.add(truck);db.session.flush();trucks.append(truck)
                    db.session.add(NeoScorpionSortTruck(sort_date_operation_id=op.id,fuel_truck_id=truck.id,
                        status='available',starting_gallons=9000,current_gallons=9000))
                ids=[]
                for index in range(7):
                    mission=SortDateMission(sort_date_operation_id=op.id, sort_date=op.sort_date,
                        gateway_code=gateway.code,sort_name='night',mission_type='departure',mission_source='manual',
                        flight_number=f'UPS09{index:02}',origin=gateway.code,destination='SDF',assigned_tail_number=f'N43{index}UP',
                        timezone='America/Chicago',planned_source='manual',planned_datetime_utc=datetime.utcnow()+timedelta(hours=2),
                        planned_fuel_load=25000,fuel_status='waiting',departure_status='scheduled')
                    db.session.add(mission);db.session.flush();ids.append(mission.id)
                    db.session.add(NeoScorpionFuelAssignment(sort_date_operation_id=op.id,sort_date_mission_id=mission.id,
                        assigned_fueler_user_id=user.id,assigned_truck_id=trucks[index%5].id,confirmed_tail_number=mission.assigned_tail_number))
                db.session.commit(); oid,gid,uid=op.id,gateway.id,user.id
            with ExitStack() as stack:
                stack.enter_context(patch('app.services.neoscorpion.current_existing_operational_sort_operations',
                    side_effect=lambda gateway,now=None:[db.session.get(SortDateOperation,oid)]))
                from app.services.live_screen_refresh import live_screen_refresh_value
                stack.enter_context(patch('app.services.neoscorpion.live_screen_refresh_value',side_effect=lambda gateway,key,**kw:
                    replace(live_screen_refresh_value(gateway,key,**kw),effective_interval_ms=5000)))
                for engine,width in (('chromium',1440),('webkit',390)):
                    browser=getattr(F.pw,engine).launch()
                    context=browser.new_context(viewport={'width':width,'height':900},has_touch=width<900,is_mobile=width<900)
                    page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                    F().login(page);page.evaluate("localStorage.setItem('neoapps.neoscorpion.spear-splash.v1','seen')")
                    F().ready(page,'/neoscorpion/fuel-dispatch')
                    toggle=page.locator(f'[data-dispatch-mission-id="{ids[0]}"] [data-neoscorpion-mobile-details]:visible').first if width<900 else page.locator(f'[aria-controls="neoscorpion-dispatch-detail-{ids[0]}-1"]:visible').first
                    toggle.click()
                    detail=page.locator(f'#neoscorpion-dispatch-detail-{ids[0]}-1');expect(detail).to_be_visible()
                    form=detail.locator('[data-tail-note-form]');note=form.locator('textarea')
                    text=f'{engine} tail note <literal>\nSecond line'
                    note.fill(text)
                    with page.expect_response(lambda r:'/fuel-dispatch/tail-notes' in r.url and r.request.method=='POST') as saved:form.get_by_role('button',name='SAVE',exact=True).click()
                    self.assertEqual(saved.value.status,200);expect(form.locator('[data-tail-note-status]')).to_have_text('SAVED')
                    # Another browser sees the same canonical tail note; Fueler has no edit control.
                    fueler=context.new_page();F().ready(fueler,'/neoscorpion/fueler')
                    aircraft=fueler.locator('[data-tail-notes][data-tail="N430UP"]');expect(aircraft.locator('[data-tail-note-text]')).to_have_text(text)
                    self.assertEqual(aircraft.locator('textarea,button').count(),0)
                    page.bring_to_front();note.fill('Unfinished draft')
                    with F.app.app_context():
                        from app.models import Gateway, NeoScorpionTailNote
                        record=NeoScorpionTailNote.query.filter_by(gateway_id=gid,tail_number='N430UP').one()
                        save_tail_note(db.session.get(Gateway,gid),db.session.get(User,uid),dict(tail_number='N430UP',notes='Other dispatcher',expected_version=str(record.version)))
                        db.session.commit()
                    # Real existing poll must leave the draft untouched and reject a stale save.
                    self.assertEqual(page.request.get(page.url.split('/neoscorpion/')[0]+'/neoscorpion/fuel-dispatch/revision').status, 200)
                    expect(note).to_have_value('Unfinished draft')
                    with page.expect_response(lambda r:'/fuel-dispatch/tail-notes' in r.url and r.request.method=='POST') as stale:form.get_by_role('button',name='SAVE',exact=True).click()
                    self.assertEqual(stale.value.status,409);expect(note).to_have_value('Unfinished draft')
                    expect(form.locator('[data-tail-note-status]')).to_contain_text('changed in another window')
                    page.reload();page.wait_for_load_state('load')
                    if toggle.get_attribute('aria-expanded') != 'true': toggle.click()
                    expect(note).to_have_value('Other dispatcher')
                    # Clear persists while preserving the record/version, and updates Fueler by existing polling.
                    with page.expect_response(lambda r:'/fuel-dispatch/tail-notes' in r.url and r.request.method=='POST') as clear:form.get_by_role('button',name='CLEAR',exact=True).click()
                    self.assertEqual(clear.value.status,200);expect(note).to_have_value('')
                    fueler.bring_to_front();expect(aircraft.locator('[data-tail-note-text]')).to_have_text('-',timeout=15000)
                    fueler.close();page.bring_to_front()
                    note.focus()
                    with F.app.app_context():
                        record=NeoScorpionTailNote.query.filter_by(gateway_id=gid,tail_number='N430UP').one()
                        save_tail_note(db.session.get(Gateway,gid),db.session.get(User,uid),dict(tail_number='N430UP',notes='Live refresh note',expected_version=str(record.version)))
                        db.session.commit()
                    with page.expect_response(lambda r:'/fuel-dispatch/revision' in r.url,timeout=12000): pass
                    expect(note).to_be_focused();expect(note).to_have_value('')
                    page.evaluate("document.activeElement.blur();window.tailNoteContinuity='kept'")
                    expect(note).to_have_value('Live refresh note',timeout=15000)
                    expect(detail).to_be_visible();expect(toggle).to_have_attribute('aria-expanded','true')
                    self.assertEqual(page.evaluate('window.tailNoteContinuity'),'kept')
                    metrics=[]
                    for current_width in ((1440,1200,1920) if width>900 else (390,)):
                        page.set_viewport_size({'width':current_width,'height':900})
                        for board in ((False,True) if width>900 else (False,)):
                            if board:page.locator('[data-operational-board-toggle]:visible').first.click()
                            for zoom in (1,1.25) if width>900 else (1,):
                                page.locator('body').evaluate('(el,zoom)=>el.style.zoom=String(zoom)',zoom)
                                measure=page.evaluate('''() => {
                                    const rect=s=>document.querySelector(s).getBoundingClientRect();
                                    const truck=rect('.neoscorpion-truck-visual-panel'),sort=rect('.neoscorpion-dispatch-sort'),table=rect('.neoscorpion-table-wrap--sticky');
                                    return {truckBottom:truck.bottom,sortTop:sort.top,sortBottom:sort.bottom,tableTop:table.top,tableHeight:table.height,
                                        heights:[...document.querySelectorAll('.neoscorpion-dispatch-sort button')].map(b=>getComputedStyle(b).height),
                                        overflow:document.documentElement.scrollWidth>innerWidth+1};
                                }''')
                                metrics.append(dict(width=current_width,board=board,zoom=zoom,**measure))
                                self.assertTrue(all(h=='28px' for h in measure['heights']),measure)
                                self.assertLessEqual(measure['truckBottom'],measure['sortTop']+1,measure)
                                self.assertLessEqual(measure['sortBottom'],measure['tableTop']+1,measure)
                                self.assertGreater(measure['tableHeight'],50,measure);self.assertFalse(measure['overflow'],measure)
                            page.locator('body').evaluate("el=>el.style.zoom='1'")
                            # Horizontal truck scrolling stays inside its own region; table headers
                            # remain sticky in the independent mission scroller.
                            if board:
                                scroll=page.evaluate("""() => {
                                    const list=document.querySelector('.neoscorpion-truck-visual-list');
                                    const sort=document.querySelector('.neoscorpion-dispatch-sort');
                                    const top=sort.getBoundingClientRect().top;
                                    list.scrollLeft=list.scrollWidth;
                                    const wrap=document.querySelector('.neoscorpion-table-wrap--sticky');
                                    wrap.scrollTop=150;
                                    const head=wrap.querySelector('thead th').getBoundingClientRect();
                                    const box=wrap.getBoundingClientRect();
                                    const result={sortShift:Math.abs(sort.getBoundingClientRect().top-top),headTop:head.top,tableTop:box.top};
                                    list.scrollLeft=0;wrap.scrollTop=0;return result;
                                }""")
                                self.assertLessEqual(scroll['sortShift'],1)
                                self.assertAlmostEqual(scroll['headTop'],scroll['tableTop'],delta=3)
                            # Both collapse states must release space without an overlapping sort bar.
                            collapse=page.locator('[data-dispatch-collapse="trucks"]')
                            before=page.locator('.neoscorpion-table-wrap--sticky').bounding_box()['height']
                            for _ in range(2):
                                with page.expect_response(lambda r:'/fuel-dispatch/preferences' in r.url and r.request.method=='POST'):collapse.click()
                            if board:self.assertAlmostEqual(page.locator('.neoscorpion-table-wrap--sticky').bounding_box()['height'],before,delta=2)
                            page.screenshot(path=str(F.evidence/f'scorpion-tail-notes-{engine}-{current_width}-{"board" if board else "normal"}.png'),full_page=True)
                            if board:page.locator('.operational-board-exit').click()
                    import json
                    (F.evidence/f'scorpion-tail-notes-{engine}-metrics.json').write_text(json.dumps(metrics,indent=2),encoding='utf-8')
                    self.assertEqual(errors,[]);context.close();browser.close();browser=None
        finally:
            if browser:browser.close()
            F.tearDownClass()
