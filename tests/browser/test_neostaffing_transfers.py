"""Actual confirmation, cancellation, Shift cleanup and optional dates on both engines."""
import unittest
from pathlib import Path
from playwright.sync_api import expect
from app.extensions import db
from app.models import StaffingPerson, StaffingUnit, StaffingWorkAssignment
from app.services import neostaffing as staffing
from tests.browser import test_neostaffing_shift_roster as roster


class EmployeeTransfersBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        roster.ShiftRosterBrowserTest.setUpClass()
        cls.fixture=roster.ShiftRosterBrowserTest.fixture
        cls.start_id=roster.ShiftRosterBrowserTest.area_ids['Door 1']
        cls.evidence=Path('instance/employee-transfers-20261104/screenshots');cls.evidence.mkdir(parents=True,exist_ok=True)
        with cls.fixture.app.app_context():
            home=db.session.get(StaffingUnit,cls.start_id)
            department=staffing.create_unit(dict(unit_type='department',name='Aviation Services',parent_id=home.parent.parent_id))
            cls.destination_id=staffing.create_unit(dict(unit_type='work_area',name='Deice',parent_id=department.id)).id
            db.session.commit()

    @classmethod
    def tearDownClass(cls):roster.ShiftRosterBrowserTest.tearDownClass()

    def employee(self,employee_id):
        with self.fixture.app.app_context():
            person=staffing.create_person(dict(employee_id=employee_id,first_name='Transfer',last_name=employee_id,
                classification='part_time',seniority_date='2020-01-01'))
            home=db.session.get(StaffingUnit,self.start_id)
            staffing.assign_work_area(person,home)
            staffing.create_shift_flow_plan(person,{'shift_flow_sort_start_work_area_id':home.id},home)
            db.session.commit();return person.id

    def exercise(self,engine):
        browser=getattr(self.fixture.pw,engine).launch()
        try:
            for width in (320,390,1440):
                with self.subTest(engine=engine,width=width):
                    pid=self.employee(f'TRANSFER-{engine}-{width}')
                    context=browser.new_context(viewport={'width':width,'height':900},has_touch=width<500)
                    try:
                        page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                        helper=self.fixture();helper.login(page)
                        helper.ready(page,f'/neostaffing/people?person_id={pid}')
                        editor=page.locator('[data-employee-editor]');expect(editor).to_be_visible()
                        editor.locator('[name=first_name]').fill('Unsaved')
                        editor.locator('[data-employee-transfer]').click()
                        expect(editor.locator('[data-employee-error]')).to_contain_text('Save your employee')
                        expect(editor.locator('[name=first_name]')).to_have_value('Unsaved')
                        editor.locator('[name=first_name]').fill('Transfer')
                        editor.locator('[data-employee-transfer]').click()
                        dialog=page.locator('[data-employee-transfer-dialog]');expect(dialog).to_be_visible()
                        expect(dialog).to_contain_text('TRANSFER EMPLOYEE')
                        dialog.locator('[name=destination_work_area_id]').select_option(str(self.destination_id))
                        dialog.get_by_role('button',name='CANCEL',exact=True).click();expect(dialog).not_to_be_visible()
                        with self.fixture.app.app_context():self.assertEqual(staffing.assignment_service.shift_home(db.session.get(StaffingPerson,pid)).work_area_unit_id,self.start_id)
                        editor.locator('[data-employee-transfer]').click()
                        dialog.locator('[name=destination_work_area_id]').select_option(str(self.destination_id))
                        page.screenshot(path=str(self.evidence/f'{engine}-{width}-confirm.png'))
                        self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
                        with page.expect_response(lambda response:response.request.method=='POST' and response.url.endswith(f'/people/{pid}/transfer'),timeout=15000) as transferred:
                            dialog.get_by_role('button',name='CONFIRM TRANSFER',exact=True).click()
                        self.assertEqual(transferred.value.status,200)
                        expect(dialog).not_to_be_visible(timeout=15000);expect(editor).not_to_be_visible(timeout=15000)
                        with self.fixture.app.app_context():
                            person=db.session.get(StaffingPerson,pid)
                            self.assertIsNone(person.shift_flow_plan)
                            self.assertEqual(StaffingWorkAssignment.query.filter_by(person_id=pid,active=True).one().work_area_unit_id,self.destination_id)
                        helper.ready(page,'/neostaffing/shift-flow')
                        expect(page.locator(f'[data-roster-person="{pid}"]')).to_have_count(0)
                        helper.ready(page,f'/neostaffing/people?person_id={pid}')
                        expect(editor).to_be_visible();expect(editor).to_contain_text('Deice')
                        editor.locator('[data-employee-transfer]').click()
                        dialog.locator('[name=destination_work_area_id]').select_option(str(self.start_id))
                        expect(dialog.locator('[data-transfer-shift-note]')).to_be_visible()
                        with page.expect_response(lambda response:response.request.method=='POST' and response.url.endswith(f'/people/{pid}/transfer'),timeout=15000) as returned:
                            dialog.get_by_role('button',name='CONFIRM TRANSFER',exact=True).click()
                        self.assertEqual(returned.value.status,200)
                        expect(editor).not_to_be_visible(timeout=15000)
                        helper.ready(page,'/neostaffing/shift-flow')
                        expect(page.locator(f'[data-roster-person="{pid}"]')).to_have_count(1)
                        page.locator('[data-employee-add="shift"]').click();expect(editor).to_be_visible()
                        form=editor.locator('[data-employee-form]')
                        form.locator('[name=employee_id]').fill(f'UNDATED-{engine}-{width}')
                        form.locator('[name=first_name]').fill('Seasonal');form.locator('[name=last_name]').fill('Optional')
                        form.locator('[name=classification]').select_option('part_time')
                        self.assertTrue(form.locator('[name=seniority_date]').evaluate('el=>el.required'))
                        form.locator('[name=classification]').select_option('seasonal')
                        self.assertFalse(form.locator('[name=seniority_date]').evaluate('el=>el.required'))
                        form.locator('[data-employee-start]').select_option(str(self.start_id))
                        form.get_by_role('button',name='SAVE',exact=True).click();expect(editor).not_to_be_visible()
                        with self.fixture.app.app_context():
                            person=StaffingPerson.query.filter_by(employee_id=f'UNDATED-{engine}-{width}').one()
                            self.assertIsNone(person.seniority_date)
                        page.screenshot(path=str(self.evidence/f'{engine}-{width}-seasonal-saved.png'))
                        self.assertFalse(errors,errors)
                    finally:context.close()
        finally:browser.close()

    def test_chromium(self):self.exercise('chromium')
    def test_webkit(self):self.exercise('webkit')
