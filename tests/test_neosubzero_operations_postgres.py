"""Additive preference migration and transaction checks, loopback UUID schemas only."""
import os
import unittest
from sqlalchemy import inspect, text
from unittest.mock import patch
from app.extensions import db
from app.models import User, NeoSubZeroUserPreference, SortDateOperation
from app.services.neosubzero_preferences import set_neosubzero_operations_preferences, neosubzero_operations_preferences
from app.services.neosubzero_schema import ensure_neosubzero_pretreat_table, verify_neosubzero_schema_contract
from app.services.schema_sync import sync_database_schema
from tests import test_neosubzero_ucc_postgres as fixtures


@unittest.skipUnless(os.environ.get('NEOSTAFFING_TEST_POSTGRES_URL'), 'Requires disposable loopback PostgreSQL')
class OperationsPostgresTest(unittest.TestCase):
    setUp=fixtures.UccPostgresTest.setUp
    drop_schema=fixtures.UccPostgresTest.drop_schema
    race=fixtures.UccPostgresTest.race

    def test_upgrade_preserves_existing_weather_preferences_and_repairs_canonical_contract(self):
        f=self.fixture;user=f._user('pg-ops-admin','grandmaster')
        preference=NeoSubZeroUserPreference(user_id=user.id,weather_animations_enabled=False,frost_risk_explanations_enabled=False)
        db.session.add(preference);db.session.commit();pid=preference.id
        db.session.remove()
        with db.engine.begin() as connection:
            for column in ('operations_mode','operations_layout','operations_ramp','operations_sort_id'):
                connection.execute(text('ALTER TABLE neosubzero_user_preferences DROP COLUMN '+column))
            with self.assertRaisesRegex(RuntimeError,'operations_'):
                verify_neosubzero_schema_contract(connection)
        with patch.dict(f.app.config,{'TESTING':False}):
            sync_database_schema(f.app)
        verify_neosubzero_schema_contract(db.session.connection())
        db.session.commit()
        with db.engine.begin() as connection:
            connection.execute(text('ALTER TABLE neosubzero_user_preferences DROP COLUMN operations_sort_id'))
        with patch.dict(f.app.config,{'TESTING':False}):
            self.assertTrue(ensure_neosubzero_pretreat_table(f.app));self.assertTrue(ensure_neosubzero_pretreat_table(f.app))
        verify_neosubzero_schema_contract(db.session.connection())
        preference=db.session.get(NeoSubZeroUserPreference,pid)
        self.assertFalse(preference.weather_animations_enabled);self.assertFalse(preference.frost_risk_explanations_enabled)
        self.assertIsNone(preference.operations_mode)
        self.assertTrue({'operations_mode','operations_layout','operations_ramp','operations_sort_id'} <= {c['name'] for c in inspect(db.engine).get_columns('neosubzero_user_preferences')})

    def test_concurrent_independent_preference_changes_do_not_overwrite_each_other(self):
        f=self.fixture;user=f._user('pg-ops-race','grandmaster');uid=user.id
        result=self.race(lambda index,op: set_neosubzero_operations_preferences(db.session.get(User,uid),op,({'mode':'pretreat'},{'layout':'visual','ramp':'Echo'})[index]))
        self.assertEqual(result,['saved','saved']);db.session.expire_all()
        self.assertEqual(neosubzero_operations_preferences(db.session.get(User,uid),db.session.get(SortDateOperation,self.ids[1])),dict(mode='pretreat',layout='visual',ramp='Echo'))
        self.assertEqual(NeoSubZeroUserPreference.query.filter_by(user_id=uid).count(),1)
