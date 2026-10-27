"""Complete local evidence and compact production attestation, isolated only."""
import copy
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
from app.extensions import db
from app.models import FrostHistoryTask, FrostDocumentAudit, NeoSubZeroSprayRecord
from app.services import frost_dataset_contract as contract,frost_analysis as analysis,frost_vault as vault,frost_weather as weather,frost_model_registry as registry
from scripts import frost_dataset as assembly,frost_local as local
from tests import test_frost_history as history_tests,test_frost_vault as sources,test_frost_models as models


def local_manifest():
    data=models.synthetic();old=data['manifest'];n=len(data['examples'])
    catalog=contract.seal(dict(gateway_id=1,gateway_code='RFD',documents=[dict(id=1,sha256='a'*64,filename='source.csv',bytes=100,
        source_rows=n,extracted_key='private',verified=True,extraction_status='extracted')]))
    return contract.seal(dict(schema=contract.VERSION,version=analysis.VERSION,time_contract=analysis.TIME_CONTRACT,
        parser_version=contract.PARSER_VERSION,weather_version=weather.VERSION,implementation_sha256=contract.code_hash(),
        source_origin='authenticated_frost_vault',source_catalog=catalog,ready=True,state='LOCAL DATA READY',
        catalog=dict(source_rows=n,deduplicated_records=n,overlapping_rows=0,revisions=0,revision_conflicts=0,seasons=old['catalog']['seasons']),
        partitions=[dict(month=s[:4]+'-12',records=120,sha256='b'*64,source_rows_sha256='c'*64) for s in old['catalog']['seasons']],
        quality=dict(weather_matched=n,weather_unmatched=0,incomplete_window=0,requires_review=0,
            classifications={'LIKELY FROST PRESENT':n//2,'NON-FROST TREATMENT':n//2},exclusions=[]),weather=[],
        eligibility=dict(examples=n,classes={'0':n//2,'1':n//2},examples_sha256=vault.digest(vault.canonical(data['examples'])),lead_hours=0,excluded={})))


class DatasetTest(unittest.TestCase):
    def test_incremental_refresh_reuses_original_hashes_and_does_not_replace_local_readiness(self):
        raw=sources.sample();digest=vault.digest(raw)
        source=contract.seal(dict(gateway_id=1,gateway_code='RFD',documents=[dict(id=1,sha256=digest,bytes=len(raw),verified=True)]))
        remote_manifest=models.synthetic()['manifest'];remote_manifest['source_catalog']=source
        remote_manifest=contract.seal(remote_manifest)
        class Remote:
            downloads=0
            def request(self,method,path):
                if path.endswith('dataset-manifest'):return vault.canonical({'manifest':remote_manifest})
                self.downloads+=1;return raw
        remote=Remote()
        with tempfile.TemporaryDirectory() as directory:
            local.write_json(Path(directory)/'manifest.json',{'local_pointer':'preserve'})
            local.refresh(remote,directory);local.refresh(remote,directory)
            self.assertEqual(remote.downloads,1)
            self.assertEqual(local.read_json(Path(directory)/'manifest.json'),{'local_pointer':'preserve'})
            self.assertEqual((Path(directory)/'originals'/(digest+'.csv')).read_bytes(),raw)

    def test_readiness_requires_hashes_counts_quality_dispositions_and_code_not_true_flag(self):
        original=local_manifest();contract.validate(original,training=True)
        for mutate in (lambda m:m['catalog'].update(deduplicated_records=599),lambda m:m['quality'].update(weather_unmatched=2),
                lambda m:m.update(implementation_sha256='0'*64),lambda m:m['source_catalog']['documents'][0].update(verified=False),
                lambda m:m['eligibility']['classes'].update({'1':0})):
            bad=copy.deepcopy(original);mutate(bad);bad=contract.seal(bad)
            with self.assertRaises(ValueError):contract.validate(bad,training=True)
        bad=copy.deepcopy(original);bad['quality'].update(weather_matched=598,weather_unmatched=2)
        bad['quality']['exclusions']=[dict(record_id=str(i),reason='weather_unmatched',sources=[{'document_id':1,'document_sha256':'a'*64,'source_row':i+2}],disposition='excluded; no fabricated weather') for i in (0,1)]
        bad['eligibility'].update(examples=598,classes={'0':299,'1':299})
        contract.validate(contract.seal(bad),training=True)

    def test_complete_assembly_reuses_archived_and_local_results_without_weather_or_analysis(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);originals=root/'originals';originals.mkdir();cached=root/'cached';cached.mkdir()
            docs=[];all_rows=[]
            for i,day,reason in ((1,'1/02/2025','F'),(2,'2/02/2025','SN')):
                raw=sources.sample(RecordID=str(i),ApplicationDate=day,Reason=reason)
                name=str(i)+'.csv';(originals/name).write_bytes(raw);digest=vault.digest(raw)
                docs.append(dict(id=i,filename=name,bytes=len(raw),sha256=digest,source_rows=1,extracted_key='private/'+str(i),verified=True,extraction_status='extracted'))
                parsed=sources.extract(raw,gateway_code='RFD')['rows'][0]
                row=analysis.compact_row(parsed,SimpleNamespace(id=i,sha256=digest,extracted_key='private/'+str(i)))
                selected,_=analysis.deduplicate([row]);all_rows+=selected
                start=datetime(2025,i,1,tzinfo=timezone.utc);end=start+timedelta(days=4)
                result=analysis.analyze(selected,weather.parse(history_tests.weather_csv(start,end),start,end))
                if i==1:
                    objects=cached/'objects';objects.mkdir();local.write_json(objects/(vault.digest(vault.canonical(result))+'.json'),result)
                else:
                    with zipfile.ZipFile(cached/'result-2025-02-existing.zip','w') as archive:archive.writestr('result.json',vault.canonical(result))
            catalog=contract.seal(dict(gateway_id=1,gateway_code='RFD',documents=docs))
            with patch.object(analysis,'analyze',side_effect=AssertionError('Repeated analysis')),patch.object(weather,'fetch',side_effect=AssertionError('Repeated weather retrieval')):
                manifest=assembly.assemble(root/'complete',catalog,originals,caches=[cached])
                self.assertEqual(manifest['assembly'],{'reused_records':2,'reconstructed_records':0})
                prepared=local.prepare(root/'complete');self.assertEqual(len(prepared['examples']),2)
                self.assertEqual(prepared['manifest']['eligibility']['classes'],{'0':1,'1':1})
                # A second assembly verifies checksums and source rows, never redoes features.
                assembly.assemble(root/'complete',catalog,originals,caches=[cached])
            # An overlapping export adds provenance, not another event. Only
            # the changed row is reconciled, never the unrelated February row.
            duplicate=(originals/'1.csv').read_bytes();(originals/'3.csv').write_bytes(duplicate)
            docs.append(dict(docs[0],id=3,filename='3.csv',extracted_key='private/3'))
            expanded=contract.seal(dict(gateway_id=1,gateway_code='RFD',documents=docs))
            original_analyze=analysis.analyze;calls=[]
            def track(records,observed):calls.append(len(records));return original_analyze(records,observed)
            with patch.object(analysis,'analyze',side_effect=track),patch.object(weather,'fetch',side_effect=lambda start,end:(history_tests.weather_csv(start,end),'fixture')):
                changed=assembly.assemble(root/'complete',expanded,originals,caches=[cached])
            self.assertEqual(calls,[1]);self.assertEqual(changed['catalog']['deduplicated_records'],2)
            self.assertEqual(changed['catalog']['overlapping_rows'],1)
            (originals/'1.csv').write_bytes(b'corrupt')
            with self.assertRaisesRegex(ValueError,'Original hash'):assembly.assemble(root/'complete',expanded,originals,caches=[cached])

    def test_render_rejects_assembly_before_any_source_access(self):
        import os
        with patch.dict(os.environ,{'RENDER':'true'}):
            with self.assertRaises(RuntimeError):assembly.assemble('not-opened',{},'not-opened')


class LocalRegistryTest(models.RegistryTest):
    def local_candidate(self):
        artifact=models.candidate();manifest=local_manifest()
        artifact['local_dataset_manifest']=manifest
        artifact['dataset'].update(fingerprint=manifest['fingerprint'])
        return artifact,manifest

    def test_compact_local_candidate_ignores_production_processing_but_checks_live_sources(self):
        artifact,manifest=self.local_candidate()
        with patch.object(registry,'source_catalog',return_value=manifest['source_catalog']),patch.object(registry,'dataset_manifest',side_effect=AssertionError('Production readiness dependency')):
            row=registry.register(self.gw,self.actor.id,vault.canonical(artifact));self.assertEqual(row.status,'validated')
            self.assertIsNone(registry.current_release(self.gw))
            registry.release(self.gw,self.actor.id,row.id,0,'Isolated explicit local review')
        changed=copy.deepcopy(manifest['source_catalog']);changed['documents'][0]['sha256']='e'*64;changed=contract.seal(changed)
        with patch.object(registry,'source_catalog',return_value=changed):
            with self.assertRaisesRegex(ValueError,'catalog changed'):registry.verify_local(artifact,self.gw)
        self.assertEqual(NeoSubZeroSprayRecord.query.count(),0)

    def test_retirement_is_audited_idempotent_preserves_pointers_retries_and_archive(self):
        pending=FrostHistoryTask(gateway_id=self.gw,kind='partition',identity='2025-02',version=analysis.VERSION,status='pending',summary={'records':123},source_key='original-source',input_hash='a'*64)
        archived=FrostHistoryTask(gateway_id=self.gw,kind='partition',identity='2025-01',version=analysis.VERSION,status='ready',summary={'records':321},result_key='original-result')
        # An immutable source document supplies the existing audit mechanism.
        self.base.stage();db.session.add_all([pending,archived]);db.session.commit();retry=pending.retry_at
        records=registry.retire_publication(self.gw,self.actor.id)
        self.assertEqual([r['id'] for r in records],[pending.id]);self.assertEqual(pending.status,'local_only')
        self.assertEqual(pending.retry_at,retry);self.assertEqual(pending.source_key,'original-source')
        self.assertEqual(archived.result_key,'original-result');self.assertEqual(archived.status,'ready')
        self.assertEqual(registry.retire_publication(self.gw,self.actor.id),[])
        self.assertEqual(FrostDocumentAudit.query.filter_by(action='historical_publication_retired').count(),1)
        self.assertEqual(NeoSubZeroSprayRecord.query.count(),0)

    def test_memory_guard_applies_to_small_models_and_retired_routes_are_gone(self):
        with patch.object(registry.frost_history,'memory_allows_work',return_value=False):
            with self.assertRaisesRegex(RuntimeError,'headroom'):registry.register(self.gw,self.actor.id,b'{}')
        admin=self.base.client('master')
        from flask import g
        with patch.object(registry.frost_history,'memory_allows_work',return_value=False):
            g.pop('_login_user',None)
            response=admin.post('/neosubzero/frost-vault/models',data={'action':'import','model':(io.BytesIO(b'{}'),'model.json')},content_type='multipart/form-data')
            self.assertEqual(response.status_code,503);self.assertIn('headroom',response.json['error'])
        g.pop('_login_user',None);self.assertEqual(admin.get('/neosubzero/frost-vault/backfill/1').status_code,410)
        g.pop('_login_user',None);self.assertEqual(admin.post('/neosubzero/frost-vault/backfill/publish').status_code,410)


@unittest.skipUnless(models.pg.URI,'Requires disposable PostgreSQL')
class LocalRegistryPostgresTest(LocalRegistryTest):
    setUp=models.RegistryPostgresTest.setUp
    drop_schema=models.RegistryPostgresTest.drop_schema
