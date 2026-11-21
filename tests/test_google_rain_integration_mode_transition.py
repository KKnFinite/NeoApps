from datetime import date, datetime, time
import unittest
from unittest.mock import patch

from app import create_app
from app.extensions import db
from app.models import Gateway, GatewaySortMatrix, SortDateMission, SortDateOperation, User
from app.services.gateway_matrix import save_gateway_matrix
from app.services.google_rain_integration_mode import (
    GOOGLE_PRIMARY, NEO_ONLY, NEO_PRIMARY_GOOGLE_MIRROR,
    RainIntegrationTransitionError, RainIntegrationWindowError,
    change_rain_integration_mode, rain_integration_mode, set_rain_integration_mode,
)
from tests.test_neorain_sort_refresh import configure_sort_window
from app.models.sort_date_operation import ManualTestSortArchive
from app.services.manual_test_sort_archive import archive_manual_test_operation


class GoogleRainIntegrationModeTransitionTest(unittest.TestCase):
    def setUp(self):
        config = type("TransitionConfig", (), {
            "SECRET_KEY": "test",
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "DEFAULT_GATEWAY_TIMEZONE": "America/Chicago",
        })
        self.app = create_app(config)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.gateway = Gateway(code="RFD", name="NeoGateway", is_active=True)
        db.session.add(self.gateway)
        db.session.flush()
        self.operation = SortDateOperation(
            gateway_id=self.gateway.id, gateway_code="RFD", sort_name="night", sort_date=date(2026, 8, 29),
        )
        db.session.add(self.operation)
        db.session.flush()
        db.session.add(SortDateMission(
            sort_date_operation_id=self.operation.id, sort_date=self.operation.sort_date, gateway_code="RFD",
            sort_name="night", mission_type="departure", mission_source="master", flight_number="UPS0910",
            origin="RFD", destination="LAX", timezone="America/Chicago", departure_status="departed",
            ramp_load_completed_at_utc=datetime(2026, 8, 30, 7, 25), ramp_load_completed_source="neorain",
            actual_block_out_datetime_utc=datetime(2026, 8, 30, 8, 5), actual_block_out_source="neorain",
        ))
        self.night, _ = configure_sort_window(self.gateway, self.operation.sort_date)
        save_gateway_matrix(self.gateway, {("thursday", "night")})
        self.now(datetime(2026, 8, 29, 20, 59))
        for target, attr in (
            ("app.services.google_rain_sheets.read_google_rain_outbound_milestones", "reader"),
            ("app.services.google_rain_sheets.write_google_rain_departure_milestone", "writer"),
            ("app.services.google_rain_live_milestones.apply_google_rain_departure_milestones", "applier"),
        ):
            context = patch(target, side_effect=AssertionError("A mode change cannot call Google or apply milestones."))
            setattr(self, attr, context.start())
            self.addCleanup(context.stop)

    def tearDown(self):
        self.reader.assert_not_called()
        self.writer.assert_not_called()
        self.applier.assert_not_called()
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def now(self, when):
        self.app.config["CURRENT_GATEWAY_LOCAL_DATETIME_OVERRIDE"] = when

    def change(self, mode):
        return change_rain_integration_mode(self.gateway, "night", mode)

    def facts(self):
        table = SortDateMission.__table__
        return db.session.execute(table.select().order_by(table.c.id)).all()

    def test_explicit_test_archive_is_atomic_and_preserves_missions(self):
        actor = User(username="test_owner", password_hash="fixture", role="grandmaster")
        db.session.add(actor)
        db.session.flush()
        self.operation.generated_by_user_id = actor.id
        self.now(datetime(2026, 8, 29, 22))
        mission = SortDateMission.query.one()
        mission.departure_status = "scheduled"
        mission.actual_block_out_datetime_utc = mission.ramp_load_completed_at_utc = None
        db.session.commit()
        before = self.facts()
        with self.assertRaises(RainIntegrationWindowError):
            self.change(NEO_ONLY)
        archive_manual_test_operation(self.gateway, self.operation.id, actor, "Owner test")
        db.session.flush()
        db.session.rollback()
        self.assertIsNone(self.operation.archived_at_utc)
        self.assertIsNone(db.session.get(ManualTestSortArchive, self.operation.id))
        archive_manual_test_operation(self.gateway, self.operation.id, actor, "Owner test")
        db.session.commit()
        receipt = db.session.get(ManualTestSortArchive, self.operation.id)
        self.assertEqual((receipt.reason, receipt.actor_user_id, receipt.archived_at_utc),
                         ("Owner test", actor.id, self.operation.archived_at_utc))
        self.assertEqual(self.change(NEO_ONLY)["mode"], NEO_ONLY)
        self.assertEqual(before, self.facts())
        save_gateway_matrix(self.gateway, {("saturday", "night")})
        with self.assertRaises(RainIntegrationWindowError):
            self.change(GOOGLE_PRIMARY)

    def test_test_archive_refuses_unauthorized_scheduled_and_operational_sorts(self):
        actor = User(username="sort_owner", password_hash="fixture", role="operator")
        db.session.add(actor)
        db.session.flush()
        self.operation.generated_by_user_id = actor.id
        with self.assertRaises(PermissionError):
            archive_manual_test_operation(self.gateway, self.operation.id, actor, "Test")
        actor.role = "grandmaster"
        db.session.commit()
        with self.assertRaisesRegex(ValueError, "Operational activity"):
            archive_manual_test_operation(self.gateway, self.operation.id, actor, "Test")
        save_gateway_matrix(self.gateway, {("saturday", "night")})
        with self.assertRaisesRegex(ValueError, "explicitly mark"):
            archive_manual_test_operation(self.gateway, self.operation.id, actor, "Test")
        self.assertIsNone(self.operation.archived_at_utc)

    def test_opening_midnight_and_last_minute_block_every_authority_change(self):
        before = self.facts()
        for current, new in (
            (GOOGLE_PRIMARY, NEO_ONLY), (NEO_ONLY, GOOGLE_PRIMARY),
            (NEO_ONLY, NEO_PRIMARY_GOOGLE_MIRROR), (NEO_PRIMARY_GOOGLE_MIRROR, NEO_ONLY),
        ):
            set_rain_integration_mode(self.gateway, "night", current)
            db.session.commit()
            for now in (datetime(2026, 8, 29, 21), datetime(2026, 8, 30), datetime(2026, 8, 30, 4, 59)):
                with self.subTest(current=current, new=new, now=now):
                    self.now(now)
                    with patch("app.services.google_rain_integration_mode.ensure_rain_integration_setting") as initializer:
                        with self.assertRaisesRegex(RainIntegrationWindowError, "Sort operating hours"):
                            self.change(new)
                        initializer.assert_not_called()
                    self.assertEqual(rain_integration_mode(self.gateway, "night"), current)
                    self.assertEqual(self.facts(), before)

    def test_wider_ops_and_planning_do_not_block_safe_gap_and_rollback(self):
        before = self.facts()
        for when, mode in (
            (datetime(2026, 8, 29, 20, 59), NEO_ONLY),
            (datetime(2026, 8, 30, 5), GOOGLE_PRIMARY),
            (datetime(2026, 8, 30, 5, 1), NEO_PRIMARY_GOOGLE_MIRROR),
            (datetime(2026, 8, 30, 6), NEO_ONLY),
        ):
            self.now(when)
            status = self.change(mode)
            self.assertEqual(status["mode"], mode)
            self.assertFalse(status["handoff_performed"])
            self.assertEqual(self.facts(), before)

    def test_missing_partial_equal_and_nonexistent_dst_boundaries_fail_closed(self):
        for start, end in ((None, time(5)), (time(21), None), (time(21), time(21))):
            self.night.sort_window_start_local, self.night.sort_window_end_local = start, end
            db.session.commit()
            with self.assertRaisesRegex(RainIntegrationWindowError, "valid operating hours"):
                self.change(NEO_ONLY)
        self.now(datetime(2026, 3, 8, 8))
        self.night.sort_window_start_local, self.night.sort_window_end_local = time(2, 30), time(5)
        db.session.commit()
        with self.assertRaisesRegex(RainIntegrationWindowError, "valid operating hours"):
            self.change(NEO_ONLY)
        db.session.delete(self.night)
        db.session.commit()
        with self.assertRaisesRegex(RainIntegrationWindowError, "valid operating hours"):
            self.change(NEO_ONLY)
        self.assertEqual(rain_integration_mode(self.gateway, "night"), GOOGLE_PRIMARY)

    def test_active_schedule_without_an_operation_is_still_protected(self):
        self.now(datetime(2026, 8, 30, 13))
        day = next(setting for setting in self.night.timeline_settings.sort_settings if setting.sort_name == "day")
        day.sort_window_start_local, day.sort_window_end_local = time(10), time(15)
        save_gateway_matrix(self.gateway, {("thursday", "night"), ("sunday", "day")})
        with self.assertRaisesRegex(RainIntegrationWindowError, "Sort operating hours"):
            self.change(NEO_ONLY)
        self.assertEqual(SortDateOperation.query.count(), 1)

    def test_friday_night_without_a_scheduled_or_existing_sort_is_an_idle_gap(self):
        before = self.facts()
        for now, target in (
            (datetime(2026, 11, 20, 21, 30), NEO_ONLY),
            (datetime(2026, 11, 21, 1, 40), GOOGLE_PRIMARY),
        ):
            self.now(now)
            self.assertEqual(self.change(target)["mode"], target)
            self.assertEqual(self.facts(), before)

    def test_scheduled_night_requires_no_operation_row_and_protects_its_overnight_spillover(self):
        for now in (datetime(2026, 11, 19, 21), datetime(2026, 11, 20, 4, 59)):
            self.now(now)
            with self.assertRaisesRegex(RainIntegrationWindowError, "Sort operating hours"):
                self.change(NEO_ONLY)
        self.now(datetime(2026, 11, 20, 5))
        self.assertEqual(self.change(NEO_ONLY)["mode"], NEO_ONLY)

    def test_unscheduled_manual_friday_operation_still_blocks_until_its_window_ends(self):
        actor = User(username="manual_sort_fixture", password_hash="fixture")
        db.session.add(actor)
        db.session.flush()
        db.session.add(SortDateOperation(
            gateway_id=self.gateway.id, gateway_code=self.gateway.code, sort_name="night",
            sort_date=date(2026, 11, 20), generated_by_user_id=actor.id,
        ))
        db.session.commit()
        for now in (datetime(2026, 11, 20, 21, 30), datetime(2026, 11, 21, 1, 40)):
            self.now(now)
            with self.assertRaisesRegex(RainIntegrationWindowError, "Sort operating hours"):
                self.change(NEO_ONLY)
        self.now(datetime(2026, 11, 21, 5))
        self.assertEqual(self.change(NEO_ONLY)["mode"], NEO_ONLY)

    def test_missing_matrix_cell_and_conflicting_gateway_identity_fail_closed(self):
        self.now(datetime(2026, 11, 21, 1, 40))
        cell = GatewaySortMatrix.query.filter_by(gateway_id=self.gateway.id,
                                                 day_of_week="friday", sort_name="night").one()
        db.session.delete(cell)
        db.session.commit()
        with self.assertRaises(RainIntegrationWindowError):
            self.change(NEO_ONLY)
        save_gateway_matrix(self.gateway, {("thursday", "night")})
        cell = GatewaySortMatrix.query.filter_by(gateway_id=self.gateway.id,
                                                 day_of_week="friday", sort_name="night").one()
        cell.gateway_code = "OTHER"
        db.session.commit()
        with self.assertRaises(RainIntegrationWindowError):
            self.change(NEO_ONLY)
        self.assertEqual(rain_integration_mode(self.gateway, "night"), GOOGLE_PRIMARY)

    def test_same_mode_in_sort_is_a_read_only_no_op(self):
        self.now(datetime(2026, 8, 29, 21))
        status = self.change(GOOGLE_PRIMARY)
        self.assertEqual(status["mode"], GOOGLE_PRIMARY)
        self.assertFalse(status["persisted"])

    def test_failed_commit_rolls_back_complete_transaction_and_mode(self):
        before = self.facts()
        def failing_commit():
            db.session.flush()
            raise RuntimeError("private exception")
        with patch.object(db.session, "commit", side_effect=failing_commit):
            with self.assertRaisesRegex(RainIntegrationTransitionError, "previous mode remains active"):
                self.change(NEO_ONLY)
        self.assertEqual(rain_integration_mode(self.gateway, "night"), GOOGLE_PRIMARY)
        self.assertEqual(self.facts(), before)
