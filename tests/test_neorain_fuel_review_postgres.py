"""Publication tracking shares the original commit/rollback on real PostgreSQL."""
import os
import unittest
from uuid import uuid4
from unittest.mock import patch
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from app import create_app
from app.extensions import db
from app.models import NeoRainFuelPublication
from app.services.neorain_fuel_authority import acknowledge_fuel_review
from tests import test_neorain_fuel_authority as fixtures
from tests import test_neorain_fuel_review_regression as regression

URI=os.environ.get('NEOSTAFFING_TEST_POSTGRES_URL')


@unittest.skipUnless(URI,'Requires disposable local PostgreSQL')
class FuelReviewPostgresTest(unittest.TestCase):
    publish=regression.FuelReviewRegressionTest.publish
    value=regression.FuelReviewRegressionTest.value
    pending=regression.FuelReviewRegressionTest.pending

    def setUp(self):
        url=make_url(URI)
        if url.host not in {'localhost','127.0.0.1'} or not url.database.startswith('neostaffing_test_'):
            raise RuntimeError('Refusing non-disposable PostgreSQL')
        self.schema='rain_fuel_'+uuid4().hex
        self.engine=create_engine(URI)
        with self.engine.begin() as connection:connection.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        self.addCleanup(self.drop_schema)
        def isolated(config):
            config=type('PostgresFuelReviewConfig',(config,),dict(SQLALCHEMY_DATABASE_URI=URI,
                SQLALCHEMY_ENGINE_OPTIONS={'connect_args':{'options':f'-csearch_path={self.schema}'}}))
            return create_app(config)
        with patch.object(fixtures,'create_app',isolated):regression.FuelReviewRegressionTest.setUp(self)
        self.addCleanup(regression.FuelReviewRegressionTest.tearDown,self)

    def drop_schema(self):
        with self.engine.begin() as connection:connection.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
        self.engine.dispose()

    def test_initial_transaction_and_value_only_generations_with_exact_acknowledgements(self):
        regression.FuelReviewRegressionTest.test_first_completion_with_final_measurements_in_same_flush_and_transaction(self)
        revision=self.value()['revision'];acknowledge_fuel_review(self.operation,self.mission.id,revision,self.user);db.session.commit()
        self.assertFalse(self.pending())
        self.event.center_fuel_lbs+=1;db.session.commit();self.assertTrue(self.pending())
        with self.assertRaisesRegex(ValueError,'changed'):acknowledge_fuel_review(self.operation,self.mission.id,revision,self.user)

    def test_additive_schema_legacy_baseline_and_rollback(self):
        self.publish();NeoRainFuelPublication.__table__.drop(db.engine)
        for _ in range(2):db.create_all()
        self.assertFalse(self.pending())
        self.event.neo_fuel_lbs=46000;db.session.flush();self.assertTrue(self.pending());db.session.rollback()
        self.assertFalse(self.pending());self.assertEqual(self.event.neo_fuel_lbs,45000)
        self.event.neo_fuel_lbs=46100;db.session.commit();self.assertTrue(self.pending())
        pub=NeoRainFuelPublication.query.one();self.assertEqual(pub.baseline_neo_fuel_lbs,45000)
