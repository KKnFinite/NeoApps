"""Report a finalized physical event without mutable-tail fallback on PostgreSQL."""
from datetime import timedelta
import unittest
from app.extensions import db
from app.models import NeoScorpionTailFuelState
from app.services import neoscorpion as fuel
from tests import test_neoscorpion_segments_postgres as fixtures


@unittest.skipUnless(fixtures.URI, 'Requires disposable loopback PostgreSQL')
class ReportFobPostgresTest(unittest.TestCase):
    setUp = fixtures.PhysicalSegmentPostgresTest.setUp
    drop_schema = fixtures.PhysicalSegmentPostgresTest.drop_schema
    prepare = fixtures.PhysicalSegmentPostgresTest.prepare
    save = fixtures.PhysicalSegmentPostgresTest.save
    off = fixtures.PhysicalSegmentPostgresTest.off
    final_data = fixtures.PhysicalSegmentPostgresTest.final_data
    submit_current = fixtures.PhysicalSegmentPostgresTest.submit_current

    def test_finalized_event_readback_preserves_fob_and_inventory(self):
        self.save(self.start+timedelta(minutes=1), **self.final_data())
        self.off();self.submit_current()
        fuel.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id)
        db.session.commit()
        report = fuel.fuel_report_context(self.gateway)
        self.assertEqual(report['fuel_report_rows'][0]['fob_display'], '12.0')
        inventory = self.selection.current_gallons
        NeoScorpionTailFuelState.query.one().fob_lbs = 199_000
        db.session.commit();db.session.expire_all()
        self.assertEqual(fuel.fuel_report_context(self.gateway), report)
        self.assertEqual(self.selection.current_gallons, inventory)
