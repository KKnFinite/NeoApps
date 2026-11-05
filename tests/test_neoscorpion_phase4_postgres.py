"""Equivalent read projections on a disposable loopback PostgreSQL schema."""
import os
import unittest
from uuid import uuid4
from unittest.mock import patch
from sqlalchemy import create_engine,text
from sqlalchemy.engine import make_url
from app import create_app
from tests import test_neoscorpion_phase4 as cases
from tests import test_neoscorpion_dispatch_planning as fixtures

URI=os.environ.get('NEOSTAFFING_TEST_POSTGRES_URL')


@unittest.skipUnless(URI,'Requires disposable loopback PostgreSQL')
class Phase4PostgresTest(cases.Phase4Test):
    def setUp(self):
        url=make_url(URI)
        if url.host not in {'localhost','127.0.0.1'} or not url.database.startswith('neostaffing_test_'):
            raise RuntimeError('Refusing non-disposable PostgreSQL')
        schema='scorpion_phase4_'+uuid4().hex
        admin=create_engine(URI,connect_args={'connect_timeout':5})
        with admin.begin() as connection:connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        def drop():
            with admin.begin() as connection:connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            admin.dispose()
        self.addCleanup(drop)
        def local(config):
            return create_app(type('Phase4PostgresConfig',(config,),dict(SQLALCHEMY_DATABASE_URI=URI,
                SQLALCHEMY_ENGINE_OPTIONS={'connect_args':{'connect_timeout':5,'options':
                    f'-csearch_path={schema} -clock_timeout=3000 -cstatement_timeout=10000'}})))
        with patch.object(fixtures,'create_app',local):super().setUp()
