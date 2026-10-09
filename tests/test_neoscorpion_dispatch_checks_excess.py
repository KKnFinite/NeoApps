"""Personal Dispatch checks and presentation-only, density-aware excess alerts."""
from datetime import timedelta
import re
import unittest

from sqlalchemy import Column, MetaData, Table, inspect, text
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (Gateway, NeoScorpionDispatcherCheck, NeoScorpionFuelAuditEntry,
    NeoScorpionFuelCycleHistory, NeoScorpionFuelingEvent, NeoScorpionFuelTankState,
    NeoScorpionFuelWorkState, NeoScorpionSettings, NeoScorpionSortAssetState,
    NeoScorpionSpearAuditEntry, SortDateMission, SortDateOperation)
from app.services.neoscorpion import fuel_dispatch_context, neo_fuel_excess_alert, settings_context
from app.services.neoscorpion_schema import NEOSCORPION_ADDITIVE_COLUMNS
from app.services.schema_sync import LOCAL_SQLITE_OPTIONAL_COLUMNS, POSTGRES_OPTIONAL_COLUMNS, sync_local_sqlite_schema
from tests import test_neoscorpion_dispatch_planning as dispatch_fixture


class NeoFuelExcessClassificationTest(unittest.TestCase):
    def test_inclusive_threshold_uses_raw_pounds_and_gateway_density(self):
        for neo, required, density, threshold, expected in (
            (28_749, 25_400, 6.7, 500, False), (28_750, 25_400, 6.7, 500, True),
            (28_751, 25_400, 6.7, 500, True), (28_750, 25_400, 7, 500, False),
            (28_900, 25_400, 7, 500, True), (28_750, 25_400, 6.7, 501, False),
            (25_400, 25_400, 6.7, 0, False), (25_401, 25_400, 6.7, 0, True),
            (3_350, 0, 6.7, 500, True), (25_000, 25_400, 6.7, 0, False),
        ):
            with self.subTest(neo=neo, required=required, density=density, threshold=threshold):
                self.assertEqual(neo_fuel_excess_alert(neo, required, density, threshold), expected)

    def test_missing_invalid_and_nonfinite_inputs_never_alert(self):
        for values in ((None, 1000, 6.7), (10_000, None, 6.7), (10_000, 1000, None),
                       (10_000, 1000, 0), (10_000, 1000, -6.7), (-100, 0, 6.7),
                       (10_000, -1, 6.7), (10_000, 1000, "NaN"),
                       ("Infinity", 1000, 6.7), (10_000, 1000, "bad")):
            with self.subTest(values=values):
                self.assertFalse(neo_fuel_excess_alert(*values))


class DispatchChecksExcessIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.fixture = dispatch_fixture.NeoScorpionDispatchPlanningTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.gateway, self.operation, self.client = self.fixture.gateway, self.fixture.operation, self.fixture.client
        self.mission = self.fixture._mission("UPS901", "N491UP", 25_400, 1)
        self.assignment = self.fixture._assignment(self.mission)
        self.work = NeoScorpionFuelWorkState(fuel_assignment_id=self.assignment.id,
            tail_number=self.mission.assigned_tail_number, apu_running=False, apu_allowance_lbs=0)
        db.session.add(self.work)
        db.session.flush()
        db.session.add_all([NeoScorpionFuelTankState(fuel_work_state_id=self.work.id,
            tank_code=code, actual_lbs=lbs) for code, lbs in (("left", 15_000), ("ctr", 0), ("right", 13_750))])
        db.session.add(NeoScorpionSortAssetState(sort_date_operation_id=self.operation.id, revision=5))
        db.session.commit()
        self.user = self.fixture._login_user("personal_dispatcher", "master")

    def _check(self, checked="1", expected="0", **overrides):
        data = {"mission_id": str(self.mission.id), "operation_id": str(self.operation.id),
                "checked": checked, "expected_checked": expected, **overrides}
        return self.client.post("/neoscorpion/fuel-dispatch/check", data=data)

    def _settings(self, threshold, **overrides):
        return self.client.post("/neoscorpion/settings", data={"action": "save_settings",
            "red_assignment_alert_threshold_minutes": "30", "fuel_density_lbs_per_gallon": "6.7",
            "planning_inbound_fuel_fallback": "12.0", "neo_fuel_excess_alert_gallons": threshold, **overrides})

    def test_checkbox_persists_through_get_live_refresh_and_reload_without_operational_writes(self):
        before = fuel_dispatch_context(self.gateway)
        original_assignment = {col.name: getattr(self.assignment, col.name) for col in self.assignment.__table__.columns}
        response = self._check(user_id="999999")  # Ownership always comes from the signed-in user.
        self.assertEqual(response.status_code, 200)
        saved = NeoScorpionDispatcherCheck.query.one()
        self.assertEqual(saved.user_id, self.user.id)
        self.assertTrue(saved.checked)
        self.assertEqual({col.name: getattr(self.assignment, col.name) for col in self.assignment.__table__.columns},
                         original_assignment)
        for path in ("/neoscorpion/fuel-dispatch", "/neoscorpion/fuel-dispatch/live-panel"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            html = response.get_json()["html"] if response.is_json else response.get_data(as_text=True)
            self.assertRegex(html, r'data-dispatch-check[^>]+data-saved-checked="1"\s+checked')
        after = fuel_dispatch_context(self.gateway)
        self.assertEqual(before["sort_fuel_totals"], after["sort_fuel_totals"])
        self.assertEqual(before["spear_plan"].token, after["spear_plan"].token)
        self.assertEqual(NeoScorpionSortAssetState.query.one().revision, 5)
        for model in (NeoScorpionFuelAuditEntry, NeoScorpionFuelingEvent, NeoScorpionSpearAuditEntry, NeoScorpionFuelCycleHistory):
            self.assertEqual(model.query.count(), 0)
        self.assertEqual(self._check("0", "1").status_code, 200)
        self.assertFalse(NeoScorpionDispatcherCheck.query.one().checked)

    def test_checkbox_isolation_by_dispatcher_mission_and_sort_identity(self):
        self.assertEqual(self._check().status_code, 200)
        second = self.fixture._mission("UPS901", "N492UP", 25_400, 2)
        db.session.commit()
        self.assertEqual(self._check(mission_id=str(second.id)).status_code, 200)
        other = self.fixture._login_user("other_dispatcher", "simulator")
        live = self.client.get("/neoscorpion/fuel-dispatch/live-panel").get_json()["html"]
        self.assertNotIn('data-saved-checked="1"', live)
        self.assertEqual(self._check().status_code, 200)
        self.assertEqual({row.user_id for row in NeoScorpionDispatcherCheck.query.all()}, {self.user.id, other.id})
        prior = SortDateOperation(generated_by_user_id=self.operation.generated_by_user_id,
            gateway_id=self.gateway.id, gateway_code=self.gateway.code,
            sort_date=self.operation.sort_date - timedelta(days=1), sort_name="night", window_minutes=60)
        db.session.add(prior)
        db.session.flush()
        old_mission = SortDateMission(sort_date_operation_id=prior.id, sort_date=prior.sort_date,
            gateway_code=self.gateway.code, sort_name="night", mission_type="departure", flight_number="UPS901",
            origin=self.gateway.code, destination="SDF")
        db.session.add(old_mission)
        db.session.commit()
        self.assertEqual(self._check(operation_id=str(prior.id), mission_id=str(old_mission.id)).status_code, 409)
        self.assertEqual(self._check(mission_id=str(old_mission.id)).status_code, 400)
        self.assertEqual(NeoScorpionDispatcherCheck.query.count(), 3)

    def test_checkbox_permissions_invalid_scope_and_duplicate_constraints(self):
        for data in ({"checked": "yes"}, {"expected_checked": "bad"}, {"mission_id": "invalid"},
                     {"mission_id": "999999999999999999999"}):
            self.assertEqual(self._check(**data).status_code, 400)
        self.mission.departure_status = "cancelled"
        db.session.commit()
        self.assertEqual(self._check().status_code, 400)
        self.mission.departure_status = "loading"
        db.session.commit()
        self.fixture._login_user("view_only_dispatcher", "operator")
        self.assertEqual(self._check().status_code, 403)
        self.assertEqual(NeoScorpionDispatcherCheck.query.count(), 0)
        self.fixture._login_user("checked_dispatcher", "simulator")
        self.assertEqual(self._check().status_code, 200)
        record = NeoScorpionDispatcherCheck.query.one()
        db.session.add(NeoScorpionDispatcherCheck(user_id=record.user_id,
            sort_date_operation_id=record.sort_date_operation_id, sort_date_mission_id=record.sort_date_mission_id))
        with self.assertRaises(IntegrityError):
            db.session.flush()
        db.session.rollback()

    def test_settings_default_validation_permissions_gateway_scope_and_live_revision(self):
        self.assertEqual(settings_context(self.gateway)["settings"]["neo_fuel_excess_alert_gallons"], 500)
        self.assertEqual(self._settings("700").status_code, 302)
        self.assertEqual(NeoScorpionSettings.query.one().neo_fuel_excess_alert_gallons, 700)
        self.assertEqual(NeoScorpionSortAssetState.query.one().revision, 6)
        self.assertFalse(fuel_dispatch_context(self.gateway)["rows"][0]["neo_fuel_excess_alert"])
        for threshold in ("", "-1", "1.1", "1e3", "NaN", "2147483648", "999999999999999"):
            with self.subTest(threshold=threshold):
                self.assertEqual(self._settings(threshold).status_code, 400)
                self.assertEqual(NeoScorpionSettings.query.one().neo_fuel_excess_alert_gallons, 700)
        self.assertEqual(self._settings("0").status_code, 302)
        self.assertTrue(fuel_dispatch_context(self.gateway)["rows"][0]["neo_fuel_excess_alert"])
        other = Gateway(code="AAA", name="Other Gateway", is_active=True)
        db.session.add(other)
        db.session.flush()
        db.session.add(NeoScorpionSettings(gateway_id=other.id, neo_fuel_excess_alert_gallons=123))
        db.session.commit()
        self.fixture._login_user("settings_viewer", "simulator")
        response = self.client.get("/neoscorpion/settings")
        self.assertRegex(response.get_data(as_text=True), r'name="neo_fuel_excess_alert_gallons"[^>]+readonly')
        self.assertEqual(self._settings("900").status_code, 403)
        self.assertEqual(NeoScorpionSettings.query.filter_by(gateway_id=other.id).one().neo_fuel_excess_alert_gallons, 123)

    def test_required_and_apu_changes_reconcile_only_that_employee_fuel_value(self):
        row = fuel_dispatch_context(self.gateway)["rows"][0]
        self.assertEqual(row["neo_fuel_lbs"], 28_750)
        self.assertTrue(row["neo_fuel_excess_alert"])
        response = self.client.post("/neoscorpion/fuel-dispatch/autosave", data={
            "mission_id": self.mission.id, "field_name": "required_fuel", "value": "25.5", "expected_value": "25.4"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()["neo_fuel"]["excess"])
        self.assertEqual(response.get_json()["neo_fuel"]["display"], "28.8")
        self.work.apu_running = True
        self.work.apu_allowance_lbs = 1000
        self.work.automatic_apu_allowance_lbs = 1000
        self.work.apu_source_tank_code = "left"
        db.session.commit()
        row = fuel_dispatch_context(self.gateway)["rows"][0]
        self.assertEqual(row["neo_fuel_lbs"], 27_750)
        self.assertFalse(row["neo_fuel_excess_alert"])
        # Density and threshold changes advance live refresh, but calculations stay identical.
        before = NeoScorpionSortAssetState.query.one().revision
        self.assertEqual(self._settings("0", fuel_density_lbs_per_gallon="7.0").status_code, 302)
        self.assertGreater(NeoScorpionSortAssetState.query.one().revision, before)
        self.assertEqual(fuel_dispatch_context(self.gateway)["rows"][0]["neo_fuel_lbs"], 27_750)

    def test_history_has_no_checkbox_or_alert_and_all_rows_have_sixteen_columns(self):
        self.assignment.current_cycle_number = 2
        db.session.add(NeoScorpionFuelCycleHistory(sort_date_operation_id=self.operation.id,
            fuel_assignment_id=self.assignment.id, mission_id=self.mission.id, cycle_number=1,
            label="COMPLETED", snapshot={"neo_fuel_display": "28.8", "neo_fuel_excess_alert": True}))
        db.session.commit()
        html = self.client.get("/neoscorpion/fuel-dispatch/live-panel").get_json()["html"]
        history = re.search(r'<tr class="neoscorpion-dispatch-history-row"[\s\S]*?</tr>', html)[0]
        current = re.search(r'<tr class="neoscorpion-dispatch-primary-row[^>]+>[\s\S]*?</tr>', html)[0]
        self.assertNotIn("data-dispatch-check ", history)
        self.assertNotIn("is-neo-fuel-excess", history)
        self.assertEqual(history.count("<td"), 16)
        self.assertEqual(current.count("<td"), 16)
        self.assertEqual(html.count('colspan="16"'), 2)

    def test_additive_schema_repairs_existing_settings_and_creates_check_table(self):
        NeoScorpionSettings.__table__.drop(db.engine)
        old = Table("neoscorpion_settings", MetaData(), *(Column(column.name, column.type,
            primary_key=column.primary_key) for column in NeoScorpionSettings.__table__.columns
            if column.name != "neo_fuel_excess_alert_gallons"))
        old.create(db.engine)
        db.session.execute(text("INSERT INTO neoscorpion_settings (id,gateway_id,fuel_density_lbs_per_gallon) VALUES (1,:gateway,6.7)"),
                           {"gateway": self.gateway.id})
        db.session.commit()
        NeoScorpionDispatcherCheck.__table__.drop(db.engine)
        sync_local_sqlite_schema(self.fixture.app)
        sync_local_sqlite_schema(self.fixture.app)
        db.session.commit()
        self.assertIn("neoscorpion_dispatcher_checks", inspect(db.engine).get_table_names())
        self.assertEqual(NeoScorpionSettings.query.one().neo_fuel_excess_alert_gallons, 500)
        for columns in (LOCAL_SQLITE_OPTIONAL_COLUMNS, POSTGRES_OPTIONAL_COLUMNS, NEOSCORPION_ADDITIVE_COLUMNS):
            self.assertEqual(columns["neoscorpion_settings"]["neo_fuel_excess_alert_gallons"], "INTEGER NOT NULL DEFAULT 500")
