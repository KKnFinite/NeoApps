"""Concurrent read-only resource projections in a disposable local schema."""
import unittest
from app.extensions import db
from app.models import NeoScorpionFuelWorkState, NeoScorpionFuelingEvent, NeoScorpionSortTruck
from app.services import neoscorpion as fuel
from app.services.neoscorpion_spear import _current_resource_owners
from tests import test_neoscorpion_fueler_handoff_postgres as pg


@unittest.skipUnless(pg.fixtures.URI, 'Requires disposable local PostgreSQL')
class NeoScorpionBandwidthPostgresTest(unittest.TestCase):
    setUp=pg.FuelerHandoffPostgresTest.setUp
    drop_schema=pg.FuelerHandoffPostgresTest.drop_schema
    prepare=pg.FuelerHandoffPostgresTest.prepare
    save=pg.FuelerHandoffPostgresTest.save
    off=pg.FuelerHandoffPostgresTest.off
    final_data=pg.FuelerHandoffPostgresTest.final_data
    concurrent=pg.FuelerHandoffPostgresTest.concurrent
    replacement=pg.FuelerHandoffPostgresTest.replacement

    def test_two_concurrent_spear_readers_use_committed_handoff_without_mutating_evidence(self):
        replacement=self.replacement('pg-resource-handoff')
        before=(self.work.id,self.work.manual_on_at_utc,self.work.physical_fueler_user_id,self.selection.current_gallons)
        truck_id,selection_id=self.truck.id,self.selection.id
        fuel.swap_assignment_fueler(self.gateway,self.dispatcher,self.assignment.id,replacement)
        db.session.commit()
        def read(index,gateway,fueler,dispatcher):
            context=fuel.fuel_dispatch_context(gateway,include_spear=False)
            owners,trucks=_current_resource_owners(context['rows'])
            return sorted(owners),sorted(trucks)
        results=self.concurrent(read)
        self.assertEqual(results,[([replacement],[truck_id])]*2)
        db.session.expire_all();work=db.session.get(NeoScorpionFuelWorkState,before[0])
        selection=db.session.get(NeoScorpionSortTruck,selection_id)
        self.assertEqual((work.id,work.manual_on_at_utc,work.physical_fueler_user_id,selection.current_gallons),before)
        self.assertEqual(NeoScorpionFuelWorkState.query.count(),1)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)
