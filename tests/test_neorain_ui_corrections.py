"""Presentation invariants without changing canonical time/writeback contracts."""
from datetime import datetime, time
from html import unescape
import re
import unittest
from unittest.mock import patch
from app.extensions import db
from app.models import MasterFlightSchedule
from app.services.neorain_reports import report_context, briefing_text, save_report_entry
from tests import test_neorain_overrides as fixtures


class RainUICorrectionsTest(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.RainOverrideTest();self.f.setUp();self.addCleanup(self.f.doCleanups)

    def report_html(self):
        with patch('app.services.neorain_reports.snapshot_current_google',return_value='Google unavailable'):
            response=self.f.client.get('/neorain/rainrock?operation_id='+str(self.f.operation.id))
        self.assertEqual(response.status_code,200)
        return response.get_data(as_text=True)

    def test_missing_report_metrics_are_concise_and_zero_remains_available(self):
        html=self.report_html()
        self.assertIn('data-report-volume-unavailable',html)
        self.assertIn('data-report-staffing-unavailable',html)
        self.assertNotIn('class="rain-report-metric"',html)
        self.assertNotIn('aria-label="Separate Hub / Shift and Ramp staffing"',html)
        save_report_entry(self.f.operation,'actual_volume','0',0,self.f.actor.id)
        save_report_entry(self.f.operation,'ramp_planned_payroll','0',0,self.f.actor.id)
        html=self.report_html()
        self.assertNotIn('data-report-volume-unavailable',html)
        self.assertNotIn('data-report-staffing-unavailable',html)
        self.assertIn('class="rain-report-metric"',html)
        self.assertIn('<strong>0</strong>',html)
        self.assertIn('aria-label="Separate Hub / Shift and Ramp staffing"',html)

    def test_briefing_content_exactly_matches_existing_projection(self):
        with patch('app.services.neorain_reports.snapshot_current_google',return_value='Google unavailable'):
            report=report_context(self.f.operation,current=True)
            expected=briefing_text(report)
            response=self.f.client.get('/neorain/daily-briefing?operation_id='+str(self.f.operation.id))
        self.assertEqual(response.status_code,200)
        raw=re.search(r'<pre[^>]*id="briefing-text"[^>]*>(.*?)</pre>',response.get_data(as_text=True),re.S).group(1)
        self.assertEqual(unescape(raw),expected)
        self.assertIn('COPY BRIEFING',response.get_data(as_text=True))

    def test_one_std_etd_value_per_layout_and_reset_preserves_std_master(self):
        departure=self.f.departure
        master=MasterFlightSchedule(gateway_id=self.f.gateway.id,gateway_code=self.f.gateway.code,
            sort_name='night',mission_type='departure',flight_number=departure.flight_number,
            origin=self.f.gateway.code,destination=departure.destination,active=True,active_days='monday',planned_time_local=time(2))
        db.session.add(master);db.session.flush();departure.master_flight_schedule_id=master.id;db.session.commit()
        before=(departure.planned_datetime_utc,departure.planned_datetime_local,master.planned_time_local)
        html=self.f.client.get('/neorain/outbound').get_data(as_text=True)
        cell=re.search(r'<td data-label="STD/ETD">(.*?)</td>',html,re.S).group(1)
        self.assertEqual(cell.count('data-rain-override-time'),1)
        self.assertNotIn('aria-label="STD"',cell)
        self.assertIsNotNone(re.search(r'<dt>STD/ETD</dt><dd>\s*<div class="rain-override"',html))
        response=self.f.edit(departure,'etd','0125')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json['row']['etd'],'01:25')
        response=self.f.edit(departure,'etd',action='reset')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json['value'],'')  # Existing API contract stays canonical.
        self.assertEqual(response.json['row']['etd'],departure.planned_datetime_local.strftime('%H:%M'))
        self.assertEqual((departure.planned_datetime_utc,departure.planned_datetime_local,master.planned_time_local),before)
