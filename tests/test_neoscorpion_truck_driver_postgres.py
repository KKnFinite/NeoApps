"""Driver claims and additive schema proof in a disposable loopback schema."""
import os
import unittest
from unittest.mock import patch
from sqlalchemy import inspect
from app.extensions import db
from app.models import User, NeoScorpionTruckDriver, NeoScorpionTruckDriverAudit
from app.services.neoscorpion_drivers import change_driver
from app.services.neoscorpion_schema import ensure_neoscorpion_production_schema
from tests import test_neoscorpion_segments_postgres as pg
from tests import test_neoscorpion_truck_driver as driver


@unittest.skipUnless(os.environ.get('NEOSTAFFING_TEST_POSTGRES_URL'),'Requires disposable local PostgreSQL')
class TruckDriverPostgresTest(unittest.TestCase):
    prepare=pg.PhysicalSegmentPostgresTest.prepare
    drop_schema=pg.PhysicalSegmentPostgresTest.drop_schema
    concurrent=pg.PhysicalSegmentPostgresTest.concurrent
    test_new_tables_bootstrap_idempotence_and_database_uniqueness=driver.TruckDriverTest.test_new_tables_bootstrap_idempotence_and_database_uniqueness
    claim=driver.TruckDriverTest.claim

    def setUp(self):
        pg.PhysicalSegmentPostgresTest.setUp(self)
        self.driver=self.fixture._login_user('pg-truck-driver-one','operator')
        self.other=self.fixture._login_user('pg-truck-driver-two','operator')
        db.session.commit()

    def test_concurrent_claims_have_one_winner_and_one_audit(self):
        actors=[self.driver.id,self.other.id]; truck=self.truck.id; operation_id=self.operation.id
        def claim(index,gateway,_fueler,_dispatcher):
            from app.models import SortDateOperation
            try:
                return change_driver(gateway,db.session.get(SortDateOperation,operation_id),
                    db.session.get(User,actors[index]),truck,actors[index],
                    expected_driver_id=None,expected_driver_truck_id=None)[0]
            except ValueError:
                db.session.rollback()
                return 'conflict'
        result=self.concurrent(claim)
        self.assertEqual(sorted(result,key=str),[True,'conflict'])
        self.assertEqual(NeoScorpionTruckDriver.query.count(),1)
        self.assertEqual(NeoScorpionTruckDriverAudit.query.count(),1)
        self.assertIn(NeoScorpionTruckDriver.query.one().driver_user_id,actors)

    def test_targeted_production_bootstrap_is_repeatable(self):
        self.claim()
        for _ in range(2):
            with patch.dict(self.fixture.app.config,{'TESTING':False}):
                ensure_neoscorpion_production_schema(self.fixture.app)
        self.assertEqual(NeoScorpionTruckDriver.query.one().driver_user_id,self.driver.id)
        self.assertEqual(NeoScorpionTruckDriverAudit.query.count(),1)
        names={c['name'] for c in inspect(db.engine).get_unique_constraints('neoscorpion_truck_drivers')}
        self.assertEqual(names,{'uq_scorpion_driver_sort_user','uq_scorpion_driver_sort_truck'})
