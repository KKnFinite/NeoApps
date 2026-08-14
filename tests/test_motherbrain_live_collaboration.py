from collections import Counter
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
import json
import unittest
from unittest.mock import patch

from sqlalchemy import event

from app import create_app
from app.extensions import db
from app.models import (
    FlightApiReviewItem,
    Gateway,
    SortDateCrewAssignment,
    SortDateMission,
    SortDateOperation,
    SortDateParkingAssignment,
    SortDateTailState,
    SortTimelineSettings,
    SortTimelineSortSetting,
    User,
)
from app.services.access_control import backfill_default_gateway_node_roles
from app.services.alp_preview_state import save_alp_preview_state
from app.services.live_collaboration import entity_version
from app.services.password_policy import set_user_password
from app.services.planning_collaboration import planning_state_revision


class MotherBrainLiveCollaborationTest(unittest.TestCase):
    def setUp(self):
        TestConfig = type(
            "TestConfig",
            (),
            {
                "SECRET_KEY": "test",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
                "CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE": datetime(
                    2026,
                    8,
                    10,
                    21,
                    0,
                ),
            },
        )
        self.app = create_app(TestConfig)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()

        self.gateway = Gateway.query.filter_by(code="RFD").first()
        if self.gateway is None:
            self.gateway = Gateway(code="RFD", name="Rockford")
            db.session.add(self.gateway)
            db.session.flush()
        user = User(username="live-planner", role="grandmaster")
        set_user_password(user, "TestPassword123!")
        db.session.add(user)
        db.session.flush()
        backfill_default_gateway_node_roles(user, role="grandmaster")
        self.user = user

        settings = SortTimelineSettings.query.filter_by(
            gateway_id=self.gateway.id
        ).first()
        if settings is None:
            settings = SortTimelineSettings(
                gateway_id=self.gateway.id,
                gateway_code=self.gateway.code,
            )
            db.session.add(settings)
            db.session.flush()
        sort_setting = SortTimelineSortSetting.query.filter_by(
            gateway_id=self.gateway.id,
            sort_name="night",
        ).first()
        if sort_setting is None:
            sort_setting = SortTimelineSortSetting(
                timeline_settings=settings,
                gateway_id=self.gateway.id,
                gateway_code=self.gateway.code,
                sort_name="night",
            )
            db.session.add(sort_setting)
        sort_setting.sort_window_start_local = time(14, 0)
        sort_setting.sort_window_end_local = time(5, 0)
        sort_setting.ops_window_start_local = time(20, 0)
        sort_setting.ops_window_end_local = time(3, 0)

        self.operation = SortDateOperation(
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
            sort_date=date(2026, 8, 10),
            sort_name="night",
        )
        db.session.add(self.operation)
        db.session.commit()

        self.client = self.app.test_client()
        self.client.post(
            "/login",
            data={"username": user.username, "password": "TestPassword123!"},
        )

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_arrival_and_departure_pages_use_shared_selective_live_collaboration(self):
        for mission_type in ("arrival", "departure"):
            with self.subTest(mission_type=mission_type):
                response = self.client.get(
                    f"/motherbrain/operations/{self.operation.id}/alp/{mission_type}"
                )

                self.assertEqual(response.status_code, 200)
                self.assertIn(b"data-planning-live-root", response.data)
                self.assertIn(b"data-planning-live-collaboration", response.data)
                self.assertIn(b"data-live-rows=\"review\"", response.data)
                self.assertIn(b"data-live-rows=\"missions\"", response.data)
                self.assertIn(b"window.NeoLiveUpdates.reconcileRows", response.data)
                self.assertIn(b"data-planning-revision=", response.data)
                self.assertRegex(
                    response.get_data(as_text=True),
                    r'data-planning-revision="[0-9a-f]{32}"',
                )
                self.assertIn(
                    b'pollUrl.searchParams.set("revision", currentRevision)',
                    response.data,
                )
                self.assertIn(b"payload.changed === false", response.data)
                self.assertIn(b"intervalMs: 5000", response.data)
                self.assertNotIn(b"window.location.reload()", response.data)

    def test_state_payload_has_stable_identity_version_and_remote_values(self):
        mission = self._mission("arrival", "UPS0910", origin="SDF")
        db.session.add(mission)
        db.session.commit()

        first = self.client.get(self._state_url("arrival", revision="stale"))
        first_row = first.get_json()["rows"]["missions"][0]
        first_version = first_row["version"]
        first_revision = first.get_json()["revision"]

        mission.assigned_tail_number = "N910UP"
        mission.updated_at = mission.updated_at + timedelta(seconds=1)
        db.session.commit()
        second = self.client.get(
            self._state_url("arrival", revision=first_revision)
        )
        second_payload = second.get_json()
        second_row = second_payload["rows"]["missions"][0]

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first_row["id"], f"mission:{mission.id}")
        self.assertEqual(first_row["entity_id"], mission.id)
        self.assertNotEqual(second_row["version"], first_version)
        self.assertEqual(second_row["tail_number"], "N910UP")
        self.assertIn(f'data-live-id="mission:{mission.id}"', second_payload["fragments"]["missions"])

    def test_historical_planning_state_is_not_live(self):
        historical = SortDateOperation(
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
            sort_date=date(2026, 8, 8),
            sort_name="night",
        )
        db.session.add(historical)
        db.session.commit()

        revision = planning_state_revision(historical, "arrival", self.user)
        response = self.client.get(
            f"/motherbrain/operations/{historical.id}/planning/arrival/state"
            f"?revision={revision}"
        )
        refresh = response.get_json()["refresh"]

        self.assertEqual(response.status_code, 200)
        self.assertFalse(refresh["auto_refresh_enabled"])
        self.assertEqual(refresh["reason"], "historical_sort")

    def test_unchanged_planning_poll_keeps_outside_window_status_authoritative(self):
        self.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = datetime(
            2026,
            8,
            10,
            19,
            0,
        )
        revision = planning_state_revision(
            self.operation,
            "departure",
            self.user,
        )

        response = self.client.get(
            self._state_url("departure", revision=revision)
        )
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertFalse(payload["changed"])
        self.assertFalse(payload["refresh"]["auto_refresh_enabled"])
        self.assertEqual(payload["refresh"]["reason"], "before_ops_window")

    def test_planning_live_state_skips_global_lifecycle_maintenance(self):
        with (
            patch(
                "app.services.operation_lifecycle.ensure_operational_sort_operations"
            ) as lifecycle,
            patch(
                "app.services.unmatched_review_alerts.expire_unmatched_review_alerts"
            ) as alert_expiration,
        ):
            response = self.client.get(
                self._state_url(
                    "arrival",
                    revision=planning_state_revision(
                        self.operation,
                        "arrival",
                        self.user,
                    ),
                )
            )

        self.assertEqual(response.status_code, 200)
        lifecycle.assert_not_called()
        alert_expiration.assert_not_called()

    def test_unchanged_arrival_and_departure_revisions_skip_expensive_context(self):
        for mission_type in ("arrival", "departure"):
            with self.subTest(mission_type=mission_type):
                revision = planning_state_revision(
                    self.operation,
                    mission_type,
                    self.user,
                )
                with patch(
                    "app.neomotherbrain.routes._planning_live_collections"
                ) as collections:
                    response = self.client.get(
                        self._state_url(mission_type, revision=revision)
                    )

                payload = response.get_json()
                self.assertEqual(response.status_code, 200)
                self.assertTrue(payload["ok"])
                self.assertFalse(payload["changed"])
                self.assertEqual(payload["revision"], revision)
                self.assertNotIn("rows", payload)
                self.assertNotIn("fragments", payload)
                collections.assert_not_called()

    def test_unchanged_planning_poll_remains_two_selects(self):
        for mission_type in ("arrival", "departure"):
            with self.subTest(mission_type=mission_type):
                revision = planning_state_revision(
                    self.operation,
                    mission_type,
                    self.user,
                )
                with self._capture_sql() as capture:
                    response = self.client.get(
                        self._state_url(mission_type, revision=revision)
                    )

                self.assertEqual(response.status_code, 200)
                self.assertFalse(response.get_json()["changed"])
                self.assertEqual(capture["kinds"]["SELECT"], 2)
                self.assertEqual(capture["writes"], [])
                self.assertEqual(capture["commits"], 0)

    def test_changed_planning_poll_reuses_shared_collections(self):
        for mission_type in ("arrival", "departure"):
            for index in range(6):
                tail = f"N{mission_type[0].upper()}{index:03d}UP"
                flight_number = (
                    f"UPS{1000 + index}"
                    if mission_type == "arrival"
                    else f"UPS{800 + index}"
                )
                mission = self._mission(
                    mission_type,
                    flight_number,
                    assigned_tail_number=tail,
                    origin=(f"A{index:02d}" if mission_type == "arrival" else "RFD"),
                    destination=(
                        "RFD" if mission_type == "arrival" else f"D{index:02d}"
                    ),
                )
                db.session.add(mission)
                db.session.flush()
                db.session.add(
                    SortDateCrewAssignment(
                        sort_date_mission_id=mission.id,
                        aircraft_section="topside",
                        required=True,
                    )
                )
                if index % 2 == 0:
                    db.session.add(
                        SortDateParkingAssignment(
                            sort_date_operation_id=self.operation.id,
                            tail_number=tail,
                            ramp_code="A",
                            position_code=(
                                f"A{index + 1:02d}"
                                if mission_type == "arrival"
                                else f"B{index + 1:02d}"
                            ),
                            lane_number=1,
                        )
                    )
                if index % 2 == 1:
                    db.session.add(
                        SortDateTailState(
                            sort_date=self.operation.sort_date,
                            gateway_code=self.gateway.code,
                            sort_name=self.operation.sort_name,
                            tail_number=tail,
                            parking_position=f"REMOTE {index}",
                            aircraft_type_source="unknown",
                            operational_status="normal",
                            deice_status="unknown",
                        )
                    )

            for index in range(3):
                db.session.add(
                    FlightApiReviewItem(
                        sort_date_operation_id=self.operation.id,
                        gateway_id=self.gateway.id,
                        gateway_code=self.gateway.code,
                        sort_date=self.operation.sort_date,
                        sort_name=self.operation.sort_name,
                        mission_type=mission_type,
                        review_key=f"shared:{mission_type}:{index}",
                        review_status="pending",
                        flight_number=f"UPS{3000 + index}",
                        origin="SDF" if mission_type == "arrival" else "RFD",
                        destination="RFD" if mission_type == "arrival" else "SDF",
                        revised_time_utc=datetime(2026, 8, 11, 3, index),
                        tail_number=f"NR{index:03d}UP",
                        raw_payload="{}",
                    )
                )

        db.session.add_all(
            [
                SortDateParkingAssignment(
                    sort_date_operation_id=self.operation.id,
                    tail_number="NPARKUP",
                    ramp_code="A",
                    position_code="C01",
                    lane_number=1,
                ),
                SortDateTailState(
                    sort_date=self.operation.sort_date,
                    gateway_code=self.gateway.code,
                    sort_name=self.operation.sort_name,
                    tail_number="NSTATEUP",
                    aircraft_type_source="unknown",
                    operational_status="normal",
                    deice_status="unknown",
                ),
                FlightApiReviewItem(
                    sort_date_operation_id=self.operation.id,
                    gateway_id=self.gateway.id,
                    gateway_code=self.gateway.code,
                    sort_date=self.operation.sort_date,
                    sort_name=self.operation.sort_name,
                    mission_type="departure",
                    review_key="shared:departure:hot",
                    review_status="pending",
                    flight_number="UPS9329",
                    origin="RFD",
                    destination="SDF",
                    revised_time_utc=datetime(2026, 8, 11, 4, 0),
                    tail_number="ND000UP",
                    raw_payload=json.dumps(
                        {
                            "source": "ALP",
                            "line_number": 32,
                            "reason": "No current operation mission match.",
                        }
                    ),
                ),
            ]
        )
        db.session.commit()

        for mission_type in ("arrival", "departure"):
            with self.subTest(mission_type=mission_type):
                state_url = self._state_url(mission_type, revision="stale")
                self.client.get(state_url)
                with self._capture_sql() as capture:
                    response = self.client.get(state_url)

                payload = response.get_json()
                selects = capture["selects"]
                self.assertEqual(response.status_code, 200)
                self.assertTrue(payload["changed"])
                self.assertLessEqual(len(selects), 12)
                self.assertEqual(capture["writes"], [])
                self.assertEqual(capture["commits"], 0)
                self.assertEqual(
                    self._select_count(selects, "sort_date_crew_assignments"),
                    1,
                )
                self.assertLessEqual(
                    self._select_count(selects, "sort_date_missions"),
                    2,
                )
                self.assertLessEqual(
                    self._select_count(selects, "flight_api_review_items"),
                    2,
                )
                self.assertLessEqual(
                    self._select_count(selects, "sort_date_parking_assignments"),
                    2,
                )
                self.assertLessEqual(
                    self._select_count(selects, "sort_date_tail_states"),
                    2,
                )
                self.assertFalse(
                    any(
                        "motherbrain_google_integration_settings" in statement.lower()
                        for statement in selects
                    )
                )

                if mission_type == "departure":
                    self.assertNotIn("UPS9329", payload["fragments"]["review"])
                    self.assertIn("NPARKUP", payload["fragments"]["mobile_missions"])
                    self.assertIn("NSTATEUP", payload["fragments"]["mobile_missions"])

    def test_changed_planning_crew_query_count_is_bounded_as_missions_grow(self):
        crew_query_counts = []
        total_select_counts = []
        for target_count in (1, 9):
            existing_count = SortDateMission.query.filter_by(
                sort_date_operation_id=self.operation.id,
                mission_type="arrival",
            ).count()
            for index in range(existing_count, target_count):
                mission = self._mission(
                    "arrival",
                    f"UPS{1200 + index}",
                    assigned_tail_number=f"NS{index:03d}UP",
                )
                db.session.add(mission)
                db.session.flush()
                db.session.add(
                    SortDateCrewAssignment(
                        sort_date_mission_id=mission.id,
                        aircraft_section="topside",
                        required=True,
                    )
                )
            db.session.commit()

            with self._capture_sql() as capture:
                response = self.client.get(
                    self._state_url("arrival", revision="stale")
                )
            self.assertEqual(response.status_code, 200)
            crew_query_counts.append(
                self._select_count(
                    capture["selects"],
                    "sort_date_crew_assignments",
                )
            )
            total_select_counts.append(len(capture["selects"]))

        self.assertEqual(crew_query_counts, [1, 1])
        self.assertEqual(total_select_counts[0], total_select_counts[1])

    def test_changed_departure_preview_bulk_suppresses_hot_rows(self):
        mission = self._mission(
            "departure",
            "UPS1382",
            assigned_tail_number="N409UP",
            destination="MEM",
        )
        db.session.add(mission)
        db.session.flush()
        save_alp_preview_state(
            self.operation,
            "departure",
            "\n".join(
                [
                    "11-AUG-2026\tUPS999\tSDF\tN999UP\tA01\tScheduled\t07:24 (S)",
                    "11-AUG-2026\tUPS998\tDFW\tN998UP\tA01\tScheduled\t07:30 (S)",
                    "11-AUG-2026\tUPS9329\tRFD\tN409UP\tA01\tScheduled\t07:35 (S)",
                ]
            ),
            self.user,
        )
        db.session.commit()

        state_url = self._state_url("departure", revision="stale")
        self.client.get(state_url)
        with self._capture_sql() as capture:
            response = self.client.get(state_url)

        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["changed"])
        self.assertIn("UPS0999", payload["fragments"]["review"])
        self.assertIn("UPS0998", payload["fragments"]["review"])
        self.assertNotIn("UPS9329", payload["fragments"]["review"])
        self.assertLessEqual(
            self._select_count(capture["selects"], "sort_date_missions"),
            2,
        )
        self.assertEqual(capture["writes"], [])
        self.assertEqual(capture["commits"], 0)

    def test_revisionless_stale_client_is_rejected_without_expensive_context(self):
        with (
            patch(
                "app.neomotherbrain.routes._planning_live_collections"
            ) as collections,
            patch(
                "app.neomotherbrain.routes.planning_state_revision"
            ) as revision,
        ):
            response = self.client.get(self._state_url("arrival"))

        payload = response.get_json()
        self.assertEqual(response.status_code, 428)
        self.assertFalse(payload["changed"])
        self.assertTrue(payload["reload_required"])
        self.assertIn("refresh", payload)
        collections.assert_not_called()
        revision.assert_not_called()

    def test_planning_revision_poll_still_requires_authentication_and_node_role(self):
        anonymous = self.app.test_client()
        with patch(
            "app.neomotherbrain.routes.planning_state_revision"
        ) as revision:
            response = anonymous.get(
                self._state_url("arrival", revision="known")
            )
        self.assertEqual(response.status_code, 302)
        revision.assert_not_called()

        watcher = User(username="live-watcher", role="watcher")
        set_user_password(watcher, "TestPassword123!")
        db.session.add(watcher)
        db.session.flush()
        backfill_default_gateway_node_roles(watcher, role="watcher")
        db.session.commit()
        anonymous.post(
            "/login",
            data={"username": watcher.username, "password": "TestPassword123!"},
        )
        with patch(
            "app.neomotherbrain.routes.planning_state_revision"
        ) as revision:
            response = anonymous.get(
                self._state_url("departure", revision="known")
            )
        self.assertEqual(response.status_code, 302)
        revision.assert_not_called()

    def test_planning_revision_tracks_mission_review_parking_and_tail_state(self):
        revision = planning_state_revision(
            self.operation,
            "arrival",
            self.user,
        )

        mission = self._mission("arrival", "UPS0910", origin="SDF")
        db.session.add(mission)
        db.session.commit()
        revision = self._assert_revision_changed(revision, "arrival")

        mission.assigned_tail_number = "N910UP"
        mission.updated_at = mission.updated_at + timedelta(seconds=1)
        db.session.commit()
        revision = self._assert_revision_changed(revision, "arrival")

        db.session.add(
            FlightApiReviewItem(
                sort_date_operation_id=self.operation.id,
                gateway_id=self.gateway.id,
                gateway_code=self.gateway.code,
                sort_date=self.operation.sort_date,
                sort_name=self.operation.sort_name,
                mission_type="arrival",
                review_key="api:arrival:ups0948",
                review_status="pending",
                flight_number="UPS0948",
                origin="SDF",
                destination="RFD",
            )
        )
        db.session.commit()
        revision = self._assert_revision_changed(revision, "arrival")

        db.session.add(
            SortDateParkingAssignment(
                sort_date_operation_id=self.operation.id,
                tail_number="N910UP",
                ramp_code="A",
                position_code="A01",
                lane_number=1,
            )
        )
        db.session.commit()
        revision = self._assert_revision_changed(revision, "arrival")

        db.session.add(
            SortDateTailState(
                sort_date=self.operation.sort_date,
                gateway_code=self.gateway.code,
                sort_name=self.operation.sort_name,
                tail_number="N910UP",
                aircraft_type_source="unknown",
                operational_status="normal",
                deice_status="unknown",
            )
        )
        db.session.commit()
        self._assert_revision_changed(revision, "arrival")

    def test_normal_planning_page_keeps_global_lifecycle_maintenance(self):
        with (
            patch(
                "app.services.operation_lifecycle.ensure_operational_sort_operations"
            ) as lifecycle,
            patch(
                "app.services.unmatched_review_alerts.expire_unmatched_review_alerts",
                return_value=False,
            ) as alert_expiration,
        ):
            response = self.client.get(
                f"/motherbrain/operations/{self.operation.id}/alp/arrival"
            )

        self.assertEqual(response.status_code, 200)
        lifecycle.assert_called_once_with(self.gateway)
        alert_expiration.assert_called_once_with(self.gateway)

    def test_stale_mission_save_returns_structured_field_conflict(self):
        mission = self._mission(
            "departure",
            "UPS0856",
            destination="DFW",
            planned_datetime_local=datetime(2026, 8, 10, 23, 42),
        )
        db.session.add(mission)
        db.session.commit()
        expected_version = entity_version(mission)
        original_values = self._original_mission_values(mission)

        mission.planned_datetime_local = datetime(2026, 8, 10, 23, 47)
        mission.planned_datetime_utc = datetime(2026, 8, 11, 4, 47)
        mission.updated_at = mission.updated_at + timedelta(seconds=1)
        db.session.commit()

        payload = self._mission_form_payload(
            mission,
            planned_time_local="23:50",
            expected_version=expected_version,
            original_values=json.dumps(original_values),
        )
        response = self.client.post(
            self._edit_url(mission),
            data=payload,
            headers={
                "Accept": "application/json",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        conflict = response.get_json()["conflict"]
        db.session.refresh(mission)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(conflict["type"], "stale_version")
        self.assertEqual(conflict["fields"][0]["field"], "planned_time_local")
        self.assertEqual(conflict["fields"][0]["original"], "23:42")
        self.assertEqual(conflict["fields"][0]["current"], "23:47")
        self.assertEqual(conflict["fields"][0]["submitted"], "23:50")
        self.assertIn("changed from 23:42 to 23:47", conflict["message"])
        self.assertEqual(mission.planned_datetime_local.time(), time(23, 47))

    def test_authorized_overwrite_can_resolve_stale_edit(self):
        mission = self._mission("arrival", "UPS0910", origin="SDF")
        db.session.add(mission)
        db.session.commit()
        expected_version = entity_version(mission)
        original_values = self._original_mission_values(mission)
        mission.assigned_tail_number = "NREMOTE"
        mission.updated_at = mission.updated_at + timedelta(seconds=1)
        db.session.commit()

        response = self.client.post(
            self._edit_url(mission),
            data=self._mission_form_payload(
                mission,
                assigned_tail_number="NMINE",
                expected_version=expected_version,
                original_values=json.dumps(original_values),
                force_overwrite="1",
            ),
            follow_redirects=False,
        )
        db.session.refresh(mission)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(mission.assigned_tail_number, "NMINE")

    def test_force_overwrite_cannot_bypass_existing_edit_permission(self):
        mission = self._mission("arrival", "UPS0911", origin="SDF")
        db.session.add(mission)
        operator = User(username="live-viewer", role="operator")
        set_user_password(operator, "TestPassword123!")
        db.session.add(operator)
        db.session.flush()
        backfill_default_gateway_node_roles(operator, role="operator")
        db.session.commit()

        self.client.post("/logout")
        self.client.post(
            "/login",
            data={"username": operator.username, "password": "TestPassword123!"},
        )
        response = self.client.post(
            self._edit_url(mission),
            data=self._mission_form_payload(
                mission,
                assigned_tail_number="NUNAUTHORIZED",
                expected_version=entity_version(mission),
                original_values=json.dumps(self._original_mission_values(mission)),
                force_overwrite="1",
            ),
            follow_redirects=False,
        )
        db.session.refresh(mission)

        self.assertEqual(response.status_code, 302)
        self.assertNotEqual(mission.assigned_tail_number, "NUNAUTHORIZED")

    def test_resolved_review_item_action_returns_conflict_without_recreating(self):
        item = FlightApiReviewItem(
            sort_date_operation_id=self.operation.id,
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
            sort_date=self.operation.sort_date,
            sort_name=self.operation.sort_name,
            mission_type="arrival",
            review_key="api:arrival:ups0910",
            review_status="pending",
            flight_number="UPS0910",
            origin="SDF",
            destination="RFD",
            revised_time_utc=datetime(2026, 8, 11, 3, 0),
        )
        db.session.add(item)
        db.session.commit()
        expected_version = entity_version(item)
        item.review_status = "ignored"
        item.updated_at = item.updated_at + timedelta(seconds=1)
        db.session.commit()

        response = self.client.post(
            f"/motherbrain/operations/{self.operation.id}/planning/api/{item.id}/add",
            data={
                "mission_type": "arrival",
                "wave": "",
                "expected_version": expected_version,
            },
            headers={
                "Accept": "application/json",
                "X-Requested-With": "XMLHttpRequest",
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["conflict"]["type"], "item_changed")
        self.assertEqual(SortDateMission.query.count(), 0)
        self.assertEqual(item.review_status, "ignored")

    def test_deleted_review_item_action_returns_conflict_without_recreating(self):
        item = FlightApiReviewItem(
            sort_date_operation_id=self.operation.id,
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
            sort_date=self.operation.sort_date,
            sort_name=self.operation.sort_name,
            mission_type="arrival",
            review_key="api:arrival:ups0948",
            review_status="pending",
            flight_number="UPS0948",
            origin="SDF",
            destination="RFD",
            revised_time_utc=datetime(2026, 8, 11, 3, 15),
        )
        db.session.add(item)
        db.session.commit()
        item_id = item.id
        expected_version = entity_version(item)
        db.session.delete(item)
        db.session.commit()

        response = self.client.post(
            f"/motherbrain/operations/{self.operation.id}/planning/api/{item_id}/add",
            data={
                "mission_type": "arrival",
                "wave": "",
                "expected_version": expected_version,
            },
            headers={
                "Accept": "application/json",
                "X-Requested-With": "XMLHttpRequest",
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["conflict"]["type"], "item_changed")
        self.assertEqual(SortDateMission.query.count(), 0)

    def test_deleted_mission_action_returns_conflict_without_recreating(self):
        mission = self._mission("arrival", "UPS0952", origin="SDF")
        db.session.add(mission)
        db.session.commit()
        mission_id = mission.id
        expected_version = entity_version(mission)
        db.session.delete(mission)
        db.session.commit()

        response = self.client.post(
            f"/motherbrain/operations/{self.operation.id}/missions/{mission_id}/cancel",
            data={
                "mission_type": "arrival",
                "expected_version": expected_version,
            },
            headers={
                "Accept": "application/json",
                "X-Requested-With": "XMLHttpRequest",
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["conflict"]["type"], "item_changed")
        self.assertEqual(SortDateMission.query.count(), 0)

    def _state_url(self, mission_type, revision=None):
        url = (
            f"/motherbrain/operations/{self.operation.id}/planning/"
            f"{mission_type}/state"
        )
        if revision is not None:
            url += f"?revision={revision}"
        return url

    def _assert_revision_changed(self, previous, mission_type):
        current = planning_state_revision(
            self.operation,
            mission_type,
            self.user,
        )
        self.assertNotEqual(current, previous)
        return current

    def _edit_url(self, mission):
        return (
            f"/motherbrain/operations/{self.operation.id}/missions/"
            f"{mission.id}/edit"
        )

    def _mission(self, mission_type, flight_number, **overrides):
        values = {
            "sort_date_operation_id": self.operation.id,
            "sort_date": self.operation.sort_date,
            "gateway_code": self.gateway.code,
            "sort_name": self.operation.sort_name,
            "mission_type": mission_type,
            "mission_source": "manual",
            "wave": "1",
            "flight_number": flight_number,
            "origin": "RFD" if mission_type == "departure" else "SDF",
            "destination": "SDF" if mission_type == "departure" else "RFD",
            "timezone": "America/Chicago",
            "planned_datetime_local": datetime(2026, 8, 10, 23, 42),
            "planned_datetime_utc": datetime(2026, 8, 11, 4, 42),
            "planned_source": "manual",
            "eta_source": "unknown",
            "actual_block_in_source": "unknown",
            "actual_block_out_source": "unknown",
            "tail_source": "unknown",
            "fuel_status": "waiting",
            "arrival_status": "scheduled" if mission_type == "arrival" else None,
            "departure_status": "scheduled" if mission_type == "departure" else None,
        }
        values.update(overrides)
        return SortDateMission(**values)

    def _original_mission_values(self, mission):
        return {
            "mission_type": mission.mission_type,
            "wave": mission.wave,
            "flight_number": mission.flight_number,
            "origin": mission.origin,
            "destination": mission.destination,
            "assigned_tail_number": mission.assigned_tail_number or "",
            "planned_time_local": mission.planned_datetime_local.strftime("%H:%M"),
            "timezone": mission.timezone,
            "eta_datetime_utc": "",
            "actual_block_in_datetime_utc": "",
            "actual_block_out_datetime_utc": "",
            "planned_fuel_load": "",
            "fuel_status": mission.fuel_status or "",
            "arrival_status": mission.arrival_status or "",
            "departure_status": mission.departure_status or "",
            "pure_pull_time_local": "",
            "mix_pull_time_local": "",
        }

    def _mission_form_payload(self, mission, **overrides):
        values = self._original_mission_values(mission)
        values.update(overrides)
        return values

    @contextmanager
    def _capture_sql(self):
        capture = {
            "statements": [],
            "selects": [],
            "writes": [],
            "kinds": Counter(),
            "commits": 0,
        }
        engine = db.engine

        def before_cursor_execute(
            _connection,
            _cursor,
            statement,
            _parameters,
            _context,
            _executemany,
        ):
            normalized = " ".join(statement.split())
            kind = normalized.split(" ", 1)[0].upper()
            capture["statements"].append(normalized)
            capture["kinds"][kind] += 1
            if kind == "SELECT":
                capture["selects"].append(normalized)
            elif kind in {"INSERT", "UPDATE", "DELETE"}:
                capture["writes"].append(normalized)

        def on_commit(_connection):
            capture["commits"] += 1

        event.listen(engine, "before_cursor_execute", before_cursor_execute)
        event.listen(engine, "commit", on_commit)
        try:
            yield capture
        finally:
            event.remove(engine, "before_cursor_execute", before_cursor_execute)
            event.remove(engine, "commit", on_commit)

    @staticmethod
    def _select_count(statements, table_name):
        return sum(
            1 for statement in statements if table_name in statement.lower()
        )


if __name__ == "__main__":
    unittest.main()
