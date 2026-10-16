"""The decision contract and durable retries against an isolated PostgreSQL schema."""
import os
import unittest
from uuid import uuid4
from unittest.mock import patch
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from app import create_app
from tests import test_neoscorpion_spear_replanning as cases
from tests import test_neoscorpion_spear_learning_capture as fixtures

URI = os.environ.get('NEOSTAFFING_TEST_POSTGRES_URL')


@unittest.skipUnless(URI, 'Requires disposable local PostgreSQL')
class SpearReplanningPostgresTest(cases.ReplanningTest):
    def setUp(self):
        url = make_url(URI)
        if url.host not in {'localhost','127.0.0.1'} or not url.database.startswith('neostaffing_test_'):
            raise RuntimeError('Refusing non-disposable PostgreSQL')
        schema = 'spear_replanning_' + uuid4().hex
        engine = create_engine(URI)
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        def drop():
            with engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            engine.dispose()
        self.addCleanup(drop)
        def app_for_schema(config):
            config = type('DecisionPostgresConfig', (config,), {'SQLALCHEMY_DATABASE_URI':URI,
                'SQLALCHEMY_ENGINE_OPTIONS':{'connect_args':{'options':f'-csearch_path={schema}'}}})
            return create_app(config)
        with patch.object(fixtures, 'create_app', app_for_schema):
            super().setUp()

    def test_additive_settings_bootstrap_twice_preserves_defaults_and_assignments(self):
        from sqlalchemy import inspect
        from app.extensions import db
        from app.models import NeoScorpionSettings
        from app.services.neoscorpion_schema import ensure_neoscorpion_production_schema
        before=(self.fixture.assignment.id,self.fixture.assignment.assigned_truck_id)
        # This legacy completed-outcome fixture predates visit IDs. Make its
        # existing single visit explicit; never weaken production bootstrap.
        self.fixture.work.visit_number=1
        db.session.commit()
        for name in ('spear_whole_sort_mode','spear_top_off_roundtrip_minutes','spear_use_learned_top_off'):
            db.session.execute(text('ALTER TABLE neoscorpion_settings DROP COLUMN '+name))
        for name in ('spear_predicted_return_at_utc','spear_roundtrip_policy'):
            db.session.execute(text('ALTER TABLE neoscorpion_top_off_events DROP COLUMN '+name))
        db.session.commit()
        for _ in range(2):
            with patch.dict(self.fixture.app.config,{'TESTING':False}):
                ensure_neoscorpion_production_schema(self.fixture.app)
        db.session.expire_all()
        settings=NeoScorpionSettings.query.one()
        self.assertEqual(settings.spear_whole_sort_mode,'shadow')
        self.assertEqual(settings.spear_top_off_roundtrip_minutes,40)
        self.assertFalse(settings.spear_use_learned_top_off)
        self.assertEqual((self.fixture.assignment.id,self.fixture.assignment.assigned_truck_id),before)

    def test_sent_freezes_roundtrip_return_changes_inventory_once_and_failure_is_optional(self):
        from datetime import datetime,timedelta
        from app.extensions import db
        from app.models import NeoScorpionSortTruck,NeoScorpionFuelTruck,NeoScorpionSettings,NeoScorpionTopOffEvent
        from app.services import neoscorpion_assets as assets
        f=self.fixture
        truck=db.session.get(NeoScorpionFuelTruck,f.assignment.assigned_truck_id)
        selection=NeoScorpionSortTruck(sort_date_operation_id=f.operation.id,fuel_truck_id=truck.id,
            status='available',starting_gallons=9000,current_gallons=9000)
        db.session.add(selection);db.session.commit()
        sent_at=datetime.utcnow()
        assets.mark_nightly_truck_topping_off(f.operation,truck,changed_by_user=f.user,now_utc=sent_at)
        db.session.commit()
        sent=NeoScorpionTopOffEvent.query.filter_by(kind='sent').one()
        self.assertEqual(sent.spear_predicted_return_at_utc,sent_at+timedelta(minutes=40))
        settings=NeoScorpionSettings.query.one();settings.spear_top_off_roundtrip_minutes=90;db.session.commit()
        self.assertEqual(sent.spear_predicted_return_at_utc,sent_at+timedelta(minutes=40))
        assets.complete_nightly_truck_top_off(f.operation,truck,3000,changed_by_user=f.user,
            now_utc=sent_at+timedelta(minutes=50));db.session.commit()
        self.assertEqual(selection.current_gallons,3000)
        self.assertEqual(NeoScorpionTopOffEvent.query.filter_by(kind='returned').one().sent_event_id,sent.id)
        with patch('app.services.neoscorpion_spear_replanning.load_top_off_history',side_effect=ValueError('optional history failed')):
            assets.mark_nightly_truck_topping_off(f.operation,truck,changed_by_user=f.user,now_utc=sent_at+timedelta(minutes=60))
        db.session.commit()
        self.assertEqual(selection.status,'topping_off')
        self.assertEqual(NeoScorpionTopOffEvent.query.filter_by(kind='sent').count(),2)
