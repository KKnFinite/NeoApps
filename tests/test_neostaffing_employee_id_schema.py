"""Opt-in integrity proof on disposable PostgreSQL, never a production URL."""
from concurrent.futures import ThreadPoolExecutor
import os
from threading import Barrier
import unittest
from unittest.mock import patch
from uuid import uuid4

from flask import g
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError, OperationalError

from app import create_app
from app.extensions import db
from app.models import StaffingPerson
from app.services import neostaffing as staffing
from app.services.neostaffing_employee_id_schema import (
    INDEX_NAME, EmployeeIdMigrationBlocked, audit_employee_ids,
    normalization_body, sync_staffing_employee_id_schema,
)
from app.services.operator_errors import safe_mutation_error
from tests import test_neostaffing_bulk_change as base
from tests import test_neostaffing_bulk_change_cost as cost_fixture


URI = os.environ.get('NEOSTAFFING_TEST_POSTGRES_URL')


@unittest.skipUnless(URI, 'Requires disposable NEOSTAFFING_TEST_POSTGRES_URL')
class EmployeeIdPostgresTest(unittest.TestCase):
    def setUp(self):
        url = make_url(URI)
        if (url.get_backend_name() != 'postgresql'
                or url.host not in ('localhost', '127.0.0.1')
                or not url.database.startswith('neostaffing_test_')):
            raise ValueError('Only loopback neostaffing_test_* databases are allowed')
        self.schema = 'employee_id_' + uuid4().hex
        self.admin = create_engine(URI)
        with self.admin.begin() as connection:
            connection.execute(text('CREATE SCHEMA ' + self.schema))

        def pg_app(config):
            config.SQLALCHEMY_DATABASE_URI = URI
            config.SQLALCHEMY_ENGINE_OPTIONS = {'connect_args': {'options':
                '-csearch_path=' + self.schema + ' -clock_timeout=10000 -cstatement_timeout=30000'}}
            return create_app(config, auto_bootstrap=False)

        self.fixture = cost_fixture.BulkChangeCostTest('test_profile_small_and_large')
        with patch.object(base, 'create_app', side_effect=pg_app):
            self.fixture.setUp()
        self.ids = list(self.fixture.employee_ids)
        self.engine = db.engine

    def tearDown(self):
        db.session.remove()
        self.engine.dispose()
        self.fixture.context.pop()
        with self.admin.begin() as connection:
            connection.execute(text('DROP SCHEMA ' + self.schema + ' CASCADE'))
        self.admin.dispose()

    def upgrade(self):
        sync_staffing_employee_id_schema()
        db.session.commit()

    def set_ids(self, *values):
        for pid, value in zip(self.ids, values):
            db.session.execute(text('UPDATE staffing_people SET employee_id=:value WHERE id=:id'),
                               {'value': value, 'id': pid})
        db.session.commit()

    def saved_ids(self):
        db.session.remove()
        return db.session.execute(text('SELECT id, employee_id FROM staffing_people ORDER BY id')).all()

    def test_existing_fixture_audits_and_upgrades_idempotently_without_data_changes(self):
        self.assertEqual(audit_employee_ids(db.session.connection()), 119)
        before = self.saved_ids()
        self.upgrade()
        self.upgrade()
        self.assertEqual(self.saved_ids(), before)
        self.assertEqual(db.session.execute(text(
            'SELECT count(*) FROM pg_indexes WHERE schemaname=:schema AND indexname=:name'
        ), {'schema': self.schema, 'name': INDEX_NAME}).scalar(), 1)

    def test_collision_audit_blocks_before_schema_creation_and_preserves_records(self):
        self.set_ids('ABC123', ' abc123\t', '1017853', ' 1017853 ')
        before = self.saved_ids()
        with self.assertRaises(EmployeeIdMigrationBlocked) as caught:
            self.upgrade()
        message = str(caught.exception)
        self.assertIn('ABC123', message)
        self.assertIn('1017853', message)
        for pid in self.ids[:4]:
            self.assertIn(str(pid), message)
        db.session.rollback()
        self.assertEqual(self.saved_ids(), before)
        self.assertIsNone(db.session.execute(text('SELECT to_regclass(:name)'), {'name': INDEX_NAME}).scalar())
        self.assertIsNone(db.session.execute(text("SELECT to_regprocedure('staffing_employee_id_key(text)')")).scalar())

    def test_blank_legacy_data_blocks_for_review_and_null_remains_prohibited(self):
        self.set_ids(' \t')
        with self.assertRaisesRegex(EmployeeIdMigrationBlocked, 'NULL/blank'):
            self.upgrade()
        db.session.rollback()
        with self.assertRaises(IntegrityError) as caught:
            self.set_ids(None)
        self.assertEqual(caught.exception.orig.pgcode, '23502')
        db.session.rollback()
        for value in (None, '', ' \t\n', '\u00a0'):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'Employee ID is required'):
                staffing._person_values({'employee_id': value})

    def test_case_space_unicode_duplicates_rejected_and_distinct_text_preserved(self):
        self.upgrade()
        for original, duplicate in (
                ('ABC123', 'abc123'), ('1017853', ' 1017853 '),
                ('CASE', '\tcase\n'), ('UNICODE', '\u00a0unicode\u2003'),
                ('İD', 'i\u0307d'), ('AΣ', 'aς'), ('\ua7d0ID', '\ua7d1id')):
            with self.subTest(original=original):
                self.set_ids(original)
                with self.assertRaises(IntegrityError) as caught:
                    self.set_ids(original, duplicate)
                self.assertEqual(caught.exception.orig.pgcode, '23505')
                self.assertEqual(caught.exception.orig.diag.constraint_name, INDEX_NAME)
                self.assertEqual(safe_mutation_error(caught.exception, 'save'), 'Employee ID already exists.')
                db.session.rollback()
        self.set_ids(' Mixed-Case ', 'Distinct-Case')
        actual = dict(self.saved_ids())
        self.assertEqual(actual[self.ids[0]], ' Mixed-Case ')
        self.assertEqual(actual[self.ids[1]], 'Distinct-Case')

    def test_exact_duplicate_retained_index_also_has_usable_error(self):
        self.upgrade()
        self.set_ids('EXACT-ID')
        with self.assertRaises(IntegrityError) as caught:
            self.set_ids('EXACT-ID', 'EXACT-ID')
        self.assertEqual(safe_mutation_error(caught.exception, 'save'), 'Employee ID already exists.')
        db.session.rollback()
        self.set_ids('exact-id')  # Same employee can intentionally change display casing.
        self.assertEqual(dict(self.saved_ids())[self.ids[0]], 'exact-id')

    def test_normalization_matches_python_unicode_scalar_corpus_and_context(self):
        self.upgrade()
        # Whole Unicode scalar corpus, not just ASCII or this fixture's IDs.
        scalars = ''.join(chr(i) for i in range(1, 0x110000) if not 0xD800 <= i <= 0xDFFF)
        samples = [scalars[i:i+1024] for i in range(0, len(scalars), 1024)]
        whitespace = ''.join(char for char in scalars if char.isspace())
        samples += [whitespace + 'AbC' + whitespace, '', whitespace]
        for prefix in ('', 'A', 'Σ', 'İ', '\U00010570', '\u0345'):
            for separator in ('', "'", ':', '\u0345', '\u200d', '\u00b7', '\u02b0', ' '):
                for suffix in ('', 'A', 'Σ', '\U00010570', '\u0345'):
                    samples.append(prefix + separator + 'Σ' + separator + suffix)
        for offset in range(0, len(samples), 64):
            rows = db.session.execute(text(
                'SELECT value, staffing_employee_id_key(value) FROM unnest(CAST(:samples AS text[])) AS value'
            ), {'samples': samples[offset:offset+64]}).all()
            for value, normalized in rows:
                self.assertEqual(normalized, value.strip().lower(), ascii(value))
        self.assertIsNone(db.session.execute(text('SELECT staffing_employee_id_key(NULL)')).scalar())

    def test_competing_transactions_cannot_commit_normalized_duplicates(self):
        self.upgrade()
        db.session.remove()
        for insert, first, second in (
                (False, 'NEW-ID', 'new-id'), (False, 'NEXT-ID', ' \tNEXT-ID\n'),
                (True, 'CREATE-ID', 'create-id'), (True, 'INSERT-ID', ' \tINSERT-ID\n')):
            barrier = Barrier(2)

            def update(pid, value):
                try:
                    with self.engine.begin() as connection:
                        barrier.wait(timeout=10)
                        statement = ('INSERT INTO staffing_people '
                            '(employee_id, first_name, last_name, seniority_date, classification, '
                            'employee_status, active, created_at, updated_at, shift_flow_version) '
                            'SELECT :value, first_name, last_name, seniority_date, classification, '
                            'employee_status, active, created_at, updated_at, 0 '
                            'FROM staffing_people WHERE id=:id') if insert else (
                            'UPDATE staffing_people SET employee_id=:value WHERE id=:id')
                        connection.execute(text(statement), {'id': pid, 'value': value})
                    return 'committed'
                except IntegrityError as error:
                    self.assertEqual(error.orig.pgcode, '23505')
                    self.assertEqual(error.orig.diag.constraint_name, INDEX_NAME)
                    return 'conflict'

            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(update, pid, value) for pid, value in zip(self.ids, (first, second))]
                self.assertCountEqual([future.result(timeout=15) for future in futures], ['committed', 'conflict'])
            self.assertEqual(sum(value.strip().lower() == first.strip().lower()
                                 for _, value in self.saved_ids()), 1)
            db.session.remove()

    def test_upgrade_holds_writer_lock_until_audited_index_is_committed(self):
        self.set_ids('LOCKED-ID')
        sync_staffing_employee_id_schema()  # Intentionally not committed yet.

        def competing_write():
            with self.engine.begin() as connection:
                connection.execute(text("SET LOCAL lock_timeout='200ms'"))
                connection.execute(text('UPDATE staffing_people SET employee_id=:value WHERE id=:id'),
                                   {'id': self.ids[1], 'value': 'locked-id'})

        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.assertRaises(OperationalError) as caught:
                pool.submit(competing_write).result(timeout=5)
        self.assertEqual(caught.exception.orig.pgcode, '55P03')
        db.session.commit()
        with self.assertRaises(IntegrityError) as caught:
            competing_write()
        self.assertEqual(caught.exception.orig.diag.constraint_name, INDEX_NAME)

    def test_changed_function_or_unexpected_index_is_not_silently_replaced(self):
        self.upgrade()
        db.session.execute(text('ALTER FUNCTION staffing_employee_id_key(text) VOLATILE'))
        db.session.commit()
        with self.assertRaisesRegex(EmployeeIdMigrationBlocked, 'definition changed'):
            self.upgrade()
        db.session.rollback()
        db.session.execute(text('ALTER FUNCTION staffing_employee_id_key(text) IMMUTABLE'))
        db.session.execute(text('DROP INDEX ' + INDEX_NAME))
        db.session.execute(text('CREATE INDEX ' + INDEX_NAME + ' ON staffing_people(employee_id)'))
        db.session.commit()
        with self.assertRaisesRegex(EmployeeIdMigrationBlocked, 'Unexpected Employee ID index'):
            self.upgrade()
        db.session.rollback()

    def test_real_create_and_edit_paths_return_duplicate_validation_not_sql(self):
        self.upgrade()
        self.set_ids('ABC123')
        fixture = self.fixture
        fixture._login(fixture.grandmaster_user)
        g.pop('_login_user', None)
        values = {'employee_id': 'abc123', 'first_name': 'Duplicate', 'last_name': 'Proof',
                  'seniority_date': '2026-01-01', 'classification': 'part_time',
                  'employee_status': 'active', 'active': '1', 'phone_number': ''}
        before = len(self.saved_ids())
        response = fixture.client.post('/neostaffing/app-management/people', data=values, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Employee ID already exists.', response.data)
        self.assertNotIn(b'UniqueViolation', response.data)
        self.assertEqual(len(self.saved_ids()), before)
        response = fixture.client.post(f'/neostaffing/app-management/people/{self.ids[1]}/update',
                                       data=values, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Employee ID already exists.', response.data)
        self.assertNotEqual(dict(self.saved_ids())[self.ids[1]], 'abc123')

    def test_bulk_change_budgets_and_locks_are_not_reworked(self):
        self.upgrade()
        self.fixture.test_profile_small_and_large()

    def test_empty_database_uses_real_schema_sync_and_repeated_sync_is_safe(self):
        from app.services.schema_sync import sync_database_schema
        db.session.remove()
        db.drop_all()
        db.create_all()
        for _ in range(2):
            sync_database_schema(self.fixture.app)
            db.session.commit()
        self.assertEqual(audit_employee_ids(db.session.connection()), 0)
        self.assertIsNotNone(db.session.execute(text('SELECT to_regclass(:name)'), {'name': INDEX_NAME}).scalar())


class EmployeeIdBootstrapContractTest(unittest.TestCase):
    def test_explicit_postgres_schema_sync_calls_upgrade(self):
        # No worker/request path installs this function/index. The actual schema
        # sync integration is also exercised by deployment bootstrap tests.
        import inspect
        from app.services.schema_sync import sync_database_schema
        self.assertIn('sync_staffing_employee_id_schema()', inspect.getsource(sync_database_schema))

    def test_normalization_codegen_is_deterministic(self):
        before = normalization_body()
        normalization_body.cache_clear()
        self.assertEqual(normalization_body(), before)
