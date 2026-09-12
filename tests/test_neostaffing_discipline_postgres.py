"""Opt-in disposable PostgreSQL concurrency and SQL-dialect proof."""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, local
import unittest
from unittest.mock import patch
from uuid import uuid4
from datetime import date, datetime, timedelta

from sqlalchemy import create_engine, text, event
from sqlalchemy.engine import make_url

from app import create_app
from app.extensions import db
from app.models import User, PortalAppAccess
from app.models.staffing_accountability import StaffingAccountabilityResolution as Resolution
from app.services import neostaffing_discipline as discipline
from tests import test_neostaffing_discipline as fixtures


URI = os.environ.get("NEOSTAFFING_TEST_POSTGRES_URL")


@unittest.skipUnless(URI, "Requires disposable local NEOSTAFFING_TEST_POSTGRES_URL")
class DisciplinePostgresTest(fixtures.DisciplineWorkflowTest):
    def setUp(self):
        url = make_url(URI)
        if (url.get_backend_name() != "postgresql" or url.host not in ("127.0.0.1", "localhost")
                or not (url.database or "").startswith("neostaffing_test_")):
            raise ValueError("Only disposable loopback neostaffing_test_* databases are allowed.")
        self.schema = "accountability_" + uuid4().hex
        self.admin = create_engine(URI)
        with self.admin.begin() as connection:
            connection.execute(text("CREATE SCHEMA " + self.schema))
        def pg_app(config, **_kwargs):
            config.SQLALCHEMY_DATABASE_URI = URI
            config.SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {
                "options": "-csearch_path=" + self.schema + " -clock_timeout=10000 -cstatement_timeout=30000"}}
            return create_app(config, auto_bootstrap=False)
        with patch.object(fixtures.employee_fixture.sektor_fixture, "create_app", side_effect=pg_app):
            super().setUp()

    def tearDown(self):
        db.session.remove()
        db.engine.dispose()
        self.context.pop()
        with self.admin.begin() as connection:
            connection.execute(text("DROP SCHEMA " + self.schema + " CASCADE"))
        self.admin.dispose()

    def resolution_race(self, values):
        user_id, person_id = self.user.id, self.worker.id
        db.session.commit()
        barrier = Barrier(2)
        def resolve(_index):
            with self.app.app_context():
                user = db.session.get(User, user_id)
                barrier.wait(timeout=10)
                try:
                    discipline.resolve_obligation(user, person_id, values, self.today)
                    db.session.commit()
                    return "resolved"
                except ValueError:
                    db.session.rollback()
                    return "conflict"
        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertEqual(sorted(executor.map(resolve, range(2))), ["conflict", "resolved"])
        db.session.expire_all()
        self.assertEqual(Resolution.query.count(), 1)
        self.assertFalse(self.state()["due"])

    def test_concurrent_informal_delivery_has_one_winner(self):
        self.fact()
        self.complete_history()
        self.resolution_race(self.resolve_values())

    def test_concurrent_formal_resolution_consumes_trigger_once(self):
        for offset in (4, 3, 2, 1):
            self.fact(offset)
        self.complete_history()
        db.session.add(PortalAppAccess(user_id=self.user.id, app_code="neostaffing", status="approved", role="master", is_active=True))
        db.session.commit()
        self.resolution_race(self.resolve_values("no_discipline"))

    def test_concurrent_combo_sources_create_one_workday(self):
        from app.services import neostaffing as staffing
        from app.models import StaffingDailyAttendance
        from app.models.staffing_accountability import StaffingAccountabilityWorkday, StaffingAccountabilitySource
        self.combo_setup()
        first = self._add_sort_operation(date(2026, 9, 10), "configured other")
        second = self._add_sort_operation(date(2026, 9, 11), "night")
        user_id, person_id = self.user.id, self.worker.id
        commands = [(first.id, self.other_area.id, "call_in"), (second.id, self.areas["ebm"].id, "no_call")]
        first_id, second_id = first.id, second.id
        db.session.commit()
        barrier = Barrier(2)
        def current(_gateway=None, sort_name="night"):
            from app.models import SortDateOperation
            return db.session.get(SortDateOperation, second_id if sort_name == "night" else first_id)
        def window(op, _gateway=None):
            start = datetime.combine(op.sort_date, datetime.min.time()).replace(hour=23 if op.id == first_id else 3)
            return start, start + timedelta(hours=4)
        def save(command):
            with self.app.app_context():
                actor = db.session.get(User, user_id)
                barrier.wait(timeout=10)
                operation_id, area_id, status = command
                staffing.save_attendance({"sort_date_operation_id": str(operation_id),
                    "work_area_id": str(area_id), f"status_{person_id}": status}, actor)
                db.session.commit()
        with patch.object(staffing, "current_night_attendance_operation", side_effect=current), patch.object(
            staffing, "current_attendance_operation", side_effect=current), patch(
            "app.services.neostaffing_workday_identity.sort_lookup_window_for_operation", side_effect=window):
            with ThreadPoolExecutor(max_workers=2) as executor:
                list(executor.map(save, commands))
        self.assertEqual(StaffingAccountabilityWorkday.query.filter_by(person_id=person_id).count(), 1)
        self.assertEqual(StaffingAccountabilitySource.query.filter_by(person_id=person_id).count(), 2)
        self.assertEqual(StaffingDailyAttendance.query.filter_by(person_id=person_id).count(), 2)

    def test_finalization_waiting_writer_cannot_use_prelock_snapshot(self):
        from app.models import SortDateOperation, StaffingDailyAttendance
        from app.services import neostaffing as staffing
        from app.services.neostaffing_attendance_history import finalize_attendance_summaries
        self.active_sort()
        operation_id, person_id, user_id, area_id = self.operation.id, self.worker.id, self.user.id, self.areas["ebm"].id
        db.session.commit()
        finalized, attempted, release = Event(), Event(), Event()
        thread = local()
        def observe(_conn, _cursor, statement, *_args):
            if getattr(thread, "writer", False) and "FOR NO KEY UPDATE" in statement:
                attempted.set()
        def close():
            with self.app.app_context():
                finalize_attendance_summaries(operation_id, db.session.get(User, user_id))
                finalized.set()
                if not release.wait(timeout=10):
                    raise AssertionError("Writer did not reach the operation lock")
                db.session.commit()
        def save():
            with self.app.app_context():
                thread.writer = True
                if not finalized.wait(timeout=10):
                    raise AssertionError("Finalization did not acquire its lock")
                try:
                    staffing.save_attendance({"sort_date_operation_id": str(operation_id),
                        "work_area_id": str(area_id), f"status_{person_id}": "call_in"}, db.session.get(User, user_id))
                    db.session.commit()
                    return "unexpected write"
                except ValueError as error:
                    db.session.rollback()
                    return str(error)
        event.listen(db.engine, "before_cursor_execute", observe)
        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                closer, writer = executor.submit(close), executor.submit(save)
                try:
                    self.assertTrue(attempted.wait(timeout=10))
                finally:
                    release.set()
                closer.result(timeout=10)
                self.assertIn("finalized", writer.result(timeout=10))
        finally:
            event.remove(db.engine, "before_cursor_execute", observe)
        self.assertEqual(StaffingDailyAttendance.query.count(), 0)
