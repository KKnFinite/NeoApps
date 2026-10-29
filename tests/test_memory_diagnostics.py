import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from app import create_app
from app.services.memory_diagnostics import (
    clear_memory_diagnostic_samples,
    observe_process_memory,
    record_process_memory_checkpoint,
    container_memory_snapshot,
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

    def test_process_breakdown_reports_pss_threads_and_anonymous_not_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);group=root/'group';group.mkdir();proc=root/'proc';proc.mkdir()
            (group/'memory.current').write_text('300000000')
            (group/'memory.max').write_text('536870912')
            (group/'memory.stat').write_text('anon 200000000\nfile 50000000\nkernel 50000000\n')
            process=proc/'123';process.mkdir()
            (process/'status').write_text('Name:\tgunicorn\nPPid:\t1\nVmRSS:\t100 kB\nThreads:\t3\n')
            (process/'smaps_rollup').write_text('Pss:\t75 kB\nAnonymous:\t60 kB\nPrivate_Dirty:\t50 kB\nShared_Dirty:\t10 kB\nSwap:\t0 kB\n')
            (process/'environ').write_text('MUST_NOT_READ=secret')
            value=container_memory_snapshot(group,proc)
        self.assertEqual(value['processes'],[dict(pid=123,ppid=1,rss_bytes=102400,name='gunicorn',threads=3,
            pss_bytes=76800,anonymous_bytes=61440,private_dirty_bytes=51200,shared_dirty_bytes=10240,swap_bytes=0)])
        self.assertEqual(value['usage_bytes'],300000000)
        self.assertNotIn('secret',str(value))
