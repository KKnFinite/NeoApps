"""The Dispatch column warning uses one inclusive raw-pound threshold."""
from datetime import datetime
from html.parser import HTMLParser
import unittest

from app.extensions import db
from app.models import NeoScorpionFuelingEvent, NeoScorpionSettings
from app.services import neoscorpion as fuel
from tests import test_neoscorpion_dispatch_checks_excess as fixtures


class Numbers(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.values = {}
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        for name in ('neo', 'required'):
            if f'data-dispatch-{name}-fuel' in attrs:
                self.values[name] = (tag, attrs)


class FuelComparisonMathTest(unittest.TestCase):
    def test_inclusive_boundary_in_both_directions(self):
        for delta in (-1000, -500, -499, 0, 499, 500, 1000):
            with self.subTest(delta=delta):
                self.assertEqual(fuel.dispatch_fuel_load_mismatch(30_000 + delta, 30_000), abs(delta) >= 500)
        self.assertFalse(fuel.dispatch_fuel_load_mismatch('30499.99', 30_000))
        self.assertTrue(fuel.dispatch_fuel_load_mismatch('30500.01', 30_000))

    def test_missing_invalid_and_zero_values(self):
        for neo, required in ((None, 30_000), (30_000, None), (-1, 30_000), (30_000, -1),
                              ('NaN', 30_000), (30_000, 'Infinity'), ('bad', 30_000), ('', 30_000)):
            with self.subTest(neo=neo, required=required):
                self.assertFalse(fuel.dispatch_fuel_load_mismatch(neo, required))
        self.assertTrue(fuel.dispatch_fuel_load_mismatch(0, 500))
        self.assertFalse(fuel.dispatch_fuel_load_mismatch(0, 499))


class FuelComparisonIntegrationTest(unittest.TestCase):
    setUp = fixtures.DispatchChecksExcessIntegrationTest.setUp

    def test_context_payload_and_editable_completed_markup_agree(self):
        settings = NeoScorpionSettings(gateway_id=self.gateway.id, neo_fuel_excess_alert_gallons=0)
        db.session.add(settings)
        for completed in (False, True):
            self.assignment.completed_at_utc = datetime.utcnow() if completed else None
            settings.neo_fuel_excess_alert_gallons = 999 if completed else 0
            for delta in (-1000, -500, -499, 499, 500, 1000, None):
                with self.subTest(completed=completed, delta=delta):
                    mismatch = delta is not None and abs(delta) >= 500
                    self.mission.planned_fuel_load = 28750 - delta if delta is not None else None
                    db.session.commit()
                    row = fuel.fuel_dispatch_context(self.gateway)['rows'][0]
                    payload = fuel.dispatch_neo_fuel_presentation(self.mission, self.assignment)
                    self.assertEqual(row['neo_fuel_mismatch'], mismatch)
                    self.assertEqual(payload['mismatch'], mismatch)
                    html = self.client.get('/neoscorpion/fuel-dispatch/live-panel').json['html']
                    numbers = Numbers(html).values
                    for name in ('neo', 'required'):
                        classes = numbers[name][1].get('class', '').split()
                        self.assertEqual('is-neo-fuel-mismatch' in classes, mismatch)
                    self.assertEqual(numbers['required'][0], 'span' if completed else 'input')
        self.assertEqual(NeoScorpionFuelingEvent.query.count(), 0)
        self.assertEqual({t.tank_code: t.actual_lbs for t in self.work.tank_states},
                         {'left': 15000, 'ctr': 0, 'right': 13750})

    def test_incomplete_actual_and_unconfirmed_apu_do_not_warn(self):
        self.mission.planned_fuel_load = 40000
        tank = self.work.tank_states[0]
        original = tank.actual_lbs
        for missing in ('actual', 'apu'):
            tank.actual_lbs = None if missing == 'actual' else original
            self.work.apu_running = None if missing == 'apu' else False
            db.session.commit()
            row = fuel.fuel_dispatch_context(self.gateway)['rows'][0]
            payload = fuel.dispatch_neo_fuel_presentation(self.mission, self.assignment)
            self.assertFalse(row['neo_fuel_available'])
            self.assertFalse(row['neo_fuel_mismatch'])
            self.assertFalse(payload['mismatch'])

    def test_autosave_returns_and_clears_the_paired_warning(self):
        for target, old, mismatch in (('28.3', '25.4', False), ('29.3', '28.3', True),
                                      ('28.8', '29.3', False), ('28.2', '28.8', True),
                                      ('28.3', '28.2', False)):
            response = self.client.post('/neoscorpion/fuel-dispatch/autosave', data={
                'mission_id': self.mission.id, 'field_name': 'required_fuel', 'value': target, 'expected_value': old,
            })
            self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
            self.assertEqual(response.json['neo_fuel']['mismatch'], mismatch)
            self.assertEqual(fuel.fuel_dispatch_context(self.gateway)['rows'][0]['neo_fuel_mismatch'], mismatch)

    def test_displayed_departure_load_takes_precedence_over_frozen_visit_target(self):
        self.work.visit_number = 1
        self.work.physical_sequence_number = 1
        self.work.physical_cycle_number = 1
        self.work.physical_cycle_type = 'fuel'
        self.work.physical_required_fuel_lbs = 25400
        self.work.target_frozen = True
        self.assignment.active_work_state_id = self.work.id
        for target, mismatch in ((29250, True), (29249, False), (28750, False), (28250, True)):
            self.mission.planned_fuel_load = target
            db.session.commit()
            row = fuel.fuel_dispatch_context(self.gateway)['rows'][0]
            payload = fuel.dispatch_neo_fuel_presentation(self.mission, self.assignment)
            self.assertEqual(row['required_fuel_lbs'], 25400)
            self.assertEqual(row['departure_required_fuel_lbs'], target)
            self.assertEqual(row['neo_fuel_mismatch'], mismatch)
            self.assertEqual(payload['mismatch'], mismatch)
        self.assertTrue(self.work.target_frozen)
        self.assertEqual(self.work.physical_required_fuel_lbs, 25400)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(), 0)
