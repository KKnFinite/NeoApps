"""Optional direct evidence and resumable LOCAL multi-season forecast coverage."""
import copy
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4
from app.extensions import db
from app.models import (NeoSubZeroAuditEntry, NeoSubZeroArchiveDelivery, NeoSubZeroSprayRecord,
    SortDateMission, SortDateOperation, SortDateParkingAssignment)
from app.services import frost_inspections as inspection, frost_vault as vault, neosubzero_weather as weather
from tests import test_neosubzero_release2 as release2, test_neosubzero_ucc_postgres as pg
from app.services.neosubzero_departure_deice import departure_deice_revision, mutate_departure_deice
from app.services.neosubzero_archive import drain


class InspectionTest(unittest.TestCase):
    def setUp(self):
        self.fixture=release2.Release2Test();self.fixture.setUp();self.f=self.fixture.f
        self.addCleanup(self.f.tearDown)
        self.actor=self.f._user('inspection-admin','simulator')
        self.values=dict(physical_tail=self.f.alpha.assigned_tail_number, inspection_request_id=str(uuid4()),
            frost_result='present', inspection_confirmed='yes', inspected_local=self.f.operation.sort_date.isoformat()+'T12:00',
            inspection_location='Alpha actual aircraft inspection', expected_inspection_id='')

    def save(self, **changes):
        entry=inspection.record(self.f.operation,self.f.alpha,dict(self.values,**changes),actor_id=self.actor.id)
        db.session.commit();return entry

    def test_direct_results_are_physical_evidence_not_spray_or_reason_class(self):
        revision=departure_deice_revision(self.f.gateway,self.f.operation)
        entry=self.save();item=next(iter(inspection.payloads(self.f.operation).values()))
        self.assertEqual(item['tail'],self.f.alpha.assigned_tail_number)
        self.assertTrue(item['physical_frost_confirmed']);self.assertEqual(item['observer_user_id'],self.actor.id)
        self.assertFalse(item['treatment_reason_target_eligible']);self.assertIsNone(item['weather'])
        self.assertIsNone(item['parking']);self.assertIsNone(item['prior_pretreat']);self.assertIsNone(item['exposure_hours'])
        self.assertEqual(NeoSubZeroSprayRecord.query.count(),0)
        self.assertNotEqual(revision,departure_deice_revision(self.f.gateway,self.f.operation))
        for result in ('absent','uncertain'):
            entry=self.save(frost_result=result,inspection_request_id=str(uuid4()),expected_inspection_id=str(entry.id))
            value=entry.after_values['inspection']
            self.assertEqual(value['learning_eligible'],result!='uncertain')
            self.assertEqual(value['physical_frost_confirmed'],False if result=='absent' else None)

    def test_confirmation_timestamp_dst_and_missing_inputs_rejected(self):
        for changes in (dict(frost_result=''),dict(inspection_confirmed=''),dict(inspected_local='bad'),
                dict(inspected_local='2026-11-01T01:30'),dict(inspected_local='2026-03-08T02:30'),
                dict(inspected_local='2099-01-01T12:00'),dict(inspection_location=''),dict(inspection_notes='x'*501)):
            with self.assertRaises(ValueError):self.save(**changes)
            db.session.rollback()
        self.assertEqual(NeoSubZeroAuditEntry.query.filter_by(action=inspection.ACTION).count(),0)

    def test_idempotent_retry_and_conflicting_nonce_and_stale_form(self):
        first=self.save();self.assertEqual(first.id,self.save().id)
        for changes in (dict(frost_result='absent'),dict(inspection_request_id=str(uuid4()))):
            with self.assertRaises(ValueError):self.save(**changes)
            db.session.rollback()
        self.assertEqual(NeoSubZeroAuditEntry.query.filter_by(action=inspection.ACTION).count(),1)

    def test_correction_retains_original_and_tail_swap_never_reattributes(self):
        first=self.save();original=copy.deepcopy(first.after_values)
        second=self.save(inspection_request_id=str(uuid4()),inspection_action='correct',
            expected_inspection_id=str(first.id),frost_result='absent',correction_reason='Reviewed physical inspection notes')
        self.assertEqual(first.after_values,original)
        values=list(inspection.payloads(self.f.operation).values())
        self.assertEqual(len(values),2);self.assertFalse(values[0]['evidence_active']);self.assertTrue(values[1]['evidence_active'])
        self.assertEqual(values[1]['previous_audit_id'],first.id)
        population=inspection.population(self.f.gateway)
        self.assertEqual(population['inspections'],1);self.assertEqual(population['physical_present'],0)
        self.assertEqual(population['physical_absent'],1);self.assertFalse(population['physical_model_ready'])
        self.f.alpha.assigned_tail_number='NREPLACEMENT';db.session.commit()
        with self.assertRaises(ValueError):self.save()
        db.session.rollback()
        self.assertNotEqual(list(inspection.payloads(self.f.operation).values())[1]['tail'],self.f.alpha.assigned_tail_number)
        self.assertEqual(second.after_values['inspection']['original_mission_id'],self.f.alpha.id)

    def test_completed_aircraft_can_be_inspected_without_resetting_clear(self):
        event=mutate_departure_deice(self.f.operation,self.f.alpha,'clear',{},actor_id=self.actor.id);db.session.commit()
        cleared=event.cleared_at_utc;self.save()
        self.assertEqual(event.status,'cleared');self.assertEqual(event.cleared_at_utc,cleared)
        self.assertIsNone(event.pass1_started_at_utc);self.assertEqual(NeoSubZeroSprayRecord.query.count(),0)

    def test_absent_after_actual_pretreat_remains_prevention_confounded(self):
        from app.models import NeoSubZeroPretreatState
        state=NeoSubZeroPretreatState(sort_date_operation_id=self.f.operation.id,tail_number=self.f.alpha.assigned_tail_number,
            pass1_started_at_utc=datetime.combine(self.f.operation.sort_date,datetime.min.time()))
        db.session.add(state);db.session.commit();entry=self.save(frost_result='absent')
        self.assertTrue(entry.after_values['inspection']['prior_pretreat'])
        self.assertEqual(entry.after_values['inspection']['prior_pretreat_evidence']['state_id'],state.id)
        self.assertEqual(inspection.population(self.f.gateway)['prior_pretreated'],1)
        self.assertFalse(entry.after_values['inspection']['treatment_reason_target_eligible'])

    def test_verified_parking_requires_explicit_credible_canonical_identity(self):
        parking=SortDateParkingAssignment.query.filter_by(tail_number=self.f.alpha.assigned_tail_number).one()
        with self.assertRaises(ValueError):self.save(verify_parking='yes')
        db.session.rollback();parking.updated_at=datetime.combine(self.f.operation.sort_date,datetime.min.time())
        db.session.commit();entry=self.save(verify_parking='yes')
        self.assertEqual(entry.after_values['inspection']['parking']['position'],'A03')

    def test_block_out_makes_prior_parking_insufficient_but_never_blocks_inspection(self):
        parking=SortDateParkingAssignment.query.filter_by(tail_number=self.f.alpha.assigned_tail_number).one()
        start=datetime.combine(self.f.operation.sort_date,datetime.min.time())
        parking.updated_at=start;self.f.alpha.actual_block_out_datetime_utc=start+timedelta(hours=1);db.session.commit()
        with self.assertRaisesRegex(ValueError,'blocked out'):self.save(verify_parking='yes')
        db.session.rollback();entry=self.save(inspection_location='Deice Pad')
        self.assertIsNone(entry.after_values['inspection']['parking'])
        self.assertIsNotNone(entry.after_values['inspection']['block_out_at_utc'])

    def test_later_tail_assignment_cannot_prove_historical_mission_context(self):
        self.f.alpha.tail_updated_at=datetime.now(timezone.utc).replace(tzinfo=None);db.session.commit()
        with self.assertRaisesRegex(ValueError,'tail changed after'):self.save()
        db.session.rollback()

    def test_outbox_retries_and_preserves_every_revision_before_first_delivery(self):
        first=self.save();self.save(inspection_request_id=str(uuid4()),inspection_action='correct',
            expected_inspection_id=str(first.id),frost_result='absent',correction_reason='Actual inspection correction')
        with patch('app.services.frost_history.memory_allows_work',return_value=False),patch('app.services.frost_vault.put_json') as put:
            self.assertEqual(drain(self.f.gateway.id),0);put.assert_not_called()
        self.assertEqual(NeoSubZeroArchiveDelivery.query.count(),2)
        for row in NeoSubZeroArchiveDelivery.query.all():row.retry_at=datetime.now(timezone.utc).replace(tzinfo=None)-timedelta(seconds=1)
        db.session.commit();saved=[]
        def put(category,gateway,identity,payload):saved.append(payload);return 'fixture/inspection'
        with patch('app.services.frost_history.memory_allows_work',return_value=True),patch('app.services.frost_vault.put_json',side_effect=put):
            self.assertEqual(drain(self.f.gateway.id),2);self.assertEqual(drain(self.f.gateway.id),0)
        self.assertEqual([r['result'] for r in saved],['present','absent'])
        self.assertTrue(all(r.delivered_checksum==r.content_checksum for r in NeoSubZeroArchiveDelivery.query.all()))

    def test_route_permissions_current_sort_tail_and_operation_transport(self):
        client=self.fixture.client();data=dict(self.values,operation_id=self.f.operation.id,mission_id=self.f.alpha.id)
        self.assertEqual(self.fixture.post(client,'frost-inspection',**data).status_code,200)
        self.assertEqual(self.fixture.post(client,'frost-inspection',**dict(data,physical_tail='NWRONG')).status_code,409)
        self.assertEqual(self.fixture.post(client,'frost-inspection',**dict(data,operation_id=-1)).status_code,409)
        watcher=self.fixture.client('watcher')
        self.assertEqual(self.fixture.post(watcher,'frost-inspection',**data).status_code,403)

    def test_changed_assets_have_new_immutable_cache_identities(self):
        client=self.fixture.client()
        with patch('app.neonodes.neosubzero.routes.current_neosubzero_operation',return_value=self.f.operation):
            response=client.get('/neosubzero/operations?mode=outbound')
        self.assertEqual(response.status_code,200)
        body=response.get_data(as_text=True)
        self.assertIn('neosubzero_operations.js?v=20261028-frost-prospective-v1',body)
        self.assertIn('ucc=20261028-frost-inspections-v1',body)
        self.assertNotIn('neosubzero_operations.js?v=20261023-release2-v1',body)


class WeatherAndCollectionTest(unittest.TestCase):
    def test_original_comparison_uses_actual_precision_not_unbounded_tolerance(self):
        from scripts import frost_hrrr as hrrr
        self.assertAlmostEqual(hrrr.quantization_tolerance('<f2',273.15),.126,places=6)
        self.assertLess(hrrr.quantization_tolerance('<f4',273.15),.0011)
        for dtype in ('|O','<i8'):
            with self.assertRaises(ValueError):hrrr.quantization_tolerance(dtype,273.15)
        with self.assertRaises(ValueError):hrrr.quantization_tolerance('<f2',float('nan'))

    def test_research_coverage_cannot_count_duplicate_nights_or_claim_validation(self):
        from scripts import frost_forecast_native as pipeline
        row=dict(lead_hours=1,night='2024-01-01',season='2023-2024',label=1,missing=['fcst_rain'])
        value=dict(implementation_sha256=pipeline.code_hash(),examples=[row],source_catalog='a'*64,
            excluded={},confirmed_physical_positives=0,credible_physical_negatives=0)
        report=pipeline.coverage_report(pipeline.sealed(value))
        self.assertFalse(report['approved']);self.assertEqual(report['horizons'][1]['validated_cases'],0)
        self.assertFalse(report['horizons'][1]['training']['ready'])
        value['examples']=[row,row]
        with self.assertRaisesRegex(ValueError,'Duplicate operational night'):pipeline.coverage_report(pipeline.sealed(value))

    def test_weather_reference_only_preceding_cached_krfd_no_network(self):
        at=datetime(2026,1,1,6,tzinfo=timezone.utc)
        row=dict(icaoId='KRFD',reportTime='2026-01-01T05:00:00Z',rawOb='KRFD 010500Z 00000KT CLR M01/M02 A3000',temp=-1,dewp=-2)
        with patch.dict(weather._CACHE,{'krfd_metar':dict(value=[row],fetched_at=at)},clear=True),patch.object(weather,'_fetch_metar') as fetch:
            value=inspection.cached_weather(at);self.assertEqual(value['source_sha256'],vault.digest(vault.canonical(row)))
            self.assertIsNone(inspection.cached_weather(at+timedelta(minutes=31)))
            self.assertIsNone(inspection.cached_weather(at-timedelta(hours=2)))
            row['icaoId']='KORD';self.assertIsNone(inspection.cached_weather(at));fetch.assert_not_called()

    def test_incremental_cases_preserve_prior_selection_and_original_manifest(self):
        from scripts import frost_hrrr as hrrr
        from scripts.frost_local import write_json,read_json
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);event=dict(group_id='new',event_at='2024-01-02T06:00:00+00:00',night='2024-01-01',season='2023-2024',label=1,sources=[])
            write_json(root/'prepared.json',dict(examples=[event]))
            prior=dict(source_dataset_fingerprint='a'*64,cases=[dict(group_id='old',warning_hours=1,input_lead_hours=0,night='2023-12-01',season='2023-2024')],gaps=[])
            write_json(root/'cases.json',prior)
            point=dict(forecast_hours=[1,2,3,4,5,6,7,8,9],series={},point={})
            fake=SimpleNamespace(root=root,manifest=dict(fingerprint='a'*64))
            client=SimpleNamespace(close=lambda:None,receipts=[],get=lambda *a,**k:(b'',dict(last_modified=None)),prefetch=lambda *a,**k:None)
            with patch('scripts.frost_forecast_validation.Evidence',return_value=fake),patch.object(hrrr,'Sources',return_value=client),patch.object(hrrr,'cycle_point',side_effect=lambda *a:copy.deepcopy(point)),patch.object(hrrr,'normalized',return_value={}):
                hrrr.collect(root,root,nights=3,season='2023-2024',native_hours=True)
            current=read_json(root/'cases.json')
            self.assertTrue(any(r['group_id']=='old' for r in current['cases']))
            self.assertEqual(read_json(root/'case-manifests'/(vault.digest(vault.canonical(prior))+'.json')),prior)
            self.assertFalse(current['operationally_validated'])

    def test_interrupted_collection_checkpoints_and_resumes_without_duplicates(self):
        from scripts import frost_hrrr as hrrr
        from scripts.frost_local import write_json,read_json
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);event=dict(group_id='new',event_at='2024-01-02T06:00:00+00:00',night='2024-01-01',season='2023-2024',label=1,sources=[])
            write_json(root/'prepared.json',dict(examples=[event]))
            point=dict(forecast_hours=list(range(1,10)),series={},point={})
            fake=SimpleNamespace(root=root,manifest=dict(fingerprint='a'*64))
            client=SimpleNamespace(close=Mock(),receipts=[],get=lambda *a,**k:(b'',dict(last_modified=None)),prefetch=lambda *a,**k:None)
            with patch('scripts.frost_forecast_validation.Evidence',return_value=fake),patch.object(hrrr,'Sources',return_value=client),patch.object(hrrr,'normalized',return_value={}):
                with patch.object(hrrr,'cycle_point',side_effect=[copy.deepcopy(point),RuntimeError('isolated bounded request failure')]):
                    with self.assertRaises(RuntimeError):hrrr.collect(root,root,nights=3,season='2023-2024',native_hours=True)
                self.assertEqual(len(read_json(root/'cases.json')['cases']),2)
                with patch.object(hrrr,'cycle_point',side_effect=lambda *a:copy.deepcopy(point)):
                    hrrr.collect(root,root,nights=3,season='2023-2024',native_hours=True)
            rows=read_json(root/'cases.json')['cases']
            self.assertEqual(len(rows),6);self.assertEqual(len({(r['group_id'],r['warning_hours'],r['input_lead_hours']) for r in rows}),6)
            self.assertEqual(client.close.call_count,2)


@unittest.skipUnless(pg.URI,'Requires disposable loopback PostgreSQL')
class PostgresInspectionTest(InspectionTest):
    drop_schema=pg.UccPostgresTest.drop_schema
    def setUp(self):
        pg.UccPostgresTest.setUp(self);existing=self.fixture
        with patch.object(release2.fixtures,'NeoSubZeroUccTest',return_value=existing),patch.object(existing,'setUp'),patch.object(existing,'tearDown'):
            InspectionTest.setUp(self)

    def test_two_concurrent_inspectors_cannot_overwrite(self):
        from concurrent.futures import ThreadPoolExecutor
        op_id,mission_id,actor_id=self.f.operation.id,self.f.alpha.id,self.actor.id
        db.session.remove()
        def worker(result):
            with self.f.app.app_context():
                try:
                    entry=inspection.record(db.session.get(SortDateOperation,op_id),db.session.get(SortDateMission,mission_id),
                        dict(self.values,frost_result=result,inspection_request_id=str(uuid4())),actor_id=actor_id)
                    db.session.commit();return 'saved'
                except ValueError:db.session.rollback();return 'conflict'
                finally:db.session.remove()
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(worker,('present','absent')))
        self.assertCountEqual(results,['saved','conflict'])
        self.assertEqual(NeoSubZeroAuditEntry.query.filter_by(action=inspection.ACTION).count(),1)
