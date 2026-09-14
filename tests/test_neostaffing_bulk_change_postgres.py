"""Opt-in Bulk Change proof in isolated disposable loopback PostgreSQL schemas."""
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app import create_app
from app.extensions import db
from tests import test_neostaffing_bulk_change as base_fixture
from tests import test_neostaffing_bulk_change_cost as proof


URI = os.environ.get('NEOSTAFFING_TEST_POSTGRES_URL')


@unittest.skipUnless(URI, 'Requires disposable NEOSTAFFING_TEST_POSTGRES_URL')
class BulkChangePostgresTest(proof.BulkChangeCostTest):
    def setUp(self):
        url = make_url(URI)
        if (url.get_backend_name() != 'postgresql'
                or url.host not in ('localhost', '127.0.0.1')
                or not url.database.startswith('neostaffing_test_')):
            raise ValueError('Only loopback neostaffing_test_* databases are allowed')
        self.schema = 'bulk_' + uuid4().hex
        self.admin = create_engine(URI)
        with self.admin.begin() as connection:
            connection.execute(text('CREATE SCHEMA ' + self.schema))

        def pg_app(config):
            config.SQLALCHEMY_DATABASE_URI = URI
            config.SQLALCHEMY_ENGINE_OPTIONS = {'connect_args': {'options':
                '-csearch_path=' + self.schema + ' -clock_timeout=10000 -cstatement_timeout=30000'}}
            return create_app(config, auto_bootstrap=False)

        with patch.object(base_fixture, 'create_app', side_effect=pg_app):
            super().setUp()

    def tearDown(self):
        db.session.remove()
        db.engine.dispose()
        self.context.pop()
        with self.admin.begin() as connection:
            connection.execute(text('DROP SCHEMA ' + self.schema + ' CASCADE'))
        self.admin.dispose()
