from datetime import date, datetime, time, timezone
import json
from pathlib import Path
import re
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app import create_app
from app.extensions import db
from app.models import (
    Gateway,
    GatewayMembership,
    GatewaySortMatrix,
    FlightApiReviewItem,
    MasterFlightSchedule,
    MotherBrainAlert,
    MotherBrainParkingRule,
    MotherBrainParkingSettings,
    PermissionRule,
    SortTimelineApiParticipation,
    SortTimelineMonthVariance,
    SortTimelineSettings,
    SortTimelineSpecialPollTime,
    SortTimelineUsageCounter,
    SortDateCrewAssignment,
    SortDateMission,
    SortDateOperation,
    SortDateParkingAssignment,
    SortDateTailState,
    User,
)
from app.services.access_control import backfill_default_gateway_node_roles
from app.services.gateway_matrix import current_gateway_local_date
from app.services.gateway_matrix import current_operations_for_gateway
from app.services.night_sorting import night_sort_time_key
from app.services.parking_plan import parking_plan_context, parking_status_for_rows
from app.services.parking_optimizer import (
    apply_parking_optimizer_plan as service_apply_parking_optimizer_plan,
    parking_optimizer_preview,
)
from app.services.parking_aircraft import resolve_parking_aircraft_type_from_tail
from app.services.parking_physical_validator import (
    validate_parking_physical_rules,
)
from app.services.parking_rules import (
    AIRCRAFT_TYPE_RAMP_RESTRICTION,
    AIRCRAFT_TYPE_RAMP_PREFERENCE,
    ORIGIN_RAMP_RESTRICTION,
    ORIGIN_RAMP_PREFERENCE,
)
from app.services.permission_rules import ensure_default_permission_rules
from app.services.sort_timeline import (
    ensure_sort_timeline_settings,
    record_sort_timeline_api_attempt,
    sort_timeline_context,
)


class MotherBrainRoutesTest(unittest.TestCase):
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

        user = User(username="Kessler", role="grandmaster")
        user.set_password("TestPassword123!")
        db.session.add(user)
        db.session.flush()
        backfill_default_gateway_node_roles(user, role="grandmaster")
        self.rfd_gateway = Gateway.query.filter_by(code="RFD").first()
        db.session.commit()

        self.client = self.app.test_client()
        self.client.post(
            "/login",
            data={"username": "Kessler", "password": "TestPassword123!"},
        )

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_logged_in_user_can_access_motherbrain_home(self):
        response = self.client.get("/motherbrain")
        html = response.data.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'src="/static/images/motherbrain_logo1.png"', response.data)
        self.assertIn(b"blueprint-neomotherbrain", response.data)
        self.assertIn(b"motherbrain-fixed-header", response.data)
        self.assertIn(b"motherbrain-home-page", response.data)
        self.assertIn(b'class="motherbrain-header-logo-link"', response.data)
        self.assertIn(b'class="motherbrain-header-logo"', response.data)
        self.assertIn(b"motherbrain-screen-logo", response.data)
        self.assertIn(b"neo-node-name neo-node-motherbrain", response.data)
        self.assertIn(b'<span class="neo-word">Neo</span>', response.data)
        self.assertIn(b'<span class="node-word">MotherBrain</span>', response.data)
        self.assertIn(b"DASHBOARD", response.data)
        self.assertNotIn(b"NEOMOTHERBRAIN", response.data)
        self.assertNotIn(b">Command<", response.data)
        self.assertNotIn(b"Command Console", response.data)
        self.assertNotIn(b"NeoRFD Command", response.data)
        self.assertNotIn(b"NeoRFD command", response.data)
        self.assertNotIn(b"NEORFD COMMAND", response.data)
        self.assertIn(b'aria-label="Primary"', response.data)
        self.assertNotIn(b'aria-label="MotherBrain menu"', response.data)
        self.assertNotIn(b'class="panel motherbrain-landing"', response.data)
        self.assertNotIn(b"action-button-secondary", response.data)
        self.assertNotIn(b"BACK TO NeoMotherBrain MAIN MENU", response.data)
        self.assertNotIn(b"motherbrain-main-menu-return", response.data)
        self.assertNotIn(b'class="metric-grid"', response.data)
        self.assertNotIn(b"Master Schedule Rows", response.data)
        self.assertNotIn(b"MotherBrain Home", response.data)
        self.assertNotIn(b"Back to NeoMotherBrain", response.data)
        self.assertIn(b"PORTAL MANAGEMENT", response.data)
        self.assertIn(b"Change Characters", response.data)
        self.assertNotIn(b"BACK TO NeoGateway", response.data)
        self.assertIn(b"GATEWAY MATRIX", response.data)
        self.assertIn(b"MASTER SCHEDULE", response.data)
        self.assertIn(b"MANAGE SORT", response.data)
        self.assertIn(b"PERMISSION RULES", response.data)
        self.assertIn(b"neo-node-name neo-node-motherbrain", response.data)
        self.assertIn(b"CURRENT SORT OVERVIEW", response.data)
        self.assertIn(b"No active sort selected.", response.data)
        self.assertNotIn(b"MANAGE CURRENT SORTS", response.data)
        self.assertNotIn(b"MANAGE MASTER FLIGHT SCHEDULE", response.data)
        self.assertNotIn(b"ASSIGN ACTIVE SORTS", response.data)
        self.assertNotIn(b"USER AND ACCESS CONTROLS", response.data)
        self.assertNotIn(b"SCREEN ACTION CONTROLS", response.data)
        self.assertNotIn(b"Gateway Matris", response.data)
        dashboard_html = html.split('class="motherbrain-dashboard-grid"', 1)[1]
        self.assertLess(dashboard_html.index("MANAGE SORT"), dashboard_html.index("MASTER SCHEDULE"))
        self.assertLess(dashboard_html.index("MASTER SCHEDULE"), dashboard_html.index("GATEWAY MATRIX"))
        self.assertLess(dashboard_html.index("GATEWAY MATRIX"), dashboard_html.index("PORTAL MANAGEMENT"))
        self.assertLess(dashboard_html.index("PORTAL MANAGEMENT"), dashboard_html.index("PERMISSION RULES"))
        nav_html = html.split('id="motherbrain-mobile-menu"', 1)[1].split("</nav>", 1)[0]
        self.assertLess(nav_html.index("MANAGE SORT"), nav_html.index("MASTER SCHEDULE"))
        self.assertLess(nav_html.index("MASTER SCHEDULE"), nav_html.index("GATEWAY MATRIX"))
        self.assertLess(nav_html.index("GATEWAY MATRIX"), nav_html.index("PORTAL MANAGEMENT"))
        self.assertNotIn("BACK TO", nav_html)
        self.assertNotIn("MotherBrain Home", nav_html)
        self.assertNotIn("Back to NeoMotherBrain", nav_html)
        self.assertNotIn("&gt;", nav_html)
        self.assertIn(b"Logout", response.data)
        self.assertIn(b'data-motherbrain-menu-button', response.data)
        self.assertIn(b'aria-expanded="false"', response.data)
        self.assertIn(b'aria-controls="motherbrain-mobile-menu"', response.data)
        self.assertIn(b'id="motherbrain-mobile-menu"', response.data)
        self.assertIn(b'href="/motherbrain"', response.data)
        self.assertIn(b'href="/portal/manage"', response.data)
        self.assertIn(b'href="/admin/permissions"', response.data)
        self.assertIn(b'href="/motherbrain/gateway-matrix"', response.data)
        self.assertIn(b'href="/motherbrain/master-schedule"', response.data)
        self.assertIn(b'href="/motherbrain/manage-sort"', response.data)
        self.assertIn(b'href="/logout"', response.data)
        self.assertNotIn(b"Access Requests", response.data)
        self.assertNotIn(b"Generate Nightly Operation", response.data)

    def test_motherbrain_header_navigation_routes_work(self):
        routes = {
            "/motherbrain/gateway-matrix": b'href="/motherbrain/gateway-matrix" aria-current="page"',
            "/motherbrain/master-schedule": b'href="/motherbrain/master-schedule" aria-current="page"',
            "/motherbrain/manage-sort": b'href="/motherbrain/manage-sort" aria-current="page"',
            "/motherbrain/sort-timeline": b'href="/motherbrain/sort-timeline" aria-current="page"',
        }

        for path, active_link in routes.items():
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(b"motherbrain-fixed-header", response.data)
                self.assertIn(b'class="motherbrain-header-logo-link"', response.data)
                self.assertIn(b'class="motherbrain-header-logo"', response.data)
                self.assertIn(b"NeoMotherBrain", response.data)
                self.assertNotIn(b"NEOMOTHERBRAIN", response.data)
                self.assertNotIn(b">Command<", response.data)
                self.assertNotIn(b"Command Console", response.data)
                self.assertNotIn(b"NeoRFD Command", response.data)
                self.assertNotIn(b"NeoRFD command", response.data)
                self.assertNotIn(b"NEORFD COMMAND", response.data)
                self.assertNotIn(b"motherbrain-screen-logo", response.data)
                self.assertNotIn(b"MotherBrain Home", response.data)
                self.assertNotIn(b"Back to NeoMotherBrain", response.data)
                self.assertIn(b"BACK TO", response.data)
                self.assertNotIn(b"BACK TO NeoGateway", response.data)
                self.assertIn(b"PORTAL MANAGEMENT", response.data)
                self.assertIn(b"GATEWAY MATRIX", response.data)
                self.assertIn(b"MASTER SCHEDULE", response.data)
                self.assertIn(b"MANAGE SORT", response.data)
                self.assertIn(b"SORT TIMELINE", response.data)
                self.assertIn(b'href="/motherbrain"', response.data)
                self.assertIn(b"BACK TO NeoMotherBrain MAIN MENU", response.data)
                self.assertIn(b"motherbrain-main-menu-return", response.data)
                self.assertIn(b'href="/motherbrain"', response.data)
                self.assertIn(b'aria-label="BACK TO NeoMotherBrain MAIN MENU"', response.data)
                self.assertIn(b'href="/logout"', response.data)
                self.assertIn(b'data-motherbrain-menu-button', response.data)
                self.assertIn(b'aria-controls="motherbrain-mobile-menu"', response.data)
                self.assertIn(b'id="motherbrain-mobile-menu"', response.data)
                self.assertIn(active_link, response.data)
                self.assertIn(b'aria-current="page"', response.data)

        portal_management = self.client.get("/portal/manage")
        self.assertEqual(portal_management.status_code, 200)
        self.assertIn(b"PORTAL MANAGEMENT", portal_management.data)

        rfd_response = self.client.get("/rfd")
        self.assertEqual(rfd_response.status_code, 200)
        self.assertIn(b"NeoGateway", rfd_response.data)

        still_authenticated = self.client.get("/motherbrain")
        self.assertEqual(still_authenticated.status_code, 200)
        self.assertIn(b"neo-node-name neo-node-motherbrain", still_authenticated.data)
        self.assertIn(b"Logout", still_authenticated.data)
        self.assertNotIn(b"BACK TO NeoMotherBrain MAIN MENU", still_authenticated.data)

        logout_response = self.client.get("/logout", follow_redirects=False)
        self.assertEqual(logout_response.status_code, 302)

    def test_sort_timeline_is_grandmaster_only(self):
        response = self.client.get("/motherbrain/sort-timeline?month=2026-06")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"SORT TIMELINE", response.data)
        self.assertIn(b"API PLANNING SETTINGS", response.data)
        self.assertIn(b'href="/motherbrain/flight-api-test"', response.data)
        self.assertIn(b"FLIGHT API TEST", response.data)

        self._login_motherbrain_role("timeline_master", "master")
        blocked = self.client.get("/motherbrain/sort-timeline", follow_redirects=False)
        self.assertEqual(blocked.status_code, 302)
        self.assertEqual(blocked.location, "/rfd")

    def test_sort_timeline_desktop_offset_grid_uses_wide_layout(self):
        css = Path("app/static/css/base.css").read_text()

        self.assertIn(".sort-timeline-page .sort-timeline-form", css)
        self.assertIn("max-width: none;", css)
        self.assertIn("@media (min-width: 980px)", css)
        self.assertIn("grid-template-columns: repeat(4, minmax(0, 1fr));", css)
        self.assertIn("min-height: 54px;", css)

    def test_sort_timeline_renders_autosave_without_save_button(self):
        response = self.client.get("/motherbrain/sort-timeline?month=2026-06")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"data-sort-timeline-autosave", response.data)
        self.assertIn(b"data-sort-timeline-save-status", response.data)
        self.assertIn(b"AUTO-SAVE READY", response.data)
        self.assertNotIn(b"SAVE SORT TIMELINE SETTINGS", response.data)

    def test_sort_timeline_autosave_post_returns_json_and_saves(self):
        response = self.client.post(
            "/motherbrain/sort-timeline",
            data=self._sort_timeline_form_data(
                monthly_api_units="900",
                units_per_poll="3",
                taxi_to_ramp_minutes="14",
                minimum_auto_poll_interval_minutes="17",
                provider_enabled="1",
                month_variance_6="2",
            ),
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json",
            },
        )

        settings = SortTimelineSettings.query.filter_by(gateway_id=self.rfd_gateway.id).one()
        variance = SortTimelineMonthVariance.query.filter_by(
            gateway_id=self.rfd_gateway.id,
            month_number=6,
        ).one()

        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["status"], "saved")
        self.assertEqual(payload["month"], "2026-06")
        self.assertIn("2026-06", payload["previews"])
        self.assertEqual(payload["previews"]["2026-06"]["monthly_api_units"], 900)
        self.assertEqual(payload["previews"]["2026-06"]["units_per_poll"], 3)
        self.assertEqual(payload["previews"]["2026-06"]["taxi_to_ramp_minutes"], 14)
        self.assertEqual(payload["previews"]["2026-06"]["minimum_auto_poll_interval_minutes"], 17)
        self.assertEqual(settings.monthly_api_units, 900)
        self.assertEqual(settings.units_per_poll, 3)
        self.assertEqual(settings.taxi_to_ramp_minutes, 14)
        self.assertEqual(settings.minimum_auto_poll_interval_minutes, 17)
        self.assertTrue(settings.provider_enabled)
        self.assertEqual(variance.variance, 2)

    def test_sort_timeline_taxi_to_ramp_default_is_ten_minutes(self):
        response = self.client.get("/motherbrain/sort-timeline?month=2026-06")
        settings = SortTimelineSettings.query.filter_by(gateway_id=self.rfd_gateway.id).one()
        context = sort_timeline_context(
            self.rfd_gateway,
            "2026-06",
            now=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(settings.taxi_to_ramp_minutes, 10)
        self.assertEqual(context["summary"]["taxi_to_ramp_minutes"], 10)
        self.assertIn(b"Taxi-To-Ramp Minutes", response.data)
        self.assertIn(b"Used to calculate Assumed Arrived from API runway time.", response.data)
        self.assertIn(b"Scheduled / Expected -> In Air -> On Ground", response.data)

    def test_sort_timeline_minimum_auto_poll_interval_default_is_ten_minutes(self):
        response = self.client.get("/motherbrain/sort-timeline?month=2026-06")
        settings = SortTimelineSettings.query.filter_by(gateway_id=self.rfd_gateway.id).one()
        context = sort_timeline_context(
            self.rfd_gateway,
            "2026-06",
            now=datetime(2026, 6, 1, 6, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(settings.minimum_auto_poll_interval_minutes, 10)
        self.assertEqual(context["summary"]["minimum_auto_poll_interval_minutes"], 10)
        self.assertIn(b"Minimum Auto Poll Interval Minutes", response.data)
        self.assertIn(
            b"Auto polling will never run more often than this interval",
            response.data,
        )

    def test_sort_timeline_taxi_to_ramp_reload_preserves_value(self):
        self.client.post(
            "/motherbrain/sort-timeline",
            data=self._sort_timeline_form_data(
                provider_enabled="1",
                taxi_to_ramp_minutes="18",
            ),
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json",
            },
        )

        response = self.client.get("/motherbrain/sort-timeline?month=2026-06")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'name="taxi_to_ramp_minutes" min="0" step="1" value="18"', response.data)
        self.assertIn(b'<strong data-preview-metric="taxi_to_ramp_minutes">18</strong>', response.data)

    def test_sort_timeline_minimum_auto_poll_interval_saves_as_integer_minutes(self):
        self.client.post(
            "/motherbrain/sort-timeline",
            data=self._sort_timeline_form_data(
                provider_enabled="1",
                minimum_auto_poll_interval_minutes="23",
            ),
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json",
            },
        )

        response = self.client.get("/motherbrain/sort-timeline?month=2026-06")
        settings = SortTimelineSettings.query.filter_by(gateway_id=self.rfd_gateway.id).one()

        self.assertEqual(settings.minimum_auto_poll_interval_minutes, 23)
        self.assertIn(
            b'name="minimum_auto_poll_interval_minutes" min="1" step="1" value="23"',
            response.data,
        )

    def test_sort_timeline_monthly_limit_and_provider_settings_save_without_key(self):
        response = self.client.post(
            "/motherbrain/sort-timeline",
            data=self._sort_timeline_form_data(
                monthly_api_units="750",
                units_per_poll="3",
                provider_enabled="1",
                provider_name="AeroDataBox",
                api_key_env_var_name="aerodatabox_api_key",
                api_key="SHOULD_NOT_BE_STORED",
            ),
            follow_redirects=True,
        )
        settings = SortTimelineSettings.query.filter_by(gateway_id=self.rfd_gateway.id).one()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(settings.monthly_api_units, 750)
        self.assertEqual(settings.units_per_poll, 3)
        self.assertTrue(settings.provider_enabled)
        self.assertEqual(settings.provider_name, "AeroDataBox")
        self.assertEqual(settings.api_key_env_var_name, "AERODATABOX_API_KEY")
        self.assertNotIn("SHOULD_NOT_BE_STORED", settings.api_key_env_var_name)
        self.assertNotIn(b"SHOULD_NOT_BE_STORED", response.data)

    def test_sort_timeline_default_weekday_calculation(self):
        self._add_matrix_days("night", ["monday", "tuesday", "wednesday", "thursday", "friday"])
        context = sort_timeline_context(
            self.rfd_gateway,
            "2026-06",
            now=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(context["summary"]["operating_days"], 22)
        self.assertEqual(context["summary"]["monthly_api_units"], 600)
        self.assertEqual(context["summary"]["units_per_poll"], 2)
        self.assertEqual(context["summary"]["monthly_poll_limit"], 300)
        self.assertEqual(context["summary"]["original_daily_poll_cap"], 0)
        self.assertEqual(context["summary"]["effective_daily_poll_cap"], 0)

    def test_sort_timeline_provider_enabled_acts_as_master_switch(self):
        self._add_matrix_cell("monday", "night")
        self.client.post(
            "/motherbrain/sort-timeline",
            data=self._sort_timeline_form_data(
                provider_enabled=None,
                api_enabled_night_monday="1",
                night_special_poll_time=["01:00"],
            ),
        )
        context = sort_timeline_context(
            self.rfd_gateway,
            "2026-06",
            now=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
        )

        self.assertFalse(context["current_preview"]["provider_enabled"])
        self.assertEqual(context["current_preview"]["effective_daily_poll_cap"], 0)
        self.assertEqual(context["current_preview"]["auto_interval_poll_count"], 0)
        self.assertEqual(context["current_preview"]["total_scheduled_polls"], 0)

    def test_sort_timeline_gateway_matrix_populates_api_schedule(self):
        self._add_matrix_days("night", ["monday", "tuesday", "wednesday", "thursday"])
        self._add_matrix_days("day", ["sunday", "monday", "tuesday", "wednesday", "thursday"])
        response = self.client.get("/motherbrain/sort-timeline?month=2026-06")
        context = sort_timeline_context(
            self.rfd_gateway,
            "2026-06",
            now=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"NIGHT", response.data)
        self.assertIn(b"DAY", response.data)
        self.assertIn(b"Monday", response.data)
        self.assertIn(b"Sunday", response.data)
        self.assertEqual(
            {
                (day_info["day"], sort_info["sort_name"])
                for sort_info in context["api_schedule"]["configured_sorts"]
                for day_info in sort_info["days"]
            },
            {
                ("monday", "night"),
                ("tuesday", "night"),
                ("wednesday", "night"),
                ("thursday", "night"),
                ("sunday", "day"),
                ("monday", "day"),
                ("tuesday", "day"),
                ("wednesday", "day"),
                ("thursday", "day"),
            },
        )

    def test_sort_timeline_api_participation_can_be_disabled_without_gateway_matrix_change(self):
        self._add_matrix_cell("monday", "night")
        self._add_matrix_cell("tuesday", "night")
        self.client.post(
            "/motherbrain/sort-timeline",
            data=self._sort_timeline_form_data(
                provider_enabled="1",
                api_enabled_night_monday="1",
            ),
        )
        context = sort_timeline_context(
            self.rfd_gateway,
            "2026-06",
            now=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
        )
        tuesday_participation = SortTimelineApiParticipation.query.filter_by(
            gateway_id=self.rfd_gateway.id,
            day_of_week="tuesday",
            sort_name="night",
        ).one()
        tuesday_matrix = GatewaySortMatrix.query.filter_by(
            gateway_id=self.rfd_gateway.id,
            day_of_week="tuesday",
            sort_name="night",
        ).one()

        self.assertFalse(tuesday_participation.is_enabled)
        self.assertTrue(tuesday_matrix.is_active)
        self.assertEqual(context["summary"]["base_operating_days"], 10)
        self.assertEqual(context["summary"]["operating_days"], 10)
        self.assertEqual(context["summary"]["api_polling_days"], 5)
        self.assertEqual(context["preview_by_sort"]["night"]["api_day_count"], 5)

    def test_sort_timeline_api_enabled_toggle_does_not_change_operating_days(self):
        self._add_matrix_cell("monday", "night")
        self._add_matrix_cell("tuesday", "night")
        self.client.post(
            "/motherbrain/sort-timeline",
            data=self._sort_timeline_form_data(
                provider_enabled="1",
                monthly_api_units="600",
                units_per_poll="2",
                api_enabled_night_monday="1",
                api_enabled_night_tuesday="1",
            ),
        )
        all_enabled = sort_timeline_context(
            self.rfd_gateway,
            "2026-06",
            now=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
        )

        self.client.post(
            "/motherbrain/sort-timeline",
            data=self._sort_timeline_form_data(
                provider_enabled="1",
                monthly_api_units="600",
                units_per_poll="2",
                api_enabled_night_monday="1",
            ),
        )
        tuesday_disabled = sort_timeline_context(
            self.rfd_gateway,
            "2026-06",
            now=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(all_enabled["summary"]["base_operating_days"], 10)
        self.assertEqual(tuesday_disabled["summary"]["base_operating_days"], 10)
        self.assertEqual(all_enabled["summary"]["operating_days"], 10)
        self.assertEqual(tuesday_disabled["summary"]["operating_days"], 10)
        self.assertEqual(all_enabled["summary"]["api_polling_days"], 10)
        self.assertEqual(tuesday_disabled["summary"]["api_polling_days"], 5)
        self.assertEqual(all_enabled["summary"]["original_daily_poll_cap"], 30)
        self.assertEqual(tuesday_disabled["summary"]["original_daily_poll_cap"], 60)
        self.assertEqual(all_enabled["preview_by_sort"]["night"]["api_day_count"], 10)
        self.assertEqual(tuesday_disabled["preview_by_sort"]["night"]["api_day_count"], 5)

    def test_sort_timeline_ajax_preview_api_day_toggle_changes_api_polling_days_only(self):
        self._add_matrix_cell("monday", "night")
        self._add_matrix_cell("tuesday", "night")
        all_enabled = self.client.post(
            "/motherbrain/sort-timeline",
            data=self._sort_timeline_form_data(
                provider_enabled="1",
                monthly_api_units="600",
                units_per_poll="2",
                api_enabled_night_monday="1",
                api_enabled_night_tuesday="1",
            ),
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json",
            },
        ).get_json()["previews"]["2026-06"]

        tuesday_disabled = self.client.post(
            "/motherbrain/sort-timeline",
            data=self._sort_timeline_form_data(
                provider_enabled="1",
                monthly_api_units="600",
                units_per_poll="2",
                api_enabled_night_monday="1",
            ),
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json",
            },
        ).get_json()["previews"]["2026-06"]

        self.assertEqual(all_enabled["operating_days"], 10)
        self.assertEqual(tuesday_disabled["operating_days"], 10)
        self.assertEqual(all_enabled["api_polling_days"], 10)
        self.assertEqual(tuesday_disabled["api_polling_days"], 5)
        self.assertEqual(tuesday_disabled["original_daily_poll_cap"], 60)

    def test_sort_timeline_daily_cap_uses_api_polling_days(self):
        self._add_matrix_cell("monday", "night")
        self._add_matrix_cell("tuesday", "night")
        self.client.post(
            "/motherbrain/sort-timeline",
            data=self._sort_timeline_form_data(
                provider_enabled="1",
                monthly_api_units="600",
                units_per_poll="2",
                api_enabled_night_monday="1",
            ),
        )
        context = sort_timeline_context(
            self.rfd_gateway,
            "2026-06",
            now=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(context["summary"]["monthly_poll_limit"], 300)
        self.assertEqual(context["summary"]["operating_days"], 10)
        self.assertEqual(context["summary"]["api_polling_days"], 5)
        self.assertEqual(context["summary"]["original_daily_poll_cap"], 60)

    def test_sort_timeline_remaining_api_polling_days_use_current_rfd_month_time(self):
        self._add_matrix_cell("monday", "night")
        self.client.post(
            "/motherbrain/sort-timeline",
            data=self._sort_timeline_form_data(
                provider_enabled="1",
                api_enabled_night_monday="1",
                night_polling_start="08:00",
                night_polling_end="16:00",
            ),
        )
        context = sort_timeline_context(
            self.rfd_gateway,
            "2026-06",
            now=datetime(2026, 6, 16, 17, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(context["summary"]["full_month_api_polling_days"], 5)
        self.assertEqual(context["summary"]["remaining_api_polling_days"], 2)
        self.assertEqual(context["summary"]["adjusted_daily_poll_cap"], 150)

    def test_sort_timeline_today_counts_only_when_api_window_has_time_remaining(self):
        self._add_matrix_cell("monday", "night")
        self.client.post(
            "/motherbrain/sort-timeline",
            data=self._sort_timeline_form_data(
                provider_enabled="1",
                api_enabled_night_monday="1",
                night_polling_start="08:00",
                night_polling_end="16:00",
            ),
        )

        during_window = sort_timeline_context(
            self.rfd_gateway,
            "2026-06",
            now=datetime(2026, 6, 1, 20, 0, tzinfo=timezone.utc),
        )
        after_window = sort_timeline_context(
            self.rfd_gateway,
            "2026-06",
            now=datetime(2026, 6, 1, 22, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(during_window["summary"]["remaining_api_polling_days"], 5)
        self.assertEqual(after_window["summary"]["remaining_api_polling_days"], 4)

    def test_sort_timeline_overnight_today_counts_before_window_starts(self):
        self._add_matrix_cell("monday", "night")
        self.client.post(
            "/motherbrain/sort-timeline",
            data=self._sort_timeline_form_data(
                provider_enabled="1",
                api_enabled_night_monday="1",
                night_polling_start="22:00",
                night_polling_end="04:00",
            ),
        )
        context = sort_timeline_context(
            self.rfd_gateway,
            "2026-06",
            now=datetime(2026, 6, 1, 17, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(context["summary"]["remaining_api_polling_days"], 5)

    def test_sort_timeline_gateway_matrix_changes_update_source_days(self):
        self._add_matrix_cell("monday", "night")
        one_sort = sort_timeline_context(
            self.rfd_gateway,
            "2026-06",
            now=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
        )

        self._add_matrix_cell("monday", "day")
        two_sorts = sort_timeline_context(
            self.rfd_gateway,
            "2026-06",
            now=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(one_sort["summary"]["operating_days"], 5)
        self.assertEqual(one_sort["summary"]["api_polling_days"], 5)
        self.assertEqual(two_sorts["summary"]["operating_days"], 10)
        self.assertEqual(two_sorts["summary"]["api_polling_days"], 10)
        self.assertIn("night", two_sorts["preview_by_sort"])
        self.assertIn("day", two_sorts["preview_by_sort"])

    def test_sort_timeline_month_offset_changes_api_polling_preview(self):
        self._add_matrix_cell("monday", "night")
        self._add_matrix_cell("tuesday", "night")
        response = self.client.post(
            "/motherbrain/sort-timeline",
            data=self._sort_timeline_form_data(
                provider_enabled="1",
                monthly_api_units="600",
                units_per_poll="2",
                month_variance_6="2",
                api_enabled_night_monday="1",
            ),
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json",
            },
        )
        preview = response.get_json()["previews"]["2026-06"]

        self.assertEqual(preview["operating_days"], 12)
        self.assertEqual(preview["api_polling_days"], 7)
        self.assertEqual(preview["original_daily_poll_cap"], 42)

    def test_sort_timeline_month_variance_positive_increases_ops_days(self):
        self._add_matrix_days("night", ["monday", "tuesday", "wednesday", "thursday", "friday"])
        self.client.post(
            "/motherbrain/sort-timeline",
            data=self._sort_timeline_form_data(
                provider_enabled="1",
                monthly_api_units="600",
                units_per_poll="2",
                month_variance_6="2",
                api_enabled_night_monday="1",
                api_enabled_night_tuesday="1",
                api_enabled_night_wednesday="1",
                api_enabled_night_thursday="1",
                api_enabled_night_friday="1",
            ),
        )
        context = sort_timeline_context(
            self.rfd_gateway,
            "2026-06",
            now=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(context["summary"]["base_operating_days"], 22)
        self.assertEqual(context["summary"]["month_variance"], 2)
        self.assertEqual(context["summary"]["operating_days"], 24)
        self.assertEqual(context["summary"]["monthly_poll_limit"], 300)
        self.assertEqual(context["summary"]["original_daily_poll_cap"], 12)

    def test_sort_timeline_month_variance_negative_decreases_ops_days(self):
        self._add_matrix_days("night", ["monday", "tuesday", "wednesday", "thursday", "friday"])
        self.client.post(
            "/motherbrain/sort-timeline",
            data=self._sort_timeline_form_data(
                provider_enabled="1",
                monthly_api_units="600",
                units_per_poll="2",
                month_variance_6="-1",
                api_enabled_night_monday="1",
                api_enabled_night_tuesday="1",
                api_enabled_night_wednesday="1",
                api_enabled_night_thursday="1",
                api_enabled_night_friday="1",
            ),
        )
        context = sort_timeline_context(
            self.rfd_gateway,
            "2026-06",
            now=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(context["summary"]["base_operating_days"], 22)
        self.assertEqual(context["summary"]["month_variance"], -1)
        self.assertEqual(context["summary"]["operating_days"], 21)
        self.assertEqual(context["summary"]["original_daily_poll_cap"], 14)

    def test_sort_timeline_month_variance_persists_by_month_across_years(self):
        self._add_matrix_cell("monday", "night")
        self.client.post(
            "/motherbrain/sort-timeline",
            data=self._sort_timeline_form_data(
                provider_enabled="1",
                month_key="2026-01",
                month_variance_1="2",
                api_enabled_night_monday="1",
            ),
        )
        jan_2026 = sort_timeline_context(
            self.rfd_gateway,
            "2026-01",
            now=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
        )
        jan_2027 = sort_timeline_context(
            self.rfd_gateway,
            "2027-01",
            now=datetime(2027, 1, 1, 12, 0, tzinfo=timezone.utc),
        )
        variance = SortTimelineMonthVariance.query.filter_by(
            gateway_id=self.rfd_gateway.id,
            month_number=1,
        ).one()

        self.assertEqual(variance.variance, 2)
        self.assertEqual(
            jan_2026["summary"]["operating_days"],
            jan_2026["summary"]["base_operating_days"] + 2,
        )
        self.assertEqual(
            jan_2027["summary"]["operating_days"],
            jan_2027["summary"]["base_operating_days"] + 2,
        )

    def test_sort_timeline_month_variance_clamps_adjusted_ops_days_to_zero(self):
        self._add_matrix_days("night", ["monday", "tuesday", "wednesday", "thursday", "friday"])
        self.client.post(
            "/motherbrain/sort-timeline",
            data=self._sort_timeline_form_data(
                provider_enabled="1",
                monthly_api_units="600",
                units_per_poll="2",
                month_variance_6="-100",
                api_enabled_night_monday="1",
                api_enabled_night_tuesday="1",
                api_enabled_night_wednesday="1",
                api_enabled_night_thursday="1",
                api_enabled_night_friday="1",
            ),
        )
        context = sort_timeline_context(
            self.rfd_gateway,
            "2026-06",
            now=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(context["summary"]["base_operating_days"], 22)
        self.assertEqual(context["summary"]["month_variance"], -100)
        self.assertEqual(context["summary"]["operating_days"], 0)
        self.assertEqual(context["summary"]["original_daily_poll_cap"], 0)

    def test_sort_timeline_old_added_removed_fields_do_not_drive_calculation(self):
        self._add_matrix_days("night", ["monday", "tuesday", "wednesday", "thursday", "friday"])
        response = self.client.post(
            "/motherbrain/sort-timeline",
            data=self._sort_timeline_form_data(
                provider_enabled="1",
                added_operating_days="2026-06-06\n2026-06-07",
                removed_operating_days="2026-06-01",
                api_enabled_night_monday="1",
                api_enabled_night_tuesday="1",
                api_enabled_night_wednesday="1",
                api_enabled_night_thursday="1",
                api_enabled_night_friday="1",
            ),
            follow_redirects=True,
        )
        context = sort_timeline_context(
            self.rfd_gateway,
            "2026-06",
            now=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(context["summary"]["base_operating_days"], 22)
        self.assertEqual(context["summary"]["month_variance"], 0)
        self.assertEqual(context["summary"]["operating_days"], 22)
        self.assertNotIn(b"Added Operating Days", response.data)
        self.assertNotIn(b"Removed Operating Days", response.data)

    def test_sort_timeline_special_polls_reduce_auto_polls_and_count_outside_window(self):
        self._add_matrix_cell("monday", "night")
        self.client.post(
            "/motherbrain/sort-timeline",
            data=self._sort_timeline_form_data(
                provider_enabled="1",
                monthly_api_units="44",
                units_per_poll="2",
                api_enabled_night_monday="1",
                night_polling_start="01:00",
                night_polling_end="03:00",
                night_special_poll_time=["00:30", "04:30"],
            ),
        )
        context = sort_timeline_context(
            self.rfd_gateway,
            "2026-06",
            now=datetime(2026, 6, 1, 6, 0, tzinfo=timezone.utc),
        )
        night_preview = context["preview_by_sort"]["night"]

        self.assertEqual(context["summary"]["operating_days"], 5)
        self.assertEqual(context["summary"]["monthly_poll_limit"], 22)
        self.assertEqual(context["summary"]["effective_daily_poll_cap"], 4)
        self.assertEqual(night_preview["special_poll_count"], 2)
        self.assertEqual(night_preview["budget_poll_interval_minutes"], 30)
        self.assertEqual(night_preview["actual_auto_poll_interval_minutes"], 30)
        self.assertEqual(night_preview["projected_polls_per_polling_day"], 4)
        self.assertEqual(context["summary"]["auto_interval_poll_count"], 2)
        self.assertEqual(context["summary"]["total_scheduled_polls"], 4)

    def test_sort_timeline_special_poll_delete_control_is_compact_x(self):
        self._add_matrix_cell("monday", "night")
        self.client.post(
            "/motherbrain/sort-timeline",
            data=self._sort_timeline_form_data(
                provider_enabled="1",
                api_enabled_night_monday="1",
                night_special_poll_time=["01:00"],
            ),
        )

        response = self.client.get("/motherbrain/sort-timeline?month=2026-06")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"sort-timeline-delete-row", response.data)
        self.assertIn(b"data-special-poll-delete", response.data)
        self.assertIn(b"data-delete-name=\"night_delete_special_poll_time\"", response.data)
        self.assertIn(b"&times;", response.data)
        self.assertNotIn(b"<span>Delete</span>", response.data)
        self.assertNotIn(b"sort-timeline-delete-checkbox", response.data)

    def test_sort_timeline_time_fields_use_military_text_inputs(self):
        self._add_matrix_cell("monday", "night")
        self.client.post(
            "/motherbrain/sort-timeline",
            data=self._sort_timeline_form_data(
                provider_enabled="1",
                api_enabled_night_monday="1",
                night_sort_start="22:15",
                night_polling_start="23:30",
                night_special_poll_time=["01:00"],
            ),
        )

        response = self.client.get("/motherbrain/sort-timeline?month=2026-06")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'data-military-time', response.data)
        self.assertIn(b'placeholder="HH:MM"', response.data)
        self.assertIn(b'name="night_sort_start"', response.data)
        self.assertIn(b'value="22:15"', response.data)
        self.assertIn(b'value="01:00"', response.data)
        self.assertNotIn(b'data-time-part="hour"', response.data)
        self.assertNotIn(b"NeoAppsTimeInputs", response.data)
        self.assertNotIn(b'type="time"', response.data)

    def test_sort_timeline_special_poll_delete_persists(self):
        self._add_matrix_cell("monday", "night")
        self.client.post(
            "/motherbrain/sort-timeline",
            data=self._sort_timeline_form_data(
                provider_enabled="1",
                api_enabled_night_monday="1",
                night_special_poll_time=["01:00", "02:00"],
            ),
        )

        response = self.client.post(
            "/motherbrain/sort-timeline",
            data=self._sort_timeline_form_data(
                provider_enabled="1",
                api_enabled_night_monday="1",
                night_special_poll_time=["02:00"],
                night_delete_special_poll_time=["01:00"],
            ),
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json",
            },
        )
        remaining_times = [
            row.poll_time_local.strftime("%H:%M")
            for row in SortTimelineSpecialPollTime.query.filter_by(
                gateway_id=self.rfd_gateway.id,
                sort_name="night",
            ).order_by(SortTimelineSpecialPollTime.poll_time_local.asc()).all()
        ]
        reload_response = self.client.get("/motherbrain/sort-timeline?month=2026-06")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(remaining_times, ["02:00"])
        self.assertNotIn(b'value="01:00"', reload_response.data)
        self.assertIn(b'value="02:00"', reload_response.data)

    def test_sort_timeline_blank_special_poll_rows_are_ignored(self):
        self._add_matrix_cell("monday", "night")
        self.client.post(
            "/motherbrain/sort-timeline",
            data=self._sort_timeline_form_data(
                provider_enabled="1",
                api_enabled_night_monday="1",
                night_special_poll_time=["", "01:00", ""],
            ),
        )
        saved_times = [
            row.poll_time_local.strftime("%H:%M")
            for row in SortTimelineSpecialPollTime.query.filter_by(
                gateway_id=self.rfd_gateway.id,
                sort_name="night",
            ).all()
        ]

        self.assertEqual(saved_times, ["01:00"])

    def test_sort_timeline_blank_special_poll_row_not_counted_in_preview(self):
        self._add_matrix_cell("monday", "night")
        response = self.client.post(
            "/motherbrain/sort-timeline",
            data=self._sort_timeline_form_data(
                provider_enabled="1",
                monthly_api_units="44",
                units_per_poll="2",
                api_enabled_night_monday="1",
                night_special_poll_time=["", "01:00", ""],
            ),
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json",
            },
        )
        payload = response.get_json()
        context = sort_timeline_context(
            self.rfd_gateway,
            "2026-06",
            now=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(payload["previews"]["2026-06"]["special_poll_count"], 1)
        self.assertEqual(payload["sort_previews"]["night"]["special_poll_count"], 1)
        self.assertEqual(context["summary"]["special_poll_count"], 1)

    def test_sort_timeline_valid_special_poll_updates_preview_math(self):
        self._add_matrix_cell("monday", "night")
        self.client.post(
            "/motherbrain/sort-timeline",
            data=self._sort_timeline_form_data(
                provider_enabled="1",
                monthly_api_units="44",
                units_per_poll="2",
                api_enabled_night_monday="1",
                night_polling_start="01:00",
                night_polling_end="03:00",
                night_special_poll_time=["01:00"],
            ),
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json",
            },
        )
        context = sort_timeline_context(
            self.rfd_gateway,
            "2026-06",
            now=datetime(2026, 6, 1, 6, 0, tzinfo=timezone.utc),
        )
        preview = context["summary"]

        self.assertEqual(preview["effective_daily_poll_cap"], 4)
        self.assertEqual(preview["special_poll_count"], 1)
        self.assertEqual(preview["auto_interval_poll_count"], 3)
        self.assertEqual(preview["total_scheduled_polls"], 4)
        self.assertEqual(preview["budget_poll_interval_minutes"], 30)
        self.assertEqual(preview["actual_auto_poll_interval_minutes"], 30)
        self.assertEqual(preview["projected_polls_per_polling_day"], 4)

    def test_sort_timeline_actual_auto_interval_respects_minimum(self):
        self._add_matrix_cell("monday", "night")
        self.client.post(
            "/motherbrain/sort-timeline",
            data=self._sort_timeline_form_data(
                provider_enabled="1",
                monthly_api_units="600",
                units_per_poll="2",
                minimum_auto_poll_interval_minutes="45",
                api_enabled_night_monday="1",
                night_polling_start="01:00",
                night_polling_end="03:00",
            ),
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json",
            },
        )
        context = sort_timeline_context(
            self.rfd_gateway,
            "2026-06",
            now=datetime(2026, 6, 1, 6, 0, tzinfo=timezone.utc),
        )
        night_preview = context["preview_by_sort"]["night"]

        self.assertEqual(context["summary"]["adjusted_daily_poll_cap"], 60)
        self.assertEqual(night_preview["budget_poll_interval_minutes"], 2)
        self.assertEqual(night_preview["actual_auto_poll_interval_minutes"], 45)
        self.assertEqual(night_preview["projected_polls_per_polling_day"], 2)

    def test_sort_timeline_projected_polls_do_not_exceed_adjusted_cap(self):
        self._add_matrix_cell("monday", "night")
        self.client.post(
            "/motherbrain/sort-timeline",
            data=self._sort_timeline_form_data(
                provider_enabled="1",
                monthly_api_units="10",
                units_per_poll="2",
                minimum_auto_poll_interval_minutes="1",
                api_enabled_night_monday="1",
                night_polling_start="01:00",
                night_polling_end="05:00",
            ),
        )
        context = sort_timeline_context(
            self.rfd_gateway,
            "2026-06",
            now=datetime(2026, 6, 1, 6, 0, tzinfo=timezone.utc),
        )
        night_preview = context["preview_by_sort"]["night"]

        self.assertEqual(context["summary"]["adjusted_daily_poll_cap"], 1)
        self.assertEqual(night_preview["projected_polls_per_polling_day"], 1)

    def test_sort_timeline_autosave_payload_includes_dynamic_special_poll_rows(self):
        self._add_matrix_cell("monday", "night")
        response = self.client.post(
            "/motherbrain/sort-timeline",
            data=self._sort_timeline_form_data(
                provider_enabled="1",
                monthly_api_units="44",
                units_per_poll="2",
                api_enabled_night_monday="1",
                night_special_poll_time=["01:00", "02:00", ""],
            ),
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json",
            },
        )
        payload = response.get_json()
        saved_times = sorted(
            row.poll_time_local.strftime("%H:%M")
            for row in SortTimelineSpecialPollTime.query.filter_by(
                gateway_id=self.rfd_gateway.id,
                sort_name="night",
            ).all()
        )

        self.assertEqual(saved_times, ["01:00", "02:00"])
        self.assertEqual(payload["sort_previews"]["night"]["special_poll_count"], 2)
        self.assertEqual(payload["previews"]["2026-06"]["special_poll_count"], 2)

    def test_sort_timeline_changing_special_poll_time_updates_count_without_duplicate(self):
        self._add_matrix_cell("monday", "night")
        self.client.post(
            "/motherbrain/sort-timeline",
            data=self._sort_timeline_form_data(
                provider_enabled="1",
                api_enabled_night_monday="1",
                night_special_poll_time=["01:00"],
            ),
        )

        response = self.client.post(
            "/motherbrain/sort-timeline",
            data=self._sort_timeline_form_data(
                provider_enabled="1",
                api_enabled_night_monday="1",
                night_special_poll_time=["02:00"],
                night_delete_special_poll_time=["01:00"],
            ),
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json",
            },
        )
        saved_times = [
            row.poll_time_local.strftime("%H:%M")
            for row in SortTimelineSpecialPollTime.query.filter_by(
                gateway_id=self.rfd_gateway.id,
                sort_name="night",
            ).order_by(SortTimelineSpecialPollTime.poll_time_local.asc()).all()
        ]
        payload = response.get_json()

        self.assertEqual(saved_times, ["02:00"])
        self.assertEqual(payload["sort_previews"]["night"]["special_poll_count"], 1)
        self.assertEqual(payload["previews"]["2026-06"]["special_poll_count"], 1)

    def test_sort_timeline_deleted_special_poll_not_counted_after_reload(self):
        self._add_matrix_cell("monday", "night")
        self.client.post(
            "/motherbrain/sort-timeline",
            data=self._sort_timeline_form_data(
                provider_enabled="1",
                api_enabled_night_monday="1",
                night_special_poll_time=["01:00", "02:00"],
            ),
        )
        self.client.post(
            "/motherbrain/sort-timeline",
            data=self._sort_timeline_form_data(
                provider_enabled="1",
                api_enabled_night_monday="1",
                night_special_poll_time=["02:00"],
                night_delete_special_poll_time=["01:00"],
            ),
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json",
            },
        )
        context = sort_timeline_context(
            self.rfd_gateway,
            "2026-06",
            now=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
        )
        reload_response = self.client.get("/motherbrain/sort-timeline?month=2026-06")

        self.assertEqual(context["summary"]["special_poll_count"], 1)
        self.assertEqual(context["preview_by_sort"]["night"]["special_poll_count"], 1)
        self.assertNotIn(b'value="01:00"', reload_response.data)
        self.assertIn(b'value="02:00"', reload_response.data)

    def test_sort_timeline_too_many_special_polls_clamp_auto_polls_to_zero(self):
        self._add_matrix_cell("monday", "night")
        self.client.post(
            "/motherbrain/sort-timeline",
            data=self._sort_timeline_form_data(
                provider_enabled="1",
                monthly_api_units="10",
                units_per_poll="2",
                api_enabled_night_monday="1",
                night_polling_start="01:00",
                night_polling_end="03:00",
                night_special_poll_time=["00:30", "01:30"],
            ),
        )
        context = sort_timeline_context(
            self.rfd_gateway,
            "2026-06",
            now=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
        )
        night_preview = context["preview_by_sort"]["night"]

        self.assertEqual(context["summary"]["effective_daily_poll_cap"], 1)
        self.assertEqual(night_preview["special_poll_count"], 2)
        self.assertEqual(context["summary"]["auto_interval_poll_count"], 0)
        self.assertEqual(context["summary"]["total_scheduled_polls"], 2)

    def test_sort_timeline_adjusted_daily_cap_drops_when_usage_is_high(self):
        self._add_matrix_days("night", ["monday", "tuesday", "wednesday", "thursday", "friday"])
        self.client.post(
            "/motherbrain/sort-timeline",
            data=self._sort_timeline_form_data(
                provider_enabled="1",
                api_enabled_night_monday="1",
                api_enabled_night_tuesday="1",
                api_enabled_night_wednesday="1",
                api_enabled_night_thursday="1",
                api_enabled_night_friday="1",
            ),
        )
        db.session.add(
            SortTimelineUsageCounter(
                gateway_id=self.rfd_gateway.id,
                gateway_code=self.rfd_gateway.code,
                month_key="2026-06",
                attempted_call_count=250,
            )
        )
        db.session.commit()
        context = sort_timeline_context(
            self.rfd_gateway,
            "2026-06",
            now=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(context["summary"]["monthly_poll_limit"], 300)
        self.assertEqual(context["summary"]["polls_used"], 250)
        self.assertEqual(context["summary"]["polls_remaining"], 50)
        self.assertEqual(context["summary"]["original_daily_poll_cap"], 13)
        self.assertEqual(context["summary"]["adjusted_daily_poll_cap"], 2)
        self.assertEqual(context["summary"]["effective_daily_poll_cap"], 2)

    def test_sort_timeline_budget_exhausted_schedules_zero_polls(self):
        self._add_matrix_cell("monday", "night")
        self.client.post(
            "/motherbrain/sort-timeline",
            data=self._sort_timeline_form_data(
                provider_enabled="1",
                monthly_api_units="10",
                units_per_poll="2",
                api_enabled_night_monday="1",
                night_special_poll_time=["01:00"],
            ),
        )
        db.session.add(
            SortTimelineUsageCounter(
                gateway_id=self.rfd_gateway.id,
                gateway_code=self.rfd_gateway.code,
                month_key="2026-06",
                attempted_call_count=5,
            )
        )
        db.session.commit()
        context = sort_timeline_context(
            self.rfd_gateway,
            "2026-06",
            now=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
        )

        self.assertTrue(context["summary"]["budget_exhausted"])
        self.assertEqual(context["summary"]["polls_remaining"], 0)
        self.assertEqual(context["summary"]["effective_daily_poll_cap"], 0)
        self.assertEqual(context["summary"]["auto_interval_poll_count"], 0)
        self.assertEqual(context["summary"]["total_scheduled_polls"], 0)

    def test_sort_timeline_preview_labels_and_values_render(self):
        self._add_matrix_cell("monday", "night")
        response = self.client.post(
            "/motherbrain/sort-timeline",
            data=self._sort_timeline_form_data(
                provider_enabled="1",
                api_enabled_night_monday="1",
            ),
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Current Month Preview", response.data)
        self.assertIn(b"Next Month Preview", response.data)
        self.assertIn(b"Monthly API Units", response.data)
        self.assertIn(b"Units Used", response.data)
        self.assertIn(b"Polls Remaining", response.data)
        self.assertIn(b"Full-Month API Polling Days", response.data)
        self.assertIn(b"Remaining API Polling Days", response.data)
        self.assertIn(b"Adjusted Daily Poll Cap", response.data)
        self.assertIn(b"Budget Poll Interval", response.data)
        self.assertIn(b"Minimum Auto Poll Interval", response.data)
        self.assertIn(b"Actual Auto Poll Interval", response.data)
        self.assertIn(b"Projected Polls Per Polling Day", response.data)
        self.assertNotIn(b"Effective Daily Poll Cap", response.data)

    def test_sort_timeline_usage_counter_uses_rfd_local_month_boundary(self):
        before_local_midnight = record_sort_timeline_api_attempt(
            self.rfd_gateway,
            datetime(2026, 7, 1, 4, 30, tzinfo=timezone.utc),
        )
        after_local_midnight = record_sort_timeline_api_attempt(
            self.rfd_gateway,
            datetime(2026, 7, 1, 5, 30, tzinfo=timezone.utc),
        )
        db.session.commit()

        self.assertEqual(before_local_midnight.month_key, "2026-06")
        self.assertEqual(after_local_midnight.month_key, "2026-07")
        self.assertEqual(
            SortTimelineUsageCounter.query.filter_by(month_key="2026-06").one().attempted_call_count,
            1,
        )
        self.assertEqual(
            SortTimelineUsageCounter.query.filter_by(month_key="2026-06").one().units_consumed,
            2,
        )
        self.assertEqual(
            SortTimelineUsageCounter.query.filter_by(month_key="2026-07").one().attempted_call_count,
            1,
        )
        self.assertEqual(
            SortTimelineUsageCounter.query.filter_by(month_key="2026-07").one().units_consumed,
            2,
        )

    def test_motherbrain_main_menu_footer_link_routes_home_without_logout(self):
        response = self.client.get("/motherbrain/master-schedule")
        html = response.data.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn("BACK TO NeoMotherBrain MAIN MENU", html)
        footer_html = html.split('class="motherbrain-main-menu-return"', 1)[1].split("</div>", 1)[0]
        self.assertIn('href="/motherbrain"', footer_html)
        self.assertLess(
            html.index("MASTER ARRIVALS"),
            html.index("BACK TO NeoMotherBrain MAIN MENU"),
        )

        home_response = self.client.get("/motherbrain", follow_redirects=False)
        self.assertEqual(home_response.status_code, 200)
        self.assertIn(b"neo-node-name neo-node-motherbrain", home_response.data)
        self.assertIn(b"Logout", home_response.data)
        self.assertNotIn(b"BACK TO NeoMotherBrain MAIN MENU", home_response.data)

    def test_gateway_matrix_displays_dynamic_gateway_and_sort_order(self):
        response = self.client.get("/motherbrain/gateway-matrix")
        html = response.data.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn("centered-command-page", html)
        self.assertIn("gateway-matrix-heading-block", html)
        self.assertIn("SET ACTIVE SORTS FOR RFD", html)
        for sort_header in ("Sunrise Sort", "Day Sort", "Twilight Sort", "Night Sort"):
            self.assertIn(sort_header, html)
        self.assertLess(
            html.index('name="monday_sunrise"'),
            html.index('name="monday_day"'),
        )
        self.assertLess(
            html.index('name="monday_day"'),
            html.index('name="monday_twilight"'),
        )
        self.assertLess(
            html.index('name="monday_twilight"'),
            html.index('name="monday_night"'),
        )

    def test_gateway_matrix_saves_current_gateway_sort_toggles(self):
        response = self.client.post(
            "/motherbrain/gateway-matrix",
            data={
                "monday_night": "1",
                "monday_day": "1",
                "tuesday_twilight": "1",
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        active_rows = GatewaySortMatrix.query.filter_by(
            gateway_id=self.rfd_gateway.id,
            is_active=True,
        ).all()
        self.assertEqual(
            {(row.day_of_week, row.sort_name) for row in active_rows},
            {
                ("monday", "night"),
                ("monday", "day"),
                ("tuesday", "twilight"),
            },
        )
        monday_day = GatewaySortMatrix.query.filter_by(
            gateway_id=self.rfd_gateway.id,
            day_of_week="monday",
            sort_name="day",
        ).one()
        self.assertEqual(monday_day.gateway_code, "RFD")
        self.assertTrue(monday_day.is_active)

    def test_motherbrain_auto_generates_today_active_matrix_sorts(self):
        sort_date = current_gateway_local_date(self.rfd_gateway)
        day = sort_date.strftime("%A").lower()
        self._add_matrix_cell(day, "night")
        self._add_master(
            flight_number="AUTO01",
            active_days=day,
            sort_name="night",
        )
        db.session.commit()

        response = self.client.get("/motherbrain")

        operation = SortDateOperation.query.filter_by(
            gateway_code="RFD",
            sort_date=sort_date,
            sort_name="night",
        ).first()
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(operation)
        self.assertEqual(len(operation.missions), 1)
        self.assertEqual(operation.missions[0].flight_number, "AUTO01")

    def test_manage_sort_creates_missing_operations_without_duplicates(self):
        sort_date = current_gateway_local_date(self.rfd_gateway)
        day = sort_date.strftime("%A").lower()
        self._add_matrix_cell(day, "night")
        self._add_master(
            flight_number="SORT01",
            active_days=day,
            sort_name="night",
        )
        db.session.commit()

        first_response = self.client.get("/motherbrain/manage-sort")
        second_response = self.client.get("/motherbrain/manage-sort")

        operations = SortDateOperation.query.filter_by(
            gateway_code="RFD",
            sort_date=sort_date,
            sort_name="night",
        ).all()
        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(len(operations), 1)
        self.assertIn(b"MANAGE SORT", first_response.data)
        self.assertIn(b"NIGHT", first_response.data)
        self.assertIn(b"ADD SPECIAL FLIGHT", first_response.data)
        html = first_response.data.decode()
        main_html = html.split('<main class="content">', 1)[1].split("</main>", 1)[0]
        workflow_html = main_html.split('class="motherbrain-main-menu-return"', 1)[0]
        self.assertIn("Current / Selected Sort Operation", workflow_html)
        self.assertIn("Sort Operation Settings", workflow_html)
        self.assertIn("Gateway Matrix / Schedule Source Controls", workflow_html)
        self.assertIn("API Polling Configuration", workflow_html)
        self.assertIn("Generate / Rebuild / Save Actions", workflow_html)
        self.assertIn("Warnings / Preview", workflow_html)
        self.assertIn('href="/motherbrain/gateway-matrix"', workflow_html)
        self.assertIn('href="/motherbrain/master-schedule"', workflow_html)
        self.assertIn('href="/motherbrain/sort-timeline"', workflow_html)
        self.assertNotIn('href="/motherbrain"', workflow_html)
        self.assertIn('class="motherbrain-main-menu-return"', main_html)
        self.assertIn('href="/motherbrain"', main_html)
        self.assertIn('aria-label="BACK TO NeoMotherBrain MAIN MENU"', main_html)

    def test_manage_sort_after_midnight_shows_previous_day_active_night_sort(self):
        self.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = datetime(2026, 6, 19, 0, 30)
        self._set_sort_window("night", time(22, 0), time(4, 0))
        previous_operation = self._operation(
            gateway_id=self.rfd_gateway.id,
            sort_date=date(2026, 6, 18),
            sort_name="night",
        )
        db.session.add(previous_operation)
        db.session.commit()

        response = self.client.get("/motherbrain/manage-sort")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"MANAGE SORT", response.data)
        self.assertIn(b"2026-06-18", response.data)
        self.assertIn(b"NIGHT", response.data)
        self.assertNotIn(b"No active sorts today.", response.data)

    def test_current_operations_drop_previous_day_night_after_sort_end(self):
        self._set_sort_window("night", time(22, 0), time(4, 0))
        previous_operation = self._operation(
            gateway_id=self.rfd_gateway.id,
            sort_date=date(2026, 6, 18),
            sort_name="night",
        )
        db.session.add(previous_operation)
        db.session.commit()

        operations = current_operations_for_gateway(
            self.rfd_gateway,
            now=datetime(2026, 6, 19, 4, 0),
        )

        self.assertNotIn(previous_operation, operations)

    def test_manage_sort_does_not_create_current_day_duplicate_while_prior_night_active(self):
        self.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = datetime(2026, 6, 19, 0, 30)
        self._set_sort_window("night", time(22, 0), time(4, 0))
        self._add_matrix_cell("friday", "night")
        previous_operation = self._operation(
            gateway_id=self.rfd_gateway.id,
            sort_date=date(2026, 6, 18),
            sort_name="night",
        )
        db.session.add(previous_operation)
        db.session.commit()

        response = self.client.get("/motherbrain/manage-sort")

        current_day_operations = SortDateOperation.query.filter_by(
            gateway_code="RFD",
            sort_date=date(2026, 6, 19),
            sort_name="night",
        ).all()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(current_day_operations, [])
        self.assertIn(b"2026-06-18", response.data)

    def test_manage_sort_syncs_new_master_rows_into_existing_operation(self):
        sort_date = current_gateway_local_date(self.rfd_gateway)
        day = sort_date.strftime("%A").lower()
        operation = self._operation(sort_date=sort_date)
        db.session.add(operation)
        master = self._add_master(
            flight_number="SYNCIN",
            active_days=day,
        )
        db.session.commit()

        response = self.client.get("/motherbrain/manage-sort")

        mission = SortDateMission.query.filter_by(flight_number="SYNCIN").one()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(mission.sort_date_operation_id, operation.id)
        self.assertEqual(mission.master_flight_schedule_id, master.id)
        self.assertEqual(mission.mission_source, "master")

    def test_operation_detail_syncs_newer_master_template_fields(self):
        master = self._add_master(
            flight_number="SYNCUP",
            active_days="monday",
            destination="SDF",
        )
        db.session.flush()
        operation = self._operation(
            sort_date=date(2026, 6, 1),
            generated_at_utc=datetime(2026, 1, 1, 0, 0),
        )
        db.session.add(operation)
        db.session.flush()
        mission = self._mission(
            operation=operation,
            mission_type="departure",
            flight_number="SYNCUP",
            mission_source="master",
            master_flight_schedule_id=master.id,
            destination="OLD",
        )
        db.session.add(mission)
        db.session.flush()
        master.destination = "ONT"
        master.planned_time_local = time(3, 20)
        master.updated_at = datetime(2026, 1, 2, 0, 0)
        db.session.commit()

        response = self.client.get(f"/motherbrain/operations/{operation.id}")

        updated_mission = db.session.get(SortDateMission, mission.id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(updated_mission.destination, "ONT")
        self.assertEqual(updated_mission.planned_datetime_local, datetime(2026, 6, 1, 3, 20))

    def test_manage_sort_empty_state_is_simple_centered_message(self):
        response = self.client.get("/motherbrain/manage-sort")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"No active sorts today.", response.data)
        self.assertIn(b"centered-empty-message", response.data)
        self.assertNotIn(b"No Active Sorts Today", response.data)
        self.assertNotIn(b"Open Gateway Matrix", response.data)
        self.assertNotIn(b"Enable today", response.data)

    def test_kessler_grandmaster_can_access_motherbrain_pages(self):
        operation = self._operation()
        db.session.add(operation)
        mission = self._mission(
            operation=operation,
            mission_type="departure",
            flight_number="DEPACCESS",
        )
        db.session.add(mission)
        db.session.commit()

        get_paths = (
            "/motherbrain",
            "/motherbrain/gateway-matrix",
            "/motherbrain/manage-sort",
            "/motherbrain/operations",
            "/motherbrain/operations/new",
            "/motherbrain/master-schedule",
            "/motherbrain/master-schedule/new",
            "/motherbrain/master-schedule/bulk-edit",
            f"/motherbrain/operations/{operation.id}",
            f"/motherbrain/operations/{operation.id}/arrivals",
            f"/motherbrain/operations/{operation.id}/departures",
            f"/motherbrain/operations/{operation.id}/missions/new",
            f"/motherbrain/operations/{operation.id}/missions/{mission.id}",
            f"/motherbrain/operations/{operation.id}/missions/{mission.id}/edit",
        )

        for path in get_paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(b'src="/static/images/motherbrain_logo1.png"', response.data)
                self.assertIn(b'class="motherbrain-header-logo-link"', response.data)
                if path == "/motherbrain":
                    self.assertIn(b"motherbrain-home-page", response.data)
                    self.assertIn(b"motherbrain-screen-logo", response.data)
                    self.assertIn(b"neo-node-name neo-node-motherbrain", response.data)
                    self.assertNotIn(b"BACK TO NeoMotherBrain MAIN MENU", response.data)
                else:
                    self.assertNotIn(b"motherbrain-home-page", response.data)
                    self.assertNotIn(b"motherbrain-screen-logo", response.data)
                    self.assertIn(b"BACK TO NeoMotherBrain MAIN MENU", response.data)
                    self.assertIn(b"motherbrain-main-menu-return", response.data)
                    self.assertIn(b"NeoMotherBrain", response.data)
                self.assertNotIn(b"NEOMOTHERBRAIN", response.data)
                self.assertNotIn(b">Command<", response.data)
                self.assertNotIn(b"Command Console", response.data)
                self.assertNotIn(b"NeoRFD Command", response.data)
                self.assertNotIn(b"NeoRFD command", response.data)
                self.assertNotIn(b"NEORFD COMMAND", response.data)
                self.assertNotIn(b"<p class=\"eyebrow\">NeoMotherBrain</p>", response.data)
                self.assertNotIn(b"MotherBrain Home", response.data)
                self.assertNotIn(b"Back to NeoMotherBrain", response.data)
                self.assertIn(b'href="/motherbrain"', response.data)

        response = self.client.post(
            f"/motherbrain/operations/{operation.id}/window",
            data={"window_minutes": "10"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)

    def test_master_schedule_requires_login(self):
        operation = self._operation()
        db.session.add(operation)
        db.session.commit()
        self.client.get("/logout")

        protected_paths = (
            "/motherbrain/master-schedule",
            "/motherbrain/operations",
            f"/motherbrain/operations/{operation.id}/missions/new",
        )
        for path in protected_paths:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 302)
                self.assertIn("/login", response.location)

    def test_user_without_rfd_access_cannot_enter_motherbrain(self):
        dfw_gateway = self._gateway("DFW", "NeoDFW")
        user = User(username="dfw_only", role="grandmaster")
        user.set_password("TestPassword123!")
        db.session.add(user)
        db.session.flush()
        db.session.add(
            GatewayMembership(
                user_id=user.id,
                gateway_id=dfw_gateway.id,
                status="approved",
                is_active=True,
            )
        )
        db.session.commit()

        client = self.app.test_client()
        client.post(
            "/login",
            data={"username": "dfw_only", "password": "TestPassword123!"},
        )
        response = client.get("/motherbrain", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/access-pending")

    def test_motherbrain_routes_do_not_leak_other_gateway_records(self):
        dfw_gateway = self._gateway("DFW", "NeoDFW")
        rfd_master = self._add_master(flight_number="RFD001", gateway_id=self.rfd_gateway.id)
        dfw_master = self._add_master(
            flight_number="DFW001",
            gateway_id=dfw_gateway.id,
            gateway_code="DFW",
            origin="DFW",
            destination="ONT",
        )
        rfd_operation = self._operation(gateway_id=self.rfd_gateway.id, gateway_code="RFD")
        dfw_operation = self._operation(gateway_id=dfw_gateway.id, gateway_code="DFW")
        db.session.add_all((rfd_operation, dfw_operation))
        db.session.flush()
        dfw_mission = self._mission(
            dfw_operation,
            "departure",
            "DFWDEP",
            gateway_code="DFW",
            origin="DFW",
            destination="ONT",
        )
        db.session.add(dfw_mission)
        db.session.commit()

        master_list = self.client.get("/motherbrain/master-schedule")
        operations_list = self.client.get("/motherbrain/operations")
        dfw_master_detail = self.client.get(f"/motherbrain/master-schedule/{dfw_master.id}")
        dfw_master_edit = self.client.get(f"/motherbrain/master-schedule/{dfw_master.id}/edit")
        dfw_operation_detail = self.client.get(f"/motherbrain/operations/{dfw_operation.id}")
        dfw_arrivals = self.client.get(f"/motherbrain/operations/{dfw_operation.id}/arrivals")
        dfw_departures = self.client.get(f"/motherbrain/operations/{dfw_operation.id}/departures")
        dfw_mission_detail = self.client.get(
            f"/motherbrain/operations/{dfw_operation.id}/missions/{dfw_mission.id}"
        )

        self.assertEqual(master_list.status_code, 200)
        self.assertIn(rfd_master.flight_number.encode(), master_list.data)
        self.assertNotIn(b"DFW001", master_list.data)
        self.assertEqual(operations_list.status_code, 200)
        self.assertIn(str(rfd_operation.sort_date).encode(), operations_list.data)
        self.assertNotIn(b"DFW", operations_list.data)
        for response in (
            dfw_master_detail,
            dfw_master_edit,
            dfw_operation_detail,
            dfw_arrivals,
            dfw_departures,
            dfw_mission_detail,
        ):
            self.assertEqual(response.status_code, 404)

    def test_logged_in_user_can_view_master_schedule_list(self):
        self._add_master(
            flight_number="ARR001",
            mission_type="arrival",
            origin="SDF",
            destination="RFD",
        )
        self._add_master(
            flight_number="DEP001",
            pure_pull_time_local=time(1, 10),
            first_mix_pull_time_local=time(1, 25),
            final_mix_pull_time_local=time(1, 40),
        )
        db.session.commit()

        response = self.client.get("/motherbrain/master-schedule")

        self.assertEqual(response.status_code, 200)
        html = response.data.decode()
        self.assertIn(b"MASTER FLIGHT SCHEDULE", response.data)
        self.assertIn(b"centered-command-page", response.data)
        self.assertIn(b"MASTER ARRIVALS", response.data)
        self.assertIn(b"MASTER DEPARTURES", response.data)
        self.assertIn(b"class=\"master-board-form\"", response.data)
        self.assertNotIn(b"Add Master Flights", response.data)
        self.assertIn(b"ADD ARRIVAL ROW", response.data)
        self.assertIn(b"ADD DEPARTURE ROW", response.data)
        self.assertIn(b"data-master-add-row", response.data)
        self.assertIn(b"data-master-row-template", response.data)
        self.assertNotIn(b"Edit Multiple Rows", response.data)
        self.assertIn(b"<th>FLIGHT</th>", response.data)
        self.assertIn(b"<th>ORIGIN</th>", response.data)
        self.assertIn(b"<th>AC Type</th>", response.data)
        self.assertIn(b"<th>WAVE</th>", response.data)
        self.assertIn(b"<th>STA</th>", response.data)
        self.assertIn(b"<th>DESTINATION</th>", response.data)
        self.assertIn(b"<th>STD</th>", response.data)
        self.assertIn(b"<th>PURE PULL</th>", response.data)
        self.assertIn(b"<th>1ST MIX</th>", response.data)
        self.assertIn(b"<th>2ND MIX</th>", response.data)
        self.assertIn(b"data-label=\"STD\"", response.data)
        self.assertIn(b"data-label=\"AC Type\"", response.data)
        self.assertIn(b"data-label=\"2nd Mix\"", response.data)
        self.assertIn(b"ARR001", response.data)
        self.assertIn(b"DEP001", response.data)
        self.assertIn(b"SDF", response.data)
        self.assertIn(b'name="row_arrival_0_planned_time_local_hour"', response.data)
        self.assertIn(b'data-time-max="23"', response.data)
        self.assertIn(b'data-time-max="59"', response.data)
        self.assertIn(b'data-time-part="hour"', response.data)
        self.assertIn(b'data-time-part="minute"', response.data)
        self.assertIn(b"<script>", response.data)
        self.assertIn(b"NeoAppsTimeInputs", response.data)
        self.assertIn(b'padStart(2, "0")', response.data)
        self.assertIn(b"minute.focus()", response.data)
        self.assertNotIn(b'<select name="row_arrival_0_planned_time_local_hour"', response.data)
        self.assertIn(b'name="row_arrival_0_aircraft_type"', response.data)
        self.assertIn(b'name="row_departure_0_aircraft_type"', response.data)
        self.assertIn(b'name="row_arrival_0_wave"', response.data)
        self.assertIn(b'name="row_departure_0_wave"', response.data)
        self.assertIn(b">1</option>", response.data)
        self.assertIn(b">2</option>", response.data)
        self.assertIn(b'name="row_departure_0_pure_pull_time_local_hour"', response.data)
        self.assertIn(b'name="row_departure_0_first_mix_pull_time_local_hour"', response.data)
        self.assertIn(b'name="row_departure_0_final_mix_pull_time_local_hour"', response.data)
        self.assertIn(b'name="row_arrival_new__INDEX___flight_number"', response.data)
        self.assertIn(b'name="row_departure_new__INDEX___flight_number"', response.data)
        self.assertIn(b'class="table-row-button master-row-save-button"', response.data)
        self.assertIn(b'name="master_save_row"', response.data)
        self.assertIn(b'value="arrival_new__INDEX__"', response.data)
        self.assertIn(b'value="departure_new__INDEX__"', response.data)
        self.assertIn(b">SAVE</button>", response.data)
        self.assertNotIn(b'name="row_arrival_new_flight_number"', response.data)
        self.assertNotIn(b'name="row_departure_new_flight_number"', response.data)
        self.assertNotIn(b"New arrival", response.data)
        self.assertNotIn(b"New departure", response.data)
        self.assertNotIn(b"SAVE MASTER ARRIVALS", response.data)
        self.assertNotIn(b"SAVE MASTER DEPARTURES", response.data)
        self.assertIn(b"<th>ACTION</th>", response.data)
        for aircraft_type in (b"A300", b"747", b"757", b"767", b"Other"):
            self.assertIn(b'<option value="' + aircraft_type + b'"', response.data)
        self.assertIn(b'<option value="" selected></option>', response.data)
        self.assertNotIn(b">AC</option>", response.data)
        self.assertNotIn(b">Edit</a>", response.data)
        self.assertIn(b"&times;</button>", response.data)
        self.assertLess(
            html.index("<th>STA</th>"),
            html.index("<th>WAVE</th>"),
        )
        departure_header_start = html.index('<section class="master-schedule-section" id="master-departures">')
        departure_header_html = html[departure_header_start:]
        self.assertLess(
            departure_header_html.index("<th>STD</th>"),
            departure_header_html.index("<th>WAVE</th>"),
        )
        self.assertLess(
            departure_header_html.index("<th>WAVE</th>"),
            departure_header_html.index("<th>PURE PULL</th>"),
        )
        self.assertNotIn(b"<th>TAIL</th>", response.data)
        self.assertNotIn(b"<th>PARKING</th>", response.data)
        self.assertNotIn(b"<th>ETA</th>", response.data)
        self.assertNotIn(b"<th>ETD</th>", response.data)
        self.assertNotIn(b"<th>FINAL MIX</th>", response.data)
        self.assertNotIn(b"data-label=\"Tail\"", response.data)
        self.assertNotIn(b"data-label=\"Parking\"", response.data)
        self.assertNotIn(b"data-label=\"ETA\"", response.data)
        self.assertNotIn(b"<th>MISSION</th>", response.data)
        self.assertNotIn(b"Origin/Destination", response.data)
        self.assertNotIn(b"<th>SORT</th>", response.data)
        self.assertNotIn(b"<th>ACTIVE DAYS</th>", response.data)
        self.assertNotIn(b"<td>night</td>", response.data)
        self.assertNotIn(b"<td>departure</td>", response.data)

    def test_master_schedule_list_sorts_existing_rows_by_schedule_time(self):
        self._add_master(
            flight_number="ARRLATE",
            mission_type="arrival",
            origin="SDF",
            destination="RFD",
            planned_time_local=time(3, 30),
            sort_name="night",
        )
        self._add_master(
            flight_number="ARREARLY",
            mission_type="arrival",
            origin="ONT",
            destination="RFD",
            planned_time_local=time(1, 10),
            sort_name="day",
        )
        self._add_master(
            flight_number="DEPLATE",
            destination="SDF",
            planned_time_local=time(5, 30),
            sort_name="night",
        )
        self._add_master(
            flight_number="DEPEARLY",
            destination="ONT",
            planned_time_local=time(2, 45),
            sort_name="day",
        )
        db.session.commit()

        response = self.client.get("/motherbrain/master-schedule")
        html = response.data.decode()

        self.assertEqual(response.status_code, 200)
        self.assertLess(html.index("ARREARLY"), html.index("ARRLATE"))
        self.assertLess(html.index("DEPEARLY"), html.index("DEPLATE"))

    def test_night_sort_time_key_places_after_midnight_times_after_late_night(self):
        self.assertLess(
            night_sort_time_key(time(23, 34), "night"),
            night_sort_time_key(time(0, 43), "night"),
        )
        self.assertLess(
            night_sort_time_key(time(0, 43), "day"),
            night_sort_time_key(time(23, 34), "day"),
        )

    def test_master_schedule_night_sort_orders_late_night_before_after_midnight(self):
        self._add_master(
            flight_number="NITE0043",
            mission_type="arrival",
            origin="SDF",
            destination="RFD",
            planned_time_local=time(0, 43),
            sort_name="night",
        )
        self._add_master(
            flight_number="NITE2334",
            mission_type="arrival",
            origin="ONT",
            destination="RFD",
            planned_time_local=time(23, 34),
            sort_name="night",
        )
        db.session.commit()

        response = self.client.get("/motherbrain/master-schedule")
        html = response.data.decode()

        self.assertEqual(response.status_code, 200)
        self.assertLess(html.index("NITE2334"), html.index("NITE0043"))

    def test_departure_board_night_sort_orders_late_night_before_after_midnight(self):
        operation = self._operation(sort_name="night")
        db.session.add(operation)
        db.session.add(
            self._mission(
                operation=operation,
                mission_type="departure",
                flight_number="DEP0043",
                planned_datetime_local=datetime(2026, 6, 2, 0, 43),
                planned_datetime_utc=datetime(2026, 6, 2, 5, 43),
            )
        )
        db.session.add(
            self._mission(
                operation=operation,
                mission_type="departure",
                flight_number="DEP2334",
                planned_datetime_local=datetime(2026, 6, 1, 23, 34),
                planned_datetime_utc=datetime(2026, 6, 2, 4, 34),
            )
        )
        db.session.commit()

        response = self.client.get(f"/motherbrain/operations/{operation.id}/departures")
        html = response.data.decode()

        self.assertEqual(response.status_code, 200)
        self.assertLess(html.index("DEP2334"), html.index("DEP0043"))

    def test_operation_overview_night_sort_orders_late_night_before_after_midnight(self):
        operation = self._operation(sort_name="night")
        db.session.add(operation)
        db.session.add(
            self._mission(
                operation=operation,
                mission_type="arrival",
                flight_number="ARR0043",
                planned_datetime_local=datetime(2026, 6, 2, 0, 43),
                planned_datetime_utc=datetime(2026, 6, 2, 5, 43),
            )
        )
        db.session.add(
            self._mission(
                operation=operation,
                mission_type="arrival",
                flight_number="ARR2334",
                planned_datetime_local=datetime(2026, 6, 1, 23, 34),
                planned_datetime_utc=datetime(2026, 6, 2, 4, 34),
            )
        )
        db.session.commit()

        response = self.client.get(f"/motherbrain/operations/{operation.id}")
        html = response.data.decode()

        self.assertEqual(response.status_code, 200)
        self.assertLess(html.index("ARR2334"), html.index("ARR0043"))

    def test_master_schedule_board_save_updates_existing_and_creates_new_row(self):
        existing = self._add_master(
            flight_number="DEPOLD",
            destination="SDF",
            active_days="monday,tuesday",
        )
        db.session.commit()

        response = self.client.post(
            "/motherbrain/master-schedule",
            data={
                "board_mission_type": "departure",
                "row_indexes": ["departure_0", "departure_new"],
                "row_departure_0_id": str(existing.id),
                "row_departure_0_mission_type": "departure",
                "row_departure_0_sort_name": "night",
                "row_departure_0_active": "1",
                "row_departure_0_active_days": ["monday", "tuesday"],
                "row_departure_0_flight_number": "depold",
                "row_departure_0_aircraft_type": "757",
                "row_departure_0_origin": "RFD",
                "row_departure_0_destination": "ont",
                "row_departure_0_planned_time_local": "03:15",
                "row_departure_0_pure_pull_time_local": "01:10",
                "row_departure_0_first_mix_pull_time_local": "01:25",
                "row_departure_0_final_mix_pull_time_local": "01:40",
                "row_departure_new_id": "",
                "row_departure_new_mission_type": "departure",
                "row_departure_new_sort_name": "night",
                "row_departure_new_active": "1",
                "row_departure_new_active_days": ["monday", "tuesday"],
                "row_departure_new_flight_number": "depnew",
                "row_departure_new_aircraft_type": "747",
                "row_departure_new_origin": "RFD",
                "row_departure_new_destination": "sdf",
                "row_departure_new_planned_time_local": "04:20",
                "row_departure_new_pure_pull_time_local": "02:10",
                "row_departure_new_first_mix_pull_time_local": "02:25",
                "row_departure_new_final_mix_pull_time_local": "02:40",
            },
            follow_redirects=False,
        )

        updated = db.session.get(MasterFlightSchedule, existing.id)
        created = MasterFlightSchedule.query.filter_by(flight_number="DEPNEW").first()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(updated.flight_number, "DEPOLD")
        self.assertEqual(updated.destination, "ONT")
        self.assertEqual(updated.aircraft_type, "757")
        self.assertEqual(updated.planned_time_local, time(3, 15))
        self.assertEqual(updated.pure_pull_time_local, time(1, 10))
        self.assertIsNotNone(created)
        self.assertEqual(created.origin, "RFD")
        self.assertEqual(created.destination, "SDF")
        self.assertEqual(created.aircraft_type, "747")
        self.assertEqual(created.planned_time_local, time(4, 20))
        self.assertEqual(created.final_mix_pull_time_local, time(2, 40))

    def test_master_schedule_board_autosave_json_updates_and_skips_incomplete_add_row(self):
        existing = self._add_master(
            flight_number="ARROLD",
            mission_type="arrival",
            origin="SDF",
            destination="RFD",
            active_days="monday,tuesday",
        )
        db.session.commit()

        response = self.client.post(
            "/motherbrain/master-schedule",
            data={
                "board_mission_type": "arrival",
                "row_indexes": ["arrival_0", "arrival_new"],
                "row_arrival_0_id": str(existing.id),
                "row_arrival_0_mission_type": "arrival",
                "row_arrival_0_sort_name": "night",
                "row_arrival_0_active": "1",
                "row_arrival_0_active_days": ["monday", "tuesday"],
                "row_arrival_0_flight_number": "arrold",
                "row_arrival_0_aircraft_type": "A300",
                "row_arrival_0_origin": "ont",
                "row_arrival_0_destination": "RFD",
                "row_arrival_0_planned_time_local": "03:15",
                "row_arrival_new_id": "",
                "row_arrival_new_mission_type": "arrival",
                "row_arrival_new_sort_name": "night",
                "row_arrival_new_active": "1",
                "row_arrival_new_active_days": ["monday", "tuesday"],
                "row_arrival_new_flight_number": "partial",
                "row_arrival_new_aircraft_type": "767",
                "row_arrival_new_origin": "",
                "row_arrival_new_destination": "RFD",
                "row_arrival_new_planned_time_local": "04:20",
            },
            headers={"Accept": "application/json", "X-Requested-With": "fetch"},
        )

        updated = db.session.get(MasterFlightSchedule, existing.id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["updated"], 1)
        self.assertEqual(response.get_json()["created"], 0)
        self.assertEqual(updated.origin, "ONT")
        self.assertEqual(updated.aircraft_type, "A300")
        self.assertIsNone(MasterFlightSchedule.query.filter_by(flight_number="PARTIAL").first())

    def test_master_schedule_departure_add_row_requires_pull_times_and_allows_blank_aircraft_type(self):
        partial = self.client.post(
            "/motherbrain/master-schedule",
            data={
                "board_mission_type": "departure",
                "row_indexes": ["departure_new"],
                "row_departure_new_id": "",
                "row_departure_new_mission_type": "departure",
                "row_departure_new_sort_name": "night",
                "row_departure_new_active": "1",
                "row_departure_new_active_days": ["monday", "tuesday"],
                "row_departure_new_flight_number": "depprt",
                "row_departure_new_aircraft_type": "",
                "row_departure_new_origin": "RFD",
                "row_departure_new_destination": "sdf",
                "row_departure_new_planned_time_local": "04:20",
                "row_departure_new_pure_pull_time_local": "",
                "row_departure_new_first_mix_pull_time_local": "",
                "row_departure_new_final_mix_pull_time_local": "",
            },
            headers={"Accept": "application/json", "X-Requested-With": "fetch"},
        )
        complete = self.client.post(
            "/motherbrain/master-schedule",
            data={
                "board_mission_type": "departure",
                "master_save_row": "departure_new",
                "row_indexes": ["departure_new"],
                "row_departure_new_id": "",
                "row_departure_new_mission_type": "departure",
                "row_departure_new_sort_name": "night",
                "row_departure_new_active": "1",
                "row_departure_new_active_days": ["monday", "tuesday"],
                "row_departure_new_flight_number": "depdone",
                "row_departure_new_aircraft_type": "",
                "row_departure_new_origin": "RFD",
                "row_departure_new_destination": "ont",
                "row_departure_new_planned_time_local": "05:20",
                "row_departure_new_pure_pull_time_local": "03:10",
                "row_departure_new_first_mix_pull_time_local": "03:25",
                "row_departure_new_final_mix_pull_time_local": "03:40",
            },
            headers={"Accept": "application/json", "X-Requested-With": "fetch"},
        )

        created = MasterFlightSchedule.query.filter_by(flight_number="DEPDONE").first()
        self.assertEqual(partial.status_code, 200)
        self.assertEqual(partial.get_json()["created"], 0)
        self.assertIsNone(MasterFlightSchedule.query.filter_by(flight_number="DEPPRT").first())
        self.assertEqual(complete.status_code, 200)
        self.assertEqual(complete.get_json()["created"], 1)
        self.assertIsNotNone(created)
        self.assertIsNone(created.aircraft_type)
        self.assertEqual(created.destination, "ONT")
        self.assertEqual(created.planned_time_local, time(5, 20))

    def test_master_schedule_existing_departure_autosave_does_not_create_unsaved_add_row(self):
        existing = self._add_master(
            flight_number="DEPAUTO",
            destination="SDF",
            active_days="monday,tuesday",
        )
        db.session.commit()

        response = self.client.post(
            "/motherbrain/master-schedule",
            data={
                "board_mission_type": "departure",
                "row_indexes": ["departure_0", "departure_new0"],
                "row_departure_0_id": str(existing.id),
                "row_departure_0_mission_type": "departure",
                "row_departure_0_sort_name": "night",
                "row_departure_0_active": "1",
                "row_departure_0_active_days": ["monday", "tuesday"],
                "row_departure_0_flight_number": "depauto",
                "row_departure_0_aircraft_type": "757",
                "row_departure_0_origin": "RFD",
                "row_departure_0_destination": "ont",
                "row_departure_0_planned_time_local_hour": "3",
                "row_departure_0_planned_time_local_minute": "15",
                "row_departure_0_pure_pull_time_local_hour": "1",
                "row_departure_0_pure_pull_time_local_minute": "10",
                "row_departure_0_first_mix_pull_time_local_hour": "1",
                "row_departure_0_first_mix_pull_time_local_minute": "25",
                "row_departure_0_final_mix_pull_time_local_hour": "1",
                "row_departure_0_final_mix_pull_time_local_minute": "40",
                "row_departure_new0_id": "",
                "row_departure_new0_mission_type": "departure",
                "row_departure_new0_sort_name": "night",
                "row_departure_new0_active": "1",
                "row_departure_new0_active_days": ["monday", "tuesday"],
                "row_departure_new0_flight_number": "unsaved",
                "row_departure_new0_aircraft_type": "",
                "row_departure_new0_origin": "RFD",
                "row_departure_new0_destination": "sdf",
                "row_departure_new0_planned_time_local_hour": "4",
                "row_departure_new0_planned_time_local_minute": "20",
                "row_departure_new0_pure_pull_time_local_hour": "2",
                "row_departure_new0_pure_pull_time_local_minute": "10",
                "row_departure_new0_first_mix_pull_time_local_hour": "2",
                "row_departure_new0_first_mix_pull_time_local_minute": "25",
                "row_departure_new0_final_mix_pull_time_local_hour": "2",
                "row_departure_new0_final_mix_pull_time_local_minute": "40",
            },
            headers={"Accept": "application/json", "X-Requested-With": "fetch"},
        )

        updated = db.session.get(MasterFlightSchedule, existing.id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["updated"], 1)
        self.assertEqual(response.get_json()["created"], 0)
        self.assertEqual(updated.destination, "ONT")
        self.assertEqual(updated.aircraft_type, "757")
        self.assertEqual(updated.planned_time_local, time(3, 15))
        self.assertIsNone(MasterFlightSchedule.query.filter_by(flight_number="UNSAVED").first())

    def test_master_schedule_board_save_creates_multiple_dynamic_add_rows(self):
        response = self.client.post(
            "/motherbrain/master-schedule",
            data={
                "board_mission_type": "arrival",
                "row_indexes": ["arrival_new0", "arrival_new1"],
                "row_arrival_new0_id": "",
                "row_arrival_new0_mission_type": "arrival",
                "row_arrival_new0_sort_name": "night",
                "row_arrival_new0_active": "1",
                "row_arrival_new0_active_days": ["monday", "tuesday"],
                "row_arrival_new0_flight_number": "arr100",
                "row_arrival_new0_aircraft_type": "",
                "row_arrival_new0_origin": "sdf",
                "row_arrival_new0_destination": "RFD",
                "row_arrival_new0_planned_time_local": "01:15",
                "row_arrival_new1_id": "",
                "row_arrival_new1_mission_type": "arrival",
                "row_arrival_new1_sort_name": "night",
                "row_arrival_new1_active": "1",
                "row_arrival_new1_active_days": ["monday", "tuesday"],
                "row_arrival_new1_flight_number": "arr101",
                "row_arrival_new1_aircraft_type": "A300",
                "row_arrival_new1_origin": "ont",
                "row_arrival_new1_destination": "RFD",
                "row_arrival_new1_planned_time_local": "02:25",
            },
            follow_redirects=False,
        )

        created = MasterFlightSchedule.query.order_by(
            MasterFlightSchedule.flight_number
        ).all()
        self.assertEqual(response.status_code, 302)
        self.assertEqual([row.flight_number for row in created], ["ARR100", "ARR101"])
        self.assertEqual(created[0].origin, "SDF")
        self.assertIsNone(created[0].aircraft_type)
        self.assertEqual(created[1].aircraft_type, "A300")

    def test_master_schedule_new_arrival_row_save_button_saves_and_reloads(self):
        response = self.client.post(
            "/motherbrain/master-schedule",
            data={
                "board_mission_type": "arrival",
                "master_save_row": "arrival_new0",
                "row_indexes": ["arrival_new0"],
                "row_arrival_new0_id": "",
                "row_arrival_new0_mission_type": "arrival",
                "row_arrival_new0_sort_name": "night",
                "row_arrival_new0_active": "1",
                "row_arrival_new0_active_days": ["monday", "tuesday"],
                "row_arrival_new0_flight_number": "arrsave",
                "row_arrival_new0_aircraft_type": "",
                "row_arrival_new0_origin": "sdf",
                "row_arrival_new0_destination": "RFD",
                "row_arrival_new0_planned_time_local_hour": "1",
                "row_arrival_new0_planned_time_local_minute": "5",
            },
            headers={"Accept": "application/json", "X-Requested-With": "fetch"},
        )
        reload_response = self.client.get("/motherbrain/master-schedule")

        created = MasterFlightSchedule.query.filter_by(flight_number="ARRSAVE").first()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["created"], 1)
        self.assertIsNotNone(created)
        self.assertEqual(created.origin, "SDF")
        self.assertEqual(created.destination, "RFD")
        self.assertIsNone(created.wave)
        self.assertEqual(created.planned_time_local, time(1, 5))
        self.assertIn(b"ARRSAVE", reload_response.data)

    def test_master_schedule_new_arrival_allows_same_origin_with_different_flight(self):
        existing = self._add_master(
            flight_number="ARRSDF1",
            mission_type="arrival",
            origin="SDF",
            destination="RFD",
            planned_time_local=time(0, 30),
            active_days="monday,tuesday",
        )
        db.session.commit()

        response = self.client.post(
            "/motherbrain/master-schedule",
            data={
                "board_mission_type": "arrival",
                "master_save_row": "arrival_new0",
                "row_indexes": ["arrival_0", "arrival_new0"],
                "row_arrival_0_id": str(existing.id),
                "row_arrival_0_mission_type": "arrival",
                "row_arrival_0_sort_name": "night",
                "row_arrival_0_active": "1",
                "row_arrival_0_active_days": ["monday", "tuesday"],
                "row_arrival_0_flight_number": "arrsdf1",
                "row_arrival_0_aircraft_type": "",
                "row_arrival_0_origin": "sdf",
                "row_arrival_0_destination": "RFD",
                "row_arrival_0_planned_time_local_hour": "0",
                "row_arrival_0_planned_time_local_minute": "30",
                "row_arrival_0_wave": "1",
                "row_arrival_new0_id": "",
                "row_arrival_new0_mission_type": "arrival",
                "row_arrival_new0_sort_name": "night",
                "row_arrival_new0_active": "1",
                "row_arrival_new0_active_days": ["monday", "tuesday"],
                "row_arrival_new0_flight_number": "arrsdf2",
                "row_arrival_new0_aircraft_type": "",
                "row_arrival_new0_origin": "sdf",
                "row_arrival_new0_destination": "RFD",
                "row_arrival_new0_planned_time_local_hour": "1",
                "row_arrival_new0_planned_time_local_minute": "5",
                "row_arrival_new0_wave": "1",
            },
            headers={"Accept": "application/json", "X-Requested-With": "fetch"},
        )

        created = MasterFlightSchedule.query.filter_by(flight_number="ARRSDF2").first()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["created"], 1)
        self.assertEqual(response.get_json()["updated"], 1)
        self.assertIsNotNone(created)
        self.assertEqual(created.origin, "SDF")
        self.assertEqual(created.planned_time_local, time(1, 5))

    def test_master_schedule_new_departure_row_save_button_saves_and_reloads(self):
        response = self.client.post(
            "/motherbrain/master-schedule",
            data={
                "board_mission_type": "departure",
                "master_save_row": "departure_new0",
                "row_indexes": ["departure_new0"],
                "row_departure_new0_id": "",
                "row_departure_new0_mission_type": "departure",
                "row_departure_new0_sort_name": "night",
                "row_departure_new0_active": "1",
                "row_departure_new0_active_days": ["monday", "tuesday"],
                "row_departure_new0_flight_number": "depsave",
                "row_departure_new0_aircraft_type": "",
                "row_departure_new0_origin": "RFD",
                "row_departure_new0_destination": "ont",
                "row_departure_new0_planned_time_local_hour": "4",
                "row_departure_new0_planned_time_local_minute": "20",
                "row_departure_new0_pure_pull_time_local_hour": "2",
                "row_departure_new0_pure_pull_time_local_minute": "10",
                "row_departure_new0_first_mix_pull_time_local_hour": "2",
                "row_departure_new0_first_mix_pull_time_local_minute": "25",
                "row_departure_new0_final_mix_pull_time_local_hour": "2",
                "row_departure_new0_final_mix_pull_time_local_minute": "40",
            },
            headers={"Accept": "application/json", "X-Requested-With": "fetch"},
        )
        reload_response = self.client.get("/motherbrain/master-schedule")

        created = MasterFlightSchedule.query.filter_by(flight_number="DEPSAVE").first()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["created"], 1)
        self.assertIsNotNone(created)
        self.assertEqual(created.destination, "ONT")
        self.assertIsNone(created.wave)
        self.assertEqual(created.planned_time_local, time(4, 20))
        self.assertEqual(created.pure_pull_time_local, time(2, 10))
        self.assertEqual(created.final_mix_pull_time_local, time(2, 40))
        self.assertIn(b"DEPSAVE", reload_response.data)

    def test_master_schedule_explicit_new_row_save_rejects_invalid_partial_row(self):
        response = self.client.post(
            "/motherbrain/master-schedule",
            data={
                "board_mission_type": "arrival",
                "master_save_row": "arrival_new0",
                "row_indexes": ["arrival_new0"],
                "row_arrival_new0_id": "",
                "row_arrival_new0_mission_type": "arrival",
                "row_arrival_new0_sort_name": "night",
                "row_arrival_new0_active": "1",
                "row_arrival_new0_active_days": ["monday", "tuesday"],
                "row_arrival_new0_flight_number": "badadd",
                "row_arrival_new0_aircraft_type": "",
                "row_arrival_new0_origin": "",
                "row_arrival_new0_destination": "RFD",
                "row_arrival_new0_planned_time_local_hour": "1",
                "row_arrival_new0_planned_time_local_minute": "5",
            },
            headers={"Accept": "application/json", "X-Requested-With": "fetch"},
        )

        payload = response.get_json()
        self.assertEqual(response.status_code, 400)
        self.assertFalse(payload["ok"])
        self.assertIn("Complete all required fields", payload["message"])
        self.assertIsNone(MasterFlightSchedule.query.filter_by(flight_number="BADADD").first())

    def test_master_schedule_form_uses_limited_sort_dropdown_and_capitalized_missions(self):
        response = self.client.get("/motherbrain/master-schedule/new")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'<form class="master-schedule-single-form"', response.data)
        self.assertIn(b'data-master-mode="arrival"', response.data)
        self.assertIn(b'data-master-mode="departure"', response.data)
        self.assertIn(b'<select name="row_0_sort_name">', response.data)
        self.assertIn(b'<select name="row_0_aircraft_type">', response.data)
        self.assertNotIn(b'name="sort_name" value=', response.data)
        for value, label in (
            (b"night", b"Night"),
            (b"twilight", b"Twilight"),
            (b"day", b"Day"),
            (b"sunrise", b"Sunrise"),
        ):
            self.assertIn(b'<option value="' + value + b'"', response.data)
            self.assertIn(b">" + label + b"</option>", response.data)
        self.assertIn(b">ARRIVAL</a>", response.data)
        self.assertIn(b">DEPARTURE</a>", response.data)
        for aircraft_type in (b"A300", b"747", b"757", b"767", b"Other"):
            self.assertIn(b'<option value="' + aircraft_type + b'"', response.data)
        self.assertNotIn(b">arrival</option>", response.data)
        self.assertNotIn(b">departure</option>", response.data)

    def test_add_master_schedule_arrival_mode_does_not_render_pull_time_fields(self):
        response = self.client.get("/motherbrain/master-schedule/new?mission_type=arrival")
        html = response.data.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn('data-master-mode="arrival" aria-selected="true"', html)
        self.assertIn(">STA</span>", html)
        self.assertIn(b"SAVE ARRIVAL", response.data)
        self.assertIn('<select name="row_0_sort_name">', html)
        self.assertIn(b">SORT</span>", response.data)
        self.assertIn(b">ORIGIN</span>", response.data)
        self.assertNotIn(b">DESTINATION</span>", response.data)
        self.assertNotIn(b">STD</span>", response.data)
        self.assertNotIn(b"PURE PULL", response.data)
        self.assertNotIn(b"FIRST MIX PULL", response.data)
        self.assertNotIn(b"FINAL MIX PULL", response.data)
        self.assertNotIn(b"pure_pull_time_local_hour", response.data)
        self.assertNotIn(b"first_mix_pull_time_local_hour", response.data)
        self.assertNotIn(b"final_mix_pull_time_local_hour", response.data)

    def test_master_schedule_arrival_mode_hides_pull_time_fields(self):
        master = self._add_master(
            mission_type="arrival",
            flight_number="ARRMODE",
            origin="SDF",
            destination="RFD",
        )
        db.session.commit()

        response = self.client.get(f"/motherbrain/master-schedule/{master.id}/edit")
        html = response.data.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn(">STA</span>", html)
        self.assertIn(b"SAVE ARRIVAL", response.data)
        self.assertIn('type="hidden" name="row_0_mission_type" value="arrival"', html)
        self.assertIn('type="hidden" name="row_0_sort_name" value="night"', html)
        self.assertNotIn('data-master-mode="arrival"', html)
        self.assertNotIn('data-master-mode="departure"', html)
        self.assertNotIn('<select name="row_0_sort_name">', html)
        self.assertNotIn(b">SORT</span>", response.data)
        self.assertIn(b">ORIGIN</span>", response.data)
        self.assertNotIn(b">DESTINATION</span>", response.data)
        self.assertNotIn(b">STD</span>", response.data)
        self.assertNotIn(b"PURE PULL", response.data)
        self.assertNotIn(b"FIRST MIX PULL", response.data)
        self.assertNotIn(b"FINAL MIX PULL", response.data)
        self.assertNotIn(b"pure_pull_time_local_hour", response.data)
        self.assertNotIn(b"first_mix_pull_time_local_hour", response.data)
        self.assertNotIn(b"final_mix_pull_time_local_hour", response.data)

    def test_master_schedule_departure_mode_shows_pull_time_fields(self):
        response = self.client.get("/motherbrain/master-schedule/new")
        html = response.data.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn('data-master-mode="departure" aria-selected="true"', html)
        self.assertIn(">STD</span>", html)
        self.assertIn(b"SAVE DEPARTURE", response.data)
        self.assertIn(b">DESTINATION</span>", response.data)
        self.assertNotIn(b">ORIGIN</span>", response.data)
        self.assertNotIn(b">STA</span>", response.data)
        self.assertEqual(html.count('class="master-pull-field" data-departure-only'), 3)
        self.assertNotIn("data-departure-only hidden", html)
        self.assertIn(b"PURE PULL", response.data)
        self.assertIn(b"FIRST MIX PULL", response.data)
        self.assertIn(b"FINAL MIX PULL", response.data)

    def test_rfd_master_schedule_airport_defaults_use_current_gateway(self):
        arrival = self.client.get("/motherbrain/master-schedule/new?mission_type=arrival")
        departure = self.client.get("/motherbrain/master-schedule/new?mission_type=departure")
        arrival_html = arrival.data.decode()
        departure_html = departure.data.decode()

        self.assertEqual(arrival.status_code, 200)
        self.assertIn('type="hidden" name="row_0_destination"', arrival_html)
        self.assertIn('value="RFD"', arrival_html)
        self.assertIn('name="row_0_origin"', arrival_html)
        self.assertNotIn('readonly aria-readonly="true"', arrival_html)
        self.assertEqual(departure.status_code, 200)
        self.assertIn('type="hidden" name="row_0_origin"', departure_html)
        self.assertIn('value="RFD"', departure_html)
        self.assertIn('name="row_0_destination"', departure_html)
        self.assertNotIn('readonly aria-readonly="true"', departure_html)

    def test_master_schedule_create_defaults_home_airport_from_current_gateway(self):
        arrival = self.client.post(
            "/motherbrain/master-schedule/new",
            data=self._master_schedule_form_data(
                mission_type="arrival",
                flight_number="ARRDEF",
                origin="SDF",
                destination="",
            ),
            follow_redirects=False,
        )
        departure = self.client.post(
            "/motherbrain/master-schedule/new",
            data=self._master_schedule_form_data(
                flight_number="DEPDEF",
                origin="",
                destination="SDF",
            ),
            follow_redirects=False,
        )

        arrival_master = MasterFlightSchedule.query.filter_by(flight_number="ARRDEF").first()
        departure_master = MasterFlightSchedule.query.filter_by(flight_number="DEPDEF").first()
        self.assertEqual(arrival.status_code, 302)
        self.assertEqual(departure.status_code, 302)
        self.assertEqual(arrival_master.destination, "RFD")
        self.assertEqual(departure_master.origin, "RFD")
        self.assertEqual(arrival_master.gateway_id, self.rfd_gateway.id)
        self.assertEqual(departure_master.gateway_id, self.rfd_gateway.id)

    def test_bulk_create_master_schedule_rows(self):
        response = self.client.post(
            "/motherbrain/master-schedule/new",
            data=self._bulk_master_schedule_form_data(
                {
                    "flight_number": "BULK001",
                    "origin": "rfd",
                    "destination": "sdf",
                    "planned_time_local": "01:10",
                    "pure_pull_time_local": "00:40",
                },
                {
                    "mission_type": "arrival",
                    "flight_number": "BULK002",
                    "origin": "sdf",
                    "destination": "rfd",
                    "planned_time_local": "03:20",
                    "pure_pull_time_local": "01:10",
                },
            ),
            follow_redirects=False,
        )

        departure = MasterFlightSchedule.query.filter_by(flight_number="BULK001").first()
        arrival = MasterFlightSchedule.query.filter_by(flight_number="BULK002").first()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/motherbrain/master-schedule")
        self.assertEqual(departure.origin, "RFD")
        self.assertEqual(departure.destination, "SDF")
        self.assertEqual(departure.pure_pull_time_local, time(0, 40))
        self.assertEqual(arrival.mission_type, "arrival")
        self.assertIsNone(arrival.pure_pull_time_local)

    def test_bulk_edit_master_schedule_rows(self):
        first = self._add_master(flight_number="EDITA1", active=True)
        second = self._add_master(
            flight_number="EDITA2",
            mission_type="arrival",
            origin="SDF",
            destination="RFD",
            active=True,
        )
        db.session.commit()

        response = self.client.post(
            "/motherbrain/master-schedule/bulk-edit",
            data=self._bulk_master_schedule_form_data(
                {
                    "id": str(first.id),
                    "flight_number": "EDITA1",
                    "origin": "RFD",
                    "destination": "ONT",
                    "planned_time_local": "04:15",
                    "active_days": ["monday", "friday"],
                },
                {
                    "id": str(second.id),
                    "mission_type": "departure",
                    "flight_number": "EDITA2",
                    "origin": "RFD",
                    "destination": "SDF",
                    "planned_time_local": "05:20",
                    "first_mix_pull_time_local": "04:45",
                },
            ),
            follow_redirects=False,
        )

        updated_first = db.session.get(MasterFlightSchedule, first.id)
        updated_second = db.session.get(MasterFlightSchedule, second.id)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(updated_first.destination, "ONT")
        self.assertEqual(updated_first.active_days, "monday,friday")
        self.assertEqual(updated_second.mission_type, "departure")
        self.assertEqual(updated_second.first_mix_pull_time_local, time(4, 45))

    def test_master_schedule_rejects_unknown_sort_name(self):
        response = self.client.post(
            "/motherbrain/master-schedule/new",
            data=self._master_schedule_form_data(sort_name="midnight"),
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(b"Sort name must be Night, Twilight, Day, or Sunrise.", response.data)
        self.assertIsNone(MasterFlightSchedule.query.filter_by(sort_name="midnight").first())

    def test_master_schedule_rejects_flight_number_over_8_characters(self):
        response = self.client.post(
            "/motherbrain/master-schedule/new",
            data=self._master_schedule_form_data(flight_number="FLIGHT999"),
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(b"Flight number must be 8 characters or fewer.", response.data)
        self.assertIsNone(MasterFlightSchedule.query.filter_by(flight_number="FLIGHT999").first())

    def test_master_schedule_origin_destination_are_three_letters_and_save_uppercase(self):
        invalid = self.client.post(
            "/motherbrain/master-schedule/new",
            data=self._master_schedule_form_data(destination="SD1"),
        )
        valid = self.client.post(
            "/motherbrain/master-schedule/new",
            data=self._master_schedule_form_data(
                flight_number="UP123",
                origin="rfd",
                destination="sdf",
            ),
            follow_redirects=False,
        )

        master = MasterFlightSchedule.query.filter_by(flight_number="UP123").first()
        self.assertEqual(invalid.status_code, 400)
        self.assertIn(b"Destination must be exactly 3 letters.", invalid.data)
        self.assertEqual(valid.status_code, 302)
        self.assertEqual(master.flight_number, "UP123")
        self.assertEqual(master.origin, "RFD")
        self.assertEqual(master.destination, "SDF")

    def test_master_schedule_aircraft_type_saves_and_rejects_unknown(self):
        valid = self.client.post(
            "/motherbrain/master-schedule/new",
            data=self._master_schedule_form_data(
                flight_number="ACTYPE1",
                aircraft_type="Other",
            ),
            follow_redirects=False,
        )
        invalid = self.client.post(
            "/motherbrain/master-schedule/new",
            data=self._master_schedule_form_data(
                flight_number="ACTYPE2",
                aircraft_type="A330",
            ),
        )

        master = MasterFlightSchedule.query.filter_by(flight_number="ACTYPE1").first()
        self.assertEqual(valid.status_code, 302)
        self.assertEqual(master.aircraft_type, "Other")
        self.assertEqual(invalid.status_code, 400)
        self.assertIn(b"AC Type must be A300, 747, 757, 767, or Other.", invalid.data)
        self.assertIsNone(MasterFlightSchedule.query.filter_by(flight_number="ACTYPE2").first())

    def test_master_schedule_flight_number_saves_uppercase(self):
        response = self.client.post(
            "/motherbrain/master-schedule/new",
            data=self._master_schedule_form_data(flight_number="up789"),
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertIsNotNone(MasterFlightSchedule.query.filter_by(flight_number="UP789").first())

    def test_master_schedule_saves_arrival_wave_assignment(self):
        response = self.client.post(
            "/motherbrain/master-schedule/new",
            data=self._master_schedule_form_data(
                mission_type="arrival",
                flight_number="WAVE01",
                origin="SDF",
                destination="RFD",
                wave="2",
            ),
            follow_redirects=False,
        )

        master = MasterFlightSchedule.query.filter_by(flight_number="WAVE01").first()
        detail_response = self.client.get(f"/motherbrain/master-schedule/{master.id}")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(master.wave, "2")
        self.assertIn(b"WAVE", detail_response.data)
        self.assertIn(b">2</dd>", detail_response.data)

    def test_master_schedule_saves_departure_wave_assignment(self):
        response = self.client.post(
            "/motherbrain/master-schedule/new",
            data=self._master_schedule_form_data(
                flight_number="WAVE02",
                wave="2",
            ),
            follow_redirects=False,
        )

        master = MasterFlightSchedule.query.filter_by(flight_number="WAVE02").first()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(master.wave, "2")

    def test_master_schedule_allows_blank_wave_assignment(self):
        response = self.client.post(
            "/motherbrain/master-schedule/new",
            data=self._master_schedule_form_data(
                flight_number="WAVENULL",
                wave="",
            ),
            follow_redirects=False,
        )

        master = MasterFlightSchedule.query.filter_by(flight_number="WAVENULL").first()

        self.assertEqual(response.status_code, 302)
        self.assertIsNotNone(master)
        self.assertIsNone(master.wave)

    def test_master_schedule_rejects_invalid_wave_assignment(self):
        response = self.client.post(
            "/motherbrain/master-schedule/new",
            data=self._master_schedule_form_data(
                flight_number="WAVE03",
                wave="3",
            ),
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(b"Wave must be blank, 1, or 2.", response.data)
        self.assertIsNone(MasterFlightSchedule.query.filter_by(flight_number="WAVE03").first())

    def test_master_schedule_time_fields_use_24_hour_format_and_save(self):
        response = self.client.post(
            "/motherbrain/master-schedule/new",
            data=self._master_schedule_form_data(
                flight_number="TIME01",
                planned_time_local="23:45",
                pure_pull_time_local="20:10",
            ),
            follow_redirects=False,
        )
        form_response = self.client.get("/motherbrain/master-schedule/new")

        master = MasterFlightSchedule.query.filter_by(flight_number="TIME01").first()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(master.planned_time_local, time(23, 45))
        self.assertEqual(master.pure_pull_time_local, time(20, 10))
        self.assertIn(b'class="military-time-select"', form_response.data)
        self.assertIn(b'name="row_0_planned_time_local_hour"', form_response.data)
        self.assertIn(b'name="row_0_planned_time_local_minute"', form_response.data)
        self.assertIn(b'data-time-max="23"', form_response.data)
        self.assertIn(b'data-time-max="59"', form_response.data)
        self.assertIn(b'data-time-part="hour"', form_response.data)
        self.assertIn(b'data-time-part="minute"', form_response.data)
        self.assertIn(b"<script>", form_response.data)
        self.assertIn(b"NeoAppsTimeInputs", form_response.data)
        self.assertIn(b'padStart(2, "0")', form_response.data)
        self.assertIn(b"minute.focus()", form_response.data)
        self.assertNotIn(b'<select name="row_0_planned_time_local_hour"', form_response.data)
        self.assertNotIn(b'type="time"', form_response.data)

    def test_master_schedule_split_time_fields_save_zero_padded_values(self):
        data = self._master_schedule_form_data(
            flight_number="PAD001",
            planned_time_local="",
            pure_pull_time_local="",
            first_mix_pull_time_local="",
            final_mix_pull_time_local="",
        )
        data.update(
            {
                "planned_time_local_hour": "1",
                "planned_time_local_minute": "5",
                "pure_pull_time_local_hour": "0",
                "pure_pull_time_local_minute": "7",
                "first_mix_pull_time_local_hour": "2",
                "first_mix_pull_time_local_minute": "3",
                "final_mix_pull_time_local_hour": "4",
                "final_mix_pull_time_local_minute": "9",
            }
        )

        response = self.client.post(
            "/motherbrain/master-schedule/new",
            data=data,
            follow_redirects=False,
        )

        master = MasterFlightSchedule.query.filter_by(flight_number="PAD001").first()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(master.planned_time_local, time(1, 5))
        self.assertEqual(master.pure_pull_time_local, time(0, 7))
        self.assertEqual(master.first_mix_pull_time_local, time(2, 3))
        self.assertEqual(master.final_mix_pull_time_local, time(4, 9))

    def test_master_schedule_rejects_non_military_time(self):
        response = self.client.post(
            "/motherbrain/master-schedule/new",
            data=self._master_schedule_form_data(
                flight_number="BADTIME",
                planned_time_local="9:30",
            ),
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(b"Planned time must use HH:MM military format.", response.data)
        self.assertIsNone(MasterFlightSchedule.query.filter_by(flight_number="BADTIME").first())

    def test_master_schedule_timezone_is_not_selectable_and_uses_gateway_timezone(self):
        response = self.client.post(
            "/motherbrain/master-schedule/new",
            data=self._master_schedule_form_data(
                flight_number="TZ001",
                timezone="America/New_York",
            ),
            follow_redirects=False,
        )
        form_response = self.client.get("/motherbrain/master-schedule/new")

        master = MasterFlightSchedule.query.filter_by(flight_number="TZ001").first()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(master.timezone, "America/Chicago")
        self.assertNotIn(b'name="timezone"', form_response.data)

    def test_master_schedule_list_does_not_show_parking_when_applicable(self):
        master = self._add_master(flight_number="PARK01", preferred_parking="A1")
        db.session.commit()

        form_response = self.client.get(f"/motherbrain/master-schedule/{master.id}/edit")
        list_response = self.client.get("/motherbrain/master-schedule")
        detail_response = self.client.get(f"/motherbrain/master-schedule/{master.id}")

        self.assertEqual(list_response.status_code, 200)
        self.assertNotIn(b"Parking", list_response.data)
        self.assertNotIn(b"A1", list_response.data)

        for label, response in (("form", form_response), ("detail", detail_response)):
            with self.subTest(page=label):
                self.assertEqual(response.status_code, 200)
                self.assertNotIn(b"Preferred Parking", response.data)
                self.assertNotIn(b"Parking", response.data)
                self.assertNotIn(b"A1", response.data)
        self.assertNotIn(b">Edit</a>", detail_response.data)

    def test_delete_master_schedule_removes_row_and_preserves_generated_mission(self):
        master = self._add_master(flight_number="DELMS")
        operation = self._operation()
        db.session.add(operation)
        db.session.flush()
        mission = self._mission(
            operation=operation,
            mission_type="departure",
            flight_number="DELMS",
            master_flight_schedule_id=master.id,
        )
        db.session.add(mission)
        db.session.commit()

        response = self.client.post(
            f"/motherbrain/master-schedule/{master.id}/delete",
            follow_redirects=False,
        )

        updated_mission = db.session.get(SortDateMission, mission.id)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.location, "/motherbrain/master-schedule")
        self.assertIsNone(db.session.get(MasterFlightSchedule, master.id))
        self.assertIsNotNone(updated_mission)
        self.assertIsNone(updated_mission.master_flight_schedule_id)

    def test_create_departure_master_row_with_pull_times(self):
        response = self.client.post(
            "/motherbrain/master-schedule/new",
            data=self._master_schedule_form_data(
                flight_number="DEP100",
                pure_pull_time_local="01:20",
                first_mix_pull_time_local="01:40",
                final_mix_pull_time_local="01:55",
                active_days=["monday", "wednesday"],
            ),
            follow_redirects=False,
        )

        master = MasterFlightSchedule.query.filter_by(flight_number="DEP100").first()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(master.mission_type, "departure")
        self.assertEqual(master.active_days, "monday,wednesday")
        self.assertEqual(master.timezone, "America/Chicago")
        self.assertEqual(master.pure_pull_time_local, time(1, 20))
        self.assertEqual(master.first_mix_pull_time_local, time(1, 40))
        self.assertEqual(master.final_mix_pull_time_local, time(1, 55))

    def test_create_arrival_master_row_clears_pull_times(self):
        response = self.client.post(
            "/motherbrain/master-schedule/new",
            data=self._master_schedule_form_data(
                mission_type="arrival",
                flight_number="ARR100",
                origin="SDF",
                destination="RFD",
                pure_pull_time_local="01:20",
                first_mix_pull_time_local="01:40",
                final_mix_pull_time_local="01:55",
            ),
            follow_redirects=False,
        )

        master = MasterFlightSchedule.query.filter_by(flight_number="ARR100").first()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(master.mission_type, "arrival")
        self.assertIsNone(master.pure_pull_time_local)
        self.assertIsNone(master.first_mix_pull_time_local)
        self.assertIsNone(master.final_mix_pull_time_local)

    def test_edit_arrival_clears_pull_times_after_type_change(self):
        master = self._add_master(
            flight_number="DEP200",
            pure_pull_time_local=time(1, 20),
            first_mix_pull_time_local=time(1, 40),
            final_mix_pull_time_local=time(1, 55),
        )
        db.session.commit()

        response = self.client.post(
            f"/motherbrain/master-schedule/{master.id}/edit",
            data=self._master_schedule_form_data(
                mission_type="arrival",
                flight_number="DEP200",
                origin="SDF",
                destination="RFD",
                pure_pull_time_local="01:20",
                first_mix_pull_time_local="01:40",
                final_mix_pull_time_local="01:55",
            ),
            follow_redirects=False,
        )

        updated = db.session.get(MasterFlightSchedule, master.id)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(updated.mission_type, "arrival")
        self.assertIsNone(updated.pure_pull_time_local)
        self.assertIsNone(updated.first_mix_pull_time_local)
        self.assertIsNone(updated.final_mix_pull_time_local)

    def test_duplicate_active_master_row_is_rejected(self):
        self._add_master(flight_number="DEP300", active=True)
        db.session.commit()

        response = self.client.post(
            "/motherbrain/master-schedule/new",
            data=self._master_schedule_form_data(flight_number="DEP300"),
        )

        active_duplicates = MasterFlightSchedule.query.filter_by(
            gateway_code="RFD",
            sort_name="night",
            mission_type="departure",
            flight_number="DEP300",
            active=True,
        ).count()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(active_duplicates, 1)
        self.assertIn(b"already exists", response.data)

    def test_inactive_master_row_does_not_generate_operation_mission(self):
        self.client.post(
            "/motherbrain/master-schedule/new",
            data=self._master_schedule_form_data(
                flight_number="DEP400",
                active=False,
            ),
        )

        response = self.client.post(
            "/motherbrain/operations/new",
            data={
                "sort_date": "2026-06-01",
                "gateway_code": "RFD",
                "sort_name": "night",
            },
            follow_redirects=False,
        )

        operation = SortDateOperation.query.first()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(operation.missions), 0)

    def test_active_days_saved_from_form_work_with_generation(self):
        self.client.post(
            "/motherbrain/master-schedule/new",
            data=self._master_schedule_form_data(
                flight_number="DEP500",
                active_days=["monday"],
            ),
        )

        self.client.post(
            "/motherbrain/operations/new",
            data={
                "sort_date": "2026-06-01",
                "gateway_code": "RFD",
                "sort_name": "night",
            },
        )

        operation = SortDateOperation.query.first()
        self.assertEqual(operation.missions[0].flight_number, "DEP500")

    def test_operation_generation_uses_only_current_gateway_master_schedules(self):
        dfw_gateway = self._gateway("DFW", "NeoDFW")
        self._add_master(flight_number="RFDGEN", gateway_id=self.rfd_gateway.id)
        self._add_master(
            flight_number="DFWGEN",
            gateway_id=dfw_gateway.id,
            gateway_code="DFW",
            origin="DFW",
            destination="ONT",
        )
        db.session.commit()

        response = self.client.post(
            "/motherbrain/operations/new",
            data={
                "sort_date": "2026-06-01",
                "gateway_code": "DFW",
                "sort_name": "night",
            },
            follow_redirects=False,
        )

        operation = SortDateOperation.query.filter_by(gateway_code="RFD").first()
        self.assertEqual(response.status_code, 302)
        self.assertIsNotNone(operation)
        self.assertEqual(operation.gateway_id, self.rfd_gateway.id)
        self.assertEqual([mission.flight_number for mission in operation.missions], ["RFDGEN"])
        self.assertEqual(SortDateOperation.query.filter_by(gateway_code="DFW").count(), 0)

    def test_toggle_active_changes_active_state(self):
        master = self._add_master(flight_number="DEP600", active=True)
        db.session.commit()

        response = self.client.post(
            f"/motherbrain/master-schedule/{master.id}/toggle-active",
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(db.session.get(MasterFlightSchedule, master.id).active)

        self.client.post(
            f"/motherbrain/master-schedule/{master.id}/toggle-active",
            follow_redirects=False,
        )
        self.assertTrue(db.session.get(MasterFlightSchedule, master.id).active)

    def test_create_manual_arrival_mission_clears_pull_times(self):
        operation = self._operation()
        db.session.add(operation)
        db.session.commit()

        response = self.client.post(
            f"/motherbrain/operations/{operation.id}/missions/new",
            data=self._mission_form_data(
                mission_type="arrival",
                flight_number="arrman",
                origin="sdf",
                destination="rfd",
                assigned_tail_number="n123up",
                arrival_status="en_route",
                pure_pull_time_local="01:20",
                first_mix_pull_time_local="01:40",
                final_mix_pull_time_local="01:55",
            ),
            follow_redirects=False,
        )

        mission = SortDateMission.query.filter_by(flight_number="ARRMAN").first()
        tail_state = SortDateTailState.query.filter_by(tail_number="N123UP").first()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(mission.mission_source, "manual")
        self.assertEqual(mission.mission_type, "arrival")
        self.assertEqual(mission.flight_number, "ARRMAN")
        self.assertEqual(mission.origin, "SDF")
        self.assertEqual(mission.destination, "RFD")
        self.assertEqual(mission.assigned_tail_number, "N123UP")
        self.assertEqual(mission.arrival_status, "en_route")
        self.assertIsNone(mission.pure_pull_time_local)
        self.assertIsNone(mission.first_mix_pull_time_local)
        self.assertIsNone(mission.final_mix_pull_time_local)
        self.assertIsNone(mission.pull_time_source)
        self.assertEqual(tail_state.aircraft_type, "A300")
        self.assertEqual(tail_state.aircraft_type_source, "derived")

    def test_create_manual_departure_mission_with_pull_times(self):
        operation = self._operation()
        db.session.add(operation)
        db.session.commit()

        response = self.client.post(
            f"/motherbrain/operations/{operation.id}/missions/new",
            data=self._mission_form_data(
                flight_number="depman",
                origin="rfd",
                destination="sdf",
                pure_pull_time_local="01:20",
                first_mix_pull_time_local="01:40",
                final_mix_pull_time_local="01:55",
                departure_status="loading",
            ),
            follow_redirects=False,
        )

        mission = SortDateMission.query.filter_by(flight_number="DEPMAN").first()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(mission.mission_source, "manual")
        self.assertEqual(mission.mission_type, "departure")
        self.assertEqual(mission.flight_number, "DEPMAN")
        self.assertEqual(mission.origin, "RFD")
        self.assertEqual(mission.destination, "SDF")
        self.assertEqual(mission.pure_pull_time_local, time(1, 20))
        self.assertEqual(mission.first_mix_pull_time_local, time(1, 40))
        self.assertEqual(mission.final_mix_pull_time_local, time(1, 55))
        self.assertEqual(mission.pull_time_source, "manual")
        self.assertEqual(mission.departure_status, "loading")

    def test_arrival_mission_form_does_not_render_pull_time_fields(self):
        operation = self._operation()
        db.session.add(operation)
        mission = self._mission(
            operation=operation,
            mission_type="arrival",
            flight_number="ARRFORM",
            origin="SDF",
            destination="RFD",
        )
        db.session.add(mission)
        db.session.commit()

        response = self.client.get(
            f"/motherbrain/operations/{operation.id}/missions/{mission.id}/edit"
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"PURE PULL", response.data)
        self.assertNotIn(b"FIRST MIX PULL", response.data)
        self.assertNotIn(b"FINAL MIX PULL", response.data)
        self.assertNotIn(b"pure_pull_time_local_hour", response.data)

    def test_departure_mission_form_renders_pull_time_fields(self):
        operation = self._operation()
        db.session.add(operation)
        db.session.commit()

        response = self.client.get(f"/motherbrain/operations/{operation.id}/missions/new")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"PURE PULL", response.data)
        self.assertIn(b"FIRST MIX PULL", response.data)
        self.assertIn(b"FINAL MIX PULL", response.data)
        self.assertIn(b"pure_pull_time_local_hour", response.data)

    def test_duplicate_mission_flight_number_is_rejected_inside_operation(self):
        operation = self._operation()
        db.session.add(operation)
        db.session.add(self._mission(operation, "departure", "DUP001"))
        db.session.commit()

        response = self.client.post(
            f"/motherbrain/operations/{operation.id}/missions/new",
            data=self._mission_form_data(flight_number="DUP001"),
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(b"already exists", response.data)
        self.assertEqual(
            SortDateMission.query.filter_by(
                sort_date_operation_id=operation.id,
                flight_number="DUP001",
            ).count(),
            1,
        )

    def test_edit_departure_mission_into_arrival_clears_pull_times(self):
        operation = self._operation()
        db.session.add(operation)
        mission = self._mission(
            operation=operation,
            mission_type="departure",
            flight_number="EDIT001",
            pure_pull_time_local=time(1, 20),
            first_mix_pull_time_local=time(1, 40),
            final_mix_pull_time_local=time(1, 55),
            pull_time_source="manual",
        )
        db.session.add(mission)
        db.session.commit()

        response = self.client.post(
            f"/motherbrain/operations/{operation.id}/missions/{mission.id}/edit",
            data=self._mission_form_data(
                mission_type="arrival",
                flight_number="EDIT001",
                origin="SDF",
                destination="RFD",
                pure_pull_time_local="01:20",
                first_mix_pull_time_local="01:40",
                final_mix_pull_time_local="01:55",
            ),
            follow_redirects=False,
        )

        updated = db.session.get(SortDateMission, mission.id)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(updated.mission_type, "arrival")
        self.assertIsNone(updated.pure_pull_time_local)
        self.assertIsNone(updated.first_mix_pull_time_local)
        self.assertIsNone(updated.final_mix_pull_time_local)
        self.assertIsNone(updated.pull_time_source)

    def test_manual_arrival_appears_on_arrival_board(self):
        operation = self._operation()
        db.session.add(operation)
        db.session.add(self._mission(operation, "arrival", "arrboard", arrival_status="unloaded"))
        db.session.add(self._mission(operation, "departure", "DEPBOARD"))
        db.session.commit()

        response = self.client.get(f"/motherbrain/operations/{operation.id}/arrivals")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"ARRBOARD", response.data)
        self.assertIn(b"Unloaded", response.data)
        self.assertIn(b">STATUS<", response.data)
        self.assertNotIn(b"DEPBOARD", response.data)

    def test_manual_departure_appears_on_departure_board(self):
        operation = self._operation()
        db.session.add(operation)
        db.session.add(self._mission(operation, "arrival", "ARRBOARD"))
        db.session.add(self._mission(operation, "departure", "depboard", departure_status="crew_load_complete"))
        db.session.commit()

        response = self.client.get(f"/motherbrain/operations/{operation.id}/departures")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"DEPBOARD", response.data)
        self.assertNotIn(b"Crew Load Complete", response.data)
        self.assertNotIn(b">STATUS<", response.data)
        self.assertNotIn(b"ARRBOARD", response.data)

    def test_manual_departure_window_adjusted_times_still_display(self):
        operation = self._operation(window_minutes=20)
        db.session.add(operation)
        db.session.add(
            self._mission(
                operation=operation,
                mission_type="departure",
                flight_number="WINMAN",
                planned_datetime_local=datetime(2026, 6, 1, 2, 10),
                pure_pull_time_local=time(1, 20),
                first_mix_pull_time_local=time(1, 40),
                final_mix_pull_time_local=time(1, 55),
            )
        )
        db.session.commit()

        response = self.client.get(f"/motherbrain/operations/{operation.id}/departures")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"02:30", response.data)
        self.assertNotIn(b"02:10", response.data)
        self.assertNotIn(b"02:15", response.data)

    def test_delete_mission_removes_mission_and_crew_assignments(self):
        operation = self._operation()
        db.session.add(operation)
        mission = self._mission(operation, "departure", "DEL001")
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

        response = self.client.post(
            f"/motherbrain/operations/{operation.id}/missions/{mission.id}/delete",
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertIsNone(db.session.get(SortDateMission, mission.id))
        self.assertEqual(
            SortDateCrewAssignment.query.filter_by(
                sort_date_mission_id=mission.id,
            ).count(),
            0,
        )

    def test_tail_state_manual_aircraft_type_is_preserved_on_mission_save(self):
        operation = self._operation()
        db.session.add(operation)
        db.session.add(
            SortDateTailState(
                sort_date=operation.sort_date,
                gateway_code=operation.gateway_code,
                sort_name=operation.sort_name,
                tail_number="N123UP",
                aircraft_type="A330",
                aircraft_type_source="manual",
            )
        )
        db.session.commit()

        response = self.client.post(
            f"/motherbrain/operations/{operation.id}/missions/new",
            data=self._mission_form_data(
                flight_number="TAILMAN",
                assigned_tail_number="N123UP",
            ),
            follow_redirects=False,
        )

        tail_state = SortDateTailState.query.filter_by(tail_number="N123UP").first()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(tail_state.aircraft_type, "A330")
        self.assertEqual(tail_state.aircraft_type_source, "manual")

    def test_tail_state_lookup_is_scoped_by_gateway(self):
        dfw_gateway = self._gateway("DFW", "NeoDFW")
        rfd_operation = self._operation(gateway_id=self.rfd_gateway.id, gateway_code="RFD")
        dfw_operation = self._operation(gateway_id=dfw_gateway.id, gateway_code="DFW")
        db.session.add_all((rfd_operation, dfw_operation))
        db.session.flush()
        db.session.add(
            SortDateTailState(
                sort_date=dfw_operation.sort_date,
                gateway_code="DFW",
                sort_name=dfw_operation.sort_name,
                tail_number="N123UP",
                aircraft_type="A330",
                aircraft_type_source="manual",
            )
        )
        rfd_mission = self._mission(
            rfd_operation,
            "departure",
            "RFDTAL",
            assigned_tail_number="N123UP",
        )
        db.session.add(rfd_mission)
        db.session.flush()

        response = self.client.post(
            f"/motherbrain/operations/{rfd_operation.id}/missions/{rfd_mission.id}/edit",
            data=self._mission_form_data(
                flight_number="RFDTAL",
                assigned_tail_number="N123UP",
            ),
            follow_redirects=False,
        )

        rfd_tail = SortDateTailState.query.filter_by(
            gateway_code="RFD",
            tail_number="N123UP",
        ).first()
        dfw_tail = SortDateTailState.query.filter_by(
            gateway_code="DFW",
            tail_number="N123UP",
        ).first()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(rfd_tail.aircraft_type, "A300")
        self.assertEqual(rfd_tail.aircraft_type_source, "derived")
        self.assertEqual(dfw_tail.aircraft_type, "A330")
        self.assertEqual(dfw_tail.aircraft_type_source, "manual")

    def test_tail_swap_rebuilds_crew_slots_using_existing_rules(self):
        operation = self._operation()
        db.session.add(operation)
        mission = self._mission(
            operation=operation,
            mission_type="departure",
            flight_number="SWAP001",
            assigned_tail_number="N123UP",
        )
        db.session.add(mission)
        db.session.flush()
        for section in ("topside", "front_p", "rear_p", "ab"):
            db.session.add(
                SortDateCrewAssignment(
                    sort_date_mission_id=mission.id,
                    aircraft_section=section,
                    required=True,
                )
            )
        db.session.commit()

        response = self.client.post(
            f"/motherbrain/operations/{operation.id}/missions/{mission.id}/edit",
            data=self._mission_form_data(
                flight_number="SWAP001",
                assigned_tail_number="N456UP",
            ),
            follow_redirects=False,
        )

        sections = sorted(
            assignment.aircraft_section
            for assignment in SortDateCrewAssignment.query.filter_by(
                sort_date_mission_id=mission.id,
            ).all()
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(sections, ["belly_31", "belly_34", "topside"])

    def test_operation_generation_route_creates_operation(self):
        self._add_master(flight_number="ARR001", mission_type="arrival")
        self._add_master(flight_number="DEP001", mission_type="departure")
        db.session.commit()

        response = self.client.post(
            "/motherbrain/operations/new",
            data={
                "sort_date": "2026-06-01",
                "gateway_code": "rfd",
                "sort_name": "night",
            },
            follow_redirects=False,
        )

        operation = SortDateOperation.query.first()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(operation.gateway_code, "RFD")
        self.assertEqual(operation.sort_name, "night")
        self.assertEqual(len(operation.missions), 2)

    def test_duplicate_generation_redirects_to_existing_operation(self):
        self._add_master(flight_number="DEP001")
        db.session.commit()
        self.client.post(
            "/motherbrain/operations/new",
            data={
                "sort_date": "2026-06-01",
                "gateway_code": "RFD",
                "sort_name": "night",
            },
        )

        response = self.client.post(
            "/motherbrain/operations/new",
            data={
                "sort_date": "2026-06-01",
                "gateway_code": "RFD",
                "sort_name": "night",
            },
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn(
            f"/motherbrain/operations/{SortDateOperation.query.first().id}".encode(),
            response.location.encode(),
        )
        self.assertEqual(SortDateOperation.query.count(), 1)

    def test_arrival_board_shows_only_arrival_missions(self):
        operation = self._operation_with_missions()
        db.session.commit()

        response = self.client.get(f"/motherbrain/operations/{operation.id}/arrivals")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"ARR001", response.data)
        self.assertNotIn(b"DEP999", response.data)

    def test_arrival_board_eta_displays_api_eta_as_local_time(self):
        operation = self._operation(sort_date=date(2026, 6, 1))
        settings = ensure_sort_timeline_settings(self.rfd_gateway)
        settings.taxi_to_ramp_minutes = 4
        db.session.add(operation)
        db.session.add(
            self._mission(
                operation=operation,
                mission_type="arrival",
                flight_number="UPS555",
                planned_datetime_local=datetime(2026, 6, 1, 21, 55),
                planned_datetime_utc=datetime(2026, 6, 2, 2, 55),
                eta_datetime_utc=datetime(2026, 6, 2, 3, 8),
                eta_source="api",
                api_status="Scheduled",
                api_status_raw="Expected",
            )
        )
        db.session.commit()

        response = self.client.get(f"/motherbrain/operations/{operation.id}/arrivals")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"UPS555", response.data)
        self.assertIn(b"22:12", response.data)
        self.assertIn(b"Expected", response.data)
        self.assertNotIn(b"03:08", response.data)

    def test_arrival_board_eta_applies_taxi_minutes_to_api_eta_and_delta(self):
        operation = self._operation(sort_date=date(2026, 6, 1))
        settings = ensure_sort_timeline_settings(self.rfd_gateway)
        settings.taxi_to_ramp_minutes = 4
        db.session.add(operation)
        db.session.add(
            self._mission(
                operation=operation,
                mission_type="arrival",
                flight_number="UPS0001",
                planned_datetime_local=datetime(2026, 6, 2, 0, 5),
                planned_datetime_utc=datetime(2026, 6, 2, 5, 5),
                eta_datetime_utc=datetime(2026, 6, 2, 5, 1),
                eta_source="api",
                api_status="Scheduled",
                api_status_raw="Expected",
            )
        )
        db.session.commit()

        response = self.client.get(f"/motherbrain/operations/{operation.id}/arrivals")
        html = response.data.decode()
        row = html.split("UPS0001", 1)[1].split("</tr>", 1)[0]

        self.assertEqual(response.status_code, 200)
        self.assertIn("00:05", row)
        self.assertIn(">0</td>", row)
        self.assertNotIn("00:01", row)
        self.assertNotIn("Est parking", html)

    def test_arrival_board_runway_time_displays_on_ground_parking_estimate(self):
        operation = self._operation(sort_date=date(2026, 6, 1))
        settings = ensure_sort_timeline_settings(self.rfd_gateway)
        settings.taxi_to_ramp_minutes = 4
        db.session.add(operation)
        db.session.add(
            self._mission(
                operation=operation,
                mission_type="arrival",
                flight_number="UPS777",
                planned_datetime_local=datetime(2026, 6, 1, 21, 55),
                planned_datetime_utc=datetime(2026, 6, 2, 2, 55),
                eta_datetime_utc=datetime(2026, 6, 2, 3, 8),
                eta_source="api",
                api_status="On Ground",
                api_status_raw="Expected",
                api_runway_time_utc=datetime(2026, 6, 2, 3, 8),
                api_assumed_arrived_time_utc=datetime(2026, 6, 2, 3, 18),
            )
        )
        db.session.commit()

        with patch(
            "app.neomotherbrain.routes._current_utc_naive",
            return_value=datetime(2026, 6, 2, 3, 10),
        ):
            response = self.client.get(f"/motherbrain/operations/{operation.id}/arrivals")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"UPS777", response.data)
        self.assertIn(b"22:12", response.data)
        self.assertIn(b"On Ground", response.data)
        self.assertNotIn(b"Est parking", response.data)
        self.assertNotIn(b"Actual runway", response.data)
        self.assertNotIn(b"22:18", response.data)
        self.assertNotIn(b"03:12", response.data)

    def test_arrival_board_on_ground_becomes_assumed_arrived_after_adjusted_eta(self):
        operation = self._operation(sort_date=date(2026, 6, 1))
        settings = ensure_sort_timeline_settings(self.rfd_gateway)
        settings.taxi_to_ramp_minutes = 4
        mission = self._mission(
            operation=operation,
            mission_type="arrival",
            flight_number="UPS779",
            planned_datetime_local=datetime(2026, 6, 1, 21, 55),
            planned_datetime_utc=datetime(2026, 6, 2, 2, 55),
            eta_datetime_utc=datetime(2026, 6, 2, 3, 8),
            eta_source="api",
            api_status="On Ground",
            api_status_raw="Expected",
            api_runway_time_utc=datetime(2026, 6, 2, 3, 8),
        )
        db.session.add(operation)
        db.session.add(mission)
        db.session.commit()

        with patch(
            "app.neomotherbrain.routes._current_utc_naive",
            return_value=datetime(2026, 6, 2, 3, 13),
        ):
            response = self.client.get(f"/motherbrain/operations/{operation.id}/arrivals")

        html = response.data.decode()
        row = html.split("UPS779", 1)[1].split("</tr>", 1)[0]
        refreshed = db.session.get(SortDateMission, mission.id)

        self.assertEqual(response.status_code, 200)
        self.assertIn("22:12", row)
        self.assertIn("Assumed Arrived", row)
        self.assertNotIn("On Ground", row)
        self.assertNotEqual(refreshed.arrival_status, "arrived")

    def test_arrival_board_api_arrived_with_runway_still_displays_on_ground_estimate(self):
        operation = self._operation(sort_date=date(2026, 6, 1))
        settings = ensure_sort_timeline_settings(self.rfd_gateway)
        settings.taxi_to_ramp_minutes = 4
        db.session.add(operation)
        db.session.add(
            self._mission(
                operation=operation,
                mission_type="arrival",
                flight_number="UPS888",
                planned_datetime_local=datetime(2026, 6, 1, 21, 55),
                planned_datetime_utc=datetime(2026, 6, 2, 2, 55),
                eta_datetime_utc=datetime(2026, 6, 2, 3, 6),
                eta_source="api",
                api_status="On Ground",
                api_status_raw="Arrived",
                api_runway_time_utc=datetime(2026, 6, 2, 3, 8),
                api_assumed_arrived_time_utc=datetime(2026, 6, 2, 3, 18),
            )
        )
        db.session.commit()

        with patch(
            "app.neomotherbrain.routes._current_utc_naive",
            return_value=datetime(2026, 6, 2, 3, 10),
        ):
            response = self.client.get(f"/motherbrain/operations/{operation.id}/arrivals")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"UPS888", response.data)
        self.assertIn(b"22:12", response.data)
        self.assertIn(b"On Ground", response.data)
        self.assertNotIn(b"Actual runway", response.data)
        self.assertNotIn(b"22:08", response.data)

    def test_arrival_board_manual_arrived_status_overrides_api_runway_time(self):
        operation = self._operation(sort_date=date(2026, 6, 1))
        settings = ensure_sort_timeline_settings(self.rfd_gateway)
        settings.taxi_to_ramp_minutes = 4
        db.session.add(operation)
        db.session.add(
            self._mission(
                operation=operation,
                mission_type="arrival",
                flight_number="UPS889",
                planned_datetime_local=datetime(2026, 6, 1, 21, 55),
                planned_datetime_utc=datetime(2026, 6, 2, 2, 55),
                eta_datetime_utc=datetime(2026, 6, 2, 3, 6),
                eta_source="api",
                actual_block_in_datetime_utc=datetime(2026, 6, 2, 3, 20),
                actual_block_in_source="manual",
                arrival_status="arrived",
                api_status="On Ground",
                api_status_raw="Arrived",
                api_runway_time_utc=datetime(2026, 6, 2, 3, 8),
            )
        )
        db.session.commit()

        response = self.client.get(f"/motherbrain/operations/{operation.id}/arrivals")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"UPS889", response.data)
        self.assertIn(b"22:20", response.data)
        self.assertIn(b"Arrived", response.data)
        self.assertNotIn(b"On Ground", response.data)
        self.assertNotIn(b"22:12", response.data)

    def test_arrival_board_runway_parking_estimate_crosses_midnight_locally(self):
        operation = self._operation(sort_date=date(2026, 6, 1))
        settings = ensure_sort_timeline_settings(self.rfd_gateway)
        settings.taxi_to_ramp_minutes = 4
        db.session.add(operation)
        db.session.add(
            self._mission(
                operation=operation,
                mission_type="arrival",
                flight_number="UPS890",
                planned_datetime_local=datetime(2026, 6, 1, 23, 40),
                planned_datetime_utc=datetime(2026, 6, 2, 4, 40),
                eta_datetime_utc=datetime(2026, 6, 2, 4, 55),
                eta_source="api",
                api_status="On Ground",
                api_status_raw="Expected",
                api_runway_time_utc=datetime(2026, 6, 2, 4, 58),
            )
        )
        db.session.commit()

        with patch(
            "app.neomotherbrain.routes._current_utc_naive",
            return_value=datetime(2026, 6, 2, 5, 0),
        ):
            response = self.client.get(f"/motherbrain/operations/{operation.id}/arrivals")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"UPS890", response.data)
        self.assertIn(b"00:02", response.data)
        self.assertIn(b"On Ground", response.data)
        self.assertNotIn(b"Est parking", response.data)
        self.assertNotIn(b"05:02", response.data)

    def test_arrival_board_eta_falls_back_to_scheduled_local_time(self):
        operation = self._operation(sort_date=date(2026, 6, 1))
        db.session.add(operation)
        db.session.add(
            self._mission(
                operation=operation,
                mission_type="arrival",
                flight_number="UPS1487",
                planned_datetime_local=datetime(2026, 6, 1, 22, 9),
                planned_datetime_utc=datetime(2026, 6, 2, 3, 9),
                eta_datetime_utc=None,
            )
        )
        db.session.commit()

        response = self.client.get(f"/motherbrain/operations/{operation.id}/arrivals")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"UPS1487", response.data)
        self.assertIn(b"22:09", response.data)
        self.assertNotIn(b"03:09", response.data)

    def test_arrival_board_eta_displays_crossing_midnight_local_time(self):
        operation = self._operation(sort_date=date(2026, 6, 1))
        settings = ensure_sort_timeline_settings(self.rfd_gateway)
        settings.taxi_to_ramp_minutes = 4
        db.session.add(operation)
        db.session.add(
            self._mission(
                operation=operation,
                mission_type="arrival",
                flight_number="UPS999",
                planned_datetime_local=datetime(2026, 6, 2, 0, 0),
                planned_datetime_utc=datetime(2026, 6, 2, 5, 0),
                eta_datetime_utc=datetime(2026, 6, 2, 5, 9),
                eta_source="api",
            )
        )
        db.session.commit()

        response = self.client.get(f"/motherbrain/operations/{operation.id}/arrivals")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"UPS999", response.data)
        self.assertIn(b"00:13", response.data)
        self.assertNotIn(b"05:09", response.data)

    def test_arrival_board_columns_start_with_wave_and_show_eta_delta(self):
        operation = self._operation(sort_date=date(2026, 6, 1))
        settings = ensure_sort_timeline_settings(self.rfd_gateway)
        settings.taxi_to_ramp_minutes = 4
        db.session.add(operation)
        db.session.add_all(
            [
                self._mission(
                    operation=operation,
                    mission_type="arrival",
                    flight_number="UPSEARLY",
                    planned_datetime_local=datetime(2026, 6, 1, 22, 0),
                    planned_datetime_utc=datetime(2026, 6, 2, 3, 0),
                    eta_datetime_utc=datetime(2026, 6, 2, 2, 50),
                    eta_source="api",
                ),
                self._mission(
                    operation=operation,
                    mission_type="arrival",
                    flight_number="UPSLATE",
                    planned_datetime_local=datetime(2026, 6, 1, 22, 0),
                    planned_datetime_utc=datetime(2026, 6, 2, 3, 0),
                    eta_datetime_utc=datetime(2026, 6, 2, 3, 12),
                    eta_source="api",
                ),
                self._mission(
                    operation=operation,
                    mission_type="arrival",
                    flight_number="UPSONTIME",
                    planned_datetime_local=datetime(2026, 6, 1, 22, 0),
                    planned_datetime_utc=datetime(2026, 6, 2, 3, 0),
                    eta_datetime_utc=datetime(2026, 6, 2, 2, 56),
                    eta_source="api",
                ),
                self._mission(
                    operation=operation,
                    mission_type="arrival",
                    flight_number="UPSMISSING",
                    planned_datetime_local=datetime(2026, 6, 1, 22, 0),
                    planned_datetime_utc=datetime(2026, 6, 2, 3, 0),
                    eta_datetime_utc=None,
                ),
            ]
        )
        db.session.commit()

        response = self.client.get(f"/motherbrain/operations/{operation.id}/arrivals")
        html = response.data.decode()
        header = html.split("<thead>", 1)[1].split("</thead>", 1)[0]
        header_labels = re.findall(r"<th>(.*?)</th>", header)

        self.assertEqual(
            header_labels,
            [
                "WAVE",
                "FLIGHT",
                "TAIL",
                "ORIGIN",
                "PARKING",
                "ETA",
                "+/-",
                "STATUS",
            ],
        )
        self.assertIn(">-6</td>", html)
        self.assertIn(">+16</td>", html)
        self.assertIn(">0</td>", html)
        self.assertNotIn("Est parking", html)
        self.assertIsNone(re.search(r"\b\d{1,2}:\d{2}\s*(AM|PM)\b", html))
        missing_row = html.split("UPSMISSING", 1)[1].split("</tr>", 1)[0]
        self.assertIn(">-</td>", missing_row)

    def test_arrival_board_eta_delta_handles_midnight_operational_times(self):
        operation = self._operation(sort_date=date(2026, 6, 1))
        settings = ensure_sort_timeline_settings(self.rfd_gateway)
        settings.taxi_to_ramp_minutes = 4
        db.session.add(operation)
        db.session.add_all(
            [
                self._mission(
                    operation=operation,
                    mission_type="arrival",
                    flight_number="UPS0005",
                    planned_datetime_local=datetime(2026, 6, 2, 0, 5),
                    planned_datetime_utc=datetime(2026, 6, 2, 5, 5),
                    eta_datetime_utc=datetime(2026, 6, 2, 5, 1),
                    eta_source="api",
                ),
                self._mission(
                    operation=operation,
                    mission_type="arrival",
                    flight_number="UPS0010",
                    planned_datetime_local=datetime(2026, 6, 2, 0, 5),
                    planned_datetime_utc=datetime(2026, 6, 2, 5, 5),
                    eta_datetime_utc=datetime(2026, 6, 2, 5, 6),
                    eta_source="api",
                ),
                self._mission(
                    operation=operation,
                    mission_type="arrival",
                    flight_number="UPS2355",
                    planned_datetime_local=datetime(2026, 6, 2, 0, 5),
                    planned_datetime_utc=datetime(2026, 6, 2, 5, 5),
                    eta_datetime_utc=datetime(2026, 6, 2, 4, 51),
                    eta_source="api",
                ),
            ]
        )
        db.session.commit()

        response = self.client.get(f"/motherbrain/operations/{operation.id}/arrivals")
        html = response.data.decode()

        self.assertIn("UPS0005", html)
        self.assertIn("UPS0010", html)
        self.assertIn("UPS2355", html)
        self.assertIn("00:05", html)
        self.assertIn("00:10", html)
        self.assertIn("23:55", html)
        on_time_row = html.split("UPS0005", 1)[1].split("</tr>", 1)[0]
        late_row = html.split("UPS0010", 1)[1].split("</tr>", 1)[0]
        early_row = html.split("UPS2355", 1)[1].split("</tr>", 1)[0]
        self.assertIn(">0</td>", on_time_row)
        self.assertIn(">+5</td>", late_row)
        self.assertIn(">-10</td>", early_row)
        self.assertNotIn("+1440", html)
        self.assertIsNone(re.search(r"\b\d{1,2}:\d{2}\s*(AM|PM)\b", html))

    def test_departure_board_shows_only_departure_missions(self):
        operation = self._operation_with_missions()
        db.session.commit()

        response = self.client.get(f"/motherbrain/operations/{operation.id}/departures")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"DEP999", response.data)
        self.assertNotIn(b"ARR001", response.data)

    def test_departure_board_uses_adjusted_window_display_fields(self):
        operation = SortDateOperation(
            sort_date=date(2026, 6, 1),
            gateway_code="RFD",
            sort_name="night",
            window_minutes=20,
        )
        db.session.add(operation)
        db.session.add(
            self._mission(
                operation=operation,
                mission_type="departure",
                flight_number="DEP999",
                planned_datetime_local=datetime(2026, 6, 1, 2, 10),
                pure_pull_time_local=time(1, 20),
                first_mix_pull_time_local=time(1, 40),
                final_mix_pull_time_local=time(1, 55),
            )
        )
        db.session.commit()

        response = self.client.get(f"/motherbrain/operations/{operation.id}/departures")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"02:30", response.data)
        self.assertNotIn(b"02:10", response.data)
        self.assertNotIn(b"01:20", response.data)
        self.assertNotIn(b"01:40", response.data)
        self.assertNotIn(b"02:15", response.data)

    def test_departure_board_columns_use_requested_order_and_omit_status(self):
        operation = self._operation_with_missions()
        db.session.commit()

        response = self.client.get(f"/motherbrain/operations/{operation.id}/departures")
        html = response.data.decode()
        header = html.split("<thead>", 1)[1].split("</thead>", 1)[0]
        header_labels = re.findall(r"<th>(.*?)</th>", header)

        self.assertEqual(
            header_labels,
            [
                "WAVE",
                "FLIGHT",
                "TAIL",
                "DESTINATION",
                "PARKING",
                "STD",
            ],
        )
        self.assertNotIn("<th>STATUS</th>", header)
        self.assertNotIn("<th>+/-</th>", header)
        self.assertIsNone(re.search(r"\b\d{1,2}:\d{2}\s*(AM|PM)\b", html))

    def test_arrival_planning_renders_current_sort_arrival_mission_list(self):
        operation = self._operation(sort_date=date(2026, 6, 24))
        mission = self._mission(
            operation=operation,
            mission_type="arrival",
            flight_number="UPS0910",
            wave="2",
            origin="SDF",
            assigned_tail_number="N910UP",
            planned_datetime_local=datetime(2026, 6, 24, 2, 10),
            planned_datetime_utc=datetime(2026, 6, 24, 7, 10),
            eta_datetime_utc=datetime(2026, 6, 24, 7, 24),
            eta_source="api",
            arrival_status="en_route",
        )
        db.session.add_all([operation, mission])
        db.session.commit()

        response = self.client.get(f"/motherbrain/operations/{operation.id}/alp/arrival")
        html = response.data.decode()
        mission_section = html.split("CURRENT ARRIVAL MISSIONS", 1)[1]

        self.assertEqual(response.status_code, 200)
        self.assertIn("ARRIVAL PLANNING REVIEW", html)
        self.assertIn("CURRENT ARRIVAL MISSIONS", html)
        self.assertIn("UPS0910", mission_section)
        self.assertIn("N910UP", mission_section)
        self.assertIn("SDF", mission_section)
        self.assertIn("02:10", mission_section)
        self.assertIn("02:34", mission_section)
        self.assertIn("En Route", mission_section)
        self.assertIn(
            f"/motherbrain/operations/{operation.id}/missions/{mission.id}/edit",
            mission_section,
        )
        self.assertIn(">TAILSWAP</button>", mission_section)
        self.assertNotIn(
            f"/motherbrain/operations/{operation.id}/missions/{mission.id}/tail-swap",
            mission_section,
        )
        self.assertIn(
            f"/motherbrain/operations/{operation.id}/missions/{mission.id}/cancel",
            mission_section,
        )
        self.assertIn(">CANCEL</button>", mission_section)
        self.assertNotIn(
            f"/motherbrain/operations/{operation.id}/missions/{mission.id}/restore",
            mission_section,
        )
        self.assertNotIn(">RESTORE</button>", mission_section)

    def test_departure_planning_renders_current_sort_departure_mission_list(self):
        operation = self._operation(sort_date=date(2026, 6, 24), window_minutes=20)
        mission = self._mission(
            operation=operation,
            mission_type="departure",
            flight_number="UPS0856",
            wave="1",
            destination="DFW",
            assigned_tail_number="N856UP",
            planned_datetime_local=datetime(2026, 6, 24, 2, 10),
            planned_datetime_utc=datetime(2026, 6, 24, 7, 10),
            departure_status="ramp_load_complete",
        )
        db.session.add_all([operation, mission])
        db.session.commit()

        response = self.client.get(f"/motherbrain/operations/{operation.id}/alp/departure")
        html = response.data.decode()
        mission_section = html.split("CURRENT DEPARTURE MISSIONS", 1)[1]

        self.assertEqual(response.status_code, 200)
        self.assertIn("DEPARTURE PLANNING REVIEW", html)
        self.assertIn("CURRENT DEPARTURE MISSIONS", html)
        self.assertIn("UPS0856", mission_section)
        self.assertIn("N856UP", mission_section)
        self.assertIn("DFW", mission_section)
        self.assertIn("02:30", mission_section)
        self.assertIn("Ramp Load Complete", mission_section)
        self.assertIn(
            f"/motherbrain/operations/{operation.id}/missions/{mission.id}/edit",
            mission_section,
        )
        self.assertIn(">TAILSWAP</button>", mission_section)
        self.assertIn(
            f"/motherbrain/operations/{operation.id}/missions/{mission.id}/tail-swap",
            mission_section,
        )
        self.assertIn('name="replacement_tail"', mission_section)
        self.assertIn('name="confirm_tail_swap"', mission_section)
        self.assertIn(
            f"/motherbrain/operations/{operation.id}/missions/{mission.id}/cancel",
            mission_section,
        )
        self.assertIn(">CANCEL</button>", mission_section)
        self.assertNotIn(
            f"/motherbrain/operations/{operation.id}/missions/{mission.id}/restore",
            mission_section,
        )
        self.assertNotIn(">RESTORE</button>", mission_section)

    def test_planning_mission_rows_hide_edit_for_view_only_user(self):
        operation = self._operation(sort_date=date(2026, 6, 24))
        mission = self._mission(
            operation=operation,
            mission_type="arrival",
            flight_number="UPS0910",
            assigned_tail_number="N910UP",
        )
        db.session.add_all([operation, mission])
        db.session.commit()
        self._login_motherbrain_role("simulator-user", "simulator")

        response = self.client.get(f"/motherbrain/operations/{operation.id}/alp/arrival")
        html = response.data.decode()
        mission_section = html.split("CURRENT ARRIVAL MISSIONS", 1)[1]

        self.assertEqual(response.status_code, 200)
        self.assertIn("UPS0910", mission_section)
        self.assertIn("VIEW ONLY", mission_section)
        self.assertNotIn(
            f"/motherbrain/operations/{operation.id}/missions/{mission.id}/edit",
            mission_section,
        )
        self.assertNotIn(
            f"/motherbrain/operations/{operation.id}/missions/{mission.id}/cancel",
            mission_section,
        )
        self.assertNotIn(
            f"/motherbrain/operations/{operation.id}/missions/{mission.id}/restore",
            mission_section,
        )
        self.assertNotIn(">TAILSWAP</button>", mission_section)
        self.assertNotIn(">CANCEL</button>", mission_section)
        self.assertNotIn(">RESTORE</button>", mission_section)

    def test_arrival_planning_row_shows_assigned_parking_context_link(self):
        operation = self._parking_operation()
        self._parking_pair(operation, "N457UP", destination="LAX")
        db.session.add(
            SortDateParkingAssignment(
                sort_date_operation_id=operation.id,
                tail_number="N457UP",
                ramp_code="A",
                position_code="A03",
                lane_number=1,
            )
        )
        db.session.commit()

        response = self.client.get(f"/motherbrain/operations/{operation.id}/alp/arrival")
        html = response.data.decode()
        mission_section = html.split("CURRENT ARRIVAL MISSIONS", 1)[1]

        self.assertEqual(response.status_code, 200)
        self.assertIn("A03", mission_section)
        self.assertIn("VIEW PARKING", mission_section)
        self.assertIn(f'href="/motherbrain/parking-plan/{operation.id}"', mission_section)
        self.assertNotIn("NOT PARKED", mission_section)

    def test_departure_planning_row_shows_not_parked_for_known_tail(self):
        operation = self._parking_operation()
        self._parking_pair(operation, "N457UP", destination="LAX")
        db.session.commit()

        response = self.client.get(f"/motherbrain/operations/{operation.id}/alp/departure")
        html = response.data.decode()
        mission_section = html.split("CURRENT DEPARTURE MISSIONS", 1)[1]

        self.assertEqual(response.status_code, 200)
        self.assertIn("N457UP", mission_section)
        self.assertIn("NOT PARKED", mission_section)
        self.assertIn("VIEW PARKING", mission_section)
        self.assertIn(f'href="/motherbrain/parking-plan/{operation.id}"', mission_section)

    def test_planning_row_shows_oos_red_badge(self):
        operation = self._parking_operation()
        self._parking_pair(operation, "N457UP", destination="LAX")
        db.session.flush()
        tail_state = SortDateTailState.query.filter_by(tail_number="N457UP").one()
        tail_state.is_out_of_service = True
        db.session.add(
            SortDateParkingAssignment(
                sort_date_operation_id=operation.id,
                tail_number="N457UP",
                ramp_code="R",
                position_code="R01",
                lane_number=1,
            )
        )
        db.session.commit()

        response = self.client.get(f"/motherbrain/operations/{operation.id}/alp/arrival")
        html = response.data.decode()
        mission_section = html.split("CURRENT ARRIVAL MISSIONS", 1)[1]

        self.assertEqual(response.status_code, 200)
        self.assertIn("R01", mission_section)
        self.assertIn("OOS / RED", mission_section)

    def test_cancelled_planning_row_still_shows_parking_context(self):
        operation = self._parking_operation()
        arrival, _departure = self._parking_pair(operation, "N457UP", destination="LAX")
        arrival.arrival_status = "cancelled"
        db.session.add(
            SortDateParkingAssignment(
                sort_date_operation_id=operation.id,
                tail_number="N457UP",
                ramp_code="A",
                position_code="A03",
                lane_number=1,
            )
        )
        db.session.commit()

        response = self.client.get(f"/motherbrain/operations/{operation.id}/alp/arrival")
        html = response.data.decode()
        mission_section = html.split("CURRENT ARRIVAL MISSIONS", 1)[1]

        self.assertEqual(response.status_code, 200)
        self.assertIn("N457UP", mission_section)
        self.assertIn("A03", mission_section)
        self.assertIn("MISSION CANCELLED", mission_section)
        self.assertIn("VIEW PARKING", mission_section)

    def test_cancel_arrival_marks_mission_cancelled_and_keeps_planning_row(self):
        operation = self._operation(sort_date=date(2026, 6, 24))
        mission = self._mission(
            operation=operation,
            mission_type="arrival",
            flight_number="UPS0910",
            assigned_tail_number="N910UP",
            arrival_status="en_route",
        )
        db.session.add_all([operation, mission])
        db.session.commit()

        response = self.client.post(
            f"/motherbrain/operations/{operation.id}/missions/{mission.id}/cancel",
            follow_redirects=True,
        )
        db.session.refresh(mission)
        repeated = self.client.post(
            f"/motherbrain/operations/{operation.id}/missions/{mission.id}/cancel",
            follow_redirects=True,
        )
        board = self.client.get(f"/motherbrain/operations/{operation.id}/arrivals")
        planning_html = repeated.data.decode()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(mission.arrival_status, "cancelled")
        self.assertIn("UPS0910", planning_html)
        self.assertIn("CANCELLED", planning_html)
        self.assertIn(">RESTORE</button>", planning_html)
        self.assertNotIn(">CANCEL</button>", planning_html)
        self.assertNotIn("UPS0910", board.data.decode())

    def test_restore_arrival_returns_mission_to_active_board(self):
        operation = self._operation(sort_date=date(2026, 6, 24))
        mission = self._mission(
            operation=operation,
            mission_type="arrival",
            flight_number="UPS0910",
            assigned_tail_number="N910UP",
            arrival_status="en_route",
        )
        db.session.add_all([operation, mission])
        db.session.commit()
        self.client.post(
            f"/motherbrain/operations/{operation.id}/missions/{mission.id}/cancel",
            follow_redirects=True,
        )

        response = self.client.post(
            f"/motherbrain/operations/{operation.id}/missions/{mission.id}/restore",
            follow_redirects=True,
        )
        db.session.refresh(mission)
        repeated = self.client.post(
            f"/motherbrain/operations/{operation.id}/missions/{mission.id}/restore",
            follow_redirects=True,
        )
        board = self.client.get(f"/motherbrain/operations/{operation.id}/arrivals")
        planning_html = repeated.data.decode()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(repeated.status_code, 200)
        self.assertIsNone(mission.arrival_status)
        self.assertIn("UPS0910", planning_html)
        self.assertNotIn("CANCELLED", planning_html)
        self.assertIn(">CANCEL</button>", planning_html)
        self.assertNotIn(">RESTORE</button>", planning_html)
        self.assertIn("UPS0910", board.data.decode())

    def test_cancel_departure_marks_mission_cancelled_and_keeps_planning_row(self):
        operation = self._operation(sort_date=date(2026, 6, 24))
        mission = self._mission(
            operation=operation,
            mission_type="departure",
            flight_number="UPS0856",
            assigned_tail_number="N856UP",
            departure_status="loading",
        )
        db.session.add_all([operation, mission])
        db.session.commit()

        response = self.client.post(
            f"/motherbrain/operations/{operation.id}/missions/{mission.id}/cancel",
            follow_redirects=True,
        )
        db.session.refresh(mission)
        repeated = self.client.post(
            f"/motherbrain/operations/{operation.id}/missions/{mission.id}/cancel",
            follow_redirects=True,
        )
        board = self.client.get(f"/motherbrain/operations/{operation.id}/departures")
        planning_html = repeated.data.decode()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(mission.departure_status, "cancelled")
        self.assertIn("UPS0856", planning_html)
        self.assertIn("CANCELLED", planning_html)
        self.assertIn(">RESTORE</button>", planning_html)
        self.assertNotIn(">CANCEL</button>", planning_html)
        self.assertNotIn("UPS0856", board.data.decode())

    def test_restore_departure_returns_mission_to_active_board(self):
        operation = self._operation(sort_date=date(2026, 6, 24))
        mission = self._mission(
            operation=operation,
            mission_type="departure",
            flight_number="UPS0856",
            assigned_tail_number="N856UP",
            departure_status="loading",
        )
        db.session.add_all([operation, mission])
        db.session.commit()
        self.client.post(
            f"/motherbrain/operations/{operation.id}/missions/{mission.id}/cancel",
            follow_redirects=True,
        )

        response = self.client.post(
            f"/motherbrain/operations/{operation.id}/missions/{mission.id}/restore",
            follow_redirects=True,
        )
        db.session.refresh(mission)
        repeated = self.client.post(
            f"/motherbrain/operations/{operation.id}/missions/{mission.id}/restore",
            follow_redirects=True,
        )
        board = self.client.get(f"/motherbrain/operations/{operation.id}/departures")
        planning_html = repeated.data.decode()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(repeated.status_code, 200)
        self.assertIsNone(mission.departure_status)
        self.assertIn("UPS0856", planning_html)
        self.assertNotIn("CANCELLED", planning_html)
        self.assertIn(">CANCEL</button>", planning_html)
        self.assertNotIn(">RESTORE</button>", planning_html)
        self.assertIn("UPS0856", board.data.decode())

    def test_tail_swap_updates_selected_departure_tail(self):
        operation = self._operation(sort_date=date(2026, 6, 24))
        mission = self._mission(
            operation=operation,
            mission_type="departure",
            flight_number="UPS0856",
            assigned_tail_number="N111UP",
        )
        db.session.add_all([operation, mission])
        db.session.commit()

        response = self.client.post(
            f"/motherbrain/operations/{operation.id}/missions/{mission.id}/tail-swap",
            data={"replacement_tail": "N222UP"},
            follow_redirects=True,
        )
        db.session.refresh(mission)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mission.assigned_tail_number, "N222UP")
        self.assertEqual(mission.tail_source, "manual")
        self.assertIn("Tail Swap complete.", response.data.decode())

    def test_tail_swap_respects_current_sort_scope(self):
        operation = self._operation(sort_date=date(2026, 6, 24))
        other_operation = self._operation(sort_date=date(2026, 6, 25))
        mission = self._mission(
            operation=operation,
            mission_type="departure",
            flight_number="UPS0856",
            assigned_tail_number="N111UP",
        )
        other_mission = self._mission(
            operation=other_operation,
            mission_type="departure",
            flight_number="UPS0910",
            assigned_tail_number="N222UP",
        )
        db.session.add_all([operation, other_operation, mission, other_mission])
        db.session.commit()

        response = self.client.post(
            f"/motherbrain/operations/{operation.id}/missions/{mission.id}/tail-swap",
            data={"replacement_tail": "N222UP"},
            follow_redirects=True,
        )
        db.session.refresh(mission)
        db.session.refresh(other_mission)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mission.assigned_tail_number, "N222UP")
        self.assertNotEqual(other_mission.departure_status, "cancelled")

    def test_tail_swap_requires_confirmation_before_cancelling_chained_departure(self):
        operation = self._operation(sort_date=date(2026, 6, 24))
        selected = self._mission(
            operation=operation,
            mission_type="departure",
            flight_number="UPS0856",
            assigned_tail_number="N111UP",
        )
        chained = self._mission(
            operation=operation,
            mission_type="departure",
            flight_number="UPS0910",
            assigned_tail_number="N222UP",
        )
        db.session.add_all([operation, selected, chained])
        db.session.commit()

        blocked = self.client.post(
            f"/motherbrain/operations/{operation.id}/missions/{selected.id}/tail-swap",
            data={"replacement_tail": "N222UP"},
            follow_redirects=True,
        )
        db.session.refresh(selected)
        db.session.refresh(chained)

        self.assertEqual(blocked.status_code, 200)
        self.assertEqual(selected.assigned_tail_number, "N111UP")
        self.assertNotEqual(chained.departure_status, "cancelled")
        self.assertIn("Check CONFIRM", blocked.data.decode())

        confirmed = self.client.post(
            f"/motherbrain/operations/{operation.id}/missions/{selected.id}/tail-swap",
            data={"replacement_tail": "N222UP", "confirm_tail_swap": "1"},
            follow_redirects=True,
        )
        db.session.refresh(selected)
        db.session.refresh(chained)

        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(selected.assigned_tail_number, "N222UP")
        self.assertEqual(chained.departure_status, "cancelled")
        self.assertIsNotNone(db.session.get(SortDateMission, chained.id))
        self.assertIn("cancelled chained departure UPS0910", confirmed.data.decode())

    def test_tail_swap_hot_duplicate_conflict_cancels_hot_departure(self):
        operation = self._operation(sort_date=date(2026, 6, 24))
        selected = self._mission(
            operation=operation,
            mission_type="departure",
            flight_number="UPS0856",
            assigned_tail_number="N111UP",
        )
        hot_duplicate = self._mission(
            operation=operation,
            mission_type="departure",
            flight_number="UPS0999",
            assigned_tail_number="N222UP",
        )
        db.session.add_all([operation, selected, hot_duplicate])
        db.session.flush()
        db.session.add(
            SortDateParkingAssignment(
                sort_date_operation_id=operation.id,
                tail_number="N222UP",
                ramp_code="R",
                position_code="R01",
                lane_number=1,
                is_hot=True,
            )
        )
        db.session.commit()

        response = self.client.post(
            f"/motherbrain/operations/{operation.id}/missions/{selected.id}/tail-swap",
            data={"replacement_tail": "N222UP", "confirm_tail_swap": "1"},
            follow_redirects=True,
        )
        db.session.refresh(selected)
        db.session.refresh(hot_duplicate)
        active_n222_departures = [
            mission
            for mission in SortDateMission.query.filter_by(
                sort_date_operation_id=operation.id,
                mission_type="departure",
                assigned_tail_number="N222UP",
            ).all()
            if mission.departure_status != "cancelled"
        ]

        self.assertEqual(response.status_code, 200)
        self.assertEqual(selected.assigned_tail_number, "N222UP")
        self.assertEqual(hot_duplicate.departure_status, "cancelled")
        self.assertEqual(active_n222_departures, [selected])

    def test_view_only_user_cannot_cancel_mission_by_post(self):
        operation = self._operation(sort_date=date(2026, 6, 24))
        mission = self._mission(
            operation=operation,
            mission_type="arrival",
            flight_number="UPS0910",
            arrival_status="en_route",
        )
        db.session.add_all([operation, mission])
        db.session.commit()
        self._login_motherbrain_role("simulator-cancel-user", "simulator")

        response = self.client.post(
            f"/motherbrain/operations/{operation.id}/missions/{mission.id}/cancel",
            follow_redirects=True,
        )
        db.session.refresh(mission)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mission.arrival_status, "en_route")
        self.assertIn("Access denied.", response.data.decode())

    def test_view_only_user_cannot_restore_mission_by_post(self):
        operation = self._operation(sort_date=date(2026, 6, 24))
        mission = self._mission(
            operation=operation,
            mission_type="arrival",
            flight_number="UPS0910",
            arrival_status="cancelled",
        )
        db.session.add_all([operation, mission])
        db.session.commit()
        self._login_motherbrain_role("simulator-restore-user", "simulator")

        response = self.client.post(
            f"/motherbrain/operations/{operation.id}/missions/{mission.id}/restore",
            follow_redirects=True,
        )
        db.session.refresh(mission)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mission.arrival_status, "cancelled")
        self.assertIn("Access denied.", response.data.decode())

    def test_alp_apply_does_not_reactivate_cancelled_arrival(self):
        operation = self._operation(sort_date=date(2026, 6, 24))
        mission = self._mission(
            operation=operation,
            mission_type="arrival",
            flight_number="UPS0910",
            assigned_tail_number="NOLDUP",
            arrival_status="cancelled",
        )
        db.session.add_all([operation, mission])
        db.session.commit()

        response = self.client.post(
            f"/motherbrain/operations/{operation.id}/alp/arrival",
            data={
                "paste_text": "24-JUN-2026\tUPS910\tSDF\tN910UP\tA01\tArrived\t07:24 (A)",
                "alp_action": "apply",
            },
            follow_redirects=True,
        )
        db.session.refresh(mission)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mission.arrival_status, "cancelled")
        self.assertEqual(mission.assigned_tail_number, "N910UP")
        self.assertIn("CANCELLED", response.data.decode())

    def test_arrival_board_rows_are_data_view_only(self):
        operation = self._operation_with_missions()
        db.session.commit()

        response = self.client.get(f"/motherbrain/operations/{operation.id}/arrivals")
        html = response.data.decode()
        body = html.split("<tbody>", 1)[1].split("</tbody>", 1)[0]

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(">VIEW</a>", body)
        self.assertNotIn(">EDIT</a>", body)
        self.assertNotIn("/missions/", body)

    def test_departure_board_rows_are_data_view_only(self):
        operation = self._operation_with_missions()
        db.session.commit()

        response = self.client.get(f"/motherbrain/operations/{operation.id}/departures")
        html = response.data.decode()
        body = html.split("<tbody>", 1)[1].split("</tbody>", 1)[0]

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(">VIEW</a>", body)
        self.assertNotIn(">EDIT</a>", body)
        self.assertNotIn("/missions/", body)

    def test_alp_arrival_paste_preview_matches_and_converts_zulu_times(self):
        operation = self._operation(sort_date=date(2026, 6, 24))
        db.session.add(operation)
        db.session.add_all(
            [
                self._mission(
                    operation=operation,
                    mission_type="arrival",
                    flight_number="UPS0910",
                    planned_datetime_local=datetime(2026, 6, 23, 22, 0),
                    planned_datetime_utc=datetime(2026, 6, 24, 3, 0),
                ),
                self._mission(
                    operation=operation,
                    mission_type="arrival",
                    flight_number="UPS0612",
                    planned_datetime_local=datetime(2026, 6, 23, 22, 11),
                    planned_datetime_utc=datetime(2026, 6, 24, 3, 11),
                ),
            ]
        )
        db.session.commit()
        paste = "\n".join(
            [
                "24-JUN-2026\tUPS910\tSDF\tN910UP\tA01\tScheduled\t07:24 (S)",
                "24-JUN-2026\t5X 612\tSDF\tN612UP\tA02\tArrived\t03:11 (A)",
            ]
        )

        response = self.client.post(
            f"/motherbrain/operations/{operation.id}/alp/arrival",
            data={"paste_text": paste, "alp_action": "preview"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"ARRIVAL PLANNING", response.data)
        self.assertIn(b"UPS0910", response.data)
        self.assertIn(b"N910UP", response.data)
        self.assertIn(b"02:24 Local Jun 24", response.data)
        self.assertIn(b"UPS0612", response.data)
        self.assertIn(b"22:11 Local Jun 23", response.data)
        self.assertIn(b"- -&gt; N612UP", response.data)

    def test_alp_departure_paste_apply_updates_selected_operation_only(self):
        operation = self._operation(sort_date=date(2026, 6, 24))
        other_operation = self._operation(sort_date=date(2026, 6, 25))
        db.session.add_all([operation, other_operation])
        selected_mission = self._mission(
            operation=operation,
            mission_type="departure",
            flight_number="UPS0910",
            planned_datetime_local=datetime(2026, 6, 24, 2, 10),
            planned_datetime_utc=datetime(2026, 6, 24, 7, 10),
        )
        other_mission = self._mission(
            operation=other_operation,
            mission_type="departure",
            flight_number="UPS0910",
            planned_datetime_local=datetime(2026, 6, 25, 2, 10),
            planned_datetime_utc=datetime(2026, 6, 25, 7, 10),
        )
        db.session.add_all([selected_mission, other_mission])
        db.session.commit()

        response = self.client.post(
            f"/motherbrain/operations/{operation.id}/alp/departure",
            data={
                "paste_text": "24-JUN-2026\t5X 910\tSDF\tN910UP\tA01\tScheduled\t07:24 (A)",
                "alp_action": "apply",
            },
        )

        db.session.refresh(selected_mission)
        db.session.refresh(other_mission)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(selected_mission.assigned_tail_number, "N910UP")
        self.assertEqual(selected_mission.tail_source, "alp")
        self.assertEqual(
            selected_mission.actual_block_out_datetime_utc,
            datetime(2026, 6, 24, 7, 24),
        )
        self.assertEqual(selected_mission.actual_block_out_source, "alp")
        self.assertIsNone(other_mission.assigned_tail_number)

    def test_alp_arrival_paste_apply_does_not_mutate_master_schedule(self):
        operation = self._operation(sort_date=date(2026, 6, 24))
        db.session.add(operation)
        mission = self._mission(
            operation=operation,
            mission_type="arrival",
            flight_number="UPS0948",
            assigned_tail_number="NOLDUP",
            tail_source="api",
        )
        master = MasterFlightSchedule(
            gateway_id=self.rfd_gateway.id,
            gateway_code="RFD",
            sort_name="night",
            mission_type="arrival",
            wave="1",
            flight_number="UPS0948",
            origin="SDF",
            destination="RFD",
            active_days="wednesday",
            planned_time_local=time(2, 10),
            timezone="America/Chicago",
            preferred_parking="A01",
            active=True,
        )
        db.session.add_all([mission, master])
        db.session.commit()

        response = self.client.post(
            f"/motherbrain/operations/{operation.id}/alp/arrival",
            data={
                "paste_text": "24-JUN-2026\tUPS948\tSDF\tN948UP\tA99\tIgnored\t07:24 (S)",
                "alp_action": "apply",
            },
        )

        db.session.refresh(mission)
        db.session.refresh(master)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(mission.assigned_tail_number, "N948UP")
        self.assertEqual(mission.tail_source, "alp")
        self.assertEqual(mission.eta_datetime_utc, datetime(2026, 6, 24, 7, 24))
        self.assertEqual(mission.eta_source, "alp")
        self.assertEqual(master.preferred_parking, "A01")
        self.assertEqual(MasterFlightSchedule.query.count(), 1)
        self.assertIn(b"API tail differs; ALP will replace API tail.", response.data)

    def test_alp_paste_preview_reports_duplicate_invalid_unmatched_and_missing_rows(self):
        operation = self._operation(sort_date=date(2026, 6, 24))
        db.session.add(operation)
        db.session.add_all(
            [
                self._mission(
                    operation=operation,
                    mission_type="arrival",
                    flight_number="UPS0910",
                ),
                self._mission(
                    operation=operation,
                    mission_type="arrival",
                    flight_number="UPS0856",
                ),
            ]
        )
        db.session.commit()
        paste = "\n".join(
            [
                "24-JUN-2026\tUPS910\tSDF\tN910UP\tA01\tScheduled\t07:24 (S)",
                "24-JUN-2026\t5X 910\tSDF\tN911UP\tA02\tScheduled\t07:25 (S)",
                "24-JUN-2026\tUPS999\tSDF\tN999UP\tA03\tScheduled\t07:26 (S)",
                "BROKEN ROW",
            ]
        )

        response = self.client.post(
            f"/motherbrain/operations/{operation.id}/alp/arrival",
            data={"paste_text": paste, "alp_action": "preview"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"DUPLICATE ROWS", response.data)
        self.assertIn(b"UPS0910", response.data)
        self.assertIn(b"N911UP", response.data)
        self.assertIn(b"ARRIVAL PLANNING REVIEW", response.data)
        self.assertIn(b"UPS0999", response.data)
        self.assertIn(b"INVALID ROWS", response.data)
        self.assertIn(b"Expected 7 ALP columns.", response.data)
        self.assertIn(b"CURRENT OPERATION FLIGHTS MISSING FROM PASTE", response.data)
        self.assertIn(b"UPS0856", response.data)

    def test_alp_paste_links_remain_available_on_desktop_and_marked_desktop_only(self):
        operation = self._operation(sort_date=date(2026, 6, 24))
        db.session.add(operation)
        db.session.commit()

        pages = [
            f"/motherbrain/operations/{operation.id}",
            f"/motherbrain/operations/{operation.id}/arrivals",
            f"/motherbrain/operations/{operation.id}/departures",
        ]

        for page in pages:
            with self.subTest(page=page):
                response = self.client.get(page)
                html = response.data.decode()

                self.assertEqual(response.status_code, 200)
                self.assertIn("alp-desktop-only", html)
                self.assertIn("/alp/", html)

    def test_alp_import_page_has_mobile_desktop_only_message_and_desktop_form(self):
        operation = self._operation(sort_date=date(2026, 6, 24))
        db.session.add(operation)
        db.session.commit()

        response = self.client.get(f"/motherbrain/operations/{operation.id}/alp/arrival")
        html = response.data.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn("ALP paste entry is desktop-only.", html)
        self.assertIn("ARRIVAL PLANNING", html)
        self.assertIn("alp-mobile-only", html)
        self.assertIn("alp-desktop-only", html)
        self.assertIn('textarea name="paste_text"', html)
        self.assertIn('name="alp_action" value="preview"', html)

    def test_alp_mobile_css_hides_import_links_and_controls_without_overflow(self):
        css = Path("app/static/css/base.css").read_text()

        self.assertIn(".alp-mobile-only", css)
        self.assertIn(".alp-desktop-only", css)
        self.assertIn("@media (max-width: 760px)", css)
        mobile_css = css.split("@media (max-width: 760px)", 1)[1]
        self.assertIn(".alp-desktop-only", mobile_css)
        self.assertIn("display: none !important;", mobile_css)
        self.assertIn(".alp-mobile-only", mobile_css)
        self.assertIn("display: block;", mobile_css)

    def test_planning_labels_replace_alp_paste_buttons_but_old_route_works(self):
        operation = self._operation(sort_date=date(2026, 6, 24))
        db.session.add(operation)
        db.session.commit()

        detail = self.client.get(f"/motherbrain/operations/{operation.id}").data.decode()
        arrivals = self.client.get(f"/motherbrain/operations/{operation.id}/arrivals").data.decode()
        departures = self.client.get(f"/motherbrain/operations/{operation.id}/departures").data.decode()
        old_route = self.client.get(f"/motherbrain/operations/{operation.id}/alp/arrival")

        self.assertEqual(old_route.status_code, 200)
        self.assertIn("ARRIVAL PLANNING", detail)
        self.assertIn("DEPARTURE PLANNING", detail)
        self.assertIn("ARRIVAL PLANNING", arrivals)
        self.assertIn("DEPARTURE PLANNING", departures)
        self.assertNotIn("ALP ARRIVAL PASTE", detail)
        self.assertNotIn("ALP DEPARTURE PASTE", detail)

    def test_arrival_planning_renders_alp_and_api_unmatched_rows(self):
        operation = self._operation(sort_date=date(2026, 6, 24))
        db.session.add(operation)
        db.session.add(
            self._api_review_item(
                operation,
                mission_type="arrival",
                flight_number="UPS0856",
                origin="DFW",
                tail_number="N856UP",
                revised_time_utc=datetime(2026, 6, 24, 7, 56),
            )
        )
        db.session.commit()

        response = self.client.post(
            f"/motherbrain/operations/{operation.id}/alp/arrival",
            data={
                "paste_text": "24-JUN-2026\tUPS999\tSDF\tN999UP\tA01\tScheduled\t07:24 (S)",
                "alp_action": "preview",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"ARRIVAL PLANNING REVIEW", response.data)
        self.assertIn(b"ALP", response.data)
        self.assertIn(b"UPS0999", response.data)
        self.assertIn(b"API", response.data)
        self.assertIn(b"UPS0856", response.data)
        self.assertIn(b"ADD TO CURRENT SORT", response.data)
        self.assertIn(b">HOT</button>", response.data)
        self.assertIn(b"IGNORE", response.data)

    def test_alp_unmatched_preview_persists_in_planning_review(self):
        operation = self._operation(sort_date=date(2026, 6, 24))
        db.session.add(operation)
        db.session.commit()

        preview = self.client.post(
            f"/motherbrain/operations/{operation.id}/alp/arrival",
            data={
                "paste_text": "24-JUN-2026\tUPS999\tSDF\tN999UP\tA01\tScheduled\t07:24 (S)",
                "alp_action": "preview",
            },
        )
        persisted = self.client.get(f"/motherbrain/operations/{operation.id}/alp/arrival")

        marker = FlightApiReviewItem.query.filter_by(review_status="pending").one()
        payload = json.loads(marker.raw_payload)
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(marker.review_key, "alp:arrival:0999:SDF:N999UP:202606240724")
        self.assertEqual(payload["source"], "ALP")
        self.assertEqual(payload["reason"], "No current operation mission match.")
        self.assertIn(b"ARRIVAL PLANNING REVIEW", persisted.data)
        self.assertIn(b"ALP", persisted.data)
        self.assertIn(b"LINE 1", persisted.data)
        self.assertIn(b"UPS0999", persisted.data)
        self.assertIn(b"N999UP", persisted.data)
        self.assertIn(b"02:24 Local Jun 24", persisted.data)
        self.assertIn(b"ADD TO CURRENT SORT", persisted.data)
        self.assertIn(b">HOT</button>", persisted.data)
        self.assertIn(b"IGNORE", persisted.data)

    def test_alp_planning_mismatch_detail_shows_current_and_alp_flights(self):
        operation = self._operation(sort_date=date(2026, 6, 24))
        mission = self._mission(
            operation=operation,
            mission_type="arrival",
            flight_number="UPS1234",
            origin="SDF",
            planned_datetime_local=datetime(2026, 6, 24, 2, 30),
            planned_datetime_utc=datetime(2026, 6, 24, 7, 30),
        )
        db.session.add_all([operation, mission])
        db.session.commit()

        self.client.post(
            f"/motherbrain/operations/{operation.id}/alp/arrival",
            data={
                "paste_text": "24-JUN-2026\tUPS5678\tSDF\tN567UP\tA01\tScheduled\t07:24 (S)",
                "alp_action": "preview",
            },
        )
        persisted = self.client.get(f"/motherbrain/operations/{operation.id}/alp/arrival")

        marker = FlightApiReviewItem.query.filter_by(review_status="pending").one()
        payload = json.loads(marker.raw_payload)
        self.assertEqual(payload["reason"], "No current operation mission match.")
        self.assertEqual(
            payload["reason_detail"],
            "Current flight: UPS1234 / ALP flight: UPS5678",
        )
        self.assertIn(b"Current flight: UPS1234 / ALP flight: UPS5678", persisted.data)

    def test_alp_unmatched_apply_persists_in_planning_review(self):
        operation = self._operation(sort_date=date(2026, 6, 24))
        mission = self._mission(
            operation=operation,
            mission_type="arrival",
            flight_number="UPS0910",
            origin="SDF",
            planned_datetime_local=datetime(2026, 6, 24, 2, 34),
            planned_datetime_utc=datetime(2026, 6, 24, 7, 34),
        )
        db.session.add_all([operation, mission])
        db.session.commit()

        response = self.client.post(
            f"/motherbrain/operations/{operation.id}/alp/arrival",
            data={
                "paste_text": "\n".join(
                    [
                        "24-JUN-2026\tUPS910\tSDF\tN910UP\tA01\tScheduled\t07:24 (S)",
                        "24-JUN-2026\tUPS999\tDFW\tN999UP\tA02\tScheduled\t07:56 (S)",
                    ]
                ),
                "alp_action": "apply",
            },
        )
        persisted = self.client.get(f"/motherbrain/operations/{operation.id}/alp/arrival")

        db.session.refresh(mission)
        marker = FlightApiReviewItem.query.filter_by(review_status="pending").one()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(mission.assigned_tail_number, "N910UP")
        self.assertEqual(mission.tail_source, "alp")
        self.assertEqual(marker.review_key, "alp:arrival:0999:DFW:N999UP:202606240756")
        self.assertIn(b"UPS0999", persisted.data)
        self.assertIn(b"N999UP", persisted.data)
        self.assertIn(b"02:56 Local Jun 24", persisted.data)

    def test_add_persisted_alp_planning_row_creates_mission_and_removes_row(self):
        operation = self._operation(sort_date=date(2026, 6, 24))
        db.session.add(operation)
        db.session.commit()
        self.client.post(
            f"/motherbrain/operations/{operation.id}/alp/arrival",
            data={
                "paste_text": "24-JUN-2026\tUPS999\tSDF\tN999UP\tA01\tScheduled\t07:24 (S)",
                "alp_action": "preview",
            },
        )

        response = self.client.post(
            f"/motherbrain/operations/{operation.id}/planning/arrival/alp/add",
            data={
                "line_number": "1",
                "flight_number": "UPS0999",
                "airport": "SDF",
                "tail_number": "N999UP",
                "utc_datetime": "2026-06-24T07:24:00",
                "reason": "No current operation mission match.",
            },
            follow_redirects=False,
        )
        persisted = self.client.get(f"/motherbrain/operations/{operation.id}/alp/arrival")

        mission = SortDateMission.query.filter_by(flight_number="UPS0999").one()
        marker = FlightApiReviewItem.query.filter_by(review_status="accepted").one()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(mission.sort_date_operation_id, operation.id)
        self.assertEqual(marker.accepted_mission_id, mission.id)
        self.assertEqual(FlightApiReviewItem.query.filter_by(review_status="pending").count(), 0)
        self.assertIn(b"NO UNMATCHED ARRIVAL PLANNING ROWS.", persisted.data)
        self.assertNotIn(b"ADD TO CURRENT SORT</button>", persisted.data)

    def test_hot_persisted_alp_planning_row_marks_hot_and_removes_row(self):
        operation = self._operation(sort_date=date(2026, 6, 24))
        db.session.add(operation)
        db.session.commit()
        self.client.post(
            f"/motherbrain/operations/{operation.id}/alp/departure",
            data={
                "paste_text": "24-JUN-2026\tUPS999\tSDF\tN999UP\tA01\tScheduled\t07:24 (S)",
                "alp_action": "preview",
            },
        )

        response = self.client.post(
            f"/motherbrain/operations/{operation.id}/planning/departure/alp/hot",
            data={
                "line_number": "1",
                "flight_number": "UPS0999",
                "airport": "SDF",
                "tail_number": "N999UP",
                "utc_datetime": "2026-06-24T07:24:00",
                "reason": "No current operation mission match.",
            },
            follow_redirects=False,
        )
        persisted = self.client.get(f"/motherbrain/operations/{operation.id}/alp/departure")

        mission = SortDateMission.query.filter_by(flight_number="UPS0999").one()
        marker = FlightApiReviewItem.query.filter_by(review_status="accepted").one()
        hot_state = SortDateParkingAssignment.query.filter_by(
            sort_date_operation_id=operation.id,
            tail_number="N999UP",
        ).one()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(mission.mission_type, "departure")
        self.assertEqual(marker.accepted_mission_id, mission.id)
        self.assertTrue(hot_state.is_hot)
        self.assertEqual(FlightApiReviewItem.query.filter_by(review_status="pending").count(), 0)
        self.assertIn(b"NO UNMATCHED DEPARTURE PLANNING ROWS.", persisted.data)
        self.assertNotIn(b"ADD TO CURRENT SORT</button>", persisted.data)

    def test_ignore_persisted_alp_planning_row_removes_row_for_current_operation(self):
        operation = self._operation(sort_date=date(2026, 6, 24))
        db.session.add(operation)
        db.session.commit()
        self.client.post(
            f"/motherbrain/operations/{operation.id}/alp/arrival",
            data={
                "paste_text": "24-JUN-2026\tUPS999\tSDF\tN999UP\tA01\tScheduled\t07:24 (S)",
                "alp_action": "preview",
            },
        )

        response = self.client.post(
            f"/motherbrain/operations/{operation.id}/planning/arrival/alp/ignore",
            data={
                "line_number": "1",
                "flight_number": "UPS0999",
                "airport": "SDF",
                "tail_number": "N999UP",
                "utc_datetime": "2026-06-24T07:24:00",
                "reason": "No current operation mission match.",
            },
            follow_redirects=False,
        )
        persisted = self.client.get(f"/motherbrain/operations/{operation.id}/alp/arrival")

        marker = FlightApiReviewItem.query.filter_by(review_status="ignored").one()
        self.assertEqual(response.status_code, 302)
        self.assertIn("alp:arrival:0999:SDF:N999UP:202606240724", marker.review_key)
        self.assertEqual(SortDateMission.query.count(), 0)
        self.assertEqual(FlightApiReviewItem.query.filter_by(review_status="pending").count(), 0)
        self.assertIn(b"NO UNMATCHED ARRIVAL PLANNING ROWS.", persisted.data)
        self.assertNotIn(b"ADD TO CURRENT SORT</button>", persisted.data)

    def test_duplicate_alp_add_reuses_existing_current_sort_mission(self):
        operation = self._operation(sort_date=date(2026, 6, 24))
        mission = self._mission(
            operation=operation,
            mission_type="arrival",
            flight_number="UPS0999",
            origin="SDF",
        )
        db.session.add_all([operation, mission])
        db.session.flush()
        db.session.add(
            FlightApiReviewItem(
                sort_date_operation_id=operation.id,
                gateway_id=operation.gateway_id,
                gateway_code=operation.gateway_code,
                sort_date=operation.sort_date,
                sort_name=operation.sort_name,
                mission_type="arrival",
                review_key="alp:arrival:0999:SDF:N999UP:202606240724",
                review_status="pending",
                flight_number="UPS0999",
                origin="SDF",
                destination="RFD",
                revised_time_utc=datetime(2026, 6, 24, 7, 24),
                tail_number="N999UP",
                api_status="ALP",
                raw_payload=json.dumps(
                    {
                        "source": "ALP",
                        "line_number": "1",
                        "reason": "No current operation mission match.",
                    }
                ),
            )
        )
        db.session.commit()

        response = self.client.post(
            f"/motherbrain/operations/{operation.id}/planning/arrival/alp/add",
            data={
                "line_number": "1",
                "flight_number": "UPS0999",
                "airport": "SDF",
                "tail_number": "N999UP",
                "utc_datetime": "2026-06-24T07:24:00",
                "reason": "No current operation mission match.",
            },
            follow_redirects=False,
        )

        marker = FlightApiReviewItem.query.filter_by(review_status="accepted").one()
        db.session.refresh(mission)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(SortDateMission.query.filter_by(flight_number="UPS0999").count(), 1)
        self.assertEqual(marker.accepted_mission_id, mission.id)
        self.assertEqual(mission.assigned_tail_number, "N999UP")
        self.assertEqual(mission.tail_source, "alp")

    def test_departure_planning_renders_alp_and_api_unmatched_rows_with_hot(self):
        operation = self._operation(sort_date=date(2026, 6, 24))
        db.session.add(operation)
        db.session.add(
            self._api_review_item(
                operation,
                mission_type="departure",
                flight_number="UPS0856",
                destination="DFW",
                tail_number="N856UP",
                revised_time_utc=datetime(2026, 6, 24, 7, 56),
            )
        )
        db.session.commit()

        response = self.client.post(
            f"/motherbrain/operations/{operation.id}/alp/departure",
            data={
                "paste_text": "24-JUN-2026\tUPS999\tSDF\tN999UP\tA01\tScheduled\t07:24 (S)",
                "alp_action": "preview",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"DEPARTURE PLANNING REVIEW", response.data)
        self.assertIn(b"ALP", response.data)
        self.assertIn(b"UPS0999", response.data)
        self.assertIn(b"API", response.data)
        self.assertIn(b"UPS0856", response.data)
        self.assertIn(b"ADD TO CURRENT SORT", response.data)
        self.assertIn(b">HOT</button>", response.data)
        self.assertIn(b"IGNORE", response.data)

    def test_api_planning_time_mismatch_detail_shows_current_and_api_times(self):
        operation = self._operation(sort_date=date(2026, 6, 24))
        mission = self._mission(
            operation=operation,
            mission_type="departure",
            flight_number="UPS0910",
            destination="SDF",
            planned_datetime_local=datetime(2026, 6, 24, 4, 10),
            planned_datetime_utc=datetime(2026, 6, 24, 9, 10),
        )
        db.session.add_all([operation, mission])
        db.session.flush()
        item = self._api_review_item(
            operation,
            mission_type="departure",
            flight_number="UPS0910",
            destination="SDF",
            revised_time_utc=datetime(2026, 6, 24, 18, 25),
        )
        db.session.add(item)
        db.session.commit()

        response = self.client.get(f"/motherbrain/operations/{operation.id}/alp/departure")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"departure time mismatch", response.data)
        self.assertIn(b"Current STD: 04:10 / API STD: 13:25", response.data)

    def test_api_planning_destination_mismatch_detail_shows_both_destinations(self):
        operation = self._operation(sort_date=date(2026, 6, 24))
        mission = self._mission(
            operation=operation,
            mission_type="departure",
            flight_number="UPS0910",
            destination="SDF",
        )
        db.session.add_all([operation, mission])
        db.session.flush()
        item = self._api_review_item(
            operation,
            mission_type="departure",
            flight_number="UPS0910",
            destination="ONT",
        )
        db.session.add(item)
        db.session.commit()

        response = self.client.get(f"/motherbrain/operations/{operation.id}/alp/departure")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"destination mismatch", response.data)
        self.assertIn(b"Current destination: SDF / API destination: ONT", response.data)

    def test_planning_view_only_user_cannot_use_unmatched_actions(self):
        operation = self._operation(sort_date=date(2026, 6, 24))
        db.session.add(operation)
        item = self._api_review_item(
            operation,
            mission_type="arrival",
            flight_number="UPS0856",
            origin="DFW",
            tail_number="N856UP",
        )
        db.session.add(item)
        db.session.commit()
        self.client.post(
            f"/motherbrain/operations/{operation.id}/alp/arrival",
            data={
                "paste_text": "24-JUN-2026\tUPS999\tSDF\tN999UP\tA01\tScheduled\t07:24 (S)",
                "alp_action": "preview",
            },
        )
        self._login_motherbrain_role("simulator-user", "simulator")

        response = self.client.get(f"/motherbrain/operations/{operation.id}/alp/arrival")
        blocked = self.client.post(
            f"/motherbrain/operations/{operation.id}/planning/api/{item.id}/add",
            data={"mission_type": "arrival"},
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"VIEW ONLY", response.data)
        self.assertIn(b"UPS0999", response.data)
        self.assertNotIn(b"ADD TO CURRENT SORT", response.data)
        self.assertNotIn(b">HOT</button>", response.data)
        self.assertNotIn(b"IGNORE</button>", response.data)
        self.assertIn(b"Access denied.", blocked.data)
        self.assertEqual(SortDateMission.query.count(), 0)

    def test_add_alp_arrival_planning_row_creates_current_sort_only_mission(self):
        operation = self._operation(sort_date=date(2026, 6, 24))
        db.session.add(operation)
        db.session.commit()

        response = self.client.post(
            f"/motherbrain/operations/{operation.id}/planning/arrival/alp/add",
            data={
                "line_number": "1",
                "flight_number": "UPS999",
                "airport": "SDF",
                "tail_number": "N999UP",
                "utc_datetime": "2026-06-24T07:24:00",
                "reason": "No current operation mission match.",
            },
            follow_redirects=False,
        )

        mission = SortDateMission.query.filter_by(flight_number="UPS0999").one()
        marker = FlightApiReviewItem.query.filter_by(review_status="accepted").one()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(mission.sort_date_operation_id, operation.id)
        self.assertEqual(mission.mission_type, "arrival")
        self.assertEqual(mission.origin, "SDF")
        self.assertEqual(mission.destination, "RFD")
        self.assertEqual(mission.assigned_tail_number, "N999UP")
        self.assertEqual(mission.tail_source, "alp")
        self.assertEqual(mission.eta_datetime_utc, datetime(2026, 6, 24, 7, 24))
        self.assertTrue(mission.api_added_current_sort_only)
        self.assertIsNone(mission.master_flight_schedule_id)
        self.assertEqual(marker.accepted_mission_id, mission.id)
        self.assertEqual(SortDateParkingAssignment.query.count(), 0)

    def test_add_api_departure_planning_row_creates_current_sort_only_mission(self):
        operation = self._operation(sort_date=date(2026, 6, 24))
        db.session.add(operation)
        item = self._api_review_item(
            operation,
            mission_type="departure",
            flight_number="UPS0856",
            destination="DFW",
            tail_number="N856UP",
            revised_time_utc=datetime(2026, 6, 24, 7, 56),
        )
        db.session.add(item)
        db.session.commit()

        response = self.client.post(
            f"/motherbrain/operations/{operation.id}/planning/api/{item.id}/add",
            data={"mission_type": "departure"},
            follow_redirects=False,
        )

        mission = SortDateMission.query.filter_by(flight_number="UPS0856").one()
        db.session.refresh(item)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(mission.sort_date_operation_id, operation.id)
        self.assertEqual(mission.mission_type, "departure")
        self.assertEqual(mission.destination, "DFW")
        self.assertEqual(mission.assigned_tail_number, "N856UP")
        self.assertTrue(mission.api_added_current_sort_only)
        self.assertIsNone(mission.master_flight_schedule_id)
        self.assertEqual(item.review_status, "accepted")
        self.assertEqual(item.accepted_mission_id, mission.id)

    def test_hot_departure_planning_row_marks_tail_hot(self):
        operation = self._operation(sort_date=date(2026, 6, 24))
        db.session.add(operation)
        db.session.commit()

        response = self.client.post(
            f"/motherbrain/operations/{operation.id}/planning/departure/alp/hot",
            data={
                "line_number": "1",
                "flight_number": "UPS999",
                "airport": "SDF",
                "tail_number": "N999UP",
                "utc_datetime": "2026-06-24T07:24:00",
                "reason": "No current operation mission match.",
            },
            follow_redirects=False,
        )

        mission = SortDateMission.query.filter_by(flight_number="UPS0999").one()
        hot_state = SortDateParkingAssignment.query.filter_by(
            sort_date_operation_id=operation.id,
            tail_number="N999UP",
        ).one()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(mission.mission_type, "departure")
        self.assertTrue(hot_state.is_hot)

    def test_ignore_alp_planning_row_hides_same_row_for_operation(self):
        operation = self._operation(sort_date=date(2026, 6, 24))
        db.session.add(operation)
        db.session.commit()
        form = {
            "line_number": "1",
            "flight_number": "UPS999",
            "airport": "SDF",
            "tail_number": "N999UP",
            "utc_datetime": "2026-06-24T07:24:00",
            "reason": "No current operation mission match.",
        }

        ignore = self.client.post(
            f"/motherbrain/operations/{operation.id}/planning/arrival/alp/ignore",
            data=form,
            follow_redirects=False,
        )
        response = self.client.post(
            f"/motherbrain/operations/{operation.id}/alp/arrival",
            data={
                "paste_text": "24-JUN-2026\tUPS999\tSDF\tN999UP\tA01\tScheduled\t07:24 (S)",
                "alp_action": "preview",
            },
        )

        marker = FlightApiReviewItem.query.filter_by(review_status="ignored").one()
        self.assertEqual(ignore.status_code, 302)
        self.assertIn("alp:arrival:0999:SDF:N999UP:202606240724", marker.review_key)
        self.assertNotIn(b"LINE 1", response.data)
        self.assertNotIn(b"ADD TO CURRENT SORT", response.data)

    def test_window_update_rejects_negative_values(self):
        operation = self._operation()
        db.session.add(operation)
        db.session.commit()

        response = self.client.post(
            f"/motherbrain/operations/{operation.id}/window",
            data={"window_minutes": "-1"},
            follow_redirects=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Window minutes must be 0 or higher.", response.data)
        self.assertEqual(db.session.get(SortDateOperation, operation.id).window_minutes, 0)

    def test_window_update_accepts_zero_or_positive_values(self):
        operation = self._operation()
        db.session.add(operation)
        db.session.commit()

        response = self.client.post(
            f"/motherbrain/operations/{operation.id}/window",
            data={"window_minutes": "25"},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(db.session.get(SortDateOperation, operation.id).window_minutes, 25)

        self.client.post(
            f"/motherbrain/operations/{operation.id}/window",
            data={"window_minutes": "0"},
        )
        self.assertEqual(db.session.get(SortDateOperation, operation.id).window_minutes, 0)

    def test_window_update_accepts_wave_specific_overrides(self):
        operation = self._operation(window_minutes=20)
        db.session.add(operation)
        db.session.commit()

        response = self.client.post(
            f"/motherbrain/operations/{operation.id}/window",
            data={
                "window_minutes": "20",
                "first_wave_window_minutes": "5",
                "second_wave_window_minutes": "35",
            },
            follow_redirects=False,
        )

        updated = db.session.get(SortDateOperation, operation.id)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(updated.window_minutes, 20)
        self.assertEqual(updated.first_wave_window_minutes, 5)
        self.assertEqual(updated.second_wave_window_minutes, 35)

    def _add_master(self, **overrides):
        values = {
            "gateway_code": "RFD",
            "sort_name": "night",
            "mission_type": "departure",
            "wave": "1",
            "flight_number": "DEP001",
            "aircraft_type": "",
            "origin": "RFD",
            "destination": "SDF",
            "active": True,
            "active_days": "monday,tuesday",
            "planned_time_local": time(2, 10),
        }
        if overrides.get("mission_type") == "arrival":
            values["origin"] = "SDF"
            values["destination"] = "RFD"
        values.update(overrides)
        master = MasterFlightSchedule(**values)
        db.session.add(master)
        return master

    def _gateway(self, code, name):
        gateway = Gateway.query.filter_by(code=code).first()
        if gateway:
            return gateway

        gateway = Gateway(code=code, name=name, is_active=True)
        db.session.add(gateway)
        db.session.flush()
        return gateway

    def _add_matrix_cell(self, day_of_week, sort_name, gateway=None, is_active=True):
        gateway = gateway or self.rfd_gateway
        matrix_cell = GatewaySortMatrix(
            gateway_id=gateway.id,
            gateway_code=gateway.code,
            day_of_week=day_of_week,
            sort_name=sort_name,
            is_active=is_active,
        )
        db.session.add(matrix_cell)
        return matrix_cell

    def _add_matrix_days(self, sort_name, days, gateway=None):
        return [
            self._add_matrix_cell(day, sort_name, gateway=gateway)
            for day in days
        ]

    def test_parking_plan_landing_lists_planned_sort_before_active_window(self):
        operation = self._parking_operation(now=datetime(2026, 6, 18, 10, 0))
        self._parking_pair(operation, "N457UP", destination="LAX")
        db.session.commit()

        response = self.client.get("/motherbrain/parking-plan")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"PARKING PLAN", response.data)
        self.assertIn(b"SELECT A PLANNED OR CURRENT SORT OPERATION", response.data)
        self.assertIn(b"PLANNED", response.data)
        self.assertIn(b"NIGHT", response.data)
        self.assertIn(b"SORT DATE 2026-06-18", response.data)
        self.assertIn(f'href="/motherbrain/parking-plan/{operation.id}"'.encode(), response.data)
        self.assertNotIn(b"TAIL CHECKLIST", response.data)
        self.assertNotIn(b"NO CURRENT SORT OPERATION", response.data)

    def test_parking_plan_selected_operation_renders_board_and_checklist(self):
        operation = self._parking_operation()
        self._parking_pair(operation, "N457UP", destination="LAX")
        db.session.commit()

        response = self.client.get(f"/motherbrain/parking-plan/{operation.id}")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"PARKING PLAN", response.data)
        self.assertIn(b"motherbrain-parking-plan-page", response.data)
        self.assertIn(b"TAIL CHECKLIST", response.data)
        self.assertIn(b"N457UP", response.data)
        self.assertIn(b"R01", response.data)
        self.assertIn(b"A01", response.data)
        self.assertIn(b"E10", response.data)
        self.assertEqual(response.data.count(b"data-lane-number"), 108)
        self.assertIn(b"data-slot-number", response.data)
        self.assertIn(b"SLOT 1", response.data)
        self.assertIn(b"data-slot-expand", response.data)
        self.assertIn(b"Show A01 Slot 2", response.data)
        self.assertNotIn(b"LANE 1", response.data)
        self.assertNotIn(b"LANE 2", response.data)
        self.assertIn(b"data-parking-tail", response.data)
        self.assertIn(b"data-parking-tail-assigned=\"0\"", response.data)
        self.assertIn(b"data-parking-selection-status", response.data)
        self.assertIn(b"data-parking-unassign-drop", response.data)
        self.assertIn(f'data-unassign-url="/motherbrain/parking-plan/{operation.id}/unassign"'.encode(), response.data)
        self.assertIn(b"parking-mobile-assignment", response.data)
        self.assertIn(b'href="/motherbrain/parking-plan"', response.data)
        self.assertIn(
            f'href="/motherbrain/parking-rules?operation_id={operation.id}"'.encode(),
            response.data,
        )
        self.assertIn(b"PARKING RULES", response.data)
        self.assertIn(f'action="/motherbrain/parking-plan/{operation.id}/assign"'.encode(), response.data)

    def test_motherbrain_alert_tray_renders_empty_state_on_key_pages(self):
        operation = self._parking_operation()
        self._parking_pair(operation, "N457UP", destination="LAX")
        db.session.commit()

        pages = [
            "/motherbrain",
            "/motherbrain/manage-sort",
            f"/motherbrain/operations/{operation.id}/alp/arrival",
            f"/motherbrain/operations/{operation.id}/alp/departure",
            f"/motherbrain/operations/{operation.id}/arrivals",
            f"/motherbrain/operations/{operation.id}/departures",
            f"/motherbrain/parking-plan/{operation.id}",
            f"/motherbrain/parking-rules?operation_id={operation.id}",
        ]

        for path in pages:
            with self.subTest(path=path):
                response = self.client.get(path)

                self.assertEqual(response.status_code, 200)
                self.assertIn(b'data-motherbrain-alert-tray', response.data)
                self.assertIn(b"MotherBrain Alerts", response.data)
                self.assertIn(b"No active MotherBrain alerts", response.data)

    def test_motherbrain_alert_tray_does_not_render_outside_motherbrain_pages(self):
        response = self.client.get("/rfd")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b'data-motherbrain-alert-tray', response.data)
        self.assertNotIn(b"MotherBrain Alerts", response.data)

    def test_motherbrain_alert_tray_renders_active_alert_rows(self):
        db.session.add(
            MotherBrainAlert(
                gateway_id=self.rfd_gateway.id,
                gateway_code=self.rfd_gateway.code,
                severity="warning",
                title="Parking plan needs review",
                message="One tail needs conflict review.",
                related_url="/motherbrain/parking-plan",
                related_label="VIEW PARKING PLAN",
            )
        )
        db.session.commit()

        response = self.client.get("/motherbrain")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Parking plan needs review", response.data)
        self.assertIn(b"One tail needs conflict review.", response.data)
        self.assertIn(b"WARNING", response.data)
        self.assertIn(b"VIEW PARKING PLAN", response.data)
        self.assertNotIn(b"No active MotherBrain alerts", response.data)

    def test_motherbrain_restricted_alerts_respect_future_permission_key(self):
        from app.services.motherbrain_alerts import motherbrain_alert_context

        db.session.add(
            MotherBrainAlert(
                gateway_id=self.rfd_gateway.id,
                gateway_code=self.rfd_gateway.code,
                severity="critical",
                title="Restricted parking conflict",
                message="Future parking conflict detail.",
                permission_key="motherbrain.parking_conflicts.view",
            )
        )
        db.session.commit()

        denied = motherbrain_alert_context(
            self.rfd_gateway,
            can_view_permission=lambda _permission_key: False,
        )
        allowed = motherbrain_alert_context(
            self.rfd_gateway,
            can_view_permission=lambda _permission_key: True,
        )

        self.assertFalse(denied["has_alerts"])
        self.assertEqual(denied["count"], 0)
        self.assertTrue(allowed["has_alerts"])
        self.assertEqual(allowed["count"], 1)
        self.assertEqual(allowed["alerts"][0].title, "Restricted parking conflict")

    def test_parking_rules_page_renders_and_persists_settings(self):
        operation = self._parking_operation()
        db.session.commit()

        response = self.client.get(f"/motherbrain/parking-rules?operation_id={operation.id}")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"PARKING RULES", response.data)
        self.assertIn(b"ORIGIN RAMP RULES", response.data)
        self.assertIn(b"AIRCRAFT TYPE RESTRICTIONS", response.data)
        self.assertIn(b"AIRCRAFT TYPE PREFERENCES", response.data)
        self.assertIn(b"OPTIMIZER DEFAULTS", response.data)
        self.assertIn(b"PHYSICAL PARKING RULES", response.data)
        self.assertIn(
            b"Restrictions are hard rules. The optimizer will never assign this aircraft type to the selected ramp.",
            response.data,
        )
        self.assertIn(b"A restriction row means NOT ALLOWED, not allowed.", response.data)
        self.assertIn(
            b"Preferences are soft rules. The optimizer tries to follow them when possible, but hard rules win.",
            response.data,
        )
        self.assertIn(b'<select name="new_aircraft_type_ramp_restriction_subject"', response.data)
        self.assertIn(b'<select name="new_aircraft_type_ramp_preference_subject"', response.data)
        self.assertIn(b'<option value="A300"', response.data)
        self.assertIn(b'<option value="747"', response.data)
        self.assertIn(b'<option value="757"', response.data)
        self.assertIn(b'<option value="767"', response.data)
        self.assertIn(b"09/10 throat parking is optional, with 10 filled before 9.", response.data)
        self.assertIn(f'href="/motherbrain/parking-plan/{operation.id}"'.encode(), response.data)

        save_response = self.client.post(
            f"/motherbrain/parking-rules?operation_id={operation.id}",
            data={
                "operation_id": str(operation.id),
                "include_remote_default": "1",
                "deice_spacing_threshold_minutes": "22",
                "new_origin_ramp_restriction_subject": "ont",
                "new_origin_ramp_restriction_ramp": "A",
                "new_origin_ramp_preference_subject": "sdf",
                "new_origin_ramp_preference_ramp": "B",
                "new_aircraft_type_ramp_restriction_subject": "767",
                "new_aircraft_type_ramp_restriction_ramp": "R",
                "new_aircraft_type_ramp_preference_subject": "a300",
                "new_aircraft_type_ramp_preference_ramp": "THROAT",
            },
        )

        self.assertEqual(save_response.status_code, 302)
        settings = MotherBrainParkingSettings.query.filter_by(
            gateway_id=self.rfd_gateway.id,
        ).one()
        self.assertTrue(settings.include_remote_default)
        self.assertFalse(settings.include_throat_default)
        self.assertEqual(settings.deice_spacing_threshold_minutes, 22)
        rules = {
            (
                rule.rule_category,
                rule.subject_type,
                rule.subject_value,
                rule.ramp_code,
                rule.rule_behavior,
            )
            for rule in MotherBrainParkingRule.query.filter_by(
                gateway_id=self.rfd_gateway.id,
            ).all()
        }
        self.assertIn(
            ("origin_ramp_restriction", "origin", "ONT", "A", "forbidden"),
            rules,
        )
        self.assertIn(
            ("origin_ramp_preference", "origin", "SDF", "B", "preferred"),
            rules,
        )
        self.assertIn(
            ("aircraft_type_ramp_restriction", "aircraft_type", "767", "R", "forbidden"),
            rules,
        )
        self.assertIn(
            ("aircraft_type_ramp_preference", "aircraft_type", "A300", "THROAT", "preferred"),
            rules,
        )

        reload_response = self.client.get(f"/motherbrain/parking-rules?operation_id={operation.id}")
        self.assertIn(b"value=\"ONT\"", reload_response.data)
        self.assertIn(b"value=\"767\"", reload_response.data)
        self.assertIn(b"value=\"22\"", reload_response.data)

    def test_parking_aircraft_type_resolver_maps_tail_digits(self):
        cases = {
            "N123UP": "A300",
            "N312UP": "767",
            "N412UP": "757",
            "N512UP": "747",
            "N612UP": "747",
            "N912UP": "767",
            "TAIL": "UNKNOWN",
            "N712UP": "UNKNOWN",
            "": "UNKNOWN",
        }

        for tail_number, expected_type in cases.items():
            with self.subTest(tail_number=tail_number):
                self.assertEqual(
                    resolve_parking_aircraft_type_from_tail(tail_number),
                    expected_type,
                )

    def test_parking_rules_view_permission_is_enforced(self):
        ensure_default_permission_rules()
        view_rule = PermissionRule.query.filter_by(
            permission_key="motherbrain.parking_rules.view",
        ).one()
        view_rule.minimum_role = "master"
        db.session.commit()
        self._login_motherbrain_role("ParkingRulesSimulator", "simulator")

        response = self.client.get("/motherbrain/parking-rules")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/rfd", response.headers["Location"])

    def test_parking_rules_edit_permission_is_enforced(self):
        ensure_default_permission_rules()
        view_rule = PermissionRule.query.filter_by(
            permission_key="motherbrain.parking_rules.view",
        ).one()
        edit_rule = PermissionRule.query.filter_by(
            permission_key="motherbrain.parking_rules.edit",
        ).one()
        view_rule.minimum_role = "simulator"
        edit_rule.minimum_role = "master"
        db.session.commit()
        self._login_motherbrain_role("ParkingRulesViewOnly", "simulator")

        get_response = self.client.get("/motherbrain/parking-rules")
        self.assertEqual(get_response.status_code, 200)
        self.assertIn(b"VIEW ONLY", get_response.data)
        self.assertNotIn(b"SAVE PARKING RULES", get_response.data)

        post_response = self.client.post(
            "/motherbrain/parking-rules",
            data={
                "include_remote_default": "1",
                "new_origin_ramp_restriction_subject": "ONT",
                "new_origin_ramp_restriction_ramp": "A",
            },
        )

        self.assertEqual(post_response.status_code, 302)
        self.assertEqual(MotherBrainParkingRule.query.count(), 0)

    def test_parking_optimizer_permission_keys_render_in_permission_matrix(self):
        ensure_default_permission_rules()

        response = self.client.get("/admin/permissions")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"motherbrain.parking_rules.view", response.data)
        self.assertIn(b"motherbrain.parking_rules.edit", response.data)
        self.assertIn(b"motherbrain.parking_optimizer.run", response.data)
        self.assertIn(b"motherbrain.parking_optimizer.apply", response.data)
        self.assertIn(b"motherbrain.parking_conflicts.view", response.data)

    def test_parking_tail_visibility_is_not_removed_by_mission_cancellation(self):
        operation = self._parking_operation()
        arrival, departure = self._parking_pair(operation, "N457UP", destination="LAX")
        db.session.commit()
        self.client.post(
            f"/motherbrain/parking-plan/{operation.id}/assign",
            data={
                "tail_number": "N457UP",
                "ramp_code": "A",
                "position_code": "A01",
                "lane_number": "1",
            },
        )
        self.client.post(
            f"/motherbrain/operations/{operation.id}/missions/{arrival.id}/cancel",
            follow_redirects=True,
        )
        self.client.post(
            f"/motherbrain/operations/{operation.id}/missions/{departure.id}/cancel",
            follow_redirects=True,
        )

        response = self.client.get(f"/motherbrain/parking-plan/{operation.id}")
        html = response.data.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn("N457UP", html)
        self.assertIn('data-occupied-tail="N457UP"', html)
        self.assertIn("MISSION CANCELLED", html)
        self.assertIn("NO ACTIVE MISSION", html)
        self.assertIn("TOTAL 1", html)
        self.assertIn("ASSIGNED 1", html)
        self.assertIn("UNASSIGNED 0", html)

    def test_parking_tail_oos_action_marks_red_and_keeps_assignment_and_mission_active(self):
        operation = self._parking_operation()
        arrival, _departure = self._parking_pair(operation, "N457UP", destination="LAX")
        db.session.commit()
        self.client.post(
            f"/motherbrain/parking-plan/{operation.id}/assign",
            data={
                "tail_number": "N457UP",
                "ramp_code": "A",
                "position_code": "A01",
                "lane_number": "1",
            },
        )

        response = self.client.post(
            f"/motherbrain/parking-plan/{operation.id}/tail-status",
            data={"tail_number": "N457UP", "is_out_of_service": "1"},
            follow_redirects=True,
        )
        db.session.refresh(arrival)
        tail_state = SortDateTailState.query.filter_by(tail_number="N457UP").one()
        assignment = SortDateParkingAssignment.query.filter_by(tail_number="N457UP").one()
        html = response.data.decode()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(tail_state.is_out_of_service)
        self.assertNotEqual(arrival.arrival_status, "cancelled")
        self.assertEqual(assignment.position_code, "A01")
        self.assertEqual(assignment.lane_number, 1)
        self.assertIn("parking-tail-card is-oos", html)
        self.assertIn("parking-badge parking-badge-oos", html)
        self.assertIn("OOS / RED", html)
        self.assertIn("RESTORE / GREEN", html)
        self.assertIn('data-occupied-tail="N457UP"', html)
        status_html = html.split('class="parking-status-panel', 1)[1].split(
            'class="parking-layout"',
            1,
        )[0]
        self.assertIn("OOS / RED TAILS WITH ACTIVE MISSIONS", status_html)
        self.assertIn("N457UP", status_html)
        self.assertIn("A01-1", status_html)
        self.assertIn("ARR57", status_html)
        self.assertIn("DEP57", status_html)

    def test_parking_tail_restore_green_does_not_restore_cancelled_mission(self):
        operation = self._parking_operation()
        arrival, _departure = self._parking_pair(operation, "N457UP", destination="LAX")
        db.session.commit()
        self.client.post(
            f"/motherbrain/parking-plan/{operation.id}/assign",
            data={
                "tail_number": "N457UP",
                "ramp_code": "A",
                "position_code": "A01",
                "lane_number": "1",
            },
        )
        self.client.post(
            f"/motherbrain/parking-plan/{operation.id}/tail-status",
            data={"tail_number": "N457UP", "is_out_of_service": "1"},
        )
        self.client.post(
            f"/motherbrain/operations/{operation.id}/missions/{arrival.id}/cancel",
            follow_redirects=True,
        )

        response = self.client.post(
            f"/motherbrain/parking-plan/{operation.id}/tail-status",
            data={"tail_number": "N457UP", "is_out_of_service": "0"},
            follow_redirects=True,
        )
        db.session.refresh(arrival)
        tail_state = SortDateTailState.query.filter_by(tail_number="N457UP").one()
        assignment = SortDateParkingAssignment.query.filter_by(tail_number="N457UP").one()
        html = response.data.decode()

        self.assertEqual(response.status_code, 200)
        self.assertFalse(tail_state.is_out_of_service)
        self.assertEqual(arrival.arrival_status, "cancelled")
        self.assertEqual(assignment.position_code, "A01")
        self.assertIn("RED / OOS", html)
        self.assertIn('data-occupied-tail="N457UP"', html)

    def test_mission_cancel_and_restore_do_not_change_parking_tail_oos_status(self):
        operation = self._parking_operation()
        arrival, _departure = self._parking_pair(operation, "N457UP", destination="LAX")
        db.session.commit()
        self.client.post(
            f"/motherbrain/parking-plan/{operation.id}/assign",
            data={
                "tail_number": "N457UP",
                "ramp_code": "A",
                "position_code": "A01",
                "lane_number": "1",
            },
        )
        self.client.post(
            f"/motherbrain/parking-plan/{operation.id}/tail-status",
            data={"tail_number": "N457UP", "is_out_of_service": "1"},
        )

        self.client.post(
            f"/motherbrain/operations/{operation.id}/missions/{arrival.id}/cancel",
            follow_redirects=True,
        )
        after_cancel = SortDateTailState.query.filter_by(tail_number="N457UP").one()
        self.client.post(
            f"/motherbrain/operations/{operation.id}/missions/{arrival.id}/restore",
            follow_redirects=True,
        )
        db.session.refresh(after_cancel)
        assignment = SortDateParkingAssignment.query.filter_by(tail_number="N457UP").one()
        response = self.client.get(f"/motherbrain/parking-plan/{operation.id}")
        html = response.data.decode()

        self.assertTrue(after_cancel.is_out_of_service)
        self.assertEqual(assignment.position_code, "A01")
        self.assertEqual(assignment.lane_number, 1)
        self.assertIn("parking-tail-card is-oos", html)
        self.assertIn('data-occupied-tail="N457UP"', html)

    def test_parking_plan_unattached_tail_label_renders_for_parked_tail_without_mission(self):
        operation = self._parking_operation()
        db.session.add(
            SortDateParkingAssignment(
                sort_date_operation_id=operation.id,
                tail_number="N999UP",
                ramp_code="A",
                position_code="A01",
                lane_number=1,
            )
        )
        db.session.commit()

        response = self.client.get(f"/motherbrain/parking-plan/{operation.id}")
        html = response.data.decode()
        slot_html = html.split('data-occupied-tail="N999UP"', 1)[1].split(
            "</article>",
            1,
        )[0]

        self.assertEqual(response.status_code, 200)
        self.assertIn("N999UP", slot_html)
        self.assertIn("UNATTACHED TAIL", slot_html)
        self.assertIn('data-occupied-tail="N999UP"', html)
        status_html = html.split('class="parking-status-panel', 1)[1].split(
            'class="parking-layout"',
            1,
        )[0]
        self.assertIn("PARKED TAILS WITHOUT ACTIVE MISSION", status_html)
        self.assertIn("N999UP", status_html)
        self.assertIn("A01-1", status_html)
        self.assertIn("UNATTACHED TAIL", status_html)
        self.assertIn('href="#PARKING-TAIL-N999UP"', status_html)

    def test_parking_plan_status_panel_counts_and_unassigned_tails(self):
        operation = self._parking_operation()
        self._parking_pair(operation, "N457UP", destination="LAX")
        self._parking_pair(operation, "N349UP", destination="ONT")
        db.session.commit()
        self.client.post(
            f"/motherbrain/parking-plan/{operation.id}/assign",
            data={
                "tail_number": "N457UP",
                "ramp_code": "A",
                "position_code": "A01",
                "lane_number": "1",
            },
        )

        response = self.client.get(f"/motherbrain/parking-plan/{operation.id}")
        html = response.data.decode()
        status_html = html.split('class="parking-status-panel', 1)[1].split(
            'class="parking-layout"',
            1,
        )[0]

        self.assertIn("PARKING STATUS", status_html)
        self.assertIn("CHECK ASSIGNMENTS", status_html)
        self.assertIn("TOTAL 2", status_html)
        self.assertIn("ASSIGNED 1", status_html)
        self.assertIn("UNASSIGNED 1", status_html)
        self.assertIn("UNASSIGNED TAILS", status_html)
        self.assertIn("N349UP", status_html)
        self.assertIn("NOT PARKED ACTIVE MISSIONS", status_html)
        self.assertIn("ARR49", status_html)
        self.assertIn("DEP49", status_html)
        self.assertIn('href="#PARKING-TAIL-N349UP"', status_html)
        self.assertNotIn("No parking conflicts", status_html)

    def test_parking_plan_status_panel_shows_clean_state_when_all_assigned(self):
        operation = self._parking_operation()
        self._parking_pair(operation, "N457UP", destination="LAX")
        db.session.commit()
        self.client.post(
            f"/motherbrain/parking-plan/{operation.id}/assign",
            data={
                "tail_number": "N457UP",
                "ramp_code": "A",
                "position_code": "A01",
                "lane_number": "1",
            },
        )

        response = self.client.get(f"/motherbrain/parking-plan/{operation.id}")
        html = response.data.decode()
        status_html = html.split('class="parking-status-panel', 1)[1].split(
            'class="parking-layout"',
            1,
        )[0]

        self.assertIn("NO PARKING CONFLICTS", status_html)
        self.assertIn("TOTAL 1", status_html)
        self.assertIn("ASSIGNED 1", status_html)
        self.assertIn("UNASSIGNED 0", status_html)
        self.assertIn("All current-sort tails are assigned", status_html)

    def test_parking_status_helper_detects_duplicate_conflicts(self):
        tail_rows = [
            {"tail": "N111UP", "assigned_position": "A01-1"},
            {"tail": "N222UP", "assigned_position": ""},
        ]
        assignments = [
            SimpleNamespace(
                tail_number="N111UP",
                ramp_code="A",
                position_code="A01",
                lane_number=1,
            ),
            SimpleNamespace(
                tail_number="N111UP",
                ramp_code="B",
                position_code="B02",
                lane_number=2,
            ),
            SimpleNamespace(
                tail_number="N333UP",
                ramp_code="A",
                position_code="A01",
                lane_number=1,
            ),
        ]

        status = parking_status_for_rows(tail_rows, assignments)

        self.assertEqual(status["summary"]["total_tails_needing_parking"], 2)
        self.assertEqual(status["summary"]["assigned_tails"], 1)
        self.assertEqual(status["summary"]["unassigned_tails"], 1)
        self.assertEqual(status["unassigned_tails"], ["N222UP"])
        self.assertEqual(status["conflict_count"], 2)
        self.assertTrue(status["has_conflicts"])
        self.assertEqual(status["duplicate_tail_conflicts"][0]["tail"], "N111UP")
        self.assertEqual(
            status["duplicate_tail_conflicts"][0]["locations"],
            ["A01 Slot 1", "B02 Slot 2"],
        )
        self.assertEqual(
            status["duplicate_tail_conflicts"][0]["anchor"],
            "PARKING-TAIL-N111UP",
        )
        self.assertEqual(
            status["duplicate_slot_conflicts"][0]["position"],
            "A01 Slot 1",
        )
        self.assertEqual(
            status["duplicate_slot_conflicts"][0]["tails"],
            ["N111UP", "N333UP"],
        )
        self.assertEqual(
            status["duplicate_slot_conflicts"][0]["anchor"],
            "PARKING-POSITION-A01",
        )

    def test_parking_validator_detects_normal_bank_fill_order_conflict(self):
        operation = self._parking_operation()
        self._parking_pair(operation, "N457UP", aircraft_type="757")
        db.session.flush()
        self._parking_assignment(operation, "N457UP", "A03")
        db.session.commit()
        tail_rows = parking_plan_context(self.rfd_gateway, operation=operation)["tail_rows"]

        conflicts = validate_parking_physical_rules(operation, tail_rows=tail_rows)
        messages = [conflict.message for conflict in conflicts]

        self.assertIn("A03 cannot be used until A01, A02 are filled.", messages)

    def test_parking_validator_detects_remote_fill_order_conflict(self):
        operation = self._parking_operation()
        self._parking_pair(operation, "N457UP", aircraft_type="757")
        db.session.flush()
        self._parking_assignment(operation, "N457UP", "R03", ramp_code="R")
        db.session.commit()
        tail_rows = parking_plan_context(self.rfd_gateway, operation=operation)["tail_rows"]

        conflicts = validate_parking_physical_rules(operation, tail_rows=tail_rows)
        messages = [conflict.message for conflict in conflicts]

        self.assertIn("R03 cannot be used until R01, R02 are filled.", messages)

    def test_parking_validator_detects_767_invalid_normal_anchor(self):
        operation = self._parking_operation()
        for tail, position, aircraft_type in (
            ("N451UP", "A01", "757"),
            ("N452UP", "A02", "757"),
            ("N453UP", "A03", "757"),
            ("N964UP", "A04", "757"),
        ):
            self._parking_pair(operation, tail, aircraft_type=aircraft_type, destination=position)
            db.session.flush()
            self._parking_assignment(operation, tail, position)
        db.session.commit()
        tail_rows = parking_plan_context(self.rfd_gateway, operation=operation)["tail_rows"]

        conflicts = validate_parking_physical_rules(operation, tail_rows=tail_rows)
        messages = [conflict.message for conflict in conflicts]

        self.assertIn(
            "767 at A04 is invalid because 767 aircraft cannot anchor at 04.",
            messages,
        )

    def test_parking_validator_detects_slot_occupied_while_blocked_by_767(self):
        operation = self._parking_operation()
        self._parking_pair(operation, "N967UP", aircraft_type="757")
        self._parking_pair(operation, "N457UP", aircraft_type="757", destination="SDF")
        db.session.flush()
        self._parking_assignment(operation, "N967UP", "A01")
        self._parking_assignment(operation, "N457UP", "A02")
        db.session.commit()
        tail_rows = parking_plan_context(self.rfd_gateway, operation=operation)["tail_rows"]

        conflicts = validate_parking_physical_rules(operation, tail_rows=tail_rows)
        messages = [conflict.message for conflict in conflicts]

        self.assertIn("A02 is blocked by 767 parked at A01.", messages)

    def test_parking_validator_remote_767_does_not_block_adjacent_remote(self):
        operation = self._parking_operation()
        self._parking_pair(operation, "N967UP", aircraft_type="757")
        self._parking_pair(operation, "N457UP", aircraft_type="757", destination="SDF")
        db.session.flush()
        self._parking_assignment(operation, "N967UP", "R01", ramp_code="R")
        self._parking_assignment(operation, "N457UP", "R02", ramp_code="R")
        db.session.commit()
        tail_rows = parking_plan_context(self.rfd_gateway, operation=operation)["tail_rows"]

        conflicts = validate_parking_physical_rules(operation, tail_rows=tail_rows)
        messages = [conflict.message for conflict in conflicts]

        self.assertNotIn("R02 is blocked by 767 parked at R01.", messages)
        self.assertFalse(messages)

    def test_parking_validator_throat_767_does_not_block_another_slot(self):
        operation = self._parking_operation()
        self._parking_pair(operation, "N967UP", aircraft_type="757")
        self._parking_pair(operation, "N457UP", aircraft_type="757", destination="SDF")
        db.session.flush()
        self._parking_assignment(operation, "N967UP", "A10")
        self._parking_assignment(operation, "N457UP", "A09")
        db.session.commit()
        tail_rows = parking_plan_context(self.rfd_gateway, operation=operation)["tail_rows"]

        conflicts = validate_parking_physical_rules(operation, tail_rows=tail_rows)
        messages = [conflict.message for conflict in conflicts]

        self.assertNotIn("A09 is blocked by 767 parked at A10.", messages)
        self.assertFalse(messages)

    def test_parking_validator_detects_09_used_without_10(self):
        operation = self._parking_operation()
        self._parking_pair(operation, "N457UP", aircraft_type="757")
        db.session.flush()
        self._parking_assignment(operation, "N457UP", "A09")
        db.session.commit()
        tail_rows = parking_plan_context(self.rfd_gateway, operation=operation)["tail_rows"]

        conflicts = validate_parking_physical_rules(operation, tail_rows=tail_rows)
        messages = [conflict.message for conflict in conflicts]

        self.assertIn("A09 cannot be used unless A10 is also parked.", messages)

    def test_parking_validator_detects_09_without_clear_full_bank(self):
        operation = self._parking_operation()
        assignments = (
            ("N091UP", "A09", "757"),
            ("N101UP", "A10", "757"),
            ("N001UP", "A01", "757"),
            ("N005UP", "A05", "757"),
        )
        for tail, position, aircraft_type in assignments:
            self._parking_pair(operation, tail, aircraft_type=aircraft_type, destination=position)
            db.session.flush()
            self._parking_assignment(operation, tail, position)
        db.session.commit()
        tail_rows = parking_plan_context(self.rfd_gateway, operation=operation)["tail_rows"]

        conflicts = validate_parking_physical_rules(operation, tail_rows=tail_rows)
        messages = [conflict.message for conflict in conflicts]

        self.assertIn(
            "A09 can only be used when a full A01-A04 or A05-A08 bank is clear.",
            messages,
        )

    def test_parking_validator_detects_10_without_clear_partial_bank(self):
        operation = self._parking_operation()
        assignments = (
            ("N101UP", "A01", "757"),
            ("N102UP", "A02", "757"),
            ("N105UP", "A05", "757"),
            ("N106UP", "A06", "757"),
            ("N110UP", "A10", "757"),
        )
        for tail, position, aircraft_type in assignments:
            self._parking_pair(operation, tail, aircraft_type=aircraft_type, destination=position)
            db.session.flush()
            self._parking_assignment(operation, tail, position)
        db.session.commit()
        tail_rows = parking_plan_context(self.rfd_gateway, operation=operation)["tail_rows"]

        conflicts = validate_parking_physical_rules(operation, tail_rows=tail_rows)
        messages = [conflict.message for conflict in conflicts]

        self.assertIn(
            "A10 can only be used when A02-A04 or A06-A08 are clear.",
            messages,
        )

    def test_parking_plan_page_displays_physical_validation_conflicts(self):
        operation = self._parking_operation()
        self._parking_pair(operation, "N457UP", aircraft_type="757")
        db.session.flush()
        self._parking_assignment(operation, "N457UP", "A03")
        db.session.commit()

        response = self.client.get(f"/motherbrain/parking-plan/{operation.id}")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"PHYSICAL PARKING RULES", response.data)
        self.assertIn(b"A03 cannot be used until A01, A02 are filled.", response.data)

    def test_parking_eta_order_validator_detects_normal_bank_01_04_conflict(self):
        operation = self._parking_operation()
        self._parking_pair(
            operation,
            "N001UP",
            arrival_local=datetime(2026, 6, 19, 0, 10),
            destination="A01",
        )
        self._parking_pair(
            operation,
            "N002UP",
            arrival_local=datetime(2026, 6, 19, 0, 12),
            destination="A02",
        )
        self._parking_pair(
            operation,
            "N003UP",
            arrival_local=datetime(2026, 6, 19, 0, 0),
            destination="A03",
        )
        db.session.flush()
        self._parking_assignment(operation, "N001UP", "A01")
        self._parking_assignment(operation, "N002UP", "A02")
        self._parking_assignment(operation, "N003UP", "A03")
        db.session.commit()
        tail_rows = parking_plan_context(self.rfd_gateway, operation=operation)["tail_rows"]

        conflicts = validate_parking_physical_rules(operation, tail_rows=tail_rows)
        messages = [conflict.message for conflict in conflicts]

        self.assertTrue(
            any(
                "A03 cannot arrive before A01/A02 are parked." in message
                and "N003UP at A03 ETA 00:10" in message
                for message in messages
            )
        )

    def test_parking_eta_order_validator_detects_normal_bank_05_08_conflict(self):
        operation = self._parking_operation()
        self._parking_pair(
            operation,
            "N005UP",
            arrival_local=datetime(2026, 6, 18, 23, 30),
            destination="A05",
        )
        self._parking_pair(
            operation,
            "N006UP",
            arrival_local=datetime(2026, 6, 18, 23, 0),
            destination="A06",
        )
        db.session.flush()
        self._parking_assignment(operation, "N005UP", "A05")
        self._parking_assignment(operation, "N006UP", "A06")
        db.session.commit()
        tail_rows = parking_plan_context(self.rfd_gateway, operation=operation)["tail_rows"]

        conflicts = validate_parking_physical_rules(operation, tail_rows=tail_rows)
        messages = [conflict.message for conflict in conflicts]

        self.assertTrue(
            any(
                "A06 cannot arrive before A05 are parked." in message
                and "N006UP at A06 ETA 23:10" in message
                for message in messages
            )
        )

    def test_parking_eta_order_validator_respects_767_blocked_fill_positions(self):
        operation = self._parking_operation()
        self._parking_pair(
            operation,
            "N967UP",
            aircraft_type="757",
            arrival_local=datetime(2026, 6, 19, 0, 10),
            destination="A01",
        )
        self._parking_pair(
            operation,
            "N003UP",
            aircraft_type="757",
            arrival_local=datetime(2026, 6, 19, 0, 0),
            destination="A03",
        )
        db.session.flush()
        self._parking_assignment(operation, "N967UP", "A01")
        self._parking_assignment(operation, "N003UP", "A03")
        db.session.commit()
        tail_rows = parking_plan_context(self.rfd_gateway, operation=operation)["tail_rows"]

        conflicts = validate_parking_physical_rules(operation, tail_rows=tail_rows)
        messages = [conflict.message for conflict in conflicts]

        self.assertTrue(
            any(
                "A03 cannot arrive before A01/A02 are parked." in message
                and "N967UP at A01 ETA 00:20" in message
                for message in messages
            )
        )

    def test_parking_eta_order_validator_detects_remote_conflict(self):
        operation = self._parking_operation()
        self._parking_pair(
            operation,
            "N001UP",
            arrival_local=datetime(2026, 6, 18, 23, 30),
            destination="R01",
        )
        self._parking_pair(
            operation,
            "N002UP",
            arrival_local=datetime(2026, 6, 18, 23, 35),
            destination="R02",
        )
        self._parking_pair(
            operation,
            "N003UP",
            arrival_local=datetime(2026, 6, 18, 23, 0),
            destination="R03",
        )
        db.session.flush()
        self._parking_assignment(operation, "N001UP", "R01", ramp_code="R")
        self._parking_assignment(operation, "N002UP", "R02", ramp_code="R")
        self._parking_assignment(operation, "N003UP", "R03", ramp_code="R")
        db.session.commit()
        tail_rows = parking_plan_context(self.rfd_gateway, operation=operation)["tail_rows"]

        conflicts = validate_parking_physical_rules(operation, tail_rows=tail_rows)
        messages = [conflict.message for conflict in conflicts]

        self.assertTrue(
            any(
                "R03 ETA order conflict: R03 arrives before R01/R02." in message
                and "N003UP at R03 ETA 23:10" in message
                for message in messages
            )
        )

    def test_parking_eta_order_validator_detects_09_before_10_conflict(self):
        operation = self._parking_operation()
        self._parking_pair(
            operation,
            "N010UP",
            arrival_local=datetime(2026, 6, 18, 23, 30),
            destination="A10",
        )
        self._parking_pair(
            operation,
            "N009UP",
            arrival_local=datetime(2026, 6, 18, 23, 0),
            destination="A09",
        )
        db.session.flush()
        self._parking_assignment(operation, "N010UP", "A10")
        self._parking_assignment(operation, "N009UP", "A09")
        db.session.commit()
        tail_rows = parking_plan_context(self.rfd_gateway, operation=operation)["tail_rows"]

        conflicts = validate_parking_physical_rules(operation, tail_rows=tail_rows)
        messages = [conflict.message for conflict in conflicts]

        self.assertTrue(
            any(
                "A09 ETA order conflict: A09 cannot arrive before A10." in message
                and "N009UP at A09 ETA 23:10" in message
                for message in messages
            )
        )

    def test_parking_eta_order_validator_is_midnight_aware(self):
        operation = self._parking_operation()
        self._parking_pair(
            operation,
            "N001UP",
            arrival_local=datetime(2026, 6, 19, 0, 0),
            destination="A01",
        )
        self._parking_pair(
            operation,
            "N002UP",
            arrival_local=datetime(2026, 6, 18, 23, 45),
            destination="A02",
        )
        db.session.flush()
        self._parking_assignment(operation, "N001UP", "A01")
        self._parking_assignment(operation, "N002UP", "A02")
        db.session.commit()
        tail_rows = parking_plan_context(self.rfd_gateway, operation=operation)["tail_rows"]

        conflicts = validate_parking_physical_rules(operation, tail_rows=tail_rows)
        messages = [conflict.message for conflict in conflicts]

        self.assertTrue(
            any(
                "N002UP at A02 ETA 23:55" in message
                and "N001UP at A01 ETA 00:10" in message
                for message in messages
            )
        )

    def test_parking_plan_page_displays_eta_order_conflicts(self):
        operation = self._parking_operation()
        self._parking_pair(
            operation,
            "N001UP",
            arrival_local=datetime(2026, 6, 19, 0, 10),
            destination="A01",
        )
        self._parking_pair(
            operation,
            "N002UP",
            arrival_local=datetime(2026, 6, 19, 0, 0),
            destination="A02",
        )
        db.session.flush()
        self._parking_assignment(operation, "N001UP", "A01")
        self._parking_assignment(operation, "N002UP", "A02")
        db.session.commit()

        response = self.client.get(f"/motherbrain/parking-plan/{operation.id}")

        self.assertIn(b"PHYSICAL PARKING RULES", response.data)
        self.assertIn(b"Parking ETA order conflict", response.data)
        self.assertIn(b"N002UP at A02 ETA 00:10", response.data)

    def test_parking_eta_order_alert_tray_syncs_and_clears(self):
        operation = self._parking_operation()
        _late_arrival, _late_departure = self._parking_pair(
            operation,
            "N001UP",
            arrival_local=datetime(2026, 6, 19, 0, 10),
            destination="A01",
        )
        early_arrival, _early_departure = self._parking_pair(
            operation,
            "N002UP",
            arrival_local=datetime(2026, 6, 19, 0, 0),
            destination="A02",
        )
        db.session.flush()
        self._parking_assignment(operation, "N001UP", "A01")
        self._parking_assignment(operation, "N002UP", "A02")
        db.session.commit()

        first_response = self.client.get(f"/motherbrain/parking-plan/{operation.id}")
        second_response = self.client.get(f"/motherbrain/parking-plan/{operation.id}")

        self.assertIn(b"MotherBrain Alerts", first_response.data)
        self.assertIn(b"Parking ETA order conflict", first_response.data)
        self.assertIn(b"Parking ETA order conflict", second_response.data)
        self.assertEqual(
            MotherBrainAlert.query.filter_by(
                title="Parking ETA order conflict",
                active=True,
            ).count(),
            1,
        )

        early_arrival.planned_datetime_local = datetime(2026, 6, 19, 0, 30)
        early_arrival.planned_datetime_utc = datetime(2026, 6, 19, 0, 30)
        db.session.commit()
        resolved_response = self.client.get(f"/motherbrain/parking-plan/{operation.id}")

        self.assertNotIn(b"Parking ETA order conflict", resolved_response.data)
        self.assertEqual(
            MotherBrainAlert.query.filter_by(
                title="Parking ETA order conflict",
                active=True,
            ).count(),
            0,
        )

    def test_parking_validation_alert_tray_syncs_and_does_not_duplicate(self):
        operation = self._parking_operation()
        self._parking_pair(operation, "N457UP", aircraft_type="757")
        db.session.flush()
        self._parking_assignment(operation, "N457UP", "A03")
        db.session.commit()

        first_response = self.client.get(f"/motherbrain/parking-plan/{operation.id}")
        second_response = self.client.get(f"/motherbrain/parking-plan/{operation.id}")
        active_alerts = MotherBrainAlert.query.filter_by(active=True).all()

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(len(active_alerts), 1)
        self.assertIn(b"Parking fill order conflict", second_response.data)
        self.assertIn(b"A03 cannot be used until A01, A02 are filled.", second_response.data)
        self.assertEqual(active_alerts[0].permission_key, "motherbrain.parking_conflicts.view")

    def test_parking_validation_alert_clears_when_conflict_is_resolved(self):
        operation = self._parking_operation()
        for tail, position in (
            ("N451UP", "A01"),
            ("N452UP", "A02"),
            ("N453UP", "A03"),
        ):
            self._parking_pair(operation, tail, aircraft_type="757", destination=position)
            db.session.flush()
            if position == "A03":
                self._parking_assignment(operation, tail, position)
        db.session.commit()

        self.client.get(f"/motherbrain/parking-plan/{operation.id}")
        self.assertEqual(MotherBrainAlert.query.filter_by(active=True).count(), 1)

        self._parking_assignment(operation, "N451UP", "A01")
        self._parking_assignment(operation, "N452UP", "A02")
        db.session.commit()
        response = self.client.get(f"/motherbrain/parking-plan/{operation.id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(MotherBrainAlert.query.filter_by(active=True).count(), 0)
        self.assertEqual(MotherBrainAlert.query.count(), 1)
        self.assertNotIn(b"A03 cannot be used until A01, A02 are filled.", response.data)

    def test_parking_optimizer_ortools_dependency_imports(self):
        from ortools.sat.python import cp_model

        self.assertEqual(cp_model.CpModel().__class__.__name__, "CpModel")

    def test_parking_optimizer_preview_returns_suggestions_without_writing(self):
        operation = self._parking_operation()
        self._parking_pair(operation, "N457UP", aircraft_type="757")
        db.session.commit()

        preview = self._parking_optimizer_preview(operation)

        self.assertEqual(SortDateParkingAssignment.query.count(), 0)
        self.assertEqual(preview["solver_status"], "OPTIMAL")
        self.assertEqual(preview["suggested_assignments"][0]["tail"], "N457UP")
        self.assertEqual(preview["suggested_assignments"][0]["label"], "A01 Slot 1")

    def test_parking_optimizer_preserves_locked_manual_assignments(self):
        operation = self._parking_operation()
        self._parking_pair(operation, "N457UP", aircraft_type="757")
        self._parking_pair(operation, "N349UP", aircraft_type="757", destination="SDF")
        db.session.flush()
        self._parking_assignment(operation, "N457UP", "A01")
        db.session.commit()

        preview = self._parking_optimizer_preview(operation)
        locked = {row["tail"]: row for row in preview["locked_assignments"]}
        suggestions = {row["tail"]: row for row in preview["suggested_assignments"]}

        self.assertEqual(locked["N457UP"]["label"], "A01 Slot 1")
        self.assertEqual(suggestions["N349UP"]["label"], "B01 Slot 1")
        self.assertEqual(SortDateParkingAssignment.query.count(), 1)

    def test_parking_optimizer_locked_invalid_assignment_reports_conflict(self):
        operation = self._parking_operation()
        self._parking_pair(operation, "N457UP", aircraft_type="757")
        db.session.flush()
        self._parking_assignment(operation, "N457UP", "A03")
        db.session.commit()

        preview = self._parking_optimizer_preview(operation)
        messages = [conflict["message"] for conflict in preview["conflicts"]]

        self.assertIn("A03 cannot be used until A01, A02 are filled.", messages)
        self.assertEqual(preview["locked_assignments"][0]["label"], "A03 Slot 1")

    def test_parking_optimizer_ignores_cancelled_missions(self):
        operation = self._parking_operation()
        arrival, departure = self._parking_pair(operation, "N457UP", aircraft_type="757")
        arrival.arrival_status = "cancelled"
        departure.departure_status = "cancelled"
        db.session.commit()

        preview = self._parking_optimizer_preview(operation)

        self.assertEqual(preview["suggested_assignments"], [])
        self.assertEqual(preview["unassigned_tails"], [])

    def test_parking_optimizer_remote_toggle_controls_remote_use(self):
        operation = self._parking_operation()
        self._parking_pair(operation, "N457UP", origin="ONT", aircraft_type="757")
        self._parking_rule(ORIGIN_RAMP_RESTRICTION, "origin", "ONT", "R", behavior="required")
        db.session.commit()

        off_preview = self._parking_optimizer_preview(operation, include_remote=False)
        on_preview = self._parking_optimizer_preview(operation, include_remote=True)

        self.assertEqual(off_preview["suggested_assignments"], [])
        self.assertEqual(on_preview["suggested_assignments"][0]["label"], "R01 Slot 1")

    def test_parking_optimizer_throat_toggle_controls_09_10_use(self):
        operation = self._parking_operation()
        self._parking_pair(operation, "N457UP", origin="ONT", aircraft_type="757")
        self._parking_rule(ORIGIN_RAMP_RESTRICTION, "origin", "ONT", "THROAT", behavior="required")
        db.session.commit()

        off_preview = self._parking_optimizer_preview(operation, include_throat=False)
        on_preview = self._parking_optimizer_preview(operation, include_throat=True)

        self.assertEqual(off_preview["suggested_assignments"], [])
        self.assertEqual(on_preview["suggested_assignments"][0]["label"], "A10 Slot 1")

    def test_parking_optimizer_enforces_normal_bank_fill_order(self):
        operation = self._parking_operation()
        for tail in ("N451UP", "N452UP", "N453UP"):
            self._parking_pair(operation, tail, aircraft_type="757", destination=tail)
        self._parking_rule(ORIGIN_RAMP_RESTRICTION, "origin", "ONT", "A", behavior="required")
        db.session.commit()

        preview = self._parking_optimizer_preview(operation)
        positions = [row["position"] for row in preview["suggested_assignments"]]

        self.assertNotIn("A03", positions)
        self.assertIn("A01", positions)
        self.assertIn("A02", positions)

    def test_parking_optimizer_enforces_remote_fill_order(self):
        operation = self._parking_operation()
        for tail in ("N451UP", "N452UP", "N453UP"):
            self._parking_pair(operation, tail, origin="ONT", aircraft_type="757", destination=tail)
        self._parking_rule(ORIGIN_RAMP_RESTRICTION, "origin", "ONT", "R", behavior="required")
        db.session.commit()

        preview = self._parking_optimizer_preview(operation, include_remote=True)
        positions = [row["position"] for row in preview["suggested_assignments"]]

        self.assertIn("R01", positions)
        self.assertIn("R02", positions)
        if "R03" in positions:
            self.assertTrue({"R01", "R02"}.issubset(set(positions)))

    def test_parking_optimizer_767_at_normal_01_blocks_02(self):
        operation = self._parking_operation()
        self._parking_pair(operation, "N967UP", aircraft_type="757")
        self._parking_pair(operation, "N457UP", aircraft_type="757", destination="SDF")
        db.session.flush()
        self._parking_assignment(operation, "N967UP", "A01")
        db.session.commit()

        preview = self._parking_optimizer_preview(operation)
        suggestions = {row["tail"]: row for row in preview["suggested_assignments"]}

        self.assertNotEqual(suggestions["N457UP"]["position"], "A02")

    def test_parking_optimizer_767_cannot_anchor_at_04_or_08(self):
        operation = self._parking_operation()
        for position in ("A01", "A02", "A03"):
            tail = f"N{position[-2:]}1UP"
            self._parking_pair(operation, tail, aircraft_type="757", destination=position)
            db.session.flush()
            self._parking_assignment(operation, tail, position, lane_number=1)
            self._parking_assignment(operation, f"X{position[-2:]}2UP", position, lane_number=2)
        self._parking_pair(operation, "N967UP", origin="ONT", aircraft_type="757", destination="SDF")
        self._parking_rule(ORIGIN_RAMP_RESTRICTION, "origin", "ONT", "A", behavior="required")
        db.session.commit()

        preview = self._parking_optimizer_preview(operation)
        suggestions = {row["tail"]: row for row in preview["suggested_assignments"]}

        self.assertNotIn(suggestions["N967UP"]["position"], {"A04", "A08"})

    def test_parking_optimizer_remote_767_does_not_block_adjacent_remote(self):
        operation = self._parking_operation()
        self._parking_pair(operation, "N967UP", aircraft_type="757")
        self._parking_pair(operation, "N457UP", origin="ONT", aircraft_type="757", destination="SDF")
        self._parking_rule(ORIGIN_RAMP_RESTRICTION, "origin", "ONT", "R", behavior="required")
        db.session.flush()
        self._parking_assignment(operation, "N967UP", "R01", ramp_code="R", lane_number=1)
        self._parking_assignment(operation, "XREMUP", "R01", ramp_code="R", lane_number=2)
        db.session.commit()

        preview = self._parking_optimizer_preview(operation, include_remote=True)
        suggestions = {row["tail"]: row for row in preview["suggested_assignments"]}

        self.assertEqual(suggestions["N457UP"]["position"], "R02")

    def test_parking_optimizer_throat_767_does_not_block_adjacent_throat(self):
        operation = self._parking_operation()
        self._parking_pair(operation, "N967UP", aircraft_type="757")
        self._parking_pair(operation, "N457UP", origin="ONT", aircraft_type="757", destination="SDF")
        self._parking_rule(ORIGIN_RAMP_RESTRICTION, "origin", "ONT", "THROAT", behavior="required")
        db.session.flush()
        self._parking_assignment(operation, "N967UP", "A10", lane_number=1)
        self._parking_assignment(operation, "XTHRUP", "A10", lane_number=2)
        db.session.commit()

        preview = self._parking_optimizer_preview(operation, include_throat=True)
        suggestions = {row["tail"]: row for row in preview["suggested_assignments"]}

        self.assertEqual(suggestions["N457UP"]["position"], "A09")

    def test_parking_optimizer_enforces_10_before_9(self):
        operation = self._parking_operation()
        self._parking_pair(operation, "N457UP", origin="ONT", aircraft_type="757")
        self._parking_rule(ORIGIN_RAMP_RESTRICTION, "origin", "ONT", "THROAT", behavior="required")
        db.session.commit()

        preview = self._parking_optimizer_preview(operation, include_throat=True)

        self.assertEqual(preview["suggested_assignments"][0]["position"], "A10")

    def test_parking_optimizer_enforces_aircraft_type_hard_restriction(self):
        operation = self._parking_operation()
        self._parking_pair(operation, "N457UP", aircraft_type="757")
        self._parking_rule(
            AIRCRAFT_TYPE_RAMP_RESTRICTION,
            "aircraft_type",
            "757",
            "A",
        )
        db.session.commit()

        preview = self._parking_optimizer_preview(operation)

        self.assertNotEqual(preview["suggested_assignments"][0]["position"][:1], "A")

    def test_parking_optimizer_uses_tail_resolver_for_aircraft_rules(self):
        operation = self._parking_operation()
        self._parking_pair(operation, "N967UP", aircraft_type="757")
        self._parking_rule(
            AIRCRAFT_TYPE_RAMP_RESTRICTION,
            "aircraft_type",
            "767",
            "A",
        )
        db.session.commit()

        preview = self._parking_optimizer_preview(operation)

        self.assertEqual(preview["suggested_assignments"][0]["aircraft_type"], "767")
        self.assertNotEqual(preview["suggested_assignments"][0]["position"][:1], "A")

    def test_parking_optimizer_enforces_origin_hard_restriction(self):
        operation = self._parking_operation()
        self._parking_pair(operation, "N457UP", origin="ONT", aircraft_type="757")
        self._parking_rule(ORIGIN_RAMP_RESTRICTION, "origin", "ONT", "A")
        db.session.commit()

        preview = self._parking_optimizer_preview(operation)

        self.assertNotEqual(preview["suggested_assignments"][0]["position"][:1], "A")

    def test_parking_optimizer_origin_ramp_preference_affects_scoring(self):
        operation = self._parking_operation()
        self._parking_pair(operation, "N457UP", origin="SDF", aircraft_type="757")
        self._parking_rule(
            ORIGIN_RAMP_PREFERENCE,
            "origin",
            "SDF",
            "B",
            behavior="preferred",
        )
        db.session.commit()

        preview = self._parking_optimizer_preview(operation)
        suggestion = preview["suggested_assignments"][0]

        self.assertEqual(suggestion["position"], "B01")
        self.assertIn("Origin SDF prefers ramp B.", suggestion["reason"])

    def test_parking_optimizer_origin_avoid_ramp_reduces_scoring(self):
        operation = self._parking_operation()
        self._parking_pair(operation, "N457UP", origin="SDF", aircraft_type="757")
        self._parking_rule(
            ORIGIN_RAMP_PREFERENCE,
            "origin",
            "SDF",
            "A",
            behavior="avoid",
        )
        db.session.commit()

        preview = self._parking_optimizer_preview(operation)
        suggestion = preview["suggested_assignments"][0]

        self.assertNotEqual(suggestion["position"][:1], "A")
        self.assertIn("Origin SDF avoids ramp A.", suggestion["reason"])

    def test_parking_optimizer_aircraft_type_ramp_preference_affects_scoring(self):
        operation = self._parking_operation()
        self._parking_pair(operation, "N457UP", aircraft_type="757")
        self._parking_rule(
            AIRCRAFT_TYPE_RAMP_PREFERENCE,
            "aircraft_type",
            "757",
            "B",
            behavior="preferred",
        )
        db.session.commit()

        preview = self._parking_optimizer_preview(operation)
        suggestion = preview["suggested_assignments"][0]

        self.assertEqual(suggestion["position"], "B01")
        self.assertIn("Aircraft 757 prefers ramp B.", suggestion["reason"])

    def test_parking_optimizer_aircraft_type_avoid_ramp_reduces_scoring(self):
        operation = self._parking_operation()
        self._parking_pair(operation, "N457UP", aircraft_type="757")
        self._parking_rule(
            AIRCRAFT_TYPE_RAMP_PREFERENCE,
            "aircraft_type",
            "757",
            "A",
            behavior="avoid",
        )
        db.session.commit()

        preview = self._parking_optimizer_preview(operation)
        suggestion = preview["suggested_assignments"][0]

        self.assertNotEqual(suggestion["position"][:1], "A")
        self.assertIn("Aircraft 757 avoids ramp A.", suggestion["reason"])

    def test_parking_optimizer_hard_restriction_overrides_soft_preference(self):
        operation = self._parking_operation()
        self._parking_pair(operation, "N457UP", origin="SDF", aircraft_type="757")
        self._parking_rule(
            ORIGIN_RAMP_PREFERENCE,
            "origin",
            "SDF",
            "B",
            behavior="preferred",
        )
        self._parking_rule(
            ORIGIN_RAMP_RESTRICTION,
            "origin",
            "SDF",
            "B",
            behavior="forbidden",
        )
        db.session.commit()

        preview = self._parking_optimizer_preview(operation)

        self.assertNotEqual(preview["suggested_assignments"][0]["position"][:1], "B")

    def test_parking_optimizer_penalizes_same_ramp_close_deice_departures(self):
        operation = self._parking_operation()
        self._parking_pair(
            operation,
            "N001UP",
            departure_local=datetime(2026, 6, 19, 1, 0),
        )
        self._parking_pair(
            operation,
            "N002UP",
            departure_local=datetime(2026, 6, 19, 1, 5),
            destination="SDF",
        )
        db.session.commit()

        preview = self._parking_optimizer_preview(operation)
        suggestions = {row["tail"]: row for row in preview["suggested_assignments"]}

        self.assertNotEqual(
            suggestions["N001UP"]["position"][:1],
            suggestions["N002UP"]["position"][:1],
        )
        self.assertIn("Deice spacing checked", suggestions["N001UP"]["reason"])

    def test_parking_optimizer_deice_scoring_is_ramp_level_not_slot_adjacency(self):
        operation = self._parking_operation()
        for position, departure_time in (
            ("A01", datetime(2026, 6, 19, 3, 0)),
            ("A02", datetime(2026, 6, 19, 3, 5)),
            ("A03", datetime(2026, 6, 19, 3, 10)),
            ("A04", datetime(2026, 6, 19, 1, 0)),
        ):
            tail = f"N{position[-2:]}1UP"
            self._parking_pair(
                operation,
                tail,
                departure_local=departure_time,
                destination=position,
            )
            db.session.flush()
            self._parking_assignment(operation, tail, position)
        self._parking_pair(
            operation,
            "N999UP",
            departure_local=datetime(2026, 6, 19, 1, 5),
            destination="SDF",
        )
        db.session.commit()

        preview = self._parking_optimizer_preview(operation)
        suggestions = {row["tail"]: row for row in preview["suggested_assignments"]}

        self.assertNotEqual(suggestions["N999UP"]["position"][:1], "A")

    def test_parking_optimizer_uses_deice_threshold_setting(self):
        operation = self._parking_operation()
        db.session.add(
            MotherBrainParkingSettings(
                gateway_id=self.rfd_gateway.id,
                gateway_code=self.rfd_gateway.code,
                deice_spacing_threshold_minutes=4,
            )
        )
        self._parking_pair(
            operation,
            "N001UP",
            departure_local=datetime(2026, 6, 19, 1, 0),
        )
        self._parking_pair(
            operation,
            "N002UP",
            departure_local=datetime(2026, 6, 19, 1, 5),
            destination="SDF",
        )
        db.session.flush()
        self._parking_assignment(operation, "N001UP", "A01")
        db.session.commit()

        preview = self._parking_optimizer_preview(operation)
        suggestions = {row["tail"]: row for row in preview["suggested_assignments"]}

        self.assertEqual(preview["runtime_toggles"]["deice_spacing_threshold_minutes"], 4)
        self.assertEqual(suggestions["N002UP"]["label"], "A01 Slot 2")

    def test_parking_optimizer_deice_spacing_is_midnight_aware(self):
        operation = self._parking_operation()
        self._parking_pair(
            operation,
            "N001UP",
            departure_local=datetime(2026, 6, 18, 23, 58),
        )
        self._parking_pair(
            operation,
            "N002UP",
            departure_local=datetime(2026, 6, 19, 0, 5),
            destination="SDF",
        )
        db.session.flush()
        self._parking_assignment(operation, "N001UP", "A01")
        db.session.commit()

        preview = self._parking_optimizer_preview(operation)
        suggestions = {row["tail"]: row for row in preview["suggested_assignments"]}

        self.assertEqual(suggestions["N002UP"]["position"], "B01")

    def test_parking_optimizer_apply_uses_soft_scoring(self):
        operation = self._parking_operation()
        self._parking_pair(operation, "N457UP", origin="SDF", aircraft_type="757")
        self._parking_rule(
            ORIGIN_RAMP_PREFERENCE,
            "origin",
            "SDF",
            "B",
            behavior="preferred",
        )
        db.session.commit()

        self.client.post(
            f"/motherbrain/parking-plan/{operation.id}/optimize/apply",
            data={"confirm_apply": "1"},
            follow_redirects=True,
        )

        assignment = self._parking_assignment_for_tail(operation, "N457UP")
        self.assertEqual(assignment.position_code, "B01")

    def test_parking_optimizer_preview_obeys_eta_order_constraints(self):
        operation = self._parking_operation()
        self._parking_pair(
            operation,
            "N001UP",
            origin="SDF",
            arrival_local=datetime(2026, 6, 19, 0, 10),
            destination="A01",
        )
        self._parking_pair(
            operation,
            "N002UP",
            origin="ONT",
            arrival_local=datetime(2026, 6, 19, 0, 0),
            destination="A02",
        )
        self._parking_rule(ORIGIN_RAMP_RESTRICTION, "origin", "ONT", "A", behavior="required")
        db.session.flush()
        self._parking_assignment(operation, "N001UP", "A01")
        db.session.commit()

        preview = self._parking_optimizer_preview(operation)
        suggestions = {row["tail"]: row for row in preview["suggested_assignments"]}

        self.assertNotEqual(suggestions["N002UP"]["position"], "A02")

    def test_parking_optimizer_apply_revalidates_eta_order_before_writing(self):
        operation = self._parking_operation()
        self._parking_pair(
            operation,
            "N001UP",
            arrival_local=datetime(2026, 6, 19, 0, 10),
            destination="A01",
        )
        self._parking_pair(
            operation,
            "N002UP",
            arrival_local=datetime(2026, 6, 19, 0, 0),
            destination="A02",
        )
        self._parking_pair(
            operation,
            "N003UP",
            arrival_local=datetime(2026, 6, 19, 0, 20),
            destination="SDF",
        )
        db.session.flush()
        self._parking_assignment(operation, "N001UP", "A01")
        self._parking_assignment(operation, "N002UP", "A02")
        db.session.commit()

        response = self.client.post(
            f"/motherbrain/parking-plan/{operation.id}/optimize/apply",
            data={"confirm_apply": "1"},
            follow_redirects=True,
        )

        self.assertIn(b"Resolve ETA order conflicts", response.data)
        self.assertIsNone(self._parking_assignment_for_tail(operation, "N003UP"))

    def test_parking_optimizer_preview_renders_on_parking_plan(self):
        operation = self._parking_operation()
        self._parking_pair(operation, "N457UP", aircraft_type="757")
        db.session.commit()

        response = self.client.post(f"/motherbrain/parking-plan/{operation.id}/optimize")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"OPTIMIZE / SUGGEST PLAN", response.data)
        self.assertIn(b"PREVIEW ONLY", response.data)
        self.assertIn(b"Candidates 1", response.data)
        self.assertIn(b"SUGGESTED ASSIGNMENTS", response.data)
        self.assertIn(b"N457UP", response.data)
        self.assertEqual(SortDateParkingAssignment.query.count(), 0)

    def test_parking_optimizer_preview_renders_unresolved_reason_when_no_suggestions(self):
        operation = self._parking_operation()
        self._parking_pair(operation, "N457UP", aircraft_type="A300")
        for ramp in ("A", "B", "C", "D", "E"):
            self._parking_rule(
                AIRCRAFT_TYPE_RAMP_RESTRICTION,
                "aircraft_type",
                "757",
                ramp,
            )
        db.session.commit()

        response = self.client.post(f"/motherbrain/parking-plan/{operation.id}/optimize")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Candidates 1", response.data)
        self.assertIn(b"UNASSIGNED / UNRESOLVED", response.data)
        self.assertIn(b"N457UP", response.data)
        self.assertIn(b"Aircraft type restricted from available ramps.", response.data)
        self.assertIn(b"Remote disabled.", response.data)

    def test_parking_optimizer_apply_permission_is_enforced(self):
        ensure_default_permission_rules()
        apply_rule = PermissionRule.query.filter_by(
            permission_key="motherbrain.parking_optimizer.apply",
        ).first()
        apply_rule.minimum_role = "grandmaster"
        db.session.commit()
        self._login_motherbrain_role("ParkingOptimizerMaster", "master")
        operation = self._parking_operation()
        self._parking_pair(operation, "N457UP", aircraft_type="757")
        db.session.commit()

        response = self.client.post(
            f"/motherbrain/parking-plan/{operation.id}/optimize/apply",
            data={"confirm_apply": "1"},
            follow_redirects=True,
        )

        self.assertIn(b"Access denied.", response.data)
        self.assertEqual(SortDateParkingAssignment.query.count(), 0)

    def test_parking_optimizer_apply_requires_confirmation(self):
        operation = self._parking_operation()
        self._parking_pair(operation, "N457UP", aircraft_type="757")
        db.session.commit()

        response = self.client.post(
            f"/motherbrain/parking-plan/{operation.id}/optimize/apply",
            data={},
            follow_redirects=True,
        )

        self.assertIn(b"Confirm optimizer apply", response.data)
        self.assertEqual(SortDateParkingAssignment.query.count(), 0)

    def test_parking_optimizer_apply_reruns_server_side_before_writing(self):
        operation = self._parking_operation()
        self._parking_pair(operation, "N457UP", aircraft_type="757")
        db.session.commit()

        with patch(
            "app.neomotherbrain.routes.apply_parking_optimizer_plan",
            wraps=service_apply_parking_optimizer_plan,
        ) as apply_mock:
            response = self.client.post(
                f"/motherbrain/parking-plan/{operation.id}/optimize/apply",
                data={"confirm_apply": "1"},
                follow_redirects=True,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(apply_mock.call_count, 1)
        self.assertEqual(self._parking_assignment_for_tail(operation, "N457UP").position_code, "A01")

    def test_parking_optimizer_apply_writes_valid_suggestions(self):
        operation = self._parking_operation()
        self._parking_pair(operation, "N457UP", aircraft_type="757")
        db.session.commit()

        response = self.client.post(
            f"/motherbrain/parking-plan/{operation.id}/optimize/apply",
            data={"confirm_apply": "1"},
            follow_redirects=True,
        )
        assignment = self._parking_assignment_for_tail(operation, "N457UP")

        self.assertIn(b"Applied 1 optimizer assignment", response.data)
        self.assertEqual(assignment.position_code, "A01")
        self.assertEqual(assignment.lane_number, 1)

    def test_parking_optimizer_apply_does_not_write_when_solver_not_successful(self):
        operation = self._parking_operation()
        self._parking_pair(operation, "N457UP", aircraft_type="757")
        db.session.commit()

        with patch("app.neomotherbrain.routes.apply_parking_optimizer_plan") as apply_mock:
            apply_mock.return_value = {
                "ok": False,
                "message": "Optimizer returned UNKNOWN; no assignments were applied.",
                "preview": {"unassigned_tails": []},
                "skipped": [],
            }
            response = self.client.post(
                f"/motherbrain/parking-plan/{operation.id}/optimize/apply",
                data={"confirm_apply": "1"},
                follow_redirects=True,
            )

        self.assertIn(b"Optimizer returned UNKNOWN", response.data)
        self.assertEqual(SortDateParkingAssignment.query.count(), 0)

    def test_parking_optimizer_apply_preserves_manual_locked_assignments(self):
        operation = self._parking_operation()
        self._parking_pair(operation, "N457UP", aircraft_type="757")
        self._parking_pair(operation, "N349UP", aircraft_type="757", destination="SDF")
        db.session.flush()
        self._parking_assignment(operation, "N457UP", "A01")
        db.session.commit()

        self.client.post(
            f"/motherbrain/parking-plan/{operation.id}/optimize/apply",
            data={"confirm_apply": "1"},
            follow_redirects=True,
        )

        locked = self._parking_assignment_for_tail(operation, "N457UP")
        suggested = self._parking_assignment_for_tail(operation, "N349UP")
        self.assertEqual((locked.position_code, locked.lane_number), ("A01", 1))
        self.assertEqual((suggested.position_code, suggested.lane_number), ("B01", 1))

    def test_parking_optimizer_apply_does_not_overwrite_manual_assignment_created_after_preview(self):
        operation = self._parking_operation()
        self._parking_pair(operation, "N457UP", aircraft_type="757")
        db.session.commit()
        preview = self._parking_optimizer_preview(operation)
        self.assertEqual(preview["suggested_assignments"][0]["label"], "A01 Slot 1")
        self._parking_assignment(operation, "N457UP", "B01")
        db.session.commit()

        response = self.client.post(
            f"/motherbrain/parking-plan/{operation.id}/optimize/apply",
            data={"confirm_apply": "1"},
            follow_redirects=True,
        )
        assignment = self._parking_assignment_for_tail(operation, "N457UP")

        self.assertIn(b"No candidate parking positions", response.data)
        self.assertEqual((assignment.position_code, assignment.lane_number), ("B01", 1))

    def test_parking_optimizer_apply_does_not_create_duplicate_tail_assignments(self):
        operation = self._parking_operation()
        self._parking_pair(operation, "N457UP", aircraft_type="757")
        db.session.commit()

        self.client.post(
            f"/motherbrain/parking-plan/{operation.id}/optimize/apply",
            data={"confirm_apply": "1"},
            follow_redirects=True,
        )
        self.client.post(
            f"/motherbrain/parking-plan/{operation.id}/optimize/apply",
            data={"confirm_apply": "1"},
            follow_redirects=True,
        )

        self.assertEqual(
            SortDateParkingAssignment.query.filter_by(
                sort_date_operation_id=operation.id,
                tail_number="N457UP",
            ).count(),
            1,
        )

    def test_parking_optimizer_apply_ignores_cancelled_missions(self):
        operation = self._parking_operation()
        arrival, departure = self._parking_pair(operation, "N457UP", aircraft_type="757")
        arrival.arrival_status = "cancelled"
        departure.departure_status = "cancelled"
        db.session.commit()

        response = self.client.post(
            f"/motherbrain/parking-plan/{operation.id}/optimize/apply",
            data={"confirm_apply": "1"},
            follow_redirects=True,
        )

        self.assertIn(b"No candidate parking positions", response.data)
        self.assertEqual(SortDateParkingAssignment.query.count(), 0)

    def test_parking_optimizer_apply_is_scoped_to_selected_operation(self):
        operation = self._parking_operation()
        self._parking_pair(operation, "N457UP", aircraft_type="757")
        other_operation = self._operation(sort_date=date(2026, 6, 19))
        db.session.add(other_operation)
        db.session.flush()
        self._parking_pair(other_operation, "N349UP", aircraft_type="757", destination="SDF")
        db.session.commit()

        self.client.post(
            f"/motherbrain/parking-plan/{operation.id}/optimize/apply",
            data={"confirm_apply": "1"},
            follow_redirects=True,
        )

        self.assertIsNotNone(self._parking_assignment_for_tail(operation, "N457UP"))
        self.assertIsNone(self._parking_assignment_for_tail(other_operation, "N349UP"))

    def test_parking_optimizer_apply_surfaces_physical_validator_conflicts(self):
        operation = self._parking_operation()
        self._parking_pair(operation, "N457UP", aircraft_type="757")
        self._parking_pair(operation, "N349UP", aircraft_type="757", destination="SDF")
        db.session.flush()
        self._parking_assignment(operation, "N457UP", "A03")
        db.session.commit()

        response = self.client.post(
            f"/motherbrain/parking-plan/{operation.id}/optimize/apply",
            data={"confirm_apply": "1"},
            follow_redirects=True,
        )

        self.assertIn(b"PHYSICAL PARKING RULES", response.data)
        self.assertIn(b"A03 cannot be used until", response.data)
        self.assertIn(b"are filled.", response.data)
        self.assertGreaterEqual(MotherBrainAlert.query.filter_by(active=True).count(), 1)

    def test_parking_plan_ramp_layout_renders_physical_rows_and_slots(self):
        operation = self._parking_operation()
        self._parking_pair(operation, "N457UP", destination="LAX")
        db.session.commit()

        response = self.client.get(f"/motherbrain/parking-plan/{operation.id}")
        html = response.data.decode()
        ramp_html = html.split('class="parking-ramp-layout"', 1)[1]

        self.assertLess(ramp_html.index("ALPHA"), ramp_html.index("BRAVO"))
        self.assertLess(ramp_html.index("BRAVO"), ramp_html.index("CHARLIE"))
        self.assertLess(ramp_html.index("CHARLIE"), ramp_html.index("DELTA"))
        self.assertLess(ramp_html.index("DELTA"), ramp_html.index("ECHO"))
        self.assertLess(ramp_html.index("ECHO"), ramp_html.index("REMOTE"))
        self.assertIn('data-ramp-code="A"', html)
        self.assertIn('data-ramp-code="B"', html)
        self.assertIn('data-ramp-code="C"', html)
        self.assertIn('data-ramp-code="D"', html)
        self.assertIn('data-ramp-code="E"', html)
        self.assertIn('data-ramp-code="R"', html)
        alpha_section = ramp_html.split('data-ramp-code="A"', 1)[1].split("</article>", 1)[0]
        bravo_section = ramp_html.split('data-ramp-code="B"', 1)[1].split("</article>", 1)[0]
        charlie_section = ramp_html.split('data-ramp-code="C"', 1)[1].split("</article>", 1)[0]
        delta_section = ramp_html.split('data-ramp-code="D"', 1)[1].split("</article>", 1)[0]
        echo_section = ramp_html.split('data-ramp-code="E"', 1)[1].split("</article>", 1)[0]
        remote_section = ramp_html.split('data-ramp-code="R"', 1)[1].split("</article>", 1)[0]
        self.assertIn('data-ramp-row="1"', alpha_section)
        self.assertIn('data-ramp-column="1"', alpha_section)
        self.assertIn('data-ramp-row="1"', bravo_section)
        self.assertIn('data-ramp-column="2"', bravo_section)
        self.assertIn('data-ramp-row="2"', charlie_section)
        self.assertIn('data-ramp-column="1"', charlie_section)
        self.assertIn('data-ramp-row="2"', delta_section)
        self.assertIn('data-ramp-column="2"', delta_section)
        self.assertIn('data-ramp-row="3"', echo_section)
        self.assertIn('data-ramp-column="1"', echo_section)
        self.assertIn('data-ramp-row="3"', remote_section)
        self.assertIn('data-ramp-column="2"', remote_section)
        self.assertIn("parking-ramp-a", html)
        self.assertIn("parking-ramp-b", html)
        self.assertIn("parking-ramp-c", html)
        self.assertIn("parking-ramp-d", html)
        self.assertIn("parking-ramp-e", html)
        self.assertIn("parking-ramp-r", html)
        self.assertIn('data-ramp-layout="standard"', html)
        self.assertIn('data-ramp-layout="remote"', html)
        self.assertIn("parking-ramp-center", html)
        self.assertEqual(html.count("parking-ramp-center"), 6)
        self.assertNotIn("parking-ramp-heading", html)
        self.assertIn("parking-position-left parking-position-slot-left-1", html)
        self.assertIn("parking-position-left parking-position-slot-left-4", html)
        self.assertIn("parking-position-right parking-position-slot-right-1", html)
        self.assertIn("parking-position-right parking-position-slot-right-4", html)
        self.assertIn("parking-position-top parking-position-slot-top", html)
        self.assertIn("parking-position-bottom parking-position-slot-bottom", html)
        self.assertIn("parking-position-remote-top parking-position-slot-remote-top-left", html)
        self.assertIn("parking-position-remote-top parking-position-slot-remote-top-right", html)
        self.assertIn("parking-position-remote-bottom parking-position-slot-remote-bottom-left", html)
        self.assertIn("parking-position-remote-bottom parking-position-slot-remote-bottom-right", html)
        self.assertIn('data-position-side="left"', html)
        self.assertIn('data-position-side="top"', html)
        self.assertIn('data-position-side="bottom"', html)
        self.assertIn('data-position-slot="right-1"', html)
        self.assertIn('data-position-slot="remote-bottom-right"', html)
        remote_html = html.split('class="parking-ramp-group parking-ramp-r"', 1)[1]
        self.assertLess(remote_html.index("R04"), remote_html.index("R03"))
        self.assertLess(remote_html.index("R03"), remote_html.index("R02"))
        self.assertLess(remote_html.index("R02"), remote_html.index("R01"))

    def test_parking_plan_drag_drop_and_mobile_action_order_render(self):
        operation = self._parking_operation()
        self._parking_pair(operation, "N457UP", destination="LAX")
        db.session.commit()

        response = self.client.get(f"/motherbrain/parking-plan/{operation.id}")
        html = response.data.decode()
        mobile_html = html.split('class="parking-mobile-assignment"', 1)[1].split(
            'class="parking-unassigned"', 1
        )[0]

        self.assertIn("data-parking-lane", html)
        self.assertIn("data-parking-unassign-drop", html)
        self.assertIn("page.dataset.unassignUrl", html)
        self.assertIn("Parking unassign failed.", html)
        self.assertIn("data-parking-selection-status", html)
        self.assertIn("data-clear-selected-tail", html)
        self.assertIn("setSelectedTail", html)
        self.assertIn("assignTailToLane(lane, selectedTail)", html)
        self.assertIn("is-expanded-slot", html)
        self.assertIn('data-ramp-code="A"', html)
        self.assertIn('data-position-code="A01"', html)
        self.assertIn('data-lane-number="1"', html)
        self.assertIn('data-slot-number="1"', html)
        self.assertIn('data-slot-collapsed="1"', html)
        self.assertIn("parking-lane-slot-2 is-collapsed-slot", html)
        self.assertIn("parking-slot-expand", html)
        self.assertIn('data-occupied-tail=""', html)
        self.assertIn("parking-mobile-assign-controls", mobile_html)
        self.assertIn("parking-mobile-hot-note-controls", mobile_html)
        self.assertIn("parking-mobile-remove-controls", mobile_html)
        self.assertIn("SLOT 1", mobile_html)
        self.assertIn("SLOT 2", mobile_html)
        self.assertNotIn("LANE 1", mobile_html)
        self.assertIn("REPLACE SLOT 1", html)
        self.assertIn("USE SLOT 2", html)
        self.assertLess(mobile_html.index("ASSIGN / MOVE"), mobile_html.index("HOT / NOTE"))
        self.assertLess(mobile_html.index("HOT / NOTE"), mobile_html.index("REMOVE"))
        self.assertLess(mobile_html.index("ASSIGN TAIL"), mobile_html.index("SAVE HOT / NOTE"))
        self.assertLess(mobile_html.index("SAVE HOT / NOTE"), mobile_html.index("REMOVE / UNASSIGN"))

    def test_parking_plan_slot_two_collapses_until_needed(self):
        operation = self._parking_operation()
        self._parking_pair(operation, "N457UP", destination="LAX")
        self._parking_pair(operation, "N349UP", destination="ONT")
        db.session.commit()

        empty_response = self.client.get(f"/motherbrain/parking-plan/{operation.id}")
        empty_html = empty_response.data.decode()

        self.assertIn("parking-lane-slot-1", empty_html)
        self.assertIn("parking-lane-slot-2 is-collapsed-slot", empty_html)
        self.assertIn('data-slot-collapsed="1"', empty_html)
        self.assertIn("parking-slot-expand", empty_html)
        self.assertIn("Show A01 Slot 2", empty_html)

        self.client.post(
            f"/motherbrain/parking-plan/{operation.id}/assign",
            data={
                "tail_number": "N457UP",
                "ramp_code": "A",
                "position_code": "A01",
                "lane_number": "1",
            },
        )
        slot_one_response = self.client.get(f"/motherbrain/parking-plan/{operation.id}")
        slot_one_html = slot_one_response.data.decode()
        self.assertIn('data-slot-1-tail="N457UP"', slot_one_html)
        self.assertIn('data-slot-2-tail=""', slot_one_html)
        self.assertIn("REPLACE SLOT 1", slot_one_html)
        self.assertIn("USE SLOT 2", slot_one_html)

        self.client.post(
            f"/motherbrain/parking-plan/{operation.id}/assign",
            data={
                "tail_number": "N349UP",
                "ramp_code": "A",
                "position_code": "A01",
                "lane_number": "2",
            },
        )
        occupied_response = self.client.get(f"/motherbrain/parking-plan/{operation.id}")
        occupied_html = occupied_response.data.decode()
        slot_two_html = occupied_html.split('data-occupied-tail="N349UP"', 1)[0].rsplit(
            '<div',
            1,
        )[1]

        self.assertIn("parking-lane-slot-2", slot_two_html)
        self.assertNotIn("is-collapsed-slot", slot_two_html)

    def test_parking_plan_click_to_assign_hooks_render(self):
        operation = self._parking_operation()
        self._parking_pair(operation, "N457UP", destination="LAX")
        self._parking_pair(operation, "N349UP", destination="ONT")
        db.session.commit()

        self.client.post(
            f"/motherbrain/parking-plan/{operation.id}/assign",
            data={
                "tail_number": "N457UP",
                "ramp_code": "A",
                "position_code": "A01",
                "lane_number": "1",
            },
        )

        response = self.client.get(f"/motherbrain/parking-plan/{operation.id}")
        html = response.data.decode()

        self.assertIn("data-parking-selection-status", html)
        self.assertIn("data-selected-tail-label", html)
        self.assertIn("data-clear-selected-tail", html)
        self.assertIn("data-parking-tail-assigned=\"0\"", html)
        self.assertIn("data-parking-tail-assigned=\"1\"", html)
        self.assertIn("card.dataset.parkingTailAssigned === \"1\"", html)
        self.assertIn("page.classList.toggle(\"has-selected-tail\"", html)
        self.assertIn("await assignTailToLane(lane, selectedTail)", html)
        self.assertIn("window.confirm(`${occupiedTail} is already", html)

    def test_parking_tail_card_compact_flags_and_order_render(self):
        operation = self._parking_operation()
        self._parking_pair(
            operation,
            "N457UP",
            arrival_local=datetime(2026, 6, 18, 23, 50),
            departure_local=datetime(2026, 6, 19, 0, 40),
            destination="LAX",
            aircraft_type="757",
        )
        db.session.commit()
        self.client.post(
            f"/motherbrain/parking-plan/{operation.id}/assign",
            data={
                "tail_number": "N457UP",
                "ramp_code": "A",
                "position_code": "A01",
                "lane_number": "1",
            },
        )
        self.client.post(
            f"/motherbrain/parking-plan/{operation.id}/hot",
            data={"tail_number": "N457UP", "is_hot": "1"},
        )

        response = self.client.get(f"/motherbrain/parking-plan/{operation.id}")
        html = response.data.decode()
        slot_html = html.split('data-occupied-tail="N457UP"', 1)[1].split(
            "</article>", 1
        )[0]

        self.assertIn("parking-tail-card is-quick-turn is-hot", slot_html)
        self.assertIn("parking-tail-badges", slot_html)
        self.assertIn("parking-badge parking-badge-qt", slot_html)
        self.assertIn("parking-badge parking-badge-hot", slot_html)
        self.assertIn('class="parking-order">1</span>', slot_html)
        self.assertIn("N457UP", slot_html)
        self.assertIn("ARR ARR57 ONT-RFD 23:50", slot_html)
        self.assertIn("DEP DEP57 RFD-LAX 00:40", slot_html)
        self.assertIn("GT 0:40", slot_html)
        self.assertNotIn("757", slot_html)

    def test_motherbrain_dashboard_and_menu_link_to_parking_plan(self):
        response = self.client.get("/motherbrain")
        html = response.data.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn('href="/motherbrain/parking-plan"', html)
        self.assertIn("PARKING PLAN", html)
        dashboard_html = html.split('class="motherbrain-dashboard-grid"', 1)[1]
        self.assertLess(dashboard_html.index("MANAGE SORT"), dashboard_html.index("PARKING PLAN"))

    def test_manage_sort_parking_plan_button_links_to_selected_operation_plan(self):
        operation = self._parking_operation(now=datetime(2026, 6, 18, 10, 0))
        self._parking_pair(operation, "N457UP", destination="LAX")
        db.session.commit()

        response = self.client.get("/motherbrain/manage-sort")

        self.assertEqual(response.status_code, 200)
        self.assertIn(f'href="/motherbrain/parking-plan/{operation.id}"'.encode(), response.data)

    def test_parking_plan_landing_lists_active_previous_day_overnight_sort_after_midnight(self):
        active_operation = self._parking_operation(now=datetime(2026, 6, 19, 0, 30))
        self._parking_pair(active_operation, "N457UP", destination="LAX")
        today_operation = self._operation(sort_date=date(2026, 6, 19), sort_name="day")
        db.session.add(today_operation)
        db.session.flush()
        self._parking_pair(
            today_operation,
            "N349UP",
            arrival_local=datetime(2026, 6, 19, 12, 10),
            departure_local=datetime(2026, 6, 19, 16, 0),
            destination="SDF",
        )
        db.session.commit()

        response = self.client.get("/motherbrain/parking-plan")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"ACTIVE / CURRENT", response.data)
        self.assertIn(b"NIGHT", response.data)
        self.assertIn(b"SORT DATE 2026-06-18", response.data)
        self.assertIn(b"DAY", response.data)
        self.assertIn(b"SORT DATE 2026-06-19", response.data)
        self.assertIn(f'href="/motherbrain/parking-plan/{active_operation.id}"'.encode(), response.data)
        self.assertIn(f'href="/motherbrain/parking-plan/{today_operation.id}"'.encode(), response.data)

    def test_parking_plan_selected_previous_day_overnight_sort_after_midnight(self):
        operation = self._parking_operation(now=datetime(2026, 6, 19, 0, 30))
        self._parking_pair(operation, "N457UP", destination="LAX")
        db.session.commit()

        response = self.client.get(f"/motherbrain/parking-plan/{operation.id}")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"NIGHT SORT DATE 2026-06-18", response.data)
        self.assertIn(b"N457UP", response.data)

    def test_parking_plan_shows_no_planned_sort_state(self):
        response = self.client.get("/motherbrain/parking-plan")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"NO PLANNED SORT OPERATIONS", response.data)
        self.assertIn(b"No planned sort operations found for this RFD local date.", response.data)
        self.assertNotIn(b"data-lane-number", response.data)

    def test_parking_assignment_saves_to_selected_operation_and_does_not_touch_master_schedule(self):
        operation = self._parking_operation(now=datetime(2026, 6, 18, 10, 0))
        self._parking_pair(operation, "N457UP")
        master = MasterFlightSchedule(
            gateway_id=self.rfd_gateway.id,
            gateway_code="RFD",
            sort_name="night",
            mission_type="departure",
            flight_number="UPS9999",
            origin="RFD",
            destination="LAX",
            active_days='["thursday"]',
            planned_time_local=time(1, 20),
            timezone="America/Chicago",
            active=True,
        )
        db.session.add(master)
        db.session.commit()

        response = self.client.post(
            f"/motherbrain/parking-plan/{operation.id}/assign",
            data={
                "tail_number": "N457UP",
                "ramp_code": "A",
                "position_code": "A01",
                "lane_number": "1",
            },
            headers={"Accept": "application/json"},
        )

        self.assertEqual(response.status_code, 200)
        assignment = SortDateParkingAssignment.query.one()
        self.assertEqual(assignment.sort_date_operation_id, operation.id)
        self.assertEqual(assignment.position_code, "A01")
        self.assertEqual(MasterFlightSchedule.query.count(), 1)
        self.assertEqual(db.session.get(MasterFlightSchedule, master.id).destination, "LAX")

    def test_tail_can_only_be_assigned_once_and_moves_slots(self):
        operation = self._parking_operation()
        self._parking_pair(operation, "N457UP")
        db.session.commit()

        self.client.post(
            f"/motherbrain/parking-plan/{operation.id}/assign",
            data={
                "tail_number": "N457UP",
                "ramp_code": "A",
                "position_code": "A01",
                "lane_number": "1",
            },
        )
        self.client.post(
            f"/motherbrain/parking-plan/{operation.id}/assign",
            data={
                "tail_number": "N457UP",
                "ramp_code": "B",
                "position_code": "B02",
                "lane_number": "2",
            },
        )

        assignments = SortDateParkingAssignment.query.all()
        self.assertEqual(len(assignments), 1)
        self.assertEqual(assignments[0].position_code, "B02")
        self.assertEqual(assignments[0].lane_number, 2)

    def test_occupied_slot_requires_confirmation_and_replaces_previous_tail(self):
        operation = self._parking_operation()
        self._parking_pair(operation, "N457UP", destination="ONT")
        self._parking_pair(operation, "N349UP", destination="LAX")
        db.session.commit()

        self.client.post(
            f"/motherbrain/parking-plan/{operation.id}/assign",
            data={
                "tail_number": "N457UP",
                "ramp_code": "A",
                "position_code": "A01",
                "lane_number": "1",
            },
        )
        conflict = self.client.post(
            f"/motherbrain/parking-plan/{operation.id}/assign",
            data={
                "tail_number": "N349UP",
                "ramp_code": "A",
                "position_code": "A01",
                "lane_number": "1",
            },
            headers={"Accept": "application/json"},
        )
        replaced = self.client.post(
            f"/motherbrain/parking-plan/{operation.id}/assign",
            data={
                "tail_number": "N349UP",
                "ramp_code": "A",
                "position_code": "A01",
                "lane_number": "1",
                "replace_occupied": "1",
            },
            headers={"Accept": "application/json"},
        )

        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.get_json()["occupied_tail"], "N457UP")
        self.assertEqual(replaced.status_code, 200)
        previous = SortDateParkingAssignment.query.filter_by(tail_number="N457UP").one()
        current = SortDateParkingAssignment.query.filter_by(tail_number="N349UP").one()
        self.assertIsNone(previous.position_code)
        self.assertEqual(current.position_code, "A01")
        self.assertEqual(current.lane_number, 1)

    def test_arrival_and_departure_boards_display_parking_plan_assignment(self):
        operation = self._parking_operation()
        self._parking_pair(operation, "N457UP", destination="ONT")
        db.session.commit()

        self.client.post(
            f"/motherbrain/parking-plan/{operation.id}/assign",
            data={
                "tail_number": "N457UP",
                "ramp_code": "A",
                "position_code": "A01",
                "lane_number": "1",
            },
        )

        arrival_response = self.client.get(
            f"/motherbrain/operations/{operation.id}/arrivals"
        )
        departure_response = self.client.get(
            f"/motherbrain/operations/{operation.id}/departures"
        )
        detail_response = self.client.get(f"/motherbrain/operations/{operation.id}")

        self.assertEqual(arrival_response.status_code, 200)
        self.assertEqual(departure_response.status_code, 200)
        self.assertIn(b"<td>A01</td>", arrival_response.data)
        self.assertIn(b"<td>A01</td>", departure_response.data)
        self.assertIn(b"<td>A01</td>", detail_response.data)

    def test_unassign_persists_and_boards_return_to_dash(self):
        operation = self._parking_operation()
        self._parking_pair(operation, "N457UP", destination="ONT")
        db.session.commit()

        self.client.post(
            f"/motherbrain/parking-plan/{operation.id}/assign",
            data={
                "tail_number": "N457UP",
                "ramp_code": "A",
                "position_code": "A01",
                "lane_number": "1",
            },
        )
        response = self.client.post(
            f"/motherbrain/parking-plan/{operation.id}/unassign",
            data={"tail_number": "N457UP"},
            headers={"Accept": "application/json"},
        )

        self.assertEqual(response.status_code, 200)
        assignment = SortDateParkingAssignment.query.filter_by(tail_number="N457UP").one()
        self.assertIsNone(assignment.position_code)
        plan_response = self.client.get(f"/motherbrain/parking-plan/{operation.id}")
        arrival_response = self.client.get(
            f"/motherbrain/operations/{operation.id}/arrivals"
        )
        departure_response = self.client.get(
            f"/motherbrain/operations/{operation.id}/departures"
        )
        self.assertIn(b"UNASSIGNED", plan_response.data)
        self.assertIn(b"N457UP", plan_response.data)
        self.assertIn(b"<td>-</td>", arrival_response.data)
        self.assertIn(b"<td>-</td>", departure_response.data)
        self.assertNotIn(b"<td>A01</td>", arrival_response.data)
        self.assertNotIn(b"<td>A01</td>", departure_response.data)

    def test_boards_display_same_position_for_two_slots(self):
        operation = self._parking_operation()
        self._parking_pair(operation, "N457UP", destination="ONT")
        self._parking_pair(operation, "N349UP", destination="LAX")
        db.session.commit()

        self.client.post(
            f"/motherbrain/parking-plan/{operation.id}/assign",
            data={
                "tail_number": "N457UP",
                "ramp_code": "A",
                "position_code": "A01",
                "lane_number": "1",
            },
        )
        self.client.post(
            f"/motherbrain/parking-plan/{operation.id}/assign",
            data={
                "tail_number": "N349UP",
                "ramp_code": "A",
                "position_code": "A01",
                "lane_number": "2",
            },
        )

        arrival_response = self.client.get(
            f"/motherbrain/operations/{operation.id}/arrivals"
        )
        departure_response = self.client.get(
            f"/motherbrain/operations/{operation.id}/departures"
        )

        self.assertGreaterEqual(arrival_response.data.count(b"<td>A01</td>"), 2)
        self.assertGreaterEqual(departure_response.data.count(b"<td>A01</td>"), 2)

    def test_parking_display_is_sort_operation_specific(self):
        first_operation = self._parking_operation()
        self._parking_pair(first_operation, "N457UP", destination="ONT")
        second_operation = self._operation(
            sort_date=date(2026, 6, 18),
            sort_name="day",
        )
        db.session.add(second_operation)
        db.session.flush()
        self._parking_pair(
            second_operation,
            "N457UP",
            arrival_local=datetime(2026, 6, 18, 12, 0),
            departure_local=datetime(2026, 6, 18, 16, 0),
            destination="LAX",
        )
        db.session.commit()

        self.client.post(
            f"/motherbrain/parking-plan/{first_operation.id}/assign",
            data={
                "tail_number": "N457UP",
                "ramp_code": "A",
                "position_code": "A01",
                "lane_number": "1",
            },
        )

        assigned_response = self.client.get(
            f"/motherbrain/operations/{first_operation.id}/arrivals"
        )
        unassigned_response = self.client.get(
            f"/motherbrain/operations/{second_operation.id}/arrivals"
        )

        self.assertIn(b"<td>A01</td>", assigned_response.data)
        self.assertNotIn(b"<td>A01</td>", unassigned_response.data)
        self.assertIn(b"<td>-</td>", unassigned_response.data)

    def test_unassigned_tail_board_parking_shows_dash(self):
        operation = self._parking_operation()
        self._parking_pair(operation, "N457UP", destination="ONT")
        db.session.commit()

        response = self.client.get(f"/motherbrain/operations/{operation.id}/arrivals")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"<td>-</td>", response.data)

    def test_unassign_and_hot_mark_unmark_work(self):
        operation = self._parking_operation()
        self._parking_pair(operation, "N457UP")
        db.session.commit()

        self.client.post(
            f"/motherbrain/parking-plan/{operation.id}/assign",
            data={
                "tail_number": "N457UP",
                "ramp_code": "A",
                "position_code": "A01",
                "lane_number": "1",
            },
        )
        self.client.post(
            f"/motherbrain/parking-plan/{operation.id}/hot",
            data={"tail_number": "N457UP", "is_hot": "1"},
        )
        assignment = SortDateParkingAssignment.query.filter_by(tail_number="N457UP").one()
        self.assertTrue(assignment.is_hot)

        self.client.post(
            f"/motherbrain/parking-plan/{operation.id}/unassign",
            data={"tail_number": "N457UP"},
        )
        self.client.post(
            f"/motherbrain/parking-plan/{operation.id}/hot",
            data={"tail_number": "N457UP", "is_hot": "0"},
        )
        assignment = SortDateParkingAssignment.query.filter_by(tail_number="N457UP").one()
        self.assertIsNone(assignment.position_code)
        self.assertFalse(assignment.is_hot)

    def test_arrival_time_uses_sta_then_api_eta_with_taxi_offset(self):
        operation = self._parking_operation()
        arrival, _departure = self._parking_pair(
            operation,
            "N457UP",
            arrival_local=datetime(2026, 6, 18, 23, 50),
            departure_local=datetime(2026, 6, 19, 1, 0),
        )
        db.session.commit()

        planned_response = self.client.get(f"/motherbrain/parking-plan/{operation.id}")
        self.assertIn(b"ONT 00:00", planned_response.data)

        arrival.eta_datetime_utc = datetime(2026, 6, 19, 5, 5)
        db.session.commit()
        api_response = self.client.get(f"/motherbrain/parking-plan/{operation.id}")

        self.assertIn(b"ONT 00:15", api_response.data)
        self.assertNotIn(b"05:15", api_response.data)

    def test_arrival_time_uses_api_block_in_without_double_taxi_offset(self):
        operation = self._parking_operation()
        arrival, _departure = self._parking_pair(
            operation,
            "N457UP",
            arrival_local=datetime(2026, 6, 18, 23, 50),
            departure_local=datetime(2026, 6, 19, 1, 0),
        )
        arrival.api_assumed_arrived_time_utc = datetime(2026, 6, 19, 5, 12)
        db.session.commit()

        response = self.client.get(f"/motherbrain/parking-plan/{operation.id}")

        self.assertIn(b"ONT 00:12", response.data)
        self.assertNotIn(b"ONT 00:22", response.data)

    def test_ground_time_across_midnight_and_quick_turn_thresholds(self):
        operation = self._parking_operation()
        self._parking_pair(
            operation,
            "N457UP",
            arrival_local=datetime(2026, 6, 18, 23, 50),
            departure_local=datetime(2026, 6, 19, 0, 40),
            aircraft_type="757",
        )
        self._parking_pair(
            operation,
            "N447UP",
            arrival_local=datetime(2026, 6, 18, 23, 50),
            departure_local=datetime(2026, 6, 19, 0, 50),
            aircraft_type="757",
            destination="SDF",
        )
        self._parking_pair(
            operation,
            "N171UP",
            arrival_local=datetime(2026, 6, 18, 23, 50),
            departure_local=datetime(2026, 6, 19, 1, 15),
            aircraft_type="A300",
            destination="EWR",
        )
        db.session.commit()

        response = self.client.get(f"/motherbrain/parking-plan/{operation.id}")
        html = response.data.decode()

        self.assertIn("GT 0:40", html)
        self.assertIn("GT 0:50", html)
        self.assertIn("GT 1:15", html)
        self.assertIn("N457UP", html)
        self.assertIn("N171UP", html)
        self.assertIn("QT", html)
        n447_section = html.split("<strong>N447UP</strong>", 1)[1].split("</article>", 1)[0]
        self.assertNotIn("QT", n447_section)

    def test_per_ramp_departure_order_is_calculated_by_ramp(self):
        operation = self._parking_operation()
        self._parking_pair(
            operation,
            "N457UP",
            departure_local=datetime(2026, 6, 19, 1, 20),
            destination="ONT",
        )
        self._parking_pair(
            operation,
            "N349UP",
            departure_local=datetime(2026, 6, 19, 0, 55),
            destination="LAX",
        )
        self._parking_pair(
            operation,
            "N171UP",
            departure_local=datetime(2026, 6, 19, 0, 50),
            destination="EWR",
        )
        db.session.commit()
        self.client.post(
            f"/motherbrain/parking-plan/{operation.id}/assign",
            data={"tail_number": "N457UP", "ramp_code": "A", "position_code": "A01", "lane_number": "1"},
        )
        self.client.post(
            f"/motherbrain/parking-plan/{operation.id}/assign",
            data={"tail_number": "N349UP", "ramp_code": "A", "position_code": "A02", "lane_number": "1"},
        )
        self.client.post(
            f"/motherbrain/parking-plan/{operation.id}/assign",
            data={"tail_number": "N171UP", "ramp_code": "B", "position_code": "B01", "lane_number": "1"},
        )

        response = self.client.get(f"/motherbrain/parking-plan/{operation.id}")
        html = response.data.decode()
        n349_card = html.split('data-occupied-tail="N349UP"', 1)[1].split("</article>", 1)[0]
        n457_card = html.split('data-occupied-tail="N457UP"', 1)[1].split("</article>", 1)[0]
        n171_card = html.split('data-occupied-tail="N171UP"', 1)[1].split("</article>", 1)[0]

        self.assertIn('class="parking-order">1</span>', n349_card)
        self.assertIn('class="parking-order">2</span>', n457_card)
        self.assertIn('class="parking-order">1</span>', n171_card)

    def _parking_operation(self, now=None):
        now = now or datetime(2026, 6, 18, 23, 30)
        self.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = now
        self._set_sort_window("night", time(22, 0), time(4, 0))
        settings = ensure_sort_timeline_settings(self.rfd_gateway)
        settings.taxi_to_ramp_minutes = 10
        operation = self._operation(sort_date=date(2026, 6, 18))
        db.session.add(operation)
        db.session.flush()
        return operation

    def _parking_pair(
        self,
        operation,
        tail_number,
        arrival_local=None,
        departure_local=None,
        origin="ONT",
        destination="LAX",
        aircraft_type="757",
    ):
        arrival_local = arrival_local or datetime(2026, 6, 18, 23, 50)
        departure_local = departure_local or datetime(2026, 6, 19, 1, 0)
        arrival = self._mission(
            operation=operation,
            mission_type="arrival",
            flight_number=f"ARR{tail_number[-4:-2]}",
            origin=origin,
            destination="RFD",
            assigned_tail_number=tail_number,
            planned_datetime_local=arrival_local,
            planned_datetime_utc=arrival_local.replace(tzinfo=timezone.utc).replace(tzinfo=None),
        )
        departure = self._mission(
            operation=operation,
            mission_type="departure",
            flight_number=f"DEP{tail_number[-4:-2]}",
            origin="RFD",
            destination=destination,
            assigned_tail_number=tail_number,
            planned_datetime_local=departure_local,
            planned_datetime_utc=departure_local.replace(tzinfo=timezone.utc).replace(tzinfo=None),
        )
        state = SortDateTailState(
            sort_date=operation.sort_date,
            gateway_code=operation.gateway_code,
            sort_name=operation.sort_name,
            tail_number=tail_number,
            aircraft_type=aircraft_type,
            aircraft_type_source="manual",
        )
        db.session.add_all([arrival, departure, state])
        return arrival, departure

    def _parking_assignment(self, operation, tail_number, position_code, ramp_code=None, lane_number=1):
        assignment = SortDateParkingAssignment(
            sort_date_operation_id=operation.id,
            tail_number=tail_number,
            ramp_code=ramp_code or str(position_code)[0],
            position_code=position_code,
            lane_number=lane_number,
        )
        db.session.add(assignment)
        return assignment

    def _parking_assignment_for_tail(self, operation, tail_number):
        return SortDateParkingAssignment.query.filter_by(
            sort_date_operation_id=operation.id,
            tail_number=tail_number,
        ).first()

    def _parking_rule(
        self,
        category,
        subject_type,
        subject_value,
        ramp_code,
        behavior="forbidden",
    ):
        rule = MotherBrainParkingRule(
            gateway_id=self.rfd_gateway.id,
            gateway_code=self.rfd_gateway.code,
            rule_category=category,
            subject_type=subject_type,
            subject_value=subject_value,
            ramp_code=ramp_code,
            rule_behavior=behavior,
            active=True,
        )
        db.session.add(rule)
        return rule

    def _parking_optimizer_preview(
        self,
        operation,
        include_remote=False,
        include_throat=False,
    ):
        context = parking_plan_context(self.rfd_gateway, operation=operation)
        return parking_optimizer_preview(
            self.rfd_gateway,
            operation,
            include_remote=include_remote,
            include_throat=include_throat,
            tail_rows=context["tail_rows"],
        )

    def _master_schedule_form_data(self, **overrides):
        values = {
            "gateway_code": "RFD",
            "sort_name": "night",
            "mission_type": "departure",
            "wave": "1",
            "flight_number": "DEP001",
            "origin": "RFD",
            "destination": "SDF",
            "active_days": ["monday", "tuesday"],
            "planned_time_local": "02:10",
            "timezone": "America/Chicago",
            "preferred_parking": "",
            "pure_pull_time_local": "",
            "first_mix_pull_time_local": "",
            "final_mix_pull_time_local": "",
            "active": True,
        }
        values.update(overrides)
        active = values.pop("active")
        if active:
            values["active"] = "1"
        return values

    def _sort_timeline_form_data(self, **overrides):
        values = {
            "month_key": "2026-06",
            "monthly_api_units": "600",
            "units_per_poll": "2",
            "taxi_to_ramp_minutes": "10",
            "minimum_auto_poll_interval_minutes": "10",
            "provider_name": "",
            "api_key_env_var_name": "",
        }
        for month_number in range(1, 13):
            values[f"month_variance_{month_number}"] = "0"
        for sort_name in ("sunrise", "day", "twilight", "night"):
            values.update(
                {
                    f"{sort_name}_sort_start": "",
                    f"{sort_name}_sort_end": "",
                    f"{sort_name}_ops_start": "",
                    f"{sort_name}_ops_end": "",
                    f"{sort_name}_polling_start": "",
                    f"{sort_name}_polling_end": "",
                    f"{sort_name}_special_poll_time": [],
                    f"{sort_name}_delete_special_poll_time": [],
                }
            )
        values.update(overrides)
        values = {
            key: value
            for key, value in values.items()
            if value is not None
        }
        return values

    def _login_motherbrain_role(self, username, role):
        self.client.get("/logout")
        user = User(username=username, role=role)
        user.set_password("TestPassword123!")
        db.session.add(user)
        db.session.flush()
        backfill_default_gateway_node_roles(user, role=role)
        db.session.commit()
        self.client.post(
            "/login",
            data={"username": username, "password": "TestPassword123!"},
        )
        return user

    def _bulk_master_schedule_form_data(self, *rows):
        data = {"row_indexes": [str(index) for index in range(len(rows))]}
        for index, overrides in enumerate(rows):
            values = self._master_schedule_form_data(**overrides)
            row_id = overrides.get("id", "")
            active_days = values.pop("active_days", [])
            active = values.pop("active", None)
            values.pop("gateway_code", None)
            values.pop("timezone", None)
            values.pop("preferred_parking", None)

            prefix = f"row_{index}_"
            data[f"{prefix}id"] = row_id
            for key, value in values.items():
                data[f"{prefix}{key}"] = value
            data[f"{prefix}active_days"] = active_days
            if active:
                data[f"{prefix}active"] = "1"
        return data

    def _mission_form_data(self, **overrides):
        values = {
            "mission_type": "departure",
            "flight_number": "DEP001",
            "origin": "RFD",
            "destination": "SDF",
            "assigned_tail_number": "",
            "planned_time_local": "02:10",
            "timezone": "America/Chicago",
            "eta_datetime_utc": "",
            "actual_block_in_datetime_utc": "",
            "actual_block_out_datetime_utc": "",
            "planned_fuel_load": "",
            "fuel_status": "",
            "departure_status": "",
            "pure_pull_time_local": "",
            "first_mix_pull_time_local": "",
            "final_mix_pull_time_local": "",
        }
        values.update(overrides)
        return values

    def _operation(self, **overrides):
        values = {
            "sort_date": date(2026, 6, 1),
            "gateway_id": self.rfd_gateway.id,
            "gateway_code": "RFD",
            "sort_name": "night",
        }
        values.update(overrides)
        return SortDateOperation(**values)

    def _set_sort_window(self, sort_name, start_time, end_time):
        settings = ensure_sort_timeline_settings(self.rfd_gateway)
        sort_setting = next(
            setting
            for setting in settings.sort_settings
            if setting.sort_name == sort_name
        )
        sort_setting.sort_window_start_local = start_time
        sort_setting.sort_window_end_local = end_time
        db.session.flush()

    def _operation_with_missions(self):
        operation = self._operation()
        db.session.add(operation)
        db.session.add(
            self._mission(
                operation=operation,
                mission_type="arrival",
                flight_number="ARR001",
                origin="SDF",
                destination="RFD",
            )
        )
        db.session.add(
            self._mission(
                operation=operation,
                mission_type="departure",
                flight_number="DEP999",
                origin="RFD",
                destination="SDF",
            )
        )
        return operation

    def _mission(self, operation, mission_type, flight_number, **overrides):
        values = {
            "sort_date_operation": operation,
            "sort_date": operation.sort_date,
            "gateway_code": operation.gateway_code,
            "sort_name": operation.sort_name,
            "mission_type": mission_type,
            "mission_source": "manual",
            "wave": "1",
            "flight_number": flight_number.upper(),
            "origin": "SDF" if mission_type == "arrival" else "RFD",
            "destination": "RFD" if mission_type == "arrival" else "SDF",
            "planned_datetime_local": datetime(2026, 6, 1, 2, 10),
            "planned_datetime_utc": datetime(2026, 6, 1, 7, 10),
        }
        values.update(overrides)
        return SortDateMission(**values)

    def _api_review_item(self, operation, mission_type="arrival", flight_number="UPS0999", **overrides):
        if operation.id is None:
            db.session.flush()
        origin = overrides.get(
            "origin",
            "SDF" if mission_type == "arrival" else operation.gateway_code,
        )
        destination = overrides.get(
            "destination",
            operation.gateway_code if mission_type == "arrival" else "SDF",
        )
        revised_time_utc = overrides.get("revised_time_utc", datetime(2026, 6, 24, 7, 24))
        local_time = "2026-06-24 02:24"
        if isinstance(revised_time_utc, datetime):
            # The route tests only need a provider-like payload shape; review fields
            # below remain the source of truth for exact stored UTC values.
            local_time = revised_time_utc.strftime("%Y-%m-%d %H:%M")
        raw_payload = json.dumps(
            {
                "_mission_type": mission_type,
                "number": flight_number,
                "callSign": flight_number,
                "airline": {"icao": "UPS", "iata": "5X"},
                "departure": {
                    "airport": {"iata": origin},
                    "revisedTime": {"local": local_time},
                },
                "arrival": {
                    "airport": {"iata": destination},
                    "revisedTime": {"local": local_time},
                },
                "aircraft": {
                    "reg": overrides.get("tail_number", "N999UP"),
                    "model": overrides.get("aircraft_model", "B763"),
                },
                "status": overrides.get("api_status", "Scheduled"),
            },
            sort_keys=True,
        )
        values = {
            "sort_date_operation_id": operation.id,
            "gateway_id": operation.gateway_id,
            "gateway_code": operation.gateway_code,
            "sort_date": operation.sort_date,
            "sort_name": operation.sort_name,
            "mission_type": mission_type,
            "review_key": f"api:{mission_type}:{flight_number}:{origin}:{destination}",
            "review_status": "pending",
            "flight_number": flight_number,
            "call_sign": flight_number,
            "origin": origin,
            "destination": destination,
            "revised_time_utc": revised_time_utc,
            "tail_number": "N999UP",
            "aircraft_model": "B763",
            "api_status": "Scheduled",
            "raw_payload": raw_payload,
        }
        values.update(overrides)
        return FlightApiReviewItem(**values)


if __name__ == "__main__":
    unittest.main()
