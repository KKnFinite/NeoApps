"""Real employee-editor transfer, historical time autosave and stale/revoked reads."""
import unittest
from datetime import date,datetime
from pathlib import Path
from unittest.mock import patch
from playwright.sync_api import expect
from app.extensions import db
from app.models import User,StaffingPerson,StaffingUnit,SortDateOperation
from app.models.staffing_timecard import StaffingTimecardSlice as Slice,StaffingTimecardSegment as Segment
from app.services import neostaffing as staffing
from app.services import neostaffing_timecards as tc
from tests.browser import test_mobile_drawer as existing


class RetainedTimecardsBrowserTest(unittest.TestCase):
    def test_chromium_webkit_transfer_prior_week_autosave_stale_and_revocation(self):
        F=existing.MobileDrawerBrowserTest;F.setUpClass();evidence=Path('instance/w4/browser');evidence.mkdir(parents=True,exist_ok=True)
        try:
            F.app.config['CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE']=datetime(2026,9,12,23)
            with F.app.app_context():
                actor=User.query.filter_by(username='drawer-admin').one();actor.employee_id='W4-SUP'
                night=StaffingUnit(name='Night',unit_type='sort');ramp=StaffingUnit(name='Ramp',unit_type='operation',parent=night)
                shift=StaffingUnit(name='Shift',unit_type='department',parent=ramp)
                east=StaffingUnit(name='East Ballmat',unit_type='work_area',parent=shift);west=StaffingUnit(name='West Ballmat',unit_type='work_area',parent=shift)
                db.session.add_all([night,ramp,shift,east,west]);db.session.flush()
                manager=staffing.create_person(dict(employee_id=actor.employee_id,first_name='Authorized',last_name='Manager',classification='full_time_supervisor',seniority_date='2020-01-01'))
                staffing.create_leadership_assignment(manager,shift)
                current=SortDateOperation(gateway_code='RFD',sort_date=date(2026,9,12),sort_name='night',generated_by_user_id=actor.id)
                prior=SortDateOperation(gateway_code='RFD',sort_date=date(2026,9,3),sort_name='night',generated_by_user_id=actor.id)
                db.session.add_all([current,prior]);db.session.flush();cases=[]
                for engine in ('chromium','webkit'):
                    person=staffing.create_person(dict(employee_id='W4-'+engine,first_name='Retained',last_name=engine,classification='part_time',seniority_date='2020-01-01'))
                    home=staffing.assign_work_area(person,east)
                    tc.sync_attendance(prior,{person.id:'here'},{person.id:home},actor.id,as_of=date(2026,9,12))
                    db.session.flush();row=Slice.query.filter_by(person_id=person.id).one();cases.append((engine,person.id,row.id,row.context_json,row.work_area_unit_id))
                db.session.commit();oid,mid,wid=current.id,manager.id,west.id
            with patch('app.services.neostaffing.current_night_attendance_operation',side_effect=lambda gateway=None:db.session.get(SortDateOperation,oid)):
                for engine,pid,rid,frozen,old_area in cases:
                    browser=getattr(F.pw,engine).launch();errors=[]
                    try:
                        context=browser.new_context(viewport={'width':1440,'height':900},service_workers='block')
                        page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)));helper=F();helper.login(page)
                        path='/neosektor/manage-employees?area=ebm&mode=times&period=week&date=2026-09-03'
                        helper.ready(page,path);row=page.locator(f'[data-timecard-id="{rid}"]');expect(row).to_be_visible()
                        row.locator('[data-add-segment]').click();row.locator('[data-start]').fill('2200');row.locator('[data-start]').press('Tab')
                        with page.expect_response(lambda r:r.request.method=='POST' and '/timecards/save' in r.url) as saved:
                            row.locator('[data-end]').fill('0200');row.locator('[data-end]').press('Tab')
                        self.assertEqual(saved.value.status,200);expect(row.locator('[data-row-status]')).to_contain_text('Saved')
                        expect(row.locator('[data-row-hours]')).to_have_text('4.00 hrs')
                        page.screenshot(path=str(evidence/f'{engine}-before-transfer.png'),full_page=True)
                        helper.ready(page,f'/neostaffing/people?person_id={pid}')
                        dialog=page.locator('[data-employee-editor]');expect(dialog).to_be_visible()
                        dialog.locator('[data-employee-start]').select_option(str(wid))
                        with page.expect_response(lambda r:r.request.method=='POST' and f'/people/{pid}/update' in r.url) as moved:
                            dialog.locator('button[type="submit"]').click()
                        self.assertEqual(moved.value.status,200)
                        expect(dialog).not_to_be_visible()
                        helper.ready(page,path);expect(page.locator(f'[data-timecard-id="{rid}"]')).to_have_count(0)
                        west_path=path.replace('area=ebm','area=wbm')
                        for width in (1440,390):
                            page.set_viewport_size({'width':width,'height':900});helper.ready(page,west_path)
                            row=page.locator(f'[data-timecard-id="{rid}"]');expect(row).to_be_visible()
                            expect(row).to_contain_text('East Ballmat');expect(row.locator('[data-row-hours]')).to_have_text('4.00 hrs')
                            page.screenshot(path=str(evidence/f'{engine}-after-transfer-{width}.png'),full_page=True)
                        stale=context.new_page();stale.on('pageerror',lambda error:errors.append(str(error)));helper.ready(stale,west_path)
                        with page.expect_response(lambda r:r.request.method=='POST' and '/timecards/save' in r.url) as edited:
                            row.locator('[data-end]').fill('0300');row.locator('[data-end]').press('Tab')
                        self.assertEqual(edited.value.status,200);expect(row.locator('[data-row-status]')).to_contain_text('Saved')
                        old=stale.locator(f'[data-timecard-id="{rid}"]')
                        with stale.expect_response(lambda r:r.request.method=='POST' and '/timecards/save' in r.url) as conflict:
                            old.locator('[data-end]').fill('0400');old.locator('[data-end]').press('Tab')
                        self.assertEqual(conflict.value.status,409);expect(old.locator('[data-row-status]')).to_contain_text('changed')
                        expect(old.locator('[data-end]')).to_have_value('04:00')
                        with F.app.app_context():
                            persisted=db.session.get(Slice,rid)
                            self.assertEqual((persisted.context_json,persisted.work_area_unit_id),(frozen,old_area))
                            self.assertEqual(Segment.query.filter_by(slice_id=rid).one().end_utc.hour,8)
                            db.session.get(StaffingPerson,mid).active=False;db.session.commit()
                        self.assertEqual(page.goto(helper.origin+west_path).status,403)
                        with F.app.app_context():db.session.get(StaffingPerson,mid).active=True;db.session.commit()
                        self.assertEqual(errors,[])
                    finally:browser.close()
        finally:F.tearDownClass()
