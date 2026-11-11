from tests.css_contracts import stylesheet_source
from datetime import date, datetime, timedelta
from html import unescape
import re
from pathlib import Path
import unittest

from flask import g
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError

from app import create_app
from app.extensions import db
from app.models import (
    PermissionRule,
    PortalAppAccess,
    SortDateOperation,
    StaffingDailyAttendance,
    StaffingPerson,
    StaffingLeadershipAssignment,
    StaffingUnit,
    StaffingWorkAssignment,
    User,
)
from app.services import neostaffing as staffing_service
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.password_policy import set_user_password
from app.services.permission_rules import ensure_default_permission_rules


class NeoStaffingDailyAttendanceTest(unittest.TestCase):
    OPERATION_DATE = date(2026, 8, 15)

    def setUp(self):
        config = type(
            "NeoStaffingDailyAttendanceConfig",
            (),
            {
                "SECRET_KEY": "test",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
                "CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE": datetime(2026, 8, 15, 21, 0),
            },
        )
        self.app = create_app(config)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.client = self.app.test_client()
        self.gateway = ensure_default_gateway_and_nodes()
        ensure_default_permission_rules()
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_missing_night_operation_is_disabled_and_does_not_create_one(self):
        self._hierarchy()
        before = SortDateOperation.query.count()

        context = staffing_service.attendance_context()

        self.assertFalse(context["ready"])
        self.assertEqual(context["message"], "NIGHT SORT HAS NOT BEEN CREATED YET.")
        self.assertEqual(context["scope_tree"], [])
        self.assertEqual(context["scope_here_counts"], {})
        self.assertEqual(SortDateOperation.query.count(), before)
        self.assertFalse(db.session.new)

    def test_scope_here_rollups_are_distinct_bounded_and_night_only(self):
        from app.services.neostaffing_attendance_authority import attendance_authority

        sort, operation_unit, department, area, outside = self._hierarchy()
        second = staffing_service.create_unit({"unit_type": "work_area", "name": "Second Door", "parent_id": department.id})
        empty = staffing_service.create_unit({"unit_type": "work_area", "name": "Empty Door", "parent_id": department.id})
        operation = self._night_operation()
        overlap = self._person("HERE-OVERLAP", area)
        other = self._person("HERE-OUTSIDE", outside)
        absent = self._person("HERE-ABSENT", second)
        yesterday = self._person("HERE-YESTERDAY", area)
        wrong_sort = self._person("HERE-WRONG-SORT", area)
        day_operation = SortDateOperation(gateway_id=self.gateway.id, gateway_code=self.gateway.code,
                                          sort_date=operation.sort_date, sort_name="day")
        db.session.add(day_operation)
        db.session.flush()
        self._person("HERE-UNMARKED", second)
        # Model a legacy overlap without weakening today's assignment guard.
        db.session.execute(StaffingWorkAssignment.__table__.insert().values(
            person_id=overlap.id, work_area_unit_id=second.id, active=True,
        ))
        for person, status, day, linked_operation in (
            (overlap, "here", operation.sort_date, operation.id),
            (other, "here", operation.sort_date, None),  # Canonical legacy row.
            (absent, "vacation", operation.sort_date, operation.id),
            (yesterday, "here", operation.sort_date - timedelta(days=1), None),
            (wrong_sort, "here", operation.sort_date, day_operation.id),
        ):
            db.session.add(StaffingDailyAttendance(person_id=person.id, status=status, sort_unit_id=sort.id,
                attendance_date=day, sort_date_operation_id=linked_operation))
        manager = self._user("here-rollups-manager", "operator")
        limited = self._user("here-rollups-limited", "operator")
        leader = StaffingPerson.query.filter_by(employee_id=limited.employee_id).one()
        leader.classification = "full_time_supervisor"
        leadership = StaffingLeadershipAssignment.query.filter_by(person_id=leader.id).one()
        leadership.unit = department
        leadership.leadership_level = "department"
        db.session.commit()

        hierarchy = staffing_service._daily_attendance_hierarchy()
        authority = attendance_authority(manager, hierarchy)
        read_counts = staffing_service._daily_attendance_scope_here_counts
        db.session.refresh(operation)
        self.assertEqual(self._count_selects(lambda: read_counts(operation, sort, hierarchy, authority.work_area_ids)), 1)
        counts = {sort.id: 2, operation_unit.id: 2, department.id: 1, area.id: 1,
                  second.id: 1, empty.id: 0, outside.id: 1}
        for filters in ({"sort_id": sort.id}, {"work_area_id": area.id}, {"work_area_ids": [area.id, second.id]}):
            context = staffing_service.attendance_context(filters, manager, authority=authority)
            self.assertEqual(context["scope_here_counts"], counts)
            self.assertEqual(context["summary"]["here"], 2 if "sort_id" in filters else 1)
        limited_authority = attendance_authority(limited, hierarchy)
        restricted = staffing_service.attendance_context({"sort_id": sort.id}, limited, authority=limited_authority)
        self.assertEqual(restricted["scope_here_counts"], {sort.id: 1, operation_unit.id: 1, department.id: 1,
                         area.id: 1, second.id: 1, empty.id: 0})
        self.assertEqual(read_counts(SortDateOperation(sort_name="day"), sort, hierarchy, authority.work_area_ids), {})

    def test_missing_night_operation_route_performs_no_persistent_write(self):
        self._hierarchy()
        viewer = self._user("missing_operation_viewer", "watcher")
        db.session.commit()
        self._login(viewer.username)
        dml = []

        def capture_dml(_connection, _cursor, statement, _parameters, _context, _many):
            verb = statement.lstrip().split(None, 1)[0].upper()
            if verb in {"INSERT", "UPDATE", "DELETE"}:
                dml.append(statement)

        event.listen(db.engine, "before_cursor_execute", capture_dml)
        try:
            response = self.client.get("/neostaffing/attendance")
        finally:
            event.remove(db.engine, "before_cursor_execute", capture_dml)

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"NIGHT SORT HAS NOT BEEN CREATED YET.", response.data)
        self.assertEqual(dml, [])
        self.assertEqual(SortDateOperation.query.count(), 0)

    def test_existing_current_night_operation_and_its_date_are_authoritative(self):
        self._hierarchy()
        operation = self._night_operation()

        context = staffing_service.attendance_context(
            {"attendance_date": "1999-01-01"}
        )

        self.assertTrue(context["ready"])
        self.assertIs(context["sort_date_operation"], operation)
        self.assertEqual(context["attendance_date"], operation.sort_date)

    def test_ambiguous_staffing_sort_mapping_disables_attendance(self):
        self._hierarchy(sort_name="Night")
        staffing_service.create_unit({"unit_type": "sort", "name": "Night Sort"})
        self._night_operation()

        context = staffing_service.attendance_context()

        self.assertFalse(context["ready"])
        self.assertIn("Multiple active NeoStaffing Sorts", context["message"])

    def test_unmarked_summary_rollups_and_placement_snapshots(self):
        staffing_sort, operation_unit, department, nested_area, direct_area = self._hierarchy()
        operation = self._night_operation()
        nested = self._person("DS100", nested_area)
        direct = self._person("DS101", direct_area)
        recorder = self._user("snapshot_recorder", "operator")
        db.session.commit()

        before = staffing_service.attendance_context(
            {"operation_id": str(operation_unit.id)}
        )
        self.assertEqual(before["summary"]["total_roster"], 2)
        self.assertEqual(before["summary"]["here"], 0)
        self.assertEqual(before["summary"]["absent"], 0)
        self.assertEqual(before["summary"]["unmarked"], 2)
        self.assertEqual({row["status"] for row in before["rows"]}, {""})

        staffing_service.save_attendance(
            {
                "sort_date_operation_id": str(operation.id),
                "attendance_date": "1999-01-01",
                "sort_id": str(staffing_sort.id),
                "operation_id": str(operation_unit.id),
                f"status_{nested.id}": "here",
                f"status_{direct.id}": "call_in",
            },
            recorder,
        )
        db.session.commit()

        records = {
            record.person_id: record for record in StaffingDailyAttendance.query.all()
        }
        self.assertEqual(records[nested.id].attendance_date, operation.sort_date)
        self.assertEqual(records[nested.id].sort_date_operation_id, operation.id)
        self.assertEqual(records[nested.id].work_area_unit_id, nested_area.id)
        self.assertEqual(records[nested.id].department_unit_id, department.id)
        self.assertEqual(records[nested.id].operation_unit_id, operation_unit.id)
        self.assertEqual(records[direct.id].work_area_unit_id, direct_area.id)
        self.assertIsNone(records[direct.id].department_unit_id)
        self.assertEqual(records[direct.id].operation_unit_id, operation_unit.id)

        after = staffing_service.attendance_context(
            {"operation_id": str(operation_unit.id)}
        )
        self.assertEqual(after["summary"]["here"], 1)
        self.assertEqual(after["summary"]["absent"], 1)
        self.assertEqual(after["summary"]["unmarked"], 0)
        self.assertEqual(after["rollups"]["department"][0]["total_roster"], 1)
        self.assertEqual(after["rollups"]["operation"][0]["total_roster"], 2)

    def test_status_edit_preserves_original_placement_snapshot(self):
        staffing_sort, operation_unit, department, first_area, direct_area = self._hierarchy()
        operation = self._night_operation()
        person = self._person("HS100", first_area)
        recorder = self._user("history_recorder", "operator")
        db.session.commit()
        payload = {
            "sort_date_operation_id": str(operation.id),
            "sort_id": str(staffing_sort.id),
            "operation_id": str(operation_unit.id),
            f"status_{person.id}": "here",
        }
        staffing_service.save_attendance(payload, recorder)
        db.session.commit()

        staffing_service.assign_work_area(person, direct_area)
        staffing_service.save_attendance(
            {**payload, f"status_{person.id}": "vacation"},
            recorder,
        )
        db.session.commit()

        record = StaffingDailyAttendance.query.filter_by(person_id=person.id).one()
        self.assertEqual(record.status, "vacation")
        self.assertEqual(record.work_area_unit_id, first_area.id)
        self.assertEqual(record.department_unit_id, department.id)
        self.assertEqual(record.operation_unit_id, operation_unit.id)

        direct_person = self._person("HS101", direct_area)
        staffing_service.save_attendance(
            {
                "sort_date_operation_id": str(operation.id),
                "sort_id": str(staffing_sort.id),
                "operation_id": str(operation_unit.id),
                f"status_{direct_person.id}": "here",
            },
            recorder,
        )
        db.session.flush()
        staffing_service.assign_work_area(direct_person, first_area)
        staffing_service.save_attendance(
            {
                "sort_date_operation_id": str(operation.id),
                "sort_id": str(staffing_sort.id),
                "operation_id": str(operation_unit.id),
                f"status_{direct_person.id}": "call_in",
            },
            recorder,
        )
        direct_record = StaffingDailyAttendance.query.filter_by(
            person_id=direct_person.id
        ).one()
        self.assertEqual(direct_record.work_area_unit_id, direct_area.id)
        self.assertIsNone(direct_record.department_unit_id)

    def test_all_here_and_clear_to_unmarked_are_operation_scoped(self):
        staffing_sort, operation_unit, _department, work_area, _direct_area = self._hierarchy()
        operation = self._night_operation()
        first = self._person("AH200", work_area)
        second = self._person("AH201", work_area)
        recorder = self._user("all_here_recorder", "operator")
        historical = StaffingDailyAttendance(
            person_id=first.id,
            attendance_date=operation.sort_date - timedelta(days=1),
            sort_unit_id=staffing_sort.id,
            work_area_unit_id=work_area.id,
            status="vacation",
        )
        db.session.add(historical)
        db.session.commit()

        saved = staffing_service.save_attendance(
            {
                "sort_date_operation_id": str(operation.id),
                "sort_id": str(staffing_sort.id),
                "work_area_id": str(work_area.id),
                "bulk_status": "here",
            },
            recorder,
        )
        db.session.commit()
        self.assertEqual(saved, 2)
        self.assertEqual(
            StaffingDailyAttendance.query.filter_by(
                sort_date_operation_id=operation.id,
                status="here",
            ).count(),
            2,
        )

        cleared = staffing_service.save_attendance(
            {
                "sort_date_operation_id": str(operation.id),
                "sort_id": str(staffing_sort.id),
                "work_area_id": str(work_area.id),
                f"status_{first.id}": "",
            },
            recorder,
            return_rows=True,
            return_scope_here=True,
        )
        db.session.commit()
        self.assertEqual(cleared["scope_here"]["counts"][work_area.id], 1)
        self.assertEqual(cleared["scope_here"]["counts"][staffing_sort.id], 1)
        self.assertIsNone(
            StaffingDailyAttendance.query.filter_by(
                person_id=first.id,
                sort_date_operation_id=operation.id,
            ).first()
        )
        self.assertIsNotNone(db.session.get(StaffingDailyAttendance, historical.id))

    def test_async_save_returns_fresh_original_and_preserves_unmarked_note(self):
        sort, _, _, area, _ = self._hierarchy()
        operation = self._night_operation()
        person = self._person("ASY100", area)
        supervisor = self._user("async_note_supervisor", "operator")
        db.session.commit()
        self._login(supervisor.username)
        path = f"/neostaffing/attendance?work_area_id={area.id}"
        form = {"sort_date_operation_id": operation.id, "sort_id": sort.id, "work_area_id": area.id,
                f"status_{person.id}": "", f"note_{person.id}": "  Keep this note  ",
                f"original_{person.id}": self._attendance_original_from_page(path, person.id)}
        note = self.client.post("/neostaffing/attendance/save", data=form)
        self.assertEqual(note.status_code, 200)
        saved = note.json["rows"][0]
        self.assertEqual((saved["status"], saved["note"]), ("", "Keep this note"))
        self.assertEqual(StaffingDailyAttendance.query.count(), 0)
        self.assertIn(b'Keep this note', self.client.get(path).data)

        form[f"status_{person.id}"] = "here"
        stale = self.client.post("/neostaffing/attendance/save", data=form)
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json["row"], saved)
        form[f"original_{person.id}"] = saved["original"]
        form.pop(f"note_{person.id}")
        marked = self.client.post("/neostaffing/attendance/save", data=form)
        self.assertEqual(marked.status_code, 200)
        saved = marked.json["rows"][0]
        self.assertEqual((saved["status"], saved["note"]), ("here", "Keep this note"))
        form.update({f"original_{person.id}": saved["original"], f"status_{person.id}": ""})
        cleared = self.client.post("/neostaffing/attendance/save", data=form)
        self.assertEqual(cleared.status_code, 200)
        self.assertEqual(cleared.json["rows"][0]["note"], "Keep this note")
        self.assertEqual(StaffingDailyAttendance.query.count(), 0)

    def test_multi_area_scope_post_autosave_all_here_and_reject_outside(self):
        from werkzeug.datastructures import MultiDict

        sort, _, department, first_area, outside_area = self._hierarchy()
        second_area = staffing_service.create_unit({
            "unit_type": "work_area", "name": "Second Door",
            "parent_id": department.id,
        })
        operation = self._night_operation()
        marked = self._person("SCOPE-MARKED", first_area)
        eligible_one = self._person("SCOPE-FIRST", first_area)
        eligible_two = self._person("SCOPE-SECOND", second_area)
        outside = self._person("SCOPE-OUT", outside_area)
        operator = self._user("multi_scope_writer", "operator")
        db.session.commit()
        self._login(operator.username)

        path = (f"/neostaffing/attendance?sort_id={sort.id}"
                f"&department_id={department.id}")
        common = [
            ("sort_date_operation_id", str(operation.id)),
            ("sort_id", str(sort.id)),
            ("department_id", str(department.id)),
            ("work_area_ids", str(first_area.id)),
            ("work_area_ids", str(second_area.id)),
        ]
        self.assertEqual(
            staffing_service._submitted_attendance_work_area_ids(MultiDict(common)),
            [str(first_area.id), str(second_area.id)],
        )
        self.assertEqual(
            staffing_service._submitted_attendance_work_area_ids({
                "work_area_ids": [str(first_area.id), str(second_area.id)]}),
            [str(first_area.id), str(second_area.id)],
        )

        # A row in the SECOND selected area previously failed HTTP 400 because
        # a MultiDict .get("work_area_ids") discarded every ID except the first.
        second_original = self._attendance_original_from_page(path, eligible_two.id)
        note_only = self.client.post("/neostaffing/attendance/save", data=MultiDict(
            common + [(f"status_{eligible_two.id}", ""),
                      (f"note_{eligible_two.id}", "Supervisor note"),
                      (f"original_{eligible_two.id}", second_original)]))
        self.assertEqual(note_only.status_code, 200, note_only.get_data(as_text=True))
        self.assertEqual(note_only.json["rows"][0]["note"], "Supervisor note")

        # Marking someone in the FIRST area must also work, without rewriting
        # the second person's note or changing anyone outside the scope.
        first_original = self._attendance_original_from_page(path, marked.id)
        first = self.client.post("/neostaffing/attendance/save", data=MultiDict(
            common + [(f"status_{marked.id}", "vacation"),
                      (f"note_{marked.id}", "Previously approved"),
                      (f"original_{marked.id}", first_original)]))
        self.assertEqual(first.status_code, 200, first.get_data(as_text=True))

        bulk = [*common, ("bulk_status", "here")]
        for person in (eligible_one, eligible_two):
            bulk.extend([
                (f"status_{person.id}", ""),
                (f"original_{person.id}",
                 self._attendance_original_from_page(path, person.id)),
            ])
        response = self.client.post("/neostaffing/attendance/save", data=MultiDict(bulk))
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(response.json["saved"], 2)
        self.assertEqual({
            x.person_id: (x.status, x.note)
            for x in StaffingDailyAttendance.query.all()
        }, {
            marked.id: ("vacation", "Previously approved"),
            eligible_one.id: ("here", None),
            eligible_two.id: ("here", "Supervisor note"),
        })
        self.assertIsNone(StaffingDailyAttendance.query.filter_by(person_id=outside.id).first())

        # Adding an unsanctioned person to the same multi-area request still fails
        # before any mutation. Do not relax scope authorization for the fix.
        forged = [*common, ("bulk_status", "here"),
                  (f"status_{outside.id}", ""),
                  (f"original_{outside.id}",
                   self._attendance_original_from_page(
                       f"/neostaffing/attendance?sort_id={sort.id}", outside.id))]
        rejected = self.client.post("/neostaffing/attendance/save", data=MultiDict(forged))
        self.assertEqual(rejected.status_code, 400)
        self.assertIn("outside the selected scope", rejected.json["error"])

    def test_async_bulk_uses_signed_scope_and_skips_newer_statuses_and_notes(self):
        sort, _, department, area, outside_area = self._hierarchy()
        operation = self._night_operation()
        unmarked, raced, noted, marked, outside = [self._person(f"BULK{index}", location)
            for index, location in enumerate([area, area, area, area, outside_area])]
        supervisor = self._user("bulk_scope_supervisor", "operator")
        manager = StaffingPerson.query.filter_by(employee_id=supervisor.employee_id).one()
        manager.classification = "full_time_supervisor"
        leadership = StaffingLeadershipAssignment.query.filter_by(person_id=manager.id).one()
        leadership.unit = department
        leadership.leadership_level = "department"
        writer = self._user("bulk_other_supervisor", "operator")
        scope = {"sort_date_operation_id": operation.id, "sort_id": sort.id, "work_area_id": area.id}
        staffing_service.save_attendance({**scope, f"status_{marked.id}": "vacation", f"note_{marked.id}": "Approved"}, writer)
        db.session.commit()
        self._login(supervisor.username)
        path = f"/neostaffing/attendance?sort_id={sort.id}"
        batch = {**scope, "bulk_status": "here"}
        for person in (unmarked, raced, noted, marked):
            batch[f"status_{person.id}"] = "here"
            batch[f"note_{person.id}"] = "must not be applied"
            batch[f"original_{person.id}"] = self._attendance_original_from_page(path, person.id)

        staffing_service.save_attendance({**scope, f"status_{raced.id}": "call_in", f"note_{raced.id}": "Called"}, writer)
        staffing_service.save_attendance({**scope, f"status_{noted.id}": "", f"note_{noted.id}": "New note"}, writer)
        db.session.commit()
        forged = {**batch, "work_area_id": "", f"status_{outside.id}": "",
                  f"original_{outside.id}": self._attendance_original_from_page(path, outside.id)}
        self.assertEqual(self.client.post("/neostaffing/attendance/save", data=forged).status_code, 400)
        self.assertEqual(StaffingDailyAttendance.query.count(), 2)

        response = self.client.post("/neostaffing/attendance/save", data=batch)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["saved"], 1)
        self.assertEqual({row["person_id"]: (row["status"], row["note"]) for row in response.json["rows"]},
                         {unmarked.id: ("here", ""), raced.id: ("call_in", "Called"), noted.id: ("", "New note")})
        self.assertEqual({row.person_id: (row.status, row.note) for row in StaffingDailyAttendance.query.all()},
                         {unmarked.id: ("here", None), raced.id: ("call_in", "Called"), marked.id: ("vacation", "Approved")})
        self.assertEqual(self.client.post("/neostaffing/attendance/save", data=batch).json["saved"], 0)

    def _attendance_original_from_page(self, path, person_id):
        page = self.client.get(path).get_data(as_text=True)
        return unescape(re.search(fr'name="original_{person_id}" value="([^"]+)"', page)[1])

    def test_legacy_null_operation_record_remains_readable(self):
        staffing_sort, _operation_unit, department, work_area, _direct = self._hierarchy()
        operation = self._night_operation()
        person = self._person("LG100", work_area)
        db.session.add(
            StaffingDailyAttendance(
                person_id=person.id,
                attendance_date=operation.sort_date,
                sort_unit_id=staffing_sort.id,
                work_area_unit_id=work_area.id,
                department_unit_id=department.id,
                sort_date_operation_id=None,
                status="call_in",
            )
        )
        db.session.commit()

        context = staffing_service.attendance_context(
            {"work_area_id": str(work_area.id)}
        )

        self.assertEqual(context["rows"][0]["status"], "call_in")
        self.assertEqual(context["summary"]["absent"], 1)

    def test_post_rejects_noncurrent_operation_and_out_of_scope_person(self):
        staffing_sort, operation_unit, _department, work_area, direct_area = self._hierarchy()
        operation = self._night_operation()
        current_person = self._person("SP100", work_area)
        outside_person = self._person("SP101", direct_area)
        recorder = self._user("spoof_recorder", "operator")
        stale_operation = SortDateOperation(
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
            sort_date=operation.sort_date - timedelta(days=1),
            sort_name="night",
        )
        db.session.add(stale_operation)
        db.session.commit()

        with self.assertRaisesRegex(ValueError, "no longer current"):
            staffing_service.save_attendance(
                {
                    "sort_date_operation_id": str(stale_operation.id),
                    "sort_id": str(staffing_sort.id),
                    f"status_{current_person.id}": "here",
                },
                recorder,
            )
        with self.assertRaisesRegex(ValueError, "outside the selected scope"):
            staffing_service.save_attendance(
                {
                    "sort_date_operation_id": str(operation.id),
                    "sort_id": str(staffing_sort.id),
                    "work_area_id": str(work_area.id),
                    f"status_{outside_person.id}": "here",
                },
                recorder,
            )
        self.assertEqual(StaffingDailyAttendance.query.count(), 0)

    def test_management_authority_controls_cross_area_write_dynamically(self):
        staffing_sort, _operation_unit, department, first_area, _direct_area = self._hierarchy()
        second_area = staffing_service.create_unit(
            {
                "unit_type": "work_area",
                "name": "WBM",
                "parent_id": department.id,
            }
        )
        operation = self._night_operation()
        person = self._person("XA100", second_area)
        operator = self._user("cross_area_operator", "operator")
        db.session.commit()
        self._login(operator.username)

        page = self.client.get(
            f"/neostaffing/attendance?work_area_id={second_area.id}"
        )
        original = re.search(fr'name="original_{person.id}" value="([^"]+)"', page.get_data(as_text=True))
        saved = self.client.post(
            "/neostaffing/attendance",
            data={
                "sort_date_operation_id": str(operation.id),
                "sort_id": str(staffing_sort.id),
                "work_area_id": str(second_area.id),
                f"status_{person.id}": "here",
                f"original_{person.id}": unescape(original[1]),
            },
            follow_redirects=True,
        )
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"XA100", page.data)
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(StaffingDailyAttendance.query.filter_by(person_id=person.id).count(), 1)

        record = StaffingDailyAttendance.query.filter_by(person_id=person.id).one()
        db.session.delete(record)
        rule = PermissionRule.query.filter_by(
            permission_key="neostaffing.attendance.take"
        ).one()
        rule.minimum_role = "simulator"
        # Node/application permission rules no longer grant attendance authority.
        supervisor = StaffingPerson.query.filter_by(employee_id=operator.employee_id).one()
        supervisor.active = False
        db.session.commit()
        denied = self.client.post(
            "/neostaffing/attendance",
            data={
                "sort_date_operation_id": str(operation.id),
                "sort_id": str(staffing_sort.id),
                "work_area_id": str(second_area.id),
                f"status_{person.id}": "here",
            },
            follow_redirects=True,
        )
        self.assertIn(b"management attendance authority", denied.data)
        self.assertEqual(StaffingDailyAttendance.query.filter_by(person_id=person.id).count(), 0)
        self.assertNotEqual(first_area.id, second_area.id)

    def test_existing_attendance_unique_contract_is_unchanged(self):
        staffing_sort, _operation_unit, _department, work_area, _direct = self._hierarchy()
        operation = self._night_operation()
        person = self._person("UQ100", work_area)
        db.session.add_all(
            [
                StaffingDailyAttendance(
                    person_id=person.id,
                    attendance_date=operation.sort_date,
                    sort_unit_id=staffing_sort.id,
                    sort_date_operation_id=operation.id,
                    status="here",
                ),
                StaffingDailyAttendance(
                    person_id=person.id,
                    attendance_date=operation.sort_date,
                    sort_unit_id=staffing_sort.id,
                    sort_date_operation_id=None,
                    status="call_in",
                ),
            ]
        )
        with self.assertRaises(IntegrityError):
            db.session.commit()
        db.session.rollback()

    def test_large_roster_context_and_save_use_bounded_selects(self):
        staffing_sort, operation_unit, _department, work_area, _direct = self._hierarchy()
        operation = self._night_operation()
        recorder = self._user("large_batch_recorder", "operator")
        people = []
        assignments = []
        for index in range(1500):
            person = StaffingPerson(
                employee_id=f"L{index:04d}",
                first_name="Large",
                last_name=f"Roster {index:04d}",
                seniority_date=date(2020, 1, 1),
                classification="part_time",
                active=True,
            )
            people.append(person)
            assignments.append(
                StaffingWorkAssignment(
                    person=person,
                    work_area=work_area,
                    active=True,
                )
            )
        db.session.add_all(people + assignments)
        db.session.commit()
        db.session.expire_all()

        context_selects = self._count_selects(
            lambda: staffing_service.attendance_context(
                {"operation_id": str(operation_unit.id)}
            )
        )
        self.assertLessEqual(context_selects, 10)

        save_selects = self._count_selects(
            lambda: staffing_service.save_attendance(
                {
                    "sort_date_operation_id": str(operation.id),
                    "sort_id": str(staffing_sort.id),
                    "operation_id": str(operation_unit.id),
                    "bulk_status": "here",
                },
                recorder,
            )
        )
        # Original ten reads plus one set-based leadership authorization and
        # one post-lock assignment revalidation, independent of roster size.
        self.assertLessEqual(save_selects, 12)
        db.session.commit()
        self.assertEqual(
            StaffingDailyAttendance.query.filter_by(
                sort_date_operation_id=operation.id,
                status="here",
            ).count(),
            1500,
        )

    def test_attendance_route_renders_full_width_console_and_multi_area_scope(self):
        staffing_sort, operation_unit, department, nested_area, direct_area = self._hierarchy()
        self._night_operation()
        nested = self._person("UI100", nested_area)
        direct = self._person("UI101", direct_area)
        operator = self._user("attendance_console_operator", "operator")
        db.session.commit()
        self._login(operator.username)

        response = self.client.get(
            "/neostaffing/attendance",
            query_string=[
                ("work_area_ids", str(nested_area.id)),
                ("work_area_ids", str(direct_area.id)),
            ],
        )
        page = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn('class="neostaffing-attendance-console theme-staffing"', page)
        self.assertIn('data-attendance-tree-scroll', page)
        self.assertIn('data-attendance-tree', page)
        self.assertIn('data-attendance-tree-toggle', page)
        self.assertIn('neostaffing.attendance.hierarchy.v1', page)
        self.assertIn("2 AREAS", page)
        self.assertIn(nested_area.name, page)
        self.assertIn(direct_area.name, page)
        self.assertIn(f'name="work_area_ids" value="{nested_area.id}"', page)
        self.assertIn(f'name="work_area_ids" value="{direct_area.id}"', page)
        self.assertIn(nested.employee_id, page)
        self.assertIn(direct.employee_id, page)
        self.assertIn("Attendance Area", page)
        for label in (
            "On Payroll", "Working", "Unmarked", "Called In", "No Call",
            "Scheduled Off", "Vacation", "Opt Day", "Anniversary Day",
            "Disability", "Work Comp", "Funeral", "Jury", "FMLA",
            "Military", "Personal Leave", "Cleared",
        ):
            self.assertIn(label, page)
        self.assertNotIn("neostaffing-dashboard-shell", page)
        self.assertNotIn("neostaffing-data-card", page)

        css = stylesheet_source()
        attendance_css = css.split(
            "/* NeoStaffing Attendance full-width operations console. */", 1
        )[1]
        self.assertIn("grid-template-columns: minmax(0, 1fr)", attendance_css)
        self.assertNotIn("body:has(.neostaffing-attendance-console) { overflow-x", css)
        self.assertNotIn("min-width: 720px; table-layout: fixed", attendance_css)
        self.assertIn("min-height: 27px", attendance_css)
        self.assertIn('data-attendance-search', page)
        self.assertIn('ALL HERE — CURRENT SCOPE', page)
        self.assertNotIn('SAVE ATTENDANCE', page)

        context = staffing_service.attendance_context(
            {"work_area_ids": [str(nested_area.id), str(direct_area.id)]}
        )
        self.assertEqual(
            [area.id for area in context["selected_work_areas"]],
            sorted([nested_area.id, direct_area.id]),
        )
        self.assertEqual(context["scope_tree"][0]["unit"].id, staffing_sort.id)
        self.assertEqual(context["scope_tree"][0]["children"][0]["unit"].id, operation_unit.id)
        self.assertEqual(
            context["scope_tree"][0]["children"][0]["children"][0]["unit"].id,
            department.id,
        )

    def _count_selects(self, action):
        count = 0

        def count_statement(_connection, _cursor, statement, _parameters, _context, _many):
            nonlocal count
            if statement.lstrip().upper().startswith("SELECT"):
                count += 1

        event.listen(db.engine, "before_cursor_execute", count_statement)
        try:
            action()
        finally:
            event.remove(db.engine, "before_cursor_execute", count_statement)
        return count

    def _hierarchy(self, sort_name="Night"):
        staffing_sort = staffing_service.create_unit(
            {"unit_type": "sort", "name": sort_name}
        )
        operation = staffing_service.create_unit(
            {
                "unit_type": "operation",
                "name": "Hub Operations",
                "parent_id": staffing_sort.id,
            }
        )
        department = staffing_service.create_unit(
            {
                "unit_type": "department",
                "name": "Outbound",
                "parent_id": operation.id,
            }
        )
        work_area = staffing_service.create_unit(
            {
                "unit_type": "work_area",
                "name": "EBM",
                "parent_id": department.id,
            }
        )
        direct_area = staffing_service.create_unit(
            {
                "unit_type": "work_area",
                "name": "Load Planning",
                "parent_id": operation.id,
            }
        )
        return staffing_sort, operation, department, work_area, direct_area

    def _night_operation(self):
        operation = SortDateOperation(
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
            sort_date=self.OPERATION_DATE,
            sort_name="night",
        )
        db.session.add(operation)
        db.session.flush()
        return operation

    def _person(self, employee_id, work_area):
        person = staffing_service.create_person(
            {
                "employee_id": employee_id,
                "first_name": "Test",
                "last_name": employee_id,
                "seniority_date": "2020-01-01",
                "classification": "part_time",
            }
        )
        staffing_service.assign_work_area(person, work_area)
        return person

    def _user(self, username, role):
        user = User(
            username=username,
            email=f"{username}@example.com",
            first_name="Staffing",
            last_name="User",
            full_name="Staffing User",
            employee_id=f"EMP-{username}",
            role="watcher",
            is_active=True,
            email_verified_at=datetime.utcnow(),
        )
        set_user_password(user, "TestPassword123!")
        db.session.add(user)
        db.session.flush()
        db.session.add(
            PortalAppAccess(
                user_id=user.id,
                app_code="neostaffing",
                status="approved",
                role=role,
                is_active=True,
                approved_at=datetime.utcnow(),
            )
        )
        db.session.flush()
        if role != "watcher":
            manager = staffing_service.create_person({
                "employee_id": user.employee_id, "first_name": "Manager", "last_name": username,
                "seniority_date": "2020-01-01", "classification": "division_manager",
            })
            for scope in StaffingUnit.query.filter_by(unit_type="sort", active=True).all():
                db.session.add(StaffingLeadershipAssignment(person=manager, unit=scope, leadership_level="sort"))
            db.session.flush()
        return user

    def _login(self, username):
        g.pop("_login_user", None)
        return self.client.post(
            "/login",
            data={"username": username, "password": "TestPassword123!"},
            follow_redirects=False,
        )


if __name__ == "__main__":
    unittest.main()
