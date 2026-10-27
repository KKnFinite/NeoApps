"""Private originals, source-grounded extraction, retry and legacy compatibility."""
import csv
import gzip
import io
import json
import os
import unittest
from datetime import datetime,timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from flask import g
from sqlalchemy import text,inspect

from tests import test_neosubzero_release2 as fixtures
from app.extensions import db
from app.models import FrostDocument,FrostDocumentAudit,NeoSubZeroSprayRecord,Gateway
from app.services import frost_documents as documents,frost_vault as vault
from app.services.frost_xchange import extract,REQUIRED


def sample(**values):
    row={key:'' for key in REQUIRED}
    row.update(RecordID='411983',ApplicationNumber='87446',GatewayCode='RFD',ApplicationDate='9/04/2025',
        TailNumber='N144UP',TruckNumber='831995',Reason='CS',Precipitation='None',AmbientTemp='48.00',DewPoint='25.00',
        LiquidUOM='GAL',TempUOM='F',Type1Used='11.00',Type1SKU='5650',Type1Concentration='63',StartTime1='02:54',EndTime1='02:55',
        Type4Used='2.00',Type4SKU='TEST-SKU',Type4AConcentration='100',StartTime4='02:56',EndTime4='02:57',Notes='Complete\nsource notes')
    row.update(values)
    stream=io.StringIO(newline='');writer=csv.DictWriter(stream,fieldnames=sorted(row));writer.writeheader();writer.writerow(row)
    return stream.getvalue().encode()


class MissingObject(Exception):
    response={'ResponseMetadata':{'HTTPStatusCode':404}}


class MemoryR2:
    def __init__(self):self.objects={};self.writes=0
    def head_object(self,*,Bucket,Key):
        if Key not in self.objects:raise MissingObject()
        body,metadata=self.objects[Key];return {'Metadata':metadata,'ContentLength':len(body)}
    def put_object(self,*,Bucket,Key,Body,Metadata,**kwargs):
        self.objects[Key]=(bytes(Body),dict(Metadata));self.writes+=1
    def get_object(self,*,Bucket,Key):
        if Key not in self.objects:raise MissingObject()
        body,metadata=self.objects[Key];return {'Body':io.BytesIO(body),'Metadata':metadata}


class FrostVaultTest(unittest.TestCase):
    setUp=fixtures.Release2Test.setUp
    client=fixtures.Release2Test.client
    flow=fixtures.Release2Test.flow

    def storage(self):
        client=MemoryR2()
        self.enterContext(patch('app.services.neoscorpion_learning_vault._require_r2_config',return_value={'bucket':'fixture'}))
        self.enterContext(patch('app.services.neoscorpion_learning_vault._r2_client',return_value=client))
        return client

    def actor(self):
        return self.f._user('frost-admin','master')

    def test_standalone_downloads_close_transport_and_nested_writes_reuse_it(self):
        """Authenticated downloads run outside the background batch's scope."""
        from unittest.mock import Mock
        store=self.storage();store.close=Mock()
        key=vault.put_json('training-datasets',self.f.gateway.id,'download-fixture',{'records':[]})
        self.assertEqual(store.close.call_count,1)
        store.close.reset_mock()
        for _ in range(3):
            self.assertEqual(vault.read_json(key,self.f.gateway.id),{'records':[]})
        self.assertEqual(store.close.call_count,3)
        store.close.reset_mock()
        store.get_object=Mock(side_effect=RuntimeError('isolated storage failure'))
        with self.assertRaises(vault.provider.LearningVaultUnavailable):vault.read_json(key,self.f.gateway.id)
        store.close.assert_called_once()

    def test_original_and_extraction_are_private_immutable_verified_and_not_live_spray(self):
        store=self.storage();raw=sample();actor=self.actor()
        document,created=documents.stage(self.f.gateway,actor,'deicing.csv',raw);db.session.commit()
        self.assertTrue(created);self.assertNotEqual(bytes(document.staged_bytes),raw)
        self.assertEqual(documents.original(document),raw)
        self.assertEqual(documents.drain(self.f.gateway.id),1)
        self.assertIsNone(document.staged_bytes)
        self.assertEqual(documents.original(document),raw)
        extracted=vault.read_json(document.extracted_key,self.f.gateway.id)
        self.assertEqual(extracted['rows'][0]['raw']['Notes'],'Complete\nsource notes')
        self.assertEqual(extracted['source_document_id'],document.id)
        self.assertEqual(extracted['source_sha256'],vault.digest(raw))
        self.assertEqual(extracted['rows'][0]['fluids'][1]['quantity'],'2.00')
        self.assertIsNone(extracted['rows'][0]['frost_observed'])
        self.assertFalse(extracted['training_eligible'])
        self.assertTrue(all(key.startswith('frost-vault/v1/') for key in store.objects))
        self.assertEqual(NeoSubZeroSprayRecord.query.count(),0)
        count=store.writes;self.assertEqual(documents.drain(self.f.gateway.id),0);self.assertEqual(count,store.writes)
        duplicate,created=documents.stage(self.f.gateway,actor,'renamed.csv',raw);db.session.commit()
        self.assertFalse(created);self.assertEqual(document.id,duplicate.id);self.assertEqual(FrostDocument.query.count(),1)
        self.assertEqual(FrostDocumentAudit.query.filter_by(action='duplicate_upload_rejected').count(),1)

    def test_archive_outage_keeps_original_and_retries_without_false_success(self):
        document,_=documents.stage(self.f.gateway,self.actor(),'outage.csv',sample());db.session.commit()
        with patch('app.services.frost_vault.put_bytes',side_effect=ValueError('outage')):
            self.assertEqual(documents.drain(self.f.gateway.id),0)
        self.assertIsNone(document.archived_at_utc);self.assertIsNotNone(document.staged_bytes);self.assertTrue(document.last_error)
        document.retry_at=datetime.utcnow()-timedelta(seconds=1);db.session.commit()
        self.storage();self.assertEqual(documents.drain(self.f.gateway.id),1)
        self.assertIsNone(document.last_error);self.assertEqual(documents.original(document),sample())

    def test_reject_paths_binary_malformed_nonfinite_and_wrong_gateway_without_fabrication(self):
        actor=self.actor()
        for filename,raw in [('../a.csv',sample()),('a.exe',sample()),('a.csv',b'\x00bad'),('a.csv',b'a,b\n1\n'),('a.csv',sample(GatewayCode='OTHER'))]:
            with self.subTest(filename=filename):
                with self.assertRaises(ValueError):documents.stage(self.f.gateway,actor,filename,raw)
        parsed=extract(sample(Type1Used='NaN'),gateway_code='RFD')
        self.assertEqual(parsed['status'],'requires_review');self.assertTrue(parsed['issues']);self.assertFalse(parsed['training_eligible'])
        self.assertEqual(FrostDocument.query.count(),0)

    def test_unsupported_csv_preserved_with_truthful_status(self):
        document,_=documents.stage(self.f.gateway,self.actor(),'unknown.csv',b'other,column\nx,y\n');db.session.commit()
        self.assertEqual(document.extraction_status,'unsupported')
        self.assertEqual(documents.original(document),b'other,column\nx,y\n')

    def test_partial_delivery_retry_reuses_original_and_rejects_tampering(self):
        store=self.storage();raw=sample()
        document,_=documents.stage(self.f.gateway,self.actor(),'partial.csv',raw);db.session.commit()
        with patch('app.services.frost_vault.put_json',side_effect=ValueError('temporary outage')):
            self.assertEqual(documents.drain(self.f.gateway.id),0)
        self.assertEqual(store.writes,1);self.assertIsNotNone(document.staged_bytes)
        document.retry_at=datetime.utcnow()-timedelta(seconds=1);db.session.commit()
        self.assertEqual(documents.drain(self.f.gateway.id),1)
        self.assertEqual(store.writes,3)  # Original is verified, not duplicated.
        body,metadata=store.objects[document.original_key]
        store.objects[document.original_key]=(body+b'corruption',metadata)
        with self.assertRaises(ValueError):documents.original(document)

    def test_size_limit_and_partial_fluid_review_preserve_truthful_counts(self):
        from app.services.frost_xchange import MAX_DOCUMENT_BYTES
        with self.assertRaises(ValueError):extract(b'x'*(MAX_DOCUMENT_BYTES+1),gateway_code='RFD')
        parsed=extract(sample(Type4Used='Infinity'),gateway_code='RFD')
        self.assertEqual(parsed['status'],'requires_review')
        self.assertEqual(parsed['contribution_count'],1)
        self.assertEqual(parsed['rows'][0]['raw']['Type4Used'],'Infinity')

    def test_legacy_spray_is_readable_but_spear_and_other_gateway_keys_denied(self):
        store=self.storage();body=vault.canonical({'id':'departure:1:pass:1','tail':'N144UP'})
        key=f'subzero-spray/v1/{self.f.gateway.id}/1/departure/1/pass/1/{vault.digest(body)}.json.gz'
        store.objects[key]=(gzip.compress(body),{'checksum':vault.digest(body)})
        self.assertEqual(vault.read_json(key,self.f.gateway.id)['tail'],'N144UP')
        for invalid in ['learning-record/v1/private.json.gz',key.replace('/'+str(self.f.gateway.id)+'/', '/999/',1),'../'+key]:
            with self.assertRaises(ValueError):vault.read_json(invalid,self.f.gateway.id)
        store.objects[key]=(gzip.compress(b'changed'),{'checksum':vault.digest(body)})
        with self.assertRaises(ValueError):vault.read_json(key,self.f.gateway.id)

    def test_upload_download_permission_csrf_identity_and_duplicate_validation(self):
        client=self.client('master');g.pop('_login_user',None)
        page=client.get('/neosubzero/frost-vault').get_data(as_text=True)
        self.assertIn('aria-label="IMPORT CSV"',page)
        self.assertEqual(page.count('-frost-history-v1'),2)  # Do not reuse old immutable mobile assets.
        self.assertIn('application/vnd.ms-excel,text/plain',page)
        response=client.post('/neosubzero/frost-vault',data={'document':(io.BytesIO(sample()),'source.csv')},headers={'X-Requested-With':'XMLHttpRequest'})
        self.assertEqual(response.status_code,200,response.data)
        document=FrostDocument.query.one()
        self.assertEqual(client.get(f'/neosubzero/frost-vault/documents/{document.id}').data,sample())
        self.assertEqual(client.post('/neosubzero/frost-vault',data={'document':(io.BytesIO(sample()),'same.csv')},headers={'X-Requested-With':'XMLHttpRequest'}).status_code,409)
        watcher=self.client('watcher');g.pop('_login_user',None)
        for path in ['/neosubzero/frost-vault','/neosubzero/frost-vault/status',f'/neosubzero/frost-vault/documents/{document.id}']:
            self.assertEqual(watcher.get(path).status_code,403)
        self.assertEqual(watcher.post('/neosubzero/frost-vault',data={'document':(io.BytesIO(sample()),'forbidden.csv')}).status_code,403)

    def test_additive_bootstrap_preserves_populated_evidence_and_guards_audits(self):
        self.flow();before=NeoSubZeroSprayRecord.query.count();gateway_id=self.f.gateway.id;db.session.remove()
        FrostDocumentAudit.__table__.drop(db.engine);FrostDocument.__table__.drop(db.engine)
        from app.services.schema_sync import sync_database_schema
        for _ in range(2):sync_database_schema(self.f.app);db.session.commit()
        self.assertTrue(inspect(db.engine).has_table('frost_documents'))
        self.assertEqual(NeoSubZeroSprayRecord.query.count(),before)
        document,_=documents.stage(db.session.get(Gateway,gateway_id),self.actor(),'source.csv',sample());db.session.commit()
        with self.assertRaises(Exception):
            db.session.execute(text("UPDATE frost_document_audits SET action='changed'"));db.session.commit()
        db.session.rollback()
        self.assertEqual(FrostDocumentAudit.query.one().action,'import')


class SuppliedXchangeContractTest(unittest.TestCase):
    def test_supplied_season_file_reconciles_exact_rows_units_and_quantities(self):
        path=Path(os.environ.get('FROST_SAMPLE_CSV',r'C:\Users\kknfi\.codex\codex-remote-attachments\01a01dc6-8beb-70b2-bfb7-2622e24a2c5f\C4E2E506-A782-4399-8A01-862BC3683F49\1-deicing_log_RFD-2.csv'))
        if not path.is_file():self.skipTest('User-supplied private season export is not in the repository')
        raw=path.read_bytes();parsed=extract(raw,gateway_code='RFD')
        self.assertEqual(vault.digest(raw),'cb26890bee895c63913ff6eda7a7b146e5496cb6185c850197c66a0c8a7a28cb')
        self.assertEqual(parsed['source_row_count'],3053)
        fluids=[fluid for row in parsed['rows'] for fluid in row['fluids']]
        self.assertEqual(sum(Decimal(f['quantity']) for f in fluids if f['fluid_type']=='TYPE I'),Decimal('150887.00'))
        self.assertEqual(sum(Decimal(f['quantity']) for f in fluids if f['fluid_type']=='TYPE IV'),Decimal('39790.00'))
        self.assertEqual((parsed['date_start'],parsed['date_end']),('2025-09-04','2026-06-25'))
        self.assertTrue(all(f['surface'] is None and f['occurred_at_utc'] is None for f in fluids))
        self.assertTrue(all(row['frost_observed'] is None for row in parsed['rows']))
