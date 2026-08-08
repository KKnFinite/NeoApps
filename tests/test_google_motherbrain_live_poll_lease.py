from datetime import date, datetime, timedelta
import unittest

from sqlalchemy import inspect, text

from app import create_app
from app.extensions import db
from app.models import (
    Gateway,
    MotherBrainGoogleLivePollState,
    SortDateOperation,
)
from app.services.google_motherbrain_live_poll_lease import (
    acquire_google_motherbrain_live_poll_lease,
    complete_google_motherbrain_live_poll_failure,
    complete_google_motherbrain_live_poll_success,
)
from app.services.google_motherbrain_live_polling import (
    set_google_motherbrain_live_polling_enabled,
)
from app.services.schema_sync import sync_local_sqlite_schema


class GoogleMotherBrainLivePollLeaseTest(unittest.TestCase):
    def setUp(self):
        TestConfig = type(
            "GoogleMotherBrainLivePollLeaseTestConfig",
            (),
            {
                "SECRET_KEY": "google-live-poll-lease-test",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            },
        )
        self.app = create_app(TestConfig)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.gateway = self._gateway("RFD")
        self.operation = self._operation(self.gateway, date(2026, 8, 8), "night")
        self.now = datetime(2026, 8, 8, 22, 0)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_off_does_not_create_or_acquire_a_lease(self):
        result = acquire_google_motherbrain_live_poll_lease(self.operation, self.now)

        self.assertEqual(result.status, "disabled")
        self.assertIsNone(result.lease)
        self.assertEqual(MotherBrainGoogleLivePollState.query.count(), 0)

    def test_first_due_worker_wins_and_second_worker_reports_in_progress(self):
        self._enable(self.gateway, "night")

        first = acquire_google_motherbrain_live_poll_lease(self.operation, self.now)
        second = acquire_google_motherbrain_live_poll_lease(self.operation, self.now)

        self.assertTrue(first.acquired)
        self.assertEqual(second.status, "in_progress")
        self.assertEqual(MotherBrainGoogleLivePollState.query.count(), 1)

    def test_valid_lease_blocks_another_worker(self):
        self._enable(self.gateway, "night")
        acquired = acquire_google_motherbrain_live_poll_lease(self.operation, self.now)

        blocked = acquire_google_motherbrain_live_poll_lease(
            self.operation,
            self.now + timedelta(seconds=10),
        )

        self.assertTrue(acquired.acquired)
        self.assertEqual(blocked.status, "in_progress")

    def test_expired_lease_is_recoverable(self):
        self._enable(self.gateway, "night")
        first = acquire_google_motherbrain_live_poll_lease(self.operation, self.now)
        state = self._state(self.operation)
        state.lease_expires_at_utc = self.now - timedelta(seconds=1)
        state.last_attempt_at_utc = self.now - timedelta(minutes=2)
        db.session.commit()

        replacement = acquire_google_motherbrain_live_poll_lease(self.operation, self.now)

        self.assertTrue(first.acquired)
        self.assertTrue(replacement.acquired)
        self.assertNotEqual(first.lease.token, replacement.lease.token)

    def test_success_suppresses_another_attempt_for_one_minute(self):
        self._enable(self.gateway, "night")
        acquired = acquire_google_motherbrain_live_poll_lease(self.operation, self.now)

        self.assertTrue(
            complete_google_motherbrain_live_poll_success(
                acquired.lease,
                self.now + timedelta(seconds=5),
            )
        )
        state = self._state(self.operation)
        self.assertEqual(state.last_success_at_utc, self.now + timedelta(seconds=5))
        self.assertIsNone(state.last_error)
        self.assertEqual(
            acquire_google_motherbrain_live_poll_lease(
                self.operation,
                self.now + timedelta(seconds=59),
            ).status,
            "not_due",
        )
        self.assertTrue(
            acquire_google_motherbrain_live_poll_lease(
                self.operation,
                self.now + timedelta(minutes=1),
            ).acquired
        )

    def test_failure_preserves_last_success_and_waits_for_next_interval(self):
        self._enable(self.gateway, "night")
        state = MotherBrainGoogleLivePollState(
            gateway_id=self.gateway.id,
            sort_name="night",
            sort_date=self.operation.sort_date,
            last_success_at_utc=self.now - timedelta(minutes=5),
        )
        db.session.add(state)
        db.session.commit()
        acquired = acquire_google_motherbrain_live_poll_lease(self.operation, self.now)

        self.assertTrue(
            complete_google_motherbrain_live_poll_failure(
                acquired.lease,
                RuntimeError("provider unavailable"),
                self.now + timedelta(seconds=5),
            )
        )
        state = self._state(self.operation)
        self.assertEqual(state.last_success_at_utc, self.now - timedelta(minutes=5))
        self.assertEqual(state.last_error, "RuntimeError")
        self.assertEqual(
            acquire_google_motherbrain_live_poll_lease(
                self.operation,
                self.now + timedelta(seconds=10),
            ).status,
            "not_due",
        )
        self.assertTrue(
            acquire_google_motherbrain_live_poll_lease(
                self.operation,
                self.now + timedelta(minutes=1),
            ).acquired
        )

    def test_gateway_sort_and_sort_date_scopes_are_independent(self):
        second_gateway = self._gateway("DFW")
        day_operation = self._operation(self.gateway, self.operation.sort_date, "day")
        next_date_operation = self._operation(
            self.gateway,
            self.operation.sort_date + timedelta(days=1),
            "night",
        )
        second_gateway_operation = self._operation(
            second_gateway,
            self.operation.sort_date,
            "night",
        )
        for gateway, sort_name in (
            (self.gateway, "night"),
            (self.gateway, "day"),
            (second_gateway, "night"),
        ):
            self._enable(gateway, sort_name)

        results = [
            acquire_google_motherbrain_live_poll_lease(operation, self.now)
            for operation in (
                self.operation,
                day_operation,
                next_date_operation,
                second_gateway_operation,
            )
        ]

        self.assertTrue(all(result.acquired for result in results))
        self.assertEqual(MotherBrainGoogleLivePollState.query.count(), 4)

    def test_sqlite_schema_sync_adds_the_state_table_idempotently(self):
        db.session.execute(text("DROP TABLE motherbrain_google_live_poll_states"))
        db.session.commit()

        sync_local_sqlite_schema(self.app)
        sync_local_sqlite_schema(self.app)
        db.session.commit()

        table_names = set(inspect(db.engine).get_table_names())
        columns = {
            column["name"]
            for column in inspect(db.engine).get_columns(
                "motherbrain_google_live_poll_states"
            )
        }
        self.assertIn("motherbrain_google_live_poll_states", table_names)
        self.assertTrue(
            {
                "gateway_id",
                "sort_name",
                "sort_date",
                "last_attempt_at_utc",
                "last_success_at_utc",
                "last_error",
                "lease_expires_at_utc",
                "lease_token",
            }.issubset(columns)
        )

    def _enable(self, gateway, sort_name):
        set_google_motherbrain_live_polling_enabled(gateway, sort_name, True)
        db.session.commit()

    def _gateway(self, code):
        gateway = Gateway(code=code, name=f"{code} Gateway", is_active=True)
        db.session.add(gateway)
        db.session.flush()
        return gateway

    def _operation(self, gateway, sort_date, sort_name):
        operation = SortDateOperation(
            gateway_id=gateway.id,
            sort_date=sort_date,
            gateway_code=gateway.code,
            sort_name=sort_name,
        )
        db.session.add(operation)
        db.session.flush()
        return operation

    def _state(self, operation):
        return MotherBrainGoogleLivePollState.query.filter_by(
            gateway_id=operation.gateway_id,
            sort_name=operation.sort_name,
            sort_date=operation.sort_date,
        ).one()


if __name__ == "__main__":
    unittest.main()
