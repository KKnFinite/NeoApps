from datetime import date, datetime, time, timezone
from pathlib import Path
import unittest

from app import create_app
from app.extensions import db
from app.models import (
    Gateway,
    GatewayMembership,
    GatewaySortMatrix,
    MasterFlightSchedule,
    SortTimelineApiParticipation,
    SortTimelineMonthVariance,
    SortTimelineSettings,
    SortTimelineSpecialPollTime,
    SortTimelineUsageCounter,
    SortDateCrewAssignment,
    SortDateMission,
    SortDateOperation,
    SortDateTailState,
    User,
)
from app.services.access_control import backfill_default_gateway_node_roles
from app.services.gateway_matrix import current_gateway_local_date
from app.services.gateway_matrix import current_operations_for_gateway
from app.services.night_sorting import night_sort_time_key
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
        self.assertIn(b"USER MANAGEMENT", response.data)
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
        self.assertLess(dashboard_html.index("GATEWAY MATRIX"), dashboard_html.index("USER MANAGEMENT"))
        self.assertLess(dashboard_html.index("USER MANAGEMENT"), dashboard_html.index("PERMISSION RULES"))
        nav_html = html.split('id="motherbrain-mobile-menu"', 1)[1].split("</nav>", 1)[0]
        self.assertLess(nav_html.index("MANAGE SORT"), nav_html.index("MASTER SCHEDULE"))
        self.assertLess(nav_html.index("MASTER SCHEDULE"), nav_html.index("GATEWAY MATRIX"))
        self.assertLess(nav_html.index("GATEWAY MATRIX"), nav_html.index("USER MANAGEMENT"))
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
        self.assertIn(b'href="/admin/users"', response.data)
        self.assertIn(b'href="/admin/permissions"', response.data)
        self.assertIn(b'href="/motherbrain/gateway-matrix"', response.data)
        self.assertIn(b'href="/motherbrain/master-schedule"', response.data)
        self.assertIn(b'href="/motherbrain/manage-sort"', response.data)
        self.assertIn(b'href="/logout"', response.data)
        self.assertNotIn(b"Access Requests", response.data)
        self.assertNotIn(b"Generate Nightly Operation", response.data)

    def test_motherbrain_header_navigation_routes_work(self):
        routes = {
            "/admin/users": b'href="/admin/users" aria-current="page"',
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
                self.assertIn(b"USER MANAGEMENT", response.data)
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
        self.assertNotIn('href="/motherbrain/gateway-matrix"', workflow_html)
        self.assertNotIn('href="/motherbrain/master-schedule"', workflow_html)
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
        self.assertIn(b"<script>", response.data)
        self.assertIn(b"NeoAppsTimeInputs", response.data)
        self.assertIn(b'padStart(2, "0")', response.data)
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
        self.assertNotIn(b'name="row_arrival_new_flight_number"', response.data)
        self.assertNotIn(b'name="row_departure_new_flight_number"', response.data)
        self.assertNotIn(b"New arrival", response.data)
        self.assertNotIn(b"New departure", response.data)
        self.assertNotIn(b"SAVE MASTER ARRIVALS", response.data)
        self.assertNotIn(b"SAVE MASTER DEPARTURES", response.data)
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
        self.assertIn(b"<script>", form_response.data)
        self.assertIn(b"NeoAppsTimeInputs", form_response.data)
        self.assertIn(b'padStart(2, "0")', form_response.data)
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
        self.assertIn(b"Crew Load Complete", response.data)
        self.assertIn(b">STATUS<", response.data)
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


if __name__ == "__main__":
    unittest.main()
