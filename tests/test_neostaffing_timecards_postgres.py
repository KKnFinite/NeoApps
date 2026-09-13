"""Disposable PostgreSQL proof: real independent sessions and row locks."""
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from uuid import uuid4
from unittest.mock import patch
from datetime import date

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app import create_app
from app.extensions import db
from app.models import User
from app.models.staffing_timecard import StaffingTimecardSegment as Segment, StaffingTimecardWeek as Week
from app.services import neostaffing_timecards as tc
from tests import test_neostaffing_timecards as fixtures

URI = os.environ.get("NEOSTAFFING_TEST_POSTGRES_URL")


@unittest.skipUnless(URI, "Requires disposable loopback PostgreSQL")
class TimecardsPostgresTest(fixtures.TimecardsTest):
    def setUp(self):
        url = make_url(URI)
        if url.host not in ("localhost", "127.0.0.1") or not (url.database or "").startswith("neostaffing_test_"):
            raise ValueError("Disposable loopback database required.")
        self.schema = "timecards_" + uuid4().hex
        self.admin = create_engine(URI)
        with self.admin.begin() as connection:
            connection.execute(text("CREATE SCHEMA " + self.schema))
        def factory(config, **_kwargs):
            config.SQLALCHEMY_DATABASE_URI = URI
            config.SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"options": "-csearch_path=" + self.schema + " -clock_timeout=10000 -cstatement_timeout=30000"}}
            return create_app(config, auto_bootstrap=False)
        with patch.object(fixtures.fixtures.sektor_fixture, "create_app", side_effect=factory):
            super().setUp()

    def tearDown(self):
        db.session.remove()
        db.engine.dispose()
        self.context.pop()
        with self.admin.begin() as connection:
            connection.execute(text("DROP SCHEMA " + self.schema + " CASCADE"))
        self.admin.dispose()

    def test_concurrent_same_slice_one_winner(self):
        row = self.attendance()
        command = self.command(row)
        user_id = self.user.id
        db.session.commit()
        barrier = Barrier(2)
        def save(_):
            with self.app.app_context():
                actor = db.session.get(User, user_id)
                barrier.wait(timeout=10)
                try:
                    tc.save_segments(actor, [command], as_of=date(2026,9,12))
                    db.session.commit()
                    return "saved"
                except ValueError:
                    db.session.rollback()
                    return "conflict"
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(save, range(2))), ["conflict", "saved"])
        self.assertEqual(Segment.query.count(), 1)

    def test_concurrent_distinct_employees_both_persist(self):
        row, peer = self.attendance(), self.attendance(self.peer)
        commands = [self.command(row), self.command(peer)]
        user_id = self.user.id
        db.session.commit()
        barrier = Barrier(2)
        def save(command):
            with self.app.app_context():
                actor = db.session.get(User, user_id)
                barrier.wait(timeout=10)
                tc.save_segments(actor, [command], as_of=date(2026,9,12))
                db.session.commit()
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(save, commands))
        self.assertEqual(Segment.query.count(), 2)

    def test_archive_snapshot_then_concurrent_edit_rejects_download_ack(self):
        from app.services import neostaffing_timecard_exports as exports
        row = self.attendance()
        command, actor_id = self.command(row), self.user.id
        db.session.commit()
        captured, release = Event(), Event()
        original = exports.build_package
        def held_package(*args, **kwargs):
            package = original(*args, **kwargs)
            captured.set()
            self.assertTrue(release.wait(10))
            return package
        def archive():
            with self.app.app_context():
                actor = db.session.get(User, actor_id)
                _, token = exports.generate(actor, date(2026,9,6), complete=True, as_of=date(2026,9,14))
                db.session.commit()
                return token
        def edit():
            with self.app.app_context():
                actor = db.session.get(User, actor_id)
                tc.save_segments(actor, [command], as_of=date(2026,9,14))
                db.session.commit()
        with patch.object(exports, "can_archive", return_value=True), patch.object(exports, "build_package", side_effect=held_package):
            with ThreadPoolExecutor(max_workers=2) as pool:
                downloading = pool.submit(archive)
                self.assertTrue(captured.wait(10))
                editing = pool.submit(edit)
                release.set()
                token = downloading.result(timeout=15)
                editing.result(timeout=15)
            with self.assertRaisesRegex(ValueError, "stale"):
                exports.acknowledge_download(self.user, token)
            db.session.rollback()
        self.assertEqual(Segment.query.count(), 1)
        self.assertIsNone(tc.lock_week(date(2026,9,6)).purge_after)
