"""Shared shell rendering must leave page authority and mutation forms intact."""
import unittest
import re
from types import SimpleNamespace
from flask import render_template, url_for
from html.parser import HTMLParser

from tests import test_neostaffing_employee_records as fixture
from app.extensions import db
from app.models import StaffingLeadershipAssignment, StaffingReportingRelationship
from app.services import neostaffing as staffing


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

    def test_dashboard_has_launcher_without_sidebar_or_secondary_controls(self):
        html = self.client.get('/neostaffing').get_data(as_text=True)
        self.assertIn('neostaffing-launch-grid', html)
        self.assertNotIn('data-operational-sidebar ', html)
        self.assertNotIn('data-staffing-secondary ', html)
        inner = self.client.get('/neostaffing/people').get_data(as_text=True)
        rail = inner.split('data-operational-sidebar ', 1)[1].split('</aside>', 1)[0]
        self.assertNotIn('>Home<', rail)
        self.assertIn('Global operational navigation', rail)

    def test_chart_excludes_hourly_reports_and_labels_only_active_canonical_areas(self):
        supervisor = self.person('FT-SUP', classification='full_time_supervisor')
        db.session.add(StaffingReportingRelationship(person=self.manager, reports_to_person=supervisor))
        # Legacy hourly reporting/leadership rows must not leak into this management projection.
        db.session.add(StaffingReportingRelationship(person=self.peer, reports_to_person=supervisor))
        db.session.add(StaffingLeadershipAssignment(person=self.peer, unit=self.areas['ebm'], leadership_level='work_area'))
        stale = StaffingLeadershipAssignment(person=self.manager, unit=self.areas['wbm'], leadership_level='work_area', active=False)
        db.session.add(stale)
        db.session.commit()
        context = staffing.management_org_chart_context(supervisor.id)
        self.assertEqual([p.id for p in context['direct_reports']], [self.manager.id])
        self.assertNotIn(self.peer.id, [p.id for p in context['visible_people']])
        self.assertIsNone(staffing.management_org_chart_context(self.peer.id)['selected_person'])
        self.assertEqual(context['management_scope_labels'][self.manager.id], ['Shift · East Ballmat'])
        operational = staffing.org_chart_context(self.areas['ebm'].id)
        self.assertEqual([a.person_id for a in operational['selected_detail']['leadership']], [self.manager.id])
        self.leadership.active = False
        stale.active = True
        db.session.commit()
        html = self.client.get(f'/neostaffing/org-chart?view=management&person_id={self.manager.id}').get_data(as_text=True)
        self.assertIn('Shift · West Ballmat', html)
        self.assertNotIn('Shift · East Ballmat', html)
        self.assertIn('DIRECT REPORTS · 0', html)

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


    def navigation(self, *, desktop=True, denied=(), settings=True, endpoint="neostaffing.people"):
        with self.app.test_request_context('/neostaffing/people') as context:
            context.request.url_rule = SimpleNamespace(endpoint=endpoint)
            return render_template('neostaffing/_navigation_items.html',
                staffing_nav_class='motherbrain-desktop-side-link' if desktop else '',
                user_can=lambda permission: permission not in denied,
                neostaffing_settings_visible=settings,
                neostaffing_nav={'actionable_requests':3,'unread_notifications':7})

    def nav_links(self, html):
        return re.findall(r'<a\b[^>]*data-staffing-nav[^>]*href="([^"]+)"',html)

    def test_navigation_exact_order_desktop_split_and_mobile_links(self):
        endpoints=['people','org_chart','attendance','reports','shift_flow','staffing_groups',
                   'change_requests','staffing_notifications','bulk_change','settings',
                   'employee_records','timecards','accountability','vacation_selection']
        with self.app.test_request_context():
            expected=[url_for('neostaffing.'+endpoint) for endpoint in endpoints]
            home=url_for('neostaffing.index')
        desktop=self.navigation()
        self.assertEqual(self.nav_links(desktop),expected)
        self.assertEqual(desktop.count('UNDER CONSTRUCTION'),1)
        self.assertLess(desktop.index('href="'+expected[9]+'"'),desktop.index('UNDER CONSTRUCTION'))
        self.assertLess(desktop.index('UNDER CONSTRUCTION'),desktop.index('href="'+expected[10]+'"'))
        mobile=self.navigation(desktop=False)
        self.assertEqual(self.nav_links(mobile),[home]+expected)
        self.assertNotIn('UNDER CONSTRUCTION',mobile)
        for html in (desktop,mobile):
            self.assertNotIn('aria-disabled',html)
            self.assertIn('aria-label="3 actionable requests">3</strong>',html)
            self.assertIn('aria-label="7 unread notifications">7</strong>',html)

    def test_navigation_preserves_each_permission_gate_on_both_surfaces(self):
        gates={'neostaffing.staffing_groups.view':['staffing_groups'],
               'neostaffing.change_requests.view':['change_requests','staffing_notifications'],
               'neostaffing.bulk_change.use':['bulk_change'],
               'neostaffing.vacation_selection.view':['vacation_selection']}
        for desktop in (True,False):
            all_links=self.nav_links(self.navigation(desktop=desktop))
            for permission,endpoints in gates.items():
                with self.subTest(desktop=desktop,permission=permission),self.app.test_request_context():
                    hidden={url_for('neostaffing.'+endpoint) for endpoint in endpoints}
                    self.assertEqual(self.nav_links(self.navigation(desktop=desktop,denied={permission})),
                                     [link for link in all_links if link not in hidden])
            with self.app.test_request_context():
                settings=url_for('neostaffing.settings')
            self.assertEqual(self.nav_links(self.navigation(desktop=desktop,settings=False)),
                             [link for link in all_links if link!=settings])

    def test_bottom_links_keep_active_page_and_full_shell_mobile_menu(self):
        for endpoint in ('employee_records','timecards','accountability','vacation_selection'):
            for desktop in (True,False):
                with self.subTest(endpoint=endpoint,desktop=desktop),self.app.test_request_context():
                    href=url_for('neostaffing.'+endpoint)
                    html=self.navigation(desktop=desktop,endpoint='neostaffing.'+endpoint)
                    link=re.search(r'<a[^>]*href="'+re.escape(href)+r'"[^>]*>',html).group()
                    self.assertIn('aria-current="page"',link)
        html=self.client.get('/neostaffing/people').get_data(as_text=True)
        rail=html.split('data-operational-sidebar ',1)[1].split('</aside>',1)[0]
        mobile=html.split('id="neo-mobile-drawer"',1)[1].split('</aside>',1)[0]
        self.assertIn('UNDER CONSTRUCTION',rail)
        self.assertNotIn('UNDER CONSTRUCTION',mobile)
        self.assertEqual(self.nav_links(rail),self.nav_links(mobile)[1:])
