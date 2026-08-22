from datetime import date, datetime, time
import unittest

from sqlalchemy import event

from app import create_app
from app.extensions import db
from app.models import (
    Gateway,
    GatewaySortMatrix,
    NeoScorpionFuelTruck,
    NeoScorpionSortTruck,
    SortDateMission,
    SortDateOperation,
    SortTimelineSettings,
    SortTimelineSortSetting,
)
from app.services.neoscorpion import (
    current_sort_operation,
    fuel_dispatch_context,
    fueler_context,
    hanzo_context,
    neoscorpion_live_revision,
    truck_manager_context,
)


class NeoScorpionCurrentOperationTest(unittest.TestCase):
    def setUp(self):
        config = type(
            "TestConfig",
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
        self.gateway = Gateway(code="RFD", name="RFD", is_active=True)
        self.friday = date(2026, 6, 19)
        settings = SortTimelineSettings(gateway=self.gateway, gateway_code="RFD")
        night = SortTimelineSortSetting(
            timeline_settings=settings,
            gateway=self.gateway,
            gateway_code="RFD",
            sort_name="night",
            planning_start_local=time(18, 0),
            sort_window_start_local=time(19, 0),
            sort_window_end_local=time(8, 0),
        )
        active_night = GatewaySortMatrix(
            gateway=self.gateway,
            gateway_code="RFD",
            day_of_week="friday",
            sort_name="night",
            is_active=True,
        )
        self.operation = SortDateOperation(
            gateway=self.gateway,
            gateway_code="RFD",
            sort_date=self.friday,
            sort_name="night",
        )
        self.mission = SortDateMission(
            sort_date=self.friday,
            gateway_code="RFD",
            sort_name="night",
            sort_date_operation=self.operation,
            mission_type="departure",
            mission_source="manual",
            flight_number="UPS100",
            origin="RFD",
            destination="SDF",
            timezone="America/Chicago",
            planned_source="manual",
            fuel_status="waiting",
            departure_status="loading",
        )
        self.truck = NeoScorpionFuelTruck(
            gateway=self.gateway,
            truck_number="422809",
            capacity_gallons=10000,
        )
        db.session.add_all([settings, night, active_night, self.operation, self.mission, self.truck])
        db.session.flush()
        db.session.add(
            NeoScorpionSortTruck(
                sort_date_operation_id=self.operation.id,
                fuel_truck_id=self.truck.id,
                status="available",
                starting_gallons=9000,
                current_gallons=9000,
            )
        )
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def _set_local_now(self, value):
        self.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = value

    def test_night_sort_remains_current_across_midnight_inside_lifecycle_window(self):
        self._set_local_now(datetime(2026, 6, 20, 1, 30))
        self.assertEqual(current_sort_operation(self.gateway).id, self.operation.id)

    def test_expired_previous_sort_is_not_current_and_current_contexts_are_empty(self):
        self._set_local_now(datetime(2026, 6, 20, 8, 0))
        self.assertIsNone(current_sort_operation(self.gateway))
        self.assertEqual(fuel_dispatch_context(self.gateway)["rows"], [])
        self.assertIsNone(fuel_dispatch_context(self.gateway)["operation"])
        self.assertEqual(fueler_context(self.gateway, None)["rows"], [])
        self.assertIsNone(hanzo_context(self.gateway)["operation"])
        self.assertEqual(hanzo_context(self.gateway)["rows"], [])
        self.assertEqual(truck_manager_context(self.gateway)["nightly_trucks_by_id"], {})
        self.assertEqual(
            neoscorpion_live_revision(self.gateway),
            {"current_operation": False, "operation_id": None, "revision": 0},
        )
        self.assertIsNotNone(db.session.get(SortDateOperation, self.operation.id))
        self.assertEqual(NeoScorpionSortTruck.query.count(), 1)

    def test_active_lifecycle_operation_resolves_read_only_with_bounded_queries(self):
        self._set_local_now(datetime(2026, 6, 19, 20, 0))
        statements = []

        def capture(_conn, _cursor, statement, _parameters, _context, _many):
            statements.append(statement)

        event.listen(db.engine, "before_cursor_execute", capture)
        try:
            self.assertEqual(current_sort_operation(self.gateway).id, self.operation.id)
        finally:
            event.remove(db.engine, "before_cursor_execute", capture)
        self.assertLessEqual(
            len([statement for statement in statements if statement.lstrip().upper().startswith("SELECT")]),
            6,
        )
        self.assertFalse(
            any(statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for statement in statements)
        )
