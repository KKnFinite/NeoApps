"""Same N12 cases on disposable loopback PostgreSQL, each in its own schema."""
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from app import create_app
from tests import test_neostaffing_leadership_query as cases

URI = os.environ.get('NEOSTAFFING_TEST_POSTGRES_URL')


@unittest.skipUnless(URI, 'Requires isolated loopback PostgreSQL')
class LeadershipPostgresTest(cases.LeadershipQueryTest):
    def setUp(self):
        url = make_url(URI)
        if url.host not in ('127.0.0.1', 'localhost') or not url.database.startswith('neostaffing_test_'):
            raise RuntimeError('Refusing non-disposable PostgreSQL')
        schema = 'leadership_' + uuid4().hex
        engine = create_engine(URI, poolclass=NullPool)
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        def cleanup():
            with engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            engine.dispose()
        self.addCleanup(cleanup)
        def app(config):
            return create_app(type('LeadershipPostgresConfig', (config,), dict(
                SQLALCHEMY_DATABASE_URI=URI, SQLALCHEMY_ENGINE_OPTIONS={
                    'poolclass': NullPool, 'connect_args': {'options':
                        f'-csearch_path={schema} -clock_timeout=5000 -cstatement_timeout=15000'}})))
        with patch.object(cases, 'create_app', app):
            super().setUp()
