from datetime import date, datetime, time
import unittest
from unittest.mock import patch

from app import create_app
from app.extensions import db
from app.models import (
    Gateway,
    MasterFlightSchedule,
    MotherBrainAlert,
    SortDateGoogleMissionLink,
    SortDateMission,
    SortDateOperation,
    SortDateParkingAssignment,
    SortDateTailState,
)
from app.services.google_motherbrain_live_missions import (
    GOOGLE_MOTHERBRAIN_MISSION_SOURCE,
    apply_google_motherbrain_live_mission_batch,
    apply_google_motherbrain_live_rows,
)
from app.services.google_motherbrain_live_polling import (
    google_motherbrain_live_polling_enabled,
)


class GoogleMotherBrainLiveMissionTest(unittest.TestCase):
    def setUp(self):
        TestConfig = type(
            "TestConfig",
            (),
            {
                "SECRET_KEY": "google-live-mission-test",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
                "DEFAULT_GATEWAY_TIMEZONE": "America/Chicago",
            },
        )
        self.app = create_app(TestConfig)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.gateway = Gateway(code="RFD", name="NeoGateway", is_active=True)
        db.session.add(self.gateway)
        db.session.flush()
        self.operation = self._operation(date(2026, 8, 7), "night")
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_existing_current_sort_mission_is_reused_and_source_preserved(self):
        master = self._master("arrival", "UPS0947", "SDF", "RFD", time(23, 0))
        mission = self._mission(
            "arrival",
            "UPS0947",
            tail="N457UP",
            origin="SDF",
            source="master",
            master_id=master.id,
        )
        db.session.commit()

        result = self._apply_arrivals(self._inbound(4, "5X947", "N457UP", status="DEP"))
        db.session.commit()

        self.assertEqual(result["results"][0]["mission_id"], mission.id)
        self.assertFalse(result["results"][0]["created"])
        self.assertEqual(db.session.get(SortDateMission, mission.id).mission_source, "master")
        self.assertEqual(SortDateMission.query.count(), 1)

    def test_missing_mission_is_current_sort_only_with_google_provenance(self):
        result = self._apply_arrivals(self._inbound(4, "947", "N457UP"))
        db.session.commit()

        mission = db.session.get(SortDateMission, result["results"][0]["mission_id"])
        self.assertEqual(mission.sort_date_operation_id, self.operation.id)
        self.assertEqual(mission.flight_number, "UPS0947")
        self.assertEqual(mission.mission_source, GOOGLE_MOTHERBRAIN_MISSION_SOURCE)
        self.assertIsNone(mission.master_flight_schedule_id)

    def test_correlated_row_updates_same_mission_when_flight_changes(self):
        first = self._apply_arrivals(self._inbound(22, "947", "N457UP"))
        db.session.commit()
        mission_id = first["results"][0]["mission_id"]

        second = self._apply_arrivals(self._inbound(22, "955", "N457UP"))
        db.session.commit()

        self.assertEqual(second["results"][0]["mission_id"], mission_id)
        self.assertEqual(db.session.get(SortDateMission, mission_id).flight_number, "UPS0955")
        self.assertEqual(SortDateMission.query.count(), 1)

    def test_duplicate_google_flight_rows_first_row_wins(self):
        result = self._apply_arrivals(
            self._inbound(4, "947", "N457UP", origin="SDF"),
            self._inbound(5, "5X0947", "N458UP", origin="ONT"),
        )
        db.session.commit()

        self.assertEqual([row["status"] for row in result["results"]], ["applied", "skipped"])
        mission = SortDateMission.query.one()
        self.assertEqual((mission.assigned_tail_number, mission.origin), ("N457UP", "SDF"))

    def test_correlation_is_operation_and_direction_scoped(self):
        arrival = self._apply_arrivals(self._inbound(4, "947", "N457UP"))
        departure = self._apply_departures(self._outbound(4, "947", "N458UP"))
        other_operation = self._operation(date(2026, 8, 8), "night")
        other = apply_google_motherbrain_live_mission_batch(
            other_operation,
            "arrival",
            [self._inbound(4, "947", "N459UP")],
        )
        db.session.commit()

        mission_ids = {
            arrival["results"][0]["mission_id"],
            departure["results"][0]["mission_id"],
            other["results"][0]["mission_id"],
        }
        self.assertEqual(len(mission_ids), 3)
        self.assertEqual(SortDateGoogleMissionLink.query.count(), 3)

    def test_new_inbound_blank_t_is_pending_then_later_populates(self):
        first = self._apply_arrivals(self._inbound(4, "947", "N457UP", planned=""))
        db.session.commit()
        mission_id = first["results"][0]["mission_id"]
        mission = db.session.get(SortDateMission, mission_id)
        self.assertIsNone(mission.planned_datetime_local)
        self.assertIsNone(mission.planned_datetime_utc)

        self._apply_arrivals(self._inbound(4, "947", "N457UP", planned="23:15"))
        db.session.commit()
        mission = db.session.get(SortDateMission, mission_id)
        self.assertEqual(mission.planned_datetime_local, datetime(2026, 8, 7, 23, 15))
        self.assertEqual(mission.planned_source, GOOGLE_MOTHERBRAIN_MISSION_SOURCE)

    def test_new_outbound_blank_t_is_pending_then_later_populates(self):
        first = self._apply_departures(self._outbound(4, "755", "N457UP", planned=""))
        db.session.commit()
        mission_id = first["results"][0]["mission_id"]
        self.assertIsNone(db.session.get(SortDateMission, mission_id).planned_datetime_utc)

        self._apply_departures(self._outbound(4, "755", "N457UP", planned="01:20"))
        db.session.commit()
        mission = db.session.get(SortDateMission, mission_id)
        self.assertEqual(mission.planned_datetime_local, datetime(2026, 8, 8, 1, 20))

    def test_formatted_google_dates_apply_inbound_planned_eta_and_actual(self):
        self.operation.sort_date = date(2026, 8, 10)
        db.session.commit()

        result = self._apply_arrivals(
            self._inbound(
                4,
                "947",
                "N457UP",
                planned="8/10 22:20",
                operational="8/11 0:04",
                status="DEP",
            ),
            self._inbound(
                5,
                "955",
                "N458UP",
                planned="8/10 23:30",
                operational="8/11 3:38",
                status="ARR",
            ),
        )
        db.session.commit()

        self.assertEqual(result["applied_count"], 2)
        self.assertEqual(result["skipped_count"], 0)
        en_route = self._mission_by_flight("UPS0947")
        arrived = self._mission_by_flight("UPS0955")
        self.assertEqual(
            en_route.planned_datetime_local,
            datetime(2026, 8, 10, 22, 20),
        )
        self.assertEqual(en_route.eta_datetime_utc, datetime(2026, 8, 11, 5, 4))
        self.assertEqual(
            arrived.actual_block_in_datetime_utc,
            datetime(2026, 8, 11, 8, 38),
        )

    def test_formatted_google_dates_apply_outbound_planned_and_block_out(self):
        self.operation.sort_date = date(2026, 8, 10)
        db.session.commit()

        result = self._apply_departures(
            self._outbound(
                4,
                "755",
                "N457UP",
                planned="08/10 22:20",
                operational="08/11 00:04",
            ),
            now=datetime(2026, 8, 11, 5, 4),
        )
        db.session.commit()

        self.assertEqual(result["applied_count"], 1)
        self.assertEqual(result["skipped_count"], 0)
        mission = self._mission_by_flight("UPS0755")
        self.assertEqual(
            mission.planned_datetime_local,
            datetime(2026, 8, 10, 22, 20),
        )
        self.assertEqual(
            mission.actual_block_out_datetime_utc,
            datetime(2026, 8, 11, 5, 4),
        )

    def test_dash_live_datetime_is_treated_as_blank(self):
        result = self._apply_departures(
            self._outbound(
                4,
                "755",
                "N457UP",
                destination="HOT",
                planned="-",
                operational="-",
            )
        )
        db.session.commit()

        self.assertEqual(result["applied_count"], 1)
        self.assertEqual(result["skipped_count"], 0)
        mission = self._mission_by_flight("UPS0755")
        self.assertEqual(mission.destination, "HOT")
        self.assertIsNone(mission.planned_datetime_local)
        self.assertIsNone(mission.actual_block_out_datetime_utc)

    def test_formatted_google_date_resolves_december_to_january_year(self):
        self.operation.sort_date = date(2026, 12, 31)
        db.session.commit()

        result = self._apply_departures(
            self._outbound(
                4,
                "755",
                "N457UP",
                planned="12/31 22:20",
                operational="1/1 0:04",
            ),
            now=datetime(2027, 1, 1, 6, 4),
        )
        db.session.commit()

        self.assertEqual(result["applied_count"], 1)
        mission = self._mission_by_flight("UPS0755")
        self.assertEqual(
            mission.planned_datetime_local,
            datetime(2026, 12, 31, 22, 20),
        )
        self.assertEqual(
            mission.actual_block_out_datetime_utc,
            datetime(2027, 1, 1, 6, 4),
        )

    def test_hhmm_live_datetime_behavior_is_unchanged(self):
        result = self._apply_arrivals(
            self._inbound(
                4,
                "947",
                "N457UP",
                planned="23:15",
                operational="00:04",
                status="DEP",
            )
        )
        db.session.commit()

        self.assertEqual(result["applied_count"], 1)
        mission = self._mission_by_flight("UPS0947")
        self.assertEqual(
            mission.planned_datetime_local,
            datetime(2026, 8, 7, 23, 15),
        )
        self.assertEqual(mission.eta_datetime_utc, datetime(2026, 8, 8, 5, 4))

    def test_formatted_google_live_batch_creates_missions_instead_of_skipping(self):
        self.operation.sort_date = date(2026, 8, 10)
        db.session.commit()

        result = apply_google_motherbrain_live_rows(
            self.operation,
            inbound_rows=[
                self._inbound(
                    4,
                    "947",
                    "N457UP",
                    planned="8/10 22:20",
                    operational="8/11 0:04",
                    status="DEP",
                )
            ],
            outbound_rows=[
                self._outbound(
                    4,
                    "755",
                    "N458UP",
                    planned="8/10 23:20",
                    operational="8/11 3:38",
                )
            ],
        )
        db.session.commit()

        self.assertEqual(result["applied_count"], 2)
        self.assertEqual(result["skipped_count"], 0)
        self.assertEqual(SortDateMission.query.count(), 2)

    def test_inbound_status_and_timing_mappings(self):
        cases = (
            (4, "901", "", "", "scheduled"),
            (5, "902", "DEP", "23:20", "en_route"),
            (6, "903", "ON", "23:30", "on_ground"),
            (7, "904", "ARR", "23:40", "arrived"),
            (8, "905", "ARRIVED", "23:50", "arrived"),
        )
        rows = [
            self._inbound(row, flight, f"N45{row}UP", status=status, operational=value)
            for row, flight, status, value, _expected in cases
        ]
        self._apply_arrivals(*rows)
        db.session.commit()

        for _row, flight, status, _value, expected in cases:
            mission = self._mission_by_flight(f"UPS0{flight}")
            self.assertEqual(mission.arrival_status, expected)
            if status in {"DEP", "ON"}:
                self.assertEqual(mission.eta_source, GOOGLE_MOTHERBRAIN_MISSION_SOURCE)
            if status in {"ARR", "ARRIVED"}:
                self.assertEqual(
                    mission.actual_block_in_source,
                    GOOGLE_MOTHERBRAIN_MISSION_SOURCE,
                )

    def test_cancelled_ignores_u_and_reverses_to_live_states(self):
        result = self._apply_arrivals(
            self._inbound(4, "947", "N457UP", status="CNL", operational="23:40")
        )
        db.session.commit()
        mission_id = result["results"][0]["mission_id"]
        mission = db.session.get(SortDateMission, mission_id)
        self.assertEqual(mission.arrival_status, "cancelled")
        self.assertIsNone(mission.eta_datetime_utc)
        self.assertIsNone(mission.actual_block_in_datetime_utc)

        for status, expected in (("DEP", "en_route"), ("CNL", "cancelled"), ("ON", "on_ground"), ("CNL", "cancelled"), ("ARR", "arrived")):
            self._apply_arrivals(
                self._inbound(4, "947", "N457UP", status=status, operational="23:45")
            )
            db.session.commit()
            self.assertEqual(db.session.get(SortDateMission, mission_id).arrival_status, expected)

    def test_inbound_progress_never_downgrades(self):
        for index, (current, stale_status) in enumerate(
            (("en_route", ""), ("on_ground", "DEP"), ("arrived", "ON")),
            start=1,
        ):
            mission = self._mission(
                "arrival",
                f"UPS08{index}",
                tail=f"N48{index}UP",
                origin="SDF",
                arrival_status=current,
            )
            db.session.commit()
            self._apply_arrivals(
                self._inbound(20 + index, f"8{index}", f"N48{index}UP", status=stale_status)
            )
            db.session.commit()
            self.assertEqual(db.session.get(SortDateMission, mission.id).arrival_status, current)

    def test_unloaded_never_downgrades_but_arrival_corrects_actual(self):
        mission = self._mission(
            "arrival",
            "UPS0947",
            tail="N457UP",
            origin="SDF",
            arrival_status="unloaded",
        )
        mission.actual_block_in_datetime_utc = datetime(2026, 8, 8, 4, 30)
        mission.actual_block_in_source = "manual"
        db.session.commit()

        self._apply_arrivals(
            self._inbound(4, "947", "N457UP", status="ARR", operational="23:45")
        )
        db.session.commit()

        mission = db.session.get(SortDateMission, mission.id)
        self.assertEqual(mission.arrival_status, "unloaded")
        self.assertEqual(mission.actual_block_in_datetime_utc, datetime(2026, 8, 8, 4, 45))
        self.assertEqual(mission.actual_block_in_source, GOOGLE_MOTHERBRAIN_MISSION_SOURCE)

    def test_google_eta_populates_until_api_owns_eta(self):
        first = self._apply_arrivals(
            self._inbound(4, "947", "N457UP", status="DEP", operational="23:30")
        )
        db.session.commit()
        mission_id = first["results"][0]["mission_id"]
        mission = db.session.get(SortDateMission, mission_id)
        self.assertEqual(mission.eta_datetime_utc, datetime(2026, 8, 8, 4, 30))

        mission.eta_datetime_utc = datetime(2026, 8, 8, 4, 42)
        mission.eta_source = "api"
        db.session.commit()
        self._apply_arrivals(
            self._inbound(4, "947", "N457UP", status="ON", operational="23:47")
        )
        db.session.commit()

        mission = db.session.get(SortDateMission, mission_id)
        link = SortDateGoogleMissionLink.query.one()
        self.assertEqual(mission.eta_datetime_utc, datetime(2026, 8, 8, 4, 42))
        self.assertEqual(link.google_eta_datetime_utc, datetime(2026, 8, 8, 4, 47))

        self._apply_arrivals(
            self._inbound(4, "947", "N457UP", status="ARR", operational="23:45")
        )
        db.session.commit()
        self.assertEqual(
            db.session.get(SortDateMission, mission_id).actual_block_in_datetime_utc,
            datetime(2026, 8, 8, 4, 45),
        )

    def test_normal_outbound_creates_and_updates_current_mission(self):
        result = self._apply_departures(
            self._outbound(4, "755", "N457UP", destination="SDF", planned="01:10")
        )
        db.session.commit()
        mission_id = result["results"][0]["mission_id"]
        mission = db.session.get(SortDateMission, mission_id)
        self.assertEqual((mission.origin, mission.destination), ("RFD", "SDF"))

        self._apply_departures(
            self._outbound(4, "755", "N457UP", destination="ONT", planned="01:20")
        )
        db.session.commit()
        mission = db.session.get(SortDateMission, mission_id)
        self.assertEqual((mission.destination, SortDateMission.query.count()), ("ONT", 1))

    def test_hot_to_destination_and_back_uses_same_pending_mission(self):
        first = self._apply_departures(
            self._outbound(4, "755", "N457UP", destination="HOT", planned="")
        )
        db.session.commit()
        mission_id = first["results"][0]["mission_id"]
        self.assertEqual(self._tail_state("N457UP").operational_status, "hot")
        self.assertIsNone(db.session.get(SortDateMission, mission_id).planned_datetime_local)

        second = self._apply_departures(
            self._outbound(4, "755", "N457UP", destination="SDF", planned="")
        )
        db.session.commit()
        self.assertEqual(second["results"][0]["mission_id"], mission_id)
        self.assertEqual(self._tail_state("N457UP").operational_status, "normal")
        self.assertEqual(db.session.get(SortDateMission, mission_id).destination, "SDF")

        self._apply_departures(
            self._outbound(4, "755", "N457UP", destination="HOT", planned="")
        )
        db.session.commit()
        self.assertEqual(SortDateMission.query.count(), 1)
        self.assertEqual(self._tail_state("N457UP").operational_status, "hot")

    def test_effective_tail_change_updates_same_mission_and_moves_hot(self):
        first = self._apply_departures(
            self._outbound(4, "755", "N457UP", destination="HOT")
        )
        db.session.commit()
        mission_id = first["results"][0]["mission_id"]

        self._apply_departures(
            self._outbound(4, "755", "N967UP", destination="HOT")
        )
        db.session.commit()

        mission = db.session.get(SortDateMission, mission_id)
        self.assertEqual(mission.assigned_tail_number, "N967UP")
        self.assertEqual(self._tail_state("N457UP").operational_status, "normal")
        self.assertEqual(self._tail_state("N967UP").operational_status, "hot")
        self.assertEqual(SortDateMission.query.count(), 1)

    def test_spare_row_creates_no_departure_and_uses_existing_spare_state(self):
        result = self._apply_departures(
            self._outbound(4, "", "N457UP", destination="SPARE", parking="E4")
        )
        db.session.commit()

        self.assertEqual(result["results"][0]["status"], "applied")
        self.assertEqual(SortDateMission.query.count(), 0)
        self.assertEqual(self._tail_state("N457UP").operational_status, "spare")
        assignment = SortDateParkingAssignment.query.one()
        self.assertEqual(assignment.position_code, "E04")

    def test_correlated_normal_mission_to_spare_is_preserved(self):
        first = self._apply_departures(self._outbound(4, "755", "N457UP"))
        db.session.commit()
        mission_id = first["results"][0]["mission_id"]

        result = self._apply_departures(
            self._outbound(4, "755", "N457UP", destination="SPARE")
        )
        db.session.commit()

        self.assertEqual(result["results"][0]["status"], "preserved")
        self.assertEqual(db.session.get(SortDateMission, mission_id).destination, "SDF")
        self.assertEqual(SortDateMission.query.count(), 1)

    def test_outbound_planned_update_never_changes_master_schedule(self):
        master = self._master("departure", "UPS0755", "RFD", "SDF", time(1, 0))
        mission = self._mission(
            "departure",
            "UPS0755",
            tail="N457UP",
            destination="SDF",
            source="master",
            master_id=master.id,
        )
        db.session.commit()

        self._apply_departures(
            self._outbound(4, "755", "N457UP", destination="ONT", planned="01:30")
        )
        db.session.commit()

        mission = db.session.get(SortDateMission, mission.id)
        master = db.session.get(MasterFlightSchedule, master.id)
        self.assertEqual((mission.destination, mission.planned_datetime_local.time()), ("ONT", time(1, 30)))
        self.assertEqual((master.destination, master.planned_time_local), ("SDF", time(1, 0)))

    def test_inbound_corrections_never_change_master_schedule(self):
        master = self._master("arrival", "UPS0947", "SDF", "RFD", time(23, 0))
        mission = self._mission(
            "arrival",
            "UPS0947",
            tail="N457UP",
            origin="SDF",
            source="master",
            master_id=master.id,
        )
        db.session.commit()

        self._apply_arrivals(
            self._inbound(4, "947", "N457UP", origin="SDF", planned="23:00")
        )
        db.session.commit()

        self._apply_arrivals(
            self._inbound(4, "955", "N457UP", origin="ONT", planned="23:20")
        )
        db.session.commit()

        mission = db.session.get(SortDateMission, mission.id)
        master = db.session.get(MasterFlightSchedule, master.id)
        self.assertEqual((mission.flight_number, mission.origin), ("UPS0955", "ONT"))
        self.assertEqual((master.flight_number, master.origin), ("UPS0947", "SDF"))

    def test_google_block_out_sets_departed(self):
        result = self._apply_departures(
            self._outbound(4, "755", "N457UP", operational="01:40"),
            now=datetime(2026, 8, 8, 6, 40),
        )
        db.session.commit()
        mission = db.session.get(SortDateMission, result["results"][0]["mission_id"])
        self.assertEqual(mission.departure_status, "departed")
        self.assertEqual(mission.actual_block_out_source, GOOGLE_MOTHERBRAIN_MISSION_SOURCE)
        self.assertEqual(mission.actual_block_out_datetime_utc, datetime(2026, 8, 8, 6, 40))

    def test_new_google_departure_with_blank_u_remains_scheduled(self):
        self.operation.sort_date = date(2026, 8, 10)
        db.session.commit()

        with patch(
            "app.services.google_motherbrain_live_missions.apply_google_motherbrain_parking",
            return_value={"status": "applied"},
        ) as parking:
            result = self._apply_departures(
                self._outbound(
                    4,
                    "755",
                    "N755UP",
                    parking="E4",
                    planned="8/11 2:25",
                    operational="",
                ),
                now=datetime(2026, 8, 11, 3, 10),
            )
            db.session.commit()

        mission = self._mission_by_flight("UPS0755")
        self.assertEqual(mission.departure_status, "scheduled")
        self.assertEqual(mission.planned_datetime_local, datetime(2026, 8, 11, 2, 25))
        self.assertEqual(mission.assigned_tail_number, "N755UP")
        self.assertIsNone(mission.actual_block_out_datetime_utc)
        self.assertEqual(result["results"][0]["parking"]["status"], "applied")
        parking.assert_called_once()

    def test_future_google_block_out_is_ignored_without_losing_other_updates(self):
        self.operation.sort_date = date(2026, 8, 10)
        db.session.commit()

        with patch(
            "app.services.google_motherbrain_live_missions.apply_google_motherbrain_parking",
            return_value={"status": "applied"},
        ) as parking:
            result = self._apply_departures(
                self._outbound(
                    4,
                    "755",
                    "N755UP",
                    destination="SDF",
                    parking="E4",
                    planned="8/11 2:25",
                    operational="8/11 2:39",
                ),
                now=datetime(2026, 8, 11, 3, 10),
            )
            db.session.commit()

        mission = self._mission_by_flight("UPS0755")
        self.assertEqual(mission.departure_status, "scheduled")
        self.assertIsNone(mission.actual_block_out_datetime_utc)
        self.assertEqual(mission.actual_block_out_source, "unknown")
        self.assertEqual(mission.planned_datetime_local, datetime(2026, 8, 11, 2, 25))
        self.assertEqual(mission.assigned_tail_number, "N755UP")
        self.assertEqual(result["results"][0]["parking"]["status"], "applied")
        self.assertIn("Future Google block-out ignored.", result["results"][0]["warnings"])
        parking.assert_called_once()

    def test_blank_google_u_clears_existing_future_google_block_out(self):
        self.operation.sort_date = date(2026, 8, 10)
        mission = self._mission("departure", "UPS0755", tail="N755UP", destination="SDF")
        mission.actual_block_out_datetime_utc = datetime(2026, 8, 11, 7, 39)
        mission.actual_block_out_source = GOOGLE_MOTHERBRAIN_MISSION_SOURCE
        mission.departure_status = "departed"
        db.session.commit()

        result = self._apply_departures(
            self._outbound(4, "755", "N755UP", operational=""),
            now=datetime(2026, 8, 11, 3, 10),
        )
        db.session.commit()

        mission = db.session.get(SortDateMission, mission.id)
        self.assertIsNone(mission.actual_block_out_datetime_utc)
        self.assertEqual(mission.actual_block_out_source, "unknown")
        self.assertEqual(mission.departure_status, "scheduled")
        self.assertIn(
            "Future Google block-out state cleared.",
            result["results"][0]["warnings"],
        )

    def test_future_native_block_out_is_not_cleared(self):
        self.operation.sort_date = date(2026, 8, 10)
        mission = self._mission("departure", "UPS0755", tail="N755UP", destination="SDF")
        mission.actual_block_out_datetime_utc = datetime(2026, 8, 11, 7, 39)
        mission.actual_block_out_source = "manual"
        mission.departure_status = "departed"
        db.session.commit()

        self._apply_departures(
            self._outbound(4, "755", "N755UP", operational=""),
            now=datetime(2026, 8, 11, 3, 10),
        )
        db.session.commit()

        mission = db.session.get(SortDateMission, mission.id)
        self.assertEqual(mission.actual_block_out_datetime_utc, datetime(2026, 8, 11, 7, 39))
        self.assertEqual(mission.actual_block_out_source, "manual")
        self.assertEqual(mission.departure_status, "departed")

    def test_blank_google_u_clears_past_google_block_out_and_departed_status(self):
        self.operation.sort_date = date(2026, 8, 10)
        mission = self._mission("departure", "UPS0755", tail="N755UP", destination="SDF")
        mission.actual_block_out_datetime_utc = datetime(2026, 8, 11, 2, 39)
        mission.actual_block_out_source = GOOGLE_MOTHERBRAIN_MISSION_SOURCE
        mission.departure_status = "departed"
        db.session.commit()

        self._apply_departures(
            self._outbound(4, "755", "N755UP", operational=""),
            now=datetime(2026, 8, 11, 3, 10),
        )
        db.session.commit()

        mission = db.session.get(SortDateMission, mission.id)
        self.assertIsNone(mission.actual_block_out_datetime_utc)
        self.assertEqual(mission.actual_block_out_source, "unknown")
        self.assertEqual(mission.departure_status, "scheduled")

    def test_blank_google_u_restores_strongest_remaining_progress(self):
        self.operation.sort_date = date(2026, 8, 10)
        mission = self._mission("departure", "UPS0755", tail="N755UP", destination="SDF")
        mission.crew_load_completed_at_utc = datetime(2026, 8, 11, 2, 20)
        mission.actual_block_out_datetime_utc = datetime(2026, 8, 11, 2, 39)
        mission.actual_block_out_source = GOOGLE_MOTHERBRAIN_MISSION_SOURCE
        mission.departure_status = "departed"
        db.session.commit()

        self._apply_departures(
            self._outbound(4, "755", "N755UP", operational=""),
            now=datetime(2026, 8, 11, 3, 10),
        )
        db.session.commit()

        mission = db.session.get(SortDateMission, mission.id)
        self.assertIsNone(mission.actual_block_out_datetime_utc)
        self.assertEqual(mission.departure_status, "crew_load_complete")

    def test_google_updates_do_not_regress_loading_without_timestamps(self):
        mission = self._mission(
            "departure",
            "UPS0755",
            tail="N755UP",
            destination="SDF",
            source=GOOGLE_MOTHERBRAIN_MISSION_SOURCE,
        )
        mission.departure_status = "loading"
        db.session.commit()

        self._apply_departures(
            self._outbound(4, "755", "N755UP", operational=""),
            now=datetime(2026, 8, 8, 3, 10),
        )
        db.session.commit()

        self.assertEqual(
            db.session.get(SortDateMission, mission.id).departure_status,
            "loading",
        )

    def test_blank_google_u_never_regresses_cancelled(self):
        mission = self._mission("departure", "UPS0755", tail="N755UP", destination="SDF")
        mission.actual_block_out_datetime_utc = datetime(2026, 8, 8, 6, 35)
        mission.actual_block_out_source = GOOGLE_MOTHERBRAIN_MISSION_SOURCE
        mission.departure_status = "cancelled"
        db.session.commit()

        self._apply_departures(
            self._outbound(4, "755", "N755UP", operational=""),
            now=datetime(2026, 8, 8, 7, 0),
        )
        db.session.commit()

        mission = db.session.get(SortDateMission, mission.id)
        self.assertIsNone(mission.actual_block_out_datetime_utc)
        self.assertEqual(mission.departure_status, "cancelled")

    def test_real_downstream_departure_statuses_are_not_downgraded(self):
        statuses = (
            "last_uld_enroute",
            "ramp_load_complete",
            "crew_load_complete",
            "blocked_out",
            "departed",
            "cancelled",
        )
        missions = []
        rows = []
        for index, status in enumerate(statuses, start=1):
            flight = f"UPS08{index}"
            tail = f"N80{index}UP"
            mission = self._mission(
                "departure",
                flight,
                tail=tail,
                destination="SDF",
                source=GOOGLE_MOTHERBRAIN_MISSION_SOURCE,
            )
            mission.departure_status = status
            if status == "departed":
                mission.actual_block_out_datetime_utc = datetime(2026, 8, 8, 2, 45)
                mission.actual_block_out_source = "manual"
            missions.append((mission, status))
            rows.append(self._outbound(index + 3, f"8{index}", tail, operational=""))
        db.session.commit()

        self._apply_departures(*rows, now=datetime(2026, 8, 8, 3, 10))
        db.session.commit()

        for mission, expected_status in missions:
            with self.subTest(expected_status=expected_status):
                self.assertEqual(
                    db.session.get(SortDateMission, mission.id).departure_status,
                    expected_status,
                )

    def test_loading_with_downstream_timestamp_is_not_normalized(self):
        mission = self._mission(
            "departure",
            "UPS0755",
            tail="N755UP",
            destination="SDF",
            source=GOOGLE_MOTHERBRAIN_MISSION_SOURCE,
        )
        mission.departure_status = "loading"
        mission.last_uld_enroute_at_utc = datetime(2026, 8, 8, 2, 55)
        db.session.commit()

        self._apply_departures(
            self._outbound(4, "755", "N755UP", operational=""),
            now=datetime(2026, 8, 8, 3, 10),
        )
        db.session.commit()

        self.assertEqual(
            db.session.get(SortDateMission, mission.id).departure_status,
            "loading",
        )

    def test_native_block_out_authority_is_preserved(self):
        mission = self._mission("departure", "UPS0755", tail="N457UP", destination="SDF")
        mission.actual_block_out_datetime_utc = datetime(2026, 8, 8, 6, 35)
        mission.actual_block_out_source = "manual"
        mission.departure_status = "departed"
        db.session.commit()

        result = self._apply_departures(
            self._outbound(4, "755", "N457UP", operational="01:45")
        )
        db.session.commit()

        mission = db.session.get(SortDateMission, mission.id)
        self.assertEqual(mission.actual_block_out_datetime_utc, datetime(2026, 8, 8, 6, 35))
        self.assertEqual(mission.actual_block_out_source, "manual")
        self.assertIn("Native Neo block-out preserved", result["results"][0]["warnings"][0])

    def test_google_rain_block_out_authority_is_preserved(self):
        mission = self._mission("departure", "UPS0755", tail="N457UP", destination="SDF")
        mission.actual_block_out_datetime_utc = datetime(2026, 8, 8, 6, 35)
        mission.actual_block_out_source = "google_rain"
        mission.departure_status = "departed"
        db.session.commit()

        result = self._apply_departures(
            self._outbound(4, "755", "N457UP", operational="01:45")
        )
        db.session.commit()

        mission = db.session.get(SortDateMission, mission.id)
        self.assertEqual(mission.actual_block_out_datetime_utc, datetime(2026, 8, 8, 6, 35))
        self.assertEqual(mission.actual_block_out_source, "google_rain")
        self.assertIn("Native Neo block-out preserved", result["results"][0]["warnings"][0])

    def test_pending_tail_swap_is_recorded_but_not_effective_then_clears(self):
        first = self._apply_departures(
            self._outbound(
                4,
                "755",
                "N457UP",
                proposed_tail="N967UP",
                swap_flight="755",
                swap_destination="SDF",
                acknowledgment="ACK",
            )
        )
        db.session.commit()
        mission_id = first["results"][0]["mission_id"]
        link = SortDateGoogleMissionLink.query.one()
        self.assertEqual(db.session.get(SortDateMission, mission_id).assigned_tail_number, "N457UP")
        self.assertEqual(link.pending_tail_number, "N967UP")

        self._apply_departures(
            self._outbound(4, "755", "N967UP", proposed_tail="N967UP")
        )
        db.session.commit()
        link = db.session.get(SortDateGoogleMissionLink, link.id)
        self.assertEqual(db.session.get(SortDateMission, mission_id).assigned_tail_number, "N967UP")
        self.assertIsNone(link.pending_tail_number)

    def test_outbound_canx_is_explicitly_unsupported(self):
        result = self._apply_departures(
            self._outbound(4, "755", "N457UP", destination="CANX")
        )
        self.assertEqual(result["results"][0]["status"], "skipped")
        self.assertIn("unsupported", result["results"][0]["reason"].lower())
        self.assertEqual(SortDateMission.query.count(), 0)

    def test_inbound_and_outbound_delegate_parking_to_shared_service(self):
        with patch(
            "app.services.google_motherbrain_live_missions.apply_google_motherbrain_parking"
        ) as parking:
            parking.return_value = {"status": "applied"}
            apply_google_motherbrain_live_rows(
                self.operation,
                inbound_rows=[self._inbound(4, "947", "N457UP", parking="A1")],
                outbound_rows=[self._outbound(4, "755", "N458UP", parking="E4-b")],
            )
            db.session.flush()

        self.assertEqual(parking.call_count, 2)
        self.assertEqual(parking.call_args_list[0].args[2], "A1")
        self.assertEqual(parking.call_args_list[1].args[2], "E4-b")

    def test_parking_conflict_applies_without_failing_mission(self):
        self._mission("departure", "UPS0700", tail="N457UP", destination="SDF")
        db.session.flush()
        db.session.add(
            SortDateParkingAssignment(
                sort_date_operation_id=self.operation.id,
                tail_number="N457UP",
                ramp_code="E",
                position_code="E03",
                lane_number=1,
            )
        )
        db.session.commit()

        result = self._apply_departures(
            self._outbound(4, "755", "N967UP", parking="E4")
        )
        db.session.commit()

        self.assertEqual(result["results"][0]["status"], "applied")
        self.assertEqual(result["results"][0]["parking"]["status"], "applied")
        self.assertEqual(SortDateMission.query.filter_by(flight_number="UPS0755").count(), 1)
        self.assertEqual(MotherBrainAlert.query.filter_by(active=True).count(), 1)

    def test_secondary_parking_reuses_native_b_slot_behavior(self):
        self._mission("departure", "UPS0700", tail="N457UP", destination="SDF")
        db.session.flush()
        db.session.add(
            SortDateParkingAssignment(
                sort_date_operation_id=self.operation.id,
                tail_number="N457UP",
                ramp_code="E",
                position_code="E04",
                lane_number=1,
            )
        )
        db.session.commit()

        result = self._apply_departures(
            self._outbound(4, "755", "N458UP", parking="E4-b")
        )
        db.session.commit()
        parking = result["results"][0]["parking"]
        self.assertEqual((parking["position_code"], parking["lane_number"]), ("E04", 2))

    def test_bad_row_does_not_stop_valid_row(self):
        result = self._apply_arrivals(
            self._inbound(4, "947", "", origin="BAD"),
            self._inbound(5, "955", "N457UP", origin="SDF"),
        )
        db.session.commit()

        self.assertEqual([row["status"] for row in result["results"]], ["skipped", "applied"])
        self.assertEqual(SortDateMission.query.filter_by(flight_number="UPS0955").count(), 1)

    def test_missing_row_in_later_batch_does_not_delete_or_cancel(self):
        first = self._apply_arrivals(self._inbound(4, "947", "N457UP"))
        db.session.commit()
        mission_id = first["results"][0]["mission_id"]

        self._apply_arrivals(self._inbound(5, "955", "N458UP"))
        db.session.commit()

        mission = db.session.get(SortDateMission, mission_id)
        self.assertIsNotNone(mission)
        self.assertNotEqual(mission.arrival_status, "cancelled")

    def test_service_makes_no_google_calls_and_live_polling_stays_off(self):
        self.assertFalse(google_motherbrain_live_polling_enabled(self.gateway, "night"))
        with patch(
            "app.services.google_motherbrain_sheets.read_google_motherbrain_envelope"
        ) as reader, patch(
            "app.services.neosektor_sheets_compat.mirror_neosektor_sheet_update"
        ) as writer:
            self._apply_arrivals(self._inbound(4, "947", "N457UP"))
            db.session.commit()
        reader.assert_not_called()
        writer.assert_not_called()
        self.assertFalse(google_motherbrain_live_polling_enabled(self.gateway, "night"))

    def test_schema_supports_google_source_on_ground_departed_and_null_planned(self):
        self.assertTrue(SortDateMission.__table__.c.planned_datetime_local.nullable)
        self.assertTrue(SortDateMission.__table__.c.planned_datetime_utc.nullable)
        constraint_sql = " ".join(
            str(constraint.sqltext)
            for constraint in SortDateMission.__table__.constraints
            if hasattr(constraint, "sqltext")
        )
        self.assertIn("google_motherbrain", constraint_sql)
        self.assertIn("on_ground", constraint_sql)
        self.assertIn("departed", constraint_sql)
        self.assertIn("scheduled", constraint_sql)
        self.assertIn("sort_date_google_mission_links", db.inspect(db.engine).get_table_names())

    def _apply_arrivals(self, *rows, now=None):
        return apply_google_motherbrain_live_mission_batch(
            self.operation,
            "arrival",
            rows,
            now=now,
        )

    def _apply_departures(self, *rows, now=None):
        return apply_google_motherbrain_live_mission_batch(
            self.operation,
            "departure",
            rows,
            now=now,
        )

    def _inbound(
        self,
        sheet_row,
        flight,
        tail,
        *,
        origin="SDF",
        parking="",
        planned="",
        operational="",
        status="",
    ):
        return {
            "sheet_row": sheet_row,
            "P": flight,
            "Q": tail,
            "R": origin,
            "S": parking,
            "T": planned,
            "U": operational,
            "W": status,
        }

    def _outbound(
        self,
        sheet_row,
        flight,
        tail,
        *,
        destination="SDF",
        parking="",
        planned="",
        operational="",
        proposed_tail="",
        swap_flight="",
        swap_destination="",
        acknowledgment="",
    ):
        return {
            "sheet_row": sheet_row,
            "P": flight,
            "Q": tail,
            "R": destination,
            "S": parking,
            "T": planned,
            "U": operational,
            "W": swap_flight,
            "X": swap_destination,
            "Y": proposed_tail,
            "Z": acknowledgment,
        }

    def _operation(self, sort_date, sort_name):
        operation = SortDateOperation(
            gateway=self.gateway,
            gateway_code=self.gateway.code,
            sort_date=sort_date,
            sort_name=sort_name,
        )
        db.session.add(operation)
        db.session.flush()
        return operation

    def _mission(
        self,
        mission_type,
        flight_number,
        *,
        tail,
        origin="RFD",
        destination="RFD",
        source="manual",
        master_id=None,
        arrival_status=None,
    ):
        mission = SortDateMission(
            sort_date_operation=self.operation,
            sort_date=self.operation.sort_date,
            gateway_code=self.operation.gateway_code,
            sort_name=self.operation.sort_name,
            mission_type=mission_type,
            mission_source=source,
            master_flight_schedule_id=master_id,
            flight_number=flight_number,
            origin=origin,
            destination=destination,
            timezone="America/Chicago",
            planned_datetime_local=datetime(2026, 8, 7, 23, 0),
            planned_datetime_utc=datetime(2026, 8, 8, 4, 0),
            assigned_tail_number=tail,
            tail_source=source,
            fuel_status="waiting",
            arrival_status=(arrival_status or "scheduled") if mission_type == "arrival" else None,
            departure_status="scheduled" if mission_type == "departure" else None,
        )
        db.session.add(mission)
        db.session.flush()
        return mission

    def _master(self, mission_type, flight, origin, destination, planned):
        master = MasterFlightSchedule(
            gateway=self.gateway,
            gateway_code="RFD",
            sort_name="night",
            mission_type=mission_type,
            flight_number=flight,
            origin=origin,
            destination=destination,
            active=True,
            active_days="monday,tuesday,wednesday,thursday,friday,saturday,sunday",
            planned_time_local=planned,
            timezone="America/Chicago",
        )
        db.session.add(master)
        db.session.flush()
        return master

    def _tail_state(self, tail_number):
        return SortDateTailState.query.filter_by(
            sort_date=self.operation.sort_date,
            gateway_code=self.operation.gateway_code,
            sort_name=self.operation.sort_name,
            tail_number=tail_number,
        ).one()

    def _mission_by_flight(self, flight_number):
        return SortDateMission.query.filter_by(flight_number=flight_number).one()


if __name__ == "__main__":
    unittest.main()
