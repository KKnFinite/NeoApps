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
    StaffingVacationUnionCalendarShare,
    StaffingVacationUnionSelection,
    StaffingVacationWeekConversion,
    StaffingVacationDaySelection,
    StaffingVacationDayEntitlement,
    StaffingVacationHolidayRule,
    StaffingVacationQualifyingHoliday,
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

    def test_recurring_holiday_rules_resolve_across_vacation_years(self):
        fixed = Mock(
            rule_type="fixed_date", month=12, day_of_month=25,
            weekday=None, occurrence=None,
        )
        labor = Mock(
            rule_type="nth_weekday", month=9, day_of_month=None,
            weekday=0, occurrence=1,
        )
        thanksgiving = Mock(
            rule_type="nth_weekday", month=11, day_of_month=None,
            weekday=3, occurrence=4,
        )
        memorial = Mock(
            rule_type="last_weekday", month=5, day_of_month=None,
            weekday=0, occurrence=None,
        )

        self.assertEqual(
            vacation_service.resolve_holiday_rule_date(fixed, 2027),
            date(2027, 12, 25),
        )
        self.assertEqual(
            vacation_service.resolve_holiday_rule_date(labor, 2027),
            date(2027, 9, 6),
        )
        self.assertEqual(
            vacation_service.resolve_holiday_rule_date(labor, 2028),
            date(2028, 9, 4),
        )
        self.assertEqual(
            vacation_service.resolve_holiday_rule_date(thanksgiving, 2027),
            date(2027, 11, 25),
        )
        self.assertEqual(
            vacation_service.resolve_holiday_rule_date(memorial, 2028),
            date(2028, 5, 29),
        )

    def test_recurring_holiday_settings_add_edit_delete_and_reject_duplicate(self):
        master = self._user("recurring_holiday_master", "master")
        rule = vacation_service.save_qualifying_holiday(
            None,
            name="Labor Day",
            user=master,
            rule_type="nth_weekday",
            month=9,
            weekday=0,
            occurrence=1,
        )
        self.assertEqual(vacation_service.holiday_rule_label(rule), "First Monday in September")
        db.session.commit()
        with self.assertRaisesRegex(ValueError, "equivalent"):
            vacation_service.save_qualifying_holiday(
                None,
                name="Duplicate Labor Day",
                user=master,
                rule_type="nth_weekday",
                month=9,
                weekday=0,
                occurrence=1,
            )
        db.session.rollback()
        rule = StaffingVacationHolidayRule.query.one()
        vacation_service.save_qualifying_holiday(
            rule,
            name="Memorial Day",
            user=master,
            rule_type="last_weekday",
            month=5,
            weekday=0,
        )
        self.assertEqual(vacation_service.holiday_rule_label(rule), "Last Monday in May")
        vacation_service.delete_qualifying_holiday(rule, master)
        db.session.flush()
        self.assertEqual(StaffingVacationHolidayRule.query.count(), 0)

    def test_recurring_and_legacy_holiday_dates_do_not_duplicate_awards(self):
        grandmaster = self._user("recurring_legacy_gm", "grandmaster")
        vacation_service.save_qualifying_holiday(
            None, date(self.YEAR, 7, 4), "Recurring Independence Day", grandmaster
        )
        db.session.add(
            StaffingVacationQualifyingHoliday(
                holiday_date=date(self.YEAR, 7, 4),
                name="Legacy Independence Day",
                created_by_user_id=grandmaster.id,
                updated_by_user_id=grandmaster.id,
            )
        )
        calendar = self._calendar(grandmaster, [self.units["blue_area"].id])
        person = self._union_person(
            "RLG1", "Recurring", "Legacy", self.units["blue_area"]
        )
        vacation_service.add_union_week(
            calendar, person, self.YEAR, date(self.YEAR, 7, 10), "regular", grandmaster
        )
        db.session.flush()
        self.assertEqual(StaffingVacationDayEntitlement.query.count(), 1)

    def test_recurring_rule_mutations_reconcile_only_exact_affected_weeks(self):
        grandmaster = self._user("bounded_rule_gm", "grandmaster")
        person = self._union_person(
            "BRH1", "Bounded", "Holiday", self.units["blue_area"]
        )
        old_week = date(self.YEAR, 7, 10)
        new_week = date(self.YEAR, 9, 11)
        unrelated_historical_week = date(2005, 2, 5)
        db.session.add_all(
            [
                StaffingVacationUnionSelection(
                    staffing_person_id=person.id,
                    vacation_year=self.YEAR,
                    week_ending=old_week,
                    bank_type="regular",
                    status="approved",
                    entered_by_user_id=grandmaster.id,
                ),
                StaffingVacationUnionSelection(
                    staffing_person_id=person.id,
                    vacation_year=self.YEAR,
                    week_ending=new_week,
                    bank_type="optional",
                    status="approved",
                    entered_by_user_id=grandmaster.id,
                ),
                StaffingVacationUnionSelection(
                    staffing_person_id=person.id,
                    vacation_year=2005,
                    week_ending=unrelated_historical_week,
                    bank_type="regular",
                    status="approved",
                    entered_by_user_id=grandmaster.id,
                ),
            ]
        )
        db.session.commit()
        selects = []

        def record_select(_conn, _cursor, statement, parameters, _context, _many):
            if statement.lstrip().upper().startswith("SELECT"):
                selects.append((statement, parameters))

        event.listen(db.engine, "before_cursor_execute", record_select)
        try:
            rule = vacation_service.save_qualifying_holiday(
                None,
                name="Independence Day",
                user=grandmaster,
                rule_type="fixed_date",
                month=7,
                day_of_month=4,
            )
            db.session.flush()
        finally:
            event.remove(db.engine, "before_cursor_execute", record_select)

        selection_reads = [
            statement
            for statement, _parameters in selects
            if "staffing_vacation_union_selections" in statement
            and "staffing_vacation_union_selections.id" in statement
        ]
        self.assertTrue(selection_reads)
        self.assertTrue(all("week_ending IN" in statement for statement in selection_reads))
        self.assertLessEqual(len(selects), 14)
        award = StaffingVacationDayEntitlement.query.one()
        self.assertEqual(award.source_holiday_date, date(self.YEAR, 7, 4))

        vacation_service.save_qualifying_holiday(
            rule,
            name="Labor Day",
            user=grandmaster,
            rule_type="nth_weekday",
            month=9,
            weekday=0,
            occurrence=1,
        )
        db.session.flush()
        awards = StaffingVacationDayEntitlement.query.all()
        self.assertEqual(len(awards), 1)
        self.assertEqual(awards[0].source_holiday_date, date(self.YEAR, 9, 6))

        vacation_service.delete_qualifying_holiday(rule, grandmaster)
        db.session.flush()
        self.assertEqual(StaffingVacationDayEntitlement.query.count(), 0)

    def test_neostaffing_header_has_compact_portal_actions(self):
        user = self._user("holiday_portal_gm", "grandmaster")
        self._login(user)
        page = self.client.get("/neostaffing/settings")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Back to NeoPortal", page.data)
        self.assertIn(b'class="neostaffing-mobile-portal-link"', page.data)

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
        self.assertEqual(calendar.name, "Hourly Vacation Calendar - Blue Ramp")
        self.assertTrue(calendar.include_full_time)
        self.assertEqual({scope.staffing_unit_id for scope in calendar.scopes}, {self.units["blue_area"].id})

    def test_official_calendar_authority_overlap_and_generated_name(self):
        grandmaster = self._user("official_overlap_gm", "grandmaster")
        pt_actor = self._union_actor(
            "official_pt", "simulator", "part_time_supervisor"
        )
        ft_actor = self._union_actor(
            "official_ft", "simulator", "full_time_supervisor"
        )
        master_actor = self._union_actor(
            "official_master", "master", "full_time_supervisor"
        )
        employee = self._union_person(
            "OC1", "Official", "Employee", self.units["blue_area"]
        )
        db.session.commit()
        values = {
            "vacation_year": self.YEAR,
            "calendar_type": "official",
            "operation_unit_id": self.units["ramp"].id,
            "include_part_time": "1",
            "staffing_unit_ids": [self.units["blue_area"].id],
            "active": "1",
        }
        with self.assertRaisesRegex(ValueError, "FT Supervisor or Manager"):
            vacation_service.create_union_calendar(values, pt_actor)
        db.session.rollback()

        sideways_values = dict(values)
        sideways_values["staffing_unit_ids"] = [self.units["brown_area"].id]
        with self.assertRaisesRegex(ValueError, "FT Supervisor or Manager"):
            vacation_service.create_union_calendar(sideways_values, ft_actor)
        db.session.rollback()
        sideways = vacation_service.create_union_calendar(
            sideways_values, master_actor
        )
        db.session.commit()
        self.assertIn("Brown Outbound", sideways.name)

        calendar = vacation_service.create_union_calendar(values, ft_actor)
        db.session.commit()
        self.assertEqual(calendar.calendar_type, "official")
        self.assertEqual(calendar.owner_user_id, ft_actor.id)
        self.assertEqual(
            calendar.name, "Hourly Vacation Calendar - PT - Blue Ramp"
        )
        with self.assertRaisesRegex(ValueError, "only one Official") as conflict:
            vacation_service.create_union_calendar(values, grandmaster)
        self.assertIn(employee.employee_id, str(conflict.exception))
        self.assertIn("Blue Ramp", str(conflict.exception))

    def test_official_overlap_uses_every_active_employee_assignment(self):
        grandmaster = self._user("multi_assignment_gm", "grandmaster")
        employee = self._union_person(
            "OC-MULTI", "Multiple", "Assignments", self.units["blue_area"]
        )
        second_assignment = StaffingWorkAssignment(
            person_id=employee.id,
            work_area_unit_id=self.units["brown_area"].id,
            active=True,
        )
        blue = self._calendar(grandmaster, [self.units["blue_area"].id])
        db.session.commit()
        assignment_rows = [
            (employee, employee.work_assignment),
            (employee, second_assignment),
        ]

        brown_values = {
            "vacation_year": self.YEAR,
            "calendar_type": "official",
            "operation_unit_id": self.units["ramp"].id,
            "include_part_time": "1",
            "staffing_unit_ids": [self.units["brown_area"].id],
            "active": "1",
        }
        with patch.object(
            vacation_service,
            "_active_union_people_with_assignments",
            return_value=assignment_rows,
        ):
            with self.assertRaisesRegex(ValueError, "only one Official") as conflict:
                vacation_service.create_union_calendar(brown_values, grandmaster)
        self.assertIn(employee.employee_id, str(conflict.exception))
        self.assertIn("Blue Ramp", str(conflict.exception))
        self.assertIn("Brown Ramp", str(conflict.exception))
        db.session.rollback()

        hub_values = dict(brown_values)
        hub_values["operation_unit_id"] = self.units["other_operation"].id
        hub_values["staffing_unit_ids"] = [self.units["other_area"].id]
        hub = vacation_service.create_union_calendar(hub_values, grandmaster)
        db.session.commit()
        self.assertNotEqual(blue.id, hub.id)

        view_values = dict(brown_values)
        view_values.update(
            calendar_type="view_only",
            name="Overlapping Read Only View",
        )
        view_only = vacation_service.create_union_calendar(
            view_values, grandmaster
        )
        db.session.commit()
        self.assertEqual(view_only.calendar_type, "view_only")

    def test_official_multi_assignment_overlap_check_is_bounded(self):
        grandmaster = self._user("bounded_overlap_gm", "grandmaster")
        person_ids = []
        for index in range(12):
            person = self._union_person(
                f"OC-B{index}", "Bounded", f"Person{index}", self.units["blue_area"]
            )
            person_ids.append(person.id)
        self._calendar(grandmaster, [self.units["blue_area"].id])
        db.session.commit()
        people = StaffingPerson.query.filter(
            StaffingPerson.id.in_(person_ids)
        ).order_by(StaffingPerson.id).all()
        assignments = {
            row.person_id: row
            for row in StaffingWorkAssignment.query.filter(
                StaffingWorkAssignment.person_id.in_(person_ids)
            ).all()
        }
        assignment_rows = []
        for person in people:
            assignment_rows.extend(
                [
                    (person, assignments[person.id]),
                    (
                        person,
                        StaffingWorkAssignment(
                            person_id=person.id,
                            work_area_unit_id=self.units["brown_area"].id,
                            active=True,
                        ),
                    ),
                ]
            )
        selects = []

        def record_select(_conn, _cursor, statement, _parameters, _context, _many):
            if statement.lstrip().upper().startswith("SELECT"):
                selects.append(statement)

        event.listen(db.engine, "before_cursor_execute", record_select)
        try:
            with patch.object(
                vacation_service,
                "_active_union_people_with_assignments",
                return_value=assignment_rows,
            ):
                conflicts = vacation_service.official_calendar_overlap_conflicts(
                    self.YEAR,
                    {self.units["brown_area"].id},
                    True,
                    False,
                )
        finally:
            event.remove(db.engine, "before_cursor_execute", record_select)
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(len(conflicts[0]["employee_ids"]), 12)
        self.assertLessEqual(len(selects), 4)

    def test_watcher_can_view_vacation_but_cannot_mutate(self):
        watcher = self._management_user(
            "vacation_read_only", "watcher", self.units["blue_department"]
        )
        person = StaffingPerson.query.filter_by(
            employee_id=watcher.employee_id
        ).one()
        db.session.commit()
        self._login(watcher)
        page = self.client.get(
            f"/neostaffing/vacation-selection/management?year={self.YEAR}"
        )
        self.assertEqual(page.status_code, 200)
        context = vacation_service.management_vacation_context(
            self.YEAR, watcher, today=date(self.YEAR - 1, 11, 1)
        )
        self.assertFalse(
            any(
                person_row["can_select"]
                or person_row["can_pass"]
                or person_row["can_request_week_changes"]
                for area_row in context["areas"]
                for person_row in area_row["person_rows"]
            )
        )
        union_context = vacation_service.union_calendars_context(
            self.YEAR, watcher
        )
        self.assertFalse(union_context["can_create_official"])
        self.assertFalse(union_context["can_create_view"])

        mutation_calls = (
            lambda: vacation_service.add_management_weeks(
                person, self.YEAR, [], watcher, today=date(self.YEAR - 1, 11, 1)
            ),
            lambda: vacation_service.pass_management_turn(
                self.YEAR, self.units["blue_department"].id, person, watcher
            ),
            lambda: vacation_service.create_union_calendar({}, watcher),
            lambda: vacation_service.schedule_split_vacation_day(
                999999, date(self.YEAR, 1, 2), watcher
            ),
            lambda: vacation_service.save_qualifying_holiday(
                None, date(self.YEAR, 1, 1), "Forbidden", watcher
            ),
            lambda: vacation_service.reset_management_vacation_area(
                self.units["blue_department"], self.YEAR, watcher
            ),
        )
        for mutation in mutation_calls:
            with self.subTest(mutation=mutation):
                with self.assertRaisesRegex(ValueError, "read-only"):
                    mutation()
                db.session.rollback()

    def test_writable_role_still_requires_hierarchy_and_master_sideways(self):
        operator = self._union_actor(
            "write_operator", "operator", "full_time_supervisor"
        )
        master = self._union_actor(
            "write_master", "master", "full_time_supervisor"
        )
        values = {
            "vacation_year": self.YEAR,
            "calendar_type": "official",
            "operation_unit_id": self.units["ramp"].id,
            "include_part_time": "1",
            "staffing_unit_ids": [self.units["brown_area"].id],
            "active": "1",
        }
        with self.assertRaisesRegex(ValueError, "FT Supervisor or Manager"):
            vacation_service.create_union_calendar(values, operator)
        db.session.rollback()
        calendar = vacation_service.create_union_calendar(values, master)
        db.session.commit()
        self.assertEqual(calendar.calendar_type, "official")

    def test_official_name_truncation_scope_edit_and_delete_preserves_selection(self):
        grandmaster = self._user("official_admin_gm", "grandmaster")
        extra_units = []
        for index in range(4):
            department = StaffingUnit(
                unit_type="department",
                name=f"Extra Department {index + 1}",
                parent=self.units["ramp"],
                display_order=10 + index,
            )
            area = StaffingUnit(
                unit_type="work_area",
                name=f"Extra Area {index + 1}",
                parent=department,
                display_order=1,
            )
            db.session.add_all([department, area])
            extra_units.append(area)
        person = self._union_person(
            "OC2", "Durable", "Selection", self.units["blue_area"]
        )
        db.session.flush()
        values = {
            "vacation_year": self.YEAR,
            "calendar_type": "official",
            "operation_unit_id": self.units["ramp"].id,
            "include_part_time": "1",
            "staffing_unit_ids": [area.id for area in extra_units],
            "active": "1",
        }
        calendar = vacation_service.create_union_calendar(values, grandmaster)
        self.assertIn("+1 more", calendar.name)
        self.assertIn(
            "Extra Department 4",
            vacation_service.union_calendar_scope_label(calendar),
        )
        selection = StaffingVacationUnionSelection(
            staffing_person_id=person.id,
            vacation_year=self.YEAR,
            week_ending=date(self.YEAR, 7, 10),
            bank_type="regular",
            status="approved",
            entered_by_user_id=grandmaster.id,
        )
        db.session.add(selection)
        db.session.commit()

        update = dict(values)
        update["staffing_unit_ids"] = [self.units["brown_area"].id]
        vacation_service.update_union_calendar(calendar, update, grandmaster)
        self.assertEqual(
            calendar.name, "Hourly Vacation Calendar - PT - Brown Outbound"
        )
        vacation_service.delete_union_calendar(calendar, grandmaster)
        db.session.commit()
        self.assertIsNotNone(
            db.session.get(StaffingVacationUnionSelection, selection.id)
        )

    def test_view_only_limit_overlap_sharing_read_only_and_independent_copy(self):
        owner = self._union_actor(
            "view_owner", "simulator", "part_time_supervisor"
        )
        recipient = self._union_actor(
            "view_recipient", "simulator", "part_time_supervisor"
        )
        recipient_two = self._union_actor(
            "second_recipient", "simulator", "full_time_supervisor"
        )
        grandmaster = self._user("view_overlap_gm", "grandmaster")
        official = self._calendar(
            grandmaster, [self.units["blue_area"].id], name="Official"
        )
        db.session.commit()
        created = []
        for index in range(5):
            created.append(
                vacation_service.create_union_calendar(
                    {
                        "vacation_year": self.YEAR,
                        "calendar_type": "view_only",
                        "name": f"Personal {index + 1}",
                        "operation_unit_id": self.units["ramp"].id,
                        "include_part_time": "1",
                        "staffing_unit_ids": [self.units["blue_area"].id],
                        "active": "1",
                    },
                    owner,
                )
            )
        db.session.commit()
        self.assertEqual(official.calendar_type, "official")
        self.assertEqual(len(created), 5)
        with self.assertRaisesRegex(ValueError, "up to 5"):
            vacation_service.create_union_calendar(
                {
                    "vacation_year": self.YEAR,
                    "calendar_type": "view_only",
                    "name": "Sixth",
                    "operation_unit_id": self.units["ramp"].id,
                    "include_part_time": "1",
                    "staffing_unit_ids": [self.units["blue_area"].id],
                    "active": "1",
                },
                owner,
            )
        db.session.rollback()
        calendar = db.session.get(StaffingVacationUnionCalendar, created[0].id)
        vacation_service.update_view_calendar_shares(
            calendar, [recipient.id, recipient_two.id], owner
        )
        db.session.commit()
        self.assertEqual(StaffingVacationUnionCalendarShare.query.count(), 2)
        self.assertTrue(vacation_service.can_view_union_calendar(calendar, recipient))
        self.assertTrue(
            vacation_service.can_view_union_calendar(calendar, recipient_two)
        )
        search = vacation_service.search_management_calendar_users("second_rec")
        self.assertEqual([row["user"].id for row in search], [recipient_two.id])
        with self.assertRaisesRegex(ValueError, "Only the owner"):
            vacation_service.update_union_calendar(
                calendar,
                {
                    "vacation_year": self.YEAR,
                    "calendar_type": "view_only",
                    "name": "Forged Rename",
                    "operation_unit_id": self.units["ramp"].id,
                    "include_part_time": "1",
                    "staffing_unit_ids": [self.units["blue_area"].id],
                    "active": "1",
                },
                recipient,
            )
        db.session.rollback()
        copied = vacation_service.copy_shared_view_calendar(
            calendar, "Independent Copy", recipient
        )
        db.session.commit()
        self.assertNotEqual(copied.id, calendar.id)
        self.assertEqual(copied.owner_user_id, recipient.id)
        calendar.name = "Owner Changed Name"
        db.session.commit()
        self.assertEqual(copied.name, "Independent Copy")
        self._login(recipient)
        self.assertEqual(
            self.client.get(
                f"/neostaffing/vacation-selection/union/{calendar.id}/view"
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(
                f"/neostaffing/vacation-selection/union/{calendar.id}/edit"
            ).status_code,
            302,
        )

    def test_calendar_owner_fallback_prefers_level_then_seniority(self):
        owner = self._union_actor(
            "fallback_owner", "simulator", "full_time_supervisor"
        )
        senior_ft = self._union_actor(
            "fallback_ft", "simulator", "full_time_supervisor"
        )
        manager = self._union_actor(
            "fallback_manager", "simulator", "manager"
        )
        official = vacation_service.create_union_calendar(
            {
                "vacation_year": self.YEAR,
                "calendar_type": "official",
                "operation_unit_id": self.units["ramp"].id,
                "include_part_time": "1",
                "staffing_unit_ids": [self.units["blue_area"].id],
                "active": "1",
            },
            owner,
        )
        calendar = vacation_service.create_union_calendar(
            {
                "vacation_year": self.YEAR,
                "calendar_type": "view_only",
                "name": "Fallback View",
                "operation_unit_id": self.units["ramp"].id,
                "include_part_time": "1",
                "staffing_unit_ids": [self.units["blue_area"].id],
                "active": "1",
            },
            owner,
        )
        owner.is_active = False
        db.session.commit()

        replacement = vacation_service.resolve_union_calendar_owner(
            calendar, persist=True
        )
        db.session.commit()
        self.assertEqual(replacement.id, manager.id)
        self.assertEqual(calendar.owner_user_id, manager.id)
        self.assertNotEqual(replacement.id, senior_ft.id)
        official_replacement = vacation_service.resolve_union_calendar_owner(
            official, persist=True
        )
        self.assertEqual(official_replacement.id, manager.id)
        self.assertEqual(official.owner_user_id, manager.id)

        grandmaster = self._user("fallback_gm", "grandmaster")
        manager.is_active = False
        senior_ft.is_active = False
        db.session.commit()
        fallback = vacation_service.resolve_union_calendar_owner(
            calendar, persist=True
        )
        db.session.commit()
        self.assertEqual(fallback.id, grandmaster.id)
        self.assertEqual(calendar.owner_user_id, grandmaster.id)
        self.assertEqual(
            vacation_service.resolve_union_calendar_owner(official).id,
            grandmaster.id,
        )

    def test_november_official_carry_forward_copies_definition_only(self):
        owner = self._union_actor(
            "carry_owner", "simulator", "full_time_supervisor"
        )
        person = self._union_person(
            "CF1", "Carry", "Employee", self.units["blue_area"]
        )
        source = self._calendar(owner, [self.units["blue_area"].id])
        db.session.add(
            StaffingVacationUnionSelection(
                staffing_person_id=person.id,
                vacation_year=self.YEAR,
                week_ending=date(self.YEAR, 7, 10),
                bank_type="regular",
                status="approved",
            )
        )
        db.session.commit()

        self.assertEqual(
            vacation_service.official_calendar_carry_forward_candidates(
                owner, today=date(self.YEAR, 10, 31)
            ),
            [],
        )
        candidates = vacation_service.official_calendar_carry_forward_candidates(
            owner, today=date(self.YEAR, 11, 1)
        )
        self.assertEqual([row["calendar"].id for row in candidates], [source.id])
        self.assertEqual(candidates[0]["target_year"], self.YEAR + 1)
        self.assertEqual(StaffingVacationUnionCalendar.query.count(), 1)

        created = vacation_service.carry_forward_official_calendar(
            source, owner, today=date(self.YEAR, 11, 1)
        )
        db.session.commit()
        self.assertEqual(created.vacation_year, self.YEAR + 1)
        self.assertEqual(created.calendar_type, "official")
        self.assertEqual(created.operation_unit_id, source.operation_unit_id)
        self.assertEqual(
            {scope.staffing_unit_id for scope in created.scopes},
            {scope.staffing_unit_id for scope in source.scopes},
        )
        self.assertEqual(
            (created.include_part_time, created.include_full_time),
            (source.include_part_time, source.include_full_time),
        )
        self.assertEqual(
            StaffingVacationUnionSelection.query.filter_by(
                vacation_year=self.YEAR + 1
            ).count(),
            0,
        )
        self.assertEqual(
            vacation_service.official_calendar_carry_forward_candidates(
                owner, today=date(self.YEAR, 11, 1)
            ),
            [],
        )
        with self.assertRaisesRegex(ValueError, "already carried forward"):
            vacation_service.carry_forward_official_calendar(
                source, owner, today=date(self.YEAR, 11, 1)
            )

    def test_owner_reconciliation_is_bounded_and_inherited_limit_applies(self):
        first_owner = self._union_actor(
            "fallback_first", "simulator", "part_time_supervisor"
        )
        second_owner = self._union_actor(
            "fallback_second", "simulator", "part_time_supervisor"
        )
        replacement = self._union_actor(
            "fallback_receiver", "simulator", "manager"
        )
        calendars = []
        for index in range(5):
            calendars.append(
                vacation_service.create_union_calendar(
                    {
                        "vacation_year": self.YEAR,
                        "calendar_type": "view_only",
                        "name": f"Inherited {index + 1}",
                        "operation_unit_id": self.units["ramp"].id,
                        "include_part_time": "1",
                        "staffing_unit_ids": [self.units["blue_area"].id],
                        "active": "1",
                    },
                    first_owner,
                )
            )
        calendars.append(
            vacation_service.create_union_calendar(
                {
                    "vacation_year": self.YEAR,
                    "calendar_type": "view_only",
                    "name": "Inherited 6",
                    "operation_unit_id": self.units["ramp"].id,
                    "include_part_time": "1",
                    "staffing_unit_ids": [self.units["blue_area"].id],
                    "active": "1",
                },
                second_owner,
            )
        )
        first_owner.is_active = False
        second_owner.is_active = False
        db.session.commit()
        selects = []

        def record(_conn, _cursor, statement, _params, _context, _many):
            if statement.lstrip().upper().startswith("SELECT"):
                selects.append(statement)

        event.listen(db.engine, "before_cursor_execute", record)
        try:
            changed = vacation_service.reconcile_union_calendar_owners(self.YEAR)
            db.session.commit()
        finally:
            event.remove(db.engine, "before_cursor_execute", record)
        self.assertEqual(len(changed), 6)
        self.assertLessEqual(len(selects), 8)
        self.assertTrue(
            all(calendar.owner_user_id == replacement.id for calendar in calendars)
        )
        context = vacation_service.union_calendars_context(self.YEAR, replacement)
        self.assertEqual(context["owned_view_count"], 6)
        self.assertFalse(context["can_create_view"])

    def test_grandmaster_union_reset_is_pool_and_year_scoped_and_rolls_back(self):
        grandmaster = self._user("reset_union_gm", "grandmaster")
        lower = self._user("reset_union_lower", "master")
        person = self._union_person(
            "UR1", "Reset", "Union", self.units["blue_area"]
        )
        other = self._union_person(
            "UR2", "Other", "Union", self.units["brown_area"]
        )
        calendar = self._calendar(grandmaster, [self.units["blue_area"].id])
        other_calendar = self._calendar(
            grandmaster, [self.units["brown_area"].id], name="Other"
        )
        selection = StaffingVacationUnionSelection(
            staffing_person_id=person.id,
            vacation_year=self.YEAR,
            week_ending=date(self.YEAR, 7, 10),
            bank_type="optional",
            status="pending",
        )
        other_selection = StaffingVacationUnionSelection(
            staffing_person_id=other.id,
            vacation_year=self.YEAR,
            week_ending=date(self.YEAR, 7, 17),
            bank_type="regular",
            status="approved",
        )
        later_selection = StaffingVacationUnionSelection(
            staffing_person_id=person.id,
            vacation_year=self.YEAR + 1,
            week_ending=date(self.YEAR + 1, 7, 8),
            bank_type="regular",
            status="approved",
        )
        db.session.add_all([selection, other_selection, later_selection])
        db.session.flush()
        conversion = StaffingVacationWeekConversion(
            staffing_person_id=person.id,
            vacation_year=self.YEAR,
            program="union",
            source_union_selection_id=selection.id,
        )
        db.session.add(conversion)
        db.session.flush()
        db.session.add(
            StaffingVacationDaySelection(
                conversion_id=conversion.id,
                staffing_person_id=person.id,
                vacation_year=self.YEAR,
                vacation_date=date(self.YEAR, 8, 2),
                item_type="split_vacation",
                status="scheduled",
            )
        )
        holiday = StaffingVacationQualifyingHoliday(
            holiday_date=date(self.YEAR, 7, 4), name="Preserved Holiday"
        )
        db.session.add(holiday)
        db.session.commit()
        selection_id = selection.id
        other_selection_id = other_selection.id
        later_selection_id = later_selection.id
        calendar_id = calendar.id
        other_calendar_id = other_calendar.id
        holiday_id = holiday.id

        with self.assertRaisesRegex(ValueError, "Grandmaster"):
            vacation_service.reset_union_vacation_calendar(
                calendar, self.YEAR, lower
            )
        db.session.rollback()
        vacation_service.reset_union_vacation_calendar(
            calendar, self.YEAR, grandmaster
        )
        db.session.rollback()
        self.assertIsNotNone(db.session.get(StaffingVacationUnionSelection, selection_id))

        reset_selects = []

        def record_reset(_conn, _cursor, statement, _params, _context, _many):
            if statement.lstrip().upper().startswith("SELECT"):
                reset_selects.append(statement)

        event.listen(db.engine, "before_cursor_execute", record_reset)
        try:
            vacation_service.reset_union_vacation_calendar(
                calendar, self.YEAR, grandmaster
            )
            db.session.commit()
        finally:
            event.remove(db.engine, "before_cursor_execute", record_reset)
        self.assertLessEqual(len(reset_selects), 12)
        self.assertIsNone(db.session.get(StaffingVacationUnionSelection, selection_id))
        self.assertIsNotNone(db.session.get(StaffingVacationUnionSelection, other_selection_id))
        self.assertIsNotNone(db.session.get(StaffingVacationUnionSelection, later_selection_id))
        self.assertIsNotNone(db.session.get(StaffingVacationUnionCalendar, calendar_id))
        self.assertIsNotNone(db.session.get(StaffingVacationUnionCalendar, other_calendar_id))
        self.assertIsNotNone(db.session.get(StaffingVacationQualifyingHoliday, holiday_id))
        self.assertEqual(
            StaffingVacationDaySelection.query.filter_by(
                staffing_person_id=person.id, vacation_year=self.YEAR
            ).count(),
            0,
        )

    def test_grandmaster_calendar_admin_and_context_queries_are_bounded(self):
        grandmaster = self._user("calendar_admin_gm", "grandmaster")
        owner = self._union_actor(
            "bounded_view_owner", "simulator", "part_time_supervisor"
        )
        for index in range(5):
            vacation_service.create_union_calendar(
                {
                    "vacation_year": self.YEAR,
                    "calendar_type": "view_only",
                    "name": f"Bounded View {index}",
                    "operation_unit_id": self.units["ramp"].id,
                    "include_part_time": "1",
                    "staffing_unit_ids": [self.units["blue_area"].id],
                    "active": "1",
                },
                owner,
            )
        db.session.commit()
        statements = []

        def record(_conn, _cursor, statement, _params, _context, _many):
            if statement.lstrip().upper().startswith("SELECT"):
                statements.append(statement)

        event.listen(db.engine, "before_cursor_execute", record)
        try:
            context = vacation_service.union_calendars_context(self.YEAR, owner)
        finally:
            event.remove(db.engine, "before_cursor_execute", record)
        self.assertEqual(len(context["my_view_calendars"]), 5)
        # The active Aug-Jul Optional Day usage is one additional bounded aggregate.
        self.assertLessEqual(len(statements), 17)
        admin = vacation_service.union_calendar_admin_context(grandmaster)
        self.assertEqual(len(admin["calendars"]), 5)
        with self.assertRaisesRegex(ValueError, "Grandmaster"):
            vacation_service.union_calendar_admin_context(owner)
        self._login(grandmaster)
        page = self.client.get("/neostaffing/vacation-selection/union/admin")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"CALENDAR ADMIN", page.data)
        self.assertIn(b"Bounded View 0", page.data)

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

    def test_union_entitlement_includes_exactly_one_optional_week(self):
        expected = ((0, 0), (1, 1), (3, 2), (8, 3), (15, 4), (20, 5), (25, 6), (30, 7))
        for years, regular in expected:
            with self.subTest(years=years):
                seniority = date(self.YEAR - years, 12, 31)
                entitlement = vacation_service.union_vacation_entitlement(seniority, self.YEAR)
                self.assertEqual(entitlement.regular_weeks, regular)
                self.assertEqual(entitlement.optional_weeks, 1)

    def test_union_direct_and_pt_entries_reserve_capacity_and_release_on_deny(self):
        grandmaster = self._user("union_pick_gm", "grandmaster")
        calendar = self._calendar(grandmaster, [self.units["blue_area"].id])
        first = self._union_person("UP1", "Able", "Union", self.units["blue_area"])
        second = self._union_person("UP2", "Baker", "Union", self.units["blue_area"])
        pt_actor = self._union_actor("union_pt", "simulator", "part_time_supervisor")
        ft_actor = self._union_actor("union_ft", "simulator", "full_time_supervisor")
        db.session.commit()
        week = vacation_service.vacation_year_weeks(self.YEAR)[4].week_ending

        pending = vacation_service.add_union_week(calendar, first, self.YEAR, week, "regular", pt_actor)
        db.session.commit()
        self.assertEqual(pending.status, "pending")
        with self.assertRaisesRegex(ValueError, "capacity is full"):
            vacation_service.add_union_week(calendar, second, self.YEAR, week, "regular", pt_actor)
        db.session.rollback()

        vacation_service.review_union_selection(pending, True, ft_actor)
        db.session.commit()
        self.assertEqual(pending.status, "approved")
        vacation_service.cancel_union_selection(pending, ft_actor)
        db.session.commit()
        approved = vacation_service.add_union_week(calendar, second, self.YEAR, week, "regular", ft_actor)
        db.session.commit()
        self.assertEqual(approved.status, "approved")

        later_week = vacation_service.vacation_year_weeks(self.YEAR)[5].week_ending
        pending = vacation_service.add_union_week(calendar, first, self.YEAR, later_week, "optional", pt_actor)
        db.session.commit()
        vacation_service.review_union_selection(pending, False, ft_actor)
        db.session.commit()
        self.assertEqual(pending.status, "denied")

        cancel_week = vacation_service.vacation_year_weeks(self.YEAR)[7].week_ending
        cancelled_pending = vacation_service.add_union_week(
            calendar, first, self.YEAR, cancel_week, "regular", pt_actor
        )
        db.session.commit()
        vacation_service.cancel_union_selection(cancelled_pending, pt_actor)
        db.session.commit()
        self.assertEqual(cancelled_pending.status, "cancelled")
        replacement = vacation_service.add_union_week(
            calendar, second, self.YEAR, cancel_week, "regular", ft_actor
        )
        db.session.commit()
        self.assertEqual(replacement.status, "approved")

    def test_union_pending_approval_requires_one_write_override_when_over_capacity(self):
        grandmaster = self._user("union_review_over_gm", "grandmaster")
        calendar = self._calendar(grandmaster, [self.units["blue_area"].id])
        first = self._union_person("URO1", "Pending", "Union", self.units["blue_area"])
        second = self._union_person("URO2", "Direct", "Union", self.units["blue_area"])
        pt_actor = self._union_actor(
            "union_review_over_pt", "simulator", "part_time_supervisor"
        )
        ft_actor = self._union_actor(
            "union_review_over_ft", "simulator", "full_time_supervisor"
        )
        db.session.commit()
        week = vacation_service.vacation_year_weeks(self.YEAR)[8].week_ending

        pending = vacation_service.add_union_week(
            calendar, first, self.YEAR, week, "regular", pt_actor
        )
        vacation_service.add_union_week(
            calendar,
            second,
            self.YEAR,
            week,
            "regular",
            ft_actor,
            capacity_override=True,
        )
        db.session.commit()
        with self.assertRaisesRegex(ValueError, "Approval exceeds"):
            vacation_service.review_union_selection(pending, True, ft_actor)
        db.session.rollback()
        vacation_service.review_union_selection(
            pending, True, ft_actor, capacity_override=True
        )
        db.session.commit()
        self.assertEqual(pending.status, "approved")

    def test_union_override_over_indicator_and_pt_override_rejection(self):
        grandmaster = self._user("union_over_gm", "grandmaster")
        calendar = self._calendar(grandmaster, [self.units["blue_area"].id])
        first = self._union_person("UO1", "Able", "Union", self.units["blue_area"])
        second = self._union_person("UO2", "Baker", "Union", self.units["blue_area"])
        pt_actor = self._union_actor("union_over_pt", "simulator", "part_time_supervisor")
        ft_actor = self._union_actor("union_over_ft", "simulator", "full_time_supervisor")
        db.session.commit()
        week = vacation_service.vacation_year_weeks(self.YEAR)[6].week_ending

        vacation_service.add_union_week(calendar, first, self.YEAR, week, "regular", ft_actor)
        db.session.commit()
        with self.assertRaisesRegex(ValueError, "PT Supervisors cannot override"):
            vacation_service.add_union_week(calendar, second, self.YEAR, week, "regular", pt_actor, capacity_override=True)
        db.session.rollback()
        vacation_service.add_union_week(calendar, second, self.YEAR, week, "regular", ft_actor, capacity_override=True)
        db.session.commit()
        context = vacation_service.union_calendars_context(self.YEAR, grandmaster)
        row = next(item for item in context["calendars"] if item["calendar"].id == calendar.id)
        self.assertTrue(row["over"])
        self.assertEqual(next(item for item in row["week_rows"] if item["week"].week_ending == week)["used"], 2)

    def test_union_direct_move_remove_override_and_past_correction(self):
        grandmaster = self._user("union_change_gm", "grandmaster")
        calendar = self._calendar(grandmaster, [self.units["blue_area"].id])
        first = self._union_person(
            "UC1", "Direct", "One", self.units["blue_area"], "2000-01-01"
        )
        second = self._union_person(
            "UC2", "Direct", "Two", self.units["blue_area"], "2000-01-01"
        )
        pt_actor = self._union_actor(
            "union_change_pt", "simulator", "part_time_supervisor"
        )
        ft_actor = self._union_actor(
            "union_change_ft", "simulator", "full_time_supervisor"
        )
        db.session.commit()
        weeks = vacation_service.vacation_year_weeks(self.YEAR)
        source_week = weeks[20].week_ending
        full_week = weeks[21].week_ending
        first_pick = vacation_service.add_union_week(
            calendar, first, self.YEAR, source_week, "regular", ft_actor
        )
        vacation_service.add_union_week(
            calendar, second, self.YEAR, full_week, "regular", ft_actor
        )
        db.session.commit()

        with self.assertRaisesRegex(ValueError, "capacity is full"):
            vacation_service.move_union_selection(
                first_pick,
                full_week,
                ft_actor,
                today=date(self.YEAR, 1, 1),
            )
        db.session.rollback()
        with self.assertRaisesRegex(ValueError, "authorized FT Supervisor or Manager"):
            vacation_service.move_union_selection(
                first_pick,
                full_week,
                pt_actor,
                capacity_override=True,
                today=date(self.YEAR, 1, 1),
            )
        db.session.rollback()

        selects = []

        def record(_conn, _cursor, statement, _params, _context, _many):
            if statement.lstrip().upper().startswith("SELECT"):
                selects.append(statement)

        event.listen(db.engine, "before_cursor_execute", record)
        try:
            moved = vacation_service.move_union_selection(
                first_pick,
                full_week,
                ft_actor,
                capacity_override=True,
                today=date(self.YEAR, 1, 1),
            )
            db.session.commit()
        finally:
            event.remove(db.engine, "before_cursor_execute", record)
        self.assertLessEqual(len(selects), 22)
        self.assertEqual((moved.week_ending, moved.status), (full_week, "approved"))
        context = vacation_service.union_calendars_context(
            self.YEAR, ft_actor, today=date(self.YEAR, 1, 1)
        )
        row = next(item for item in context["calendars"] if item["calendar"].id == calendar.id)
        target = next(item for item in row["week_rows"] if item["week"].week_ending == full_week)
        self.assertEqual((target["used"], target["over"]), (2, True))

        vacation_service.cancel_union_selection(
            moved, ft_actor, today=date(self.YEAR, 1, 1)
        )
        db.session.commit()
        self.assertEqual(moved.status, "cancelled")
        self.assertEqual(
            len(
                vacation_service.union_calendars_context(
                    self.YEAR, ft_actor, today=date(self.YEAR, 1, 1)
                )["calendars"][0]["person_rows"][0]["selections"]
            ),
            0,
        )

        past = StaffingVacationUnionSelection(
            staffing_person_id=first.id,
            vacation_year=self.YEAR,
            week_ending=weeks[0].week_ending,
            bank_type="regular",
            status="approved",
            entered_by_user_id=ft_actor.id,
            reviewed_by_user_id=ft_actor.id,
        )
        db.session.add(past)
        db.session.commit()
        with self.assertRaisesRegex(ValueError, "require correction"):
            vacation_service.cancel_union_selection(
                past, ft_actor, today=date(self.YEAR, 2, 1)
            )
        db.session.rollback()
        vacation_service.cancel_union_selection(
            past,
            ft_actor,
            correction=True,
            today=date(self.YEAR, 2, 1),
        )
        db.session.commit()
        self.assertEqual(past.status, "cancelled")
        context = vacation_service.union_calendars_context(
            self.YEAR, ft_actor, today=date(self.YEAR, 2, 1)
        )
        row = next(item for item in context["calendars"] if item["calendar"].id == calendar.id)
        person_row = next(
            item for item in row["person_rows"] if item["person"].id == first.id
        )
        self.assertEqual(
            person_row["regular_remaining"], person_row["entitlement"].regular_weeks
        )

    def test_union_day_edits_are_direct_management_only_and_restore_past_balance(self):
        grandmaster = self._user("union_day_polish_gm", "grandmaster")
        calendar = self._calendar(grandmaster, [self.units["blue_area"].id])
        person = self._union_person(
            "UDP1", "Union", "Day", self.units["blue_area"], "2000-01-01"
        )
        pt_actor = self._union_actor(
            "union_day_polish_pt", "simulator", "part_time_supervisor"
        )
        ft_actor = self._union_actor(
            "union_day_polish_ft", "simulator", "full_time_supervisor"
        )
        db.session.commit()
        day = date(self.YEAR, 8, 10)
        with self.assertRaisesRegex(ValueError, "authorized FT Supervisor or Manager"):
            vacation_service.schedule_vacation_entitlement_day(
                person,
                day,
                "optional_day",
                pt_actor,
                program="union",
            )
        db.session.rollback()
        scheduled = vacation_service.schedule_vacation_entitlement_day(
            person,
            day,
            "optional_day",
            ft_actor,
            program="union",
        )
        db.session.commit()
        vacation_service.cancel_vacation_entitlement_day(
            scheduled, ft_actor, today=date(self.YEAR, 9, 1)
        )
        db.session.commit()
        replacement = vacation_service.schedule_vacation_entitlement_day(
            person,
            day + timedelta(days=1),
            "optional_day",
            ft_actor,
            program="union",
        )
        db.session.commit()
        self.assertEqual(replacement.status, "scheduled")

    def test_union_pending_and_direct_ui_authorization_contracts(self):
        grandmaster = self._user("union_ui_gm", "grandmaster")
        calendar = self._calendar(grandmaster, [self.units["blue_area"].id])
        person = self._union_person(
            "UUI1", "Union", "Pending", self.units["blue_area"], "2000-01-01"
        )
        direct_person = self._union_person(
            "UUI2", "Union", "Approved", self.units["blue_area"], "2000-01-01"
        )
        pt_actor = self._union_actor(
            "union_ui_pt", "simulator", "part_time_supervisor"
        )
        ft_actor = self._union_actor(
            "union_ui_ft", "simulator", "full_time_supervisor"
        )
        db.session.commit()
        pending = vacation_service.add_union_week(
            calendar,
            person,
            self.YEAR,
            vacation_service.vacation_year_weeks(self.YEAR)[15].week_ending,
            "regular",
            pt_actor,
        )
        vacation_service.add_union_week(
            calendar,
            direct_person,
            self.YEAR,
            vacation_service.vacation_year_weeks(self.YEAR)[17].week_ending,
            "regular",
            ft_actor,
        )
        db.session.commit()

        self._login(pt_actor)
        pt_page = self.client.get(
            f"/neostaffing/vacation-selection/union?year={self.YEAR}"
        )
        self.assertIn(b"ENTRIES PENDING FT REVIEW", pt_page.data)
        self.assertIn(b"SUBMIT PENDING", pt_page.data)
        self.assertIn(b"REMOVE PENDING", pt_page.data)
        self.assertNotIn(b"APPROVE OVERRIDE", pt_page.data)
        self.assertNotIn(b"ADD APPROVED DAY", pt_page.data)

        self._login(ft_actor)
        ft_page = self.client.get(
            f"/neostaffing/vacation-selection/union?year={self.YEAR}"
        )
        self.assertIn(b"DIRECT APPROVED ENTRY", ft_page.data)
        self.assertIn(b"APPROVE", ft_page.data)
        self.assertIn(b"DENY", ft_page.data)
        self.assertIn(b"REMOVE PENDING", ft_page.data)
        self.assertIn(b"ADD APPROVED DAY", ft_page.data)
        self.assertIn(b"MOVE APPROVED", ft_page.data)
        self.assertIn(b"REMOVE APPROVED", ft_page.data)

        self.app.config["CSRF_PROTECT_TESTING"] = True
        response = self.client.post(
            f"/neostaffing/vacation-selection/union/selection/{pending.id}/move",
            data={
                "vacation_year": self.YEAR,
                "requested_week_ending": vacation_service.vacation_year_weeks(
                    self.YEAR
                )[16].week_ending.isoformat(),
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_union_selection_survives_transfer_scope_loss_and_reattachment(self):
        grandmaster = self._user("union_transfer_gm", "grandmaster")
        blue = self._calendar(grandmaster, [self.units["blue_area"].id], name="Blue")
        brown = self._calendar(grandmaster, [self.units["brown_area"].id], name="Brown")
        person = self._union_person("UT1", "Transfer", "Union", self.units["blue_area"])
        db.session.commit()
        week = vacation_service.vacation_year_weeks(self.YEAR)[8].week_ending
        selection = vacation_service.add_union_week(blue, person, self.YEAR, week, "optional", grandmaster)
        db.session.commit()

        person.work_assignment.work_area_unit_id = self.units["other_area"].id
        db.session.commit()
        context = vacation_service.union_calendars_context(self.YEAR, grandmaster)
        self.assertEqual(sum(row["week_rows"][8]["used"] for row in context["calendars"]), 0)
        self.assertEqual(db.session.get(StaffingVacationUnionSelection, selection.id).status, "approved")

        person.work_assignment.work_area_unit_id = self.units["brown_area"].id
        db.session.commit()
        context = vacation_service.union_calendars_context(self.YEAR, grandmaster)
        brown_row = next(row for row in context["calendars"] if row["calendar"].id == brown.id)
        self.assertEqual(brown_row["week_rows"][8]["used"], 1)
        self.assertEqual(brown_row["person_rows"][0]["selections"][0]["selection"].id, selection.id)

    def test_union_selection_route_requires_csrf_and_preserves_atomic_capacity(self):
        grandmaster = self._user("union_route_gm", "grandmaster")
        calendar = self._calendar(grandmaster, [self.units["blue_area"].id])
        first = self._union_person("UR1", "Route", "One", self.units["blue_area"])
        second = self._union_person("UR2", "Route", "Two", self.units["blue_area"])
        db.session.commit()
        self._login(grandmaster)
        self.app.config["CSRF_PROTECT_TESTING"] = True
        page = self.client.get(f"/neostaffing/vacation-selection/union?year={self.YEAR}")
        token = re.search(r'<meta name="csrf-token" content="([^"]+)">', page.get_data(as_text=True)).group(1)
        week = vacation_service.vacation_year_weeks(self.YEAR)[10].week_ending.isoformat()
        values = {"vacation_year": self.YEAR, "staffing_person_id": first.id, "week_ending": week, "bank_type": "regular"}
        self.assertEqual(self.client.post(f"/neostaffing/vacation-selection/union/{calendar.id}/select", data=values).status_code, 400)
        self.assertEqual(StaffingVacationUnionSelection.query.count(), 0)
        values["csrf_token"] = token
        self.assertEqual(self.client.post(f"/neostaffing/vacation-selection/union/{calendar.id}/select", data=values).status_code, 302)
        values["staffing_person_id"] = second.id
        self.client.post(f"/neostaffing/vacation-selection/union/{calendar.id}/select", data=values)
        self.assertEqual(StaffingVacationUnionSelection.query.filter(StaffingVacationUnionSelection.status.in_(("pending", "approved"))).count(), 1)

    def test_union_optional_split_regular_rejection_and_day_capacity_override(self):
        grandmaster = self._user("union_split_gm", "grandmaster")
        calendar = self._calendar(grandmaster, [self.units["blue_area"].id])
        first = self._union_person("US1", "Split", "One", self.units["blue_area"])
        second = self._union_person("US2", "Split", "Two", self.units["blue_area"])
        ft_actor = self._union_actor("union_split_ft", "simulator", "full_time_supervisor")
        db.session.commit()
        weeks = vacation_service.vacation_year_weeks(self.YEAR)
        regular = vacation_service.add_union_week(
            calendar, first, self.YEAR, weeks[20].week_ending, "regular", ft_actor
        )
        optional = vacation_service.add_union_week(
            calendar, first, self.YEAR, weeks[21].week_ending, "optional", ft_actor
        )
        db.session.commit()
        with self.assertRaisesRegex(ValueError, "Regular Union vacation weeks cannot"):
            vacation_service.split_union_optional_week(
                calendar, first, self.YEAR, ft_actor, selection=regular, today=date(2027, 1, 1)
            )
        db.session.rollback()
        first_conversion = vacation_service.split_union_optional_week(
            calendar, first, self.YEAR, ft_actor, selection=optional, today=date(2027, 1, 1)
        )
        second_conversion = vacation_service.split_union_optional_week(
            calendar, second, self.YEAR, ft_actor, today=date(2027, 1, 1)
        )
        db.session.commit()
        self.assertEqual(optional.status, "cancelled")

        day = date(2027, 2, 10)
        vacation_service.schedule_split_vacation_day(first_conversion, day, ft_actor)
        db.session.commit()
        with self.assertRaisesRegex(ValueError, "single-day capacity is full"):
            vacation_service.schedule_split_vacation_day(second_conversion, day, ft_actor)
        db.session.rollback()
        vacation_service.schedule_split_vacation_day(
            second_conversion, day, ft_actor, capacity_override=True
        )
        db.session.commit()
        context = vacation_service.union_calendars_context(self.YEAR, grandmaster)
        row = next(item for item in context["calendars"] if item["calendar"].id == calendar.id)
        self.assertTrue(row["daily_over"])
        self.assertEqual(row["daily_usage"][day], 2)
        first_row = next(
            person_row for person_row in row["person_rows"]
            if person_row["person"].id == first.id
        )
        self.assertEqual(first_row["split_day_balance"], 4)
        self._login(ft_actor)
        rendered = self.client.get(
            f"/neostaffing/vacation-selection/union?year={self.YEAR}"
        )
        self.assertEqual(rendered.status_code, 200)
        self.assertIn(b"DAY OVER", rendered.data)

    def test_split_day_whole_week_overlap_cancel_and_union_recombine(self):
        grandmaster = self._user("union_recombine_gm", "grandmaster")
        calendar = self._calendar(grandmaster, [self.units["blue_area"].id])
        person = self._union_person("US3", "Recombine", "Union", self.units["blue_area"])
        ft_actor = self._union_actor("union_recombine_ft", "simulator", "full_time_supervisor")
        db.session.commit()
        conversion = vacation_service.split_union_optional_week(
            calendar, person, self.YEAR, ft_actor, today=date(2027, 1, 1)
        )
        whole = vacation_service.add_union_week(
            calendar, person, self.YEAR, date(2027, 3, 13), "regular", ft_actor
        )
        db.session.commit()
        with self.assertRaisesRegex(ValueError, "whole vacation week"):
            vacation_service.schedule_split_vacation_day(
                conversion, date(2027, 3, 10), ft_actor
            )
        db.session.rollback()
        scheduled = vacation_service.schedule_split_vacation_day(
            conversion, date(2027, 4, 10), ft_actor
        )
        db.session.commit()
        with self.assertRaisesRegex(ValueError, "Cancel all scheduled"):
            vacation_service.recombine_split_vacation_week(conversion, ft_actor)
        db.session.rollback()
        vacation_service.cancel_split_vacation_day(scheduled, ft_actor)
        vacation_service.recombine_split_vacation_week(conversion, ft_actor)
        db.session.commit()
        self.assertIsNotNone(conversion.recombined_at)
        self.assertEqual(whole.status, "approved")

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
        # Generic days, cycle usage, and durable awards are bounded bulk reads.
        self.assertLessEqual(len(statements), 9)

    def test_union_optional_day_context_uses_active_august_july_cycle(self):
        grandmaster = self._user("optional_cycle_gm", "grandmaster")
        self._calendar(grandmaster, [self.units["blue_area"].id])
        person = self._union_person(
            "OPTC1", "Cycle", "Employee", self.units["blue_area"]
        )
        db.session.add_all(
            [
                StaffingVacationDaySelection(
                    staffing_person_id=person.id,
                    vacation_year=self.YEAR - 1,
                    vacation_date=date(self.YEAR - 1, 8, 15),
                    item_type="optional_day",
                    status="scheduled",
                ),
                StaffingVacationDaySelection(
                    staffing_person_id=person.id,
                    vacation_year=self.YEAR,
                    vacation_date=date(self.YEAR, 1, 15),
                    item_type="optional_day",
                    status="scheduled",
                ),
            ]
        )
        db.session.commit()

        context = vacation_service.union_calendars_context(
            self.YEAR, grandmaster, today=date(self.YEAR, 1, 20)
        )
        person_row = context["calendars"][0]["person_rows"][0]
        self.assertEqual(person_row["optional_days_remaining"], 2)
        self.assertEqual(
            person_row["optional_day_cycle_start"], date(self.YEAR - 1, 8, 1)
        )
        self.assertEqual(
            vacation_service.optional_day_usage_by_person(
                date(self.YEAR, 1, 20)
            )[person.id],
            2,
        )

        vacation_service.schedule_vacation_entitlement_day(
            person,
            date(self.YEAR, 2, 15),
            "optional_day",
            grandmaster,
            program="union",
        )
        vacation_service.schedule_vacation_entitlement_day(
            person,
            date(self.YEAR, 3, 15),
            "optional_day",
            grandmaster,
            program="union",
        )
        db.session.commit()
        with self.assertRaisesRegex(ValueError, "No Optional Days remain"):
            vacation_service.schedule_vacation_entitlement_day(
                person,
                date(self.YEAR, 4, 15),
                "optional_day",
                grandmaster,
                program="union",
            )
        db.session.rollback()
        context = vacation_service.union_calendars_context(
            self.YEAR, grandmaster, today=date(self.YEAR, 3, 20)
        )
        self.assertEqual(
            context["calendars"][0]["person_rows"][0]["optional_days_remaining"],
            0,
        )

        reset_context = vacation_service.union_calendars_context(
            self.YEAR, grandmaster, today=date(self.YEAR, 8, 1)
        )
        reset_row = reset_context["calendars"][0]["person_rows"][0]
        self.assertEqual(reset_row["optional_days_remaining"], 4)
        self.assertEqual(
            reset_row["optional_day_cycle_start"], date(self.YEAR, 8, 1)
        )

    def test_union_optional_days_reset_august_first_and_restore(self):
        grandmaster = self._user("optional_gm", "grandmaster")
        calendar = self._calendar(grandmaster, [self.units["blue_area"].id])
        person = self._union_person(
            "OPT1", "Optional", "Employee", self.units["blue_area"]
        )
        db.session.commit()
        rows = []
        for month, day in ((8, 1), (9, 1), (10, 1), (11, 1)):
            rows.append(
                vacation_service.schedule_vacation_entitlement_day(
                    person,
                    date(self.YEAR, month, day),
                    "optional_day",
                    grandmaster,
                    program="union",
                )
            )
        db.session.commit()
        with self.assertRaisesRegex(ValueError, "No Optional Days remain"):
            vacation_service.schedule_vacation_entitlement_day(
                person,
                date(self.YEAR, 12, 1),
                "optional_day",
                grandmaster,
                program="union",
            )
        db.session.rollback()
        rows = [db.session.get(StaffingVacationDaySelection, row.id) for row in rows]
        vacation_service.cancel_vacation_entitlement_day(
            rows[0], grandmaster, today=date(self.YEAR, 12, 1)
        )
        restored = vacation_service.schedule_vacation_entitlement_day(
            person,
            date(self.YEAR, 12, 1),
            "optional_day",
            grandmaster,
            program="union",
        )
        self.assertEqual(restored.item_type, "optional_day")
        self.assertNotEqual(
            vacation_service.optional_day_cycle(date(self.YEAR, 7, 31)),
            vacation_service.optional_day_cycle(date(self.YEAR, 8, 1)),
        )

    def test_floating_holiday_award_is_durable_idempotent_and_consumable(self):
        grandmaster = self._user("floating_gm", "grandmaster")
        calendar = self._calendar(grandmaster, [self.units["blue_area"].id])
        person = self._union_person(
            "FLT1", "Floating", "Employee", self.units["blue_area"]
        )
        selection = vacation_service.add_union_week(
            calendar,
            person,
            self.YEAR,
            date(self.YEAR, 7, 10),
            "regular",
            grandmaster,
        )
        db.session.commit()
        holiday = date(self.YEAR, 7, 4)
        first = vacation_service.award_floating_holidays_for_selection(
            selection, "union", {holiday: "Qualifying Holiday"}
        )
        db.session.commit()
        second = vacation_service.award_floating_holidays_for_selection(
            selection, "union", {holiday: "Qualifying Holiday"}
        )
        self.assertEqual(first[0].id, second[0].id)
        self.assertEqual(StaffingVacationDayEntitlement.query.count(), 1)
        used = vacation_service.schedule_vacation_entitlement_day(
            person,
            date(self.YEAR, 8, 15),
            "floating_holiday",
            grandmaster,
            program="union",
            entitlement_id=first[0].id,
        )
        self.assertEqual(used.entitlement_id, first[0].id)
        with self.assertRaisesRegex(ValueError, "already used"):
            vacation_service.schedule_vacation_entitlement_day(
                person,
                date(self.YEAR, 8, 16),
                "floating_holiday",
                grandmaster,
                program="union",
                entitlement_id=first[0].id,
            )

    def test_configured_floating_holidays_award_approved_week_once_each(self):
        grandmaster = self._user("configured_floating_gm", "grandmaster")
        vacation_service.save_qualifying_holiday(
            None, date(self.YEAR, 7, 4), "Independence Day", grandmaster
        )
        vacation_service.save_qualifying_holiday(
            None, date(self.YEAR, 7, 5), "Second Qualifier", grandmaster
        )
        calendar = self._calendar(grandmaster, [self.units["blue_area"].id])
        person = self._union_person(
            "FLT2", "Configured", "Employee", self.units["blue_area"]
        )

        selection = vacation_service.add_union_week(
            calendar,
            person,
            self.YEAR,
            date(self.YEAR, 7, 10),
            "regular",
            grandmaster,
        )
        db.session.commit()
        self.assertEqual(StaffingVacationDayEntitlement.query.count(), 2)

        vacation_service.reconcile_floating_holiday_entitlements()
        db.session.commit()
        self.assertEqual(StaffingVacationDayEntitlement.query.count(), 2)
        self.assertEqual(
            {
                row.source_holiday_date
                for row in StaffingVacationDayEntitlement.query.filter_by(
                    source_program="union", source_selection_id=selection.id
                ).all()
            },
            {date(self.YEAR, 7, 4), date(self.YEAR, 7, 5)},
        )

    def test_late_qualifying_date_reconciles_existing_approved_weeks(self):
        grandmaster = self._user("late_floating_gm", "grandmaster")
        calendar = self._calendar(grandmaster, [self.units["blue_area"].id])
        person = self._union_person(
            "FLT3", "Late", "Employee", self.units["blue_area"]
        )
        vacation_service.add_union_week(
            calendar,
            person,
            self.YEAR,
            date(self.YEAR, 7, 10),
            "regular",
            grandmaster,
        )
        db.session.commit()
        self.assertEqual(StaffingVacationDayEntitlement.query.count(), 0)

        vacation_service.save_qualifying_holiday(
            None, date(self.YEAR, 7, 4), "Late Qualifier", grandmaster
        )
        db.session.commit()

        award = StaffingVacationDayEntitlement.query.one()
        self.assertEqual(award.source_holiday_name, "Late Qualifier")

    def test_qualifying_date_edit_delete_preserves_consumed_durable_award(self):
        grandmaster = self._user("safe_floating_gm", "grandmaster")
        holiday = vacation_service.save_qualifying_holiday(
            None, date(self.YEAR, 7, 4), "Original Holiday", grandmaster
        )
        calendar = self._calendar(grandmaster, [self.units["blue_area"].id])
        person = self._union_person(
            "FLT4", "Safe", "Employee", self.units["blue_area"]
        )
        vacation_service.add_union_week(
            calendar,
            person,
            self.YEAR,
            date(self.YEAR, 7, 10),
            "regular",
            grandmaster,
        )
        award = StaffingVacationDayEntitlement.query.one()
        vacation_service.schedule_vacation_entitlement_day(
            person,
            date(self.YEAR, 8, 15),
            "floating_holiday",
            grandmaster,
            program="union",
            entitlement_id=award.id,
        )
        vacation_service.save_qualifying_holiday(
            holiday, date(self.YEAR, 7, 4), "Renamed Holiday", grandmaster
        )
        vacation_service.delete_qualifying_holiday(holiday, grandmaster)
        db.session.commit()

        preserved = db.session.get(StaffingVacationDayEntitlement, award.id)
        self.assertIsNotNone(preserved)
        self.assertEqual(preserved.source_holiday_name, "Renamed Holiday")
        self.assertEqual(StaffingVacationQualifyingHoliday.query.count(), 0)

    def test_qualifying_date_duplicate_authority_and_settings_csrf(self):
        master = self._user("holiday_master", "master")
        watcher = self._user("holiday_watcher", "watcher")
        vacation_service.save_qualifying_holiday(
            None, date(self.YEAR, 12, 25), "Holiday", master
        )
        with self.assertRaisesRegex(ValueError, "already exists"):
            vacation_service.save_qualifying_holiday(
                None, date(self.YEAR, 12, 25), "Duplicate", master
            )
        db.session.rollback()
        with self.assertRaisesRegex(ValueError, "read-only|Master access"):
            vacation_service.save_qualifying_holiday(
                None, date(self.YEAR, 1, 1), "Forbidden", watcher
            )

        self._login(master)
        self.app.config["CSRF_PROTECT_TESTING"] = True
        page = self.client.get("/neostaffing/settings")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"FLOATING HOLIDAYS", page.data)
        self.assertIn(b"Holiday", page.data)
        self.assertIn(b"Fixed date", page.data)
        token = re.search(
            r'<meta name="csrf-token" content="([^"]+)">',
            page.get_data(as_text=True),
        ).group(1)
        missing = self.client.post(
            "/neostaffing/settings/floating-holidays",
            data={
                "name": "No CSRF",
                "rule_type": "fixed_date",
                "month": "11",
                "day_of_month": "27",
            },
        )
        self.assertEqual(missing.status_code, 400)
        saved = self.client.post(
            "/neostaffing/settings/floating-holidays",
            data={
                "csrf_token": token,
                "name": "Thanksgiving",
                "rule_type": "nth_weekday",
                "month": "11",
                "weekday": "3",
                "occurrence": "4",
            },
        )
        self.assertEqual(saved.status_code, 302)
        self.assertEqual(StaffingVacationHolidayRule.query.count(), 1)
        with self.client.session_transaction() as session:
            session.clear()
        self._login(watcher)
        self.assertEqual(self.client.get("/neostaffing/settings").status_code, 302)

    def test_floating_holiday_reconciliation_uses_bounded_selects(self):
        grandmaster = self._user("bounded_floating_gm", "grandmaster")
        holiday = StaffingVacationQualifyingHoliday(
            holiday_date=date(self.YEAR, 7, 4),
            name="Bounded Holiday",
            created_by_user_id=grandmaster.id,
            updated_by_user_id=grandmaster.id,
        )
        db.session.add(holiday)
        for index in range(12):
            person = self._person(
                f"BF{index}", "Bounded", f"Person{index}", "2000-01-01", "part_time"
            )
            db.session.add(
                StaffingVacationUnionSelection(
                    staffing_person_id=person.id,
                    vacation_year=self.YEAR,
                    week_ending=date(self.YEAR, 7, 10),
                    bank_type="regular",
                    status="approved",
                    entered_by_user_id=grandmaster.id,
                )
            )
        db.session.commit()
        selects = []

        def record_select(_conn, _cursor, statement, _parameters, _context, _many):
            if statement.lstrip().upper().startswith("SELECT"):
                selects.append(statement)

        event.listen(db.engine, "before_cursor_execute", record_select)
        try:
            vacation_service.reconcile_floating_holiday_entitlements()
            db.session.flush()
        finally:
            event.remove(db.engine, "before_cursor_execute", record_select)
        self.assertLessEqual(len(selects), 5)
        self.assertEqual(StaffingVacationDayEntitlement.query.count(), 12)

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
        self.assertIn(b"DYNAMIC ORG CHART OWNERSHIP", management.data)
        self.assertIn(b"data-vacation-union-editor", editor.data)
        self.assertIn(b"SELECTING A PARENT SELECTS ALL CHILDREN", editor.data)
        self.assertIn(b'aria-current="page"', union.data)

    def test_lifecycle_controls_render_and_reset_requires_csrf(self):
        grandmaster = self._user("lifecycle_route_gm", "grandmaster")
        person = self._union_person(
            "LR1", "Lifecycle", "Employee", self.units["blue_area"]
        )
        calendar = self._calendar(
            grandmaster, [self.units["blue_area"].id]
        )
        selection = StaffingVacationUnionSelection(
            staffing_person_id=person.id,
            vacation_year=self.YEAR,
            week_ending=date(self.YEAR, 7, 10),
            bank_type="regular",
            status="approved",
        )
        db.session.add(selection)
        db.session.commit()
        selection_id = selection.id
        self._login(grandmaster)

        with patch.object(
            vacation_service,
            "official_calendar_carry_forward_candidates",
            return_value=[
                {
                    "calendar": calendar,
                    "owner": grandmaster,
                    "target_year": self.YEAR + 1,
                    "display_name": calendar.name,
                    "scope_label": "Night / Ramp / Blue Outbound / Blue Ramp",
                }
            ],
        ):
            page = self.client.get(
                f"/neostaffing/vacation-selection/union?year={self.YEAR + 1}"
            )
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"NEXT VACATION YEAR", page.data)
        self.assertIn(f"CREATE {self.YEAR + 1}".encode(), page.data)
        self.assertIn(b"NOT NOW", page.data)
        admin = self.client.get("/neostaffing/vacation-selection/union/admin")
        self.assertEqual(admin.status_code, 200)
        self.assertIn(b"RESET CALENDAR", admin.data)

        self.app.config["CSRF_PROTECT_TESTING"] = True
        response = self.client.post(
            f"/neostaffing/vacation-selection/union/{calendar.id}/reset",
            data={"vacation_year": self.YEAR},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIsNotNone(
            db.session.get(StaffingVacationUnionSelection, selection_id)
        )

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

    def test_read_only_page_ignores_redundant_view_threshold(self):
        user = self._user("vacation_below_threshold", "watcher")
        PermissionRule.query.filter_by(permission_key="neostaffing.vacation_selection.view").one().minimum_role = "operator"
        db.session.commit()
        self._login(user)
        page = self.client.get("/neostaffing/vacation-selection")
        self.assertEqual(page.status_code, 200)

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

    def _union_person(self, employee_id, first, last, work_area, seniority="2000-01-01"):
        person = self._person(employee_id, first, last, seniority, "part_time")
        db.session.add(
            StaffingWorkAssignment(person=person, work_area=work_area, active=True)
        )
        db.session.flush()
        return person

    def _union_actor(self, username, role, classification):
        employee_id = f"EMP-{username}"
        person = self._person(
            employee_id,
            username,
            "Supervisor",
            "1995-01-01",
            classification,
        )
        db.session.add(
            StaffingLeadershipAssignment(
                person=person,
                unit=self.units["blue_department"],
                leadership_level="department",
                active=True,
            )
        )
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
