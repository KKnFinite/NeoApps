from datetime import date, datetime, timedelta
import json
import unittest
from unittest.mock import patch

from flask import g
from sqlalchemy import event

from app import create_app
from app.extensions import db
from app.models import (
    PortalAppAccess,
    StaffingChangeRequest,
    StaffingLeadershipAssignment,
    StaffingNotification,
    StaffingPerson,
    StaffingUnit,
    StaffingWorkAssignment,
    User,
)
from app.services import neostaffing_change_requests as request_service
from app.services import neostaffing_notifications as notification_service
from app.services.password_policy import set_user_password
from app.services.permission_rules import ensure_default_permission_rules


class NeoStaffingNotificationsTest(unittest.TestCase):
    def setUp(self):
        config = type(
            "NeoStaffingNotificationsConfig",
            (),
            {
                "SECRET_KEY": "test",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            },
        )
        self.app = create_app(config)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        ensure_default_permission_rules()
        self.client = self.app.test_client()

        self.sort = self._unit("sort", "Night")
        self.operation = self._unit("operation", "Unload", self.sort)
        self.source_department = self._unit(
            "department", "Source Department", self.operation
        )
        self.destination_department = self._unit(
            "department", "Destination Department", self.operation
        )
        self.source_area = self._unit(
            "work_area", "Source Area", self.source_department
        )
        self.destination_area = self._unit(
            "work_area", "Destination Area", self.destination_department
        )

        self.submitter = self._person(
            "PT-100", "part_time_supervisor", "Pat", "Submitter"
        )
        self.source_approver = self._person(
            "FT-100", "full_time_supervisor", "Fran", "Source"
        )
        self.destination_approver = self._person(
            "FT-200", "full_time_supervisor", "Drew", "Destination"
        )
        self.manager = self._person("MGR-100", "manager", "Morgan", "Manager")
        self.division_manager = self._person(
            "DIV-100", "division_manager", "Dana", "Division"
        )
        self.target = self._person("EMP-100", "part_time", "Taylor", "Employee")
        db.session.add_all(
            [
                StaffingWorkAssignment(
                    person=self.target,
                    work_area=self.source_area,
                    active=True,
                ),
                StaffingLeadershipAssignment(
                    person=self.submitter,
                    unit=self.source_area,
                    leadership_level="work_area",
                    active=True,
                ),
                StaffingLeadershipAssignment(
                    person=self.source_approver,
                    unit=self.source_department,
                    leadership_level="department",
                    active=True,
                ),
                StaffingLeadershipAssignment(
                    person=self.destination_approver,
                    unit=self.destination_department,
                    leadership_level="department",
                    active=True,
                ),
                StaffingLeadershipAssignment(
                    person=self.manager,
                    unit=self.operation,
                    leadership_level="operation",
                    active=True,
                ),
                StaffingLeadershipAssignment(
                    person=self.division_manager,
                    unit=self.sort,
                    leadership_level="sort",
                    active=True,
                ),
            ]
        )
        self.submitter_user = self._user("pt_submitter", "operator", self.submitter)
        self.source_user = self._user("ft_source", "operator", self.source_approver)
        self.manager_user = self._user("manager", "operator", self.manager)
        self.division_user = self._user(
            "division", "operator", self.division_manager
        )
        self.watcher_user = self._user("watcher", "watcher")
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_new_request_notifies_only_linked_routed_ft_supervisors_and_dedupes(self):
        change_request = self._submit(
            requested_first_name="Updated",
            requested_work_area_unit_id=str(self.destination_area.id),
        )
        db.session.commit()

        notifications = StaffingNotification.query.filter_by(
            notification_type="new_request"
        ).all()
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0].recipient_user_id, self.source_user.id)
        self.assertNotIn(
            self.manager_user.id,
            {row.recipient_user_id for row in notifications},
        )
        self.assertNotIn(
            self.division_user.id,
            {row.recipient_user_id for row in notifications},
        )

        self._service_call(notification_service.notify_new_requests, [change_request])
        db.session.commit()
        self.assertEqual(
            StaffingNotification.query.filter_by(
                notification_type="new_request"
            ).count(),
            1,
        )

    def test_actionable_badges_follow_ft_routing_manager_purview_and_roles(self):
        self._submit(requested_first_name="Badge")
        db.session.commit()

        self.assertEqual(self._navigation(self.source_user)["actionable_requests"], 1)
        self.assertEqual(self._navigation(self.manager_user)["actionable_requests"], 1)
        self.assertEqual(self._navigation(self.division_user)["actionable_requests"], 0)
        self.assertEqual(self._navigation(self.watcher_user)["actionable_requests"], 0)

    def test_submitter_gets_one_completion_summary_not_each_field_decision(self):
        change_request = self._submit(
            requested_first_name="First",
            requested_last_name="Last",
        )
        db.session.commit()
        first_item, second_item = change_request.items

        self._service_call(
            request_service.decide_change_request_item,
            first_item.id,
            "approve",
            None,
            self.source_user,
            request_service.change_request_item_revision(first_item),
        )
        db.session.commit()
        self.assertEqual(
            StaffingNotification.query.filter_by(
                recipient_user_id=self.submitter_user.id,
                notification_type="request_completed",
            ).count(),
            0,
        )

        self._service_call(
            request_service.decide_change_request_item,
            second_item.id,
            "deny",
            "Not approved",
            self.source_user,
            request_service.change_request_item_revision(second_item),
        )
        db.session.commit()
        completion = StaffingNotification.query.filter_by(
            recipient_user_id=self.submitter_user.id,
            notification_type="request_completed",
        ).one()
        details = json.loads(completion.details_json)
        self.assertEqual(details["counts"]["approved"], 1)
        self.assertEqual(details["counts"]["denied"], 1)
        self.assertEqual(
            StaffingNotification.query.filter_by(
                recipient_user_id=self.submitter_user.id
            ).count(),
            1,
        )

    def test_superseded_and_reversal_notify_submitter_immediately(self):
        superseded_request = self._submit(requested_first_name="Requested")
        db.session.commit()
        superseded_item = superseded_request.items[0]
        self.target.first_name = "Newer Value"
        db.session.commit()
        self._service_call(
            request_service.decide_change_request_item,
            superseded_item.id,
            "approve",
            None,
            self.source_user,
            request_service.change_request_item_revision(superseded_item),
        )
        db.session.commit()
        self.assertEqual(
            StaffingNotification.query.filter_by(
                recipient_user_id=self.submitter_user.id,
                notification_type="item_superseded",
            ).count(),
            1,
        )

        reversed_request = self._submit(requested_last_name="Applied")
        db.session.commit()
        reversed_item = reversed_request.items[0]
        self._service_call(
            request_service.decide_change_request_item,
            reversed_item.id,
            "approve",
            None,
            self.source_user,
            request_service.change_request_item_revision(reversed_item),
        )
        db.session.commit()
        self._service_call(
            request_service.reverse_change_request_item,
            reversed_item.id,
            "Reconsider",
            self.source_user,
            request_service.change_request_item_revision(reversed_item),
        )
        db.session.commit()
        self.assertEqual(
            StaffingNotification.query.filter_by(
                recipient_user_id=self.submitter_user.id,
                notification_type="decision_reversed",
            ).count(),
            1,
        )

    def test_overdue_notifies_routed_ft_and_manager_once_but_not_division(self):
        change_request = self._submit(requested_first_name="Overdue")
        db.session.commit()
        now = datetime.utcnow()
        change_request.submitted_at = now - timedelta(hours=49)
        db.session.commit()

        first = self._service_call(notification_service.maintain_notifications, now)
        db.session.commit()
        repeated_dml = []

        def capture_dml(_connection, _cursor, statement, _parameters, _context, _many):
            if statement.lstrip().split(None, 1)[0].upper() in {
                "INSERT",
                "UPDATE",
                "DELETE",
            }:
                repeated_dml.append(statement)

        event.listen(db.engine, "before_cursor_execute", capture_dml)
        try:
            second = self._service_call(
                notification_service.maintain_notifications,
                now,
            )
        finally:
            event.remove(db.engine, "before_cursor_execute", capture_dml)
        db.session.commit()

        overdue = StaffingNotification.query.filter_by(
            notification_type="request_overdue"
        ).all()
        self.assertEqual(first["overdue_created"], 2)
        self.assertEqual(second["overdue_created"], 0)
        self.assertEqual(repeated_dml, [])
        self.assertEqual(
            {row.recipient_user_id for row in overdue},
            {self.source_user.id, self.manager_user.id},
        )
        self.assertNotIn(
            self.division_user.id,
            {row.recipient_user_id for row in overdue},
        )

    def test_notification_history_cleanup_is_opportunistic_and_14_days(self):
        self._submit(requested_first_name="Old Notification")
        db.session.commit()
        notification = StaffingNotification.query.filter_by(
            notification_type="new_request"
        ).one()
        notification_id = notification.id
        notification.created_at = datetime.utcnow() - timedelta(days=15)
        db.session.commit()

        result = self._service_call(
            notification_service.maintain_notifications,
            datetime.utcnow(),
        )
        db.session.commit()
        self.assertEqual(result["purged"], 1)
        self.assertIsNone(db.session.get(StaffingNotification, notification_id))

    def test_notification_page_marks_read_and_deep_links_to_request(self):
        change_request = self._submit(requested_first_name="Open Me")
        db.session.commit()
        notification = StaffingNotification.query.filter_by(
            notification_type="new_request"
        ).one()
        self._login(self.source_user)

        dml = []

        def capture_sql(_connection, _cursor, statement, _parameters, _context, _many):
            if statement.lstrip().split(None, 1)[0].upper() in {
                "INSERT",
                "UPDATE",
                "DELETE",
            }:
                dml.append(statement)

        event.listen(db.engine, "before_cursor_execute", capture_sql)
        try:
            with patch("app.neostaffing.routes.db.session.commit") as commit:
                page = self.client.get("/neostaffing/notifications")
        finally:
            event.remove(db.engine, "before_cursor_execute", capture_sql)
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"neostaffing-notifications-console", page.data)
        self.assertIn(b"neostaffing-notifications-table", page.data)
        self.assertNotIn(b"neostaffing-dashboard-shell", page.data)
        self.assertIn(b"UNREAD", page.data)
        self.assertIn(b"OPEN REQUEST", page.data)
        self.assertEqual(dml, [])
        commit.assert_not_called()

        marked = self.client.post(
            f"/neostaffing/notifications/{notification.id}/read",
            follow_redirects=False,
        )
        self.assertEqual(marked.status_code, 302)
        db.session.expire_all()
        self.assertIsNotNone(db.session.get(StaffingNotification, notification.id).read_at)

        notification = db.session.get(StaffingNotification, notification.id)
        notification.read_at = None
        db.session.commit()
        opened = self.client.post(
            f"/neostaffing/notifications/{notification.id}/open",
            follow_redirects=False,
        )
        self.assertEqual(opened.status_code, 302)
        self.assertIn(f"search={change_request.id}", opened.headers["Location"])

    def test_notification_materialization_queries_stay_bounded_at_1500_people(self):
        people = [
            StaffingPerson(
                employee_id=f"SCALE-{index:04d}",
                first_name="Scale",
                last_name=f"Person {index:04d}",
                seniority_date=date(2020, 1, 1),
                classification="part_time",
                employee_status="active",
                active=True,
            )
            for index in range(1494)
        ]
        db.session.add_all(people)
        db.session.flush()
        now = datetime.utcnow()
        requests = [
            StaffingChangeRequest(
                person_id=person.id,
                submitted_by_user_id=self.submitter_user.id,
                submitted_by_person_id=self.submitter.id,
                source_work_area_unit_id=self.source_area.id,
                routed_approver_person_ids_json=json.dumps(
                    [self.source_approver.id]
                ),
                status="pending",
                submitted_at=now - timedelta(hours=49),
            )
            for person in people[:100]
        ]
        db.session.add_all(requests)
        db.session.commit()
        db.session.expunge_all()

        select_count = 0

        def count_selects(_connection, _cursor, statement, _parameters, _context, _many):
            nonlocal select_count
            if statement.lstrip().upper().startswith("SELECT"):
                select_count += 1

        event.listen(db.engine, "before_cursor_execute", count_selects)
        try:
            result = self._service_call(
                notification_service.maintain_notifications,
                now,
            )
        finally:
            event.remove(db.engine, "before_cursor_execute", count_selects)

        self.assertEqual(result["overdue_created"], 200)
        self.assertLessEqual(select_count, 8)

    def _submit(self, **changes):
        values = {"person_id": str(self.target.id), **changes}
        return self._service_call(
            request_service.submit_change_request,
            values,
            self.submitter_user,
        )

    def _navigation(self, user):
        with self.app.test_request_context("/neostaffing/requests"):
            return notification_service.notification_navigation_state(user)

    def _service_call(self, callback, *args):
        with self.app.test_request_context("/neostaffing/requests"):
            return callback(*args)

    def _unit(self, unit_type, name, parent=None):
        unit = StaffingUnit(
            unit_type=unit_type,
            name=name,
            parent=parent,
            active=True,
        )
        db.session.add(unit)
        db.session.flush()
        return unit

    def _person(self, employee_id, classification, first_name, last_name):
        person = StaffingPerson(
            employee_id=employee_id,
            first_name=first_name,
            last_name=last_name,
            seniority_date=date(2020, 1, 1),
            classification=classification,
            employee_status="active",
            active=True,
        )
        db.session.add(person)
        db.session.flush()
        return person

    def _user(self, username, app_role, person=None):
        user = User(
            username=username,
            email=f"{username}@example.com",
            employee_id=person.employee_id if person else f"USER-{username}",
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
                role=app_role,
                is_active=True,
                approved_at=datetime.utcnow(),
            )
        )
        db.session.flush()
        return user

    def _login(self, user):
        g.pop("_login_user", None)
        return self.client.post(
            "/login",
            data={"username": user.username, "password": "TestPassword123!"},
        )


if __name__ == "__main__":
    unittest.main()
