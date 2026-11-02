"""R07: canonical native Block-Out survives ordinary Google imports."""
from datetime import date, datetime
import unittest
from unittest.mock import patch

from sqlalchemy import event

from app.extensions import db
from app.models import Gateway, NeoRainMissionOverride, SortDateGoogleMissionLink, SortDateMission, SortDateOperation
from app.neomotherbrain.routes import _apply_mission_form, _mission_form_from_model
from app.services.google_motherbrain_live_missions import apply_google_motherbrain_live_mission_batch
from app.services.google_rain_live_milestones import (
    apply_google_rain_departure_milestones,
    NEO_TO_GOOGLE_AUTHORITY_HANDOFF,
)
from app.services.live_collaboration import entity_version
from tests import test_google_rain_live_milestones as fixtures
from tests import test_neorain_overrides as override_fixtures


def manual_block_out(mission, operation, value, *, commit=True):
    """Use the actual MotherBrain editor, not a fabricated source flag."""
    form = _mission_form_from_model(mission)
    form.update(wave="1", actual_block_out_datetime_utc=value)
    _apply_mission_form(mission, operation, form)
    if commit:
        db.session.commit()


def primary_row(mission, block="2:45"):
    return dict(sheet_row=4, flight_number=mission.flight_number,
                destination=mission.destination, effective_tail=mission.assigned_tail_number,
                planned_time="2:24", operational_time=block)


class MotherBrainBlockOutAuthorityTest(unittest.TestCase):
    setUp = fixtures.GoogleRainLiveMilestonesTest.setUp
    tearDown = fixtures.GoogleRainLiveMilestonesTest.tearDown
    _mission = fixtures.GoogleRainLiveMilestonesTest._mission
    _row = staticmethod(fixtures.GoogleRainLiveMilestonesTest._row)
    _apply = fixtures.GoogleRainLiveMilestonesTest._apply

    def native(self, mission, value="2026-08-11 07:00"):
        manual_block_out(mission, self.operation, value)

    def assert_native(self, mission, value=datetime(2026, 8, 11, 7)):
        self.assertEqual(mission.actual_block_out_datetime_utc, value)
        self.assertEqual(mission.actual_block_out_source, "manual")

    def test_canonical_manual_0700_survives_google_rain_0745_and_withdrawal(self):
        mission = self._mission("UPS0910", "LAX")
        self.native(mission)
        self.assertEqual(NeoRainMissionOverride.query.count(), 0)
        version = entity_version(mission)
        for block in ("2:45", "3:00", ""):
            result = self._apply(self._row("UPS0910", block=block))
            self.assert_native(mission)
            self.assertEqual(entity_version(mission), version)
            self.assertEqual(result["results"][0]["changed_fields"], [])
            self.assertIn("Protected Rain Official Block-Out preserved.", result["results"][0]["warnings"])

    def test_google_then_canonical_manual_correction_remains_native(self):
        mission = self._mission("UPS0910", "LAX")
        self._apply(self._row("UPS0910", block="2:45"))
        self.native(mission, "2026-08-11 07:10")
        version = entity_version(mission)
        self._apply(self._row("UPS0910", block="2:55"))
        self.assert_native(mission, datetime(2026, 8, 11, 7, 10))
        self.assertEqual(entity_version(mission), version)

    def test_google_authority_revises_and_clears_without_native_value(self):
        mission = self._mission("UPS0910", "LAX")
        for block, minute in (("2:30", 30), ("2:45", 45)):
            self._apply(self._row("UPS0910", block=block))
            self.assertEqual(mission.actual_block_out_datetime_utc, datetime(2026, 8, 11, 7, minute))
            self.assertEqual(mission.actual_block_out_source, "google_rain")
        self._apply(self._row("UPS0910", block=""))
        self.assertIsNone(mission.actual_block_out_datetime_utc)
        self.assertEqual(mission.actual_block_out_source, "unknown")

    def test_repeated_identical_and_protected_imports_do_not_update_mission(self):
        mission = self._mission("UPS0910", "LAX")
        self._apply(self._row("UPS0910", block="2:45"))
        for native in (False, True):
            if native:
                self.native(mission)
            version = entity_version(mission)
            statements = []
            def record(_conn, _cursor, sql, _params, _context, _many):
                if sql.lower().startswith("update sort_date_missions"):
                    statements.append(sql)
            event.listen(db.engine, "before_cursor_execute", record)
            try:
                for _ in range(3):
                    self._apply(self._row("UPS0910", block="2:45"))
            finally:
                event.remove(db.engine, "before_cursor_execute", record)
            self.assertEqual(statements, [])
            self.assertEqual(entity_version(mission), version)

    def test_both_importers_preserve_native_sources(self):
        mission = self._mission("UPS0910", "LAX")
        self.native(mission)
        for source in ("manual", "neo", "api", "alp", "neorain", "neo_rain", "rain"):
            with self.subTest(source=source):
                mission.actual_block_out_source = source
                db.session.commit()
                self._apply(self._row("UPS0910", block="2:45"))
                apply_google_motherbrain_live_mission_batch(
                    self.operation, "departure", [primary_row(mission)], now=datetime(2026, 8, 11, 10))
                db.session.commit()
                self.assertEqual(mission.actual_block_out_datetime_utc, datetime(2026, 8, 11, 7))
                self.assertEqual(mission.actual_block_out_source, source)

    def test_primary_google_revisions_and_rain_priority_are_unchanged(self):
        mission = self._mission("UPS0910", "LAX")
        for value in ("2:30", "2:35"):
            apply_google_motherbrain_live_mission_batch(self.operation, "departure",
                [primary_row(mission, value)], now=datetime(2026, 8, 11, 10))
            db.session.commit()
        self.assertEqual(mission.actual_block_out_source, "google_motherbrain")
        self.assertEqual(mission.actual_block_out_datetime_utc.minute, 35)
        self._apply(self._row("UPS0910", block="2:40"))
        apply_google_motherbrain_live_mission_batch(self.operation, "departure",
            [primary_row(mission, "2:45")], now=datetime(2026, 8, 11, 10))
        db.session.commit()
        self.assertEqual(mission.actual_block_out_source, "google_rain")
        self.assertEqual(mission.actual_block_out_datetime_utc.minute, 40)

    def test_mode_handoff_does_not_grant_permission_to_replace_manual_time(self):
        mission = self._mission("UPS0910", "LAX")
        self.native(mission)
        apply_google_rain_departure_milestones(self.operation,
            rows=[self._row("UPS0910", block="2:45")],
            authority_handoff=NEO_TO_GOOGLE_AUTHORITY_HANDOFF)
        db.session.commit()
        self.assert_native(mission)

    def test_authorized_neorain_handoff_still_works(self):
        mission = self._mission("UPS0910", "LAX")
        mission.actual_block_out_datetime_utc = datetime(2026, 8, 11, 7)
        mission.actual_block_out_source = "neorain"
        db.session.commit()
        apply_google_rain_departure_milestones(self.operation,
            rows=[self._row("UPS0910", block="2:45")],
            authority_handoff=NEO_TO_GOOGLE_AUTHORITY_HANDOFF)
        db.session.commit()
        self.assertEqual(mission.actual_block_out_source, "google_rain")
        self.assertEqual(mission.actual_block_out_datetime_utc.minute, 45)

    def test_external_row_cannot_authorize_override_and_other_missions_are_isolated(self):
        target = self._mission("UPS0910", "LAX")
        other = self._mission("UPS0948", "LAX", tail="N456UP")
        self.native(target)
        untouched = entity_version(other)
        self._apply(self._row("UPS0910", block="2:45", override=True,
                            authority_handoff=NEO_TO_GOOGLE_AUTHORITY_HANDOFF))
        self.assert_native(target)
        self.assertIsNone(other.actual_block_out_datetime_utc)
        self.assertEqual(entity_version(other), untouched)
        foreign_gateway = Gateway(code="TST", name="Isolated gateway", is_active=True)
        db.session.add(foreign_gateway)
        db.session.flush()
        later = SortDateOperation(gateway_id=foreign_gateway.id, gateway_code="TST",
                                  sort_date=date(2026, 8, 12), sort_name="night")
        db.session.add(later)
        db.session.flush()
        foreign = SortDateMission(sort_date_operation_id=later.id, gateway_code="TST",
            sort_date=later.sort_date, sort_name="night", mission_type="departure",
            flight_number="UPS0910", origin="RFD", destination="LAX", assigned_tail_number="N789UP",
            departure_status="scheduled")
        db.session.add(foreign)
        db.session.commit()
        self._apply(self._row("UPS0910", block="2:50"))
        self.assertIsNone(foreign.actual_block_out_datetime_utc)
        self.assertEqual(foreign.assigned_tail_number, "N789UP")


class BlockOutExplicitOverrideTest(unittest.TestCase):
    setUp = override_fixtures.RainOverrideTest.setUp
    edit = override_fixtures.RainOverrideTest.edit

    def test_authorized_override_remains_protected_and_reset_restores_fresh_upstream(self):
        manual_block_out(self.departure, self.operation, "2026-08-31 07:00")
        db.session.add(SortDateGoogleMissionLink(sort_date_operation_id=self.operation.id,
            sort_date_mission_id=self.departure.id, mission_type="departure",
            source_sheet="Outbound", source_row=4))
        db.session.commit()
        with patch("app.services.neorain_google_overrides.certified_write", return_value="Outbound!O4"):
            response = self.edit(self.departure, "official_block_out", "0240")
        self.assertEqual(response.status_code, 200, response.json)
        apply_google_rain_departure_milestones(self.operation, rows=[
            dict(sheet_row=4, flight_number=self.departure.flight_number, block="2:45")])
        db.session.commit()
        self.assertEqual(self.departure.actual_block_out_datetime_utc, datetime(2026, 8, 31, 7, 40))
        self.assertEqual(self.departure.actual_block_out_source, "neorain")
        override = NeoRainMissionOverride.query.filter_by(field="official_block_out").one()
        self.assertEqual(override.upstream["actual_block_out_source"], "google_rain")
        self.app.config["NEORAIN_FRESH_UPSTREAM_READER"] = lambda *_args: {
            "actual_block_out_datetime_utc": datetime(2026, 8, 31, 7, 45),
            "actual_block_out_source": "google_rain"}
        response = self.edit(self.departure, "official_block_out", action="reset")
        self.assertEqual(response.status_code, 200, response.json)
        self.assertFalse(override.active)
        self.assertEqual(self.departure.actual_block_out_datetime_utc, datetime(2026, 8, 31, 7, 45))
        self.assertEqual(self.departure.actual_block_out_source, "google_rain")

    def test_unauthorized_override_cannot_change_native_time(self):
        manual_block_out(self.departure, self.operation, "2026-08-31 07:00")
        watcher = self.fixture._user("blockout_watcher", "watcher", rain_role="watcher")
        db.session.commit()
        self.fixture._login(watcher)
        response = self.edit(self.departure, "official_block_out", "0245")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.departure.actual_block_out_datetime_utc, datetime(2026, 8, 31, 7))
        self.assertEqual(self.departure.actual_block_out_source, "manual")
        self.assertEqual(NeoRainMissionOverride.query.count(), 0)
