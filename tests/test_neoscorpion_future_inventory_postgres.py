"""Forecasts agree with the existing epoch-aware exactly-once posting."""
from datetime import timedelta
import unittest
from app.extensions import db
from app.models import NeoScorpionFuelingEvent
from app.services import neoscorpion as fuel
from app.services.neoscorpion_inventory import apply_reading
from tests import test_neoscorpion_segments_postgres as pg


@unittest.skipUnless(pg.URI,'Requires disposable local PostgreSQL')
class FutureInventoryPostgresTest(unittest.TestCase):
    setUp=pg.PhysicalSegmentPostgresTest.setUp
    drop_schema=pg.PhysicalSegmentPostgresTest.drop_schema
    prepare=pg.PhysicalSegmentPostgresTest.prepare
    save=pg.PhysicalSegmentPostgresTest.save
    off=pg.PhysicalSegmentPostgresTest.off
    final_data=pg.PhysicalSegmentPostgresTest.final_data

    def finish(self,reading_mode):
        self.save(self.start+timedelta(minutes=10),**self.final_data());self.off()
        apply_reading(self.gateway,self.operation,self.selection,self.dispatcher,8000,mode=reading_mode)
        db.session.commit()
        row=next(r for r in fuel.fuel_dispatch_context(self.gateway)['rows'] if r['mission'].id==self.mission.id)
        expected=8000 if reading_mode=='physical' else 5000
        self.assertEqual(row['projected_truck_gallons'],expected)
        self.assertEqual(self.selection.current_gallons,8000)
        baseline=fuel.fueler_context(self.gateway,self.fueler,assignment_id=self.assignment.id)['rows'][0]['edit_baseline']
        fuel.submit_fueler_assignment(self.gateway,self.fueler,self.assignment.id,expected=baseline);db.session.commit()
        for _ in range(2):
            fuel.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id);db.session.commit()
        db.session.refresh(self.selection)
        self.assertEqual(self.selection.current_gallons,expected)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)

    def test_physical_reading_already_covers_transfer(self):self.finish('physical')
    def test_baseline_correction_still_deducts_unposted_transfer_once(self):self.finish('baseline')
