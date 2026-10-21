"""Real normalized-identity trigger reproductions; isolated loopback schemas only."""
import os
import unittest
from unittest.mock import patch
from uuid import uuid4
from sqlalchemy import create_engine,text
from sqlalchemy.engine import make_url
from app import create_app
from app.extensions import db
from app.services.mission_identity import sync_mission_identity_schema
from tests import test_google_motherbrain_live_missions as contracts

URI=os.environ.get('NEOSTAFFING_TEST_POSTGRES_URL')


@unittest.skipUnless(URI,'Requires disposable loopback PostgreSQL')
class GoogleHotIdentityPostgresTest(unittest.TestCase):
    tearDown=contracts.GoogleMotherBrainLiveMissionTest.tearDown
    _operation=contracts.GoogleMotherBrainLiveMissionTest._operation
    _mission=contracts.GoogleMotherBrainLiveMissionTest._mission
    _master=contracts.GoogleMotherBrainLiveMissionTest._master
    _apply_arrivals=contracts.GoogleMotherBrainLiveMissionTest._apply_arrivals
    _apply_departures=contracts.GoogleMotherBrainLiveMissionTest._apply_departures
    _inbound=contracts.GoogleMotherBrainLiveMissionTest._inbound
    _outbound=contracts.GoogleMotherBrainLiveMissionTest._outbound
    _tail_state=contracts.GoogleMotherBrainLiveMissionTest._tail_state
    _mission_by_flight=contracts.GoogleMotherBrainLiveMissionTest._mission_by_flight
    test_existing_hot_flight_wins_over_stale_google_row_link=contracts.GoogleMotherBrainLiveMissionTest.test_existing_hot_flight_wins_over_stale_google_row_link
    test_reordered_hot_and_regular_rows_preserve_both_mission_ids=contracts.GoogleMotherBrainLiveMissionTest.test_reordered_hot_and_regular_rows_preserve_both_mission_ids
    test_hot_placeholder_tail_digits_are_not_a_flight_identity=contracts.GoogleMotherBrainLiveMissionTest.test_hot_placeholder_tail_digits_are_not_a_flight_identity
    test_production_hot_inbound_and_outbound_echo_preserve_both_canonical_identities=contracts.GoogleMotherBrainLiveMissionTest.test_production_hot_inbound_and_outbound_echo_preserve_both_canonical_identities
    test_hot_echo_rejects_unproven_identity_without_duplicate_or_rename=contracts.GoogleMotherBrainLiveMissionTest.test_hot_echo_rejects_unproven_identity_without_duplicate_or_rename
    test_hot_echo_preserves_physical_work_transfer_snapshots_and_inventory=contracts.GoogleMotherBrainLiveMissionTest.test_hot_echo_preserves_physical_work_transfer_snapshots_and_inventory
    test_hot_echo_can_later_become_real_departure_without_changing_mission_id=contracts.GoogleMotherBrainLiveMissionTest.test_hot_echo_can_later_become_real_departure_without_changing_mission_id
    test_correlated_row_updates_same_mission_when_flight_changes=contracts.GoogleMotherBrainLiveMissionTest.test_correlated_row_updates_same_mission_when_flight_changes
    test_inbound_hot_creates_pending_hot_departure_for_fueling=contracts.GoogleMotherBrainLiveMissionTest.test_inbound_hot_creates_pending_hot_departure_for_fueling
    test_real_outbound_claims_inbound_hot_departure_without_duplication=contracts.GoogleMotherBrainLiveMissionTest.test_real_outbound_claims_inbound_hot_departure_without_duplication
    test_duplicate_google_flight_rows_first_row_wins=contracts.GoogleMotherBrainLiveMissionTest.test_duplicate_google_flight_rows_first_row_wins
    test_repeated_unchanged_google_tail_does_not_churn_mission_version=contracts.GoogleMotherBrainLiveMissionTest.test_repeated_unchanged_google_tail_does_not_churn_mission_version
    test_effective_tail_change_updates_same_mission_and_moves_hot=contracts.GoogleMotherBrainLiveMissionTest.test_effective_tail_change_updates_same_mission_and_moves_hot
    test_correlation_is_operation_and_direction_scoped=contracts.GoogleMotherBrainLiveMissionTest.test_correlation_is_operation_and_direction_scoped
    def setUp(self):
        url=make_url(URI)
        if url.host not in ('127.0.0.1','localhost') or not url.database.startswith('neostaffing_test_'):
            raise RuntimeError('Refusing non-disposable PostgreSQL')
        self.schema='google_hot_'+uuid4().hex;admin=create_engine(URI)
        with admin.begin() as conn:conn.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        def cleanup():
            with admin.begin() as conn:conn.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
            admin.dispose()
        self.addCleanup(cleanup)
        def pg_app(config):
            config.SQLALCHEMY_DATABASE_URI=URI
            config.SQLALCHEMY_ENGINE_OPTIONS={'connect_args':{'options':f'-csearch_path={self.schema}'}}
            return create_app(config)
        with patch.object(contracts,'create_app',side_effect=pg_app):contracts.GoogleMotherBrainLiveMissionTest.setUp(self)
        sync_mission_identity_schema(db.session.connection());db.session.commit()
