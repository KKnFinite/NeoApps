from datetime import date, datetime, timedelta
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app import create_app
from app.extensions import db
from app.models import (StaffingPerson, StaffingUnit, SortDateOperation,
                        StaffingAttendanceOccurrence, StaffingAttendanceSummary)
from app.models.staffing_accountability import (
    StaffingAccountabilityWorkday as Workday, StaffingAccountabilitySource as Source,
)
from app.services import neostaffing_discipline as discipline
from app.services.neostaffing_workday_identity import chronological_pair
from tests import test_neosektor_employees as employee_fixture
from app.models.staffing_accountability import (
    StaffingTrackerSetting as Setting, StaffingComboWorkday as Combo,
    StaffingAccountabilityReconciliation as Reconciliation,
    StaffingAccountabilityResolution as Resolution, StaffingAccountabilityCoverage as Coverage,
)


class DisciplineCalculationTest(unittest.TestCase):
    def test_default_and_custom_thresholds(self):
        self.assertIsNone(discipline.policy_action(0))
        self.assertEqual([discipline.policy_action(n) for n in range(1, 9)],
                         [action for _, action in discipline.DEFAULT_POLICY])
        self.assertEqual(discipline.policy_action(50), "Termination")
        custom = discipline.normalized_policy([[5, "Suspension"], [2, "Written Warning"]])
        self.assertIsNone(discipline.policy_action(1, custom))
        self.assertEqual(discipline.policy_action(4, custom), "Written Warning")
        self.assertIsNone(discipline.normalized_policy([list(row) for row in discipline.DEFAULT_POLICY]))

    def test_policy_rejects_invalid_thresholds_and_actions(self):
        for value in ([[0, "Verbal"]], [[True, "Verbal"]], [[1, "Fired"]],
                      [[1, "Verbal"], [1, "Suspension"]]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                discipline.normalized_policy(value)

    def test_formal_progression_cannot_skip(self):
        for raw in discipline.FORMAL:
            self.assertEqual(discipline.sequence_action(raw, None), "Warning Letter")
        self.assertEqual(discipline.sequence_action("Termination", "Warning Letter"), "Suspension")
        self.assertEqual(discipline.sequence_action("Termination", "Suspension"), "Termination")
        self.assertEqual(discipline.sequence_action("Verbal", "Suspension"), "Verbal")

    def test_chronological_pair_crosses_midnight_and_preserves_source_dates(self):
        first = SimpleNamespace(id=1, sort_name="Configured A", sort_date=date(2026, 9, 10))
        second = SimpleNamespace(id=2, sort_name="Configured B", sort_date=date(2026, 9, 11))
        starts = {1: datetime(2026, 9, 10, 22), 2: datetime(2026, 9, 11, 5)}
        window = lambda op: (starts[op.id], starts[op.id] + timedelta(hours=4))
        for source_id in (1, 2):
            self.assertEqual(chronological_pair([second, first], "Configured A", "Configured B", source_id, window), (first, second))
        self.assertIsNone(chronological_pair([first, second], "Configured B", "Configured A", 1, window))
        self.assertEqual(first.sort_date, date(2026, 9, 10))
        self.assertEqual(second.sort_date, date(2026, 9, 11))

    def test_missing_partner_is_not_borrowed_after_another_first_sort(self):
        ops = [SimpleNamespace(id=i, sort_name=name) for i, name in ((1, "A"), (2, "A"), (3, "B"))]
        window = lambda op: (datetime(2026, 9, op.id), datetime(2026, 9, op.id, 4))
        self.assertIsNone(chronological_pair(ops, "A", "B", 1, window))
        self.assertEqual(chronological_pair(ops, "A", "B", 3, window), (ops[1], ops[2]))


class DisciplineFactTest(unittest.TestCase):
    def setUp(self):
        self.app = create_app(type("Config", (), {"TESTING": True, "SECRET_KEY": "test",
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", "SQLALCHEMY_TRACK_MODIFICATIONS": False}), auto_bootstrap=False)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.person = StaffingPerson(employee_id="TEST1", first_name="Test", last_name="Worker",
                                     classification="full_time_combo", active=True, seniority_date=date(2020, 1, 1))
        self.unit = StaffingUnit(unit_type="operation", name="Fixture Operation")
        self.operations = [SortDateOperation(sort_date=date(2026, 9, 10 + i), sort_name=name, gateway_code="TST")
                           for i, name in enumerate(("Configured A", "Configured B"))]
        db.session.add_all([self.person, self.unit, *self.operations])
        db.session.flush()
        self.group = Workday(person_id=self.person.id, workday_date=date(2026, 9, 10), requires_pair=True)
        db.session.add(self.group)
        db.session.flush()
        db.session.add_all(Source(person_id=self.person.id, workday_id=self.group.id,
            operation_id=op.id, position=i) for i, op in enumerate(self.operations, 1))
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def occurrence(self, index, status):
        op = self.operations[index]
        row = StaffingAttendanceOccurrence(person_id=self.person.id,
            sort_date_operation_id=op.id, attendance_date=op.sort_date, status=status)
        db.session.add(row)
        db.session.commit()
        return row

    def finalize(self, index):
        op = self.operations[index]
        db.session.add(StaffingAttendanceSummary(sort_date_operation_id=op.id,
            attendance_date=op.sort_date, scope_type="operation", scope_unit_id=self.unit.id))
        db.session.commit()

    def facts(self):
        return db.session.execute(discipline.finalized_facts(date(2026, 9, 12))).mappings().all()

    def test_both_finalizations_required_one_qualifying_side(self):
        self.occurrence(0, "call_in")
        self.assertEqual(self.facts(), [])
        self.finalize(0)
        self.assertEqual(self.facts(), [])
        self.finalize(1)
        self.assertEqual(len(self.facts()), 1)
        self.assertEqual(self.facts()[0]["severity"], 1)

    def test_no_call_wins_and_two_sources_count_once(self):
        first = self.occurrence(0, "call_in")
        self.occurrence(1, "no_call")
        self.finalize(0)
        self.finalize(1)
        self.assertEqual(len(self.facts()), 1)
        self.assertEqual(self.facts()[0]["severity"], 2)
        self.assertEqual(StaffingAttendanceOccurrence.query.count(), 2)
        self.assertEqual(first.sort_date_operation_id, self.operations[0].id)

    def test_correction_before_close_removes_infraction(self):
        row = self.occurrence(0, "no_call")
        identity = row.id
        row.status = "call_in"
        db.session.commit()
        self.assertEqual(row.id, identity)
        db.session.delete(row)
        db.session.commit()
        self.finalize(0)
        self.finalize(1)
        self.assertEqual(self.facts(), [])

    def test_logical_retention_is_nine_calendar_months(self):
        self.occurrence(0, "call_in")
        self.finalize(0)
        self.finalize(1)
        self.group.workday_date = date(2025, 12, 11)
        db.session.commit()
        self.assertEqual(self.facts(), [])
        self.group.workday_date = date(2025, 12, 12)
        db.session.commit()
        self.assertEqual(len(self.facts()), 1)


class DisciplineWorkflowTest(employee_fixture.SektorEmployeesTest):
    def setUp(self):
        super().setUp()
        self.worker = self.workers["ebm"]
        self.today = date(2026, 9, 12)
        db.session.add(Setting(unit_id=self.ramp.id, enabled=True))
        db.session.commit()

    def fact(self, offset=1, *, person=None):
        person = person or self.worker
        day = self.today - timedelta(days=offset)
        op = SortDateOperation.query.filter_by(gateway_code="RFD", sort_name="night", sort_date=day).first()
        if not op:
            op = SortDateOperation(gateway_code="RFD", sort_name="night", sort_date=day)
            db.session.add(op)
            db.session.flush()
            db.session.add(StaffingAttendanceSummary(sort_date_operation_id=op.id,
                attendance_date=day, scope_type="operation", scope_unit_id=self.ramp.id))
        group = Workday(person_id=person.id, workday_date=day)
        db.session.add(group)
        db.session.flush()
        db.session.add_all([Source(workday_id=group.id, person_id=person.id, operation_id=op.id, position=1),
            StaffingAttendanceOccurrence(person_id=person.id, sort_date_operation_id=op.id,
                                         attendance_date=day, status="call_in")])
        db.session.commit()
        return group

    def complete_history(self, person=None):
        person = person or self.worker
        db.session.add(Reconciliation(person_id=person.id,
            complete_since=discipline.occurrence_cutoff(self.today), actor_id=self.user.id))
        db.session.commit()

    def state(self, person=None):
        person = person or self.worker
        args = discipline.lock_authorized_employee(self.user, person.id, lock=False)
        return discipline.employee_state(*args, self.today)

    def resolve_values(self, kind="informal"):
        state = self.state()
        return dict(kind=kind, expected_facts=",".join(str(row["id"]) for row in state["facts"]),
                    recommendation=state["recommendation"])

    def test_queue_reconciliation_filter_and_employee_page(self):
        self.fact()
        context = discipline.queue_context(self.user, {}, self.today)
        self.assertEqual(context["total"], 1)
        self.assertEqual(context["rows"][0]["status"], "RECONCILIATION NEEDED")
        response = self.client.get("/neostaffing/accountability")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"RECONCILIATION NEEDED", response.data)
        detail = self.client.get(f"/neostaffing/accountability/employee/{self.worker.id}")
        self.assertEqual(detail.status_code, 200)
        self.assertIn(b"THIS IS THEIR FIRST INFRACTION", detail.data)

    def test_informal_supersedes_and_delivery_covers_globally_once(self):
        self.fact(2)
        self.complete_history()
        self.assertEqual(self.state()["recommendation"], "Verbal")
        self.fact(1)
        self.assertEqual(self.state()["recommendation"], "Written Warning")
        values = self.resolve_values()
        discipline.resolve_obligation(self.user, self.worker.id, values, self.today)
        db.session.commit()
        self.assertEqual(Resolution.query.count(), 1)
        self.assertEqual(Coverage.query.count(), 2)
        self.assertFalse(self.state()["due"])
        with self.assertRaises(ValueError):
            discipline.resolve_obligation(self.user, self.worker.id, values, self.today)

    def test_formal_consumes_only_latest_trigger_no_older_resurrection(self):
        for offset in (4, 3, 2, 1):
            self.fact(offset)
        self.complete_history()
        state = self.state()
        self.assertEqual(state["recommendation"], "Warning Letter")
        values = self.resolve_values("issue")
        with self.assertRaises(ValueError):
            discipline.resolve_obligation(self.user, self.worker.id, values, self.today)
        db.session.rollback()
        with patch.object(discipline, "user_can_access_app", return_value=True):
            discipline.resolve_obligation(self.user, self.worker.id, values, self.today)
        db.session.commit()
        self.assertFalse(self.state()["due"])
        self.assertEqual(Resolution.query.one().trigger_workday_id, state["trigger"]["id"])
        self.assertEqual(discipline.queue_context(self.user, {}, self.today)["total"], 0)

    def test_no_discipline_consumes_trigger(self):
        for offset in (4, 3, 2, 1):
            self.fact(offset)
        self.complete_history()
        with patch.object(discipline, "user_can_access_app", return_value=True):
            discipline.resolve_obligation(self.user, self.worker.id, self.resolve_values("no_discipline"), self.today)
        db.session.commit()
        self.assertFalse(self.state()["due"])
        self.assertIsNone(self.state()["last_formal"])
        history = self.client.get("/neostaffing/accountability?tab=formal_history")
        self.assertEqual(history.status_code, 200)
        self.assertIn(b"No formal discipline", history.data)

    def test_tracker_off_still_counts_and_reconciliation_is_nonblocking(self):
        self.fact()
        db.session.get(Setting, self.ramp.id).enabled = False
        db.session.commit()
        self.assertEqual(self.state()["count"], 1)
        self.assertEqual(self.state()["status"], "TRACKER OFF")
        discipline.reconcile_history(self.user, self.worker.id, "skip", [], self.today)
        self.assertTrue(self.state()["reconciliation_needed"])
        discipline.reconcile_history(self.user, self.worker.id, "first", [], self.today)
        db.session.commit()
        self.assertFalse(self.state()["reconciliation_needed"])
        self.assertEqual(Workday.query.count(), 1)

    def test_nonmanagement_and_outside_scope_cannot_resolve(self):
        self.fact(person=self.outsider)
        with self.assertRaises(ValueError):
            discipline.lock_authorized_employee(self.user, self.outsider.id)
        self.manager.classification = "part_time"
        db.session.commit()
        self.assertEqual(self.client.get("/neostaffing/accountability").status_code, 403)

    def test_queue_pagination_is_bounded(self):
        for index in range(30):
            self.person(f"Queue-{index}", area=self.areas["ebm"])
        db.session.commit()
        first = discipline.queue_context(self.user, {"tab": "all", "search": "Queue"}, self.today)
        second = discipline.queue_context(self.user, {"tab": "all", "search": "Queue", "page": 2}, self.today)
        self.assertEqual(first["total"], 30)
        self.assertEqual(len(first["rows"]), 25)
        self.assertEqual(len(second["rows"]), 5)
        self.assertFalse({row["id"] for row in first["rows"]} & {row["id"] for row in second["rows"]})

    def combo_setup(self):
        from app.services import neostaffing as staffing
        from app.models import StaffingLeadershipAssignment
        self.other_sort = StaffingUnit(unit_type="sort", name="Configured Other")
        self.other_operation = StaffingUnit(unit_type="operation", name="Other Operation", parent=self.other_sort)
        self.other_department = StaffingUnit(unit_type="department", name="Other Department", parent=self.other_operation)
        self.other_area = StaffingUnit(unit_type="work_area", name="Other Area", parent=self.other_department)
        db.session.add_all([self.other_sort, self.other_operation, self.other_department, self.other_area])
        self.worker.classification = "full_time_combo"
        db.session.flush()
        staffing.assign_work_area(self.worker, self.other_area)
        db.session.add(StaffingLeadershipAssignment(person=self.manager, unit=self.other_area, leadership_level="work_area"))
        db.session.commit()
        discipline.configure_workday(self.user, self.worker.id, self.other_sort.id, self.night.id, 0)
        db.session.commit()

    def test_combo_either_tracker_and_stricter_policy(self):
        self.combo_setup()
        self.fact()
        self.complete_history()
        db.session.get(Setting, self.ramp.id).enabled = False
        db.session.add(Setting(unit_id=self.other_operation.id, enabled=True,
            policy_json='[[1,"Suspension"]]'))
        db.session.commit()
        self.assertTrue(self.state()["enabled"])
        self.assertEqual(self.state()["recommendation"], "Warning Letter")
        queue = discipline.queue_context(self.user, {}, self.today)
        self.assertEqual(queue["rows"][0]["recommendation"], "Warning Letter")
        self.assertEqual(queue["rows"][0]["infractions"], 1)

    def test_two_sort_attendance_writes_bind_once_and_preserve_sources(self):
        from app.services import neostaffing as staffing
        from app.models import StaffingDailyAttendance
        self.combo_setup()
        first = self._add_sort_operation(date(2026, 9, 10), "configured other")
        second = self._add_sort_operation(date(2026, 9, 11), "night")
        def window(op, _gateway=None):
            hour = 23 if op.sort_name == "configured other" else 3
            start = datetime.combine(op.sort_date, datetime.min.time()).replace(hour=hour)
            return start, start + timedelta(hours=4)
        for operation, area, status in [(first, self.other_area, "call_in"),
                                        (second, self.areas["ebm"], "no_call"),
                                        (first, self.other_area, "no_call")]:
            with patch.object(staffing, "current_night_attendance_operation", return_value=operation), patch.object(
                staffing, "current_attendance_operation", return_value=operation), patch(
                "app.services.neostaffing_workday_identity.sort_lookup_window_for_operation", side_effect=window):
                staffing.save_attendance({"sort_date_operation_id": str(operation.id),
                    "work_area_id": str(area.id), f"status_{self.worker.id}": status}, self.user)
                db.session.commit()
        self.assertEqual(Workday.query.filter_by(person_id=self.worker.id).count(), 1)
        self.assertEqual(Source.query.filter_by(person_id=self.worker.id).count(), 2)
        self.assertEqual(StaffingDailyAttendance.query.filter_by(person_id=self.worker.id).count(), 2)
        self.assertEqual(db.session.execute(discipline.finalized_facts(self.today)).all(), [])
        for operation, unit in ((first, self.other_operation), (second, self.ramp)):
            db.session.add(StaffingAttendanceSummary(sort_date_operation_id=operation.id,
                attendance_date=operation.sort_date, scope_type="operation", scope_unit_id=unit.id))
        db.session.commit()
        facts = db.session.execute(discipline.finalized_facts(self.today)).mappings().all()
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["severity"], 2)
        binding = {(row.workday_id, row.operation_id, row.position) for row in Source.query.all()}
        discipline.configure_workday(self.user, self.worker.id, self.night.id, self.other_sort.id, 1)
        db.session.commit()
        self.assertEqual(binding, {(row.workday_id, row.operation_id, row.position) for row in Source.query.all()})

    def test_late_partner_finalization_completes_frozen_old_configuration(self):
        from app.services import neostaffing as staffing
        from app.services.neostaffing_attendance_history import finalize_attendance_summaries
        self.combo_setup()
        first = self._add_sort_operation(date(2026, 9, 10), "configured other")
        def window(op, _gateway=None):
            start = datetime.combine(op.sort_date, datetime.min.time()).replace(
                hour=23 if op.sort_name == "configured other" else 3)
            return start, start + timedelta(hours=4)
        with patch.object(staffing, "current_attendance_operation", return_value=first), patch(
            "app.services.neostaffing_workday_identity.sort_lookup_window_for_operation", side_effect=window):
            staffing.save_attendance({"sort_date_operation_id": str(first.id),
                "work_area_id": str(self.other_area.id), f"status_{self.worker.id}": "call_in"}, self.user)
            db.session.commit()
            finalize_attendance_summaries(first, self.user)
            db.session.commit()
        group_id = Workday.query.one().id
        self.assertEqual(Source.query.count(), 1)
        self.assertEqual(db.session.execute(discipline.finalized_facts(self.today)).all(), [])
        discipline.configure_workday(self.user, self.worker.id, self.night.id, self.other_sort.id, 1)
        db.session.commit()
        second = self._add_sort_operation(date(2026, 9, 11), "night")
        with patch("app.services.neostaffing_workday_identity.sort_lookup_window_for_operation", side_effect=window):
            finalize_attendance_summaries(second, self.user)
            db.session.commit()
        self.assertEqual(Source.query.count(), 2)
        facts = db.session.execute(discipline.finalized_facts(self.today)).mappings().all()
        self.assertEqual([row["id"] for row in facts], [group_id])
        self.assertEqual(Workday.query.one().first_sort_id, self.other_sort.id)

    def test_configured_sort_attendance_navigation_uses_child_ancestry(self):
        from app.services import neostaffing as staffing
        from app.models import PortalAppAccess
        self.combo_setup()
        operation = self._add_sort_operation(date(2026, 9, 11), "configured other")
        db.session.add(PortalAppAccess(user_id=self.user.id, app_code="neostaffing", role="master", status="approved", is_active=True))
        db.session.commit()
        with patch.object(staffing, "current_attendance_operation", return_value=operation) as current:
            context = staffing.attendance_context({"work_area_id": str(self.other_area.id)}, self.user)
        self.assertEqual(current.call_args.args[1], self.other_sort.name)
        self.assertTrue(context["ready"])
        self.assertEqual([row["person"].id for row in context["rows"]], [self.worker.id])
        with patch.object(staffing, "current_attendance_operation", return_value=operation), patch(
            "app.neostaffing.routes.attendance_history_service.maintain_current_attendance_rollover"):
            response = self.client.get(f"/neostaffing/attendance?sort_id={self.other_sort.id}")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'aria-label="Attendance Sort"', response.data)
        self.assertIn(f'sort_id={self.other_sort.id}&amp;work_area_id={self.other_area.id}'.encode(), response.data)

    def test_additive_table_bootstrap_and_backfill_preserve_existing_facts(self):
        from sqlalchemy import inspect
        from app.services.schema_sync import _create_missing_application_tables
        operation = self._add_sort_operation(date(2026, 9, 10), "night")
        occurrence = StaffingAttendanceOccurrence(person_id=self.worker.id, sort_date_operation_id=operation.id,
            attendance_date=operation.sort_date, status="call_in")
        db.session.add(occurrence)
        db.session.commit()
        person_id, occurrence_id = self.worker.id, occurrence.id
        # End expired-attribute reads before DDL uses another connection.
        db.session.remove()
        tables = {model.__table__ for model in (Setting, Combo, Workday, Source, Resolution, Coverage, Reconciliation)}
        for table in reversed(db.metadata.sorted_tables):
            if table in tables:
                table.drop(db.engine)
        for _ in range(2):
            _create_missing_application_tables(set(inspect(db.engine).get_table_names()))
            discipline.backfill_legacy_workdays(self.today)
            db.session.commit()
        self.assertIsNotNone(db.session.get(StaffingPerson, person_id))
        self.assertEqual(StaffingAttendanceOccurrence.query.one().id, occurrence_id)
        self.assertEqual(Source.query.count(), 1)
        self.assertTrue({table.name for table in tables} <= set(inspect(db.engine).get_table_names()))

    def test_either_combo_side_delivers_same_obligation(self):
        from app.models import User, StaffingLeadershipAssignment
        self.combo_setup()
        self.fact()
        self.complete_history()
        second_manager = self.person("SECOND-SUP", classification="part_time_supervisor")
        second_user = User(username="second-sup", employee_id="SECOND-SUP", is_active=True,
                           password_hash="disabled-test-login", role="watcher")
        db.session.add_all([second_user, StaffingLeadershipAssignment(person=second_manager,
                            unit=self.other_area, leadership_level="work_area")])
        db.session.commit()
        values = self.resolve_values()
        discipline.resolve_obligation(second_user, self.worker.id, values, self.today)
        db.session.commit()
        with self.assertRaises(ValueError):
            discipline.resolve_obligation(self.user, self.worker.id, values, self.today)
        self.assertEqual(Resolution.query.count(), 1)

    def test_override_records_reason_and_consumes_trigger(self):
        for offset in (4, 3, 2, 1):
            self.fact(offset)
        self.complete_history()
        values = {**self.resolve_values("override"), "action": "Warning Letter", "note": "Verified exception"}
        with patch.object(discipline, "user_can_access_app", return_value=True):
            discipline.resolve_obligation(self.user, self.worker.id, values, self.today)
        db.session.commit()
        resolution = Resolution.query.one()
        self.assertEqual((resolution.note, resolution.actor_id), ("Verified exception", self.user.id))
        self.assertFalse(self.state()["due"])

    def test_queue_query_count_does_not_grow_per_employee(self):
        from sqlalchemy import event
        counts = []
        for size in (0, 30):
            for i in range(size):
                self.person(f"Count-{i}", area=self.areas["ebm"])
            db.session.commit()
            statements = []
            def capture(_connection, _cursor, statement, *_args):
                if statement.lstrip().lower().startswith("select"):
                    statements.append(statement)
            event.listen(db.engine, "before_cursor_execute", capture)
            try:
                discipline.queue_context(self.user, {"tab": "all"}, self.today)
            finally:
                event.remove(db.engine, "before_cursor_execute", capture)
            counts.append(len(statements))
        self.assertEqual(counts[0], counts[1])
        self.assertLessEqual(counts[0], 7)

    def test_queue_severity_order_filters_and_formal_history(self):
        wbm = self.workers["wbm"]
        for person, count, last in ((self.worker, 4, None), (wbm, 6, "Warning Letter"),
                                    (self.peer, 8, "Suspension")):
            for offset in range(1, count + 1):
                self.fact(offset, person=person)
            self.complete_history(person)
            if last:
                db.session.add(Resolution(person_id=person.id, kind="history", action=last,
                    resolved_on=self.today - timedelta(days=20), actor_id=self.user.id))
        self.fact(person=self.workers["dis"])
        db.session.commit()
        queue = discipline.queue_context(self.user, {}, self.today)
        self.assertEqual([row["id"] for row in queue["rows"]],
                         [self.peer.id, wbm.id, self.worker.id, self.workers["dis"].id])
        filtered = discipline.queue_context(self.user, {"recommendation": "Suspension",
            "operation_id": str(self.ramp.id), "department_id": str(self.shift.id), "search": "Worker-wbm"}, self.today)
        self.assertEqual([row["id"] for row in filtered["rows"]], [wbm.id])
        history = discipline.queue_context(self.user, {"tab": "formal_history", "status": "CURRENT"}, self.today)
        self.assertEqual(history["total"], 0)
        history = discipline.queue_context(self.user, {"tab": "formal_history", "status": "ACTION REQUIRED"}, self.today)
        self.assertEqual(history["total"], 2)

    def test_combo_source_binding_queries_are_set_based(self):
        from sqlalchemy import event
        from app.services.neostaffing_workday_identity import bind_workday_sources
        self.combo_setup()
        first = self._add_sort_operation(date(2026, 9, 10), "configured other")
        self._add_sort_operation(date(2026, 9, 11), "night")
        counts = []
        for size in (1, 30):
            people = [self.person(f"COMBO-{size}-{i}", classification="full_time_combo", area=self.other_area) for i in range(size)]
            db.session.add_all(Combo(person_id=person.id, first_sort_id=self.other_sort.id, second_sort_id=self.night.id) for person in people)
            db.session.commit()
            ids = {person.id for person in people}
            # Same existing lock contract as the attendance caller, outside the
            # measured binder. The binder itself must not query once per person.
            db.session.query(StaffingPerson.id).filter(StaffingPerson.id.in_(ids)).order_by(StaffingPerson.id).with_for_update().all()
            first.id, first.gateway
            queries = []
            def capture(_connection, _cursor, statement, *_args):
                if statement.lstrip().lower().startswith("select"):
                    queries.append(statement)
            def window(op, _gateway=None):
                start = datetime.combine(op.sort_date, datetime.min.time())
                return start, start + timedelta(hours=4)
            event.listen(db.engine, "before_cursor_execute", capture)
            try:
                with patch("app.services.neostaffing_workday_identity.sort_lookup_window_for_operation", side_effect=window):
                    bind_workday_sources(first, ids, user_id=self.user.id)
                db.session.flush()
            finally:
                event.remove(db.engine, "before_cursor_execute", capture)
            counts.append(len(queries))
            db.session.commit()
            self.assertEqual(Source.query.filter(Source.person_id.in_(ids)).count(), size * 2)
        self.assertEqual(counts[0], counts[1])
        self.assertLessEqual(counts[0], 6)

    def test_tracker_department_inheritance_and_stale_configuration(self):
        self.fact()
        self.manager.classification = "manager"
        db.session.get(Setting, self.ramp.id).enabled = False
        db.session.add(Setting(unit_id=self.shift.id, enabled=True))
        db.session.commit()
        self.assertTrue(self.state()["enabled"])
        with patch.object(discipline, "user_can_access_app", return_value=True):
            discipline.configure_tracker(self.user, self.ramp.id, True, None, 0)
            db.session.commit()
            with self.assertRaises(ValueError):
                discipline.configure_tracker(self.user, self.shift.id, False, None, 0)
            with self.assertRaises(ValueError):
                discipline.configure_tracker(self.user, self.ramp.id, False, None, 0)

    def test_prior_history_exact_dates_reconciliation_and_skip(self):
        self.fact()
        with self.assertRaises(ValueError):
            discipline.reconcile_history(self.user, self.worker.id, "history", [{"date": "2026-09-11", "status": "here"}], self.today)
        discipline.reconcile_history(self.user, self.worker.id, "history", [
            {"date": "2026-08-10", "status": "call_in"},
            {"date": "2026-07-15", "status": "no_call"},
        ], self.today)
        db.session.commit()
        state = self.state()
        self.assertEqual(state["count"], 3)
        self.assertFalse(state["reconciliation_needed"])
        self.assertEqual(StaffingAttendanceOccurrence.query.count(), 1)
        with self.assertRaises(ValueError):
            discipline.reconcile_history(self.user, self.worker.id, "history", [{"date": "2026-08-01", "status": "call_in"}], self.today)

    def test_bounded_retention_keeps_unexpired_formal_source_identity(self):
        old = discipline.occurrence_cutoff(self.today) - timedelta(days=1)
        groups = [Workday(person_id=self.worker.id, workday_date=old, imported_status="call_in") for _ in range(4)]
        db.session.add_all(groups)
        db.session.flush()
        db.session.add(Resolution(person_id=self.worker.id, trigger_workday_id=groups[0].id,
            kind="issue", action="Warning Letter", actor_id=self.user.id, resolved_on=self.today))
        db.session.add_all(Resolution(person_id=self.worker.id, kind="informal", action="Verbal",
            actor_id=self.user.id, resolved_on=old, resolved_at=datetime.combine(old, datetime.min.time())) for _ in range(3))
        db.session.commit()
        protected_id = groups[0].id
        with patch.object(discipline, "CLEANUP_BATCH", 2):
            discipline.purge_expired_discipline(self.today)
            db.session.commit()
        self.assertEqual(Resolution.query.count(), 2)
        self.assertEqual(Workday.query.count(), 2)
        self.assertIsNotNone(db.session.get(Workday, protected_id))
        self.assertEqual(self.state()["count"], 0)
        self.assertEqual(self.state()["last_formal"], "Warning Letter")

    def test_legacy_backfill_preserves_source_identity_and_is_idempotent(self):
        operation = self._add_sort_operation(date(2026, 9, 10), "night")
        row = StaffingAttendanceOccurrence(person_id=self.worker.id, sort_date_operation_id=operation.id,
            attendance_date=operation.sort_date, status="no_call")
        db.session.add(row)
        db.session.commit()
        identity = row.id
        self.assertEqual(discipline.backfill_legacy_workdays(self.today), 1)
        db.session.commit()
        self.assertEqual(discipline.backfill_legacy_workdays(self.today), 0)
        self.assertEqual(StaffingAttendanceOccurrence.query.one().id, identity)
        self.assertEqual(Source.query.one().operation_id, operation.id)

    def test_accountability_gets_are_read_only(self):
        from sqlalchemy import event
        self.fact()
        statements = []
        def capture(_connection, _cursor, statement, *_args):
            if statement.lstrip().lower().startswith(("insert", "update", "delete")):
                statements.append(statement)
        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            self.assertEqual(self.client.get("/neostaffing/accountability").status_code, 200)
            self.assertEqual(self.client.get(f"/neostaffing/accountability/employee/{self.worker.id}").status_code, 200)
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)
        self.assertEqual(statements, [])

    def test_sektor_native_employees_links_shared_accountability_without_sort(self):
        page = self.client.get('/neosektor/manage-employees?area=ebm&view=all')
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'href="/neostaffing/accountability">ACCOUNTABILITY</a>', page.data)
        self.assertNotIn(b'SAVE ATTENDANCE', page.data)
        self.assertEqual(self.client.get('/neostaffing/accountability').status_code, 200)

    def test_formal_history_master_only_idempotent_and_real_dates(self):
        self.fact()
        with self.assertRaises(ValueError):
            discipline.reconcile_formal_history(self.user, self.worker.id, "Warning Letter", "2026-08-01", "Prior issued action", self.today)
        with patch.object(discipline, "user_can_access_app", return_value=True):
            for _ in range(2):
                discipline.reconcile_formal_history(self.user, self.worker.id, "Warning Letter", "2026-08-01", "Prior issued action", self.today)
                db.session.commit()
        self.assertEqual(Resolution.query.count(), 1)
        row = Resolution.query.one()
        self.assertEqual(row.resolved_on, date(2026, 8, 1))
        self.assertIsNone(row.trigger_workday_id)
        self.assertEqual(self.state()["last_formal"], "Warning Letter")

    def test_override_cannot_skip_formal_sequence(self):
        for offset in (4, 3, 2, 1):
            self.fact(offset)
        self.complete_history()
        values = {**self.resolve_values("override"), "action": "Termination", "note": "Cannot bypass progression"}
        with patch.object(discipline, "user_can_access_app", return_value=True), self.assertRaises(ValueError):
            discipline.resolve_obligation(self.user, self.worker.id, values, self.today)
        self.assertEqual(Resolution.query.count(), 0)
        self.assertTrue(self.worker.active)

    def test_finalized_operation_rejects_attendance_even_if_still_selected(self):
        from app.services import neostaffing as staffing
        self.active_sort()
        db.session.add(StaffingAttendanceSummary(sort_date_operation_id=self.operation.id,
            attendance_date=self.operation.sort_date, scope_type="operation", scope_unit_id=self.ramp.id))
        db.session.commit()
        with self.assertRaisesRegex(ValueError, "finalized"):
            staffing.save_attendance({"sort_date_operation_id": str(self.operation.id),
                "work_area_id": str(self.areas["ebm"].id), f"status_{self.worker.id}": "call_in"}, self.user)
        self.assertEqual(StaffingAttendanceOccurrence.query.count(), 0)
