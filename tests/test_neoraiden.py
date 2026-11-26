import re
import unittest
from hashlib import sha256
from pathlib import Path
from datetime import date, datetime, time, timedelta
from unittest.mock import patch
from sqlalchemy import event

from app.extensions import db
from app.models import Gateway, GatewayMembership, GatewayNodeRole, NeoErmacDoorPull, NeoNode, SortDateMission, SortDateOperation, SortDateParkingAssignment
from app.models.account_preference import AccountOperationalScreen
from app.neonodes.neoraiden.services import snapshot
from tests import test_gateway_shell as fixtures


def seed_sort(gateway):
    op = SortDateOperation(gateway_id=gateway.id, gateway_code=gateway.code, sort_date=date(2026, 4, 12), sort_name="night")
    db.session.add(op)
    db.session.flush()
    missions = []
    for number, tail, hour, eta in (("FX101","N101",4,6), ("FX202","N202",5,5)):
        m = SortDateMission(sort_date_operation_id=op.id, sort_date=op.sort_date, sort_name=op.sort_name,
            gateway_code=gateway.code, flight_number=number, assigned_tail_number=tail, origin=gateway.code,
            destination="OAK", mission_type="departure", planned_datetime_utc=datetime(2026,4,13,hour),
            eta_datetime_utc=datetime(2026,4,13,eta))
        db.session.add(m)
        missions.append(m)
    db.session.flush()
    db.session.add(SortDateParkingAssignment(sort_date_operation_id=op.id,tail_number="N202",ramp_code="B",position_code="B02"))
    db.session.add(NeoErmacDoorPull(gateway_id=gateway.id,sort_date_operation_id=op.id,sort_date_mission_id=missions[0].id,
        destination="OAK",door="D13", actual_pure_pull_time_local=time(1,25)))
    db.session.commit()
    return op


class RaidenTest(unittest.TestCase):
    setUp = fixtures.GatewayShellTest.setUp
    tearDown = fixtures.GatewayShellTest.tearDown

    def test_wave_stats_use_canonical_eligibility_and_weighted_two_wave_total(self):
        from types import SimpleNamespace
        from app.neonodes.neorain.services import _late_summary
        from app.neonodes.neoraiden.services import _flight_performance, _live_arrival_performance

        sta = datetime(2026, 4, 13, 5)
        def arrival(wave, minutes, *, include=None, status="arrived"):
            return SimpleNamespace(mission_type="arrival", arrival_status=status, wave=wave,
                planned_datetime_utc=sta, eta_datetime_utc=sta + timedelta(hours=1),
                actual_block_in_datetime_utc=sta + timedelta(minutes=minutes) if minutes is not None else None,
                late_metrics_included_override=include)

        pending = arrival("2", None, status="en_route")
        zero = arrival("2", 0)
        self.assertIsNone(_flight_performance([pending])["combined"])
        self.assertEqual(_flight_performance([zero])["combined"], dict(aircraft_late=0, late_minutes=0, average="0"))
        rows = [arrival("1", 8), arrival("1", 12), arrival("2", 40),
                arrival("2", 90, include=False), arrival("1", 65, status="cancelled"),
                arrival("3", 120, include=True), pending, zero]
        projection = _flight_performance(rows)
        summary = _late_summary(rows)
        self.assertEqual(projection["first_wave"], summary["first_wave"])
        self.assertEqual(projection["second_wave"], summary["second_wave"])
        self.assertEqual(projection["combined"], dict(aircraft_late=3, late_minutes=60, average="20"))
        self.assertEqual(summary["total"]["aircraft_late"], 4)
        live_average, live_waves = _live_arrival_performance(zip(rows, (8, 12, 40, 90, None, 120, None, 0), strict=True))
        self.assertEqual(live_waves, projection)
        self.assertEqual(live_average, {"overall": 54.0, "included": 45.0})

        from flask import render_template
        from app.services.neorain_performance import average_lateness
        with self.app.test_request_context():
            html = render_template("neonodes/neoraiden/_board.html", board=dict(
                operation_id=1, sort_label="Night", arrivals=[], departures=[], uld=None, marked_here=None,
                wave_performance={"inbound": projection}, performance={"inbound": average_lateness(rows)},
            ))
        self.assertIn("<dt>Overall</dt><dd>54.0</dd>", html)
        self.assertIn("<dt>With Removed</dt><dd>45.0</dd>", html)

    def test_load_complete_mismatch_compares_instants_and_respects_rain_access(self):
        op = seed_sort(self.gateway)
        departure = SortDateMission.query.filter_by(flight_number="FX101").one()
        ramp = datetime(2026, 4, 13, 4, 59)
        departure.ramp_load_completed_at_utc = ramp
        with self.app.test_request_context():
            for interval, expected in ((None, False), (0, False), (60, False), (61, True), (-61, True), (86400, True)):
                with self.subTest(interval=interval):
                    departure.crew_load_completed_at_utc = ramp + timedelta(seconds=interval) if interval is not None else None
                    db.session.flush()
                    board = snapshot(self.user, self.gateway, op, sources=({"rain"}, None))
                    row = next(row for row in board["departures"] if row["id"] == departure.id)
                    self.assertIs(row["load_mismatch"], expected)
            restricted = snapshot(self.user, self.gateway, op, sources=(set(), None))
            for row in restricted["departures"]:
                self.assertNotIn("load_mismatch", row)
                self.assertNotIn("no_return", row)

    def test_native_staffing_distinguishes_missing_zero_and_here(self):
        from app.models import StaffingDailyAttendance
        from tests.neoraiden_fixtures import record_attendance

        op = seed_sort(self.gateway)
        with self.app.test_request_context(), patch("requests.sessions.Session.request",
                side_effect=AssertionError("native staffing attempted external HTTP")):
            def here(operation):
                return snapshot(self.user, self.gateway, operation)["marked_here"]

            self.assertIsNone(here(op))
            record_attendance(op)
            self.assertEqual(here(op), 1)
            attendance = StaffingDailyAttendance.query.filter_by(sort_date_operation_id=op.id)
            attendance.filter_by(status="here").one().status = "vacation"
            db.session.commit()
            self.assertEqual(here(op), 0)
            other = SortDateOperation(gateway_id=self.gateway.id, gateway_code=self.gateway.code,
                                      sort_date=date(2026, 4, 13), sort_name="night")
            db.session.add(other)
            db.session.commit()
            self.assertIsNone(here(other))

    def test_automatic_state_is_bounded_by_physical_sort_hours(self):
        from app.models import LiveScreenRefreshSetting
        from tests.test_neorain_sort_refresh import configure_sort_window
        op = seed_sort(self.gateway)
        _, start = configure_sort_window(self.gateway, op.sort_date)
        path = f"/neoraiden/state?operation_id={op.id}"
        for minutes, status in ((-1,204),(0,200),(479,200),(480,204),(1440,204)):
            self.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = start + timedelta(minutes=minutes)
            self.assertEqual(self.client.get(path, headers={"X-Raiden-Automatic":"1"}).status_code, status)
        self.assertEqual(self.client.get(path).status_code, 200)
        op.archived_at_utc = start
        db.session.commit()
        self.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = start
        self.assertEqual(self.client.get(path, headers={"X-Raiden-Automatic":"1"}).status_code, 204)
        op.archived_at_utc = None
        db.session.add(LiveScreenRefreshSetting(gateway_id=self.gateway.id, screen_key="neoraiden.index", interval_seconds=0))
        db.session.commit()
        off = self.client.get(path, headers={"X-Raiden-Automatic":"1"})
        self.assertEqual((off.status_code, off.headers["X-Raiden-Interval-Ms"]), (204, "0"))

    def test_arrival_order_uses_displayed_time_and_overnight_window(self):
        from tests.test_neorain_sort_refresh import configure_sort_window

        op = seed_sort(self.gateway)
        configure_sort_window(self.gateway, op.sort_date)
        first = SortDateMission.query.filter_by(flight_number="FX101").one()
        second = SortDateMission.query.filter_by(flight_number="FX202").one()
        first.wave = second.wave = "1"
        start = datetime(2026, 4, 13, 2)  # 21:00 at RFD

        def add(flight, planned=None, *, eta=None, actual=None, origin="SDF", status="scheduled"):
            mission = SortDateMission(
                sort_date_operation_id=op.id, sort_date=op.sort_date, sort_name=op.sort_name,
                gateway_code=self.gateway.code, mission_type="arrival", flight_number=flight,
                origin=origin, destination=self.gateway.code, arrival_status=status,
                planned_datetime_utc=start + timedelta(minutes=planned) if planned is not None else None,
                eta_datetime_utc=start + timedelta(minutes=eta) if eta is not None else None,
                actual_block_in_datetime_utc=start + timedelta(minutes=actual) if actual is not None else None,
            )
            db.session.add(mission)
            return mission

        missing = add("NO-TIME", status="cancelled")
        late = add("UPS0612", -1145)  # Legacy STA 01:55 on the Sort date
        no_overnight = add("OUTSIDE", 495)
        hot = add("HOT-01", 10, origin="HOT")
        eta = add("UPS1361", 110, eta=55)
        actual = add("UPS0540", 125, eta=250, actual=65)
        midnight = add("UPS0990", 180, eta=182)
        first_sta = add("UPS0927", -1200)  # Legacy STA 01:00 on the Sort date
        middle_sta = add("UPS0616")
        middle_sta.planned_datetime_local = datetime(2026, 4, 12, 1, 30)
        tied = add("UPS0888", 55)
        db.session.commit()

        def read():
            with self.app.test_request_context():
                return snapshot(self.user, self.gateway, op)

        board = read()
        self.assertEqual([row["id"] for row in board["arrivals"]],
                         [m.id for m in (eta, tied, actual, midnight, first_sta, middle_sta, late,
                                         missing, no_overnight, hot)])
        self.assertEqual([(row["flight"], row["route_label"], row["tail"], row["parking"])
                          for row in board["departures"]],
                         [("FX202", "1st OAK", "N202", "B02"), ("FX101", "2nd OAK", "N101", None)])
        self.assertEqual(first_sta.planned_datetime_utc, start - timedelta(minutes=1200))
        self.assertEqual(board["arrivals"][4]["planned"], "01:00")
        self.assertEqual(board["arrivals"][5]["planned"], "01:30")
        self.assertFalse(board["arrivals"][4]["eta"])
        self.assertEqual(board["arrivals"][7]["status"], "cancelled")
        eta.eta_datetime_utc = start + timedelta(minutes=255)
        db.session.commit()
        self.assertEqual([row["id"] for row in read()["arrivals"]][:6],
                         [m.id for m in (tied, actual, midnight, first_sta, eta, middle_sta)])

    def test_arrival_variance_uses_authorized_status_and_sort_chronology(self):
        from app.services.neorain_daily_briefing import briefing_context
        from app.services.neorain_reports import report_context
        from tests.test_neorain_sort_refresh import configure_sort_window

        op = seed_sort(self.gateway)
        configure_sort_window(self.gateway, op.sort_date)
        sta = datetime(2026, 4, 13, 6)  # 01:00 local after midnight.
        flight = SortDateMission(
            sort_date_operation_id=op.id, sort_date=op.sort_date, sort_name=op.sort_name,
            gateway_code=self.gateway.code, mission_type="arrival", flight_number="PROJECTED",
            origin="SDF", destination=self.gateway.code, wave="1",
            planned_datetime_utc=sta - timedelta(days=1), eta_datetime_utc=sta + timedelta(minutes=12),
        )
        finished = SortDateMission(
            sort_date_operation_id=op.id, sort_date=op.sort_date, sort_name=op.sort_name,
            gateway_code=self.gateway.code, mission_type="arrival", flight_number="FINISHED",
            origin="SDF", destination=self.gateway.code, wave="2", arrival_status="arrived",
            planned_datetime_utc=sta, actual_block_in_datetime_utc=sta + timedelta(minutes=7),
        )
        db.session.add_all([flight, finished])
        db.session.commit()

        def read(access=frozenset({"rain"})):
            with self.app.test_request_context():
                board = snapshot(self.user, self.gateway, op, sources=(access, None))
            row = next(row for row in board["arrivals"] if row["id"] == flight.id)
            return (row["variance"], board["performance"].get("inbound"),
                    board.get("wave_performance", {}).get("inbound", {}))

        baseline_report = report_context(op)
        baseline_briefing = briefing_context(op)["inbound"]
        self.assertEqual(read()[:2], (None, {"overall": 7.0, "included": 7.0}))
        self.assertIsNone(read()[2]["first_wave"])
        before, after = sta - timedelta(minutes=2, seconds=59), sta + timedelta(minutes=12, seconds=29)
        for status, eta, actual, expected in (
            ("en_route", before, None, -2),
            ("en_route", sta, None, 0),
            ("en_route", after, None, 12),
            ("on_ground", after, None, 12),
            ("on_ground", after, before, -2),
            ("on_ground", None, None, None),
            ("en_route", None, None, None),
            ("scheduled", after, before, None),
            ("cancelled", after, before, None),
            ("arrived", after, None, None),
            ("arrived", after, before, -2),
            ("unloaded", after, sta + timedelta(minutes=3), 3),
        ):
            with self.subTest(status=status, eta=eta, actual=actual):
                flight.arrival_status, flight.eta_datetime_utc = status, eta
                flight.actual_block_in_datetime_utc = actual
                db.session.flush()
                variance, _, waves = read()
                self.assertEqual(variance, expected)
                if expected is None:
                    self.assertIsNone(waves["first_wave"])
                else:
                    self.assertEqual(waves["first_wave"]["aircraft_late"], int(expected > 0))
                    self.assertEqual(waves["first_wave"]["late_minutes"], max(0, expected))

        flight.arrival_status, flight.actual_block_in_datetime_utc = "en_route", None
        flight.eta_datetime_utc = after
        db.session.commit()
        self.assertEqual(read()[:2], (12, {"overall": 9.5, "included": 9.5}))
        self.assertEqual(read()[2]["combined"], dict(aircraft_late=2, late_minutes=19, average="9.5"))
        self.assertEqual(read(set()), (None, None, {}))
        self.assertEqual(flight.planned_datetime_utc, sta - timedelta(days=1))
        self.assertEqual(report_context(op)["inbound_late"], baseline_report["inbound_late"])
        self.assertEqual(briefing_context(op)["inbound"], baseline_briefing)

        flight.planned_datetime_utc = None
        db.session.flush()
        self.assertIsNone(read()[0])
        flight.planned_datetime_local = datetime(2026, 4, 12, 1)
        flight.timezone = "America/Chicago"
        db.session.flush()
        self.assertEqual(read()[0], 12)

        # A correctly dated schedule and ETA on opposite sides of midnight.
        flight.planned_datetime_utc = datetime(2026, 4, 13, 4, 58)
        flight.eta_datetime_utc = datetime(2026, 4, 13, 5, 4)
        db.session.flush()
        self.assertEqual(read()[0], 6)

    def test_arrival_variance_withholds_ambiguous_dst_sta(self):
        from datetime import timezone
        from types import SimpleNamespace
        from zoneinfo import ZoneInfo
        from app.neonodes.neoraiden.services import _arrival_variance_minutes

        zone = ZoneInfo("America/Chicago")
        start = datetime(2026, 11, 1, 2, tzinfo=timezone.utc)
        window = zone, start, datetime(2026, 11, 1, 11, tzinfo=timezone.utc)
        legacy = datetime(2026, 10, 31, 6, 30)  # Would move to the ambiguous 01:30.
        flight = SimpleNamespace(mission_type="arrival", arrival_status="en_route",
            eta_datetime_utc=datetime(2026, 11, 1, 7), planned_datetime_utc=legacy)
        self.assertIsNone(_arrival_variance_minutes(flight, legacy, window))
        flight.planned_datetime_utc = None
        flight.planned_datetime_local = datetime(2026, 11, 1, 1, 30)
        flight.timezone = "America/Chicago"
        self.assertIsNone(_arrival_variance_minutes(flight, datetime(2026, 11, 1, 6, 30), window))

    def test_missing_display_values_are_blank_and_zero_and_no_pull_are_literal(self):
        from flask import render_template_string

        with self.app.test_request_context():
            html = render_template_string(
                "{% from 'neonodes/neoraiden/_mission.html' import val %}"
                "{% for value in values %}[{{ val(value) }}]{% endfor %}",
                values=[None, "", "-", "—", "–", 0, "0.0", "-5", "+5", "NO PURE", "NO MIX"],
            )
        self.assertEqual(html, "[][][][][][0][0.0][-5][+5][NO PURE][NO MIX]")

    def test_fuel_labels_format_both_mission_layouts_without_changing_fuel_color(self):
        from flask import render_template_string

        cases = (
            ("PENDING", "Pending", "gray"), ("ASSIGNED", "Assigned", "orange"),
            ("READY", "Ready", "amber"), ("FUELING", "Fueling", "yellow"),
            ("COMPLETE", "Complete", "green"), ("OFF", "Off", "teal"),
            ("REVIEW", "Review", "red"), ("FOB READY", "FOB Ready", "teal"),
            ("FOB", "FOB", "green"), ("45.6", "45.6", "green"),
            ("45.6 K", "45.6 K", "green"),
        )
        with self.app.test_request_context():
            for raw, display, color in cases:
                with self.subTest(raw=raw):
                    html = render_template_string(
                        "{% from 'neonodes/neoraiden/_mission.html' import mission,departures,mobile_departures %}"
                        "{{ mission(row,departures,false) }}{{ mission(row,mobile_departures,false,mobile=true) }}",
                        row=dict(id=1, flight="FX101", variance=None, neo_fuel=raw, fuel_color=color),
                    )
                    self.assertEqual(html.count(f'data-value-color="{color}">{display}</span>'), 2)

    def test_milestones_use_exact_missions_and_respect_node_access(self):
        from app.models import NeoScorpionFuelAssignment
        from tests.neoraiden_fixtures import record_departure_milestones, record_pull_roster

        op = seed_sort(self.gateway)
        record_departure_milestones(op)
        record_pull_roster(op)
        self.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = datetime.combine(op.sort_date, time(21))
        db.session.add(SortDateMission(sort_date_operation_id=op.id, sort_date=op.sort_date,
            sort_name=op.sort_name, gateway_code=op.gateway_code, mission_type="arrival",
            flight_number="ARR1", origin="SDF", destination=op.gateway_code, wave="2nd Wave",
            arrival_status="arrived", planned_datetime_utc=datetime(2026,4,13,5),
            actual_block_in_datetime_utc=datetime(2026,4,13,5,7)))
        db.session.commit()
        with self.app.test_request_context(), patch("requests.sessions.Session.request",
                side_effect=AssertionError("Raiden requested external traffic")):
            board = snapshot(self.user, self.gateway, op)
            self.assertEqual((board["arrivals"][0]["wave"], board["arrivals"][0]["variance"]), ("2", 7))
            fob, complete = board["departures"]
            self.assertEqual((fob["route_label"], fob["neo_fuel"], fob["variance"], fob["pure"], fob["mix"]),
                             ("1st OAK", "45.0", -5, None, "NO MIX"))
            self.assertEqual((complete["route_label"], complete["neo_fuel"], complete["variance"],
                              complete["pure"], complete["mix"], complete["ramp"], complete["crew"],
                              complete["block"], complete["no_return"]),
                             ("2nd OAK", "45.6", 9, "00:02", "00:57", "00:59", "01:02", "01:09", True))
            self.assertEqual((fob["fuel_color"], complete["fuel_color"]), ("green", "green"))
            restricted = snapshot(self.user, self.gateway, op, sources=(set(), None))
            for row in restricted["departures"]:
                self.assertIsNone(row["neo_fuel"])
                self.assertIsNone(row["pure"])
                self.assertIsNone(row["mix"])
                self.assertIsNone(row["variance"])
                self.assertNotIn("block", row)
            path = f"/neoraiden/state?operation_id={op.id}"
            initial = self.client.get(path)
            self.assertEqual(initial.json["html"].count('data-value-color="green">45.6</span>'), 2)
            mission = db.session.get(SortDateMission, complete["id"])
            assignment = NeoScorpionFuelAssignment.query.filter_by(sort_date_mission_id=mission.id).one()
            assignment.completed_at_utc = None
            assignment.review_status = "pending"
            mission.fuel_status = "waiting"
            db.session.commit()
            pending = snapshot(self.user, self.gateway, op)["departures"][1]
            self.assertEqual((pending["neo_fuel"], pending["fuel_color"]), ("PENDING", "gray"))
            updated = self.client.get(path, headers={"If-None-Match": initial.headers["ETag"]})
            self.assertEqual(updated.status_code, 200)
            self.assertNotIn('>45.6</span>', updated.json["html"])
            self.assertEqual(updated.json["html"].count('data-value-color="gray">Pending</span>'), 2)

    def test_explicit_selection_loads_archive_status_without_a_second_select(self):
        from sqlalchemy import inspect
        from app.neonodes.neoraiden.services import gateway_operations

        op = seed_sort(self.gateway)
        op.archived_at_utc = datetime(2026, 4, 14)
        db.session.commit()
        operation_id = op.id
        db.session.expunge(op)
        selected = gateway_operations(self.gateway).filter_by(id=operation_id).one()
        self.assertNotIn("archived_at_utc", inspect(selected).unloaded)
        self.assertEqual(selected.archived_at_utc, datetime(2026, 4, 14))
        response = self.client.get(f"/neoraiden/state?operation_id={operation_id}",
                                   headers={"X-Raiden-Automatic": "1"})
        self.assertEqual(response.status_code, 204)

    def test_live_inbound_performance_uses_native_exclusions_without_changing_rain(self):
        from app.neonodes.neorain.services import _inbound_late_summary

        op = seed_sort(self.gateway)
        planned = datetime(2026, 4, 13, 3)
        arrivals = []
        for number, minutes, status, wave, included in (
            ("A1", 10, "arrived", "1", None),
            ("A2", 30, "unloaded", "2", False),
            ("A3", -20, "arrived", "2", None),
            ("A4", 45, "cancelled", "1", True),
            ("A5", 50, "on_ground", "1", None),
        ):
            arrivals.append(SortDateMission(sort_date_operation_id=op.id, gateway_code=op.gateway_code,
                sort_date=op.sort_date, sort_name=op.sort_name, mission_type="arrival", flight_number=number,
                origin="SDF", destination="RFD", planned_datetime_utc=planned,
                actual_block_in_datetime_utc=planned + timedelta(minutes=minutes), arrival_status=status,
                wave=wave, late_metrics_included_override=included))
        db.session.add_all(arrivals)
        db.session.commit()
        with self.app.test_request_context():
            def performance():
                return snapshot(self.user, self.gateway, op)["performance"]
            self.assertEqual(performance(), {"inbound": {"overall": 30.0, "included": 30.0},
                                            "outbound": {"overall": None, "included": None}})
            self.assertEqual(_inbound_late_summary(arrivals)["total"]["average"], "10")
            arrivals[0].late_metrics_included_override = False
            db.session.commit()
            self.assertEqual(performance()["inbound"]["included"], 50.0)
            self.assertEqual(_inbound_late_summary(arrivals)["total"]["average"], "0")
            self.assertEqual(snapshot(self.user, self.gateway, op, sources=(set(), None))["performance"], {})

    def test_deice_revision_follows_physical_tail_and_requires_ownership_evidence(self):
        from app.models import NeoSubZeroDepartureDeiceEvent

        op = seed_sort(self.gateway)
        a = SortDateMission.query.filter_by(flight_number="FX101").one()
        b = SortDateMission.query.filter_by(flight_number="FX202").one()
        start = datetime(2026, 4, 13, 4, 55)
        event = NeoSubZeroDepartureDeiceEvent(sort_date_operation_id=op.id, sort_date_mission_id=a.id,
            tail_number="N101", status="finished", treatment_plan="one_type_i",
            pass1_started_at_utc=start, pass1_ended_at_utc=start + timedelta(minutes=15))
        db.session.add(event)
        db.session.commit()
        with self.app.test_request_context():
            def statuses(sources=None):
                return {r["flight"]: (r["deice_state"], r["deice"])
                        for r in snapshot(self.user, self.gateway, op, sources=sources)["departures"]}
            completed = ("fin", "Completed 00:10")
            self.assertEqual(statuses()["FX101"], completed)
            self.assertEqual(statuses()["FX202"], (None, None))
            event.status, event.clear_outcome, event.radio_state = "cleared", "sprayed", "pending"
            event.cleared_at_utc = start + timedelta(minutes=20)
            a.assigned_tail_number, b.assigned_tail_number = "N202", "N101"
            db.session.commit()
            self.assertEqual(statuses()["FX202"], completed)
            self.assertEqual(statuses()["FX101"], (None, None))
            first = self.client.get(f"/neoraiden/state?operation_id={op.id}")
            self.assertIn("Completed 00:10", first.json["html"])
            self.assertNotIn("RADIO", first.json["html"])
            event.radio_state = "complete"
            event.radio_completed_at_utc = datetime(2026, 4, 13, 5, 30)
            event.radio_completed_by_user_id = self.user.id
            b.departure_status = "departed"
            db.session.commit()
            reply = self.client.get(f"/neoraiden/state?operation_id={op.id}", headers={"If-None-Match": first.headers["ETag"]})
            self.assertEqual(reply.status_code, 200)
            self.assertIn("Completed 00:10", reply.json["html"])
            self.assertNotIn("RADIO", reply.json["html"])
            event.pass1_ended_at_utc += timedelta(minutes=2)
            db.session.commit()
            corrected = self.client.get(f"/neoraiden/state?operation_id={op.id}", headers={"If-None-Match": reply.headers["ETag"]})
            self.assertIn("Completed 00:12", corrected.json["html"])
            self.assertNotIn("Completed 00:10", corrected.json["html"])
            a.assigned_tail_number = "N101"
            db.session.commit()
            self.assertEqual(statuses()["FX202"], ("review", "TREATMENT OWNERSHIP REVIEW"))
            self.assertEqual(statuses()["FX101"], ("fin", "Completed 00:12"))
            self.assertEqual(statuses((set(), None))["FX101"], (None, None))
            event.reset_at_utc = datetime(2026, 4, 13, 5, 32)
            db.session.commit()
            self.assertEqual(set(statuses().values()), {(None, None)})

    def test_pretreat_and_two_pass_display_follow_recorded_milestones(self):
        from app.models import NeoSubZeroDepartureDeiceEvent, NeoSubZeroPretreatState

        op = seed_sort(self.gateway)
        mission = SortDateMission.query.filter_by(flight_number="FX101").one()
        other = SortDateMission.query.filter_by(flight_number="FX202").one()
        start = datetime(2026, 4, 13, 4, 30)
        state = NeoSubZeroPretreatState(sort_date_operation_id=op.id, tail_number="N101", original_mission_id=mission.id,
            pass1_started_at_utc=start, pass1_ended_at_utc=start + timedelta(minutes=10),
            pass2_started_at_utc=start + timedelta(minutes=15))
        db.session.add(state)
        db.session.commit()
        path = f"/neoraiden/state?operation_id={op.id}"
        incomplete = self.client.get(path)
        self.assertNotIn("Pretreated", incomplete.json["html"])
        state.pass2_ended_at_utc = start + timedelta(minutes=19)
        db.session.commit()
        pretreat = self.client.get(path, headers={"If-None-Match": incomplete.headers["ETag"]})
        self.assertEqual(pretreat.status_code, 200)
        self.assertIn("Pretreated 23:45", pretreat.json["html"])

        def label(flight="FX101"):
            with self.app.test_request_context():
                return next(r["deice"] for r in snapshot(self.user, self.gateway, op)["departures"] if r["flight"] == flight)

        routine = NeoSubZeroDepartureDeiceEvent(sort_date_operation_id=op.id, sort_date_mission_id=mission.id,
            tail_number="N101", status="cleared", clear_outcome="pretreated", cleared_at_utc=start + timedelta(minutes=20))
        db.session.add(routine)
        mission.departure_status = "departed"
        db.session.commit()
        self.assertEqual(label(), "Pretreated 23:45")
        other.assigned_tail_number = "N101"
        db.session.commit()
        self.assertEqual(label("FX202"), "TREATMENT OWNERSHIP REVIEW")
        mission.assigned_tail_number = "N202"
        db.session.commit()
        self.assertIsNone(label())
        self.assertEqual(label("FX202"), "Pretreated 23:45")

        event = NeoSubZeroDepartureDeiceEvent(sort_date_operation_id=op.id, sort_date_mission_id=other.id,
            tail_number="N101", status="deice_planned", treatment_plan="type_i_type_iv")
        db.session.add(event)
        db.session.commit()
        self.assertEqual(label("FX202"), "PLANNED")
        event.status, event.configured_at_utc = "configured", start + timedelta(minutes=22)
        db.session.commit()
        self.assertEqual(label("FX202"), "CONFIGURED")
        event.pass1_started_at_utc = start + timedelta(minutes=24)
        db.session.commit()
        self.assertEqual(label("FX202"), "DEICING")
        event.status, event.pass1_ended_at_utc = "finished", start + timedelta(minutes=28)
        db.session.commit()
        self.assertEqual(label("FX202"), "FIN")
        event.pass2_started_at_utc = start + timedelta(minutes=32)
        event.pass2_ended_at_utc = start + timedelta(minutes=44)
        db.session.commit()
        self.assertEqual(label("FX202"), "Completed 00:14")
        for outcome, description in (("negative", "NEGATIVE DEICE"), ("not_sprayed", "NOT SPRAYED")):
            event.status, event.clear_outcome = "cleared", outcome
            event.cleared_at_utc = start + timedelta(minutes=45)
            db.session.commit()
            self.assertEqual(label("FX202"), description)
        event.reset_at_utc = start + timedelta(minutes=50)
        db.session.commit()
        self.assertEqual(label("FX202"), "Pretreated 23:45")
        state.pass2_ended_at_utc = None
        db.session.commit()
        self.assertEqual(label("FX202"), "GONE · CLEARED")

    def test_recorded_uld_projection_and_wave_lifecycle(self):
        from datetime import timedelta
        from app.models import NeoSektorOperationalSetting, NeoSektorBallmatWaveCount, NeoSektorWaveState
        from app.services.neosektor_live_counts import native_uld_summary
        from tests.neoraiden_fixtures import record_counts

        op = seed_sort(self.gateway)
        db.session.add(NeoSektorOperationalSetting(gateway_id=self.gateway.id, gateway_code=self.gateway.code,
                       integration_mode="google_primary", first_wave_unload_modifier=10, all_up_to_down_minutes=15))
        db.session.commit()
        def read():
            return native_uld_summary(self.gateway, op.sort_date, op.sort_name)
        with patch("app.services.neosektor_live_counts._neosektor_integration_mode",side_effect=AssertionError("integration routing")), \
             patch("requests.sessions.Session.request",side_effect=AssertionError("external HTTP")):
            self.assertIsNone(read())
            state = record_counts(self.gateway,op)
            first, second = read()["waves"]
            self.assertEqual((first["east"],first["west"],first["arrive"],first["unload"]),(6,0,15,26))
            self.assertEqual((second["east"],second["west"],second["arrive"],second["unload"]),(4,7,32,38))
            self.assertEqual((read()["east_open"],read()["west_open"],read()["unloaded"],read()["percent"]),(2,3,17,17))
            self.assertIsNone(native_uld_summary(self.gateway,op.sort_date,"day"))
            other=Gateway(code="SEA",name="Seattle")
            db.session.add(other)
            db.session.commit()
            self.assertIsNone(native_uld_summary(other,op.sort_date,op.sort_name))
            with self.app.test_request_context():
                self.assertEqual(snapshot(self.user,self.gateway,op)["uld"],read())
            sector = NeoNode.query.filter_by(code="sektor").one()
            membership = GatewayMembership.query.filter_by(user_id=self.user.id,gateway_id=self.gateway.id).one()
            GatewayNodeRole.query.filter_by(node_id=sector.id,gateway_membership_id=membership.id).update({"is_active":False})
            db.session.commit()
            with self.app.test_request_context():
                self.assertIsNone(snapshot(self.user,self.gateway,op)["uld"])
            waves = NeoSektorWaveState.query.filter_by(sort_state_id=state.id)
            counts = NeoSektorBallmatWaveCount.query.filter_by(sort_state_id=state.id)
            waves.update({"planned_count":0})
            counts.update({"count":0})
            db.session.commit()
            self.assertEqual([(w["arrive"],w["unload"]) for w in read()["waves"]],
                             [("ALL IN","ALL UP"),("ALL IN","PENDING")])
            self.assertIsNone(waves.first().all_up_started_at)
            waves.update({"all_up_started_at":datetime.utcnow()-timedelta(hours=2)})
            db.session.commit()
            self.assertEqual([w["unload"] for w in read()["waves"]],["DOWN","DOWN"])
            counts.filter_by(side="WEST",wave_name="2ND WAVE").delete()
            db.session.commit()
            incomplete = read()
            self.assertIsNone(incomplete["waves"][1]["west"])
            self.assertIsNone(incomplete["waves"][1]["unload"])
            self.assertIsNone(incomplete["unloaded"])
            self.assertEqual(incomplete["waves"][0]["unload"],"DOWN")

    def test_command_center_reads_under_explicit_grants_without_dml(self):
        from app.models import AccountAuthorizationState, AccountSecurityEvent, PortalAppAccess
        from tests.neoraiden_fixtures import record_counts
        op = seed_sort(self.gateway)
        record_counts(self.gateway, op)
        approval = PortalAppAccess.query.filter_by(user_id=self.user.id, app_code="neogateway").first()
        if not approval:
            approval = PortalAppAccess(user_id=self.user.id, app_code="neogateway")
            db.session.add(approval)
        approval.status, approval.is_active, approval.role = "approved", True, "watcher"
        receipt = AccountSecurityEvent(actor_user_id=self.user.id,subject_user_id=self.user.id,
                                       action="fixture",details={})
        db.session.add(receipt)
        db.session.flush()
        db.session.add(AccountAuthorizationState(id=1, active=True, security_event_id=receipt.id,
                                                inventory_fingerprint="fixture"))
        db.session.commit()
        statements = []
        def trace(_conn, _cur, statement, _params, _ctx, _many):
            statements.append(statement.split()[0].upper())
        engine = db.engine
        event.listen(engine, "before_cursor_execute", trace)
        try:
            with patch("requests.sessions.Session.request", side_effect=AssertionError("external fetch")):
                for path in ("/neoraiden", "/neoraiden/state"):
                    result = self.client.get(path + "?operation_id=" + str(op.id))
                    self.assertEqual(result.status_code,200)
            self.assertFalse({"INSERT", "UPDATE", "DELETE", "MERGE"} & set(statements))
        finally:
            event.remove(engine,"before_cursor_execute",trace)
        approval.is_active = False
        db.session.commit()
        self.assertEqual(self.client.get("/neoraiden").status_code,403)
        self.assertEqual(self.client.get("/neoraiden/state").status_code,403)

    def test_native_projection_identity_and_revalidation(self):
        from tests.neoraiden_fixtures import record_pull_roster

        op = seed_sort(self.gateway)
        lineup = record_pull_roster(op)
        self.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = datetime.combine(op.sort_date, time(21))
        self.user.role = "watcher"
        db.session.commit()
        path = "/neoraiden/state?gateway=RFD&operation_id=" + str(op.id)
        with patch("requests.sessions.Session.request", side_effect=AssertionError("external traffic")):
            result = self.client.get(path)
            self.assertEqual(result.status_code, 200)
            with self.app.test_request_context():
                rows = snapshot(self.user, self.gateway, op)["departures"]
            self.assertEqual([(r["tail"], r["route_label"], r["ordinal"], r["parking"]) for r in rows],
                             [("N202", "OAK", None, "B02"), ("N101", "OAK", None, None)])
            self.assertEqual([(r["pure"], r["mix"]) for r in rows], [(None, "NO MIX"), ("00:02", "00:57")])
            self.assertIsNone(rows[0]["deice"])
            self.assertEqual(self.client.get(path, headers={"If-None-Match":result.headers["ETag"]}).status_code, 304)
            self.assertIn("no-store",result.headers["Cache-Control"])
            complete = SortDateMission.query.filter_by(flight_number="FX101").one()
            last = NeoErmacDoorPull.query.filter_by(sort_date_mission_id=complete.id, door="D4").one()
            last.actual_pure_pull_time_local = None
            db.session.commit()
            corrected = self.client.get(path, headers={"If-None-Match":result.headers["ETag"]})
            self.assertEqual(corrected.status_code, 200)
            self.assertNotIn("00:02", corrected.json["html"])
            self.assertIn("00:57", corrected.json["html"])
            lineup.west_destination_2 = "OAK"
            lineup.flight_assignments = dict(lineup.flight_assignments, west_destination_2=complete.master_flight_schedule_id)
            db.session.delete(last)
            db.session.commit()
            # Without the second assigned door both types remain blank, even though D1 reported.
            with self.app.test_request_context():
                self.assertEqual([(r["pure"], r["mix"]) for r in snapshot(self.user, self.gateway, op)["departures"]],
                                 [(None, "NO MIX"), (None, None)])
            lineup.west_destination_1 = lineup.west_destination_2 = None
            lineup.flight_assignments = {key: value for key, value in lineup.flight_assignments.items()
                                         if key.startswith("east_")}
            db.session.commit()
            assigned = self.client.get(path, headers={"If-None-Match":corrected.headers["ETag"]})
            self.assertEqual(assigned.status_code, 200)
            self.assertIn("23:47", assigned.json["html"])
            self.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] += timedelta(days=1)
            historical = self.client.get(path, headers={"If-None-Match":assigned.headers["ETag"]})
            self.assertEqual(historical.status_code, 200)
            self.assertNotIn("23:47", historical.json["html"])
            self.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] -= timedelta(days=1)
            node = NeoNode.query.filter_by(code="ermac").one()
            GatewayNodeRole.query.filter_by(node_id=node.id).update({"is_active":False})
            db.session.commit()
            changed = self.client.get(path, headers={"If-None-Match":assigned.headers["ETag"]})
            self.assertEqual(changed.status_code,200)
            self.assertNotIn("23:47",changed.json["html"])
            self.assertEqual(self.client.post(path).status_code,405)
            GatewayMembership.query.filter_by(user_id=self.user.id).update({"is_active": False})
            db.session.commit()
            self.assertEqual(self.client.get(path,headers={"If-None-Match":changed.headers["ETag"]}).status_code,403)

    def test_gateway_and_sort_isolation_and_read_only_current(self):
        foreign = Gateway(code="DEN",name="Denver")
        db.session.add(foreign)
        db.session.commit()
        op = seed_sort(foreign)
        self.assertEqual(self.client.get("/neoraiden?gateway=DEN").status_code,403)
        access = GatewayMembership(user_id=self.user.id, gateway_id=foreign.id, status="approved", is_active=True)
        db.session.add(access)
        db.session.commit()
        result = self.client.get("/neoraiden?gateway=DEN&operation_id="+str(op.id))
        self.assertEqual(result.status_code, 200)
        self.assertIn(f'href="/neoraiden?gateway=DEN&amp;operation_id={op.id}" aria-label="NeoRaiden home"', result.text)
        self.assertEqual(self.client.get("/neoraiden/state?gateway=RFD&operation_id="+str(op.id)).status_code,404)
        self.assertEqual(self.client.get("/neoraiden?operation_id=garbage").status_code,404)
        response = self.client.get("/neoraiden")
        self.assertEqual(response.status_code,200)
        self.assertNotIn(b"FX101",response.data)
        self.assertIn(b"No active Sort",response.data)
        self.assertEqual(SortDateOperation.query.count(),1)

    def test_returning_browsers_fetch_current_shell_assets(self):
        for page, files in (("/rfd", ["css/gateway_launcher.css"]),
                            ("/neoraiden", ["css/operational_node_shell.css", "js/operational_shell.js"])):
            html = self.client.get(page).text
            for asset in files:
                with self.subTest(asset=asset):
                    content = (Path(self.app.static_folder) / asset).read_bytes().replace(b"\r\n", b"\n")
                    url = re.search(r'/static/' + re.escape(asset) + r'\?[^"]+', html).group(0)
                    self.assertIn("rev=" + sha256(content).hexdigest()[:12], url)

    def test_last_screen_on_staffing_and_standalone_routing(self):
        from app.models import NeoSektorOperationalSetting
        db.session.add(NeoSektorOperationalSetting(gateway_id=self.gateway.id, gateway_code=self.gateway.code,
                                                   integration_mode="neo_only"))
        db.session.commit()
        for path in ("/neostaffing/attendance", "/neosektor/driver-routing"):
            with self.subTest(path=path):
                page = self.client.get(path)
                self.assertEqual(page.status_code, 200)
                token = re.search(rb'data-visit="([^"]+)"', page.data).group(1).decode()
                self.assertEqual(self.client.post("/account/operational-screen", data={"visit":token}).status_code, 204)
                self.assertEqual(self.client.get("/login").location, path)
        self.assertNotIn(b'data-visit="', self.client.get("/neosektor/driver-routing?tv=1").data)

    def test_store_restore_and_fallback_on_revocation(self):
        op = seed_sort(self.gateway)
        screen = "/neoraiden?gateway=RFD&operation_id=" + str(op.id)
        page = self.client.get(screen)
        token = re.search(rb'data-visit="([^"]+)"',page.data).group(1).decode()
        endpoint = "/account/operational-screen"
        self.assertEqual(self.client.post(endpoint,data={"visit":"https://elsewhere.test/"}).status_code,403)
        self.assertEqual(self.client.post(endpoint,data={"visit":token}).status_code,204)
        self.assertEqual(self.client.get("/login").location, screen)
        self.client.post("/logout")
        response = self.client.post("/login",data={"username":"gateway-shell","password":"TestPassword123!"})
        self.assertEqual(response.location,screen)
        saved = db.session.get(AccountOperationalScreen,self.user.id)
        self.assertEqual(saved.operation_id,op.id)
        newer = re.search(rb'data-visit="([^"]+)"',self.client.get("/neoermac/door-view").data).group(1).decode()
        self.assertEqual(self.client.post(endpoint,data={"visit":newer}).status_code,204)
        self.assertEqual(self.client.post(endpoint,data={"visit":token}).status_code,204)
        self.assertEqual(self.client.get("/login").location,"/neoermac/door-view")
        node=NeoNode.query.filter_by(code="ermac").one()
        GatewayNodeRole.query.filter_by(node_id=node.id).update({"is_active":False})
        db.session.commit()
        self.assertNotEqual(self.client.get("/login").location,"/neoermac/door-view")
