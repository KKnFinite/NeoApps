"""First-insert races and atomic stale rejection in disposable PostgreSQL."""
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from app.extensions import db
from app.models import NeoRainReportEntry
from tests import test_neorain_daily_briefing as daily
from tests import test_neorain_overrides_postgres as pg


@unittest.skipUnless(pg.URI,'Requires disposable loopback PostgreSQL')
class BriefingPostgresTest(unittest.TestCase):
    def setUp(self):
        with patch.object(daily.fixtures,'RainOverrideTest',pg.RainOverridePostgresTest):
            daily.DailyBriefingTest.setUp(self)

    post=daily.DailyBriefingTest.post
    test_atomic_stale_bulk=daily.DailyBriefingTest.test_invalid_or_stale_bulk_save_is_atomic_and_no_schema_change

    def test_concurrent_initial_save_accepts_only_one_version(self):
        with self.client.session_transaction() as session:cookie=dict(session)
        barrier=Barrier(2)
        operation_id=self.operation.id
        def save(value):
            client=self.app.test_client()
            with client.session_transaction() as session:session.update(cookie)
            barrier.wait(timeout=5)
            return client.post('/neorain/daily-briefing/inputs?operation_id='+str(operation_id),
                json={'values':{'briefing_safety':value},'versions':{'briefing_safety':0}}).status_code
        with ThreadPoolExecutor(2) as pool:
            jobs=[pool.submit(save,value) for value in ('First operator','Second operator')]
            self.assertCountEqual([job.result(timeout=20) for job in jobs],[200,409])
        db.session.expire_all()
        row=NeoRainReportEntry.query.filter_by(data_source='manual').one()
        self.assertEqual(row.version,1)
        self.assertIn(row.value,('First operator','Second operator'))
