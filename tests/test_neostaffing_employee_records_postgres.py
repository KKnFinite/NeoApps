"""Disposable loopback PostgreSQL proof for immutable records and competing writers."""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from app import create_app
from app.extensions import db
from app.models import User
from app.services import neostaffing_employee_records as records
from tests import test_neostaffing_employee_records as fixtures

URI = os.environ.get('NEOSTAFFING_TEST_POSTGRES_URL')


@skipUnless(URI, 'Requires disposable loopback NEOSTAFFING_TEST_POSTGRES_URL')
class EmployeeRecordPostgresTest(fixtures.EmployeeRecordTest):
    def setUp(self):
        url = make_url(URI)
        if url.get_backend_name() != 'postgresql' or url.host not in ('127.0.0.1','localhost') or not url.database.startswith('neostaffing_test_'):
            raise ValueError('Use a disposable loopback neostaffing_test_* database.')
        self.schema = 'employee_records_' + uuid4().hex
        self.admin = create_engine(URI)
        with self.admin.begin() as conn:
            conn.execute(text('CREATE SCHEMA ' + self.schema))
        def pg_app(config, **kwargs):
            config.SQLALCHEMY_DATABASE_URI = URI
            config.SQLALCHEMY_ENGINE_OPTIONS = {'connect_args': {'options': '-csearch_path=' + self.schema + ' -clock_timeout=10000 -cstatement_timeout=30000'}}
            return create_app(config, auto_bootstrap=False)
        with patch.object(fixtures.fixture.sektor_fixture, 'create_app', side_effect=pg_app):
            super().setUp()

    def tearDown(self):
        db.session.remove(); db.engine.dispose(); self.context.pop()
        with self.admin.begin() as conn:
            conn.execute(text('DROP SCHEMA ' + self.schema + ' CASCADE'))
        self.admin.dispose()

    def race(self, actions):
        self.enable()
        record_id = self.draft().id
        user_id = self.user.id
        db.session.commit()
        barrier = Barrier(2)
        def run(action):
            with self.app.app_context():
                actor = db.session.get(User, user_id)
                barrier.wait(timeout=10)
                try:
                    action(actor, record_id)
                    db.session.commit()
                    return 'saved'
                except ValueError:
                    db.session.rollback()
                    return 'conflict'
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(run, actions)), ['conflict','saved'])
        db.session.expire_all()
        self.assertEqual(records.Event.query.count(), 2)

    def test_two_finalizations_only_one_wins(self):
        action = lambda user, key: records.finalize(user, key, 1, 'yes')
        self.race([action, action])

    def test_edit_cannot_race_review_of_original_version(self):
        self.race([lambda user,key: records.edit(user,key,1,'verbal','New draft'),
                   lambda user,key: records.finalize(user,key,1,'yes')])

    def test_committed_management_revocation_beats_cached_authority(self):
        self.enable()
        record = self.draft()
        records.access(self.user, self.worker.id)
        leadership_id, record_id, user_id = self.leadership.id, record.id, self.user.id
        db.session.commit()
        with self.admin.begin() as conn:
            conn.execute(text(f'UPDATE {self.schema}.staffing_leadership_assignments SET active=false WHERE id=:id'), {'id':leadership_id})
        with self.assertRaises(ValueError):
            records.finalize(db.session.get(User,user_id),record_id,1,'yes')
        db.session.rollback()
        self.assertIsNone(db.session.get(records.Record, record_id).finalized_at)
