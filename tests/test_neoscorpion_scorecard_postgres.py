"""Scorecard reads exact committed physical events without transaction mutations."""
import unittest
from sqlalchemy import text
from app.extensions import db
from app.models import NeoScorpionFuelingEvent
from app.services.neoscorpion_spear_scorecard import report_context
from tests import test_neoscorpion_segments_postgres as pg
from tests import test_neoscorpion_spear_scorecard as cases


@unittest.skipUnless(pg.URI,'Requires disposable local PostgreSQL')
class ScorecardPostgresTest(unittest.TestCase):
    setUp=pg.PhysicalSegmentPostgresTest.setUp
    drop_schema=pg.PhysicalSegmentPostgresTest.drop_schema
    prepare=pg.PhysicalSegmentPostgresTest.prepare
    save=pg.PhysicalSegmentPostgresTest.save
    off=pg.PhysicalSegmentPostgresTest.off
    final_data=pg.PhysicalSegmentPostgresTest.final_data
    completed=cases.ScorecardIntegrationTest.completed
    _exercise_returning_tail=pg.PhysicalSegmentPostgresTest._exercise_returning_tail

    def test_committed_snapshot_under_database_read_only_transaction(self):
        e=self.completed();event_id=e.id;work_id=e.fuel_work_state_id
        gateway,operation=self.gateway,self.operation
        db.session.commit()
        db.session.execute(text('SET TRANSACTION READ ONLY'))
        for _ in range(2):
            r=report_context(gateway,operation)
            self.assertEqual([(i['event'].id,i['event'].fuel_work_state_id) for i in r['off']],[(event_id,work_id)])
        self.assertFalse(db.session.new);self.assertFalse(db.session.dirty)
        db.session.rollback()
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)

    def test_a_b_a_three_visits_remain_independent_and_read_only(self):
        self._exercise_returning_tail()
        before=[(e.id,e.fuel_work_state_id,e.tail_number,e.transfer_fuel_gallons)
                for e in NeoScorpionFuelingEvent.query.order_by(NeoScorpionFuelingEvent.id)]
        r=report_context(self.gateway,self.operation)
        self.assertEqual(len(r['off']),3)
        self.assertEqual(len({i['event'].fuel_work_state_id for i in r['off']}),3)
        self.assertEqual(before,[(e.id,e.fuel_work_state_id,e.tail_number,e.transfer_fuel_gallons)
                for e in NeoScorpionFuelingEvent.query.order_by(NeoScorpionFuelingEvent.id)])
        self.assertEqual(self.selection.current_gallons,4000)
