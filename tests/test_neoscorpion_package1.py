"""Package 1 safeguards: no production data or historical rewrites."""
import unittest
from unittest.mock import patch

from app.extensions import db
from app.models import NeoScorpionSettings, NeoScorpionSortTruck, NeoScorpionTopOffEvent
from app.services import neoscorpion as fuel
from app.services import neoscorpion_assets as assets
from tests import test_neoscorpion_dispatch_planning as fixtures


class OperationalSafeguardsTest(unittest.TestCase):
    setUp = fixtures.NeoScorpionDispatchPlanningTest.setUp
    tearDown = fixtures.NeoScorpionDispatchPlanningTest.tearDown
    _login_user = fixtures.NeoScorpionDispatchPlanningTest._login_user

    def settings_form(self, density):
        return dict(fuel_density_lbs_per_gallon=density,
                    red_assignment_alert_threshold_minutes='30')

    def test_nonfinite_density_is_validation_error_not_persisted(self):
        for value in ('NaN', 'sNaN', 'Infinity', '-Infinity', 'inf', '-inf', 'abc', '6_7', '0', '-1', '1e9999', '1e-9999'):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    fuel.save_settings(self.gateway, self.settings_form(value))
                db.session.rollback()
        self.assertEqual(NeoScorpionSettings.query.count(), 0)

    def test_density_endpoint_rejects_bad_values_with_400(self):
        self._login_user('density-master', 'master')
        for value in ('NaN', 'Infinity', '-Infinity', 'bad', '0'):
            with self.subTest(value=value):
                response = self.client.post('/neoscorpion/settings', data=self.settings_form(value))
                self.assertEqual(response.status_code, 400)
                self.assertIn('Fuel density', response.get_data(as_text=True))

    def test_valid_and_missing_density_contract_and_calculations_unchanged(self):
        for value in ('6.7', '6.0', '7', '0.1'):
            fuel.save_settings(self.gateway, self.settings_form(value)); db.session.commit()
            self.assertEqual(NeoScorpionSettings.query.one().fuel_density_lbs_per_gallon, float(value))
        self.assertEqual(fuel.gallons_to_lbs(1000, '6.7'), 6700)
        self.assertEqual(fuel.lbs_to_gallons(6700, '6.7'), 1000)
        for value in ('', None):
            fuel.save_settings(self.gateway, self.settings_form(value)); db.session.commit()
            self.assertIsNone(NeoScorpionSettings.query.one().fuel_density_lbs_per_gallon)

    def test_calculation_rejects_nonfinite_density_including_legacy_values(self):
        for fn in (fuel.gallons_to_lbs, fuel.lbs_to_gallons):
            for value in ('NaN', 'Infinity', '-Infinity'):
                with self.subTest(fn=fn.__name__, value=value), self.assertRaises(ValueError):
                    fn(1000, value)

    def truck(self):
        truck = fixtures.NeoScorpionDispatchPlanningTest._nightly_truck(self)
        truck.capacity_gallons = 10000
        self.selection = NeoScorpionSortTruck.query.filter_by(fuel_truck_id=truck.id).one()
        self.selection.starting_gallons = self.selection.current_gallons = 8000
        db.session.commit()
        return truck

    def capacity(self, truck, value, **extra):
        with patch.object(fuel, 'current_sort_operation', return_value=self.operation):
            return fuel.save_truck(self.gateway, dict(truck_id=truck.id, truck_number=truck.truck_number,
                capacity_gallons=str(value), is_active='1', **extra))

    def test_capacity_below_nightly_snapshot_rejected_and_top_off_still_returns(self):
        truck = self.truck()
        with self.assertRaisesRegex(ValueError, 'capacity|Capacity'):
            self.capacity(truck, 1000)
        db.session.rollback()
        assets.mark_nightly_truck_topping_off(self.operation, truck); db.session.commit()
        assets.complete_nightly_truck_top_off(self.operation, truck, 9000); db.session.commit()
        self.assertEqual(self.selection.status, 'available')
        self.assertEqual(self.selection.starting_gallons, 8000)
        self.assertEqual(self.selection.current_gallons, 9000)
        self.assertEqual(NeoScorpionTopOffEvent.query.count(), 2)

    def test_capacity_reduction_during_top_off_rejected_even_above_old_inventory(self):
        truck = self.truck()
        assets.mark_nightly_truck_topping_off(self.operation, truck); db.session.commit()
        with self.assertRaisesRegex(ValueError, 'TOP OFF'):
            self.capacity(truck, 9000)
        db.session.rollback()
        assets.complete_nightly_truck_top_off(self.operation, truck, 10000); db.session.commit()
        self.assertEqual(self.selection.current_gallons, 10000)

    def test_compatible_capacity_reduction_and_increase_allowed_stale_edit_rejected(self):
        truck = self.truck()
        self.capacity(truck, 9000, expected_capacity_gallons='10000'); db.session.commit()
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.capacity(truck, 9500, expected_capacity_gallons='10000')
        db.session.rollback()
        self.capacity(truck, 12000, expected_capacity_gallons='9000'); db.session.commit()
        self.assertEqual(truck.capacity_gallons, 12000)

    def test_operator_cannot_change_density_or_capacity(self):
        self._login_user('safe-operator', 'operator')
        truck = self.truck()
        self.assertEqual(self.client.post('/neoscorpion/settings', data=self.settings_form('Infinity')).status_code, 403)
        self.assertEqual(self.client.post('/neoscorpion/truck-manager', data=dict(action='save_truck',
            truck_id=truck.id, truck_number=truck.truck_number, capacity_gallons='1000')).status_code, 403)
        self.assertEqual(truck.capacity_gallons, 10000)

