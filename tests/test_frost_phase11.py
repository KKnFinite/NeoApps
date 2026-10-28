"""Isolated fixtures only. No production training, credentials or observations."""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch

from scripts import frost_native_research as research, frost_forecast_native as pipeline
from app.services import frost_forecast_native as native, frost_model as model, frost_vault as vault
from app.services.memory_diagnostics import container_memory_snapshot


class ResearchPolicyTest(unittest.TestCase):
    def rows(self):
        from tests.test_frost_phase9 import NativeContractTest
        obs,fc,kw=NativeContractTest().data();base=native.build(obs,fc,**kw)
        rows=[]
        for year in range(2020,2026):
            for i in range(30):
                at=datetime(year,10,1,tzinfo=timezone.utc)+timedelta(days=i)
                features=list(base['features']);features[native.FEATURES.index('fcst_temperature_c')]=3 if i%2 else -3
                rows.append(dict(base,features=features,season=f'{year}-{year+1}',night=at.date().isoformat(),
                    label=i%2,at=at.isoformat(),event_at=(at+timedelta(hours=1)).isoformat(),lead_hours=1,target=native.TARGET))
        return rows

    def dataset(self):
        return pipeline.sealed(dict(schema=pipeline.SCHEMA,feature_version=native.VERSION,
            implementation_sha256=pipeline.code_hash(),examples=self.rows()))

    def test_small_seasons_allow_exploration_not_independent_approval(self):
        rows=self.rows();gate=research.eligibility(rows)
        self.assertTrue(gate['ready']);self.assertEqual(gate['policy'],'frost-native-exploratory-v2')
        self.assertFalse(gate['operational_approval_eligible'])
        self.assertFalse(pipeline.readiness(rows,None)['ready'])
        self.assertTrue(pipeline.readiness(rows,None)['experimental']['ready'])

    def test_too_few_classes_seasons_and_duplicate_nights_fail(self):
        for rows in (self.rows()[:60],[dict(r,label=0) for r in self.rows()],self.rows()+[self.rows()[0]]):
            self.assertFalse(research.eligibility(rows)['ready'])

    def test_wilson_and_missing_intervals_honest(self):
        self.assertIsNone(research.wilson(0,0));self.assertGreater(research.wilson(0,5)[1],.4)
        self.assertLess(research.wilson(5,5)[0],.6)
        self.assertIsNone(research.describe([])['recall'])

    def test_local_and_request_context_protection(self):
        with patch.dict(os.environ,RENDER='true'):
            with self.assertRaises(RuntimeError):research.evaluate({},1,'blocked')
            with self.assertRaises(RuntimeError):research.eligibility([])
        from flask import Flask
        with Flask('blocked').test_request_context('/'):
            with self.assertRaises(RuntimeError):research.evaluate({},1,'blocked')

    def test_real_fitting_reproducible_and_unpromotable(self):
        dataset=self.dataset()
        report,candidates=research.evaluate(dataset,1,'fixture')
        second,again=research.evaluate(dataset,1,'fixture')
        self.assertEqual(report['summary'],second['summary']);self.assertEqual(candidates,again)
        self.assertEqual(len(report['folds']),4)
        for fold in report['folds']:
            self.assertLess(max(fold['fit_seasons']),fold['evaluation_season'])
        for candidate in candidates:
            self.assertFalse(candidate['approved']);self.assertFalse(candidate['registry_compatible'])
            for check in candidate['parity_checks']:
                self.assertAlmostEqual(model.score_vector(candidate,check['features'],len(native.FEATURES)),check['score'],places=10)
            with self.assertRaises(ValueError):model.decode(vault.canonical(candidate))
        for predictions in report['predictions'].values():
            self.assertEqual(len({r['night'] for r in predictions}),len(predictions))
        self.assertEqual(candidates[0]['preprocessing']['means'][native.FEATURES.index('aircraft_exposure_hours')],0)

    def test_mixed_lead_target_nonfinite_and_tampered_dataset_rejected(self):
        for change in ({'event_at':'2099-01-01T00:00:00Z'},{'target':'physical_frost'},
                {'features':[float('inf')]*len(native.FEATURES)}):
            data=self.dataset();data['examples'][0].update(change)
            with self.assertRaises(ValueError):research.evaluate(pipeline.sealed(data),1,'bad')
        data=self.dataset();data['examples'][0]['label']=1
        with self.assertRaises(ValueError):research.evaluate(data,1,'tampered')

    def test_preprocessing_fits_only_the_training_fold(self):
        data=self.dataset();original=research.fit;means=[]
        def capture(rows,kind,**kw):
            fitted=original(rows,kind,**kw);means.append((len(rows),fitted['preprocessing']['means']))
            return fitted
        with patch.object(research,'fit',side_effect=capture):research.evaluate(data,1,'preprocessing')
        # First forward fold contains only the first two seasons, not test data.
        self.assertEqual(means[0][0],60)
        self.assertEqual(means[0][1][native.FEATURES.index('fcst_temperature_c')],0)

    def test_outcome_refresh_reuses_hashes_retains_revisions_and_rejects_duplicate_pages(self):
        from scripts.frost_local import write_json,read_json
        payload=dict(schema_version='frost-physical-inspection-v1',id='inspection:1',result='present')
        sha=vault.digest(vault.canonical(payload));row=dict(source_id=payload['id'],sha256=sha,archived=True,
            url='/neosubzero/frost-vault/observations/1')
        class Remote:
            def __init__(self):self.bodies=0;self.duplicate=False
            def request(self,method,path):
                if path.endswith('/1'):
                    self.bodies+=1;return vault.canonical(payload)
                return vault.canonical(dict(schema='frost-outcome-catalog-v1',observations=[row,row] if self.duplicate else [row],next_before=None))
        with tempfile.TemporaryDirectory() as folder:
            remote=Remote();pipeline.refresh_outcomes(remote,folder);pipeline.refresh_outcomes(remote,folder)
            self.assertEqual(remote.bodies,1);before=read_json(Path(folder)/'catalog.json')
            remote.duplicate=True
            with self.assertRaises(ValueError):pipeline.refresh_outcomes(remote,folder)
            self.assertEqual(read_json(Path(folder)/'catalog.json'),before)
            self.assertTrue((Path(folder)/'objects'/(sha+'.json')).exists())

    def test_research_page_cannot_claim_approval_or_invalid_metrics(self):
        from app.services import frost_research
        from scripts.frost_local import write_json
        value=frost_research.native_summary()
        self.assertEqual(len(value['experimental_models']),8)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'summary.json'
            for change in ({'approved':True},{'candidate_count':9},
                    {'experimental_models':[dict(value['experimental_models'][0],recall=1.5)]+value['experimental_models'][1:]}):
                write_json(path,dict(value,**change))
                with patch.object(frost_research,'NATIVE_REPORT',path):
                    with self.assertRaises(ValueError):frost_research.native_summary()


class MemoryHardeningTest(unittest.TestCase):
    def test_startup_does_not_import_optimizer_native_dependencies(self):
        code="from app import create_app; import sys; C=type('C',(),{'TESTING':True,'SECRET_KEY':'test','SQLALCHEMY_DATABASE_URI':'sqlite:///:memory:'}); create_app(C); assert 'ortools.sat.python.cp_model' not in sys.modules; assert 'pandas' not in sys.modules"
        result=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,timeout=60)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_cgroup_components_and_pss_not_mislabeled_as_process_rss(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);cg=root/'cg';proc=root/'proc';cg.mkdir();proc.mkdir();pid=proc/'80';pid.mkdir()
            (cg/'memory.current').write_text('400000000');(cg/'memory.max').write_text('536870912')
            (cg/'memory.stat').write_text('anon 250000000\nfile 130000000\nkernel 20000000\n')
            (pid/'status').write_text('Name:\tpython\nPPid:\t1\nVmRSS:\t250000 kB\n')
            (pid/'smaps_rollup').write_text('Pss: 230000 kB\n')
            result=container_memory_snapshot(str(cg),str(proc))
            self.assertEqual(result['components_bytes']['file'],130000000)
            self.assertEqual(result['processes'][0]['rss_bytes'],256000000)
            self.assertEqual(result['processes'][0]['pss_bytes'],235520000)
            self.assertNotIn('Name',json.dumps(result))

    def test_missing_cgroup_diagnostic_is_safe_and_does_not_weaken_guard(self):
        self.assertEqual(container_memory_snapshot('missing','missing'),{})
        from app.services.frost_history import memory_budget
        with patch.object(Path,'read_text',side_effect=OSError):self.assertFalse(memory_budget()['safe'])


if __name__=='__main__':unittest.main()
