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
