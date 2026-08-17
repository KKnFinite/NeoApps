import unittest
from datetime import date

from app import create_app
from app.extensions import db
from app.models import (
    Gateway,
    NeoScorpionFuelTruck,
    NeoScorpionSortAssetState,
    NeoScorpionSortFueler,
    NeoScorpionSortTruck,
    SortDateOperation,
    User,
)
from app.services.neoscorpion_assets import (
    complete_nightly_truck_top_off,
    mark_nightly_truck_topping_off,
    remove_nightly_fueler,
    remove_nightly_truck,
    select_nightly_fueler,
    select_nightly_truck,
    set_nightly_fuel_island_count,
    update_nightly_truck,
)


class NeoScorpionNightlyAssetServicesTest(unittest.TestCase):
    def setUp(self):
        TestConfig = type(
            "TestConfig",
            (),
            {
                "SECRET_KEY": "neoscorpion-nightly-service-test",
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

        self.gateway = Gateway(code="RFD", name="Rockford")
        self.other_gateway = Gateway(code="SDF", name="Louisville")
        self.user = User(username="night_fueler", password_hash="test")
        db.session.add_all((self.gateway, self.other_gateway, self.user))
        db.session.flush()
        self.operation_a = self._operation(self.gateway, date(2026, 8, 17))
        self.operation_b = self._operation(self.gateway, date(2026, 8, 18))
        self.other_operation = self._operation(
            self.other_gateway,
            date(2026, 8, 17),
        )
        self.truck = NeoScorpionFuelTruck(
            gateway_id=self.gateway.id,
            truck_number="T-1",
            capacity_gallons=2000,
            remaining_fuel_gallons=1500,
            is_out_of_service=False,
        )
        self.other_truck = NeoScorpionFuelTruck(
            gateway_id=self.other_gateway.id,
            truck_number="T-2",
            capacity_gallons=1800,
        )
        db.session.add_all((self.truck, self.other_truck))
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def _operation(self, gateway, sort_date):
        operation = SortDateOperation(
            gateway_id=gateway.id,
            gateway_code=gateway.code,
            sort_date=sort_date,
            sort_name="night",
        )
        db.session.add(operation)
        return operation

    def _commit(self, result):
        if result.changed:
            db.session.commit()
        return result

    def _state(self, operation):
        return NeoScorpionSortAssetState.query.filter_by(
            sort_date_operation_id=operation.id
        ).one_or_none()

    def test_island_count_revision_and_no_op_semantics(self):
        result = set_nightly_fuel_island_count(self.operation_a, None)
        self.assertFalse(result.changed)
        self.assertEqual(result.revision, 0)
        self.assertIsNone(self._state(self.operation_a))

        result = self._commit(set_nightly_fuel_island_count(self.operation_a, 2))
        self.assertTrue(result.changed)
        self.assertEqual(result.revision, 1)
        self.assertEqual(self._state(self.operation_a).fuel_island_count, 2)

        result = set_nightly_fuel_island_count(self.operation_a, "2")
        self.assertFalse(result.changed)
        self.assertEqual(result.revision, 1)

        result = self._commit(set_nightly_fuel_island_count(self.operation_a, 4))
        self.assertEqual(result.revision, 2)
        result = self._commit(set_nightly_fuel_island_count(self.operation_a, None))
        self.assertEqual(result.revision, 3)
        self.assertIsNone(self._state(self.operation_a).fuel_island_count)

        with self.assertRaisesRegex(ValueError, "between 0 and 4"):
            set_nightly_fuel_island_count(self.operation_a, 5)
        self.assertEqual(self._state(self.operation_a).revision, 3)

    def test_fueler_mutations_are_idempotent_and_operation_scoped(self):
        added = self._commit(select_nightly_fueler(self.operation_a, self.user))
        self.assertEqual(added.revision, 1)
        duplicate = select_nightly_fueler(self.operation_a, self.user)
        self.assertFalse(duplicate.changed)
        self.assertEqual(duplicate.revision, 1)
        self.assertEqual(
            NeoScorpionSortFueler.query.filter_by(
                sort_date_operation_id=self.operation_a.id,
                user_id=self.user.id,
            ).count(),
            1,
        )

        operation_b_add = self._commit(
            select_nightly_fueler(self.operation_b, self.user)
        )
        self.assertEqual(operation_b_add.revision, 1)
        removed = self._commit(remove_nightly_fueler(self.operation_a, self.user))
        self.assertEqual(removed.revision, 2)
        missing = remove_nightly_fueler(self.operation_a, self.user)
        self.assertFalse(missing.changed)
        self.assertEqual(missing.revision, 2)
        self.assertEqual(
            NeoScorpionSortFueler.query.filter_by(
                sort_date_operation_id=self.operation_b.id,
                user_id=self.user.id,
            ).count(),
            1,
        )

    def test_truck_selection_validation_and_atomicity(self):
        selected = self._commit(
            select_nightly_truck(
                self.operation_a,
                self.truck,
                status="available",
                starting_gallons="1800",
                current_gallons=1700,
            )
        )
        self.assertEqual(selected.revision, 1)
        duplicate = select_nightly_truck(
            self.operation_a,
            self.truck,
            status="available",
            starting_gallons=1800,
            current_gallons=1700,
        )
        self.assertFalse(duplicate.changed)
        self.assertEqual(duplicate.revision, 1)

        invalid_operation = self.other_operation
        invalid_calls = (
            lambda: select_nightly_truck(
                invalid_operation,
                self.other_truck,
                status="available",
                starting_gallons=None,
                current_gallons=100,
            ),
            lambda: select_nightly_truck(
                invalid_operation,
                self.other_truck,
                status="unavailable_oos",
                starting_gallons=-1,
            ),
            lambda: select_nightly_truck(
                invalid_operation,
                self.other_truck,
                status="available",
                starting_gallons=1700,
                current_gallons=1900,
            ),
            lambda: select_nightly_truck(
                invalid_operation,
                self.truck,
                status="unavailable_oos",
            ),
        )
        for call in invalid_calls:
            with self.assertRaises(ValueError):
                call()
            self.assertIsNone(self._state(invalid_operation))
            self.assertEqual(
                NeoScorpionSortTruck.query.filter_by(
                    sort_date_operation_id=invalid_operation.id
                ).count(),
                0,
            )

        oos = self._commit(
            select_nightly_truck(
                self.operation_b,
                self.truck,
                status="unavailable_oos",
            )
        )
        self.assertEqual(oos.revision, 1)

    def test_truck_update_and_remove_increment_once(self):
        self._commit(
            select_nightly_truck(
                self.operation_a,
                self.truck,
                status="available",
                starting_gallons=1800,
                current_gallons=1700,
            )
        )
        updated = self._commit(
            update_nightly_truck(
                self.operation_a,
                self.truck,
                current_gallons=1600,
            )
        )
        self.assertEqual(updated.revision, 2)
        self.assertEqual(
            NeoScorpionSortTruck.query.filter_by(
                sort_date_operation_id=self.operation_a.id,
                fuel_truck_id=self.truck.id,
            ).one().current_gallons,
            1600,
        )

        unchanged = update_nightly_truck(
            self.operation_a,
            self.truck,
            status="available",
            starting_gallons=1800,
            current_gallons=1600,
        )
        self.assertFalse(unchanged.changed)
        self.assertEqual(unchanged.revision, 2)
        removed = self._commit(remove_nightly_truck(self.operation_a, self.truck))
        self.assertEqual(removed.revision, 3)
        missing = remove_nightly_truck(self.operation_a, self.truck)
        self.assertFalse(missing.changed)
        self.assertEqual(missing.revision, 3)

    def test_top_off_requires_dedicated_valid_completion(self):
        legacy_remaining = self.truck.remaining_fuel_gallons
        legacy_oos = self.truck.is_out_of_service
        self._commit(
            select_nightly_truck(
                self.operation_a,
                self.truck,
                status="available",
                starting_gallons=1800,
                current_gallons=1200,
            )
        )
        topping = self._commit(
            mark_nightly_truck_topping_off(self.operation_a, self.truck)
        )
        self.assertEqual(topping.revision, 2)
        duplicate = mark_nightly_truck_topping_off(self.operation_a, self.truck)
        self.assertFalse(duplicate.changed)
        self.assertEqual(duplicate.revision, 2)

        with self.assertRaisesRegex(ValueError, "Top Off Complete"):
            update_nightly_truck(
                self.operation_a,
                self.truck,
                status="available",
                current_gallons=1500,
            )
        with self.assertRaisesRegex(ValueError, "Enter current gallons"):
            complete_nightly_truck_top_off(self.operation_a, self.truck, None)
        with self.assertRaisesRegex(ValueError, "capacity"):
            complete_nightly_truck_top_off(self.operation_a, self.truck, 2100)
        self.assertEqual(self._state(self.operation_a).revision, 2)

        completed = self._commit(
            complete_nightly_truck_top_off(
                self.operation_a,
                self.truck,
                1900,
            )
        )
        self.assertEqual(completed.revision, 3)
        nightly_truck = NeoScorpionSortTruck.query.filter_by(
            sort_date_operation_id=self.operation_a.id,
            fuel_truck_id=self.truck.id,
        ).one()
        self.assertEqual(nightly_truck.status, "available")
        self.assertEqual(nightly_truck.starting_gallons, 1800)
        self.assertEqual(nightly_truck.current_gallons, 1900)
        self.assertEqual(self.truck.remaining_fuel_gallons, legacy_remaining)
        self.assertEqual(self.truck.is_out_of_service, legacy_oos)

        with self.assertRaisesRegex(ValueError, "not currently topping off"):
            complete_nightly_truck_top_off(
                self.operation_a,
                self.truck,
                1800,
            )
        self.assertEqual(self._state(self.operation_a).revision, 3)

    def test_operation_isolation_and_one_revision_per_logical_mutation(self):
        fueler_result = self._commit(
            select_nightly_fueler(self.operation_a, self.user)
        )
        self.assertEqual(fueler_result.revision, 1)
        self.assertIsNone(self._state(self.operation_b))

        truck_result = self._commit(
            select_nightly_truck(
                self.operation_a,
                self.truck,
                status="unavailable_oos",
            )
        )
        self.assertEqual(truck_result.revision, 2)
        self.assertEqual(self._state(self.operation_a).revision, 2)
        self.assertIsNone(self._state(self.operation_b))
        self.assertEqual(
            NeoScorpionSortTruck.query.filter_by(
                sort_date_operation_id=self.operation_b.id
            ).count(),
            0,
        )


if __name__ == "__main__":
    unittest.main()
