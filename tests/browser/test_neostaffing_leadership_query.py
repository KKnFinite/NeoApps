"""Small synthetic leadership fixture on current NeoStaffing screens, two engines."""
from datetime import date
import unittest

from playwright.sync_api import expect

from app.extensions import db
from app.models import StaffingLeadershipAssignment, StaffingPerson, StaffingUnit
from tests.browser import test_mobile_drawer as fixture_module


class LeadershipBoardBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Fixture = fixture_module.MobileDrawerBrowserTest
        Fixture.setUpClass()
        with Fixture.app.app_context():
            sort = StaffingUnit(name='N12 Night', unit_type='sort')
            operation = StaffingUnit(name='N12 Ramp', unit_type='operation', parent=sort)
            department = StaffingUnit(name='N12 Department', unit_type='department', parent=operation)
            area = StaffingUnit(name='N12 Work Area', unit_type='work_area', parent=department,
                                required_headcount=4)
            db.session.add_all([sort, operation, department, area])
            db.session.flush()
            for number, (unit, classification) in enumerate(((sort, 'division_manager'),
                (operation, 'manager'), (department, 'full_time_supervisor'),
                (area, 'part_time_supervisor'), (area, 'twenty_c_full_time_supervisor'))):
                person = StaffingPerson(employee_id=f'LEADER-{number}', first_name='Synthetic',
                    last_name=f'Leader {number}', classification=classification,
                    seniority_date=date(2020, 1, 1))
                db.session.add(person)
                db.session.flush()
                db.session.add(StaffingLeadershipAssignment(person=person, unit=unit,
                    leadership_level=unit.unit_type))
            db.session.commit()
            cls.people_before = [(p.id, p.employee_id, p.classification, p.updated_at)
                                 for p in StaffingPerson.query.order_by(StaffingPerson.id)]
            cls.assignments_before = [(a.id, a.person_id, a.unit_id, a.active, a.updated_at)
                                      for a in StaffingLeadershipAssignment.query.order_by(StaffingLeadershipAssignment.id)]

    @classmethod
    def tearDownClass(cls):
        fixture_module.MobileDrawerBrowserTest.tearDownClass()

    def board(self, engine):
        Fixture = fixture_module.MobileDrawerBrowserTest
        browser = getattr(Fixture.pw, engine).launch()
        errors = []
        try:
            for width, height in ((1920, 900), (390, 844)):
                with self.subTest(engine=engine, width=width):
                    context = browser.new_context(viewport={'width': width, 'height': height})
                    page = context.new_page()
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    Fixture().login(page)
                    Fixture().ready(page, '/neostaffing/org-chart')
                    expect(page.locator('[data-reporting-person]')).to_have_count(5)
                    expect(page.locator('[data-org-forest]')).to_have_attribute('data-org-layout', 'ready')
                    self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), width + 1)
                    Fixture().ready(page, '/neostaffing/app-management/required-headcount')
                    self.assertIn('/neostaffing/org-chart', page.url)
                    expect(page.locator('[data-reporting-person]')).to_have_count(5)
                    Fixture().ready(page, '/neostaffing/people')
                    expect(page.locator('body')).to_contain_text('LEADER-0')
                    page.screenshot(path=str(Fixture.evidence / f'n12-{engine}-{width}.png'))
                    context.close()
            context = browser.new_context()
            page = context.new_page()
            Fixture().login(page, 'drawer-watcher')
            Fixture().ready(page, '/neostaffing/org-chart')
            expect(page.locator('[data-reporting-person]')).to_have_count(5)
            expect(page.locator('button').filter(has_text='SAVE REPORTS TO')).to_have_count(0)
            context.close()
            self.assertEqual(errors, [])
            with Fixture.app.app_context():
                self.assertEqual(self.people_before, [(p.id, p.employee_id, p.classification, p.updated_at)
                    for p in StaffingPerson.query.order_by(StaffingPerson.id)])
                self.assertEqual(self.assignments_before, [(a.id, a.person_id, a.unit_id, a.active, a.updated_at)
                    for a in StaffingLeadershipAssignment.query.order_by(StaffingLeadershipAssignment.id)])
        finally:
            browser.close()

    def test_chromium(self):
        self.board('chromium')

    def test_webkit(self):
        self.board('webkit')
