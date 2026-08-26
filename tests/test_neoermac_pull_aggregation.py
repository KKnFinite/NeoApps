import unittest
from datetime import date, datetime, time

from app import create_app
from app.extensions import db
from app.models import (
    GatewaySortMatrix,
    MasterFlightSchedule,
    NeoErmacDoorPull,
    SortDateMission,
    SortDateOperation,
    SortTimelineSettings,
    SortTimelineSortSetting,
)
from app.services.access_control import ensure_default_gateway_and_nodes
from app.services.neoermac_building_lineup import (
    get_building_lineup_rows,
    save_building_lineup_destination,
)
from app.services.neoermac_door_view import (
    door_view_uld_state,
    save_single_door_pull,
)
from app.services.neoermac_dashboard import neoermac_dashboard_context


_DEFAULT_PULL = object()


class NeoErmacPullAggregationTest(unittest.TestCase):
    def setUp(self):
        TestConfig = type(
            "TestConfig",
            (),
            {
                "SECRET_KEY": "test",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
                "DEFAULT_GATEWAY_TIMEZONE": "America/Chicago",
                "CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE": datetime(
                    2026, 8, 10, 22, 0
                ),
            },
        )
        self.app = create_app(TestConfig)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.gateway = ensure_default_gateway_and_nodes()
        timeline = SortTimelineSettings(
            gateway_id=self.gateway.id,
            gateway_code=self.gateway.code,
        )
        db.session.add(timeline)
        db.session.flush()
        db.session.add_all(
            [
                GatewaySortMatrix(
                    gateway_id=self.gateway.id,
                    gateway_code=self.gateway.code,
                    day_of_week="monday",
                    sort_name="night",
                    is_active=True,
                ),
                SortTimelineSortSetting(
                    timeline_settings=timeline,
                    gateway_id=self.gateway.id,
                    gateway_code=self.gateway.code,
                    sort_name="night",
                    sort_window_start_local=time(20, 0),
                    sort_window_end_local=time(4, 0),
                ),
            ]
        )
        self.operation = SortDateOperation(
            gateway_id=self.gateway.id,
            sort_date=date(2026, 8, 10),
            gateway_code=self.gateway.code,
            sort_name="night",
        )
        db.session.add(self.operation)
        db.session.flush()
        self._assign_destination("runout_10", "east_destination_1", "SDF")
        self._assign_destination("runout_10", "west_destination_1", "SDF")
        self.mission = self._add_departure()
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_latest_pure_and_mix_are_aggregated_independently(self):
        self._save("D32", "pure", "01:55")
        self._save("D34", "pure", "01:45")
        self._save("D32", "mix", "02:10")
        self._save("D34", "mix", "02:20")

        mission = self._mission()
        self.assertEqual(mission.actual_pure_pull_time_local, time(1, 55))
        self.assertEqual(mission.actual_mix_pull_time_local, time(2, 20))

    def test_no_values_account_for_requirements_without_contributing_timestamp(self):
        self.mission.mix_pull_time_local = None
        db.session.commit()

        first_card = self._save("D32", "pure", no_pull=True)
        self.assertTrue(first_card["pulls_complete"])
        self.assertEqual(self._mission().departure_status, "scheduled")
        second_card = self._save("D34", "pure", "23:45")

        mission = self._mission()
        self.assertTrue(second_card["pulls_complete"])
        self.assertEqual(mission.actual_pure_pull_time_local, time(23, 45))
        self.assertIsNone(mission.actual_mix_pull_time_local)
        self.assertEqual(mission.departure_status, "last_uld_enroute")
        self.assertEqual(
            mission.last_uld_enroute_at_utc,
            datetime(2026, 8, 11, 4, 45),
        )

    def test_unplanned_pull_type_is_not_listed_as_incomplete(self):
        self.mission.mix_pull_time_local = None
        db.session.commit()

        context = neoermac_dashboard_context(self.gateway)
        pull_types = [row["pull_type"] for row in context["west"]]

        self.assertEqual(pull_types, ["Pure"])

    def test_all_no_pull_requirements_complete_without_synthetic_event_time(self):
        for door in ("D32", "D34"):
            self._save(door, "pure", no_pull=True)
            self._save(door, "mix", no_pull=True)

        mission = self._mission()
        self.assertEqual(mission.departure_status, "last_uld_enroute")
        self.assertIsNone(mission.actual_pure_pull_time_local)
        self.assertIsNone(mission.actual_mix_pull_time_local)
        self.assertIsNone(mission.last_uld_enroute_at_utc)

    def test_changing_and_clearing_pull_recomputes_aggregate_and_derived_status(self):
        self.mission.mix_pull_time_local = None
        db.session.commit()

        self._save("D32", "pure", "23:40")
        self._save("D34", "pure", "23:50")
        self.assertEqual(self._mission().actual_pure_pull_time_local, time(23, 50))

        self._save("D34", "pure", "23:35")
        mission = self._mission()
        self.assertEqual(mission.actual_pure_pull_time_local, time(23, 40))
        self.assertEqual(
            mission.last_uld_enroute_at_utc,
            datetime(2026, 8, 11, 4, 40),
        )

        self._save("D32", "pure", "")
        mission = self._mission()
        self.assertEqual(mission.actual_pure_pull_time_local, time(23, 35))
        self.assertEqual(mission.departure_status, "scheduled")
        self.assertIsNone(mission.last_uld_enroute_at_utc)

    def test_last_uld_waits_for_every_door_and_uses_latest_real_pull(self):
        self._save("D32", "pure", "23:50")
        self._save("D32", "mix", "00:20")
        self._save("D34", "pure", no_pull=True)

        mission = self._mission()
        self.assertEqual(mission.departure_status, "scheduled")
        self.assertIsNone(mission.last_uld_enroute_at_utc)

        self._save("D34", "mix", "00:25")
        mission = self._mission()
        state = door_view_uld_state(self.gateway, "D34")
        self.assertEqual(mission.departure_status, "last_uld_enroute")
        self.assertEqual(
            mission.last_uld_enroute_at_utc,
            datetime(2026, 8, 11, 5, 25),
        )
        self.assertEqual(
            state["destinations"][0]["status"],
            "Last Uld Enroute",
        )

    def test_stronger_operational_progress_prevents_regression(self):
        self.mission.mix_pull_time_local = None
        db.session.commit()
        cases = (
            ("ramp_load_complete", "ramp_load_completed_at_utc"),
            ("crew_load_complete", "crew_load_completed_at_utc"),
            ("blocked_out", "actual_block_out_datetime_utc"),
            ("departed", None),
            ("cancelled", None),
        )
        for status, timestamp_attr in cases:
            with self.subTest(status=status):
                mission = self._mission()
                mission.departure_status = "scheduled"
                mission.ramp_load_completed_at_utc = None
                mission.crew_load_completed_at_utc = None
                mission.actual_block_out_datetime_utc = None
                db.session.commit()
                self._save("D32", "pure", "23:40")
                self._save("D34", "pure", "23:50")

                mission = self._mission()
                derived_at = mission.last_uld_enroute_at_utc
                mission.departure_status = status
                if timestamp_attr:
                    setattr(mission, timestamp_attr, datetime(2026, 8, 11, 5, 0))
                db.session.commit()

                self._save("D34", "pure", "")
                mission = self._mission()
                self.assertEqual(mission.departure_status, status)
                self.assertEqual(mission.last_uld_enroute_at_utc, derived_at)
                self.assertEqual(mission.actual_pure_pull_time_local, time(23, 40))

        mission = self._mission()
        mission.departure_status = "scheduled"
        mission.actual_block_out_datetime_utc = datetime(2026, 8, 11, 5, 30)
        db.session.commit()
        self._save("D32", "pure", "")
        mission = self._mission()
        self.assertEqual(mission.departure_status, "scheduled")
        self.assertIsNotNone(mission.last_uld_enroute_at_utc)

    def test_lineup_changes_recompute_current_doors_and_preserve_old_records(self):
        self.mission.mix_pull_time_local = None
        self._add_master_departure()
        db.session.commit()
        self._save("D32", "pure", "23:40")
        self._save("D34", "pure", "23:50")
        self.assertEqual(self._mission().departure_status, "last_uld_enroute")

        save_building_lineup_destination(
            self.gateway,
            "lineup_runout_10_east_destination_1",
            "",
        )
        db.session.commit()
        mission = self._mission()
        self.assertEqual(mission.actual_pure_pull_time_local, time(23, 50))
        self.assertEqual(mission.departure_status, "last_uld_enroute")
        self.assertEqual(NeoErmacDoorPull.query.count(), 2)

        save_building_lineup_destination(
            self.gateway,
            "lineup_runout_11_west_destination_1",
            "SDF",
        )
        db.session.commit()
        mission = self._mission()
        self.assertEqual(mission.actual_pure_pull_time_local, time(23, 50))
        self.assertEqual(mission.departure_status, "scheduled")

        self._save("D37", "pure", no_pull=True)
        mission = self._mission()
        self.assertEqual(mission.departure_status, "last_uld_enroute")
        self.assertEqual(mission.actual_pure_pull_time_local, time(23, 50))
        self.assertEqual(NeoErmacDoorPull.query.count(), 3)

    def _assign_destination(self, runout_key, field_name, destination):
        row = next(
            row
            for row in get_building_lineup_rows(self.gateway)
            if row.runout_key == runout_key
        )
        setattr(row, field_name, destination)
        db.session.flush()

    def _add_departure(
        self,
        destination="SDF",
        pure_pull_time_local=_DEFAULT_PULL,
        mix_pull_time_local=_DEFAULT_PULL,
    ):
        mission = SortDateMission(
            sort_date=self.operation.sort_date,
            gateway_code=self.gateway.code,
            sort_name=self.operation.sort_name,
            sort_date_operation_id=self.operation.id,
            mission_type="departure",
            mission_source="master",
            wave="1",
            flight_number="UPS302",
            origin=self.gateway.code,
            destination=destination,
            timezone="America/Chicago",
            planned_datetime_local=datetime(2026, 8, 11, 2, 30),
            planned_datetime_utc=datetime(2026, 8, 11, 7, 30),
            planned_source="master",
            departure_status="scheduled",
            pure_pull_time_local=(
                time(23, 30)
                if pure_pull_time_local is _DEFAULT_PULL
                else pure_pull_time_local
            ),
            mix_pull_time_local=(
                time(0, 10)
                if mix_pull_time_local is _DEFAULT_PULL
                else mix_pull_time_local
            ),
        )
        db.session.add(mission)
        db.session.flush()
        return mission

    def _add_master_departure(self):
        db.session.add(
            MasterFlightSchedule(
                gateway_id=self.gateway.id,
                gateway_code=self.gateway.code,
                sort_name="night",
                mission_type="departure",
                flight_number="UPS302",
                origin=self.gateway.code,
                destination="SDF",
                active=True,
                active_days="monday,tuesday,wednesday,thursday,friday,saturday,sunday",
                planned_time_local=time(2, 30),
                timezone="America/Chicago",
                pure_pull_time_local=time(23, 30),
            )
        )

    def _save(self, door, pull_key, actual_value="", no_pull=False):
        card = save_single_door_pull(
            self.gateway,
            door,
            "SDF",
            pull_key,
            actual_value,
            no_pull,
        )
        db.session.commit()
        return card

    def _mission(self):
        return db.session.get(SortDateMission, self.mission.id)


if __name__ == "__main__":
    unittest.main()
