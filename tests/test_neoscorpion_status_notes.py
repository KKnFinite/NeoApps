"""Status copy changes no forecast, physical timestamps, or PROF facts."""
from datetime import datetime,timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import unittest
from jinja2 import Environment,FileSystemLoader
from app.services.neoscorpion import _attach_dispatch_off_estimates

class StatusNotesTest(unittest.TestCase):
    def test_compact_forecasts_retain_original_numeric_facts_and_full_tooltip(self):
        now=datetime(2026,10,14,5)
        for difference,finish,expected in ((0,now+timedelta(minutes=34),'EST OFF 00:34'),
                (-2,now+timedelta(minutes=34),'EST OFF 00:34'),
                (5,now+timedelta(minutes=34),'OVER +5 MIN'),(None,None,'TIMING UNKNOWN')):
            forecast=dict(likely=finish,difference=difference,source='BASELINE')
            row=dict(dispatch_status_key='fueling',physical_off_forecast=forecast)
            before=dict(forecast)
            with patch('app.services.neoscorpion.gateway_timezone',return_value='America/Chicago'):
                _attach_dispatch_off_estimates({'rows':[row]},SimpleNamespace(),{}, {},now_utc=now)
            self.assertEqual(row['fueler_off_status_note'],expected)
            self.assertEqual(forecast,before)
            self.assertEqual(row['fueler_off_prof_difference'],difference)
            if finish: self.assertIn('BASELINE',row['fueler_off_estimate_display'])

    def test_recorded_off_and_critical_warning_priority(self):
        env=Environment(loader=FileSystemLoader(Path(__file__).resolve().parents[1]/'app/templates'))
        template=env.from_string('{% from "neonodes/neoscorpion/_fuel_status.html" import fuel_status %}{{ fuel_status(row,dispatcher=true) }}')
        row=dict(dispatch_status_key='complete',dispatch_status_label='COMPLETE',cycle_type='fuel',off_time='00:14',fuel_status_secondary='')
        html=template.render(row=row)
        self.assertIn('<span>TRUCK OFF</span><span>00:14</span>',html)
        self.assertIn('neoscorpion-dispatch-size-small',html)
        row.update(dispatch_status_key='off',fuel_status_secondary='Fuel direction discrepancy')
        html=template.render(row=row)
        self.assertIn('FUEL DIRECTION',html)
        self.assertNotIn('<span>TRUCK OFF',html)
        row.update(dispatch_status_key='assigned',fuel_status_secondary='WAITING FOR TRUCK · TRUCK STILL FUELING')
        self.assertIn('>WAITING FOR TRUCK</small>',template.render(row=row))
