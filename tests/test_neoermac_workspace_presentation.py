"""Presentation isolation without changing the shared attendance contract."""
from pathlib import Path
import unittest

from flask import Flask, render_template
from jinja2 import ChoiceLoader, DictLoader, FileSystemLoader
from types import SimpleNamespace as Row


class ErmacWorkspacePresentationTest(unittest.TestCase):
    def test_attendance_fields_and_real_sections_remain_intact(self):
        app = Flask(__name__)
        app.jinja_loader = ChoiceLoader([
            DictLoader({'base.html': '{% block content %}{% endblock %}'}),
            FileSystemLoader('app/templates'),
        ])
        app.add_url_rule('/neostaffing/shift-flow', 'neostaffing.shift_flow', lambda: '')
        person = Row(id=41, full_name='Fixture Employee')
        row = Row(person=person, status='working', status_label='Working',
                  status_writable=True, flow='Door 13')
        counts = dict.fromkeys(['called_in', 'no_call', 'scheduled_off', 'anniversary_day',
            'vacation', 'opt_day', 'disability', 'work_comp', 'funeral', 'jury', 'fmla',
            'military', 'personal_leave', 'cleared'], 0)
        counts.update(on_payroll=1, working=1, unmarked=0)
        with app.test_request_context():
            html = render_template('neostaffing/operational_manage_employees.html',
                title='Employee Attendance', attendance_workspace='ermac',
                back_url='/neoermac/door-view', attendance_scope_label='Selected Doors: D13 · D17',
                area_tabs=[], show_coming=True, can_edit_attendance=True,
                attendance=Row(operation=Row(id=7), counts=counts, here=[row], coming=[row],
                               status_choices=[('working', 'Working')]))
        for value in ['ATTENDANCE HERE', 'COMING TO THESE DOORS', 'Selected Doors: D13 · D17',
                      'name="status_41"', 'value="working" selected', 'SAVE ATTENDANCE',
                      'name="sort_date_operation_id" value="7"', 'data-operational-manage-employees']:
            self.assertIn(value, html)

    def test_workspace_asset_is_endpoint_scoped_and_shell_is_not_overridden(self):
        base = Path('app/templates/base.html').read_text()
        self.assertIn("request.endpoint in ['neoermac.door_view', 'neoermac.manage_employees']", base)
        css = Path('app/static/css/neoermac_door_workspace.css').read_text()
        for forbidden in ['.neo-mobile-bottom', '.mobile-bottom-nav', '.shell {', '100svh', '!important']:
            self.assertNotIn(forbidden, css)
        self.assertIn('.neostaffing-operational-attendance--ermac', css)
        for state in ['is-pull-due-soon', 'is-pull-due-now', 'is-pull-late']:
            self.assertIn(f':not(.{state})', css)
