"""Shared desktop/mobile editor and real filtered roster in both engines."""
from pathlib import Path
import unittest
from playwright.sync_api import expect
from app.extensions import db
from app.models import StaffingPerson
from tests.browser import test_neostaffing_employee_editor as fixtures


class PreSeniorityBrowserTest(unittest.TestCase):
    def test_chromium_webkit_employee_edit_and_filter(self):
        helper=fixtures.EmployeeEditorBrowserTest
        helper.setUpClass();case=helper();evidence=Path('instance/staffing-test-sync-20261104/browser');evidence.mkdir(parents=True,exist_ok=True)
        try:
            for engine,width in (('chromium',1440),('webkit',390)):
                with self.subTest(engine=engine):
                    pid=case.person('PRE-'+engine,'Door 1')
                    browser=getattr(helper.fixture.pw,engine).launch()
                    try:
                        page=browser.new_page(viewport={'width':width,'height':900});errors=[]
                        page.on('pageerror',lambda e:errors.append(str(e)))
                        session=helper.fixture();session.login(page)
                        session.ready(page,f'/neostaffing/people?person_id={pid}')
                        dialog=page.locator('[data-employee-editor]');expect(dialog).to_be_visible()
                        form=dialog.locator('form');form.locator('[name=classification]').select_option('pre_seniority')
                        expect(form.locator('[name=seniority_date]')).to_have_attribute('required','')
                        page.screenshot(path=str(evidence/f'{engine}-pre-seniority.png'),full_page=True)
                        form.locator('button[type=submit]').click()
                        expect(dialog).not_to_be_visible(timeout=15000)
                        with helper.fixture.app.app_context():
                            self.assertEqual(db.session.get(StaffingPerson,pid).classification,'pre_seniority')
                        session.ready(page,'/neostaffing/people?classification=pre_seniority')
                        expect(page.locator('body')).to_contain_text('Pre-Seniority')
                        self.assertFalse(errors,errors)
                    finally:browser.close()
        finally:helper.tearDownClass()
