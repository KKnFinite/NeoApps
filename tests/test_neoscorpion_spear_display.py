"""Display-only SPEAR summaries and canonical Dispatch HTTP regressions."""
from pathlib import Path
from types import SimpleNamespace
import re
import unittest

from jinja2 import Environment, FileSystemLoader

from app.extensions import db
from app.services.neoscorpion import _attach_spear_plan
from tests import test_neoscorpion_fueling_status as status_fixture


class SpearDisplayTest(unittest.TestCase):
    def setUp(self):
        self.env = Environment(loader=FileSystemLoader(Path(__file__).resolve().parents[1]/'app/templates'), autoescape=True)

    def render(self, macro, **values):
        args = 'row' if macro == 'spear_indicator' else 'spear, parking'
        return self.env.from_string('{% from "neonodes/neoscorpion/_spear_display.html" import '+macro+' %}{{ '+macro+'('+args+') }}').render(**values)

    def test_single_indicator_priority_and_covered_not_mutated(self):
        for risk, reasons, problem, expected in (
            ('COVERED', (), None, 'READY'), ('LATE', ('arrival',), None, 'LATE'),
            ('AT RISK', ('arrival',), None, 'AT RISK'), ('COVERED', ('arrival',), None, 'WAITING'),
            (None, (), 'NO AVAILABLE TRUCK — active assignment', 'NO TRUCK'),
            (None, (), 'NO ELIGIBLE FUELER', 'NO FUELER'),
            (None, (), 'PARKING RAMP NOT MAPPED — travel cannot be planned safely', 'PARKING'),
            (None, (), 'NO FEASIBLE RESOURCE — truck fuel / capacity constraints', 'FUEL'),
        ):
            with self.subTest(expected=expected):
                row = dict(spear_risk=risk, spear_readiness_reasons=reasons, spear_problem=problem, spear_ready=True)
                html = self.render('spear_indicator', row=row)
                self.assertEqual(html.count('data-spear-tail-indicator'), 1)
                self.assertIn('SPEAR · '+expected, html)
                self.assertNotIn('COVERED', html)
                self.assertEqual(row['spear_risk'], risk)

    def test_labeled_parking_resources_risk_and_ramp_fallback(self):
        spear = dict(action_type='assign', flight_number='UPS0910', ramp='Echo', truck_number='10', fueler_name='DANIEL', risk='COVERED')
        for parking, label in (('E03', 'PARKING E03'), (None, 'RAMP ECHO')):
            html = self.render('spear_resources', spear=spear, parking=parking)
            for text in ('FLIGHT UPS0910', label, 'TRUCK 10', 'FUELER DANIEL'): self.assertIn(text, html)
            self.assertNotIn('COVERED', html)
        for risk in ('LATE', 'AT RISK'):
            spear['risk'] = risk
            self.assertIn(risk, self.render('spear_resources', spear=spear, parking='E03'))
        spear['action_type'] = 'top_off'
        html = self.render('spear_resources', spear=spear, parking=None)
        self.assertIn('TOP OFF · TRUCK 10', html)
        self.assertNotIn('FLIGHT', html)

    def test_card_parking_matches_canonical_mission_without_queries(self):
        rows = [dict(mission=SimpleNamespace(id=i), parking_position=parking, parking_valid=True)
                for i, parking in ((1,'B06'), (2,'E03'))]
        visual = dict(truck_id=7)
        plan = SimpleNamespace(risks_by_mission_id={2:'COVERED'}, readiness_by_mission_id={2:()},
            waiting_for_data_by_mission_id={}, unavailable_by_mission_id={}, steps=[SimpleNamespace(mission_id=2, truck_id=7)])
        _attach_spear_plan(rows, [visual], plan)
        self.assertEqual(visual['spear_parking_position'], 'E03')
        self.assertEqual(rows[1]['spear_risk'], 'COVERED')
        rows[1]['parking_valid'] = False
        _attach_spear_plan(rows, [visual], plan)
        self.assertIsNone(visual['spear_parking_position'])

    def test_status_secondary_is_compact_with_full_explanation_and_call_priority(self):
        template = self.env.from_string('{% from "neonodes/neoscorpion/_fuel_status.html" import fuel_status %}{{ fuel_status(row, dispatcher=true, can_ack=true) }}')
        for full, compact in (
            ('Needs Fueler / Truck', 'Fueler/Truck'),
            ('Fuel direction discrepancy', 'FUEL DIRECTION'),
            ('Dispatcher review requested', 'REVIEW REQUEST'),
            ('TAIL SWAP · advisory', 'TAIL SWAP'),
            ('A genuine operational interruption with a long explanation', 'SEE DETAILS'),
        ):
            with self.subTest(full=full):
                row = dict(cycle_type='fuel', dispatch_status_label='READY', fuel_status_secondary=full)
                html = template.render(row=row)
                self.assertIn('title="'+full+'"', html)
                self.assertIn('>'+compact+'</small>', html)
                self.assertEqual(html.count('data-fuel-status-secondary'), 1)
                row['call_dispatch_alert'] = True
                html = template.render(row=row)
                self.assertIn('CALL DISPATCH', html)
                self.assertNotIn('>'+compact+'</small>', html)
                self.assertEqual(html.count('data-fuel-status-secondary'), 1)


class SpearDisplayHttpTest(unittest.TestCase):
    setUp = status_fixture.FuelingStatusIntegrationTest.setUp
    login = status_fixture.FuelingStatusIntegrationTest.login

    def test_dispatch_and_live_panel_missing_etd_and_incomplete_data_return_200(self):
        self.login(self.dispatcher)
        self.mission.planned_datetime_utc = None
        for incomplete in (False, True):
            if incomplete:
                self.mission.planned_fuel_load = None
                self.tail.inbound_fuel_lbs = None
                self.arrival.arrival_status = 'scheduled'
            db.session.commit()
            for path in ('/neoscorpion/fuel-dispatch', '/neoscorpion/fuel-dispatch/live-panel'):
                with self.subTest(path=path, incomplete=incomplete):
                    response = self.client.get(path)
                    self.assertEqual(response.status_code, 200, response.text[:200])
                    html = response.json['html'] if response.is_json else response.text
                    self.assertEqual(len(re.findall('data-spear-tail-indicator', html)), 1)
                    if incomplete: self.assertIn('SPEAR · WAITING', html)
