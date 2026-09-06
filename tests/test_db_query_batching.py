"""Synthetic per-invocation SQL budgets; fixture/login work is excluded."""
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta, date
import json
import unittest
from unittest.mock import patch

from sqlalchemy import event

from app.extensions import db
from app.models import StaffingChangeRequest, StaffingNotification, SortDateOperation, SortDateMission, PermissionRule
from app.services import neostaffing_notifications as notifications
from tests import test_neostaffing_notifications as fixtures


@contextmanager
def sql_work():
    counts = Counter(dict.fromkeys(("SELECT", "INSERT", "UPDATE", "DELETE", "COMMIT"), 0))
    counts.select_sql = []
    def statement(_conn, _cursor, sql, _params, _context, _many):
        verb = sql.lstrip().split(None, 1)[0].upper()
        if verb == "WITH":
            verb = "SELECT"
        if verb in counts:
            counts[verb] += 1
        if verb == "SELECT":
            counts.select_sql.append(sql.lower())
    def commit(_conn):
        counts["COMMIT"] += 1
    engine = db.engine
    event.listen(engine, "before_cursor_execute", statement)
    event.listen(engine, "commit", commit)
    try:
        yield counts
    finally:
        event.remove(engine, "before_cursor_execute", statement)
        event.remove(engine, "commit", commit)


class QueryBatchingTest(unittest.TestCase):
    setUp = fixtures.NeoStaffingNotificationsTest.setUp
    tearDown = fixtures.NeoStaffingNotificationsTest.tearDown
    _unit = fixtures.NeoStaffingNotificationsTest._unit
    _person = fixtures.NeoStaffingNotificationsTest._person
    _user = fixtures.NeoStaffingNotificationsTest._user
    _login = fixtures.NeoStaffingNotificationsTest._login

    def test_staffing_query_counts(self):
        results = {}
        for count in (6, 18):
            now = datetime.utcnow()
            requests = []
            for i in range(count):
                person = self._person(f"COUNT-{count}-{i}", "part_time", "Count", str(i))
                requests.append(StaffingChangeRequest(person_id=person.id,
                    submitted_by_user_id=self.submitter_user.id, submitted_by_person_id=self.submitter.id,
                    source_work_area_unit_id=self.source_area.id,
                    destination_work_area_unit_id=self.destination_area.id,
                    routed_approver_person_ids_json=json.dumps([self.source_approver.id, self.destination_approver.id]),
                    submitted_at=now - timedelta(hours=49), status="pending"))
            db.session.add_all(requests)
            db.session.commit()
            for label, user in (("navigation_supervisor", self.source_user), ("navigation_manager", self.manager_user)):
                user.id  # exclude an expired fixture user refresh
                with self.app.test_request_context("/neostaffing"):
                    with sql_work() as metric:
                        state = notifications.notification_navigation_state(user)
                    self.assertEqual(state["actionable_requests"], count)
                    results[f"{count}:{label}"] = dict(metric)
            self._login(self.source_user)
            for label, path in (("ordinary_get", "/neostaffing"), ("requests_get", "/neostaffing/requests"), ("requests_get_unchanged", "/neostaffing/requests"), ("notifications_get", "/neostaffing/notifications")):
                with sql_work() as metric:
                    response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                results[f"{count}:{label}"] = dict(metric)
            # The explicit maintenance service still supports non-page callers.
            StaffingNotification.query.delete()
            db.session.commit()
            for label in ("materialize_new", "materialize_unchanged"):
                with self.app.test_request_context("/neostaffing/notifications"):
                    with sql_work() as metric:
                        result = notifications.maintain_notifications(now)
                        if result["changed"]:
                            db.session.commit()
                self.assertEqual(result["overdue_created"], count * 2 if label == "materialize_new" else 0)
                results[f"{count}:{label}"] = dict(metric)
            StaffingNotification.query.delete()
            StaffingChangeRequest.query.delete()
            db.session.commit()
        print("STAFFING_COUNTS=" + json.dumps(results, sort_keys=True))
        budgets = {"navigation_supervisor": 5, "navigation_manager": 5, "ordinary_get": 18,
                   "requests_get": 26, "requests_get_unchanged": 26, "notifications_get": 14, "materialize_new": 6, "materialize_unchanged": 6}
        for key, metric in results.items():
            label = key.split(":", 1)[1]
            self.assertLessEqual(metric["SELECT"], budgets[label], key)
            self.assertEqual(metric["UPDATE"], 0)
            self.assertEqual(metric["DELETE"], 0)
            if label not in {"requests_get", "materialize_new"}:
                self.assertEqual(metric["COMMIT"], 0)
                self.assertEqual(metric["INSERT"], 0)
        for label in budgets:
            self.assertEqual(results[f"6:{label}"], results[f"18:{label}"])

    def test_portal_and_permission_query_counts(self):
        from app.auth.routes import _portal_app_access_rows, _apply_permission_rule_form
        from app.services.permission_rules import permission_is_configurable
        user = self.source_user
        user.id
        with self.app.test_request_context("/portal"):
            # Warm canonical Gateway initialization, not the measured lookup.
            _portal_app_access_rows(user)
            db.session.flush()
        with self.app.test_request_context("/portal"):
            with sql_work() as metric:
                rows = _portal_app_access_rows(user)
        self.assertEqual([row["app"]["code"] for row in rows], ["neogateway", "neostaffing", "neobid"])
        print("PORTAL_COUNTS=" + json.dumps(metric))
        self.assertLessEqual(metric["SELECT"], 11)
        ids = [r.id for r in PermissionRule.query.all() if permission_is_configurable(r.permission_key)][:18]
        for count in (6, 18):
            db.session.expunge_all()
            values = {"rule_ids": [str(i) for i in ids[:count]]}
            values.update({f"minimum_role_{i}": "simulator" for i in ids[:count]})
            with self.app.test_request_context("/permission-rules", method="POST", data=values):
                with sql_work() as metric:
                    _apply_permission_rule_form()
                    db.session.flush()
            print(f"PERMISSION_COUNTS_{count}=" + json.dumps(metric))
            self.assertEqual(metric["SELECT"], 1)
            self.assertEqual(metric["COMMIT"], 0)
            db.session.rollback()

    def test_alp_and_review_query_counts(self):
        from app.services.alp_import import apply_alp_paste
        from app.neomotherbrain.routes import _persist_alp_unmatched_rows
        from app.models import Gateway
        gateway = Gateway(code="TEST", name="Test", is_active=True)
        db.session.add(gateway)
        db.session.flush()
        for count in (6, 18):
            operation = SortDateOperation(gateway_id=gateway.id, gateway_code="TEST",
                sort_date=date(2026, 9, 6), sort_name=f"test{count}")
            db.session.add(operation)
            db.session.flush()
            for i in range(count):
                db.session.add(SortDateMission(sort_date_operation_id=operation.id, gateway_code="TEST",
                    sort_date=operation.sort_date, sort_name=operation.sort_name, mission_type="arrival",
                    flight_number=f"UPS{i+1000}", origin="SDF", destination="TEST",
                    planned_datetime_utc=datetime(2026, 9, 6, 20)))
            db.session.commit()
            operation.id
            paste = "\n".join(f"06-SEP-2026\t{i+1000}\tSDF\tN{i+100}UP\tA1\tScheduled\t20:00" for i in range(count))
            with sql_work() as metric:
                result = apply_alp_paste(operation, "arrival", paste)
            self.assertEqual(result["applied_count"], count)
            self.assertEqual(sum("from sort_date_missions" in sql for sql in metric.select_sql), 1)
            self.assertLessEqual(metric["SELECT"], count + 1)  # canonical tail-state reads are retained
            print(f"ALP_COUNTS_{count}=" + json.dumps(metric))
            operation.id
            rows = [{"flight_number": str(i+2000), "normalized_flight_number": f"UPS{i+2000}",
                     "airport": "SDF", "tail_number": f"N{i+200}UP", "utc_datetime": datetime(2026, 9, 6, 20),
                     "reason": "No current operation mission match."} for i in range(count)]
            with sql_work() as metric:
                _persist_alp_unmatched_rows(operation, "arrival", {"unmatched_rows": rows})
            print(f"REVIEW_COUNTS_{count}=" + json.dumps(metric))
            self.assertLessEqual(metric["SELECT"], 9)
            db.session.commit()

    def test_scoped_get_recipients_and_retention_remain_correct(self):
        now = datetime.utcnow()
        # Include a second linked account and an unrelated managerial subtree.
        second = self._user("second_ft", "watcher")
        second.employee_id = self.source_approver.employee_id.lower()
        foreign_root = self._unit("sort", "Foreign")
        foreign_area = self._unit("work_area", "Foreign area", foreign_root)
        foreign = self._person("OTHER-MGR", "manager", "Other", "Manager")
        foreign_user = self._user("other_manager", "operator", foreign)
        from app.models import StaffingLeadershipAssignment
        db.session.add(StaffingLeadershipAssignment(person_id=foreign.id, unit_id=foreign_root.id, leadership_level="sort", active=True))
        def add_request(age, area, routed, submitter):
            row = StaffingChangeRequest(person_id=self.target.id, submitted_by_user_id=submitter.id,
                submitted_by_person_id=self.submitter.id, source_work_area_unit_id=area.id,
                routed_approver_person_ids_json=json.dumps(routed), status="pending", submitted_at=now-timedelta(hours=age))
            db.session.add(row)
            return row
        due = add_request(49, self.source_area, [self.source_approver.id], self.submitter_user)
        fresh = add_request(47, self.source_area, [self.source_approver.id], self.submitter_user)
        old = add_request(31*24, foreign_area, [], foreign_user)
        db.session.commit()
        due_id, old_id = due.id, old.id
        for user in (self.source_user, self.manager_user, second):
            self._login(user)
            response = self.client.get("/neostaffing/notifications")
            self.assertEqual(response.status_code, 200)
            with sql_work() as metric:
                repeated = self.client.get("/neostaffing/notifications")
            self.assertEqual(repeated.status_code, 200)
            self.assertEqual(metric["INSERT"] + metric["UPDATE"] + metric["DELETE"] + metric["COMMIT"], 0)
        reminders = StaffingNotification.query.filter_by(notification_type="request_overdue").all()
        self.assertEqual({(r.change_request_id, r.recipient_user_id) for r in reminders},
                         {(due_id, u.id) for u in (self.source_user, self.manager_user, second)})
        self.assertEqual(db.session.get(StaffingChangeRequest, old_id).status, "pending")
        # A visit within the foreign manager's purview performs its own expiry.
        self._login(foreign_user)
        self.assertEqual(self.client.get("/neostaffing/requests").status_code, 200)
        self.assertEqual(db.session.get(StaffingChangeRequest, old_id).status, "completed")
        # Real-time evaluation remains; no scheduler or cached timestamp required.
        with self.app.test_request_context("/neostaffing/notifications"):
            changed = notifications.maintain_notifications(now + timedelta(hours=2), user=self.source_user)
        self.assertEqual(changed["overdue_created"], 1)

    def test_permission_form_order_missing_duplicates_and_rollback(self):
        from app.auth.routes import _apply_permission_rule_form
        from app.services.permission_rules import permission_is_configurable
        rule = next(r for r in PermissionRule.query.all() if permission_is_configurable(r.permission_key))
        rule_id, original = rule.id, rule.minimum_role
        with self.app.test_request_context("/permissions", method="POST", data={
                "rule_ids": [str(rule_id), str(rule_id), "9999999"], f"minimum_role_{rule_id}": "simulator"}):
            _apply_permission_rule_form()
            self.assertEqual(rule.minimum_role, "simulator")
        db.session.rollback()
        self.assertEqual(db.session.get(PermissionRule, rule_id).minimum_role, original)
        with self.app.test_request_context("/permissions", method="POST", data={
                "rule_ids": [str(rule_id), "bad"], f"minimum_role_{rule_id}": "invalid"}):
            with self.assertRaisesRegex(ValueError, "Unsupported minimum role"):
                _apply_permission_rule_form()
        db.session.rollback()

    def test_overdue_batches_preserve_recipients_and_active_filters(self):
        from app.models import StaffingLeadershipAssignment
        now = datetime.utcnow()
        db.session.add_all([StaffingChangeRequest(person_id=self.target.id,
            submitted_by_user_id=self.submitter_user.id,
            source_work_area_unit_id=self.source_area.id,
            routed_approver_person_ids_json=json.dumps([self.source_approver.id]),
            status="pending", submitted_at=now-timedelta(hours=49)) for _ in range(205)])
        db.session.commit()
        with sql_work() as metric:
            result = notifications.maintain_notifications(now)
        self.assertEqual(result["overdue_created"], 410)
        self.assertLessEqual(metric["SELECT"], 11)  # two bounded batches, not 205 lookups
        db.session.commit()
        self.assertEqual(notifications.maintain_notifications(now)["overdue_created"], 0)
        StaffingNotification.query.delete()
        db.session.commit()
        with self.app.test_request_context("/neostaffing/notifications"):
            self.assertEqual(notifications.maintain_notifications(now, user=self.source_user)["overdue_created"], 205)
        db.session.commit()
        StaffingNotification.query.delete()
        self.source_approver.active = False
        StaffingLeadershipAssignment.query.filter_by(person_id=self.manager.id).update({"active": False})
        db.session.commit()
        self.assertEqual(notifications.maintain_notifications(now)["overdue_created"], 0)
        with self.app.test_request_context("/neostaffing"):
            self.assertEqual(notifications.notification_navigation_state(self.manager_user)["actionable_requests"], 0)

    def test_review_map_preserves_duplicate_order_status_and_operation_scope(self):
        from app.models import FlightApiReviewItem, Gateway
        from app.neomotherbrain.routes import _persist_alp_unmatched_rows
        gateway = Gateway(code="REVIEW", name="Review", is_active=True)
        db.session.add(gateway)
        db.session.flush()
        operations = [SortDateOperation(gateway_id=gateway.id, gateway_code="REVIEW", sort_date=date(2026, 9, 6), sort_name=name)
                      for name in ("one", "two")]
        db.session.add_all(operations)
        db.session.commit()
        row = {"flight_number": "2000", "normalized_flight_number": "UPS2000", "airport": "SDF",
               "tail_number": "N200UP", "utc_datetime": datetime(2026, 9, 6, 20),
               "reason": "No current operation mission match."}
        for operation in operations:
            _persist_alp_unmatched_rows(operation, "arrival", {"unmatched_rows": [row, {**row, "line_number": 2}]})
        db.session.commit()
        items = FlightApiReviewItem.query.order_by(FlightApiReviewItem.id).all()
        self.assertEqual(len(items), 2)
        self.assertEqual({item.sort_date_operation_id for item in items}, {op.id for op in operations})
        self.assertTrue(all(json.loads(item.raw_payload)["line_number"] == 2 for item in items))
        for item, status in zip(items, ("accepted", "ignored")):
            item.review_status = status
        db.session.commit()
        for operation in operations:
            _persist_alp_unmatched_rows(operation, "arrival", {"unmatched_rows": [row]})
        self.assertEqual([item.review_status for item in items], ["accepted", "ignored"])

    def test_alp_id_map_skips_foreign_missing_and_wrong_direction_in_original_order(self):
        from app.services.alp_import import apply_alp_paste
        operations = [SortDateOperation(gateway_code=code, sort_date=date(2026, 9, 6), sort_name="night")
                      for code in ("ONE", "TWO")]
        db.session.add_all(operations)
        db.session.flush()
        missions = [SortDateMission(sort_date_operation_id=operation.id, gateway_code=operation.gateway_code,
                    sort_date=operation.sort_date, sort_name=operation.sort_name, mission_type=direction,
                    flight_number="UPS1000", assigned_tail_number="N100UP", origin="SDF", destination="RFD",
                    planned_datetime_utc=datetime(2026, 9, 6, 20))
                    for operation, direction in ((operations[0], "arrival"), (operations[1], "arrival"), (operations[0], "departure"))]
        db.session.add_all(missions)
        db.session.commit()
        def row(mission_id, tail):
            return {"mission_id": mission_id, "tail_number": tail, "utc_datetime": datetime(2026, 9, 6, 20)}
        rows = [row(missions[0].id, "N200UP"), row(missions[1].id, "N300UP"),
                row(missions[2].id, "N400UP"), row(999999, "N500UP"), row(missions[0].id, "N600UP")]
        with patch("app.services.alp_import.preview_alp_paste", return_value={"mission_type": "arrival", "matched_rows": rows}):
            result = apply_alp_paste(operations[0], "arrival", "")
        self.assertEqual(result["applied_rows"], [rows[0], rows[-1]])
        self.assertEqual([mission.assigned_tail_number for mission in missions], ["N600UP", "N100UP", "N100UP"])

    def test_portal_access_batch_preserves_missing_pending_denied_and_user_scope(self):
        from app.auth.routes import _portal_app_access_rows
        from app.models import PortalAppAccess
        access = PortalAppAccess.query.filter_by(user_id=self.source_user.id, app_code="neostaffing").one()
        for status in ("approved", "pending", "denied"):
            access.status = status
            db.session.commit()
            with self.app.test_request_context("/portal"):
                rows = {row["app"]["code"]: row["access"] for row in _portal_app_access_rows(self.source_user)}
            self.assertEqual(rows["neostaffing"].status, status)
            self.assertEqual(rows["neostaffing"].user_id, self.source_user.id)
            self.assertIsNone(rows["neobid"])
        db.session.delete(access)
        db.session.commit()
        with self.app.test_request_context("/portal"):
            rows = {row["app"]["code"]: row["access"] for row in _portal_app_access_rows(self.source_user)}
        self.assertIsNone(rows["neostaffing"])
