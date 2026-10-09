"""Rendered shared editor, atomic failures, return state and trusted input."""
import unittest
from pathlib import Path
from playwright.sync_api import expect

from app.extensions import db
from app.models import StaffingPerson, StaffingUnit, StaffingReportingRelationship
from app.services import neostaffing as service
from tests.browser import test_neostaffing_shift_roster as roster_module


class EmployeeEditorBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.roster = roster_module.ShiftRosterBrowserTest
        cls.roster.setUpClass()
        cls.fixture = cls.roster.fixture
        cls.areas = cls.roster.area_ids
        cls.evidence = Path('instance/browser-evidence/employee-editor').resolve()
        cls.evidence.mkdir(parents=True,exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        cls.roster.tearDownClass()

    def person(self, employee_id, start='West Ballmat', classification='part_time'):
        with self.fixture.app.app_context():
            person = service.create_person(dict(employee_id=employee_id,first_name='Shared',last_name=employee_id,
                classification=classification,seniority_date='2020-01-01',employee_status='active'))
            service.assign_work_area(person,db.session.get(StaffingUnit,self.areas[start]))
            db.session.commit()
            return person.id

    def test_desktop_same_editor_atomic_error_close_and_return_filters_scroll(self):
        pid = self.person('EDITOR-DESKTOP','Door 1')
        browser = self.fixture.pw.chromium.launch()
        try:
            page = browser.new_page(viewport={'width':1280,'height':900})
            errors=[]; page.on('pageerror',lambda cause:errors.append(str(cause)))
            helper=self.fixture(); helper.login(page); helper.ready(page,'/neostaffing/shift-flow')
            card=page.locator(f'[data-roster-person="{pid}"]'); card.scroll_into_view_if_needed()
            search=page.locator('[data-roster-search]'); search.fill('Editor-Desktop')
            scroll=page.locator('[data-roster-scroll]'); x=scroll.evaluate('el=>el.scrollLeft')
            card.click(); dialog=page.locator('[data-employee-editor]'); expect(dialog).to_be_visible()
            self.assertEqual(dialog.locator('form').count(),1)
            self.assertEqual(dialog.locator('button[type="submit"]').count(),1)
            form=dialog.locator('form'); fields=form.locator('[name="first_name"]')
            start=form.locator('[data-employee-start]'); final=form.locator('[data-employee-final]')
            expect(final).to_have_value(str(self.areas['Door 1']))
            start.select_option(str(self.areas['Door 32']))
            expect(final).to_have_value(str(self.areas['Door 32']))
            fields.fill('Atomic')
            form.locator('[name="shift_flow_sort_start_work_area_id"]').select_option(str(self.areas['West Ballmat']))
            form.locator('[name="shift_flow_ballmat_transition"]').select_option('2')
            # A personal validation error must also roll back valid flow edits.
            form.locator('[name="seniority_date"]').fill('99/99/2020')
            form.locator('button[type="submit"]').click()
            expect(form.locator('[data-employee-error]')).to_be_visible(); expect(dialog).to_be_visible()
            with self.fixture.app.app_context():
                person=db.session.get(StaffingPerson,pid)
                self.assertEqual(person.first_name,'Shared'); self.assertIsNone(person.shift_flow_plan)
                self.assertEqual(service.assignment_service.shift_home(person).work_area_unit_id,self.areas['Door 1'])
            form.locator('[name="seniority_date"]').fill('01/01/2020')
            form.locator('[data-employee-final]').select_option(str(self.areas['Door 1']))
            form.locator('button[type="submit"]').click()
            expect(dialog).not_to_be_visible()
            expect(search).to_have_value('Editor-Desktop')
            self.assertAlmostEqual(scroll.evaluate('el=>el.scrollLeft'),x,delta=2)
            expect(page.locator(f'[data-roster-person="{pid}"]')).to_have_class('shift-door-person is-wave-2 is-search-match is-search-current')
            helper.ready(page,'/neostaffing/people?classification=part_time&search=EDITOR-DESKTOP&per_page=25')
            original=page.url
            page.locator(f'.neostaffing-people-roster-table a[href*="person_id={pid}"]').click()
            expect(dialog).to_be_visible()
            expect(dialog.locator('[name="first_name"]')).to_have_value('Atomic')
            expect(dialog.locator('[name="shift_flow_ballmat_transition"]')).to_have_value('2')
            dialog.locator('[name="last_name"]').fill('Editor-Desktop')
            dialog.locator('button[type="submit"]').click(); expect(dialog).not_to_be_visible()
            self.assertEqual(page.url,original)
            page.screenshot(path=str(self.evidence/'desktop-shared-editor-return.png'),full_page=True)
            self.assertFalse(errors)
        finally: browser.close()

    def test_mobile_create_door_default_override_green_and_delete_with_name_id(self):
        browser=self.fixture.pw.chromium.launch()
        try:
            context=browser.new_context(viewport={'width':390,'height':844},is_mobile=True,has_touch=True)
            page=context.new_page(); errors=[]; page.on('pageerror',lambda error:errors.append(str(error)))
            helper=self.fixture(); helper.login(page); helper.ready(page,'/neostaffing/shift-flow')
            page.locator('[data-employee-add="shift"]').tap()
            dialog=page.locator('[data-employee-editor]'); expect(dialog).to_be_visible()
            form=dialog.locator('form')
            self.assertLess(form.locator('[name="employee_id"]').bounding_box()['y'],form.locator('legend').bounding_box()['y'])
            form.locator('[name="employee_id"]').fill('MOBILE-SEASONAL')
            form.locator('[name="first_name"]').fill('Mobile'); form.locator('[name="last_name"]').fill('Seasonal')
            form.locator('[name="seniority_date"]').fill('01/01/2020')
            form.locator('[name="classification"]').select_option('seasonal')
            start=form.locator('[data-employee-start]'); final=form.locator('[data-employee-final]')
            start.select_option(str(self.areas['Door 32'])); expect(final).to_have_value(str(self.areas['Door 32']))
            start.select_option(str(self.areas['Door 34'])); expect(final).to_have_value(str(self.areas['Door 34']))
            final.select_option(str(self.areas['Door 1'])); start.select_option(str(self.areas['Door 32']))
            expect(final).to_have_value(str(self.areas['Door 1']))
            final.select_option(str(self.areas['Door 32']))
            self.assertLessEqual(dialog.evaluate('el=>el.scrollWidth'),dialog.evaluate('el=>el.clientWidth'))
            self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'),390)
            page.screenshot(path=str(self.evidence/'mobile-editor-seasonal.png'))
            form.locator('button[type="submit"]').tap(); expect(dialog).not_to_be_visible()
            with self.fixture.app.app_context():
                person=StaffingPerson.query.filter_by(employee_id='MOBILE-SEASONAL').one(); pid=person.id
                self.assertEqual(person.classification,'seasonal')
                self.assertEqual(person.shift_flow_plan.final_door_work_area_id,self.areas['Door 32'])
                self.assertEqual(service.assignment_service.shift_home(person).work_area_unit_id,self.areas['Door 32'])
            card=page.locator(f'[data-roster-person="{pid}"]'); expect(card).to_have_class('shift-door-person is-at-door')
            card.tap(); expect(dialog).to_be_visible()
            messages=[]
            dismiss = lambda prompt: (messages.append(prompt.message), prompt.dismiss())
            page.on('dialog', dismiss)
            dialog.locator('[data-employee-delete]').tap(); expect(dialog).to_be_visible()
            self.assertIn('Mobile Seasonal',messages[0]); self.assertIn('MOBILE-SEASONAL',messages[0])
            page.remove_listener('dialog', dismiss); page.on('dialog',lambda prompt:prompt.accept())
            dialog.locator('[data-employee-delete]').tap(); expect(dialog).not_to_be_visible()
            expect(page.locator(f'[data-roster-person="{pid}"]')).to_have_count(0)
            self.assertFalse(errors)
        finally: browser.close()

    def test_trusted_desktop_and_touch_drop_missing_plan_does_not_open_editor(self):
        browser=self.fixture.pw.chromium.launch()
        try:
            for mobile in (False,True):
                pid=self.person('TOUCH-NO-PLAN' if mobile else 'MOUSE-NO-PLAN',classification='seasonal')
                width=390 if mobile else 1280
                context=browser.new_context(viewport={'width':width,'height':900},is_mobile=mobile,has_touch=mobile)
                page=context.new_page(); helper=self.fixture(); helper.login(page); helper.ready(page,'/neostaffing/shift-flow')
                card=page.locator(f'[data-roster-person="{pid}"]'); expect(page.locator(f'[data-roster-needs-people] [data-roster-person="{pid}"]')).to_have_count(1)
                card.scroll_into_view_if_needed()
                scroll=page.locator('[data-roster-scroll]'); source=card.bounding_box(); box=scroll.bounding_box()
                x,y=source['x']+source['width']/2,source['y']+source['height']/2
                if mobile:
                    session=context.new_cdp_session(page)
                    def touch(kind,tx=None,ty=None):
                        session.send('Input.dispatchTouchEvent',{'type':kind,'touchPoints':[] if kind=='touchEnd' else [{'id':1,'x':tx,'y':ty,'radiusX':8,'radiusY':8,'force':1}]})
                    touch('touchStart',x,y); touch('touchMove',x+12,y); touch('touchMove',box['x']+box['width']-8,y)
                    expect(page.locator('[data-roster-range]')).to_have_text('D6–D1',timeout=15000)
                    door='Door 1'
                else:
                    page.mouse.move(x,y); page.mouse.down(); page.mouse.move(x-12,y,steps=4)
                    page.mouse.move(box['x']+8,y,steps=10)
                    page.wait_for_function("document.querySelector('[data-roster-scroll]').scrollLeft <= 1")
                    door='Door 34'
                target=page.locator(f'[data-final-door-target="{self.areas[door]}"]'); dest=target.bounding_box()
                dx,dy=dest['x']+dest['width']/2,dest['y']+45
                with page.expect_response(lambda response:response.url.endswith('/final-door')) as saved:
                    if mobile: touch('touchMove',dx,dy); touch('touchEnd')
                    else: page.mouse.move(dx,dy,steps=10); page.mouse.up()
                self.assertEqual(saved.value.status,200,saved.value.text())
                expect(target.locator(f'[data-roster-person="{pid}"]')).to_have_count(1)
                expect(page.locator(f'[data-roster-needs-people] [data-roster-person="{pid}"]')).to_have_count(0)
                expect(page.locator('[data-employee-editor]')).not_to_be_visible()
                card.click(); expect(page.locator('[data-employee-editor]')).to_be_visible()
                context.close()
        finally: browser.close()

    def test_blocked_delete_keeps_editor_open_and_shows_history_error(self):
        pid=self.person('DELETE-BLOCKED')
        target=self.person('REPORTS-TARGET')
        with self.fixture.app.app_context():
            db.session.add(StaffingReportingRelationship(person_id=pid,reports_to_person_id=target,active=False))
            db.session.commit()
        browser=self.fixture.pw.chromium.launch()
        try:
            page=browser.new_page(viewport={'width':1280,'height':900}); helper=self.fixture(); helper.login(page)
            helper.ready(page,f'/neostaffing/people?person_id={pid}')
            dialog=page.locator('[data-employee-editor]'); expect(dialog).to_be_visible()
            page.on('dialog',lambda prompt:prompt.accept()); dialog.locator('[data-employee-delete]').click()
            expect(dialog.locator('[data-employee-error]')).to_contain_text('history'); expect(dialog).to_be_visible()
            dialog.locator('[data-employee-close]').click(); expect(dialog).not_to_be_visible()
        finally: browser.close()
