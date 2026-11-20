"""Forward roster journal with real PostgreSQL transaction boundaries."""

import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from threading import Barrier

from sqlalchemy import text

from app import create_app
from app.extensions import db
from app.models import (
    Gateway, SortDateOperation, StaffingAttendanceOutcome, StaffingDailyAttendance, StaffingPerson,
    StaffingRosterClose, StaffingRosterPlacement, StaffingRosterRevision, StaffingTimecardSlice, StaffingUnit,
)
from app.services import neostaffing as staffing
from app.services import neostaffing_historical_records as history
from app.services.neostaffing_workspace import attendance_history_people, recorded_attendance
from app.services.neostaffing_history_schema import initialize_reporting_history
from tests import test_motherbrain_block_out_authority_postgres as pg


@unittest.skipUnless(pg.URI, "Requires disposable loopback PostgreSQL")
class StaffingHistoricalRecordsPostgresTest(unittest.TestCase):
    def setUp(self):
        pg.postgres_setup(self, sys.modules[__name__], self._setup)

    def _setup(self):
        config = type("Config", (), {"TESTING": True, "SECRET_KEY": "pg-test-only",
                                     "SQLALCHEMY_DATABASE_URI": "sqlite://"})
        self.app = create_app(config)
        context = self.app.app_context()
        context.push()
        self.addCleanup(context.pop)
        db.create_all()
        gateway = Gateway(code="RFD", name="Synthetic", is_active=True)
        sort = StaffingUnit(unit_type="sort", name="Night")
        operation = StaffingUnit(unit_type="operation", name="Ramp", parent=sort)
        self.original = StaffingUnit(unit_type="work_area", name="EBM", parent=operation)
        self.destination = StaffingUnit(unit_type="work_area", name="WBM", parent=operation)
        self.person = StaffingPerson(employee_id="HISTORY-1", first_name="Test", last_name="Person",
                                     classification="part_time", seniority_date=date(2020, 1, 1))
        self.operation = SortDateOperation(gateway=gateway, gateway_code="RFD", sort_name="night",
                                           sort_date=date(2026, 11, 19))
        db.session.add_all((self.original, self.destination, self.person, self.operation))
        db.session.flush()
        staffing.assign_work_area(self.person, self.original)
        db.session.commit()
        assert initialize_reporting_history()
        db.session.commit()

    def make_close(self):
        cutoff = db.session.scalar(text("SELECT clock_timestamp() AT TIME ZONE 'UTC'"))
        close = StaffingRosterClose(
            sort_date_operation_id=self.operation.id, gateway_code="RFD", sort_name="night",
            sort_date=self.operation.sort_date, sort_unit_id=self.original.parent.parent_id,
            opens_at=cutoff - timedelta(microseconds=1), closes_at=cutoff,
        )
        db.session.add(close)
        db.session.commit()
        return cutoff

    def test_after_close_transfer_defers_capture_then_two_seals_publish_once(self):
        assert not initialize_reporting_history()  # Safe after repeated invocation.
        db.session.commit()
        cutoff = self.make_close()
        operation_id, original_id = self.operation.id, self.original.id
        staffing.assign_work_area(self.person, self.destination)
        self.person.classification = "pre_seniority"
        db.session.flush()

        def capture(barrier=None):
            with self.app.app_context():
                if barrier:
                    barrier.wait(timeout=10)
                result = history.seal_roster(operation_id, cutoff + timedelta(seconds=1))
                db.session.commit()
                return result

        with ThreadPoolExecutor(2) as executor:
            assert executor.submit(capture).result(timeout=10) is False
            db.session.commit()
            barrier = Barrier(2)
            results = [executor.submit(capture, barrier) for _ in range(2)]
            assert sorted(job.result(timeout=15) for job in results) == [False, True]
        record = StaffingRosterPlacement.query.filter_by(sort_date_operation_id=operation_id).one()
        assert record.work_area_id == original_id and record.classification == "part_time"
        assert record.area_path == "Night / Ramp / EBM"
        assert record.employee_id == "HISTORY-1"
        assert len(StaffingRosterRevision.query.filter(StaffingRosterRevision.change_set_id.is_not(None)).all()) >= 2

    def test_rollback_excludes_source_changes_and_restores_unsealed_close(self):
        count = StaffingRosterRevision.query.count()
        staffing.assign_work_area(self.person, self.destination)
        db.session.flush()
        assert StaffingRosterRevision.query.count() > count
        db.session.rollback()
        assert StaffingRosterRevision.query.count() == count
        cutoff = self.make_close()
        op_id, original_id = self.operation.id, self.original.id
        assert history.seal_roster(op_id, cutoff)
        db.session.rollback()
        assert StaffingRosterPlacement.query.count() == 0
        assert db.session.get(StaffingRosterClose, op_id).sealed_at is None
        assert history.seal_roster(op_id, cutoff)
        db.session.commit()
        assert StaffingRosterPlacement.query.one().work_area_id == original_id

    def test_status_only_cutover_and_a_rolled_back_correction(self):
        self.app.config["NEOSTAFFING_HISTORY_ENABLED"] = True
        op, person, area = self.operation, self.person, self.original
        day, sort_id = op.sort_date, area.parent.parent_id
        db.session.add(StaffingTimecardSlice(
            person_id=person.id, sort_date_operation_id=op.id, sort_unit_id=sort_id,
            work_area_unit_id=area.id, workday_date=day, attendance_status="call_in",
            context_json='{"employee_id":"HISTORY-1","employee":"Test Person","classification_at_recording":"part_time"}',
        ))
        db.session.commit()
        assert history.import_available_timecard_marks() == 1
        db.session.commit()
        assert history.import_available_timecard_marks() == 0
        scope = {"gateway_code": "RFD", "areas": {area.id}}
        # Missing original actor attribution excludes a speculative classification.
        assert not attendance_history_people(scope, "HISTORY-1", "part_time", day, day)
        assert [row[-1] for row in recorded_attendance(scope, day, day)] == ["call_in"]
        source = StaffingDailyAttendance(person_id=person.id, sort_date_operation_id=op.id, sort_unit_id=sort_id,
            work_area_unit_id=area.id, attendance_date=day, status="no_call", note="synthetic private note")
        assignment = person.work_assignments[0]
        units = staffing._daily_attendance_hierarchy()["by_id"]
        history.record_attendance_changes(op, {person.id: "no_call"}, {person.id: source},
                                          {person.id: assignment}, {}, units)
        assert StaffingAttendanceOutcome.query.one().status == "no_call"
        db.session.rollback()
        assert StaffingAttendanceOutcome.query.one().status == "call_in"
