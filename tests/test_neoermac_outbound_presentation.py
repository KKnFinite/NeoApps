"""Presentation-only pull timing and shared outbound row contracts."""
import re
import unittest
from datetime import datetime, time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask

from app.services.neoermac_transport import outbound_snapshot
from app.services.neoermac_view_outbound import _pull_timing_state, _row_for_destination


class OutboundPresentationTest(unittest.TestCase):
    def test_clock_boundaries_early_and_midnight(self):
        cases = (
            ("pure", time(1, 20), time(1, 25), "on-time"),
            ("pure", time(1, 20), time(1, 26), "late"),
            ("mix", time(1, 20), time(1, 21), "on-time"),
            ("mix", time(1, 20), time(1, 22), "late"),
            ("pure", time(1, 20), time(1, 19), "on-time"),
            ("mix", time(1, 20), time(1, 19), "on-time"),
            ("pure", time(23, 59), time(0, 3), "on-time"),
            ("pure", time(23, 59), time(0, 5), "late"),
            ("mix", time(23, 59), time(0, 0), "on-time"),
            ("mix", time(23, 59), time(0, 1), "late"),
            ("pure", time(0, 3), time(23, 59), "on-time"),
            ("mix", time(0, 3), time(23, 59), "on-time"),
        )
        for key, planned, actual, expected in cases:
            with self.subTest(key=key, planned=planned, actual=actual):
                self.assertEqual(_pull_timing_state(planned, actual, key), expected)
        for key in ("pure", "mix"):
            self.assertEqual(_pull_timing_state(time(1), None, key), "")
            self.assertEqual(_pull_timing_state(None, time(1), key), "")
            self.assertEqual(_pull_timing_state(time(1), time(2), key, no_pull=True), "")

    def row(self, *, missing=False, no_pull=False):
        mission = None if missing else SimpleNamespace(
            id=41, flight_number="UPS501", assigned_tail_number="N501UP",
            departure_status="scheduled", planned_datetime_local=datetime(2026, 6, 11, 2, 35),
            pure_pull_time_local=time(1, 20), mix_pull_time_local=time(1, 55),
            actual_pure_pull_time_local=None if no_pull else time(1, 45),
            actual_mix_pull_time_local=None if no_pull else time(2, 17),
        )
        assignments = [dict(door=door, location="Belt fixture") for door in ("D32", "D34")]
        pulls = [SimpleNamespace(door=door, no_pure_pull=True, no_mix_pull=True,
                    actual_pure_pull_time_local=None, actual_mix_pull_time_local=None)
                 for door in ("D32", "D34")] if no_pull else []
        with patch('app.services.neoermac_view_outbound.mission_display_timing_data', return_value={
            'effective_window_minutes':20, 'adjusted_pure_pull_time':time(1, 40),
            'adjusted_mix_pull_time':time(2, 15),
        }):
            return _row_for_destination("SDF", assignments, pulls, None, mission, {"N501UP":"E02"}, {})

    def snapshot(self, row):
        app = Flask(__name__, template_folder=str(Path('app/templates').resolve()))
        with app.app_context():
            snapshot = outbound_snapshot(dict(rows=[row], operation=None))
            full = app.jinja_env.get_template('neonodes/neoermac/_view_outbound_content.html').render(rows=[row])
        return snapshot, full

    def test_adjusted_plan_and_shared_desktop_mobile_rendering(self):
        row = self.row()
        self.assertEqual(row['pull_timing'], {'pure':'on-time', 'mix':'late'})
        snapshot, full = self.snapshot(row)
        self.assertEqual(snapshot['order'], ['m41'])
        desktop, mobile = (snapshot['rows']['m41'][key] for key in ('desktop','mobile'))
        self.assertEqual(desktop.count('<td '), 11)
        self.assertNotIn('Location', full)
        self.assertNotIn('data-outbound-field="location"', desktop)
        for field in ('pure-plan', 'pure-actual', 'mix-plan', 'mix-actual'):
            self.assertIn('data-outbound-pull-cell="' + field + '"', desktop)
        self.assertTrue(desktop.lstrip().startswith('<tr '))
        self.assertTrue(mobile.lstrip().startswith('<article '))
        self.assertEqual(mobile.count('data-neoermac-outbound-mobile-field='), 6)
        for html in (desktop, mobile):
            self.assertIn('data-outbound-key="m41"', html)
            self.assertRegex(html, r'class="neoermac-outbound-actual is-pull-on-time">\s*01:45')
            self.assertRegex(html, r'class="neoermac-outbound-actual is-pull-late">\s*02:17')
        fields = dict(re.findall(r'data-neoermac-outbound-mobile-field="([^"]+)">(.*?)(?=<span class="neoermac-outbound-mobile-field|</article>)', mobile, re.S))
        for field, top, bottom in (('destination','SDF','UPS501'), ('tail','N501UP','E02'), ('doors','D32','D34')):
            self.assertRegex(fields[field], 'neoermac-outbound-top">' + top)
            self.assertRegex(fields[field], 'neoermac-outbound-bottom">' + bottom)
            self.assertLess(fields[field].index(top), fields[field].index(bottom))
        for key, planned, actual in (('pure','01:40','01:45'), ('mix','02:15','02:17')):
            self.assertRegex(fields[key], 'neoermac-outbound-top">' + planned)
            self.assertLess(fields[key].index(planned), fields[key].index(actual))

    def test_missing_and_no_pull_render_neutral_in_both_rows(self):
        for missing, no_pull in ((True, False), (False, True)):
            row = self.row(missing=missing, no_pull=no_pull)
            self.assertEqual(row['pull_timing'], {'pure':'', 'mix':''})
            snapshot, _ = self.snapshot(row)
            for html in next(iter(snapshot['rows'].values())).values():
                self.assertNotIn('is-pull-on-time', html)
                self.assertNotIn('is-pull-late', html)
                if no_pull:
                    self.assertIn('NO PURE', html)
                    self.assertIn('NO MIX PULL', html)
                if missing:
                    self.assertIn('is-missing-mission', html)
                    self.assertIn('data-outbound-key="missing:SDF"', html)
