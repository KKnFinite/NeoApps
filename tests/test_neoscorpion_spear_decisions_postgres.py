"""The decision contract and durable retries against an isolated PostgreSQL schema."""
import os
import unittest
from uuid import uuid4
from unittest.mock import patch
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from app import create_app
from tests import test_neoscorpion_spear_decisions as cases
from tests import test_neoscorpion_spear_learning_capture as fixtures

URI = os.environ.get('NEOSTAFFING_TEST_POSTGRES_URL')


@unittest.skipUnless(URI, 'Requires disposable local PostgreSQL')
class SpearDecisionPostgresTest(cases.SpearDecisionTest):
    def setUp(self):
        url = make_url(URI)
        if url.host not in {'localhost','127.0.0.1'} or not url.database.startswith('neostaffing_test_'):
            raise RuntimeError('Refusing non-disposable PostgreSQL')
        schema = 'spear_decisions_' + uuid4().hex
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
