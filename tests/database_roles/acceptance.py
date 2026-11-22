"""Run unchanged operational assertions on independent restricted PG connections."""
from contextlib import nullcontext
import os
import json
import sys
import unittest
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch

from flask import current_app, has_app_context
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.sql import visitors
from sqlalchemy.sql.schema import Table

from app.extensions import db
from scripts.runtime_database_access import configure
from tests import test_gateway_shell as gateway
from tests import test_unmatched_review_alerts as review_alerts
from tests import test_neorain_sort_refresh as windows
from tests.test_neorain_overrides_postgres import RainOverridePostgresTest
from tests import test_google_rain_integration_mode_transition as manual
from tests.test_sektor_ermac_performance_postgres import postgres
from run_local import DBNAME, FALLBACK, MIGRATOR, RUNTIME
from operations import AccountUpdates, GatewayUpdates, StaffingUpdates


class Gateway(gateway.GatewayShellTest):
    def setUp(self):
        postgres(self, gateway, super().setUp)


class SortHours(windows.NeoRainSortRefreshTest):
    def setUp(self):
        with patch.object(windows.fixtures, "RainOverrideTest", RainOverridePostgresTest):
            super().setUp()


class ReviewAlerts(review_alerts.UnmatchedReviewAlertsTest):
    def setUp(self):
        postgres(self, review_alerts, super().setUp)


class ManualSort(manual.GoogleRainIntegrationModeTransitionTest):
    def setUp(self):
        postgres(self, manual, super().setUp)


CASES = {
    "review_alert": "__main__.ReviewAlerts.test_alert_action_marks_only_current_user_read_and_deep_links_queue",
    "vendor_membership": "test_fuel_driver_accounts_postgres.FuelDriverAccountsPostgresTest.test_company_capability_changes_serialize_and_preserve_history",
    "transfer": "test_neostaffing_transfers_postgres.EmployeeTransferPostgresTest.test_competing_transfers_accept_one_revision_and_keep_one_active_placement",
    "timecard": "test_neostaffing_timecards_postgres.TimecardsPostgresTest.test_concurrent_distinct_employees_both_persist",
    "employee_record": "test_neostaffing_employee_records_postgres.EmployeeRecordPostgresTest.test_two_finalizations_only_one_wins",
    "delay": "test_neorain_delay_entry_postgres.RainDelayEntryPostgresTest.test_concurrent_corrections_preserve_one_winner",
    "password": "test_password_reset_atomic_postgres.AtomicPasswordResetPostgresTest.test_invalid_password_does_not_consume_token_postgres",
    "subzero_archive": "test_neosubzero_release4_postgres.Release4PostgresTest.test_complete_multi_aircraft_sort_and_archive_handoff",
    "vendor": "test_neoscorpion_vendor_postgres.VendorPostgresTest.test_reading_never_crosses_another_sort_physical_visit",
    "usage": "__main__.GatewayUpdates.test_usage",
    "ground_time": "__main__.GatewayUpdates.test_ground_time",
    "fluid": "__main__.GatewayUpdates.test_fluid",
    "truck": "__main__.GatewayUpdates.test_truck",
    "flow": "__main__.StaffingUpdates.test_green_drop_moves_home_final_and_matching_setup_and_stays_green",
    "unit": "__main__.StaffingUpdates.test_unit",
    "preferences": "__main__.AccountUpdates.test_edit_preferences",
    "reptile_unavailable": "__main__.Gateway.test_gateway_marks_unavailable_nodes_disabled_without_changing_launch_contracts",
    "sort_forwarding": "__main__.Gateway.test_gateway_preserves_operation_forwarding_for_node_launches",
    "recovery_rollback": "test_account_deletion_postgres.AccountDeletionPostgresTest.test_post_audit_failure_rolls_back_all_security_changes",
    "commission": "test_grandmaster_commissioning_postgres.GrandmasterCommissioningPostgresTest.test_actor_revocation_after_wait_is_rechecked",
    "roster_rollback": "test_neostaffing_historical_records_postgres.StaffingHistoricalRecordsPostgresTest.test_rollback_excludes_source_changes_and_restores_unsealed_close",
    "closeout": "test_neorain_rainrock_postgres.RainrockPostgresTest.test_rainrock_and_briefing_simultaneous_first_insert_share_one_identity",
    "conditional_uld": "test_neosektor_raiden_postgres.SektorRaidenMigrationTest.test_conditional_read_skips_mission_projection_and_tracks_committed_sources",
    "manual_negative": "__main__.ManualSort.test_test_archive_refuses_unauthorized_scheduled_and_operational_sorts",
    "sort_gap": "__main__.ManualSort.test_unscheduled_manual_friday_operation_still_blocks_until_its_window_ends",
    "gateway": "__main__.Gateway.test_gateway_renders_its_own_header_launcher_and_locked_node_logos",
    "portal": "__main__.Gateway.test_gateway_shell_does_not_change_portal_or_staffing_shells",
    "sort_hours": "__main__.SortHours.test_all_automatic_endpoints_honor_the_overnight_window",
    "historical_hours": "__main__.SortHours.test_historical_state_is_manual_only_and_rollover_never_reuses_a_board",
    "idle_hours": "__main__.SortHours.test_an_operation_record_does_not_enable_an_unscheduled_or_inactive_sort",
    "manual_sort": "__main__.ManualSort.test_explicit_test_archive_is_atomic_and_preserves_missions",
    "report_save": "test_neorain_daily_briefing_postgres.BriefingPostgresTest.test_concurrent_initial_save_accepts_only_one_version",
    "scoped_policy": "test_scoped_authorization_postgres.ScopedAuthorizationPostgresTest.test_operator_threshold_saves_and_forged_protected_or_out_of_scope_edit_rolls_back",
    "audit_atomicity": "test_scoped_authorization_postgres.ScopedAuthorizationPostgresTest.test_failed_audit_rolls_back_role_and_session",
    "history": "test_neostaffing_historical_records_postgres.StaffingHistoricalRecordsPostgresTest.test_after_close_transfer_defers_capture_then_two_seals_publish_once",
    "archive_race": "test_subzero_archive_reconciliation_postgres.ArchiveReconciliationPostgresTest.test_two_workers_skip_an_existing_gateway_claim_without_duplicate_receipts",
    "access": "test_account_foundation_postgres.AccountFoundationPostgresTest.test_concurrent_approve_deny_has_one_immutable_decision_no_entitlement",
    "revocation": "test_account_foundation_postgres.AccountFoundationPostgresTest.test_actor_revocation_while_waiting_reauthorizes_before_decision",
    "motherbrain": "test_google_parking_idempotency_postgres.GoogleParkingPostgresTest.test_two_unchanged_poll_workers_create_no_duplicates_or_updates",
    "parking": "test_google_parking_idempotency_postgres.GoogleParkingPostgresTest.test_real_google_move_invalidates_stale_manual_snapshot_safely",
    "raiden": "test_neosektor_raiden_postgres.SektorRaidenMigrationTest.test_gateway_and_sort_isolation",
    "sektor": "test_sektor_ermac_performance_postgres.SektorPostgresPerformanceTest.test_independent_concurrent_readers_and_committed_update",
    "ermac": "test_sektor_ermac_performance_postgres.ErmacPostgresPerformanceTest.test_competing_lineup_edits_keep_the_existing_atomic_conflict_guard",
    "scorpion": "test_neoscorpion_direct_entry_postgres.DirectEntryPostgresTest.test_concurrent_dispatchers_get_one_completion_and_one_conflict",
    "subzero": "test_neosubzero_radio_postgres.RadioPostgresTest.test_two_confirmations_with_one_version_record_exactly_one_actor_timestamp_and_audit",
    "rain": "test_neorain_mission_notes_postgres.NativeRainPostgresTest.test_concurrent_note_update_preserves_the_winning_version",
    "staffing": "test_neostaffing_employee_recovery_postgres.EmployeeRecoveryPostgresTest.test_two_administrators_restore_idempotently",
    "hourslog": "test_neostaffing_sheet_sync_postgres.StaffingSheetPostgresTest.test_two_workers_claim_once_and_never_hold_db_during_google",
    "archive": "test_archive_delivery_postgres.ArchiveDeliveryPostgresTest.test_concurrent_workers_claim_once_and_release_transactions_for_r2",
    "expiry": "test_background_deadlines_postgres.DeadlinePostgresTest.test_two_workers_purge_expired_payload_once",
    "frost": "test_frost_learning_reliability_postgres.LearningReliabilityPostgresTest.test_empty_upload_worker_does_not_claim_or_write",
}


def assert_disposable():
    for key, expected in [('ROLE_TEST_RUNTIME_URL', RUNTIME), ('NEOSTAFFING_TEST_POSTGRES_URL', MIGRATOR)]:
        url = make_url(os.environ[key])
        if (url.host, url.username) != ('127.0.0.1', expected) or url.database not in (DBNAME, DBNAME+"_paths") or not url.port:
            raise RuntimeError("Only the locally generated, isolated acceptance database is supported")


def restricted_method(case, method, role=RUNTIME):
    # setUp and tearDown own the fixture. All application transactions inside
    # the assertion (including separate request threads) use this fresh pool.
    app = current_app._get_current_object() if has_app_context() else case.app
    with nullcontext() if has_app_context() else app.app_context():
        owner_engine = db.engine
        db.session.commit()
        with owner_engine.begin() as conn:
            schema = conn.scalar(text("SELECT current_schema()"))
            if role != MIGRATOR:
                with conn.connection.driver_connection.cursor() as cursor:
                    configure(cursor, schema, role, apply=True)
        url_key = {MIGRATOR: "NEOSTAFFING_TEST_POSTGRES_URL", RUNTIME: "ROLE_TEST_RUNTIME_URL",
                   FALLBACK: "ROLE_TEST_FALLBACK_URL"}[role]
        runtime_engine = create_engine(os.environ[url_key], connect_args={
            'options': f'-csearch_path={schema} -clock_timeout=10000 -cstatement_timeout=30000'})
        db.engines[None] = runtime_engine
        # Some concurrency assertions attach their listener to the saved
        # application engine. Preserve the assertion on the actual runtime pool.
        saved_application_handle = getattr(case, 'engine', None) is owner_engine
        if saved_application_handle:
            case.engine = runtime_engine
        observations = set()
        def require_runtime(conn, cursor, statement, parameters, context, executemany):
            if conn.dialect.name != "postgresql" or conn.connection.driver_connection.info.user != role:
                raise AssertionError("An operational assertion used a different database login")
            if os.getenv('ROLE_TEST_MATRIX') and context.compiled:
                clause = context.compiled.statement
                verbs = ('insert', 'update', 'delete')
                if clause.__visit_name__ in verbs:
                    observations.add((clause.table.name, clause.__visit_name__.upper()))
                elif getattr(clause, 'is_select', False):
                    observations.update((node.name, 'SELECT') for node in visitors.iterate(clause)
                                        if isinstance(node, Table))
        def report_denial(context):
            error = context.original_exception
            if getattr(error, 'pgcode', None) == '42501':
                print("Fixture ACL denial:", error.diag.message_primary, file=sys.stderr)
        event.listen(Engine, 'before_cursor_execute', require_runtime)
        event.listen(Engine, 'handle_error', report_denial)
        try:
            assert db.session.execute(text("SELECT session_user,current_user")).one() == (role, role)
            db.session.commit()
            method()
            if path := os.getenv('ROLE_TEST_MATRIX'):
                with open(path, 'a', encoding='utf-8') as file:
                    print(json.dumps(dict(test=case.id(), role=role, orm_operations=sorted(observations))), file=file)
        finally:
            event.remove(Engine, 'handle_error', report_denial)
            event.remove(Engine, 'before_cursor_execute', require_runtime)
            db.session.rollback()
            db.engines[None] = owner_engine
            if saved_application_handle:
                case.engine = owner_engine
            runtime_engine.dispose()


def portable_suite():
    """Replay ordinary SQLite-backed application tests with isolated PG setup."""
    suite = unittest.TestSuite()
    for name in Path(os.environ['ROLE_WORKLOADS']).read_text(encoding='utf-8-sig').splitlines():
        if not name or name.startswith('#'):
            continue
        case = unittest.defaultTestLoader.loadTestsFromName('tests.' + name)._tests[0]
        original = case.setUp
        def setup(test=case, original=original):
            uri = os.environ['NEOSTAFFING_TEST_POSTGRES_URL']
            schema = 'operational_' + uuid4().hex
            setup_engine = create_engine(uri)
            with setup_engine.begin() as connection:
                connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            def cleanup():
                with setup_engine.begin() as connection:
                    connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
                setup_engine.dispose()
            test.addCleanup(cleanup)
            init = db.init_app
            def initialize(app):
                if not app.testing:
                    raise RuntimeError("Expected an isolated test application")
                app.config.update(SQLALCHEMY_DATABASE_URI=uri, SQLALCHEMY_ENGINE_OPTIONS={
                    'connect_args': {'options': f'-csearch_path={schema} -clock_timeout=5000 -cstatement_timeout=30000'}})
                return init(app)
            with patch.object(db, 'init_app', initialize):
                original()
        case.setUp = setup
        suite.addTest(case)
    return suite


def main():
    assert_disposable()
    names = sys.argv[1:]
    if 'paths' in names:
        names = list(CASES) + [name for name in names if name != 'paths']
    suite = unittest.TestSuite()
    if 'boundary' in names:
        from boundary import RoleBoundary
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(RoleBoundary))
    runner = unittest.TextTestRunner(verbosity=2)
    boundary = runner.run(suite) if suite.countTestCases() else None
    results = [boundary] if boundary else []
    for role in (MIGRATOR, RUNTIME, FALLBACK):
        operational = unittest.TestSuite()
        if 'catalog_paths' in names and role == RUNTIME:
            operational.addTests(portable_suite())
        for name in names:
            if name in ('boundary', 'catalog_paths'):
                continue
            case = CASES[name]
            operational.addTests(unittest.defaultTestLoader.loadTestsFromName(
                case if case.startswith('__main__.') else 'tests.' + case))
        if operational.countTestCases():
            print("Testing independently authenticated role", role, flush=True)
            with patch.object(unittest.TestCase, '_callTestMethod',
                              lambda case, method: restricted_method(case, method, role)):
                results.append(runner.run(operational))
    return int(any(not result.wasSuccessful() or result.skipped for result in results))


if __name__ == '__main__':
    sys.exit(main())
