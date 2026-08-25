from datetime import date, datetime
import re
import unittest

from sqlalchemy import event

from app import create_app
from app.extensions import db
from app.models import (
    PortalAppAccess,
    StaffingLeadershipAssignment,
    StaffingPerson,
    StaffingUnit,
    StaffingVacationManagementCapacity,
    StaffingVacationManagementSelection,
    StaffingVacationManagementTurnResolution,
    StaffingVacationManagementTurnState,
    StaffingVacationWeekConversion,
    StaffingVacationDaySelection,
    User,
)
from app.services import neostaffing_vacation as vacation_service
from app.services.password_policy import set_user_password
from app.services.permission_rules import ensure_default_permission_rules


class NeoStaffingManagementVacationPicksTest(unittest.TestCase):
    YEAR = 2027
    OPEN_DAY = date(2026, 11, 1)

    def setUp(self):
        config = type(
            "ManagementVacationPicksConfig",
            (),
            {
                "SECRET_KEY": "test",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
            },
        )
        self.app = create_app(config)
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

    def test_management_entitlement_thresholds_use_seniority_date(self):
        year_end = date(self.YEAR, 12, 31)
        cases = (
            (date(self.YEAR, 1, 1), 2),
            (date(year_end.year - 5, 12, 31), 3),
            (date(year_end.year - 10, 12, 31), 4),
            (date(year_end.year - 20, 12, 31), 5),
            (date(year_end.year - 25, 12, 31), 6),
        )
        for seniority_date, expected in cases:
            with self.subTest(seniority_date=seniority_date):
                self.assertEqual(
                    vacation_service.management_vacation_entitlement(
                        seniority_date,
                        self.YEAR,
                    ),
                    expected,
                )

    def test_initial_turn_seniority_junior_block_pass_and_later_pick(self):
        senior, senior_user = self._management_user(
            "MV1", "Zoe", "Able", "1990-01-01", "full_time_supervisor"
        )
        tied_first, tied_user = self._management_user(
            "MV2", "Amy", "Baker", "2000-01-01", "full_time_supervisor"
        )
        junior, junior_user = self._management_user(
            "MV3", "Zed", "Baker", "2000-01-01", "full_time_supervisor"
        )
        self._capacity(self.units["ramp"], 3)
        db.session.commit()

        context = vacation_service.management_vacation_context(
            self.YEAR,
            senior_user,
            today=self.OPEN_DAY,
        )
        area = self._area_row(context, self.units["ramp"])
        self.assertEqual(
            [row["person"].id for row in area["person_rows"]],
            [senior.id, tied_first.id, junior.id],
        )
        self.assertEqual(area["turn"].current_person_id, senior.id)

        with self.assertRaisesRegex(ValueError, "has not reached"):
            vacation_service.add_management_week(
                junior,
                self.YEAR,
                date(2027, 1, 2),
                junior_user,
                today=self.OPEN_DAY,
            )
        self.assertEqual(StaffingVacationManagementSelection.query.count(), 0)

        vacation_service.pass_management_turn(
            self.YEAR,
            self.units["ramp"].id,
            senior,
            senior_user,
            today=self.OPEN_DAY,
        )
        db.session.commit()
        state = StaffingVacationManagementTurnState.query.one()
        self.assertEqual(state.current_person_id, tied_first.id)
        self.assertEqual(state.resolutions[0].outcome, "passed")

        vacation_service.add_management_week(
            senior,
            self.YEAR,
            date(2027, 1, 2),
            senior_user,
            today=self.OPEN_DAY,
        )
        db.session.commit()
        self.assertEqual(
            vacation_service.management_vacation_entitlement(
                senior.seniority_date,
                self.YEAR,
            )
            - 1,
            self._remaining(senior),
        )

    def test_manual_pass_authority_allows_ft_and_manager_but_not_pt(self):
        current, _current_user = self._management_user(
            "MV10", "Current", "Senior", "1990-01-01", "full_time_supervisor"
        )
        ft_admin, ft_user = self._management_user(
            "MV11", "Admin", "FT", "2000-01-01", "full_time_supervisor"
        )
        manager, manager_user = self._management_user(
            "MV12", "Area", "Manager", "1995-01-01", "manager"
        )
        self._capacity(self.units["ramp"], 2)
        db.session.commit()

        vacation_service.pass_management_turn(
            self.YEAR,
            self.units["ramp"].id,
            current,
            ft_user,
            administrative=True,
            today=self.OPEN_DAY,
        )
        db.session.commit()
        self.assertEqual(
            StaffingVacationManagementTurnResolution.query.one().outcome,
            "admin_passed",
        )

        state = StaffingVacationManagementTurnState.query.one()
        next_person = db.session.get(StaffingPerson, state.current_person_id)
        vacation_service.pass_management_turn(
            self.YEAR,
            self.units["ramp"].id,
            next_person,
            manager_user,
            administrative=True,
            today=self.OPEN_DAY,
        )
        db.session.commit()

        pt_target, _ = self._management_user(
            "MV13", "Target", "PT", "1990-01-01", "part_time_supervisor"
        )
        pt_actor, pt_user = self._management_user(
            "MV14", "Actor", "PT", "2000-01-01", "part_time_supervisor"
        )
        self._capacity(self.units["blue_department"], 2)
        db.session.commit()
        with self.assertRaisesRegex(ValueError, "do not have authority"):
            vacation_service.pass_management_turn(
                self.YEAR,
                self.units["blue_department"].id,
                pt_target,
                pt_user,
                administrative=True,
                today=self.OPEN_DAY,
            )

    def test_capacity_and_bank_are_atomic_and_prevent_double_consumption(self):
        senior, senior_user = self._management_user(
            "MV20", "Senior", "One", "1990-01-01", "full_time_supervisor"
        )
        junior, junior_user = self._management_user(
            "MV21", "Junior", "Two", "2000-01-01", "full_time_supervisor"
        )
        self._capacity(self.units["ramp"], 1)
        db.session.commit()
        full_week = date(2027, 2, 6)
        other_week = date(2027, 2, 13)

        vacation_service.add_management_week(
            senior,
            self.YEAR,
            full_week,
            senior_user,
            today=self.OPEN_DAY,
        )
        vacation_service.pass_management_turn(
            self.YEAR,
            self.units["ramp"].id,
            senior,
            senior_user,
            today=self.OPEN_DAY,
        )
        db.session.commit()
        with self.assertRaisesRegex(ValueError, "capacity is full"):
            vacation_service.add_management_weeks(
                junior,
                self.YEAR,
                [other_week, full_week],
                junior_user,
                today=self.OPEN_DAY,
            )
        db.session.rollback()
        self.assertEqual(
            StaffingVacationManagementSelection.query.filter_by(
                staffing_person_id=junior.id
            ).count(),
            0,
        )
        self.assertEqual(StaffingVacationManagementSelection.query.count(), 1)

    def test_transfer_follows_person_grandfathers_pick_and_advances_old_turn(self):
        senior, senior_user = self._management_user(
            "MV30", "Senior", "Transfer", "1990-01-01", "full_time_supervisor"
        )
        junior, _junior_user = self._management_user(
            "MV31", "Junior", "Remain", "2000-01-01", "full_time_supervisor"
        )
        self._capacity(self.units["ramp"], 1)
        self._capacity(self.units["hub"], 0)
        db.session.commit()
        week = date(2027, 3, 6)
        vacation_service.add_management_week(
            senior,
            self.YEAR,
            week,
            senior_user,
            today=self.OPEN_DAY,
        )
        db.session.commit()
        old_state = StaffingVacationManagementTurnState.query.one()
        self.assertEqual(old_state.current_person_id, senior.id)

        old_assignment = StaffingLeadershipAssignment.query.filter_by(
            person_id=senior.id,
            active=True,
        ).one()
        old_assignment.active = False
        db.session.add(
            StaffingLeadershipAssignment(
                person=senior,
                unit=self.units["hub_department"],
                leadership_level="department",
                active=True,
            )
        )
        db.session.flush()
        vacation_service.reconcile_management_person_state(
            senior,
            today=self.OPEN_DAY,
        )
        db.session.commit()

        self.assertEqual(old_state.current_person_id, junior.id)
        context = vacation_service.management_vacation_context(
            self.YEAR,
            senior_user,
            today=self.OPEN_DAY,
        )
        ramp = self._area_row(context, self.units["ramp"])
        hub = self._area_row(context, self.units["hub"])
        self.assertEqual(ramp["week_rows"][9]["used"], 0)
        selected_week = next(row for row in hub["week_rows"] if row["week"].week_ending == week)
        self.assertEqual((selected_week["used"], selected_week["limit"]), (1, 0))
        self.assertEqual(StaffingVacationManagementSelection.query.count(), 1)

    def test_completed_turn_does_not_reopen_for_new_supervisor(self):
        only, only_user = self._management_user(
            "MV40", "Only", "Existing", "1995-01-01", "full_time_supervisor"
        )
        self._capacity(self.units["ramp"], 2)
        db.session.commit()
        vacation_service.pass_management_turn(
            self.YEAR,
            self.units["ramp"].id,
            only,
            only_user,
            today=self.OPEN_DAY,
        )
        db.session.commit()
        state = StaffingVacationManagementTurnState.query.one()
        self.assertIsNotNone(state.completed_at)

        newcomer, newcomer_user = self._management_user(
            "MV41", "New", "Supervisor", "1980-01-01", "full_time_supervisor"
        )
        context = vacation_service.management_vacation_context(
            self.YEAR,
            newcomer_user,
            today=self.OPEN_DAY,
        )
        row = self._area_row(context, self.units["ramp"])
        self.assertTrue(row["turn"].completed)
        self.assertIsNone(row["turn"].current_person_id)
        vacation_service.add_management_week(
            newcomer,
            self.YEAR,
            date(2027, 4, 3),
            newcomer_user,
            today=self.OPEN_DAY,
        )
        db.session.commit()
        self.assertEqual(self._remaining(newcomer), 5)

    def test_termination_removes_future_capacity_but_preserves_past_history(self):
        person, user = self._management_user(
            "MV50", "Departing", "Supervisor", "1990-01-01", "full_time_supervisor"
        )
        self._capacity(self.units["ramp"], 2)
        db.session.commit()
        past_week = date(2027, 1, 2)
        future_week = date(2027, 7, 3)
        vacation_service.add_management_weeks(
            person,
            self.YEAR,
            [past_week, future_week],
            user,
            today=self.OPEN_DAY,
        )
        db.session.commit()

        person.active = False
        vacation_service.reconcile_management_person_state(
            person,
            today=date(2027, 6, 15),
        )
        db.session.commit()
        rows = StaffingVacationManagementSelection.query.order_by(
            StaffingVacationManagementSelection.week_ending
        ).all()
        self.assertIsNone(rows[0].cancelled_at)
        self.assertIsNotNone(rows[1].cancelled_at)
        self.assertEqual(rows[1].cancellation_reason, "left_management")
        self.assertEqual(len(rows), 2)

    def test_management_pick_routes_enforce_csrf_and_server_authority(self):
        senior, user = self._management_user(
            "MV60", "Route", "Supervisor", "1990-01-01", "full_time_supervisor"
        )
        self._capacity(self.units["ramp"], 1, year=2026)
        db.session.commit()
        self._login(user)
        self.app.config["CSRF_PROTECT_TESTING"] = True
        page = self.client.get("/neostaffing/vacation-selection/management?year=2026")
        token = re.search(
            r'<meta name="csrf-token" content="([^"]+)">',
            page.get_data(as_text=True),
        ).group(1)
        values = {
            "vacation_year": "2026",
            "staffing_person_id": str(senior.id),
            "week_endings": "2026-12-05",
        }
        missing = self.client.post(
            "/neostaffing/vacation-selection/management/select",
            data=values,
        )
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(StaffingVacationManagementSelection.query.count(), 0)
        values["csrf_token"] = token
        saved = self.client.post(
            "/neostaffing/vacation-selection/management/select",
            data=values,
        )
        self.assertEqual(saved.status_code, 302)
        self.assertEqual(StaffingVacationManagementSelection.query.count(), 1)

    def test_management_context_queries_remain_bounded_as_roster_grows(self):
        viewer = None
        for index in range(40):
            _person, user = self._management_user(
                f"MVQ{index:02d}",
                f"First{index:02d}",
                f"Last{index:02d}",
                f"{1980 + index % 20}-01-01",
                "full_time_supervisor",
            )
            viewer = viewer or user
        self._capacity(self.units["ramp"], 4)
        db.session.commit()
        db.session.expire_all()
        select_count = 0

        def count_selects(_connection, _cursor, statement, _parameters, _context, _many):
            nonlocal select_count
            if statement.lstrip().upper().startswith("SELECT"):
                select_count += 1

        event.listen(db.engine, "before_cursor_execute", count_selects)
        try:
            context = vacation_service.management_vacation_context(
                self.YEAR,
                viewer,
                today=self.OPEN_DAY,
            )
        finally:
            event.remove(db.engine, "before_cursor_execute", count_selects)

        self.assertEqual(
            len(self._area_row(context, self.units["ramp"])["person_rows"]),
            40,
        )
        # Day selections and durable Floating Holiday awards are two additional
        # bounded collections; query count must remain roster-size invariant.
        self.assertLessEqual(select_count, 12)

    def test_management_split_schedule_cancel_and_recombine(self):
        person, person_user = self._management_user(
            "MVS1", "Split", "Supervisor", "1990-01-01", "full_time_supervisor"
        )
        _manager, manager_user = self._management_user(
            "MVS2", "Area", "Manager", "1990-01-01", "manager"
        )
        self._capacity(self.units["ramp"], 2)
        db.session.commit()
        selected = vacation_service.add_management_week(
            person, self.YEAR, date(2027, 7, 3), person_user, today=self.OPEN_DAY
        )
        db.session.commit()

        conversion = vacation_service.split_management_week(
            person,
            self.YEAR,
            manager_user,
            selection=selected,
            today=date(2027, 1, 1),
        )
        db.session.commit()
        self.assertEqual(conversion.program, "management")
        self.assertIsNotNone(selected.cancelled_at)
        self.assertEqual(selected.cancellation_reason, "split")
        self.assertEqual(len(conversion.days), 0)
        context = vacation_service.management_vacation_context(
            self.YEAR, manager_user, today=date(2027, 1, 1)
        )
        person_row = next(
            row for row in self._area_row(context, self.units["ramp"])["person_rows"]
            if row["person"].id == person.id
        )
        self.assertEqual(person_row["split_day_balance"], 5)

        day = vacation_service.schedule_split_vacation_day(
            conversion, date(2027, 2, 10), manager_user
        )
        db.session.commit()
        self.assertEqual(day.status, "scheduled")
        context = vacation_service.management_vacation_context(
            self.YEAR, manager_user, today=date(2027, 1, 1)
        )
        person_row = next(
            row for row in self._area_row(context, self.units["ramp"])["person_rows"]
            if row["person"].id == person.id
        )
        self.assertEqual(person_row["split_day_balance"], 4)
        with self.assertRaisesRegex(ValueError, "time-off item"):
            vacation_service.schedule_split_vacation_day(
                conversion, date(2027, 2, 10), manager_user
            )
        db.session.rollback()
        vacation_service.cancel_split_vacation_day(day, manager_user)
        db.session.commit()
        self.assertEqual(day.status, "cancelled")
        self._login(manager_user)
        rendered = self.client.get(
            f"/neostaffing/vacation-selection/management?year={self.YEAR}"
        )
        self.assertEqual(rendered.status_code, 200)
        self.assertIn(b"5 / 5 SPLIT DAYS AVAILABLE", rendered.data)
        vacation_service.recombine_split_vacation_week(conversion, manager_user)
        db.session.commit()
        self.assertIsNotNone(conversion.recombined_at)

    def test_management_split_authority_future_boundary_and_consumed_day(self):
        person, person_user = self._management_user(
            "MVS3", "Boundary", "Supervisor", "1990-01-01", "full_time_supervisor"
        )
        _manager, manager_user = self._management_user(
            "MVS4", "Boundary", "Manager", "1990-01-01", "manager"
        )
        self._capacity(self.units["ramp"], 2)
        db.session.commit()
        selected = vacation_service.add_management_week(
            person, self.YEAR, date(2027, 1, 9), person_user, today=self.OPEN_DAY
        )
        db.session.commit()
        with self.assertRaisesRegex(ValueError, "authorized Manager"):
            vacation_service.split_management_week(
                person, self.YEAR, person_user, selection=selected, today=date(2027, 1, 1)
            )
        with self.assertRaisesRegex(ValueError, "started or past"):
            vacation_service.split_management_week(
                person, self.YEAR, manager_user, selection=selected, today=date(2027, 1, 3)
            )
        db.session.rollback()

        conversion = vacation_service.split_management_week(
            person, self.YEAR, manager_user, today=date(2026, 12, 1)
        )
        db.session.commit()
        past_day = vacation_service.schedule_split_vacation_day(
            conversion, date(2027, 1, 2), manager_user
        )
        db.session.commit()
        with self.assertRaisesRegex(ValueError, "Cancel all scheduled"):
            vacation_service.recombine_split_vacation_week(conversion, manager_user)
        db.session.rollback()
        vacation_service.cancel_split_vacation_day(past_day, manager_user)
        vacation_service.recombine_split_vacation_week(conversion, manager_user)
        db.session.commit()
        self.assertEqual(StaffingVacationDaySelection.query.count(), 1)

    def test_management_split_route_requires_csrf(self):
        person, _person_user = self._management_user(
            "MVS5", "Route", "Supervisor", "1990-01-01", "full_time_supervisor"
        )
        _manager, manager_user = self._management_user(
            "MVS6", "Route", "Manager", "1990-01-01", "manager"
        )
        db.session.commit()
        self._login(manager_user)
        self.app.config["CSRF_PROTECT_TESTING"] = True
        page = self.client.get(f"/neostaffing/vacation-selection/management?year={self.YEAR}")
        token = re.search(r'<meta name="csrf-token" content="([^"]+)">', page.get_data(as_text=True)).group(1)
        values = {"vacation_year": self.YEAR, "staffing_person_id": person.id}
        self.assertEqual(self.client.post("/neostaffing/vacation-selection/management/split", data=values).status_code, 400)
        self.assertEqual(StaffingVacationWeekConversion.query.count(), 0)
        values["csrf_token"] = token
        self.assertEqual(self.client.post("/neostaffing/vacation-selection/management/split", data=values).status_code, 302)
        self.assertEqual(StaffingVacationWeekConversion.query.count(), 1)

    def _hierarchy(self):
        night = StaffingUnit(unit_type="sort", name="Night", display_order=1)
        ramp = StaffingUnit(unit_type="operation", name="Ramp", parent=night, display_order=1)
        hub = StaffingUnit(unit_type="operation", name="Hub", parent=night, display_order=2)
        blue = StaffingUnit(unit_type="department", name="Blue", parent=ramp, display_order=1)
        hub_department = StaffingUnit(unit_type="department", name="Hub Ops", parent=hub, display_order=1)
        db.session.add_all([night, ramp, hub, blue, hub_department])
        db.session.flush()
        return {
            "night": night,
            "ramp": ramp,
            "hub": hub,
            "blue_department": blue,
            "hub_department": hub_department,
        }

    def test_management_day_entitlements_cycles_exclusivity_and_correction(self):
        person, actor = self._management_user(
            "MVD1", "Day", "Supervisor", "2000-03-15", "full_time_supervisor"
        )
        db.session.commit()
        scheduled = []
        for day_number in range(1, 6):
            scheduled.append(
                vacation_service.schedule_vacation_entitlement_day(
                    person,
                    date(self.YEAR, 1, day_number),
                    "d_day",
                    actor,
                    program="management",
                )
            )
        db.session.commit()
        with self.assertRaisesRegex(ValueError, "No D-Days remain"):
            vacation_service.schedule_vacation_entitlement_day(
                person, date(self.YEAR, 1, 6), "d_day", actor, program="management"
            )
        db.session.rollback()
        scheduled = [
            db.session.get(StaffingVacationDaySelection, row.id) for row in scheduled
        ]
        vacation_service.cancel_vacation_entitlement_day(
            scheduled[0], actor, today=date(self.YEAR, 2, 1)
        )
        replacement = vacation_service.schedule_vacation_entitlement_day(
            person, date(self.YEAR, 1, 6), "d_day", actor, program="management"
        )
        self.assertEqual(replacement.item_type, "d_day")
        with self.assertRaisesRegex(ValueError, "already has a time-off item"):
            vacation_service.schedule_vacation_entitlement_day(
                person,
                replacement.vacation_date,
                "anniversary_day",
                actor,
                program="management",
                today=date(self.YEAR, 12, 31),
            )

    def test_anniversary_day_is_available_only_on_actual_anniversary(self):
        person, actor = self._management_user(
            "MVA1", "Ann", "Supervisor", "2000-03-15", "full_time_supervisor"
        )
        with self.assertRaisesRegex(ValueError, "actual anniversary"):
            vacation_service.schedule_vacation_entitlement_day(
                person,
                date(self.YEAR, 3, 14),
                "anniversary_day",
                actor,
                program="management",
                today=date(self.YEAR, 3, 15),
            )
        row = vacation_service.schedule_vacation_entitlement_day(
            person,
            date(self.YEAR, 3, 15),
            "anniversary_day",
            actor,
            program="management",
            today=date(self.YEAR, 3, 15),
        )
        self.assertEqual(row.item_type, "anniversary_day")

    def test_special_assignment_and_corporate_class_eligibility_and_exclusivity(self):
        ft, ft_user = self._management_user(
            "MVP1", "Pinned", "FT", "1990-01-01", "full_time_supervisor"
        )
        manager, manager_user = self._management_user(
            "MVP2", "Pinned", "Manager", "1990-01-01", "manager"
        )
        pt, pt_user = self._management_user(
            "MVP3", "Pinned", "PT", "1990-01-01", "part_time_supervisor"
        )
        special = vacation_service.schedule_management_availability_day(
            ft, date(self.YEAR, 4, 5), "special_assignment", ft_user
        )
        corporate = vacation_service.schedule_management_availability_day(
            manager, date(self.YEAR, 4, 6), "corporate_class", manager_user
        )
        self.assertEqual(
            (special.item_type, corporate.item_type),
            ("special_assignment", "corporate_class"),
        )
        with self.assertRaisesRegex(ValueError, "Only an FT Supervisor or Manager"):
            vacation_service.schedule_management_availability_day(
                pt, date(self.YEAR, 4, 7), "special_assignment", ft_user
            )
        with self.assertRaisesRegex(ValueError, "do not have authority"):
            vacation_service.schedule_management_availability_day(
                ft, date(self.YEAR, 4, 8), "corporate_class", pt_user
            )
        with self.assertRaisesRegex(ValueError, "already has a time-off item"):
            vacation_service.schedule_vacation_entitlement_day(
                ft,
                special.vacation_date,
                "d_day",
                ft_user,
                program="management",
            )
        db.session.rollback()

    def test_pinned_unavailability_selects_limits_grandfathers_and_recalculates(self):
        pt_one, _ = self._management_user(
            "MVC1", "One", "PT", "1990-01-01", "part_time_supervisor"
        )
        pt_two, _ = self._management_user(
            "MVC2", "Two", "PT", "1991-01-01", "part_time_supervisor"
        )
        ft_one, ft_user = self._management_user(
            "MVC3", "One", "FT", "1980-01-01", "full_time_supervisor"
        )
        ft_two, _ = self._management_user(
            "MVC4", "Two", "FT", "1981-01-01", "full_time_supervisor"
        )
        self._capacity(self.units["blue_department"], 3)
        capacity = StaffingVacationManagementCapacity.query.filter_by(
            area_unit_id=self.units["blue_department"].id
        ).one()
        capacity.one_pinned_limit = 2
        capacity.two_plus_pinned_limit = 1
        week_ending = date(self.YEAR, 5, 8)
        db.session.add_all(
            [
                StaffingVacationManagementSelection(
                    staffing_person_id=person.id,
                    vacation_year=self.YEAR,
                    week_ending=week_ending,
                )
                for person in (pt_one, pt_two)
            ]
        )
        first = vacation_service.schedule_management_availability_day(
            ft_one, date(self.YEAR, 5, 3), "special_assignment", ft_user
        )
        second = vacation_service.schedule_management_availability_day(
            ft_two, date(self.YEAR, 5, 4), "corporate_class", ft_user
        )
        db.session.commit()

        context = vacation_service.management_vacation_context(
            self.YEAR, ft_user, today=self.OPEN_DAY
        )
        area = self._area_row(context, self.units["blue_department"])
        week = next(
            row for row in area["week_rows"] if row["week"].week_ending == week_ending
        )
        self.assertEqual(
            (week["pinned_unavailable_count"], week["limit"], week["over"]),
            (2, 1, True),
        )
        self.assertEqual(len(area["pinned_rows"]), 2)

        vacation_service.cancel_management_availability_day(second, ft_user)
        db.session.commit()
        context = vacation_service.management_vacation_context(
            self.YEAR, ft_user, today=self.OPEN_DAY
        )
        area = self._area_row(context, self.units["blue_department"])
        week = next(
            row for row in area["week_rows"] if row["week"].week_ending == week_ending
        )
        self.assertEqual(
            (week["pinned_unavailable_count"], week["limit"], week["over"]),
            (1, 2, False),
        )

        vacation_service.set_reduced_capacity_enabled(
            self.YEAR,
            self.units["blue_department"].id,
            week_ending,
            False,
            ft_user,
        )
        db.session.commit()
        context = vacation_service.management_vacation_context(
            self.YEAR, ft_user, today=self.OPEN_DAY
        )
        area = self._area_row(context, self.units["blue_department"])
        week = next(
            row for row in area["week_rows"] if row["week"].week_ending == week_ending
        )
        self.assertEqual((week["limit"], week["over"]), (3, False))
        self.assertEqual(
            vacation_service.management_capacity_limit(capacity, 0), 3
        )
        self.assertEqual(
            vacation_service.management_capacity_limit(capacity, 1), 2
        )
        self.assertEqual(
            vacation_service.management_capacity_limit(capacity, 2), 1
        )
        self.assertEqual(
            vacation_service.management_capacity_limit(
                capacity, 2, reduced_capacity_on=False
            ),
            3,
        )

    def test_availability_route_requires_csrf(self):
        ft, ft_user = self._management_user(
            "MVR1", "Route", "FT", "1990-01-01", "full_time_supervisor"
        )
        self._login(ft_user)
        self.app.config["CSRF_PROTECT_TESTING"] = True
        response = self.client.post(
            "/neostaffing/vacation-selection/management/availability",
            data={
                "vacation_year": self.YEAR,
                "staffing_person_id": ft.id,
                "availability_date": f"{self.YEAR}-06-01",
                "item_type": "special_assignment",
            },
        )
        self.assertEqual(response.status_code, 400)

    def _management_user(
        self,
        employee_id,
        first_name,
        last_name,
        seniority,
        classification,
        unit=None,
        app_role="simulator",
    ):
        person = StaffingPerson(
            employee_id=employee_id,
            first_name=first_name,
            last_name=last_name,
            seniority_date=date.fromisoformat(seniority),
            classification=classification,
            employee_status="active",
            active=True,
        )
        db.session.add(person)
        db.session.flush()
        leadership_unit = unit or self.units["blue_department"]
        db.session.add(
            StaffingLeadershipAssignment(
                person=person,
                unit=leadership_unit,
                leadership_level=leadership_unit.unit_type,
                active=True,
            )
        )
        user = User(
            username=f"user_{employee_id.lower()}",
            email=f"{employee_id.lower()}@example.com",
            first_name=first_name,
            last_name=last_name,
            full_name=f"{first_name} {last_name}",
            employee_id=employee_id,
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
                role=app_role,
                is_active=True,
                approved_at=datetime.utcnow(),
            )
        )
        db.session.commit()
        return person, user

    def _capacity(self, area, limit, year=None):
        db.session.add(
            StaffingVacationManagementCapacity(
                vacation_year=year or self.YEAR,
                area_unit_id=area.id,
                normal_limit=limit,
                one_pinned_limit=limit,
                two_plus_pinned_limit=limit,
            )
        )

    def _remaining(self, person):
        active = StaffingVacationManagementSelection.query.filter_by(
            staffing_person_id=person.id,
            vacation_year=self.YEAR,
            cancelled_at=None,
        ).count()
        return vacation_service.management_vacation_entitlement(
            person.seniority_date,
            self.YEAR,
        ) - active

    @staticmethod
    def _area_row(context, area):
        return next(row for row in context["areas"] if row["area"].id == area.id)

    def _login(self, user):
        return self.client.post(
            "/login",
            data={"username": user.username, "password": "TestPassword123!"},
        )


if __name__ == "__main__":
    unittest.main()
