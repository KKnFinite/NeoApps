"""Disposable PostgreSQL locking and nullable additive submission bootstrap."""
import unittest
from datetime import timedelta
from unittest.mock import patch
from sqlalchemy import inspect,text
from app.extensions import db
from app.models import NeoScorpionFuelingEvent,NeoScorpionSortTruck,NeoScorpionFuelWorkState
from app.services import neoscorpion as service
from app.services.neoscorpion_schema import ensure_neoscorpion_production_schema
from tests import test_neoscorpion_segments_postgres as pg
from tests import test_neoscorpion_fueler_submission as cases

@unittest.skipUnless(pg.URI,'Requires disposable local PostgreSQL')
class SubmissionPostgresTest(unittest.TestCase):
    setUp=pg.PhysicalSegmentPostgresTest.setUp
    drop_schema=pg.PhysicalSegmentPostgresTest.drop_schema
    prepare=pg.PhysicalSegmentPostgresTest.prepare
    save=pg.PhysicalSegmentPostgresTest.save
    off=pg.PhysicalSegmentPostgresTest.off
    final_data=pg.PhysicalSegmentPostgresTest.final_data
    concurrent=pg.PhysicalSegmentPostgresTest.concurrent
    row=cases.SubmissionTest.row
    baseline=cases.SubmissionTest.baseline
    submit=cases.SubmissionTest.submit
    test_postgres_off_submission_and_exact_once_inventory=cases.SubmissionTest.test_off_without_actual_and_submission_required_before_dispatcher_complete
    test_postgres_required_corrections_and_resubmission=cases.SubmissionTest.test_required_correction_invalidates_notes_preserve_and_resubmit_is_safe
    def test_concurrent_submission_rejects_stale_version_and_no_fuel_event(self):
        self.save(self.start+timedelta(minutes=10),**self.final_data());self.off()
        expected=self.baseline();aid=self.assignment.id
        results=self.concurrent(lambda i,g,f,d:service.submit_fueler_assignment(g,f,aid,expected=expected).changed)
        self.assertEqual(results.count(True),1);self.assertEqual(results.count('conflict'),1)
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),0)
    def test_additive_repeat_bootstrap_does_not_fabricate_notes_or_submission(self):
        original=(self.work.id,self.work.manual_on_at_utc,self.work.tail_number)
        db.session.commit()
        for name in ('notes','fueler_submitted_at_utc','fueler_submitted_by_user_id','fueler_submission_evidence','fueler_submission_version'):
            db.session.execute(text('ALTER TABLE neoscorpion_fuel_work_states DROP COLUMN '+name))
        db.session.commit()
        for _ in range(2):
            with patch.dict(self.fixture.app.config,{'TESTING':False}):ensure_neoscorpion_production_schema(self.fixture.app)
        db.session.expire_all();work=db.session.get(NeoScorpionFuelWorkState,original[0])
        self.assertEqual((work.id,work.manual_on_at_utc,work.tail_number),original)
        self.assertIsNone(work.notes);self.assertIsNone(work.fueler_submitted_at_utc)
        names={c['name'] for c in inspect(db.engine).get_columns('neoscorpion_fuel_work_states')}
        self.assertTrue({'notes','fueler_submission_evidence','fueler_submitted_by_user_id'}<=names)
