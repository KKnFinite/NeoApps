import unittest
from unittest.mock import patch

from app import create_app
from app.services.memory_diagnostics import (
    clear_memory_diagnostic_samples,
    observe_process_memory,
    record_process_memory_checkpoint,
)


class MemoryDiagnosticsTest(unittest.TestCase):
    def setUp(self):
        config = type(
            "TestConfig",
            (),
            {
                "SECRET_KEY": "memory-diagnostics-test-secret",
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
                "SQLALCHEMY_TRACK_MODIFICATIONS": False,
                "NEOAPPS_MEMORY_DIAGNOSTICS_ENABLED": False,
                "NEOAPPS_MEMORY_DIAGNOSTICS_SAMPLE_SECONDS": 0,
            },
        )
        self.app = create_app(config)
        self.context = self.app.app_context()
        self.context.push()
        clear_memory_diagnostic_samples()

    def tearDown(self):
        clear_memory_diagnostic_samples()
        self.context.pop()

    def test_diagnostics_are_disabled_by_default(self):
        with patch(
            "app.services.memory_diagnostics.process_memory_snapshot"
        ) as snapshot, patch(
            "app.services.memory_diagnostics._emit_diagnostic_line"
        ) as emit:
            with observe_process_memory("test_operation"):
                pass

        snapshot.assert_not_called()
        emit.assert_not_called()

    def test_enabled_operation_logs_rss_delta_and_high_water(self):
        self.app.config["NEOAPPS_MEMORY_DIAGNOSTICS_ENABLED"] = True
        snapshots = [
            {
                "rss_bytes": 100,
                "high_water_bytes": 200,
                "pid": 123,
                "python": "3.14.0",
            },
            {
                "rss_bytes": 140,
                "high_water_bytes": 240,
                "pid": 123,
                "python": "3.14.0",
            },
        ]
        with patch(
            "app.services.memory_diagnostics.process_memory_snapshot",
            side_effect=snapshots,
        ) as snapshot, patch(
            "app.services.memory_diagnostics._emit_diagnostic_line"
        ) as emit:
            with observe_process_memory("test_operation"):
                pass

        self.assertEqual(snapshot.call_count, 2)
        message = emit.call_args.args[0]
        self.assertIn("INFO NeoApps memory diagnostic", message)
        self.assertIn("operation=test_operation", message)
        self.assertIn("rss_delta_bytes=40", message)
        self.assertIn("high_water_bytes=240", message)

    def test_operation_sampling_is_rate_limited_per_worker(self):
        self.app.config.update(
            NEOAPPS_MEMORY_DIAGNOSTICS_ENABLED=True,
            NEOAPPS_MEMORY_DIAGNOSTICS_SAMPLE_SECONDS=300,
        )
        snapshot = {
            "rss_bytes": 100,
            "high_water_bytes": 200,
            "pid": 123,
            "python": "3.14.0",
        }
        with patch(
            "app.services.memory_diagnostics.process_memory_snapshot",
            return_value=snapshot,
        ) as snapshots, patch(
            "app.services.memory_diagnostics._emit_diagnostic_line"
        ) as emit:
            with observe_process_memory("test_operation"):
                pass
            with observe_process_memory("test_operation"):
                pass

        self.assertEqual(snapshots.call_count, 2)
        self.assertEqual(emit.call_count, 1)

    def test_checkpoint_uses_the_same_opt_in_gate(self):
        self.app.config["NEOAPPS_MEMORY_DIAGNOSTICS_ENABLED"] = True
        snapshot = {
            "rss_bytes": 100,
            "high_water_bytes": 200,
            "pid": 123,
            "python": "3.14.0",
        }
        with patch(
            "app.services.memory_diagnostics.process_memory_snapshot",
            return_value=snapshot,
        ) as snapshots, patch(
            "app.services.memory_diagnostics._emit_diagnostic_line"
        ) as emit:
            self.assertTrue(
                record_process_memory_checkpoint("test_checkpoint", app=self.app)
            )

        snapshots.assert_called_once()
        self.assertEqual(emit.call_count, 1)
