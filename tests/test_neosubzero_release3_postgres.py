"""Release 3 contracts in disposable UUID-scoped PostgreSQL schemas."""
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from app.extensions import db
from app.models import SortDateOperation,NeoSubZeroSprayRecord,NeoSubZeroArchiveDelivery
from app.services.live_collaboration import entity_version
from app.services.neosubzero_participation import participation_query
from app.services.neosubzero_spray import set_neosubzero_spray_gallons
from app.services.neosubzero_archive import reconcile,drain
from unittest.mock import patch
from tests import test_neosubzero_ucc_postgres as pg
from tests import test_neosubzero_release3 as r3


@unittest.skipUnless(os.environ.get('NEOSTAFFING_TEST_POSTGRES_URL'),'Requires disposable loopback PostgreSQL')
class Release3PostgresTest(r3.Release3Test):
    drop_schema=pg.UccPostgresTest.drop_schema
    def setUp(self):
        pg.UccPostgresTest.setUp(self)
        self.f=self.fixture

    def test_simultaneous_driver_corrections_one_winner(self):
        event,_=self.flow();record=participation_query(event).one()
        rid,oid,token=record.id,self.f.operation.id,entity_version(record)
        db.session.remove()
        def worker(index):
            with self.f.app.app_context():
                try:
                    op=SortDateOperation.query.filter_by(id=oid).with_for_update().one()
                    row=NeoSubZeroSprayRecord.query.filter_by(id=rid).with_for_update().one()
                    if entity_version(row)!=token:return 'conflict'
                    set_neosubzero_spray_gallons(op,row.mission,row.departure_deice_event,1,row.position_number,
                        str(12+index),record=row,correction_reason='Concurrent test')
                    db.session.commit();return 'saved'
                finally:db.session.remove()
        with ThreadPoolExecutor(max_workers=2) as pool:outcomes=list(pool.map(worker,range(2)))
        self.assertCountEqual(outcomes,['saved','conflict'])

    def test_two_archive_workers_deliver_one_revision(self):
        self.flow();gid=self.f.gateway.id
        reconcile(gid);db.session.remove()
        writes=[]
        def export(payload,**kwargs):writes.append(payload);return dict(key='fixture')
        def worker(index):
            with self.f.app.app_context():
                try:return drain(gid)
                finally:db.session.remove()
        with patch('app.services.neoscorpion_learning_vault.export_subzero_record',side_effect=export):
            with ThreadPoolExecutor(max_workers=2) as pool:counts=list(pool.map(worker,range(2)))
        self.assertEqual(sum(counts),1);self.assertEqual(len(writes),2)
        self.assertEqual(NeoSubZeroArchiveDelivery.query.count(),1)
