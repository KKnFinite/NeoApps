from datetime import date, datetime, time
import unittest

from app import create_app
from app.extensions import db
from app.models import (
    MasterFlightSchedule,
    SortDateCrewAssignment,
    SortDateMission,
    SortDateOperation,
    SortDateTailState,
)
from app.services.sort_date_operations import (
    ensure_tail_state_for_mission,
    generate_sort_date_operation_from_master,
    parse_active_days,
    sync_sort_operation_with_master,
)


class SortDateGenerationTest(unittest.TestCase):
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

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_generates_operation_from_matching_active_master_rows(self):
        self._add_master(flight_number="5X123", active_days="monday,tuesday")
        self._add_master(flight_number="5X456", active_days="monday")
        db.session.commit()

        operation = generate_sort_date_operation_from_master(
            sort_date=date(2026, 6, 1),
            gateway_code="RFD",
            sort_name="night",
            generated_by_user_id=7,
        )

        self.assertEqual(operation.window_minutes, 0)
        self.assertEqual(operation.generated_by_user_id, 7)
        self.assertEqual(len(operation.missions), 2)
        self.assertEqual(
            sorted(mission.flight_number for mission in operation.missions),
            ["5X123", "5X456"],
        )

    def test_inactive_master_rows_are_not_copied(self):
        self._add_master(flight_number="5X123", active=True)
        self._add_master(flight_number="5X999", active=False)
        db.session.commit()

        operation = generate_sort_date_operation_from_master(
            sort_date=date(2026, 6, 1),
            gateway_code="RFD",
            sort_name="night",
        )

        self.assertEqual([mission.flight_number for mission in operation.missions], ["5X123"])

    def test_active_days_filter_uses_requested_sort_date(self):
        self._add_master(flight_number="5X123", active_days="monday")
        self._add_master(flight_number="5X456", active_days="tuesday")
        db.session.commit()

        operation = generate_sort_date_operation_from_master(
            sort_date=date(2026, 6, 1),
            gateway_code="RFD",
            sort_name="night",
        )

        self.assertEqual([mission.flight_number for mission in operation.missions], ["5X123"])
        self.assertEqual(parse_active_days('["monday", "wednesday"]'), {"monday", "wednesday"})

    def test_generated_mission_has_master_source_and_master_link(self):
        master = self._add_master(flight_number="5X123")
        db.session.commit()

        operation = generate_sort_date_operation_from_master(
            sort_date=date(2026, 6, 1),
            gateway_code="RFD",
            sort_name="night",
        )

        mission = operation.missions[0]
        self.assertEqual(mission.mission_source, "master")
        self.assertEqual(mission.master_flight_schedule_id, master.id)
        self.assertEqual(mission.sort_date_operation, operation)

    def test_departure_copies_pull_times_and_sets_source(self):
        self._add_master(
            mission_type="departure",
            pure_pull_time_local=time(1, 20),
            first_mix_pull_time_local=time(1, 40),
            final_mix_pull_time_local=time(1, 55),
        )
        db.session.commit()

        operation = generate_sort_date_operation_from_master(
            sort_date=date(2026, 6, 1),
            gateway_code="RFD",
            sort_name="night",
        )

        mission = operation.missions[0]
        self.assertEqual(mission.pure_pull_time_local, time(1, 20))
        self.assertEqual(mission.first_mix_pull_time_local, time(1, 40))
        self.assertEqual(mission.final_mix_pull_time_local, time(1, 55))
        self.assertEqual(mission.pull_time_source, "master")

    def test_arrival_has_no_pull_times_or_pull_source(self):
        self._add_master(
            mission_type="arrival",
            origin="SDF",
            destination="RFD",
            pure_pull_time_local=time(1, 20),
            first_mix_pull_time_local=time(1, 40),
            final_mix_pull_time_local=time(1, 55),
        )
        db.session.commit()

        operation = generate_sort_date_operation_from_master(
            sort_date=date(2026, 6, 1),
            gateway_code="RFD",
            sort_name="night",
        )

        mission = operation.missions[0]
        self.assertIsNone(mission.pure_pull_time_local)
        self.assertIsNone(mission.first_mix_pull_time_local)
        self.assertIsNone(mission.final_mix_pull_time_local)
        self.assertIsNone(mission.pull_time_source)

    def test_duplicate_operation_generation_is_blocked(self):
        self._add_master(flight_number="5X123")
        db.session.commit()
        generate_sort_date_operation_from_master(
            sort_date=date(2026, 6, 1),
            gateway_code="RFD",
            sort_name="night",
        )

        with self.assertRaises(ValueError):
            generate_sort_date_operation_from_master(
                sort_date=date(2026, 6, 1),
                gateway_code="RFD",
                sort_name="night",
            )

    def test_duplicate_flight_numbers_inside_operation_are_blocked(self):
        self._add_master(flight_number="5X123")
        self._add_master(flight_number="5X123")
        db.session.commit()

        with self.assertRaises(ValueError):
            generate_sort_date_operation_from_master(
                sort_date=date(2026, 6, 1),
                gateway_code="RFD",
                sort_name="night",
            )

        self.assertEqual(SortDateOperation.query.count(), 0)

    def test_tail_state_is_created_when_assigned_tail_exists(self):
        mission = self._mission(assigned_tail_number="N123UP")
        db.session.add(mission.sort_date_operation)
        db.session.add(mission)
        db.session.flush()

        tail_state = ensure_tail_state_for_mission(mission, parking_position="A1")

        self.assertEqual(tail_state.tail_number, "N123UP")
        self.assertEqual(tail_state.aircraft_type, "A300")
        self.assertEqual(tail_state.aircraft_type_source, "derived")
        self.assertEqual(tail_state.parking_position, "A1")

    def test_master_generation_does_not_copy_preferred_parking_to_tail_state(self):
        master = self._add_master(
            flight_number="5X777",
            preferred_parking="A1",
        )
        master.assigned_tail_number = "N123UP"
        db.session.commit()

        generate_sort_date_operation_from_master(
            sort_date=date(2026, 6, 1),
            gateway_code="RFD",
            sort_name="night",
        )

        tail_state = SortDateTailState.query.filter_by(tail_number="N123UP").first()
        self.assertIsNotNone(tail_state)
        self.assertIsNone(tail_state.parking_position)

    def test_manual_tail_state_aircraft_type_is_not_overwritten(self):
        mission = self._mission(assigned_tail_number="N123UP")
        manual_tail_state = SortDateTailState(
            sort_date=mission.sort_date,
            gateway_code=mission.gateway_code,
            sort_name=mission.sort_name,
            tail_number="N123UP",
            aircraft_type="A330",
            aircraft_type_source="manual",
            parking_position="Manual",
        )
        db.session.add(mission.sort_date_operation)
        db.session.add(mission)
        db.session.add(manual_tail_state)
        db.session.flush()

        tail_state = ensure_tail_state_for_mission(mission, parking_position="A1")

        self.assertEqual(tail_state.aircraft_type, "A330")
        self.assertEqual(tail_state.aircraft_type_source, "manual")
        self.assertEqual(tail_state.parking_position, "Manual")

    def test_default_crew_assignment_slots_are_created(self):
        self._add_master(flight_number="5X123")
        db.session.commit()

        operation = generate_sort_date_operation_from_master(
            sort_date=date(2026, 6, 1),
            gateway_code="RFD",
            sort_name="night",
        )
        mission = operation.missions[0]
        assignments = SortDateCrewAssignment.query.filter_by(
            sort_date_mission_id=mission.id,
        ).all()

        self.assertEqual(
            sorted(assignment.aircraft_section for assignment in assignments),
            ["other", "topside"],
        )
        self.assertTrue(all(assignment.required for assignment in assignments))
        self.assertTrue(all(assignment.crew_id is None for assignment in assignments))
        self.assertTrue(all(assignment.assigned_at_utc is None for assignment in assignments))
        self.assertTrue(all(assignment.marked_not_required_at_utc is None for assignment in assignments))

    def test_sync_adds_new_matching_master_row_to_existing_operation(self):
        operation = self._operation()
        db.session.add(operation)
        self._add_master(flight_number="5X777", active_days="monday")
        db.session.commit()

        result = sync_sort_operation_with_master(operation)
        db.session.commit()

        mission = SortDateMission.query.filter_by(flight_number="5X777").one()
        self.assertEqual(len(result["added"]), 1)
        self.assertEqual(mission.sort_date_operation_id, operation.id)
        self.assertEqual(mission.mission_source, "master")
        self.assertIsNotNone(mission.master_flight_schedule_id)

    def test_sync_updates_linked_mission_from_newer_master_template(self):
        master = self._add_master(
            flight_number="5X123",
            destination="SDF",
            pure_pull_time_local=time(1, 20),
        )
        db.session.commit()
        operation = generate_sort_date_operation_from_master(
            sort_date=date(2026, 6, 1),
            gateway_code="RFD",
            sort_name="night",
        )
        operation.generated_at_utc = datetime(2026, 1, 1, 0, 0)
        mission = operation.missions[0]
        master.destination = "ONT"
        master.planned_time_local = time(3, 15)
        master.first_mix_pull_time_local = time(2, 45)
        master.final_mix_pull_time_local = time(3, 0)
        master.updated_at = datetime(2026, 1, 2, 0, 0)
        db.session.commit()

        result = sync_sort_operation_with_master(operation)
        db.session.commit()

        self.assertEqual(len(result["updated"]), 1)
        self.assertEqual(mission.destination, "ONT")
        self.assertEqual(mission.planned_datetime_local, datetime(2026, 6, 1, 3, 15))
        self.assertEqual(mission.first_mix_pull_time_local, time(2, 45))
        self.assertEqual(mission.final_mix_pull_time_local, time(3, 0))
        self.assertEqual(mission.pull_time_source, "master")

    def test_sync_does_not_duplicate_or_overwrite_manual_special_flights(self):
        operation = self._operation()
        manual_mission = self._mission(
            sort_date_operation=operation,
            mission_source="manual",
            master_flight_schedule_id=None,
            flight_number="5X999",
            destination="MANUAL",
        )
        db.session.add(operation)
        db.session.add(manual_mission)
        self._add_master(flight_number="5X999", destination="SDF")
        db.session.commit()

        result = sync_sort_operation_with_master(operation)
        db.session.commit()

        missions = SortDateMission.query.filter_by(flight_number="5X999").all()
        self.assertEqual(len(missions), 1)
        self.assertEqual(missions[0].mission_source, "manual")
        self.assertEqual(missions[0].destination, "MANUAL")
        self.assertEqual(len(result["skipped"]), 1)

    def test_sync_is_gateway_sort_and_date_scoped(self):
        operation = self._operation(gateway_code="DFW", sort_name="night")
        db.session.add(operation)
        self._add_master(flight_number="RFDONLY", gateway_code="RFD", sort_name="night")
        self._add_master(flight_number="TWILIGHT", gateway_code="DFW", sort_name="twilight")
        self._add_master(flight_number="SUNDAY", gateway_code="DFW", sort_name="night", active_days="sunday")
        db.session.commit()

        result = sync_sort_operation_with_master(operation)
        db.session.commit()

        self.assertEqual(result["added"], [])
        self.assertEqual(SortDateMission.query.count(), 0)

    def _add_master(self, **overrides):
        values = {
            "gateway_code": "RFD",
            "sort_name": "night",
            "mission_type": "departure",
            "flight_number": "5X123",
            "origin": "RFD",
            "destination": "SDF",
            "active": True,
            "active_days": "monday,tuesday",
            "planned_time_local": time(2, 10),
        }
        values.update(overrides)
        master = MasterFlightSchedule(**values)
        db.session.add(master)
        return master

    def _operation(self, **overrides):
        values = {
            "sort_date": date(2026, 6, 1),
            "gateway_code": "RFD",
            "sort_name": "night",
        }
        values.update(overrides)
        return SortDateOperation(**values)

    def _mission(self, **overrides):
        operation = SortDateOperation(
            sort_date=date(2026, 6, 1),
            gateway_code="RFD",
            sort_name="night",
        )
        values = {
            "sort_date_operation": operation,
            "sort_date": date(2026, 6, 1),
            "gateway_code": "RFD",
            "sort_name": "night",
            "mission_type": "departure",
            "mission_source": "master",
            "flight_number": "5X123",
            "origin": "RFD",
            "destination": "SDF",
            "planned_datetime_local": datetime(2026, 6, 1, 2, 10),
            "planned_datetime_utc": datetime(2026, 6, 1, 7, 10),
        }
        values.update(overrides)
        return SortDateMission(**values)


if __name__ == "__main__":
    unittest.main()
