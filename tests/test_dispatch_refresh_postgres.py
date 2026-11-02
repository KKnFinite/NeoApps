"""Fragment reads and concurrent canonical completion on disposable PostgreSQL."""
import os
import unittest
from unittest.mock import patch
from uuid import uuid4
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool
from app import create_app
from tests import test_dispatch_refresh_fragments as cases
from tests import test_neoscorpion_dispatch_planning as fixtures

URI=os.environ.get('NEOSTAFFING_TEST_POSTGRES_URL')


@unittest.skipUnless(URI,'Requires disposable loopback PostgreSQL')
class FragmentPostgresTest(cases.DispatchFragmentsTest):
    def setUp(self):
        url=make_url(URI)
        if url.host not in ('127.0.0.1','localhost') or not url.database.startswith('neostaffing_test_'):
            raise RuntimeError('Refusing non-disposable PostgreSQL')
        schema='refresh_'+uuid4().hex
        engine=create_engine(URI,poolclass=NullPool)
        with engine.begin() as connection:connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        def cleanup():
            with engine.begin() as connection:connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            engine.dispose()
        self.addCleanup(cleanup)
        def app(config):
            return create_app(type('RefreshPostgresConfig',(config,),dict(SQLALCHEMY_DATABASE_URI=URI,
                SQLALCHEMY_ENGINE_OPTIONS={'poolclass':NullPool,'connect_args':{'options':
                    f'-csearch_path={schema} -clock_timeout=5000 -cstatement_timeout=15000'}})))
        with patch.object(fixtures,'create_app',app):super().setUp()
