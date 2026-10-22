"""Real CSRF-protected desktop/mobile confirmation, deletion and admin restore."""
import unittest
from datetime import datetime
from pathlib import Path
from playwright.sync_api import expect
from app.extensions import db
from app.models import StaffingPerson,User
from app.models.staffing_employee_record import StaffingEmployeeRecord as Record,StaffingEmployeeRecordEvent as RecordEvent
from app.models.staffing_employee_recovery import StaffingEmployeeRecovery as Backup
from app.services import neostaffing as staffing
from tests.browser import test_neostaffing_shift_roster as roster_module


class EmployeeRecoveryBrowserTest(unittest.TestCase):
    def test_chromium(self):self.exercise('chromium')
    def test_webkit(self):self.exercise('webkit')

    def exercise(self,engine):
        roster=roster_module.ShiftRosterBrowserTest;roster.setUpClass();F=roster.fixture;browser=None
        evidence=Path('instance/browser-evidence/employee-recovery');evidence.mkdir(parents=True,exist_ok=True)
        try:
            browser=getattr(F.pw,engine).launch()
            for width in (1440,390,375):
                code=f'RECOVER-{engine}-{width}'
                with F.app.app_context():
                    actor=User.query.filter_by(username='drawer-admin').one()
                    person=staffing.create_person(dict(employee_id=code,first_name='Recovery',last_name='Fixture',
                        classification='part_time',seniority_date='2020-01-01',employee_status='active'))
                    staffing.assign_work_area(person,db.session.get(roster_module.StaffingUnit,roster.area_ids['Door 1']))
                    staffing.create_shift_flow_plan(person,dict(shift_flow_final_door_work_area_id=roster.area_ids['Door 1']),
                        db.session.get(roster_module.StaffingUnit,roster.area_ids['Door 1']))
                    pid=person.id
                    record=Record(person_id=pid,kind='verbal',body='Historical evidence retained in recovery',
                        context_json='[]',created_by=actor.id,finalized_at=datetime.utcnow(),finalized_by=actor.id,acknowledgment='delivered')
                    db.session.add(record);db.session.flush();rid=record.id
                    db.session.add(RecordEvent(record_id=rid,sequence=1,kind='created',body='Original audit',actor_id=actor.id))
                    db.session.commit()
                # Isolate interception from the application's installed PWA worker.
                context=browser.new_context(viewport={'width':width,'height':900},has_touch=width<500,
                    service_workers='block')
                page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                F().login(page);F().ready(page,f'/neostaffing/people?person_id={pid}')
                editor=page.locator('[data-employee-editor]');expect(editor).to_be_visible()
                posts=[];page.on('request',lambda request:posts.append(request.url) if request.method=='POST' and request.url.endswith('/delete') else None)
                editor.locator('[data-employee-delete]').click()
                confirmation=page.locator('[data-employee-delete-confirm]')
                expect(confirmation).to_be_visible();expect(confirmation).to_contain_text('ARE YOU SURE?')
                expect(confirmation).to_contain_text(code);expect(confirmation).to_contain_text('Recovery Fixture')
                expect(confirmation).to_contain_text('24 hours')
                confirmation.screenshot(path=str(evidence/f'{engine}-{width}-confirmation.png'))
                confirmation.get_by_role('button',name='CANCEL',exact=True).click()
                expect(confirmation).not_to_be_visible();self.assertFalse(posts)
                with F.app.app_context():self.assertIsNotNone(db.session.get(StaffingPerson,pid))
                if width==390:
                    editor.locator('[name="first_name"]').fill('Retain this draft')
                    intercepted=[]
                    def fail_delete(route):
                        intercepted.append(route.request.url)
                        route.abort('failed')
                    page.route('**/people/*/delete',fail_delete)
                    editor.locator('[data-employee-delete]').click()
                    confirmation.get_by_role('button',name='YES, DELETE EMPLOYEE',exact=True).click()
                    expect(editor.locator('[data-employee-error]')).to_contain_text('could not be confirmed')
                    expect(editor.locator('[name="first_name"]')).to_have_value('Retain this draft')
                    expect(editor.locator('[data-employee-delete]')).to_be_enabled()
                    self.assertEqual(len(intercepted),1)
                    page.unroute('**/people/*/delete')
                    with F.app.app_context():self.assertIsNotNone(db.session.get(StaffingPerson,pid))
                editor.locator('[data-employee-delete]').click()
                confirmation.get_by_role('button',name='YES, DELETE EMPLOYEE',exact=True).click()
                expect(editor).not_to_be_visible();self.assertEqual(len(posts),2 if width==390 else 1)
                with F.app.app_context():
                    self.assertIsNone(db.session.get(StaffingPerson,pid))
                    backup=Backup.query.filter_by(person_id=pid,state='deleted').one();bid=backup.id
                    self.assertIsNone(db.session.get(Record,rid))
                    self.assertIsNotNone(User.query.filter_by(username='drawer-admin').one())
                F().ready(page,'/neostaffing/app-management/employee-recovery')
                row=page.locator(f'[data-recovery-id="{bid}"]');expect(row).to_contain_text(code)
                self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'),width)
                row.screenshot(path=str(evidence/f'{engine}-{width}-recoverable.png'))
                row.get_by_role('button',name='RESTORE EMPLOYEE',exact=True).click()
                expect(row.locator('[data-restore-status]')).to_have_text('Employee restored.')
                page.reload();expect(page.locator(f'[data-recovery-id="{bid}"]')).to_have_count(0)
                with F.app.app_context():
                    self.assertIsNotNone(db.session.get(StaffingPerson,pid))
                    self.assertEqual(db.session.get(Record,rid).body,'Historical evidence retained in recovery')
                    self.assertEqual(RecordEvent.query.filter_by(record_id=rid).count(),1)
                F().ready(page,f'/neostaffing/people?person_id={pid}')
                expect(editor.locator('[name="employee_id"]')).to_have_value(code)
                self.assertFalse(errors)
                context.close()
        finally:
            if browser:browser.close()
            roster.tearDownClass()
