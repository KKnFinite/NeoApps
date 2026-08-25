from contextlib import ExitStack
from datetime import date, datetime, timedelta
from pathlib import Path
import re
import unittest
from unittest.mock import Mock, patch

from flask import g
from sqlalchemy import event, inspect

from app import create_app
from app.extensions import db
from app.models import (
    PermissionRule,
    PortalAppAccess,
    StaffingLeadershipAssignment,
    StaffingPerson,
    StaffingUnit,
    StaffingVacationManagementCapacity,
    StaffingVacationManagementWeekOverride,
    StaffingVacationUnionCalendar,
    StaffingVacationUnionCalendarScope,
    StaffingWorkAssignment,
    User,
)
from app.services import neostaffing_vacation as vacation_service
from app.services.neostaffing_vacation_schema import (
    NEOSTAFFING_VACATION_MODELS,
    NEOSTAFFING_VACATION_SCHEMA_LOCK_KEY,
    ensure_neostaffing_vacation_tables,
)
from app.services.password_policy import set_user_password
from app.services.permission_rules import ensure_default_permission_rules


class NeoStaffingVacationSelectionTest(unittest.TestCase):
    YEAR = 2027

    def setUp(self):
        self.config = type(
            "VacationSelectionConfig",
            (),
            {
                "SECRET_KEY": "test",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            },
        )
        self.app = create_app(self.config)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        ensure_default_permission_rules()
        self.client = self.app.test_client()
        self.units = self._hierarchy()
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_vacation_year_weeks_are_sunday_saturday_and_identified_by_saturday(self):
        weeks = vacation_service.vacation_year_weeks(2027)

        self.assertIn(len(weeks), {52, 53})
        self.assertEqual(weeks[0].week_ending, date(2027, 1, 2))
        self.assertTrue(all(row.start_date.weekday() == 6 for row in weeks))
        self.assertTrue(all(row.week_ending.weekday() == 5 for row in weeks))
        self.assertTrue(all((row.week_ending - row.start_date).days == 6 for row in weeks))
        self.assertEqual(vacation_service.vacation_selection_opens_on(2027), date(2026, 11, 1))

    def test_management_context_is_dynamic_and_primary_assignment_owns_pool(self):
        primary = self._person("M100", "Alpha", "Supervisor", "2000-01-01", "full_time_supervisor")
        later = self._person("M101", "Zulu", "Supervisor", "1999-01-01", "full_time_supervisor")
        db.session.add_all(
            [
                StaffingLeadershipAssignment(
                    person=primary,
                    unit=self.units["blue_department"],
                    leadership_level="department",
                    active=True,
                ),
                StaffingLeadershipAssignment(
                    person=primary,
                    unit=self.units["other_department"],
                    leadership_level="department",
                    active=True,
                ),
                StaffingLeadershipAssignment(
                    person=later,
                    unit=self.units["blue_department"],
                    leadership_level="department",
                    active=True,
                ),
            ]
        )
        db.session.commit()
        viewer = self._user("dynamic_viewer", "watcher")

        result = vacation_service.management_vacation_context(self.YEAR, viewer)
        ramp_row = next(row for row in result["areas"] if row["area"].id == self.units["ramp"].id)
        hub_row = next(row for row in result["areas"] if row["area"].id == self.units["other_operation"].id)

        self.assertTrue(result["is_dynamic"])
        self.assertEqual([person.employee_id for person in ramp_row["people"]], ["M101", "M100"])
        self.assertIn(primary, hub_row["secondary_people"])
        self.assertFalse(any("management_calendar" in name for name in inspect(db.engine).get_table_names()))

    def test_seniority_ties_use_last_then_first_name(self):
        same_date = "2001-02-03"
        people = [
            self._person("S1", "Zed", "Able", same_date, "part_time"),
            self._person("S2", "Amy", "Baker", same_date, "part_time"),
            self._person("S3", "Amy", "Able", same_date, "part_time"),
        ]
        for person in people:
            db.session.add(StaffingWorkAssignment(person=person, work_area=self.units["blue_area"], active=True))
        calendar = self._calendar(self._user("order_gm", "grandmaster"), [self.units["blue_area"].id])
        db.session.commit()

        self.assertEqual(
            [person.employee_id for person in vacation_service.union_calendar_members(calendar)],
            ["S3", "S1", "S2"],
        )

    def test_union_calendar_create_edit_and_canonical_tri_state_scope(self):
        user = self._user("calendar_gm", "grandmaster")
        calendar = vacation_service.create_union_calendar(
            {
                "vacation_year": self.YEAR,
                "name": "Ramp Union",
                "operation_unit_id": self.units["ramp"].id,
                "include_part_time": "1",
                "staffing_unit_ids": [
                    self.units["blue_department"].id,
                    self.units["blue_area"].id,
                ],
                "active": "1",
            },
            user,
        )
        db.session.commit()

        self.assertEqual({scope.staffing_unit_id for scope in calendar.scopes}, {self.units["blue_department"].id})
        tree = vacation_service.union_scope_tree(
            self.units["ramp"].id,
            {self.units["blue_area"].id},
        )
        blue = next(node for node in tree["children"] if node["unit"].id == self.units["blue_department"].id)
        self.assertTrue(tree["indeterminate"])
        self.assertTrue(blue["indeterminate"])

        vacation_service.update_union_calendar(
            calendar,
            {
                "vacation_year": self.YEAR,
                "name": "Ramp Union Updated",
                "operation_unit_id": self.units["ramp"].id,
                "include_part_time": "1",
                "include_full_time": "1",
                "staffing_unit_ids": [self.units["blue_area"].id],
                "active": "1",
            },
            user,
        )
        db.session.commit()
        self.assertEqual(calendar.name, "Ramp Union Updated")
        self.assertTrue(calendar.include_full_time)
        self.assertEqual({scope.staffing_unit_id for scope in calendar.scopes}, {self.units["blue_area"].id})

    def test_union_membership_filters_pt_ft_seasonal_non_domiciled_and_inactive(self):
        people = {
            "pt": self._person("U1", "PT", "Union", "2000-01-01", "part_time"),
            "legacy_ft": self._person("U2", "Legacy", "FT", "2000-01-02", "full_time_combo"),
            "domiciled_ft": self._person("U3", "Domiciled", "FT", "2000-01-03", "domiciled_full_time_combo"),
            "non_domiciled": self._person("U4", "Non", "Domiciled", "2000-01-04", "non_domiciled_full_time_combo"),
            "seasonal": self._person("U5", "Seasonal", "Worker", "2000-01-05", "seasonal"),
            "inactive": self._person("U6", "Inactive", "Worker", "2000-01-06", "part_time", active=False),
        }
        for person in people.values():
            db.session.add(StaffingWorkAssignment(person=person, work_area=self.units["blue_area"], active=True))
        user = self._user("membership_gm", "grandmaster")
        both = self._calendar(user, [self.units["blue_department"].id], include_pt=True, include_ft=True)
        db.session.commit()

        self.assertEqual(
            {person.employee_id for person in vacation_service.union_calendar_members(both)},
            {"U1", "U2", "U3"},
        )
        both.include_full_time = False
        db.session.commit()
        self.assertEqual([person.employee_id for person in vacation_service.union_calendar_members(both)], ["U1"])
        both.include_part_time = False
        both.include_full_time = True
        db.session.commit()
        self.assertEqual(
            {person.employee_id for person in vacation_service.union_calendar_members(both)},
            {"U2", "U3"},
        )

    def test_normal_master_sideways_and_grandmaster_authority(self):
        normal_user = self._management_user("normal_ft", "simulator", self.units["blue_department"])
        master_user = self._management_user("master_ft", "master", self.units["blue_department"])
        grandmaster = self._user("authority_gm", "grandmaster")
        hierarchy = vacation_service.vacation_hierarchy()

        normal = vacation_service.vacation_actor(normal_user, hierarchy)
        master = vacation_service.vacation_actor(master_user, hierarchy)
        grand = vacation_service.vacation_actor(grandmaster, hierarchy)

        self.assertTrue(vacation_service.can_edit_union_scope(normal, {self.units["blue_area"].id}))
        self.assertFalse(vacation_service.can_edit_union_scope(normal, {self.units["brown_area"].id}))
        self.assertTrue(vacation_service.can_edit_union_scope(master, {self.units["brown_area"].id}))
        self.assertTrue(vacation_service.can_edit_union_scope(grand, {self.units["other_area"].id}))
        self.assertTrue(vacation_service.can_edit_management_capacity(normal, self.units["ramp"].id))
        self.assertFalse(vacation_service.can_edit_management_capacity(normal, self.units["brown_department"].id))
        self.assertTrue(vacation_service.can_edit_management_capacity(master, self.units["brown_department"].id))

    def test_management_capacity_save_and_prior_year_carry_forward(self):
        user = self._management_user("capacity_ft", "simulator", self.units["blue_department"])
        prior = vacation_service.save_management_capacity(
            self.YEAR - 1,
            self.units["ramp"].id,
            {"normal_limit": "5", "one_pinned_limit": "3", "two_plus_pinned_limit": "2"},
            user,
        )
        db.session.commit()

        created = vacation_service.initialize_management_capacity_year(
            self.YEAR,
            [self.units["ramp"].id],
            user,
        )
        db.session.commit()

        self.assertEqual(len(created), 1)
        self.assertEqual(
            (created[0].normal_limit, created[0].one_pinned_limit, created[0].two_plus_pinned_limit),
            (prior.normal_limit, prior.one_pinned_limit, prior.two_plus_pinned_limit),
        )
        self.assertEqual(vacation_service.initialize_management_capacity_year(self.YEAR, [self.units["ramp"].id], user), [])

    def test_reduced_capacity_defaults_on_sparse_off_and_does_not_carry_years(self):
        user = self._management_user("week_ft", "simulator", self.units["blue_department"])
        week = date(self.YEAR, 3, 6)
        self.assertTrue(vacation_service.reduced_capacity_enabled(self.YEAR, self.units["ramp"].id, week))

        vacation_service.set_reduced_capacity_enabled(self.YEAR, self.units["ramp"].id, week, False, user)
        db.session.commit()
        self.assertFalse(vacation_service.reduced_capacity_enabled(self.YEAR, self.units["ramp"].id, week))
        self.assertEqual(StaffingVacationManagementWeekOverride.query.count(), 1)
        next_year_week = next(
            row.week_ending
            for row in vacation_service.vacation_year_weeks(self.YEAR + 1)
            if row.week_ending.month == 3
        )
        self.assertTrue(vacation_service.reduced_capacity_enabled(self.YEAR + 1, self.units["ramp"].id, next_year_week))

        vacation_service.set_reduced_capacity_enabled(self.YEAR, self.units["ramp"].id, week, True, user)
        db.session.commit()
        self.assertEqual(StaffingVacationManagementWeekOverride.query.count(), 0)

    def test_union_capacity_percentages_boundaries_rounding_and_minimum(self):
        easter = vacation_service.easter_sunday(self.YEAR)
        first_seasonal = easter + timedelta(days=6)
        last_seasonal = vacation_service.labor_day(self.YEAR) + timedelta(days=5)

        self.assertEqual(vacation_service.union_whole_week_capacity(24, self.YEAR, first_seasonal).capacity, 4)
        self.assertEqual(vacation_service.union_whole_week_capacity(24, self.YEAR, first_seasonal).percentage, 17)
        self.assertEqual(vacation_service.union_whole_week_capacity(24, self.YEAR, first_seasonal - timedelta(days=7)).capacity, 2)
        self.assertEqual(vacation_service.union_whole_week_capacity(24, self.YEAR, last_seasonal).percentage, 17)
        self.assertEqual(vacation_service.union_whole_week_capacity(24, self.YEAR, last_seasonal + timedelta(days=7)).percentage, 12)
        self.assertEqual(vacation_service.union_whole_week_capacity(9, self.YEAR, first_seasonal).capacity, 1)
        self.assertEqual(vacation_service.union_single_day_capacity(39).capacity, 1)
        self.assertEqual(vacation_service.union_single_day_capacity(40).capacity, 2)
        self.assertEqual(vacation_service.union_single_day_capacity(0).capacity, 1)

    def test_union_context_uses_bounded_queries_as_calendar_count_grows(self):
        user = self._user("query_gm", "grandmaster")
        for index, area in enumerate((self.units["blue_area"], self.units["brown_area"], self.units["other_area"]), start=1):
            self._calendar(user, [area.id], name=f"Calendar {index}", operation=area.parent.parent)
        db.session.commit()
        statements = []

        def capture(_connection, _cursor, statement, _parameters, _context, _many):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            result = vacation_service.union_calendars_context(self.YEAR, user)
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)

        self.assertEqual(len(result["calendars"]), 3)
        self.assertLessEqual(len(statements), 6)

    def test_vacation_routes_render_separate_workspaces_and_navigation(self):
        user = self._user("route_gm", "grandmaster")
        self._login(user)

        landing = self.client.get(f"/neostaffing/vacation-selection?year={self.YEAR}")
        management = self.client.get(f"/neostaffing/vacation-selection/management?year={self.YEAR}")
        union = self.client.get(f"/neostaffing/vacation-selection/union?year={self.YEAR}")
        editor = self.client.get(f"/neostaffing/vacation-selection/union/new?year={self.YEAR}")

        self.assertEqual((landing.status_code, management.status_code, union.status_code, editor.status_code), (200, 200, 200, 200))
        self.assertIn(b"MANAGEMENT VACATION", landing.data)
        self.assertIn(b"UNION VACATION CALENDARS", landing.data)
        self.assertIn(b"DYNAMIC ORG CHART CONTEXT", management.data)
        self.assertIn(b"data-vacation-union-editor", editor.data)
        self.assertIn(b"SELECTING A PARENT SELECTS ALL CHILDREN", editor.data)
        self.assertIn(b'aria-current="page"', union.data)

    def test_route_mutations_require_csrf_and_server_authority(self):
        grandmaster = self._user("csrf_gm", "grandmaster")
        self._login(grandmaster)
        self.app.config["CSRF_PROTECT_TESTING"] = True
        page = self.client.get(f"/neostaffing/vacation-selection/union/new?year={self.YEAR}")
        self.assertEqual(page.status_code, 200, page.get_data(as_text=True))
        token = re.search(r'<meta name="csrf-token" content="([^"]+)">', page.get_data(as_text=True)).group(1)
        values = {
            "vacation_year": self.YEAR,
            "name": "CSRF Calendar",
            "operation_unit_id": self.units["ramp"].id,
            "include_part_time": "1",
            "staffing_unit_ids": self.units["blue_area"].id,
            "active": "1",
        }
        missing = self.client.post("/neostaffing/vacation-selection/union/new", data=values)
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(StaffingVacationUnionCalendar.query.count(), 0)
        values["csrf_token"] = token
        saved = self.client.post("/neostaffing/vacation-selection/union/new", data=values)
        self.assertEqual(saved.status_code, 302)
        self.assertEqual(StaffingVacationUnionCalendar.query.count(), 1)

        lower = self._user("unauthorized_union", "operator")
        self.app.config["CSRF_PROTECT_TESTING"] = False
        self._login(lower)
        self.app.config["CSRF_PROTECT_TESTING"] = True
        page = self.client.get(f"/neostaffing/vacation-selection/union?year={self.YEAR}")
        token = re.search(r'<meta name="csrf-token" content="([^"]+)">', page.get_data(as_text=True)).group(1)
        values.update({"name": "Forged Calendar", "csrf_token": token})
        forged = self.client.post("/neostaffing/vacation-selection/union/new", data=values)
        self.assertEqual(forged.status_code, 302)
        self.assertEqual(StaffingVacationUnionCalendar.query.count(), 1)

    def test_permission_threshold_and_static_tri_state_contract(self):
        user = self._user("vacation_below_threshold", "watcher")
        PermissionRule.query.filter_by(permission_key="neostaffing.vacation_selection.view").one().minimum_role = "operator"
        db.session.commit()
        self._login(user)
        denied = self.client.get("/neostaffing/vacation-selection")
        self.assertEqual(denied.status_code, 302)

        root = Path(__file__).resolve().parents[1]
        javascript = (root / "app/static/js/neostaffing_vacation.js").read_text(encoding="utf-8")
        template = (root / "app/templates/neostaffing/vacation_union_editor.html").read_text(encoding="utf-8")
        self.assertIn("input.indeterminate", javascript)
        self.assertIn("child.checked = input.checked", javascript)
        self.assertIn("data-vacation-operation-tree", template)

    def test_schema_is_additive_idempotent_and_factory_integrated(self):
        table_names = set(inspect(db.engine).get_table_names())
        self.assertTrue({model.__tablename__ for model in NEOSTAFFING_VACATION_MODELS}.issubset(table_names))
        connection = Mock()
        self.app.config.update(TESTING=False, SQLALCHEMY_DATABASE_URI="postgresql://example.test/neoapps")
        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "app.services.neostaffing_vacation_schema.db.session.connection",
                    return_value=connection,
                )
            )
            commit = stack.enter_context(
                patch("app.services.neostaffing_vacation_schema.db.session.commit")
            )
            create_mocks = [
                stack.enter_context(patch.object(model.__table__, "create"))
                for model in NEOSTAFFING_VACATION_MODELS
            ]
            self.assertTrue(ensure_neostaffing_vacation_tables(self.app))
            self.assertTrue(ensure_neostaffing_vacation_tables(self.app))
        self.assertTrue(all(mock.call_count == 2 for mock in create_mocks))
        self.assertTrue(all(call.kwargs["checkfirst"] for mock in create_mocks for call in mock.call_args_list))
        statements = "\n".join(str(call.args[0]) for call in connection.execute.call_args_list)
        self.assertIn("pg_advisory_xact_lock", statements)
        self.assertEqual(connection.execute.call_args_list[1].args[1]["lock_key"], NEOSTAFFING_VACATION_SCHEMA_LOCK_KEY)
        self.assertNotIn("DROP TABLE", statements)
        self.assertNotIn("UPDATE ", statements)
        self.assertEqual(commit.call_count, 2)

        with patch("app.ensure_neostaffing_vacation_tables") as ensure:
            app = create_app(self.config)
        ensure.assert_called_once_with(app)

    def _hierarchy(self):
        night = StaffingUnit(unit_type="sort", name="Night", display_order=1)
        ramp = StaffingUnit(unit_type="operation", name="Ramp", parent=night, display_order=1)
        other = StaffingUnit(unit_type="operation", name="Hub", parent=night, display_order=2)
        blue = StaffingUnit(unit_type="department", name="Blue Outbound", parent=ramp, display_order=1)
        brown = StaffingUnit(unit_type="department", name="Brown Outbound", parent=ramp, display_order=2)
        hub_department = StaffingUnit(unit_type="department", name="Hub Department", parent=other, display_order=1)
        blue_area = StaffingUnit(unit_type="work_area", name="Blue Ramp", parent=blue, display_order=1)
        blue_special = StaffingUnit(unit_type="work_area", name="Ramp Marshallers", parent=blue, display_order=2)
        brown_area = StaffingUnit(unit_type="work_area", name="Brown Ramp", parent=brown, display_order=1)
        other_area = StaffingUnit(unit_type="work_area", name="Hub Area", parent=hub_department, display_order=1)
        db.session.add_all([night, ramp, other, blue, brown, hub_department, blue_area, blue_special, brown_area, other_area])
        db.session.flush()
        return {
            "night": night,
            "ramp": ramp,
            "other_operation": other,
            "blue_department": blue,
            "brown_department": brown,
            "other_department": hub_department,
            "blue_area": blue_area,
            "blue_special": blue_special,
            "brown_area": brown_area,
            "other_area": other_area,
        }

    def _person(self, employee_id, first, last, seniority, classification, active=True):
        person = StaffingPerson(
            employee_id=employee_id,
            first_name=first,
            last_name=last,
            seniority_date=date.fromisoformat(seniority),
            classification=classification,
            employee_status="active",
            active=active,
        )
        db.session.add(person)
        db.session.flush()
        return person

    def _user(self, username, role, employee_id=None):
        user = User(
            username=username,
            email=f"{username}@example.com",
            first_name=username.title(),
            last_name="User",
            full_name=f"{username.title()} User",
            employee_id=employee_id or f"EMP-{username}",
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
                role=role,
                is_active=True,
                approved_at=datetime.utcnow(),
            )
        )
        db.session.commit()
        return user

    def _management_user(self, username, role, unit):
        employee_id = f"EMP-{username}"
        person = self._person(employee_id, username, "Supervisor", "2000-01-01", "full_time_supervisor")
        db.session.add(StaffingLeadershipAssignment(person=person, unit=unit, leadership_level="department", active=True))
        db.session.commit()
        return self._user(username, role, employee_id=employee_id)

    def _calendar(self, user, scope_ids, include_pt=True, include_ft=False, name="Union Calendar", operation=None):
        return vacation_service.create_union_calendar(
            {
                "vacation_year": self.YEAR,
                "name": name,
                "operation_unit_id": (operation or self.units["ramp"]).id,
                "include_part_time": "1" if include_pt else "",
                "include_full_time": "1" if include_ft else "",
                "staffing_unit_ids": scope_ids,
                "active": "1",
            },
            user,
        )

    def _login(self, user):
        g.pop("_login_user", None)
        return self.client.post(
            "/login",
            data={"username": user.username, "password": "TestPassword123!"},
        )


if __name__ == "__main__":
    unittest.main()
