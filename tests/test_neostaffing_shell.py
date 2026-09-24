"""Shared shell rendering must leave page authority and mutation forms intact."""
import unittest
from html.parser import HTMLParser

from tests import test_neostaffing_employee_records as fixture


class Forms(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.forms = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == 'form':
            self.forms.append(dict(attrs))


class StaffingShellTest(unittest.TestCase):
    setUpBase = fixture.EmployeeRecordTest.setUpBase
    person = fixture.EmployeeRecordTest.person
    _login_approved_user = fixture.EmployeeRecordTest._login_approved_user
    setUp = fixture.EmployeeRecordTest.setUp
    tearDown = fixture.EmployeeRecordTest.tearDown

    def test_major_surfaces_share_one_shell_and_opt_in_only_read_forms(self):
        for path in ['people', 'attendance', 'timecards', 'employee-records',
                     'org-chart', 'shift-flow', 'vacation-selection', 'reports']:
            with self.subTest(path=path):
                response = self.client.get('/neostaffing/' + path)
                self.assertEqual(response.status_code, 200)
                html = response.get_data(as_text=True)
                self.assertEqual(html.count('data-operational-sidebar '), 1)
                self.assertEqual(html.count('data-staffing-secondary '), 1)
                self.assertNotIn('class="neostaffing-rail"', html)
                self.assertIn('data-mobile-navigation', html)
                for form in Forms(html).forms:
                    if 'data-staffing-filter' in form:
                        self.assertEqual(form.get('method', 'get').lower(), 'get')
                        self.assertNotIn('pdf', form.get('action', ''))

    def test_configuration_precedes_list_and_keeps_write_protection(self):
        html = self.client.get('/neostaffing/employee-records').get_data(as_text=True)
        self.assertLess(html.index('data-staffing-configure'), html.index('class="record-list"'))
        panel = html.split('data-staffing-configure', 1)[1].split('</details>', 1)[0]
        self.assertIn('<summary>CONFIGURE</summary>', panel)
        self.assertIn('name="csrf_token"', panel)
        self.assertIn('name="version"', panel)
        self.assertIn('method="post"', panel)
