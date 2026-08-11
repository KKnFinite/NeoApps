from datetime import date, datetime, time
import unittest

from app import create_app
from app.extensions import db
from app.models import (
    FlightApiReviewItem,
    MotherBrainAlert,
    MotherBrainAlertUserState,
    PermissionRule,
    SortDateOperation,
    User,
)
from app.services.access_control import (
    backfill_default_gateway_node_roles,
    ensure_default_gateway_and_nodes,
)
from app.services.live_collaboration import entity_version
from app.services.flight_api import process_provider_payload
from app.services.motherbrain_alerts import motherbrain_alert_context
from app.services.password_policy import set_user_password
from app.services.permission_rules import ensure_default_permission_rules, user_can
from app.services.sort_timeline import ensure_sort_timeline_settings
from app.services.unmatched_review_alerts import (
    UNMATCHED_REVIEW_ALERT_PERMISSION,
    expire_unmatched_review_alerts,
    mark_unmatched_review_alert_read,
    pending_review_key_sets,
    sync_unmatched_review_alert,
    sync_unmatched_review_alerts_for_operation,
    unmatched_review_alert_key,
)


class UnmatchedReviewAlertsTest(unittest.TestCase):
    def setUp(self):
        TestConfig = type(
            "UnmatchedReviewAlertsTestConfig",
            (),
            {
                "SECRET_KEY": "unmatched-review-alert-test-secret",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
                "CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE": datetime(
                    2026, 8, 10, 22, 0
                ),
            },
        )
        self.app = create_app(TestConfig)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        ensure_default_permission_rules()
        self.gateway = ensure_default_gateway_and_nodes()
        settings = ensure_sort_timeline_settings(self.gateway)
        self.sort_setting = next(
            setting
            for setting in settings.sort_settings
            if setting.sort_name == "night"
        )
        self.sort_setting.sort_window_start_local = time(20, 0)
        self.sort_setting.sort_window_end_local = time(4, 0)
        self.sort_setting.ops_window_start_local = time(21, 0)
        self.sort_setting.ops_window_end_local = time(3, 0)
        self.operation = SortDateOperation(
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
            sort_date=date(2026, 8, 10),
            sort_name="night",
        )
        db.session.add(self.operation)
        db.session.flush()
        self.grandmaster = self._user("alerts_grandmaster", "grandmaster")
        self.simulator = self._user("alerts_simulator", "simulator")
        self.operator = self._user("alerts_operator", "operator")
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_arrival_and_departure_alerts_are_separate_aggregates(self):
        self._review("arrival:a", "arrival", "UPS100")
        self._review("departure:a", "departure", "UPS200")

        result = sync_unmatched_review_alerts_for_operation(
            self.operation,
            previous_keys={"arrival": set(), "departure": set()},
        )
        db.session.commit()

        self.assertTrue(result["changed"])
        alerts = MotherBrainAlert.query.order_by(MotherBrainAlert.alert_key).all()
        self.assertEqual(len(alerts), 2)
        self.assertEqual(
            {alert.alert_key for alert in alerts},
            {
                unmatched_review_alert_key("arrival"),
                unmatched_review_alert_key("departure"),
            },
        )
        arrival = next(alert for alert in alerts if "arrival" in alert.alert_key)
        departure = next(alert for alert in alerts if "departure" in alert.alert_key)
        self.assertEqual(arrival.title, "Unmatched Arrivals")
        self.assertEqual(departure.title, "Unmatched Departures")
        self.assertIn("mission_type=arrival", arrival.related_url)
        self.assertIn("mission_type=departure", departure.related_url)
        self.assertEqual(arrival.permission_key, UNMATCHED_REVIEW_ALERT_PERMISSION)

    def test_flight_api_queue_creation_alerts_server_side_without_repeat_realert(self):
        payload = {
            "arrivals": [
                {
                    "_mission_type": "arrival",
                    "number": "5X333",
                    "callSign": "UPS333",
                    "airline": {"icao": "UPS", "iata": "5X"},
                    "departure": {
                        "airport": {"iata": "SDF", "icao": "SDF"},
                        "revisedTime": {"local": "2026-08-10T22:30:00"},
                        "scheduledTime": {"local": "2026-08-10T22:30:00"},
                    },
                    "arrival": {
                        "airport": {"iata": "RFD", "icao": "RFD"},
                        "revisedTime": {"local": "2026-08-10T22:30:00"},
                        "scheduledTime": {"local": "2026-08-10T22:30:00"},
                    },
                    "aircraft": {"reg": "N333UP", "model": "A300"},
                    "status": "Expected",
                }
            ]
        }

        first = process_provider_payload(
            payload,
            self.gateway,
            self.operation,
            apply=True,
            source="live",
        )
        alert = self._arrival_alert()
        mark_unmatched_review_alert_read(alert, self.grandmaster)
        db.session.flush()
        second = process_provider_payload(
            payload,
            self.gateway,
            self.operation,
            apply=True,
            source="live",
        )
        db.session.commit()

        self.assertEqual(len(first["review_items"]), 1)
        self.assertEqual(len(second["review_items"]), 1)
        self.assertEqual(
            MotherBrainAlert.query.filter_by(
                alert_key=unmatched_review_alert_key("arrival")
            ).count(),
            1,
        )
        self.assertEqual(MotherBrainAlertUserState.query.count(), 1)
        self.assertEqual(self._context_for(self.grandmaster)["unread_count"], 0)

    def test_multiple_items_update_one_alert_without_duplicates(self):
        first = self._review("arrival:a", "arrival", "UPS100")
        sync_unmatched_review_alerts_for_operation(
            self.operation,
            previous_keys={"arrival": set(), "departure": set()},
        )
        self._review("arrival:b", "arrival", "UPS101")
        sync_unmatched_review_alerts_for_operation(
            self.operation,
            previous_keys={"arrival": {first.review_key}, "departure": set()},
        )
        sync_unmatched_review_alerts_for_operation(self.operation)
        db.session.commit()

        alerts = MotherBrainAlert.query.filter_by(
            alert_key=unmatched_review_alert_key("arrival")
        ).all()
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].message, "2 unmatched arrivals awaiting review.")

    def test_alp_preview_creates_and_clear_resolves_server_side_alert(self):
        self._login(self.grandmaster)
        endpoint = f"/motherbrain/operations/{self.operation.id}/alp/arrival"

        preview = self.client.post(
            endpoint,
            data={
                "paste_text": (
                    "10-AUG-2026\tUPS999\tSDF\tN999UP\tA01\tScheduled\t22:30 (S)"
                ),
                "alp_action": "preview",
            },
        )

        self.assertEqual(preview.status_code, 200)
        self.assertEqual(self._arrival_alert().message, "1 unmatched arrival awaiting review.")

        cleared = self.client.post(endpoint, data={"alp_action": "clear"})

        self.assertEqual(cleared.status_code, 302)
        self.assertFalse(self._arrival_alert().active)

    def test_permission_recipients_follow_configured_minimum_role(self):
        self._activate_arrival_alert()

        grandmaster = self._context_for(self.grandmaster)
        simulator = self._context_for(self.simulator)
        operator = self._context_for(self.operator)

        self.assertEqual(grandmaster["count"], 1)
        self.assertEqual(simulator["count"], 1)
        self.assertEqual(operator["count"], 0)

        rule = PermissionRule.query.filter_by(
            permission_key=UNMATCHED_REVIEW_ALERT_PERMISSION
        ).one()
        rule.minimum_role = "operator"
        db.session.commit()

        self.assertEqual(self._context_for(self.operator)["count"], 1)

    def test_user_without_permission_cannot_mark_alert_read(self):
        self._activate_arrival_alert()
        alert = self._arrival_alert()
        self._login(self.operator)

        response = self.client.post(f"/motherbrain/alerts/{alert.id}/read")

        self.assertEqual(response.status_code, 403)
        self.assertEqual(MotherBrainAlertUserState.query.count(), 0)

    def test_per_user_read_state_and_new_item_realert_are_independent(self):
        item_a = self._review("arrival:a", "arrival", "UPS100")
        item_b = self._review("arrival:b", "arrival", "UPS101")
        self._sync_from_empty()
        alert = self._arrival_alert()
        mark_unmatched_review_alert_read(alert, self.grandmaster)
        db.session.commit()

        self.assertEqual(self._context_for(self.grandmaster)["unread_count"], 0)
        self.assertEqual(self._context_for(self.simulator)["unread_count"], 1)
        self.assertTrue(alert.active)
        self.assertFalse(alert.acknowledged)

        previous = pending_review_key_sets(self.operation)
        item_a.review_status = "ignored"
        self._review("arrival:c", "arrival", "UPS102")
        db.session.flush()
        sync_unmatched_review_alerts_for_operation(
            self.operation,
            previous_keys=previous,
        )
        db.session.commit()

        self.assertEqual(self._arrival_alert().message, "2 unmatched arrivals awaiting review.")
        self.assertEqual(MotherBrainAlertUserState.query.count(), 0)
        self.assertEqual(self._context_for(self.grandmaster)["unread_count"], 1)
        self.assertEqual(item_b.review_status, "pending")

    def test_count_decrease_updates_quietly_and_zero_resolves(self):
        item_a = self._review("arrival:a", "arrival", "UPS100")
        item_b = self._review("arrival:b", "arrival", "UPS101")
        self._sync_from_empty()
        alert = self._arrival_alert()
        mark_unmatched_review_alert_read(alert, self.grandmaster)

        item_a.review_status = "ignored"
        db.session.flush()
        sync_unmatched_review_alert(
            self.operation,
            "arrival",
            new_review_keys=set(),
        )
        db.session.commit()

        self.assertEqual(self._arrival_alert().message, "1 unmatched arrival awaiting review.")
        self.assertEqual(self._context_for(self.grandmaster)["unread_count"], 0)
        self.assertEqual(MotherBrainAlertUserState.query.count(), 1)

        item_b.review_status = "accepted"
        db.session.flush()
        sync_unmatched_review_alert(
            self.operation,
            "arrival",
            new_review_keys=set(),
        )
        db.session.commit()

        alert = self._arrival_alert()
        self.assertFalse(alert.active)
        self.assertTrue(alert.acknowledged)
        self.assertEqual(self._context_for(self.grandmaster)["count"], 0)

    def test_sort_end_expires_but_ops_end_does_not(self):
        item = self._review("arrival:a", "arrival", "UPS100")
        self._sync_from_empty()

        changed = expire_unmatched_review_alerts(
            self.gateway,
            now=datetime(2026, 8, 11, 3, 30),
        )
        self.assertEqual(changed, 0)
        self.assertTrue(self._arrival_alert().active)

        changed = expire_unmatched_review_alerts(
            self.gateway,
            now=datetime(2026, 8, 11, 4, 0),
        )
        db.session.commit()

        self.assertEqual(changed, 1)
        self.assertFalse(self._arrival_alert().active)
        self.assertEqual(item.review_status, "pending")

    def test_alert_action_marks_only_current_user_read_and_deep_links_queue(self):
        self._review("arrival:a", "arrival", "UPS100")
        self._sync_from_empty()
        self._login(self.grandmaster)
        alert = self._arrival_alert()

        page = self.client.get(
            f"/motherbrain/flight-api-review?operation_id={self.operation.id}"
            "&mission_type=arrival"
        )
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Unmatched Arrivals", page.data)
        self.assertIn(
            f'action="/motherbrain/alerts/{alert.id}/open"'.encode(),
            page.data,
        )

        response = self.client.post(
            f"/motherbrain/alerts/{alert.id}/open",
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.headers["Location"],
            f"/motherbrain/flight-api-review?operation_id={self.operation.id}"
            "&mission_type=arrival",
        )
        self.assertEqual(self._context_for(self.grandmaster)["unread_count"], 0)
        self.assertEqual(self._context_for(self.simulator)["unread_count"], 1)
        self.assertTrue(self._arrival_alert().active)

    def test_filtered_queue_and_live_payload_use_matching_alert(self):
        self._review("arrival:a", "arrival", "UPS100")
        self._review("departure:a", "departure", "UPS200")
        self._sync_from_empty()
        self._login(self.grandmaster)

        queue = self.client.get(
            f"/motherbrain/flight-api-review?operation_id={self.operation.id}"
            "&mission_type=arrival"
        )
        live = self.client.get(
            f"/motherbrain/operations/{self.operation.id}/planning/arrival/state"
        )

        self.assertEqual(queue.status_code, 200)
        self.assertIn(b"UPS100", queue.data)
        self.assertNotIn(b"UPS200", queue.data)
        self.assertEqual(live.status_code, 200)
        payload = live.get_json()
        self.assertIn("alert_tray", payload["fragments"])
        self.assertIn("Unmatched Arrivals", payload["fragments"]["alert_tray"])
        self.assertIn("Unmatched Departures", payload["fragments"]["alert_tray"])

    def test_concurrent_resolution_does_not_recreate_item(self):
        item = self._review("arrival:a", "arrival", "UPS100")
        self._sync_from_empty()
        db.session.commit()
        expected_version = entity_version(item)
        self._login(self.grandmaster)
        endpoint = (
            f"/motherbrain/operations/{self.operation.id}/planning/api/"
            f"{item.id}/ignore"
        )
        data = {
            "mission_type": "arrival",
            "expected_version": expected_version,
        }

        first = self.client.post(
            endpoint,
            data=data,
            headers={"Accept": "application/json"},
        )
        second = self.client.post(
            endpoint,
            data=data,
            headers={"Accept": "application/json"},
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.get_json()["conflict"]["type"], "item_changed")
        self.assertEqual(
            FlightApiReviewItem.query.filter_by(review_key=item.review_key).count(),
            1,
        )
        self.assertEqual(db.session.get(FlightApiReviewItem, item.id).review_status, "ignored")

    def test_shared_client_reconciles_badge_without_page_reload(self):
        source = (self.app.static_folder + "/js/live_updates.js")
        with open(source, encoding="utf-8") as handle:
            script = handle.read()

        self.assertIn("reconcileAlertTrays", script)
        self.assertIn("data-alert-read-url", script)
        self.assertIn("method: \"POST\"", script)
        self.assertIn("fragments.alert_tray", self._planning_script())

    def _activate_arrival_alert(self):
        self._review("arrival:a", "arrival", "UPS100")
        self._sync_from_empty()
        db.session.commit()

    def _sync_from_empty(self):
        sync_unmatched_review_alerts_for_operation(
            self.operation,
            previous_keys={"arrival": set(), "departure": set()},
        )
        db.session.flush()

    def _arrival_alert(self):
        return MotherBrainAlert.query.filter_by(
            sort_date_operation_id=self.operation.id,
            alert_key=unmatched_review_alert_key("arrival"),
        ).one()

    def _review(self, review_key, mission_type, flight_number):
        item = FlightApiReviewItem(
            sort_date_operation_id=self.operation.id,
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
            sort_date=self.operation.sort_date,
            sort_name=self.operation.sort_name,
            mission_type=mission_type,
            review_key=review_key,
            review_status="pending",
            flight_number=flight_number,
            origin="SDF" if mission_type == "arrival" else "RFD",
            destination="RFD" if mission_type == "arrival" else "SDF",
        )
        db.session.add(item)
        db.session.flush()
        return item

    def _context_for(self, user):
        return motherbrain_alert_context(
            self.gateway,
            can_view_permission=lambda key: user_can(key, user),
            operation=self.operation,
            user_id=user.id,
        )

    def _user(self, username, role):
        user = User(
            username=username,
            role=role,
            email_verified_at=datetime.utcnow(),
        )
        set_user_password(user, "TestPassword123!")
        db.session.add(user)
        db.session.flush()
        backfill_default_gateway_node_roles(user, role=role)
        return user

    def _login(self, user):
        response = self.client.post(
            "/login",
            data={"username": user.username, "password": "TestPassword123!"},
        )
        self.assertEqual(response.status_code, 302)

    @staticmethod
    def _planning_script():
        with open(
            "app/templates/neomotherbrain/_planning_live_updates.html",
            encoding="utf-8",
        ) as handle:
            return handle.read()


if __name__ == "__main__":
    unittest.main()
