"""Memory admission and idle work use synthetic weather/storage only."""
import unittest
from datetime import timedelta
from unittest.mock import patch
from flask import g
from app.extensions import db
from app.models import FrostHistoryTask as Task
from app.services import frost_auto as auto, frost_hrrr_point as hrrr
from app.services import frost_resources as priority
from tests import test_frost_learning_reliability_audit as fixtures


class MemoryEfficiencyTest(unittest.TestCase):
    setUp=fixtures.LearningReliabilityTest.setUp
    measure=fixtures.LearningReliabilityTest.measure
    def test_strict_hrrr_admission_precedes_all_weather_downloads(self):
        self.case.acquire.stop()
        g.frost_capture_gateway = self.gateway_id
        with patch.object(hrrr, 'memory_available', return_value=False), \
                patch.object(auto, 'fetch', side_effect=AssertionError('No weather transfers without HRRR admission')) as fetch:
            with self.assertRaisesRegex(priority.Deferred, 'memory'):
                auto.acquire()
        fetch.assert_not_called()

    def test_completed_hour_does_not_claim_worker_or_write(self):
        self.assertEqual(self.case.tick(), 1)
        with patch.object(auto.jobs, 'claim', wraps=auto.jobs.claim) as claim:
            result, counts = self.measure(self.case.tick)
        self.assertEqual(result, 0)
        claim.assert_not_called()
        self.assertEqual(sum(counts[k] for k in ('INSERT','UPDATE','DELETE')), 0, counts)

    def test_not_due_hour_retries_without_worker_claim_or_download(self):
        cycle=auto.Schedule.load(self.case.f.gateway).current_cycle(self.case.t)
        row=auto.jobs.task(self.gateway_id,'auto_capture',auto.identity(cycle.start),auto.VERSION)
        row.status='retry'; row.retry_at=auto.naive(self.case.t+timedelta(minutes=1))
        db.session.commit()
        with patch.object(auto.jobs,'claim',side_effect=AssertionError('No premature lease')):
            self.assertEqual(self.case.tick(), 0)
        self.case.provider.assert_not_called()

    def test_committed_archive_recovery_does_not_require_hrrr_headroom(self):
        with patch.object(self.case.store,'put_object',side_effect=OSError('synthetic outage')):
            self.assertEqual(self.case.tick(), 0)
        row=Task.query.filter_by(kind='auto_capture').one()
        self.assertEqual(row.status,'archive_retry')
        digest=row.input_hash
        self.case.t+=timedelta(minutes=5)
        with patch.object(hrrr,'memory_available',return_value=False):
            self.assertEqual(self.case.tick(), 1)
        self.assertEqual(row.input_hash,digest)
        self.case.provider.assert_called_once()

    def test_one_context_local_client_is_closed_after_capture_and_delivery(self):
        from unittest.mock import Mock
        from app.services import frost_vault as vault
        original=self.case.provider.side_effect
        def source():
            vault.put_json('weather',self.gateway_id,'synthetic-source',{'source':'synthetic'})
            return original()
        self.case.provider.side_effect=source
        self.case.store.close=Mock()
        with patch('app.services.neoscorpion_learning_vault._r2_client',return_value=self.case.store) as factory:
            self.assertEqual(self.case.tick(),1)
        self.assertEqual(factory.call_count,1)
        self.case.store.close.assert_called_once()

    def test_failed_capture_also_closes_its_only_client(self):
        from unittest.mock import Mock
        from app.services import frost_vault as vault
        def fail():
            vault.put_json('weather',self.gateway_id,'synthetic-source',{'source':'synthetic'})
            raise priority.Deferred('Synthetic mid-capture contention')
        self.case.provider.side_effect=fail; self.case.store.close=Mock()
        with patch('app.services.neoscorpion_learning_vault._r2_client',return_value=self.case.store) as factory:
            self.assertEqual(self.case.tick(),0)
        self.assertEqual(factory.call_count,1);self.case.store.close.assert_called_once()
        row=Task.query.filter_by(kind='auto_capture').one()
        self.assertEqual(row.status,'retry');self.assertEqual(row.attempts,0)


class ExcludedFieldTest(unittest.TestCase):
    def test_old_and_new_source_proofs_replay_and_all_model_inputs_match(self):
        import json,tempfile
        from pathlib import Path
        from tests.test_frost_phase14 import native_fixture
        from tests.test_frost_phase12 import ProspectiveTest,NOW
        from scripts import frost_prospective as study
        from app.services import frost_vault as vault, frost_forecast_native as native, frost_model as model
        helper=ProspectiveTest();helper.setUp();self.addCleanup(helper.doCleanups)
        original=hrrr.read_point
        with patch.object(hrrr,'read_point',side_effect=lambda *args,**kwargs:original(*args,exclude_unused=False)):
            old,old_forecasts,bodies=native_fixture(NOW)
        new,new_forecasts,new_bodies=native_fixture(NOW)
        self.assertNotIn('HGT',new['series']);self.assertEqual(new['excluded_variables'],hrrr.EXCLUDED_VARIABLES)
        self.assertEqual(len(old['source_receipts'])-len(new['source_receipts']),5)
        source=helper.source(None)
        for hours in native.HORIZONS:
            target=NOW+timedelta(hours=hours)
            before=native.build(source['observations'],[f for f in old_forecasts if auto.clock.instant(f['valid_at_utc'])<=target],
                cutoff=NOW.isoformat(),target=target.isoformat(),lead_hours=hours)
            after=native.build(source['observations'],[f for f in new_forecasts if auto.clock.instant(f['valid_at_utc'])<=target],
                cutoff=NOW.isoformat(),target=target.isoformat(),lead_hours=hours)
            self.assertEqual(before['features'],after['features']);self.assertEqual(before['missing'],after['missing'])
            self.assertEqual(before['core_available'],after['core_available'])
            for entry in [m for m in helper.baseline['models'] if m['hours']==hours]:
                artifact=json.loads((helper.frozen.parent/'artifacts'/(entry['sha256']+'.json')).read_bytes())
                self.assertEqual(model.score_vector(artifact,before['features'],len(native.FEATURES)),
                    model.score_vector(artifact,after['features'],len(native.FEATURES)))
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'hrrr-sources';root.mkdir()
            for raw in [*bodies.values(),*new_bodies.values()]:(root/(vault.digest(raw)+'.source')).write_bytes(raw)
            for point,forecasts in [(old,old_forecasts),(new,new_forecasts)]:
                study.verify_auto_hrrr(directory,dict(native_point=point,native_forecasts=forecasts),NOW)
            changed=dict(new,excluded_variables={'TMP':'forged exclusion'})
            changed['fingerprint']=vault.digest(vault.canonical({k:v for k,v in changed.items() if k!='fingerprint'}))
            with self.assertRaisesRegex(ValueError,'exclusion'):
                study.verify_auto_hrrr(directory,dict(native_point=changed,native_forecasts=new_forecasts),NOW)


class IdleReclamationTest(unittest.TestCase):
    def test_linux_releases_only_unused_allocator_pages_and_preserves_gc(self):
        with patch.object(priority.sys,'platform','linux'),patch.object(priority,'has_request_context',return_value=False), \
                patch.object(priority,'reason',return_value=None),patch.object(priority.gc,'collect') as collect, \
                patch('ctypes.CDLL') as library:
            library.return_value.malloc_trim.return_value=1
            self.assertTrue(priority.reclaim_memory())
            collect.assert_called_once();library.assert_called_once_with(None)
            library.return_value.malloc_trim.assert_called_once_with(0)

    def test_foreground_recovery_and_non_linux_never_trim(self):
        for platform,request,reason in [('win32',False,None),('linux',True,None),('linux',False,'Operational response recovery')]:
            with patch.object(priority.sys,'platform',platform),patch.object(priority,'has_request_context',return_value=request), \
                    patch.object(priority,'reason',return_value=reason),patch.object(priority.gc,'collect'),patch('ctypes.CDLL') as library:
                self.assertFalse(priority.reclaim_memory());library.assert_not_called()

    def test_unsupported_allocator_does_not_replace_a_verified_result(self):
        with patch.object(priority.sys,'platform','linux'),patch.object(priority,'has_request_context',return_value=False), \
                patch.object(priority,'reason',return_value=None),patch.object(priority.gc,'collect'), \
                patch('ctypes.CDLL',side_effect=OSError('Unsupported allocator')):
            self.assertFalse(priority.reclaim_memory())


if __name__ == '__main__':unittest.main()
