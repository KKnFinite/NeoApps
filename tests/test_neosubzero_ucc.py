import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from app import create_app
from app.extensions import db
from app.models import (
    Gateway,
    LiveScreenRefreshSetting,
    NeoSubZeroCalloutAssignment,
    NeoSubZeroDepartureDeiceEvent,
    NeoSubZeroPretreatState,
    NeoSubZeroUccAssignment,
    SortDateMission,
    SortDateOperation,
    SortDateParkingAssignment,
    SortDateTailState,
    StaffingPerson,
    StaffingUnit,
    StaffingWorkAssignment,
    User,
)
from app.neonodes.neosubzero.routes import _coordinator_workspace_state
from app.services.access_control import backfill_default_gateway_node_roles
from app.services.live_collaboration import entity_version
from app.services.neosubzero_staffing import (
    DEICE_QUALIFICATION_KEY,
    deactivate_neosubzero_callouts_for_attendance,
    set_neosubzero_callout_membership,
    set_staffing_person_qualification,
)
from app.services.neosubzero_ucc import (
    UCC_REFRESH_KEY,
    NeoSubZeroUccError,
    move_neosubzero_ucc_assignment,
    neosubzero_ucc_context,
    neosubzero_ucc_revision,
    set_neosubzero_ucc_assignment,
)
from app.services.password_policy import set_user_password
from app.services.permission_rules import (
    DEFAULT_PERMISSION_RULES,
    ensure_default_permission_rules,
)


class NeoSubZeroUccTest(unittest.TestCase):
    def setUp(self):
        config = type(
            "TestConfig",
            (),
            {
                "SECRET_KEY": "test",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
                "DEFAULT_GATEWAY_CODE": "RFD",
            },
        )
        self.app = create_app(config)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        ensure_default_permission_rules()
        self.gateway = Gateway(code="RFD", name="RFD", is_active=True)
        db.session.add(self.gateway)
        db.session.flush()
        self.operation = SortDateOperation(
            sort_date=date(2026, 8, 31),
            gateway_id=self.gateway.id,
            gateway_code="RFD",
            sort_name="night",
        )
        db.session.add(self.operation)
        db.session.flush()
        self.remote = self._departure("5X100", "N100", "SDF", 1)
        self.alpha = self._departure("5X200", "N200", "ONT", 2)
        db.session.add_all(
            [
                SortDateParkingAssignment(
                    sort_date_operation_id=self.operation.id,
                    tail_number="N100",
                    ramp_code="R",
                    position_code="R09",
                ),
                SortDateParkingAssignment(
                    sort_date_operation_id=self.operation.id,
                    tail_number="N200",
                    ramp_code="A",
                    position_code="A03",
                ),
            ]
        )
        night = self._unit("sort", "Night")
        aviation = self._unit("operation", "Aviation Services", night)
        deice = self._unit("work_area", "Deice", aviation)
        hub = self._unit("operation", "Hub", night)
        unload = self._unit("work_area", "Unload", hub)
        self.permanent = self._person("D100", "Permanent", "Deicer", deice)
        self.callout = self._person("D200", "Callout", "Deicer", unload)
        for person in (self.permanent, self.callout):
            set_staffing_person_qualification(
                person,
                DEICE_QUALIFICATION_KEY,
                True,
            )
        self.callout_row = set_neosubzero_callout_membership(
            self.operation,
            self.callout,
            True,
        )
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_context_uses_canonical_ramps_parking_and_subzero_states(self):
        db.session.add(
            NeoSubZeroPretreatState(
                sort_date_operation_id=self.operation.id,
                tail_number="N200",
                pretreat_planned=True,
            )
        )
        db.session.add(
            SortDateTailState(
                sort_date=self.operation.sort_date,
                gateway_code="RFD",
                sort_name="night",
                tail_number="N100",
                deice_status="negative",
            )
        )
        db.session.commit()
        context = neosubzero_ucc_context(self.gateway, self.operation)
        self.assertEqual([ramp["name"] for ramp in context["ramps"]], ["Remote", "Alpha"])
        self.assertIsNone(context["ramps"][0]["throat"])
        self.assertEqual(context["ramps"][0]["aircraft"][0]["parking"], "R09")
        self.assertEqual(context["ramps"][0]["aircraft"][0]["visual_state"], "negative")
        self.assertEqual(context["ramps"][1]["aircraft"][0]["visual_state"], "pretreat-planned")
        self.assertEqual(
            {row["person"].id for row in context["staffing_pool"]},
            {self.permanent.id, self.callout.id},
        )

    def test_configured_departure_occupies_throat_without_block_out(self):
        event = self._deice_event(
            self.alpha,
            "configured",
            configured_at=datetime(2026, 8, 31, 0, 45),
        )
        db.session.commit()
        self.assertIsNone(self.alpha.actual_block_out_datetime_utc)
        alpha = self._ramp_context("Alpha")
        self.assertEqual(alpha["throat"]["mission_id"], self.alpha.id)
        self.assertEqual(alpha["throat"]["event"].id, event.id)
        self.assertEqual(alpha["throat"]["parking"], "A03")

    def test_configured_aircraft_wins_over_multiple_planned_queue_rows(self):
        planned_two = self._departure("5X201", "N201", "SLC", 3)
        configured = self._departure("5X202", "N202", "DEN", 4)
        db.session.add_all(
            [
                SortDateParkingAssignment(
                    sort_date_operation_id=self.operation.id,
                    tail_number="N201",
                    ramp_code="A",
                    position_code="A04",
                ),
                SortDateParkingAssignment(
                    sort_date_operation_id=self.operation.id,
                    tail_number="N202",
                    ramp_code="A",
                    position_code="A05",
                ),
            ]
        )
        self._deice_event(self.alpha, "deice_planned")
        self._deice_event(planned_two, "deice_planned")
        self._deice_event(
            configured,
            "configured",
            configured_at=datetime(2026, 8, 31, 0, 50),
        )
        self.alpha.actual_block_out_datetime_utc = datetime(2026, 8, 31, 1, 40)
        planned_two.actual_block_out_datetime_utc = datetime(2026, 8, 31, 1, 20)
        db.session.commit()
        alpha = self._ramp_context("Alpha")
        self.assertEqual(alpha["throat"]["mission_id"], configured.id)
        self.assertEqual(
            [row["mission_id"] for row in alpha["waiting_queue"]],
            [planned_two.id, self.alpha.id],
        )
        self.assertEqual(
            [row["queue_position"] for row in alpha["waiting_queue"]],
            [1, 2],
        )

    def test_coordinator_selection_never_changes_universal_throat(self):
        self._deice_event(self.alpha, "deice_planned")
        configured = self._departure("5X203", "N203", "DFW", 3)
        db.session.add(
            SortDateParkingAssignment(
                sort_date_operation_id=self.operation.id,
                tail_number="N203",
                ramp_code="A",
                position_code="A06",
            )
        )
        self._deice_event(
            configured,
            "configured",
            configured_at=datetime(2026, 8, 31, 0, 55),
        )
        db.session.commit()
        before = self._ramp_context("Alpha")["throat"]["mission_id"]
        departure_rows = self._departure_rows()
        with self.app.test_request_context(
            f"/neosubzero/coordinator?ramp=Alpha&mission={self.alpha.id}"
        ):
            workspace = _coordinator_workspace_state(self.operation, departure_rows)
            self.assertEqual(workspace["selected_mission_id"], self.alpha.id)
            after = self._ramp_context("Alpha")["throat"]["mission_id"]
        self.assertEqual(before, configured.id)
        self.assertEqual(after, configured.id)

    def test_cleared_or_reset_event_leaves_throat(self):
        next_mission = self._departure("5X204", "N204", "PHX", 3)
        db.session.add(
            SortDateParkingAssignment(
                sort_date_operation_id=self.operation.id,
                tail_number="N204",
                ramp_code="A",
                position_code="A04",
            )
        )
        next_event = self._deice_event(
            next_mission,
            "configured",
            configured_at=datetime(2026, 8, 31, 0, 55),
        )
        event = self._deice_event(
            self.alpha,
            "configured",
            configured_at=datetime(2026, 8, 31, 0, 45),
        )
        db.session.commit()
        self.assertEqual(self._ramp_context("Alpha")["throat"]["mission_id"], self.alpha.id)

        event.status = "finished"
        db.session.commit()
        finished = self._ramp_context("Alpha")["throat"]
        self.assertEqual(finished["mission_id"], self.alpha.id)
        self.assertEqual(finished["visual_label"], "FINISHED / AWAITING CLEARANCE")

        event.status = "cleared"
        db.session.commit()
        self.assertEqual(
            self._ramp_context("Alpha")["throat"]["mission_id"],
            next_mission.id,
        )

        next_event.status = "deice_planned"
        next_event.configured_at_utc = None
        db.session.commit()
        alpha = self._ramp_context("Alpha")
        self.assertIsNone(alpha["throat"])
        self.assertEqual(alpha["waiting_queue"], ())

        next_mission.actual_block_out_datetime_utc = datetime(2026, 8, 31, 1, 15)
        db.session.commit()
        alpha = self._ramp_context("Alpha")
        self.assertEqual(
            [row["mission_id"] for row in alpha["waiting_queue"]],
            [next_mission.id],
        )
        self.assertEqual(alpha["waiting_queue"][0]["queue_position"], 1)

    def test_parking_position_and_pretreat_configuration_do_not_imply_throat(self):
        pretreat = NeoSubZeroPretreatState(
            sort_date_operation_id=self.operation.id,
            tail_number="N100",
            pretreat_planned=True,
            configured_at_utc=datetime(2026, 8, 31, 0, 30),
        )
        db.session.add(pretreat)
        db.session.commit()
        self.assertIsNone(self.remote.actual_block_out_datetime_utc)
        remote = self._ramp_context("Remote")
        self.assertIsNone(remote["throat"])
        self.assertEqual(remote["aircraft"][0]["parking"], "R09")
        self.assertEqual(remote["aircraft"][0]["visual_state"], "configured")

    def test_driver_flyer_assignment_and_duplicate_prevention(self):
        driver = set_neosubzero_ucc_assignment(
            self.operation, "Remote", 1, "driver", self.permanent
        )
        flyer = set_neosubzero_ucc_assignment(
            self.operation, "Remote", 1, "flyer", self.callout
        )
        db.session.commit()
        self.assertEqual(driver.person_id, self.permanent.id)
        self.assertEqual(flyer.person_id, self.callout.id)
        with self.assertRaises(NeoSubZeroUccError):
            set_neosubzero_ucc_assignment(
                self.operation, "Alpha", 2, "driver", self.permanent
            )
        self.assertEqual(NeoSubZeroUccAssignment.query.count(), 2)

    def test_ucc_assignment_moves_to_empty_slot_without_unassign_step(self):
        source = set_neosubzero_ucc_assignment(
            self.operation, "Remote", 1, "driver", self.permanent
        )
        db.session.commit()
        result = move_neosubzero_ucc_assignment(
            self.operation,
            "Alpha",
            2,
            "flyer",
            self.permanent,
            destination_assignment=None,
            source_assignment=source,
            resolution="",
        )
        db.session.commit()
        self.assertTrue(result["changed"])
        self.assertIsNone(source.person_id)
        self.assertEqual(result["destination"].person_id, self.permanent.id)

    def test_ucc_assignment_remove_replaces_occupied_destination_atomically(self):
        source = set_neosubzero_ucc_assignment(
            self.operation, "Remote", 1, "driver", self.permanent
        )
        destination = set_neosubzero_ucc_assignment(
            self.operation, "Alpha", 2, "flyer", self.callout
        )
        db.session.commit()
        move_neosubzero_ucc_assignment(
            self.operation,
            "Alpha",
            2,
            "flyer",
            self.permanent,
            destination_assignment=destination,
            source_assignment=source,
            resolution="remove",
        )
        db.session.commit()
        self.assertIsNone(source.person_id)
        self.assertEqual(destination.person_id, self.permanent.id)
        self.assertEqual(
            NeoSubZeroUccAssignment.query.filter_by(
                sort_date_operation_id=self.operation.id,
                person_id=self.permanent.id,
            ).count(),
            1,
        )

    def test_ucc_assignment_swap_exchanges_both_slots_atomically(self):
        source = set_neosubzero_ucc_assignment(
            self.operation, "Remote", 1, "driver", self.permanent
        )
        destination = set_neosubzero_ucc_assignment(
            self.operation, "Alpha", 2, "flyer", self.callout
        )
        db.session.commit()
        move_neosubzero_ucc_assignment(
            self.operation,
            "Alpha",
            2,
            "flyer",
            self.permanent,
            destination_assignment=destination,
            source_assignment=source,
            resolution="swap",
        )
        db.session.commit()
        self.assertEqual(source.person_id, self.callout.id)
        self.assertEqual(destination.person_id, self.permanent.id)

    def test_ucc_assignment_cancel_and_invalid_swap_leave_slots_unchanged(self):
        source = set_neosubzero_ucc_assignment(
            self.operation, "Remote", 1, "driver", self.permanent
        )
        destination = set_neosubzero_ucc_assignment(
            self.operation, "Alpha", 2, "flyer", self.callout
        )
        db.session.commit()
        result = move_neosubzero_ucc_assignment(
            self.operation,
            "Alpha",
            2,
            "flyer",
            self.permanent,
            destination_assignment=destination,
            source_assignment=source,
            resolution="cancel",
        )
        self.assertFalse(result["changed"])
        self.assertEqual(source.person_id, self.permanent.id)
        self.assertEqual(destination.person_id, self.callout.id)

        source.person_id = None
        db.session.commit()
        with self.assertRaisesRegex(NeoSubZeroUccError, "Swap requires"):
            move_neosubzero_ucc_assignment(
                self.operation,
                "Alpha",
                2,
                "flyer",
                self.permanent,
                destination_assignment=destination,
                source_assignment=None,
                resolution="swap",
            )
        self.assertEqual(destination.person_id, self.callout.id)

    def test_not_here_callout_clears_ucc_and_here_restores_pool_membership(self):
        slot = set_neosubzero_ucc_assignment(
            self.operation, "Alpha", 2, "flyer", self.callout
        )
        db.session.commit()
        deactivate_neosubzero_callouts_for_attendance(
            self.operation, {self.callout.id: "call_in"}
        )
        db.session.commit()
        self.assertFalse(self.callout_row.active)
        self.assertEqual(self.callout_row.removal_reason, "attendance")
        self.assertIsNone(slot.person_id)

        deactivate_neosubzero_callouts_for_attendance(
            self.operation, {self.callout.id: "here"}
        )
        db.session.commit()
        self.assertTrue(self.callout_row.active)
        self.assertIsNone(slot.person_id)

    def test_manual_callout_removal_and_qualification_revoke_clear_slots(self):
        slot = set_neosubzero_ucc_assignment(
            self.operation, "Alpha", 1, "driver", self.callout
        )
        set_neosubzero_callout_membership(
            self.operation,
            self.callout,
            False,
            assignment=self.callout_row,
        )
        db.session.commit()
        self.assertIsNone(slot.person_id)
        deactivate_neosubzero_callouts_for_attendance(
            self.operation, {self.callout.id: "here"}
        )
        self.assertFalse(self.callout_row.active)

        permanent_slot = set_neosubzero_ucc_assignment(
            self.operation, "Remote", 4, "flyer", self.permanent
        )
        qualification = next(
            row
            for row in self.permanent.qualifications
            if row.qualification_key == DEICE_QUALIFICATION_KEY
        )
        set_staffing_person_qualification(
            self.permanent,
            DEICE_QUALIFICATION_KEY,
            False,
            qualification=qualification,
        )
        db.session.commit()
        self.assertIsNone(permanent_slot.person_id)

    def test_ucc_permissions_route_refresh_and_settings_contract(self):
        defaults = {key: role for key, role, _ in DEFAULT_PERMISSION_RULES}
        self.assertEqual(defaults["neosubzero.ucc.view"], "watcher")
        self.assertEqual(defaults["neosubzero.ucc.edit"], "simulator")
        self.assertIn("neosubzero_ucc_assignments", db.metadata.tables)
        self.assertNotEqual(
            neosubzero_ucc_revision(
                self.gateway, self.operation, weather_revision="weather-r1"
            ),
            neosubzero_ucc_revision(
                self.gateway, self.operation, weather_revision="weather-r2"
            ),
        )

        watcher = self._user("ucc_watcher", "watcher")
        client = self.app.test_client()
        self._login(client, watcher)
        with patch(
            "app.neonodes.neosubzero.routes.current_neosubzero_operation",
            return_value=self.operation,
        ), patch(
            "app.neonodes.neosubzero.routes.neosubzero_weather_context",
            return_value=self._weather_context_fixture(),
        ):
            page = client.get("/neosubzero/ucc")
            denied = client.post(
                "/neosubzero/ucc",
                data={
                    "ramp": "Remote",
                    "position_number": 1,
                    "team_role": "driver",
                    "person_id": self.permanent.id,
                    "expected_version": "",
                },
            )
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"WEATHER OPERATIONS", page.data)
        self.assertIn(b"KRFD", page.data)
        self.assertIn(b"ACTIVE PRECIPITATION", page.data)
        self.assertIn(b"CURRENT SORT \xc2\xb7 HOURLY WEATHER", page.data)
        self.assertIn(b"2300", page.data)
        self.assertIn(b"FROST RISK \xc2\xb7 PRELIMINARY", page.data)
        self.assertIn(b"UPCOMING OPERATIONAL SORT WINDOWS", page.data)
        self.assertIn(b"28\xc2\xb0F", page.data)
        self.assertIn(b"CHANCE SNOW", page.data)
        self.assertNotIn(b"FUTURE_HOURLY_SHOULD_NOT_RENDER", page.data)
        self.assertIn(b"data-weather-motion-toggle", page.data)
        self.assertIn(b'data-weather-theme="snow"', page.data)
        self.assertIn(f'data-weather-user-id="{watcher.id}"'.encode(), page.data)
        self.assertIn(b"THROAT", page.data)
        self.assertIn(b"WAITING QUEUE", page.data)
        self.assertEqual(denied.status_code, 403)

        simulator = self._user("ucc_simulator", "simulator")
        self._login(client, simulator)
        before = neosubzero_ucc_revision(self.gateway, self.operation)
        with patch(
            "app.neonodes.neosubzero.routes.current_neosubzero_operation",
            return_value=self.operation,
        ):
            editable_page = client.get("/neosubzero/ucc")
            saved = client.post(
                "/neosubzero/ucc",
                data={
                    "ramp": "Remote",
                    "position_number": 1,
                    "team_role": "driver",
                    "person_id": self.permanent.id,
                    "expected_version": "",
                },
            )
            assigned_page = client.get("/neosubzero/ucc")
            revision = client.get("/neosubzero/ucc/revision?revision=old")
        self.assertIn(b"data-ucc-move-dialog", editable_page.data)
        self.assertIn(b'name="source_expected_version"', editable_page.data)
        self.assertIn(b'data-ucc-move-choice="remove"', editable_page.data)
        self.assertIn(b'data-ucc-move-choice="swap"', editable_page.data)
        self.assertIn(b'data-ucc-move-choice="cancel"', editable_page.data)
        self.assertIn(b"data-ucc-drag-assignee", assigned_page.data)
        self.assertEqual(saved.status_code, 302)
        self.assertEqual(revision.status_code, 200)
        self.assertNotEqual(before, neosubzero_ucc_revision(self.gateway, self.operation))
        self.assertEqual(UCC_REFRESH_KEY, "neosubzero.ucc")

        master = self._user("ucc_master", "master")
        self._login(client, master)
        settings = client.get("/neosubzero/settings")
        self.assertIn(b"UCC", settings.data)
        self.assertIn(b"neosubzero.ucc", settings.data)
        saved_refresh = client.post(
            "/neosubzero/settings",
            data={
                "action": "save_refresh",
                "screen_key": UCC_REFRESH_KEY,
                "refresh_interval_seconds": "10",
            },
        )
        self.assertEqual(saved_refresh.status_code, 302)
        self.assertEqual(
            LiveScreenRefreshSetting.query.filter_by(
                gateway_id=self.gateway.id,
                screen_key=UCC_REFRESH_KEY,
            ).one().interval_seconds,
            10,
        )

    def test_weather_motion_preference_and_reduced_motion_contract(self):
        root = Path(__file__).resolve().parents[1]
        javascript = (root / "app/static/js/neosubzero_ucc.js").read_text(encoding="utf-8")
        stylesheet = (root / "app/static/css/base.css").read_text(encoding="utf-8")
        self.assertIn("neosubzero.weather-motion.${weather.dataset.weatherUserId}", javascript)
        self.assertIn("window.localStorage.getItem", javascript)
        self.assertIn("window.localStorage.setItem", javascript)
        self.assertIn('window.matchMedia?.("(prefers-reduced-motion: reduce)")', javascript)
        self.assertIn("@media (prefers-reduced-motion: reduce)", stylesheet)
        self.assertIn("animation:none !important", stylesheet)

    def test_ucc_route_rejects_stale_slot_version(self):
        simulator = self._user("ucc_stale_simulator", "simulator")
        slot = set_neosubzero_ucc_assignment(
            self.operation, "Remote", 2, "driver", self.permanent
        )
        db.session.commit()
        original_version = entity_version(slot)
        slot.person_id = self.callout.id
        db.session.commit()
        client = self.app.test_client()
        self._login(client, simulator)
        with patch(
            "app.neonodes.neosubzero.routes.current_neosubzero_operation",
            return_value=self.operation,
        ):
            response = client.post(
                "/neosubzero/ucc",
                data={
                    "ramp": "Remote",
                    "position_number": 2,
                    "team_role": "driver",
                    "person_id": self.permanent.id,
                    "expected_version": original_version,
                },
                follow_redirects=True,
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"changed while you were editing", response.data)
        db.session.refresh(slot)
        self.assertEqual(slot.person_id, self.callout.id)

    def test_ucc_route_rejects_stale_source_during_move(self):
        simulator = self._user("ucc_move_stale_simulator", "simulator")
        source = set_neosubzero_ucc_assignment(
            self.operation, "Remote", 3, "driver", self.permanent
        )
        db.session.commit()
        stale_source_version = entity_version(source)
        source.assigned_at = source.assigned_at + timedelta(minutes=1)
        db.session.commit()
        client = self.app.test_client()
        self._login(client, simulator)
        with patch(
            "app.neonodes.neosubzero.routes.current_neosubzero_operation",
            return_value=self.operation,
        ):
            response = client.post(
                "/neosubzero/ucc",
                data={
                    "ramp": "Alpha",
                    "position_number": 3,
                    "team_role": "flyer",
                    "person_id": self.permanent.id,
                    "expected_version": "",
                    "source_assignment_id": source.id,
                    "source_expected_version": stale_source_version,
                    "move_resolution": "",
                },
                follow_redirects=True,
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"changed while you were moving", response.data)
        db.session.refresh(source)
        self.assertEqual(source.person_id, self.permanent.id)
        self.assertIsNone(
            NeoSubZeroUccAssignment.query.filter_by(
                sort_date_operation_id=self.operation.id,
                ramp="Alpha",
                position_number=3,
                team_role="flyer",
            ).one_or_none()
        )

    def test_ucc_route_swaps_two_current_slots_in_one_save(self):
        simulator = self._user("ucc_move_simulator", "simulator")
        source = set_neosubzero_ucc_assignment(
            self.operation, "Remote", 4, "driver", self.permanent
        )
        destination = set_neosubzero_ucc_assignment(
            self.operation, "Alpha", 4, "flyer", self.callout
        )
        db.session.commit()
        client = self.app.test_client()
        self._login(client, simulator)
        with patch(
            "app.neonodes.neosubzero.routes.current_neosubzero_operation",
            return_value=self.operation,
        ):
            response = client.post(
                "/neosubzero/ucc",
                data={
                    "ramp": "Alpha",
                    "position_number": 4,
                    "team_role": "flyer",
                    "person_id": self.permanent.id,
                    "expected_version": entity_version(destination),
                    "source_assignment_id": source.id,
                    "source_expected_version": entity_version(source),
                    "move_resolution": "swap",
                },
                follow_redirects=True,
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"UCC STAFFING SAVED", response.data)
        db.session.refresh(source)
        db.session.refresh(destination)
        self.assertEqual(source.person_id, self.callout.id)
        self.assertEqual(destination.person_id, self.permanent.id)

    def _departure(self, flight, tail, destination, hour):
        mission = SortDateMission(
            sort_date=self.operation.sort_date,
            gateway_code="RFD",
            sort_name="night",
            sort_date_operation_id=self.operation.id,
            mission_type="departure",
            mission_source="master",
            flight_number=flight,
            origin="RFD",
            destination=destination,
            timezone="America/Chicago",
            planned_datetime_local=datetime(2026, 8, 31, hour),
            planned_datetime_utc=datetime(2026, 8, 31, hour),
            assigned_tail_number=tail,
            departure_status="scheduled",
        )
        db.session.add(mission)
        db.session.flush()
        return mission

    def _deice_event(self, mission, status, *, configured_at=None):
        event = NeoSubZeroDepartureDeiceEvent(
            sort_date_operation_id=self.operation.id,
            sort_date_mission_id=mission.id,
            tail_number=mission.assigned_tail_number,
            status=status,
            configured_at_utc=configured_at,
        )
        db.session.add(event)
        db.session.flush()
        return event

    def _ramp_context(self, ramp_name):
        return next(
            row
            for row in neosubzero_ucc_context(self.gateway, self.operation)["ramps"]
            if row["name"] == ramp_name
        )

    def _departure_rows(self):
        from app.services.neosubzero_departure_deice import departure_deice_context

        return departure_deice_context(self.gateway, self.operation)["rows"]

    def _unit(self, unit_type, name, parent=None):
        unit = StaffingUnit(unit_type=unit_type, name=name, parent=parent, active=True)
        db.session.add(unit)
        db.session.flush()
        return unit

    def _person(self, employee_id, first_name, last_name, work_area):
        person = StaffingPerson(
            employee_id=employee_id,
            first_name=first_name,
            last_name=last_name,
            seniority_date=date(2020, 1, 1),
            classification="part_time",
            employee_status="active",
            active=True,
        )
        db.session.add(person)
        db.session.flush()
        db.session.add(
            StaffingWorkAssignment(
                person_id=person.id,
                work_area_unit_id=work_area.id,
                active=True,
            )
        )
        db.session.flush()
        return person

    def _user(self, username, role):
        user = User(
            username=username,
            email=f"{username}@example.com",
            employee_id=f"EMP-{username}",
            first_name="UCC",
            last_name="User",
            full_name="UCC User",
            role=role,
            is_active=True,
            email_verified_at=datetime.utcnow(),
        )
        set_user_password(user, "TestPassword123!")
        db.session.add(user)
        db.session.flush()
        backfill_default_gateway_node_roles(user, role=role)
        db.session.commit()
        return user

    def _weather_context_fixture(self):
        return {
            "station": "KRFD",
            "theme": "snow",
            "sources": {
                "current": "AviationWeather.gov",
                "forecast": "NOAA / National Weather Service",
            },
            "revision": "weather-r1",
            "current": {
                "available": True,
                "observed_at_label": "2026-08-31 23:00Z",
                "stale": False,
                "error": None,
                "temperature": "28\u00b0F",
                "dewpoint": "25\u00b0F",
                "spread": "3\u00b0F",
                "relative_humidity": "88%",
                "wind": "320\u00b0 12 kt G20 kt",
                "visibility": "4 SM",
                "conditions": "-SN",
                "sky": "OVC 1,500",
                "flight_category": "IFR",
            },
            "forecast_status": {
                "issued_at_label": "2026-08-31 22:00Z",
                "stale": False,
                "error": None,
            },
            "current_sort": {
                "date_label": "MON AUG 31",
                "window_label": "18:00\u201306:00",
                "hours": (
                    {
                        "time": "2300",
                        "temperature": "28\u00b0F",
                        "dewpoint": "25\u00b0F",
                        "spread": "3\u00b0F",
                        "probability": "70%",
                        "humidity": "88%",
                        "wind": "NW 15 mph",
                        "gust": "G25 mph",
                        "condition": "Chance Snow",
                        "frost_risk": {
                            "level": "HIGH",
                            "rationale": "near freezing, tight dew-point spread",
                        },
                    },
                ),
            },
            "future_forecast": (
                {
                    "date_label": "TUE SEP 01",
                    "window_label": "18:00\u201306:00",
                    "temperature_range": "25\u201330\u00b0F",
                    "dewpoint_range": "22\u201325\u00b0F",
                    "spread_range": "3\u20135\u00b0F",
                    "precipitation_probability": "70%",
                    "gust": "G25 mph",
                    "conditions": "CHANCE SNOW",
                    "wind": "NW 15 mph",
                    "hours": ({"condition": "FUTURE_HOURLY_SHOULD_NOT_RENDER"},),
                },
            ),
        }

    def _login(self, client, user):
        return client.post(
            "/login",
            data={"username": user.username, "password": "TestPassword123!"},
        )


if __name__ == "__main__":
    unittest.main()
