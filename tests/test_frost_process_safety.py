"""Actual OS-process lease/restart checks in disposable loopback PostgreSQL."""
import multiprocessing
import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from flask import Flask
from app.extensions import db
from app.services import frost_history as history
from tests import test_neosubzero_ucc_postgres as pg


def _batch_process(uri, schema, gateway_id, entered, release, results):
    app = Flask(__name__)
    app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI=uri,
        SQLALCHEMY_ENGINE_OPTIONS={'connect_args': {'options': f'-csearch_path={schema}'}})
    db.init_app(app)

    def slow_batch(*args, **kwargs):
        entered.set()
        if not release.wait(20):
            raise RuntimeError('Isolated batch release timed out')
        return 1

    with app.app_context(), patch.object(history, '_drain', side_effect=slow_batch):
        try:
            results.put((os.getpid(), history.drain(gateway_id)))
        finally:
            db.session.remove()
            db.engine.dispose()


@unittest.skipUnless(pg.URI, 'Requires disposable loopback PostgreSQL')
class FrostProcessSafetyTest(unittest.TestCase):
    setUp = pg.UccPostgresTest.setUp
    drop_schema = pg.UccPostgresTest.drop_schema

    def test_separate_processes_serialize_and_recover_after_hard_stop(self):
        gateway_id = self.fixture.gateway.id
        db.session.remove()
        context = multiprocessing.get_context('spawn')
        entered, release, results = context.Event(), context.Event(), context.Queue()
        args = (pg.URI, self.schema, gateway_id, entered, release, results)
        first, second = context.Process(target=_batch_process, args=args), context.Process(target=_batch_process, args=args)
        processes = [first, second]
        try:
            first.start()
            self.assertTrue(entered.wait(20))
            second.start()
            second.join(20)
            self.assertFalse(second.is_alive())
            loser = results.get(timeout=5)
            self.assertEqual(loser[1], 0)
            release.set()
            first.join(20)
            winner = results.get(timeout=5)
            self.assertEqual(winner[1], 1)
            self.assertNotEqual(winner[0], loser[0])
            self.assertEqual((first.exitcode, second.exitcode), (0, 0))

            # Killing a process inside Event.wait may strand that Event's lock;
            # give the crash fixture independent synchronization objects.
            crash_entered, crash_release = context.Event(), context.Event()
            crashed = context.Process(target=_batch_process,
                args=(pg.URI, self.schema, gateway_id, crash_entered, crash_release, results))
            processes.append(crashed)
            crashed.start()
            self.assertTrue(crash_entered.wait(20))
            crashed.terminate()
            crashed.join(10)
            self.assertFalse(crashed.is_alive())
            batch = history.task(gateway_id, 'worker', 'memory-budget', 'frost-worker-v1')
            self.assertIsNone(history.claim(batch))
            batch = history.task(gateway_id, 'worker', 'memory-budget', 'frost-worker-v1')
            batch.retry_at = datetime.utcnow() - timedelta(seconds=1)
            db.session.commit()
            with patch.object(history, '_drain', return_value=1):
                self.assertEqual(history.drain(gateway_id), 1)
        finally:
            release.set()
            for process in processes:
                if process.is_alive():
                    process.terminate()
                    process.join(5)
            results.close()
            results.join_thread()
