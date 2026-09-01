from datetime import date, datetime, time
import unittest
from unittest.mock import patch

from sqlalchemy.exc import IntegrityError

from app import create_app
from app.extensions import db
from app.models import (
    GatewaySortMatrix,
    SortDateOperation,
    SortTimelineSettings,
    SortTimelineSortSetting,
    User,
)
from app.services.access_control import (
    backfill_default_gateway_node_roles,
    ensure_default_gateway_and_nodes,
)
from app.services.operation_lifecycle import (
    create_manual_current_sort_operation,
    ensure_operational_sort_operations,
)
from app.services.operation_scope import current_operational_sort_operation
from app.services.password_policy import set_user_password
from app.services.permission_rules import ensure_default_permission_rules


class MotherBrainManualSortCreationTest(unittest.TestCase):
    LOCAL_NOW = datetime(2026, 6, 18, 12, 0)

    def setUp(self):
        TestConfig = type(
            "TestConfig",
            (),
            {
                "SECRET_KEY": "test",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
                "CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE": self.LOCAL_NOW,
            },
        )
        self.app = create_app(TestConfig)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.gateway = ensure_default_gateway_and_nodes()
        ensure_default_permission_rules()
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_simulator_can_create_scheduled_tonight_sort(self):
        user = self._login("scheduled-simulator", "simulator")
        self._schedule_night("thursday")
        db.session.commit()

        page = self.client.get("/motherbrain/manage-sort")
        response = self.client.post(
            "/motherbrain/manage-sort/create-tonight",
            follow_redirects=False,
        )

        operation = SortDateOperation.query.one()
        self.assertIn(b"CREATE TONIGHT'S SORT", page.data)
        self.assertEqual(response.status_code, 302)
        self.assertIn(f"operation_id={operation.id}", response.location)
        self.assertEqual(operation.sort_date, date(2026, 6, 18))
        self.assertEqual(operation.sort_name, "night")
        self.assertEqual(operation.generated_by_user_id, user.id)
        with self.app.test_request_context("/neosubzero/outbound"):
            self.assertEqual(
                current_operational_sort_operation(self.gateway),
                operation,
            )

    def test_simulator_is_blocked_when_tonight_is_not_scheduled(self):
        self._login("unscheduled-simulator", "simulator")

        page = self.client.get("/motherbrain/manage-sort")
        response = self.client.post(
            "/motherbrain/manage-sort/create-tonight",
            follow_redirects=True,
        )

        self.assertNotIn(b"CREATE TONIGHT'S SORT", page.data)
        self.assertIn(b"Tonight is not a scheduled sort day.", response.data)
        self.assertEqual(SortDateOperation.query.count(), 0)

    def test_grandmaster_can_create_unscheduled_tonight_sort(self):
        self._login("unscheduled-grandmaster", "grandmaster")

        page = self.client.get("/motherbrain/manage-sort")
        response = self.client.post(
            "/motherbrain/manage-sort/create-tonight",
            follow_redirects=False,
        )

        self.assertIn(b"CREATE TONIGHT'S SORT", page.data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(SortDateOperation.query.count(), 1)

    def test_existing_operation_hides_action_and_forged_post_cannot_duplicate(self):
        self._login("existing-grandmaster", "grandmaster")
        operation = SortDateOperation(
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
            sort_date=date(2026, 6, 18),
            sort_name="night",
        )
        db.session.add(operation)
        db.session.commit()

        page = self.client.get("/motherbrain/manage-sort")
        response = self.client.post(
            "/motherbrain/manage-sort/create-tonight",
            follow_redirects=True,
        )

        self.assertNotIn(b"CREATE TONIGHT'S SORT", page.data)
        self.assertIn(b"sort operation already exists", response.data)
        self.assertEqual(SortDateOperation.query.count(), 1)

    def test_duplicate_race_returns_winning_operation(self):
        user = self._user("race-grandmaster", "grandmaster")
        db.session.commit()

        def concurrent_insert(**kwargs):
            db.session.add(
                SortDateOperation(
                    gateway_id=self.gateway.id,
                    gateway_code=kwargs["gateway_code"],
                    sort_date=kwargs["sort_date"],
                    sort_name=kwargs["sort_name"],
                    generated_by_user_id=user.id,
                )
            )
            db.session.commit()
            raise IntegrityError("insert", {}, Exception("duplicate"))

        with patch(
            "app.services.operation_lifecycle.generate_sort_date_operation_from_master",
            side_effect=concurrent_insert,
        ):
            result = create_manual_current_sort_operation(
                self.gateway,
                user.id,
                allow_unscheduled=True,
                now=self.LOCAL_NOW,
            )

        self.assertFalse(result["created"])
        self.assertEqual(result["operation"], SortDateOperation.query.one())
        self.assertEqual(SortDateOperation.query.count(), 1)

    def test_automatic_lifecycle_reuses_manually_created_operation(self):
        self._login("fallback-simulator", "simulator")
        self._schedule_night("thursday")
        self._timeline_night()
        db.session.commit()
        self.client.post("/motherbrain/manage-sort/create-tonight")
        manual_operation = SortDateOperation.query.one()

        automatic = ensure_operational_sort_operations(
            self.gateway,
            now=datetime(2026, 6, 18, 22, 30),
        )

        self.assertEqual(automatic["created"], [])
        self.assertEqual(automatic["existing"], [manual_operation])
        self.assertEqual(SortDateOperation.query.count(), 1)

    def _schedule_night(self, day):
        db.session.add(
            GatewaySortMatrix(
                gateway_id=self.gateway.id,
                gateway_code=self.gateway.code,
                day_of_week=day,
                sort_name="night",
                is_active=True,
            )
        )

    def _timeline_night(self):
        settings = SortTimelineSettings(
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
        )
        db.session.add(settings)
        db.session.flush()
        db.session.add(
            SortTimelineSortSetting(
                settings_id=settings.id,
                gateway_id=self.gateway.id,
                gateway_code=self.gateway.code,
                sort_name="night",
                sort_window_start_local=time(22, 0),
                sort_window_end_local=time(4, 0),
            )
        )

    def _login(self, username, role):
        user = self._user(username, role)
        db.session.commit()
        self.client.post(
            "/login",
            data={"username": username, "password": "TestPassword123!"},
        )
        return user

    def _user(self, username, role):
        user = User(username=username, role=role)
        set_user_password(user, "TestPassword123!")
        db.session.add(user)
        db.session.flush()
        backfill_default_gateway_node_roles(user, role=role)
        return user


if __name__ == "__main__":
    unittest.main()
