from datetime import date, datetime
import unittest
from unittest.mock import patch

from app import create_app
from app.extensions import db
from app.models import (
    Gateway,
    GatewayMembership,
    SortDateMission,
    SortDateOperation,
    SortDateParkingAssignment,
    StaffingDailyAttendance,
    StaffingPerson,
    StaffingUnit,
    StaffingWorkAssignment,
    User,
)
from app.neonodes.neorain.services import (
    neorain_outbound_late_summary,
    neorain_outbound_context,
    neorain_inbound_context,
    neorain_inbound_late_summary,
    neorain_outbound_revision,
    neorain_outbound_staffing_summary,
    set_neorain_late_metrics_included,
)
from app.services.access_control import backfill_default_gateway_node_roles
from app.services.google_rain_integration_mode import (
    NEO_ONLY,
    NEO_PRIMARY_GOOGLE_MIRROR,
    set_rain_integration_mode,
)
from app.services.password_policy import set_user_password
from app.services.live_collaboration import entity_version


class NeoRainOutboundTest(unittest.TestCase):
    def setUp(self):
        TestConfig = type(
            "TestConfig",
            (),
            {
                "SECRET_KEY": "test",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            },
        )
        self.app = create_app(TestConfig)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.gateway = Gateway(code="RFD", name="RFD")
        db.session.add(self.gateway)
        db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_current_operation_rows_are_departures_ordered_and_display_canonical_fields(self):
        operation = self._operation()
        later = self._mission(
            operation,
            "UPS200",
            "LAX",
            planned=datetime(2026, 8, 30, 2, 10),
            tail="N200UP",
            wave="2",
            status="blocked_out",
        )
        earlier = self._mission(
            operation,
            "UPS100",
            "SDF",
            planned=datetime(2026, 8, 30, 1, 5),
            tail="N100UP",
            wave="1",
            status="departed",
        )
        earlier.elmac_completed_at_utc = datetime(2026, 8, 30, 6, 1)
        earlier.elmac_completed_source = "manual"
        earlier.ramp_load_completed_at_utc = datetime(2026, 8, 30, 6, 5)
        earlier.crew_load_completed_at_utc = datetime(2026, 8, 30, 6, 10)
        earlier.actual_block_out_datetime_utc = datetime(2026, 8, 30, 6, 15)
        self._mission(
            operation,
            "ARR900",
            "RFD",
            planned=datetime(2026, 8, 30, 0, 30),
            mission_type="arrival",
        )
        db.session.add(
            SortDateParkingAssignment(
                sort_date_operation_id=operation.id,
                tail_number="N100UP",
                position_code="A12",
            )
        )
        db.session.commit()

        rows = neorain_outbound_context(self.gateway, operation=operation)["rows"]

        self.assertEqual([row["flight_number"] for row in rows], ["UPS100", "UPS200"])
        self.assertEqual(rows[0]["wave"], "1")
        self.assertEqual(rows[1]["wave"], "2")
        self.assertEqual(rows[0]["tail"], "N100UP")
        self.assertEqual(rows[0]["destination"], "SDF")
        self.assertEqual(rows[0]["parking"], "A12")
        self.assertEqual(rows[0]["planned_time"], "01:05")
        self.assertEqual(rows[0]["elmac"], "01:01")
        self.assertEqual(earlier.elmac_completed_source, "manual")
        self.assertEqual(rows[0]["ramp_load_complete"], "01:05")
        self.assertEqual(rows[0]["crew_load_complete"], "01:10")
        self.assertEqual(rows[0]["official_block_out"], "01:15")
        self.assertEqual(rows[0]["departure_variance"], "+310")
        self.assertEqual(rows[0]["no_return"], "NO RETURN")
        self.assertEqual(rows[0]["version"], entity_version(earlier))
        self.assertTrue(rows[0]["late_metrics_included"])
        self.assertEqual(rows[0]["late_metrics_inclusion_source"], "default")
        self.assertEqual(rows[1]["no_return"], "")
        self.assertEqual(rows[1]["status"], "BLOCKED OUT")
        self.assertEqual(later.id, rows[1]["mission_id"])

    def test_inbound_variance_uses_sta_and_exposes_late_metrics_defaults(self):
        operation = self._operation()
        arrival = self._mission(
            operation,
            "UPS300",
            "RFD",
            planned=datetime(2026, 8, 30, 1, 0),
            mission_type="arrival",
            wave="1",
        )
        arrival.eta_datetime_utc = datetime(2026, 8, 30, 0, 30)
        arrival.actual_block_in_datetime_utc = datetime(2026, 8, 30, 1, 1)
        no_wave = self._mission(
            operation,
            "UPS301",
            "RFD",
            planned=datetime(2026, 8, 30, 2, 0),
            mission_type="arrival",
            wave=None,
        )
        db.session.commit()
        rows = neorain_inbound_context(self.gateway, operation=operation)["rows"]
        self.assertEqual(rows[0]["eta_sta"], "19:30")
        self.assertEqual(rows[0]["arrival_variance"], "+1")
        self.assertTrue(rows[0]["late_metrics_included"])
        self.assertFalse(rows[1]["late_metrics_included"])

    def test_inbound_late_summary_groups_only_included_positive_block_in_variance(self):
        operation = self._operation()
        first = self._mission(operation, "UPS310", "RFD", planned=datetime(2026, 8, 30, 1, 0), mission_type="arrival", wave="1")
        second = self._mission(operation, "UPS311", "RFD", planned=datetime(2026, 8, 30, 2, 0), mission_type="arrival", wave="2")
        no_wave = self._mission(operation, "UPS312", "RFD", planned=datetime(2026, 8, 30, 3, 0), mission_type="arrival", wave=None)
        on_time = self._mission(operation, "UPS313", "RFD", planned=datetime(2026, 8, 30, 4, 0), mission_type="arrival", wave="1")
        first.actual_block_in_datetime_utc = datetime(2026, 8, 30, 1, 10)
        second.actual_block_in_datetime_utc = datetime(2026, 8, 30, 2, 5)
        no_wave.actual_block_in_datetime_utc = datetime(2026, 8, 30, 3, 15)
        no_wave.late_metrics_included_override = True
        on_time.actual_block_in_datetime_utc = datetime(2026, 8, 30, 4, 0)
        db.session.commit()
        summary = neorain_inbound_late_summary(operation)
        self.assertEqual(summary["first_wave"], {"aircraft_late": 1, "late_minutes": 10, "average": "10"})
        self.assertEqual(summary["second_wave"], {"aircraft_late": 1, "late_minutes": 5, "average": "5"})
        self.assertEqual(summary["total"], {"aircraft_late": 3, "late_minutes": 30, "average": "10"})

    def test_inbound_connects_earliest_same_tail_departure_after_arrival(self):
        operation = self._operation()
        inbound = self._mission(
            operation, "UPS400", "RFD", planned=datetime(2026, 8, 30, 1, 0),
            mission_type="arrival", tail="N400UP",
        )
        inbound.actual_block_in_datetime_utc = datetime(2026, 8, 30, 1, 10)
        self._mission(operation, "UPS401", "SDF", planned=datetime(2026, 8, 30, 1, 5), tail="N400UP")
        next_departure = self._mission(operation, "UPS402", "ONT", planned=datetime(2026, 8, 30, 1, 57), tail="N400UP")
        next_departure.actual_block_out_datetime_utc = datetime(2026, 8, 30, 1, 55)
        self._mission(operation, "UPS403", "LAX", planned=datetime(2026, 8, 30, 2, 30), tail="N999UP")
        db.session.commit()
        row = neorain_inbound_context(self.gateway, operation=operation)["rows"][0]
        self.assertEqual(row["connecting_outbound"], "UPS402")
        self.assertEqual(row["ground_time"], "0:45")

    def test_departure_variance_uses_canonical_std_and_handles_midnight(self):
        operation = self._operation()
        late = self._mission(
            operation,
            "UPS110",
            "SDF",
            planned=datetime(2026, 8, 30, 23, 55),
        )
        early = self._mission(
            operation,
            "UPS111",
            "ONT",
            planned=datetime(2026, 8, 30, 2, 0),
        )
        exact = self._mission(
            operation,
            "UPS112",
            "OAK",
            planned=datetime(2026, 8, 30, 3, 0),
        )
        missing = self._mission(
            operation,
            "UPS113",
            "LAX",
            planned=datetime(2026, 8, 30, 4, 0),
        )
        late.actual_block_out_datetime_utc = datetime(2026, 8, 31, 0, 7)
        early.actual_block_out_datetime_utc = datetime(2026, 8, 30, 1, 56)
        exact.actual_block_out_datetime_utc = datetime(2026, 8, 30, 3, 0)
        db.session.commit()

        rows = neorain_outbound_context(self.gateway, operation=operation)["rows"]
        variances = {row["flight_number"]: row["departure_variance"] for row in rows}

        self.assertEqual(variances[late.flight_number], "+12")
        self.assertEqual(variances[early.flight_number], "-4")
        self.assertEqual(variances[exact.flight_number], "0")
        self.assertEqual(variances[missing.flight_number], "-")

    def test_wave_fallback_and_no_current_sort_are_clean(self):
        operation = self._operation()
        self._mission(operation, "UPS300", "ONT", planned=datetime(2026, 8, 30, 3), wave=None)
        db.session.commit()

        self.assertEqual(
            neorain_outbound_context(self.gateway, operation=operation)["rows"][0]["wave"],
            "-",
        )
        self.assertEqual(neorain_outbound_context(self.gateway, operation=None)["rows"], [])

    def test_late_metrics_defaults_and_explicit_overrides_are_canonical(self):
        operation = self._operation()
        wave_one = self._mission(operation, "UPS310", "ONT", planned=datetime(2026, 8, 30, 3), wave="1")
        wave_two = self._mission(operation, "UPS311", "ONT", planned=datetime(2026, 8, 30, 4), wave="2")
        no_wave = self._mission(operation, "UPS312", "ONT", planned=datetime(2026, 8, 30, 5), wave=None)
        db.session.commit()

        self.assertTrue(neorain_outbound_context(self.gateway, operation=operation)["rows"][0]["late_metrics_included"])
        self.assertTrue(neorain_outbound_context(self.gateway, operation=operation)["rows"][1]["late_metrics_included"])
        self.assertFalse(neorain_outbound_context(self.gateway, operation=operation)["rows"][2]["late_metrics_included"])
        self.assertEqual(set_neorain_late_metrics_included(wave_one, False), {"changed": True, "included": False, "source": "override"})
        self.assertEqual(set_neorain_late_metrics_included(no_wave, True), {"changed": True, "included": True, "source": "override"})
        with self.assertRaises(ValueError):
            set_neorain_late_metrics_included(wave_two, "true")

    def test_late_summary_groups_included_late_departures_without_persisting_totals(self):
        operation = self._operation()
        wave_one_late = self._mission(operation, "UPS320", "ONT", planned=datetime(2026, 8, 30, 1), wave="1")
        wave_one_on_time = self._mission(operation, "UPS321", "ONT", planned=datetime(2026, 8, 30, 2), wave="1")
        wave_one_excluded = self._mission(operation, "UPS322", "ONT", planned=datetime(2026, 8, 30, 3), wave="1")
        wave_two_late = self._mission(operation, "UPS323", "ONT", planned=datetime(2026, 8, 30, 4), wave="2")
        no_wave_late = self._mission(operation, "UPS324", "ONT", planned=datetime(2026, 8, 30, 5), wave=None)
        wave_one_late.actual_block_out_datetime_utc = datetime(2026, 8, 30, 1, 1)
        wave_one_on_time.actual_block_out_datetime_utc = datetime(2026, 8, 30, 2)
        wave_one_excluded.actual_block_out_datetime_utc = datetime(2026, 8, 30, 3, 9)
        wave_two_late.actual_block_out_datetime_utc = datetime(2026, 8, 30, 4, 4)
        no_wave_late.actual_block_out_datetime_utc = datetime(2026, 8, 30, 5, 3)
        set_neorain_late_metrics_included(wave_one_excluded, False)
        set_neorain_late_metrics_included(no_wave_late, True)
        db.session.commit()

        summary = neorain_outbound_late_summary(operation)

        self.assertEqual(summary["first_wave"], {"aircraft_late": 1, "late_minutes": 1, "average": "1"})
        self.assertEqual(summary["second_wave"], {"aircraft_late": 1, "late_minutes": 4, "average": "4"})
        self.assertEqual(summary["total"], {"aircraft_late": 3, "late_minutes": 8, "average": "2.7"})
        self.assertEqual(neorain_outbound_context(self.gateway, operation=operation)["late_summary"], summary)

    def test_late_summary_zero_case_is_explicit(self):
        operation = self._operation()

        self.assertEqual(
            neorain_outbound_late_summary(operation),
            {
                "first_wave": {"aircraft_late": 0, "late_minutes": 0, "average": "0"},
                "second_wave": {"aircraft_late": 0, "late_minutes": 0, "average": "0"},
                "total": {"aircraft_late": 0, "late_minutes": 0, "average": "0"},
            },
        )

    def test_staffing_summary_uses_canonical_current_sort_hub_and_ramp_totals(self):
        operation = self._operation()
        staffing = self._staffing_totals(operation)
        other_operation = SortDateOperation(
            gateway_id=self.gateway.id,
            gateway_code="RFD",
            sort_date=date(2026, 8, 31),
            sort_name="night",
        )
        db.session.add(other_operation)
        db.session.flush()
        db.session.add(
            StaffingDailyAttendance(
                person_id=staffing["hub_absent"].id,
                attendance_date=other_operation.sort_date,
                sort_unit_id=staffing["sort"].id,
                work_area_unit_id=staffing["hub_area"].id,
                operation_unit_id=staffing["hub"].id,
                sort_date_operation_id=other_operation.id,
                status="here",
            )
        )
        db.session.commit()

        summary = neorain_outbound_staffing_summary(operation)

        self.assertEqual(summary, {
            "hub": {"on_payroll": 2, "worked": 1},
            "ramp": {"on_payroll": 2, "worked": 1},
        })
        self.assertEqual(
            neorain_outbound_context(self.gateway, operation=operation)["staffing_summary"],
            summary,
        )
        self.assertEqual(
            neorain_outbound_context(self.gateway, operation=None)["staffing_summary"],
            {"hub": {"on_payroll": 0, "worked": 0}, "ramp": {"on_payroll": 0, "worked": 0}},
        )

    def test_revision_changes_when_displayed_staffing_totals_change(self):
        operation = self._operation()
        staffing = self._staffing_totals(operation)
        db.session.commit()
        first = neorain_outbound_revision(self.gateway, operation=operation)

        staffing["hub_absent_record"].status = "here"
        db.session.commit()
        second = neorain_outbound_revision(self.gateway, operation=operation)

        self.assertNotEqual(first, second)

    def test_outbound_renders_read_only_hub_and_ramp_staffing_totals(self):
        operation = self._operation()
        self._staffing_totals(operation)
        db.session.commit()
        watcher = self._user("rain_staffing_watcher", "watcher")
        self._login(watcher)

        with patch(
            "app.neonodes.neorain.routes.current_neorain_outbound_operation",
            return_value=operation,
        ):
            response = self.client.get("/neorain/outbound")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b">HUB<", response.data)
        self.assertIn(b">RAMP<", response.data)
        self.assertIn(b"ON PAYROLL", response.data)
        self.assertIn(b"WORKED", response.data)

    def test_revision_changes_for_mission_and_parking_changes_without_writes(self):
        operation = self._operation()
        mission = self._mission(operation, "UPS400", "OAK", planned=datetime(2026, 8, 30, 4))
        db.session.commit()
        first = neorain_outbound_revision(self.gateway, operation=operation)

        mission.updated_at = datetime(2026, 8, 30, 12)
        db.session.commit()
        second = neorain_outbound_revision(self.gateway, operation=operation)
        db.session.add(
            SortDateParkingAssignment(
                sort_date_operation_id=operation.id,
                tail_number="N400UP",
                position_code="B2",
            )
        )
        db.session.commit()
        third = neorain_outbound_revision(self.gateway, operation=operation)

        self.assertNotEqual(first, second)
        self.assertNotEqual(second, third)

    def test_watcher_can_view_current_board_and_no_rain_role_is_denied(self):
        operation = self._operation()
        self._mission(operation, "UPS500", "SDF", planned=datetime(2026, 8, 30, 5))
        db.session.commit()
        watcher = self._user("rain_watcher", "watcher")
        self._login(watcher)

        with patch(
            "app.neonodes.neorain.routes.current_neorain_outbound_operation",
            return_value=operation,
        ):
            response = self.client.get("/neorain/outbound")
            revision = self.client.get("/neorain/outbound/revision")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"UPS500", response.data)
        self.assertIn(b"data-neorain-outbound-live", response.data)
        for column in (
            b">Wave<",
            b">Flight #<",
            b">eLMAC<",
            b">Ramp Load Complete<",
            b">Crew Load Complete<",
            b">Official Block-Out<",
            b">+/-<",
            b">Include/Exclude<",
            b">No Return<",
        ):
            self.assertIn(column, response.data)
        self.assertIn(b'data-neorain-late-summary', response.data)
        self.assertLess(
            response.data.index(b'data-neorain-late-summary'),
            response.data.index(b'neorain-outbound-table'),
        )
        self.assertEqual(revision.status_code, 200)
        self.assertTrue(revision.get_json()["ok"])

        self.client.get("/logout")
        denied = self._user("not_rain", "watcher", rain_role=None)
        self._login(denied)
        self.assertIn(self.client.get("/neorain/outbound").status_code, (302, 403))

    def test_no_current_sort_renders_the_normal_empty_state(self):
        watcher = self._user("rain_empty", "watcher")
        self._login(watcher)

        with patch(
            "app.neonodes.neorain.routes.current_neorain_outbound_operation",
            return_value=None,
        ):
            response = self.client.get("/neorain/outbound")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"No current sort.", response.data)
        self.assertIn(b">HUB<", response.data)
        self.assertIn(b">RAMP<", response.data)
        self.assertIn(b"<output>0</output>", response.data)

    def test_google_primary_and_viewer_render_timestamp_milestones_read_only(self):
        operation = self._operation()
        self._mission(operation, "UPS610", "SDF", planned=datetime(2026, 8, 30, 6))
        db.session.commit()
        simulator = self._user("rain_google_primary", "simulator")
        self._login(simulator)

        with patch(
            "app.neonodes.neorain.routes.current_neorain_outbound_operation",
            return_value=operation,
        ):
            google_primary = self.client.get("/neorain/outbound")

        self.assertEqual(google_primary.status_code, 200)
        self.assertNotIn(
            b'data-neorain-field="ramp_load_complete"',
            google_primary.data,
        )
        self.assertIn(b">INCLUDED<", google_primary.data)
        self.assertIn(b'data-late-inclusion-url="/neorain/outbound/late-inclusion"', google_primary.data)
        self.assertNotIn(b'data-neorain-no-return-action', google_primary.data)

        self.client.get("/logout")
        viewer = self._user("rain_neo_viewer", "watcher")
        self._login(viewer)
        set_rain_integration_mode(self.gateway, operation.sort_name, NEO_ONLY)
        db.session.commit()
        with patch(
            "app.neonodes.neorain.routes.current_neorain_outbound_operation",
            return_value=operation,
        ):
            neo_viewer = self.client.get("/neorain/outbound")

        self.assertEqual(neo_viewer.status_code, 200)
        self.assertNotIn(
            b'data-neorain-field="ramp_load_complete"',
            neo_viewer.data,
        )
        self.assertIn(b'data-neorain-display="late_metrics_included"', neo_viewer.data)
        self.assertNotIn(b'data-neorain-late-inclusion-toggle', neo_viewer.data)
        self.assertNotIn(b'data-neorain-no-return-action', neo_viewer.data)

    def test_authorized_neo_mode_renders_three_hhmm_editors_only(self):
        operation = self._operation()
        mission = self._mission(
            operation,
            "UPS620",
            "ONT",
            planned=datetime(2026, 8, 30, 6, 20),
        )
        mission.ramp_load_completed_at_utc = datetime(2026, 8, 30, 6, 37)
        mission.crew_load_completed_at_utc = datetime(2026, 8, 30, 6, 45)
        mission.actual_block_out_datetime_utc = datetime(2026, 8, 30, 6, 55)
        db.session.commit()
        simulator = self._user("rain_neo_editor", "simulator")
        self._login(simulator)

        for mode in (NEO_PRIMARY_GOOGLE_MIRROR, NEO_ONLY):
            with self.subTest(mode=mode):
                set_rain_integration_mode(self.gateway, operation.sort_name, mode)
                db.session.commit()
                with patch(
                    "app.neonodes.neorain.routes.current_neorain_outbound_operation",
                    return_value=operation,
                ):
                    response = self.client.get("/neorain/outbound")

                self.assertEqual(response.status_code, 200)
                self.assertIn(b'data-mutation-url="/neorain/outbound/milestone"', response.data)
                self.assertIn(b'inputmode="numeric"', response.data)
                self.assertIn(b'maxlength="4"', response.data)
                self.assertIn(b'placeholder="HHMM"', response.data)
                self.assertIn(
                    f'data-mission-version="{entity_version(mission)}"'.encode(),
                    response.data,
                )
                for field, value in (
                    ("ramp_load_complete", b'value="0137"'),
                    ("crew_load_complete", b'value="0145"'),
                    ("official_block_out", b'value="0155"'),
                ):
                    self.assertIn(f'data-neorain-field="{field}"'.encode(), response.data)
                    self.assertIn(value, response.data)
                self.assertNotIn(b'data-neorain-field="elmac"', response.data)
                self.assertNotIn(b'data-neorain-field="no_return"', response.data)
                self.assertIn(b'data-neorain-no-return-action="set"', response.data)
                self.assertIn(b'data-neorain-no-return-action="reverse"', response.data)
                self.assertIn(b'field: "no_return"', response.data)
                self.assertIn(b'value: desired', response.data)
                self.assertIn(b'"Reverse No Return for this mission?"', response.data)
                self.assertIn(b'data-neorain-display="departure_variance"', response.data)
                self.assertIn(b'data-neorain-late-inclusion-toggle', response.data)
                self.assertIn(b'included: desired', response.data)
                self.assertIn(b'data-neorain-late-inclusion-saving', response.data)
                self.assertLess(response.data.index(b">+/-<"), response.data.index(b">Include/Exclude<"))
                self.assertLess(response.data.index(b">Include/Exclude<"), response.data.index(b">No Return<"))
                self.assertNotIn(b'data-neorain-collapsed-row', response.data)
                self.assertNotIn(b'data-neorain-final-row', response.data)
                self.assertNotIn(b'data-neorain-reopen', response.data)
                self.assertIn(b"expected_version", response.data)
                self.assertIn(b"stale_version", response.data)
                self.assertIn(b"neorainRefreshDeferred", response.data)

    def test_blocked_out_rows_remain_full_width(self):
        operation = self._operation()
        incomplete = self._mission(
            operation,
            "UPS640",
            "SDF",
            planned=datetime(2026, 8, 30, 6, 40),
        )
        ready = self._mission(
            operation,
            "UPS641",
            "ONT",
            planned=datetime(2026, 8, 30, 6, 41),
        )
        ready.ramp_load_completed_at_utc = datetime(2026, 8, 30, 6, 42)
        ready.crew_load_completed_at_utc = datetime(2026, 8, 30, 6, 43)
        ready.actual_block_out_datetime_utc = datetime(2026, 8, 30, 6, 44)
        ready.departure_status = "blocked_out"
        db.session.commit()
        editor = self._user("rain_collapse_editor", "simulator")
        self._login(editor)
        set_rain_integration_mode(self.gateway, operation.sort_name, NEO_ONLY)
        db.session.commit()

        with patch(
            "app.neonodes.neorain.routes.current_neorain_outbound_operation",
            return_value=operation,
        ):
            response = self.client.get("/neorain/outbound")

        self.assertEqual(response.status_code, 200)
        body = response.data
        self.assertIn(b'data-neorain-mission-id="%d"' % incomplete.id, body)
        self.assertIn(b'data-neorain-mission-id="%d"' % ready.id, body)
        self.assertIn(b'neorain-outbound-row--blocked-out', body)
        self.assertNotIn(b'data-neorain-collapsed-row', body)
        self.assertNotIn(b'data-neorain-final-row', body)
        self.assertNotIn(b'REOPEN', body)
        self.assertIn(b'Ramp Load Complete', body)
        self.assertIn(b'Parking', body)
        self.assertIn(b'Load Planner', body)

    def test_departed_row_shows_no_return_and_reverse_for_authorized_editor(self):
        operation = self._operation()
        mission = self._mission(
            operation,
            "UPS630",
            "SDF",
            planned=datetime(2026, 8, 30, 6, 30),
            status="departed",
        )
        mission.ramp_load_completed_at_utc = datetime(2026, 8, 30, 6, 37)
        mission.crew_load_completed_at_utc = datetime(2026, 8, 30, 6, 45)
        mission.actual_block_out_datetime_utc = datetime(2026, 8, 30, 6, 55)
        db.session.commit()
        editor = self._user("rain_no_return_editor", "simulator")
        self._login(editor)
        set_rain_integration_mode(self.gateway, operation.sort_name, NEO_ONLY)
        db.session.commit()

        with patch(
            "app.neonodes.neorain.routes.current_neorain_outbound_operation",
            return_value=operation,
        ):
            response = self.client.get("/neorain/outbound")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b">NO RETURN<", response.data)
        self.assertIn(b'data-neorain-no-return-action="reverse"', response.data)
        self.assertIn(b'data-neorain-late-inclusion-toggle', response.data)
        self.assertIn(b'data-neorain-no-return-action="set"', response.data)
        self.assertIn(b'data-neorain-no-return-edit', response.data)
        self.assertIn(b'neorain-outbound-row--no-return', response.data)
        self.assertNotIn(b'data-neorain-final-row', response.data)
        self.assertNotIn(b'data-neorain-collapsed-row', response.data)
        self.assertNotIn(b'REOPEN', response.data)
        for visible in (b"UPS630", b"SDF", b"N630UP", b"Crew Load Complete", b"Ramp Load Complete", b"Parking"):
            self.assertIn(visible, response.data)
        self.assertIn(b'data-neorain-field="ramp_load_complete"', response.data)
        self.assertIn(b'data-neorain-field="crew_load_complete"', response.data)
        self.assertIn(b'data-neorain-field="official_block_out"', response.data)
        self.assertIn(b'setOutboundEditLock', response.data)

    def test_cancelled_outbound_row_is_full_width_and_locked_until_edit(self):
        operation = self._operation()
        mission = self._mission(
            operation,
            "UPS635",
            "SDF",
            planned=datetime(2026, 8, 30, 6, 35),
            status="cancelled",
        )
        db.session.commit()
        editor = self._user("rain_cancelled_editor", "simulator")
        self._login(editor)
        set_rain_integration_mode(self.gateway, operation.sort_name, NEO_ONLY)
        db.session.commit()

        with patch(
            "app.neonodes.neorain.routes.current_neorain_outbound_operation",
            return_value=operation,
        ):
            response = self.client.get("/neorain/outbound")

        self.assertEqual(response.status_code, 200)
        fragment = response.data.split(
            f'data-neorain-mission-id="{mission.id}"'.encode(), 1
        )[1].split(b"</tr>", 1)[0]
        self.assertIn(b'neorain-outbound-row--cancelled', response.data)
        for visible in (b"UPS635", b"N635UP", b"Parking", b"Ramp Load Complete", b"Delay Info"):
            self.assertIn(visible, response.data)
        self.assertIn(b'data-neorain-hhmm', fragment)
        self.assertIn(b'disabled', fragment)
        self.assertIn(b'data-neorain-no-return-edit', fragment)
        self.assertIn(b'data-neorain-no-return-action="set" hidden', fragment)
        self.assertIn(b'data-neorain-outbound-edit-protected hidden', fragment)
        self.assertIn(b'setOutboundEditLock', response.data)
        self.assertIn(b'nextRow.status === "CANCELLED"', response.data)

    def test_inbound_blocked_in_and_cancelled_rows_remain_full_width(self):
        operation = self._operation()
        blocked_in = self._mission(
            operation, "UPS650", "RFD", planned=datetime(2026, 8, 30, 6, 50), mission_type="arrival"
        )
        blocked_in.arrival_status = "arrived"
        blocked_in.actual_block_in_datetime_utc = datetime(2026, 8, 30, 7, 0)
        cancelled = self._mission(
            operation, "UPS651", "RFD", planned=datetime(2026, 8, 30, 7, 10), mission_type="arrival"
        )
        cancelled.arrival_status = "cancelled"
        db.session.commit()
        editor = self._user("rain_inbound_full_rows", "simulator")
        self._login(editor)

        with patch(
            "app.neonodes.neorain.routes.current_neorain_outbound_operation",
            return_value=operation,
        ):
            response = self.client.get("/neorain/inbound")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'neorain-inbound-row--blocked-in', response.data)
        self.assertIn(b'neorain-inbound-row--cancelled', response.data)
        self.assertNotIn(b'data-neorain-inbound-collapsed', response.data)
        self.assertNotIn(b'data-neorain-inbound-reopen', response.data)
        for column in (b"Ground Time", b"Connecting Outbound", b"Include/Exclude", b"Delay Info"):
            self.assertIn(column, response.data)
        self.assertIn(b'data-neorain-late-inclusion-toggle', response.data)

    def _operation(self):
        operation = SortDateOperation(
            gateway_id=self.gateway.id,
            gateway_code="RFD",
            sort_date=date(2026, 8, 30),
            sort_name="night",
        )
        db.session.add(operation)
        db.session.flush()
        return operation

    def _mission(
        self,
        operation,
        flight_number,
        destination,
        *,
        planned,
        tail=None,
        wave="1",
        status="scheduled",
        mission_type="departure",
    ):
        mission = SortDateMission(
            sort_date_operation_id=operation.id,
            sort_date=operation.sort_date,
            gateway_code=operation.gateway_code,
            sort_name=operation.sort_name,
            mission_type=mission_type,
            mission_source="master",
            wave=wave,
            flight_number=flight_number,
            origin="RFD",
            destination=destination,
            timezone="America/Chicago",
            planned_datetime_local=planned,
            planned_datetime_utc=planned,
            planned_source="master",
            assigned_tail_number=tail or f"N{flight_number[-3:]}UP",
            departure_status=status if mission_type == "departure" else None,
        )
        db.session.add(mission)
        db.session.flush()
        return mission

    def _staffing_totals(self, operation):
        staffing_sort = StaffingUnit(unit_type="sort", name="Night", active=True)
        ramp = StaffingUnit(
            unit_type="operation", name="Ramp", parent=staffing_sort, active=True
        )
        hub = StaffingUnit(
            unit_type="operation", name="Hub", parent=staffing_sort, active=True
        )
        ramp_area = StaffingUnit(
            unit_type="work_area", name="Ramp Direct", parent=ramp, active=True
        )
        hub_area = StaffingUnit(
            unit_type="work_area", name="Hub Direct", parent=hub, active=True
        )
        db.session.add_all([staffing_sort, ramp, hub, ramp_area, hub_area])
        db.session.flush()

        def person(employee_id, area):
            value = StaffingPerson(
                employee_id=employee_id,
                first_name="Staffing",
                last_name=employee_id,
                seniority_date=date(2020, 1, 1),
                classification="part_time",
                employee_status="active",
                active=True,
            )
            db.session.add(value)
            db.session.flush()
            db.session.add(
                StaffingWorkAssignment(
                    person_id=value.id,
                    work_area_unit_id=area.id,
                    active=True,
                )
            )
            return value

        ramp_here = person("RAIN-RAMP-HERE", ramp_area)
        ramp_absent = person("RAIN-RAMP-ABSENT", ramp_area)
        hub_here = person("RAIN-HUB-HERE", hub_area)
        hub_absent = person("RAIN-HUB-ABSENT", hub_area)
        db.session.flush()

        def attendance(person_value, area, operation_unit, status):
            record = StaffingDailyAttendance(
                person_id=person_value.id,
                attendance_date=operation.sort_date,
                sort_unit_id=staffing_sort.id,
                work_area_unit_id=area.id,
                operation_unit_id=operation_unit.id,
                sort_date_operation_id=operation.id,
                status=status,
            )
            db.session.add(record)
            return record

        attendance(ramp_here, ramp_area, ramp, "here")
        ramp_absent_record = attendance(ramp_absent, ramp_area, ramp, "call_in")
        attendance(hub_here, hub_area, hub, "here")
        hub_absent_record = attendance(hub_absent, hub_area, hub, "call_in")
        return {
            "sort": staffing_sort,
            "ramp": ramp,
            "hub": hub,
            "hub_area": hub_area,
            "hub_absent": hub_absent,
            "hub_absent_record": hub_absent_record,
            "ramp_absent_record": ramp_absent_record,
        }

    def _user(self, username, role, *, rain_role="watcher"):
        user = User(
            username=username,
            email=f"{username}@example.test",
            first_name="Rain",
            last_name="User",
            full_name="Rain User",
            employee_id=f"EMP-{username}",
            email_verified_at=datetime.utcnow(),
            role=role,
            is_active=True,
        )
        set_user_password(user, "TestPassword123!")
        db.session.add(user)
        db.session.flush()
        backfill_default_gateway_node_roles(user, role=role)
        if rain_role is None:
            membership = GatewayMembership.query.filter_by(user_id=user.id).one()
            membership.is_active = False
        db.session.commit()
        return user

    def _login(self, user):
        return self.client.post(
            "/login",
            data={"username": user.username, "password": "TestPassword123!"},
            follow_redirects=False,
        )
