"""Focused STOP version race and additive upgrade on isolated PostgreSQL."""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import TestCase,skipUnless
from unittest.mock import patch
from uuid import uuid4
from sqlalchemy import create_engine,text,inspect
from sqlalchemy.engine import make_url
from app import create_app
from app.extensions import db
from app.models import Gateway,NeoSektorSortState
from app.services import neosektor_live_counts as live
from tests import test_neosektor_stop_sign as scenarios
from tests import test_neosektor_routes as fixtures

URI=os.environ.get('NEOSTAFFING_TEST_POSTGRES_URL')


@skipUnless(URI,'Requires isolated loopback PostgreSQL')
class StopSignPostgresTest(TestCase):
    def setUp(self):
        url=make_url(URI)
        if url.host not in {'localhost','127.0.0.1'} or not url.database.startswith('neostaffing_test_'):
            raise RuntimeError('Refusing a non-synthetic PostgreSQL database')
        self.schema='sektor_stop_'+uuid4().hex
        self.engine=create_engine(URI)
        with self.engine.begin() as c:c.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        def app(config):
            return create_app(type('StopSignPostgresConfig',(config,),dict(SQLALCHEMY_DATABASE_URI=URI,
                SQLALCHEMY_ENGINE_OPTIONS={'connect_args':{'options':f'-csearch_path={self.schema} -clock_timeout=5000 -cstatement_timeout=15000'}})))
        with patch.object(fixtures,'create_app',app):scenarios.StopSignTest.setUp(self)

    def tearDown(self):
        self.fixture.tearDown();self.engine.dispose() # Preserve schema and other existing test data.

    post=scenarios.StopSignTest.post
    state=scenarios.StopSignTest.state
    command=scenarios.StopSignTest.command

    def test_concurrent_same_version_accepts_once_and_failed_action_rolls_back(self):
        command=self.command(True);gateway_id=self.gateway.id;ready=Barrier(2)
        def save(_):
            with self.fixture.app.app_context():
                gateway=db.session.get(Gateway,gateway_id)
                ready.wait(5)
                bundle=live.NeoSektorOperationalStateBundle.load(gateway,include_routing=True,for_update=True)
                try:
                    live.update_discharge_controls(bundle,command);db.session.commit();return 'accepted'
                except live.BallmatModeConflict:
                    db.session.rollback();return 'conflict'
        db.session.remove()
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(save,(0,1)))
        self.assertEqual(sorted(results),['accepted','conflict'])
        row=NeoSektorSortState.query.one();self.assertTrue(row.stop_sign);self.assertEqual(row.stop_sign_version,1)
        off=self.command(False)
        with self.assertRaisesRegex(ValueError,'Synthetic failure'):
            try:
                bundle=live.NeoSektorOperationalStateBundle.load(self.gateway,include_routing=True,for_update=True)
                live.update_discharge_controls(bundle,off)
                raise ValueError('Synthetic failure after mutation')
            except Exception:db.session.rollback();raise
        db.session.expire_all();self.assertTrue(row.stop_sign);self.assertEqual(row.stop_sign_version,1)

    def test_additive_upgrade_defaults_and_idempotence(self):
        from app.services.schema_sync import sync_database_schema
        row=NeoSektorSortState.query.one();before=(row.id,row.back_pickup_mask,row.cut_discharge,row.planned_total)
        db.session.remove()
        with db.engine.begin() as c:
            c.execute(text('ALTER TABLE neosektor_sort_states DROP COLUMN stop_sign'))
            c.execute(text('ALTER TABLE neosektor_sort_states DROP COLUMN stop_sign_version'))
        for _ in range(2):sync_database_schema(self.fixture.app);db.session.commit()
        row=NeoSektorSortState.query.one()
        self.assertEqual(before,(row.id,row.back_pickup_mask,row.cut_discharge,row.planned_total))
        self.assertFalse(row.stop_sign);self.assertEqual(row.stop_sign_version,0)
