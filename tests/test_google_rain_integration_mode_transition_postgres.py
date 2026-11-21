"""NeoRain cutover shares the PostgreSQL gateway lock with Sort generation."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import date, datetime
import os
from threading import Event
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app import create_app
from app.extensions import db
from app.models import Gateway, SortDateOperation, User
from app.services.google_rain_integration_mode import (
    GOOGLE_PRIMARY, NEO_ONLY, RainIntegrationWindowError, change_rain_integration_mode, rain_integration_mode,
)
from app.services.master_schedule_sync import lock_schedule_gateway
from tests import test_google_rain_integration_mode_transition as fixtures


URI = os.environ.get("NEOSTAFFING_TEST_POSTGRES_URL")


@skipUnless(URI, "Requires disposable loopback PostgreSQL")
class RainModeTransitionPostgresTest(fixtures.GoogleRainIntegrationModeTransitionTest):
    def setUp(self):
        url = make_url(URI)
        if url.host not in ("127.0.0.1", "localhost") or not url.database.startswith("neostaffing_test_"):
            raise RuntimeError("Refusing to run against a non-disposable database.")
        engine = create_engine(URI)
        schema = "rain_mode_" + uuid4().hex
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))

        def drop_schema():
            with engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            engine.dispose()

        self.addCleanup(drop_schema)

        def pg_app(config):
            config.SQLALCHEMY_DATABASE_URI = URI
            config.SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"options": f"-csearch_path={schema}"}}
            return create_app(config)

        with patch.object(fixtures, "create_app", side_effect=pg_app):
            super().setUp()

    def test_additive_schema_upgrades_existing_neo_only_twice(self):
        from app.services.schema_sync import sync_database_schema
        from app.models import MotherBrainGoogleIntegrationSetting
        self.change(NEO_ONLY)
        before = self.facts()
        # Recreate the previously deployed shape in this test's disposable schema.
        db.session.execute(text("ALTER TABLE motherbrain_google_integration_settings DROP COLUMN rain_mirror_writes_enabled"))
        db.session.execute(text("DROP TABLE neorain_integration_events"))
        db.session.commit()
        for _ in range(2):
            sync_database_schema(self.app)
            db.session.commit()
            row = MotherBrainGoogleIntegrationSetting.query.filter_by(gateway_id=self.gateway.id, sort_name="night").one()
            self.assertEqual(row.rain_integration_mode, NEO_ONLY)
            self.assertIs(row.rain_mirror_writes_enabled, False)
            self.assertEqual(self.facts(), before)

    def test_switch_waits_for_import_and_next_worker_sees_native_authority(self):
        from app.services.google_rain_integration_mode import operation_mode
        gateway_id, operation_id = self.gateway.id, self.operation.id
        started = Event()

        def switch():
            with self.app.app_context():
                gateway = db.session.get(Gateway, gateway_id)
                started.set()
                return change_rain_integration_mode(gateway, "night", NEO_ONLY)["mode"]

        with ThreadPoolExecutor(1) as pool:
            self.assertEqual(operation_mode(self.operation, lock=True), GOOGLE_PRIMARY)
            future = pool.submit(switch)
            try:
                self.assertTrue(started.wait(5))
                with self.assertRaises(TimeoutError):
                    future.result(timeout=0.2)
            finally:
                db.session.commit()
            self.assertEqual(future.result(timeout=10), NEO_ONLY)
        self.assertEqual(operation_mode(db.session.get(SortDateOperation, operation_id), lock=True), NEO_ONLY)

    def test_concurrent_manual_sort_creation_blocks_off_day_cutover(self):
        actor = User(username="concurrent_sort_fixture", password_hash="fixture")
        db.session.add(actor)
        db.session.commit()
        actor_id, gateway_id = actor.id, self.gateway.id
        self.now(datetime(2026, 11, 21, 1, 40))
        started, release, switching = Event(), Event(), Event()

        def create_manual_sort():
            with self.app.app_context():
                lock_schedule_gateway("RFD")
                db.session.add(SortDateOperation(
                    gateway_id=gateway_id, gateway_code="RFD", sort_name="night",
                    sort_date=date(2026, 11, 20), generated_by_user_id=actor_id,
                ))
                db.session.flush()
                started.set()
                if not release.wait(8):
                    raise TimeoutError("Test did not release the generation transaction.")
                db.session.commit()

        def try_switch():
            with self.app.app_context():
                gateway = db.session.get(Gateway, gateway_id)
                switching.set()
                return change_rain_integration_mode(gateway, "night", NEO_ONLY)

        with ThreadPoolExecutor(2) as pool:
            generator = pool.submit(create_manual_sort)
            try:
                self.assertTrue(started.wait(5))
                transition = pool.submit(try_switch)
                self.assertTrue(switching.wait(5))
                with self.assertRaises(TimeoutError):
                    transition.result(timeout=0.2)
            finally:
                release.set()
            generator.result(timeout=10)
            with self.assertRaisesRegex(RainIntegrationWindowError, "Sort operating hours"):
                transition.result(timeout=10)
        self.assertEqual(rain_integration_mode(self.gateway, "night"), GOOGLE_PRIMARY)
