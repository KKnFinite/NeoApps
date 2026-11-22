"""Previously uncovered updates, exercised through the same services and forms as the app."""
from datetime import datetime
from decimal import Decimal

from app.extensions import db
from app.models import NeoSubZeroTruck
from app.models.account_preference import AccountPreference
from app.services import neostaffing
from app.services.live_collaboration import entity_version
from app.services.neorain_ground_time_settings import set_neorain_ground_time_threshold_minutes
from app.services.neosubzero_trucks import manage_truck
from app.services.sort_timeline import record_sort_timeline_api_attempt
from tests import test_accounts_workspace as accounts
from tests.test_fuel_driver_accounts import FuelDriverAccountsTest
from tests import test_gateway_shell as gateways
from tests import test_neostaffing_shift_flow as flow
from tests import test_neorain_load_planner_routes as planners
from tests import test_neoscorpion_cancel_unassign as cancel
from tests import test_neostaffing_daily_attendance as attendance
from tests.test_sektor_ermac_performance_postgres import postgres


class GatewayUpdates(gateways.GatewayShellTest):
    def setUp(self):
        postgres(self, gateways, super().setUp)

    def test_usage(self):
        instant = datetime(2026, 1, 20, 3)
        first = record_sort_timeline_api_attempt(self.gateway, instant, 2)
        db.session.commit()
        second = record_sort_timeline_api_attempt(self.gateway, instant, 3)
        db.session.commit()
        db.session.expire_all()
        self.assertEqual(first.id, second.id)
        self.assertEqual((second.attempted_call_count, second.units_consumed), (2, 5))

    def test_refresh_default(self):
        from app.services.live_screen_refresh import live_screen_refresh_value, save_live_screen_refresh_override
        key = 'neoscorpion.fuel_dispatch'
        for value, expected in [('15', 15), ('30', 30), ('default', None)]:
            self.assertTrue(save_live_screen_refresh_override(self.gateway, key, value).changed)
            db.session.commit()
            self.assertEqual(live_screen_refresh_value(self.gateway, key).override_seconds, expected)

    def test_aircraft_rate(self):
        from app.models import NeoScorpionAircraftFuelSetting
        from app.services import neoscorpion as fuel
        aircraft = fuel.NEOSCORPION_APU_AIRCRAFT_TYPES[0]
        field = fuel._apu_rate_field_name(aircraft)
        for value in ('0.70', '0.80', ''):
            self.assertTrue(fuel.save_aircraft_fuel_settings(self.gateway, self.user, {field: value}).changed)
            db.session.commit()
            row = NeoScorpionAircraftFuelSetting.query.filter_by(gateway_id=self.gateway.id, aircraft_type=aircraft).one_or_none()
            if value:
                self.assertEqual(row.apu_rate_thousand_lbs_per_hour, Decimal(value))
            else:
                self.assertIsNone(row)

    def test_ground_time(self):
        from app.services.neorain_rainrock import set_default_planned_fph
        row = set_neorain_ground_time_threshold_minutes(self.gateway, 120)
        db.session.commit()
        set_neorain_ground_time_threshold_minutes(self.gateway, 95)
        set_default_planned_fph(self.gateway, '76000')
        db.session.commit()
        db.session.expire_all()
        self.assertEqual((row.ground_time_threshold_minutes, row.default_planned_fph), (95, 76000))

    def test_fluid(self):
        from app.services.neosubzero_departure_deice import set_neosubzero_fluid_settings
        row = set_neosubzero_fluid_settings(self.gateway, 'A', 40, 'B')
        db.session.commit()
        set_neosubzero_fluid_settings(self.gateway, 'C', 50, 'D')
        db.session.commit()
        db.session.expire_all()
        self.assertEqual((row.type_i_fluid_name, row.type_i_concentration_percent,
                          row.type_iv_fluid_name), ('C', 50, 'D'))

    def test_truck(self):
        row = manage_truck(self.gateway, 'add_truck', {'truck_number': 'T01'}, actor_id=self.user.id)
        db.session.commit()
        for action, changes in [('edit_truck', {'truck_number': 'T02'}), ('deactivate_truck', {}),
                                ('activate_truck', {}), ('delete_truck', {})]:
            manage_truck(self.gateway, action, dict(truck_id=row.id, confirm='yes',
                         expected_version=entity_version(row), **changes), actor_id=self.user.id)
            db.session.commit()
            db.session.expire_all()
            self.assertEqual(row.history[-1]['action'], action)
            self.assertEqual(row.active, action in ('edit_truck', 'activate_truck'))
        self.assertIsNotNone(db.session.get(NeoSubZeroTruck, row.id).archived_at_utc)


class HistoricalOperations(attendance.NeoStaffingDailyAttendanceTest):
    def setUp(self):
        postgres(self, attendance, super().setUp)

    def test_purge(self):
        from tests.test_neostaffing_history_retention import test_purge_cutoff_keeps_the_boundary_night_and_withdraws_partial_roster
        test_purge_cutoff_keeps_the_boundary_night_and_withdraws_partial_roster(self)

    def test_corrections(self):
        from tests.test_neostaffing_historical_records import test_canonical_marks_correction_rollback_transfer_and_retention
        test_canonical_marks_correction_rollback_transfer_and_retention(self)


class FuelRollback(cancel.CancelUnassignTest):
    def setUp(self):
        postgres(self, cancel.fixture, super().setUp)
        # Historic completion evidence can predate a separate tail-fuel row.
        _, _, self.saved_assignment, _, _, _ = self._completed()
        db.session.delete(cancel.NeoScorpionTailFuelState.query.one())
        db.session.commit()

    def test_cancel_new_tail(self):
        self.assertEqual(cancel.NeoScorpionTailFuelState.query.count(), 0)
        events = [e.id for e in cancel.NeoScorpionFuelingEvent.query.all()]
        self.assertTrue(events)
        assignment = self.saved_assignment
        cancel.service.start_follow_up_fuel_cycle(
            self.gateway, self.dispatcher, assignment.id, 'uplift', '55.0', '', '',
            expected_cycle=assignment.current_cycle_number, expected_tail=assignment.confirmed_tail_number,
            now_utc=datetime(2026, 8, 18, 6),
        )
        db.session.commit()
        self.assertEqual(cancel.NeoScorpionTailFuelState.query.count(), 1)
        self.assertTrue(self._cancel(self.saved_assignment).changed)
        db.session.commit()
        self.assertEqual(cancel.NeoScorpionTailFuelState.query.count(), 0)
        self.assertEqual([e.id for e in cancel.NeoScorpionFuelingEvent.query.all()], events)


class StaffingUpdates(flow.ShiftFlowTest):
    def setUp(self):
        postgres(self, flow, super().setUp)

    def test_unit(self):
        unit = neostaffing.update_unit(self.other, dict(name='Spare', parent_id=self.shift.id,
            display_order='7', required_headcount='3', active='1'))
        db.session.commit()
        db.session.expire_all()
        self.assertEqual((unit.name, unit.display_order, unit.required_headcount), ('Spare', 7, 3))
        with self.assertRaises(ValueError):
            neostaffing.update_unit(unit, dict(name='Spare', unit_type='sort'))


class AccountUpdates(accounts.AccountsWorkspaceTest):
    def setUp(self):
        postgres(self, accounts, super().setUp)

    def test_edit_preferences(self):
        self.login(self.target)
        def save(**values):
            token = accounts.Inputs(self.client.get('/my-account').data).values['review_token']
            return self.client.post('/my-account/preferences', data=dict(review_token=token, **values))
        self.assertEqual(save(show_cryocheck='1').status_code, 302)
        row = db.session.get(AccountPreference, self.target.id)
        version = row.version
        self.assertEqual(save().status_code, 302)
        db.session.expire_all()
        self.assertFalse(row.show_cryocheck)
        self.assertEqual(row.version, version + 1)
        self.assertEqual(save(preferred_app='neobid').status_code, 400)
        db.session.expire_all()
        self.assertEqual(row.version, version + 1)


class RetiredCompanies(FuelDriverAccountsTest):
    def setUp(self):
        postgres(self, accounts, super().setUp)
        self.historical = super().legacy_supervisor()

    def legacy_supervisor(self):
        return self.historical


class CrewUpdates(planners.NeoRainLoadPlannerRoutesTest):
    def setUp(self):
        postgres(self, planners, super().setUp)

    def test_crew_crud(self):
        from app.models import StaffingUnit
        from app.services import neorain_crew_admin as crew
        area = StaffingUnit(unit_type='work_area', name='Crew Admin', parent=self.load_planners.parent, active=True)
        db.session.add(area)
        db.session.flush()
        first = self._person('CREW-1', area)
        second = self._person('CREW-2', area)
        db.session.commit()
        record = crew.add_neorain_crew_admin_assignment(self.operation, first, ['Alpha'], 'p1', 'v1')
        db.session.commit()
        crew.update_neorain_crew_admin_assignment(record, second, ['Bravo'], 'p2', 'v2')
        db.session.commit()
        db.session.expire_all()
        self.assertEqual((record.person_id, record.printer_number, record.van_number), (second.id, 'p2', 'v2'))
        crew.remove_neorain_crew_admin_assignment(record)
        db.session.commit()
        self.assertFalse(crew.neorain_crew_admin_assignments(self.operation))
