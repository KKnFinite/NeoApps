import sqlite3
import unittest
from unittest.mock import patch

from flask import Flask
from sqlalchemy import create_engine, text
from sqlalchemy.pool import QueuePool

from app import create_app
from app.config import configure_runtime_database_options
from app.extensions import db


class RuntimeDatabaseOptionsTest(unittest.TestCase):
    def test_postgres_runtime_engine_is_lazy_and_has_only_connection_defaults(self):
        app = Flask('runtime-engine-test')
        app.config.update(
            NEOAPPS_ENV='production',
            SQLALCHEMY_DATABASE_URI='postgresql+psycopg2://user@example.invalid/test',
        )
        with patch('psycopg2.connect', side_effect=AssertionError('Unexpected connection')) as connect:
            configure_runtime_database_options(app.config)
            db.init_app(app)
            with app.app_context():
                engine = db.engine
                self.assertTrue(engine.pool._pre_ping)
                self.assertEqual(app.config['SQLALCHEMY_ENGINE_OPTIONS'], {
                    'pool_pre_ping': True, 'connect_args': {'connect_timeout': 5},
                })
                # Exercise dialect argument merging without opening a socket.
                with patch.object(engine.dialect, 'connect', return_value=object()) as dialect_connect:
                    engine.pool._creator(None)
                self.assertEqual(dialect_connect.call_args.kwargs['connect_timeout'], 5)
                self.assertNotIn('options', dialect_connect.call_args.kwargs)
                engine.dispose()
            connect.assert_not_called()

    def test_sqlite_app_remains_valid(self):
        config = type('SQLiteConfig', (), {
            'TESTING': True, 'SECRET_KEY': 'test',
            'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
        })
        app = create_app(config)
        with app.app_context():
            with db.engine.connect() as connection:
                self.assertEqual(connection.scalar(text('SELECT 1')), 1)
            self.assertNotIn('connect_timeout', app.config.get('SQLALCHEMY_ENGINE_OPTIONS', {}).get('connect_args', {}))
            db.engine.dispose()

    def test_factory_resolves_postgres_options_after_test_config_without_connecting(self):
        config = type('PostgresConfig', (), {
            'TESTING': True, 'SECRET_KEY': 'test',
            'SQLALCHEMY_DATABASE_URI': 'postgresql://user@example.invalid/test',
        })
        # TESTING skips the existing targeted production schema ensures. This
        # checks the real factory wiring, not a claim that those ensures are lazy.
        with (
            patch('psycopg2.connect', side_effect=AssertionError('Unexpected connection')) as connect,
            patch('app.maybe_auto_bootstrap_database') as bootstrap,
        ):
            app = create_app(config)
            with app.app_context():
                self.assertTrue(db.engine.pool._pre_ping)
                self.assertEqual(app.config['SQLALCHEMY_ENGINE_OPTIONS']['connect_args'], {'connect_timeout': 5})
                db.engine.dispose()
            connect.assert_not_called()
            bootstrap.assert_not_called()

    def test_local_sqlite_options_are_not_modified(self):
        options = {'connect_args': {'timeout': 3}}
        config = {'SQLALCHEMY_DATABASE_URI': 'sqlite:///local.db',
                  'SQLALCHEMY_ENGINE_OPTIONS': options}
        configure_runtime_database_options(config)
        self.assertIs(config['SQLALCHEMY_ENGINE_OPTIONS'], options)
        self.assertEqual(options, {'connect_args': {'timeout': 3}})

    def test_explicit_bootstrap_options_are_preserved_not_mutated(self):
        original = {'pool_pre_ping': True, 'pool_timeout': 6, 'connect_args': {
            'connect_timeout': 6, 'options': '-c lock_timeout=4000ms -c statement_timeout=12000ms',
        }}
        config = {'SQLALCHEMY_DATABASE_URI': 'postgresql://user@example.invalid/test',
                  'SQLALCHEMY_ENGINE_OPTIONS': original}
        configure_runtime_database_options(config)
        self.assertEqual(config['SQLALCHEMY_ENGINE_OPTIONS'], original)
        self.assertIsNot(config['SQLALCHEMY_ENGINE_OPTIONS'], original)
        self.assertIsNot(config['SQLALCHEMY_ENGINE_OPTIONS']['connect_args'], original['connect_args'])

    def test_pre_ping_replaces_closed_pooled_connection_before_application_sql(self):
        # Real SQLAlchemy pool/disconnect detection, using SQLite's DBAPI only
        # as a deterministic local stand-in for a remotely closed connection.
        config = {'SQLALCHEMY_DATABASE_URI': 'postgresql://user@example.invalid/test'}
        configure_runtime_database_options(config)
        connections = []
        def creator():
            connection = sqlite3.connect(':memory:')
            connections.append(connection)
            return connection
        engine = create_engine('sqlite://', creator=creator, poolclass=QueuePool,
                               pool_pre_ping=config['SQLALCHEMY_ENGINE_OPTIONS']['pool_pre_ping'])
        try:
            self.assertEqual(connections, [])
            with engine.connect() as connection:
                self.assertEqual(connection.scalar(text('SELECT 1')), 1)
            self.assertEqual(len(connections), 1)
            connections[0].close()  # Simulate the idle socket being closed.
            self.assertEqual(len(connections), 1)  # No proactive reconnect.
            with engine.connect() as connection:
                self.assertEqual(len(connections), 2)
                self.assertEqual(connection.scalar(text('SELECT 42')), 42)
        finally:
            engine.dispose()
