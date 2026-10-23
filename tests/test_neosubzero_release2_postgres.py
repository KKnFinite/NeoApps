"""Release 2 on disposable PostgreSQL: same contracts plus real serialization."""
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from sqlalchemy import text
from tests import test_neosubzero_ucc_postgres as pg
from tests import test_neosubzero_release2 as release2
from app.extensions import db
from app.models import SortDateOperation,SortDateMission,NeoSubZeroDepartureDeiceEvent,NeoSubZeroSprayRecord
from app.services.live_collaboration import entity_version
from app.services.neosubzero_participation import change_participation
from app.services.neosubzero_departure_deice import neosubzero_fluid_settings
from app.services.neosubzero_evidence import NeoSubZeroEvidenceError
from app.services.neosubzero_schema import ensure_neosubzero_pretreat_table
from app.services.neosubzero_release2_schema import verify_release2_contract,NULLABLE
from tests.test_neosubzero_release2_schema import NEW_COLUMNS,legacy_rows


@unittest.skipUnless(os.environ.get('NEOSTAFFING_TEST_POSTGRES_URL'),'Requires disposable loopback PostgreSQL')
class Release2PostgresTest(release2.Release2Test):
    drop_schema=pg.UccPostgresTest.drop_schema
    def setUp(self):
        pg.UccPostgresTest.setUp(self);self.f=self.fixture

    def test_old_populated_schema_upgrade_idempotent(self):
        f=self.f;original=legacy_rows(f)
        db.session.remove()
        with db.engine.begin() as connection:
            for table in ('neosubzero_audit_entries','neosubzero_operation_contexts'):connection.execute(text('DROP TABLE '+table))
            for table,columns in NEW_COLUMNS.items():
                for column in columns:connection.execute(text(f'ALTER TABLE {table} DROP COLUMN {column} CASCADE'))
            connection.execute(text('ALTER TABLE neosubzero_departure_deice_events ADD CONSTRAINT uq_neosubzero_departure_deice_mission UNIQUE(sort_date_mission_id)'))
            for column in NULLABLE:connection.execute(text(f'ALTER TABLE neosubzero_spray_records ALTER COLUMN {column} SET NOT NULL'))
        with patch.dict(f.app.config,TESTING=False):
            ensure_neosubzero_pretreat_table(f.app);ensure_neosubzero_pretreat_table(f.app)
        verify_release2_contract(db.session.connection())
        event=db.session.get(NeoSubZeroDepartureDeiceEvent,original[0]);record=db.session.get(NeoSubZeroSprayRecord,original[1])
        self.assertEqual((event.id,record.id,record.created_at,event.updated_at),original)
        self.assertEqual(record.driver_name_snapshot,'Original Driver')
        self.assertEqual(record.gallons,12)
        self.assertIsNone(record.tail_number_snapshot);self.assertIsNone(record.official_at_utc)
        from app.models import NeoSubZeroAuditEntry
        audit=NeoSubZeroAuditEntry(sort_date_operation_id=event.sort_date_operation_id,action='verified')
        db.session.add(audit);db.session.commit();aid=audit.id
        for verb in ("UPDATE neosubzero_audit_entries SET action='changed'","DELETE FROM neosubzero_audit_entries"):
            with self.assertRaises(Exception):db.session.execute(text(verb+' WHERE id=:id'),dict(id=aid))
            db.session.rollback()

    def test_simultaneous_participation_has_one_winner_and_no_duplicate(self):
        f=self.f;event,_=self.flow(end='')
        eid,mid,oid=event.id,f.alpha.id,f.operation.id
        version=entity_version(event);db.session.remove()
        def worker(index):
            with f.app.app_context():
                try:
                    operation=SortDateOperation.query.filter_by(id=oid).with_for_update().one()
                    current=db.session.get(NeoSubZeroDepartureDeiceEvent,eid)
                    if entity_version(current)!=version:raise NeoSubZeroEvidenceError('Stale')
                    mission=db.session.get(SortDateMission,mid)
                    change_participation(operation,mission,current,'departure','add_truck',dict(pass_number=1,truck_number='T18'),
                        neosubzero_fluid_settings(current.operation.gateway),actor_id=None)
                    db.session.commit();return 'saved'
                except NeoSubZeroEvidenceError:
                    db.session.rollback();return 'conflict'
                finally:db.session.remove()
        with ThreadPoolExecutor(max_workers=2) as executor:results=list(executor.map(worker,range(2)))
        self.assertCountEqual(results,['saved','conflict'])
        self.assertEqual(NeoSubZeroSprayRecord.query.count(),2)
