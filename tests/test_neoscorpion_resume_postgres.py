"""Resume under actual PostgreSQL locks and immutable OFF guards."""
import unittest
from datetime import timedelta
from app.extensions import db
from app.models import NeoScorpionFuelWorkState,NeoScorpionFuelAuditEntry
from app.services import neoscorpion as fuel
from tests import test_neoscorpion_segments_postgres as pg
from tests.test_neoscorpion_resume import ResumeTest


@unittest.skipUnless(pg.URI,'Requires disposable local PostgreSQL')
class ResumePostgresTest(ResumeTest):
    setUp=pg.PhysicalSegmentPostgresTest.setUp
    drop_schema=pg.PhysicalSegmentPostgresTest.drop_schema
    concurrent=pg.PhysicalSegmentPostgresTest.concurrent
    prepare=pg.PhysicalSegmentPostgresTest.prepare

    def test_concurrent_resume_is_one_audit_and_one_visit(self):
        self.off();stamp=self.work.off_at_utc.isoformat();aid,wid,tid=self.assignment.id,self.work.id,self.truck.id
        now=self.start+timedelta(minutes=14)
        results=self.concurrent(lambda i,g,f,d:fuel.confirm_fueler_truck_on(g,f,aid,expected_cycle=1,
            expected_tail='N491UP',expected_truck_id=tid,expected_work_ref=wid,require_work_ref=True,
            resume_off_at=stamp,now_utc=now)[0])
        self.assertEqual(sorted(results),[False,True])
        self.assertEqual(NeoScorpionFuelWorkState.query.count(),1)
        self.assertEqual(NeoScorpionFuelAuditEntry.query.filter_by(field_name='resume_fueling').count(),1)

    def repair_sql(self):
        from pathlib import Path
        sql=Path('scripts/repair_neoscorpion_ups1476_duplicate.sql').read_text()
        self.dispatcher.role='grandmaster';db.session.commit()
        for name,value in [('a_id',self.assignment.id),('original_id',self.work.id),('duplicate_id',self.duplicate.id),('actor_id',self.dispatcher.id)]:
            import re
            sql=re.sub(rf'{name} integer := \d+;',f'{name} integer := {value};',sql)
        return sql

    def test_exact_approved_sql_preserves_raw_evidence_and_completes_once(self):
        from sqlalchemy import text
        from datetime import datetime
        from app.models import NeoScorpionFuelingEvent
        self.duplicate,off=self.duplicate_fixture();sql=self.repair_sql()
        raw_on=self.duplicate.manual_on_at_utc;on=self.work.manual_on_at_utc
        db.session.execute(text(sql));db.session.commit();db.session.expire_all()
        self.assertEqual(self.assignment.active_work_state_id,self.work.id)
        self.assertIsNone(self.work.off_at_utc);self.assertEqual(self.work.manual_on_at_utc,on)
        self.assertEqual(self.duplicate.manual_on_at_utc,raw_on);self.assertIsNone(self.duplicate.off_at_utc)
        self.assertEqual(len(self.row()['console_visits']),1)
        self.assertEqual(NeoScorpionFuelWorkState.query.count(),2)
        db.session.execute(text(sql));db.session.commit()
        self.assertEqual(NeoScorpionFuelAuditEntry.query.filter_by(field_name='duplicate_visit_reconciliation').count(),1)
        self.save(datetime.utcnow(),**self.final_data())
        fuel.mark_fueler_off(self.gateway,self.fueler,self.assignment.id,now_utc=datetime.utcnow()+timedelta(minutes=1));db.session.commit();self.submit()
        fuel.complete_fueled_assignment(self.gateway,self.dispatcher,self.assignment.id);db.session.commit()
        self.assertEqual(NeoScorpionFuelingEvent.query.count(),1)
        self.assertEqual(self.selection.current_gallons,7000)

    def test_exact_sql_aborts_if_actual_readings_appear(self):
        from sqlalchemy import text
        self.duplicate,off=self.duplicate_fixture();sql=self.repair_sql()
        self.duplicate.tank_states[0].actual_lbs=1000;db.session.commit()
        with self.assertRaisesRegex(Exception,'New fuel/submission/transfer/plan evidence'):
            db.session.execute(text(sql))
        db.session.rollback();self.assertEqual(self.assignment.active_work_state_id,self.duplicate.id)
        self.assertIsNone(self.duplicate.physical_finalized_at_utc)
        self.assertEqual(NeoScorpionFuelAuditEntry.query.filter_by(field_name='duplicate_visit_reconciliation').count(),0)
