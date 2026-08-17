import unittest
from datetime import date

from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app import create_app
from app.extensions import db
from app.models import (
    NeoScorpionFuelTruck,
    NeoScorpionSortAssetState,
    NeoScorpionSortFueler,
    NeoScorpionSortTruck,
    SortDateOperation,
    User,
)
from app.services.schema_sync import sync_local_sqlite_schema


class NeoScorpionNightlyAssetSchemaTest(unittest.TestCase):
    def setUp(self):
        TestConfig = type(
            "TestConfig",
            (),
            {
                "SECRET_KEY": "neoscorpion-nightly-assets-test",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
                "AUTO_BOOTSTRAP_DATABASE": False,
            },
        )
        self.app = create_app(TestConfig)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()

        self.user = User(username="nightly_fueler", password_hash="test")
        self.truck = NeoScorpionFuelTruck(truck_number="T-1")
        db.session.add_all((self.user, self.truck))
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def _operation(self, day_offset=0):
        operation = SortDateOperation(
            sort_date=date(2026, 8, 17 + day_offset),
            gateway_code="RFD",
            sort_name="night",
        )
        db.session.add(operation)
        db.session.commit()
        return operation

    def _assert_rejected(self, model):
        db.session.add(model)
        with self.assertRaises(IntegrityError):
            db.session.commit()
        db.session.rollback()

    def test_schema_sync_creates_all_missing_nightly_asset_tables_idempotently(self):
        for model in (
            NeoScorpionSortTruck,
            NeoScorpionSortFueler,
            NeoScorpionSortAssetState,
        ):
            model.__table__.drop(bind=db.engine)

        sync_local_sqlite_schema(self.app)
        sync_local_sqlite_schema(self.app)

        table_names = set(inspect(db.engine).get_table_names())
        self.assertTrue(
            {
                "neoscorpion_sort_asset_states",
                "neoscorpion_sort_fuelers",
                "neoscorpion_sort_trucks",
            }.issubset(table_names)
        )

    def test_sort_asset_state_constraints_and_defaults(self):
        operations = [self._operation(index) for index in range(6)]
        states = [
            NeoScorpionSortAssetState(sort_date_operation_id=operations[0].id),
            NeoScorpionSortAssetState(
                sort_date_operation_id=operations[1].id,
                fuel_island_count=0,
            ),
            NeoScorpionSortAssetState(
                sort_date_operation_id=operations[2].id,
                fuel_island_count=4,
            ),
        ]
        db.session.add_all(states)
        db.session.commit()

        self.assertIsNone(states[0].fuel_island_count)
        self.assertEqual(states[0].revision, 0)
        self._assert_rejected(
            NeoScorpionSortAssetState(
                sort_date_operation_id=operations[0].id,
                fuel_island_count=1,
            )
        )
        self._assert_rejected(
            NeoScorpionSortAssetState(
                sort_date_operation_id=operations[3].id,
                fuel_island_count=-1,
            )
        )
        self._assert_rejected(
            NeoScorpionSortAssetState(
                sort_date_operation_id=operations[4].id,
                fuel_island_count=5,
            )
        )
        self._assert_rejected(
            NeoScorpionSortAssetState(
                sort_date_operation_id=operations[5].id,
                revision=-1,
            )
        )

    def test_sort_fueler_uniqueness_is_scoped_to_operation(self):
        operation_a = self._operation()
        operation_b = self._operation(1)
        db.session.add_all(
            (
                NeoScorpionSortFueler(
                    sort_date_operation_id=operation_a.id,
                    user_id=self.user.id,
                ),
                NeoScorpionSortFueler(
                    sort_date_operation_id=operation_b.id,
                    user_id=self.user.id,
                ),
            )
        )
        db.session.commit()

        self._assert_rejected(
            NeoScorpionSortFueler(
                sort_date_operation_id=operation_a.id,
                user_id=self.user.id,
            )
        )

    def test_sort_truck_constraints_and_operation_scoping(self):
        operations = [self._operation(index) for index in range(10)]
        db.session.add_all(
            (
                NeoScorpionSortTruck(
                    sort_date_operation_id=operations[0].id,
                    fuel_truck_id=self.truck.id,
                    status="available",
                    starting_gallons=1000,
                    current_gallons=900,
                ),
                NeoScorpionSortTruck(
                    sort_date_operation_id=operations[1].id,
                    fuel_truck_id=self.truck.id,
                    status="unavailable_oos",
                ),
                NeoScorpionSortTruck(
                    sort_date_operation_id=operations[2].id,
                    fuel_truck_id=self.truck.id,
                    status="topping_off",
                ),
            )
        )
        db.session.commit()

        rejected_rows = (
            NeoScorpionSortTruck(
                sort_date_operation_id=operations[0].id,
                fuel_truck_id=self.truck.id,
                status="available",
                starting_gallons=1000,
                current_gallons=1000,
            ),
            NeoScorpionSortTruck(
                sort_date_operation_id=operations[3].id,
                fuel_truck_id=self.truck.id,
                status="invalid",
            ),
            NeoScorpionSortTruck(
                sort_date_operation_id=operations[4].id,
                fuel_truck_id=self.truck.id,
                status="unavailable_oos",
                starting_gallons=-1,
            ),
            NeoScorpionSortTruck(
                sort_date_operation_id=operations[5].id,
                fuel_truck_id=self.truck.id,
                status="unavailable_oos",
                current_gallons=-1,
            ),
            NeoScorpionSortTruck(
                sort_date_operation_id=operations[6].id,
                fuel_truck_id=self.truck.id,
                status="available",
                current_gallons=1000,
            ),
            NeoScorpionSortTruck(
                sort_date_operation_id=operations[7].id,
                fuel_truck_id=self.truck.id,
                status="available",
                starting_gallons=1000,
            ),
        )
        for row in rejected_rows:
            self._assert_rejected(row)

    def test_new_operation_has_no_automatic_nightly_asset_carryover(self):
        operation_a = self._operation()
        operation_b = self._operation(1)
        db.session.add_all(
            (
                NeoScorpionSortAssetState(
                    sort_date_operation_id=operation_a.id,
                    fuel_island_count=2,
                ),
                NeoScorpionSortFueler(
                    sort_date_operation_id=operation_a.id,
                    user_id=self.user.id,
                ),
                NeoScorpionSortTruck(
                    sort_date_operation_id=operation_a.id,
                    fuel_truck_id=self.truck.id,
                    status="available",
                    starting_gallons=1000,
                    current_gallons=1000,
                ),
            )
        )
        db.session.commit()

        self.assertIsNone(
            NeoScorpionSortAssetState.query.filter_by(
                sort_date_operation_id=operation_b.id
            ).first()
        )
        self.assertEqual(
            NeoScorpionSortFueler.query.filter_by(
                sort_date_operation_id=operation_b.id
            ).count(),
            0,
        )
        self.assertEqual(
            NeoScorpionSortTruck.query.filter_by(
                sort_date_operation_id=operation_b.id
            ).count(),
            0,
        )


if __name__ == "__main__":
    unittest.main()
