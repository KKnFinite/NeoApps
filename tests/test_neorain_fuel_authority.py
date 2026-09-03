from datetime import date, datetime
import unittest

from app import create_app
from app.extensions import db
from app.models import (
    Gateway,
    NeoRainGoogleFuelValue,
    NeoScorpionFuelAssignment,
    NeoScorpionFuelingEvent,
    NeoScorpionFuelWorkState,
    SortDateMission,
    SortDateOperation,
    User,
)
from app.services.neorain_fuel_authority import (
    RAIN_FUEL_SOURCE_NEO,
    acknowledge_fuel_review,
    completed_scorpion_fuel_by_mission,
    fuel_review_pending_by_mission,
    google_rain_fuel_by_mission,
    rain_fuel_data_source,
    record_google_rain_fuel_value,
    set_rain_fuel_data_source,
)


class NeoRainFuelAuthorityTest(unittest.TestCase):
    def setUp(self):
        config = type("FuelAuthorityConfig", (), {"SECRET_KEY": "test", "TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", "SQLALCHEMY_TRACK_MODIFICATIONS": False})
        self.app = create_app(config)
        self.ctx = self.app.app_context(); self.ctx.push(); db.create_all()
        self.gateway = Gateway(code="RFD", name="RFD"); db.session.add(self.gateway)
        self.operation = SortDateOperation(gateway=self.gateway, gateway_code="RFD", sort_name="night", sort_date=date(2026, 9, 3))
        self.mission = SortDateMission(sort_date_operation=self.operation, sort_date=date(2026,9,3), sort_name="night", gateway_code="RFD", mission_type="departure", flight_number="UPS1", origin="RFD", destination="SDF", planned_datetime_local=datetime(2026,9,4,2), planned_datetime_utc=datetime(2026,9,4,7))
        self.other = SortDateMission(sort_date_operation=self.operation, sort_date=date(2026,9,3), sort_name="night", gateway_code="RFD", mission_type="departure", flight_number="UPS2", origin="RFD", destination="SDF", planned_datetime_local=datetime(2026,9,4,3), planned_datetime_utc=datetime(2026,9,4,8))
        self.user = User(username="rain-user", email="rain@test", first_name="Rain", last_name="User", password_hash="test")
        db.session.add_all((self.operation, self.mission, self.other, self.user)); db.session.commit()

    def tearDown(self):
        db.session.remove(); db.drop_all(); self.ctx.pop()

    def test_google_and_neo_authority_never_mix_values(self):
        record_google_rain_fuel_value(self.operation, self.mission, "Google Neo", "Google Center")
        db.session.commit()
        self.assertEqual(google_rain_fuel_by_mission(self.operation)[self.mission.id]["neo_fuel"], "Google Neo")
        set_rain_fuel_data_source(self.gateway, "night", RAIN_FUEL_SOURCE_NEO)
        db.session.commit()
        self.assertEqual(rain_fuel_data_source(self.gateway), RAIN_FUEL_SOURCE_NEO)
        self.assertEqual(completed_scorpion_fuel_by_mission(self.operation), {})

    def test_only_completed_exact_mission_event_publishes_and_review_reopens_on_correction(self):
        assignment = NeoScorpionFuelAssignment(sort_date_operation_id=self.operation.id, sort_date_mission_id=self.mission.id)
        db.session.add(assignment); db.session.flush()
        work = NeoScorpionFuelWorkState(fuel_assignment_id=assignment.id, tail_number="N1")
        db.session.add(work); db.session.flush()
        event = NeoScorpionFuelingEvent(sort_date_operation_id=self.operation.id, fuel_assignment_id=assignment.id, fuel_work_state_id=work.id, tail_number="N1", fuel_truck_id=1, sequence_number=1, neo_fuel_lbs=12000, center_fuel_lbs=2100)
        db.session.add(event); db.session.commit()
        self.assertEqual(completed_scorpion_fuel_by_mission(self.operation), {})
        assignment.completed_at_utc = datetime.utcnow(); db.session.commit()
        values = completed_scorpion_fuel_by_mission(self.operation)
        self.assertEqual(values[self.mission.id]["neo_fuel"], "12,000")
        self.assertNotIn(self.other.id, values)
        self.assertFalse(fuel_review_pending_by_mission(self.operation, values)[self.mission.id])
        acknowledge_fuel_review(self.operation, self.mission.id, values[self.mission.id]["revision"], self.user); db.session.commit()
        event.neo_fuel_lbs = 12100; db.session.commit()
        corrected = completed_scorpion_fuel_by_mission(self.operation)
        self.assertTrue(fuel_review_pending_by_mission(self.operation, corrected)[self.mission.id])


if __name__ == "__main__":
    unittest.main()
