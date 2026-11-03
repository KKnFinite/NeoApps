"""Read-only shared header binding; no fueling lifecycle changes."""
from html.parser import HTMLParser
from unittest.mock import patch
import unittest

from app.extensions import db
from app.services import neoscorpion as service
from tests import test_neoscorpion_direct_entry as fixtures


class HeaderParser(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.values = []
        self.capturing = False
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == 'span' and 'data-fuel-header-value' in dict(attrs):
            self.capturing = True

    def handle_data(self, data):
        if self.capturing: self.values.append(data)

    def handle_endtag(self, tag):
        if tag == 'span': self.capturing = False


class FuelHeaderTest(unittest.TestCase):
    setUp = fixtures.DirectEntryTest.setUp

    def test_normal_and_dispatch_open_use_operation_gateway_not_mission_origin(self):
        case = self
        fixtures.DirectEntryTest.prepare(case)
        self.mission.origin = 'SDF'
        db.session.commit()
        original = (self.mission.origin, self.mission.destination, self.mission.updated_at)
        for user, url in ((self.fueler, '/neoscorpion/fueler'),
                          (self.dispatcher, f'/neoscorpion/fuel-dispatch/fueler-data/{self.assignment.id}')):
            with self.subTest(url=url):
                fixtures.DirectEntryTest.login(self, user)
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                html = response.json['html'] if response.is_json else response.get_data(as_text=True)
                self.assertEqual(HeaderParser(html).values, [self.mission.assigned_tail_number,
                                 self.mission.flight_number,'RFD', self.mission.destination])
                self.assertIn('<dt>ETD</dt>',html)
                self.assertNotIn('<dt>Tail</dt>',html)
                self.assertNotIn('scorpion-fueler-flight',html)
                for label in ('Fueler','Parking','Truck','ON','Cycle','Aircraft','Required Fuel','Est. Gal'):
                    self.assertIn(f'<dt>{label}</dt>',html)
        self.assertEqual((self.mission.origin,self.mission.destination,self.mission.updated_at),original)

    def test_direct_entry_uses_same_header_preserving_its_distinct_grid(self):
        fixtures.DirectEntryTest.login(self, self.dispatcher)
        response = self.client.get(f'/neoscorpion/fuel-dispatch/missions/{self.mission.id}/direct-entry')
        self.assertEqual(response.status_code, 200)
        html = response.json['html']
        self.assertEqual(HeaderParser(html).values, [self.mission.assigned_tail_number,
                         self.mission.flight_number,'RFD',self.mission.destination])
        self.assertIn('data-screen-mode="dispatcher_direct_entry"',html)
        self.assertIn('<dt>ETD</dt>',html)
        self.assertNotIn('<dt>Fueler</dt>',html)
        self.assertNotIn('<dt>Truck</dt>',html)
        self.assertNotIn('<dt>Tail</dt>',html)

    def test_row_binding_follows_another_operation_gateway_code(self):
        # An isolated operation projection proves the display has no RFD fallback
        # and never reads the outbound mission's individual origin.
        self.operation.gateway_code = 'ONT'
        self.mission.origin = 'SDF'
        db.session.commit()
        with db.session.no_autoflush, patch.object(service, 'current_sort_operation', return_value=self.operation):
            row = next(row for row in service.fuel_dispatch_context(self.gateway)['rows']
                       if row['mission'].id == self.mission.id)
            self.assertEqual(row['gateway_code'], 'ONT')
            self.assertEqual(row['mission'].origin, 'SDF')
        db.session.rollback()
