"""Forecast-native contracts and compact explicit outcome evidence."""
import copy
import os
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch
from app.services import frost_forecast_native as native,frost_vault as vault,frost_weather as weather
from scripts import frost_forecast_native as pipeline


class NativeContractTest(unittest.TestCase):
    def data(self,lead=1):
        target=datetime(2026,1,2,6,tzinfo=timezone.utc);cutoff=target-timedelta(hours=lead)
        observations=[dict(utc=(cutoff-timedelta(hours=i)).isoformat(),temperature_c=-i/2,dewpoint_c=-2,
            spread_c=2-i/2,relative_humidity=90,wind_kt=2,wind_direction_degrees=180,
            gust_kt=None,cloud_layers=['BKN'],ceiling_ft=1400,clear_sky=False,
            weather_codes='',precipitation_since_hourly_reset_in=0) for i in range(13)]
        def snapshot(valid,t):
            return dict(station=weather.STATION,cycle_at_utc=(cutoff-timedelta(hours=2)).isoformat(),
                issued_at_utc=None,available_by_utc=(cutoff-timedelta(hours=1)).isoformat(),
                valid_at_utc=valid.isoformat(),availability_basis='NOAA_original_object_Last-Modified',source_sha256='a'*64,
                values=dict(temperature_c=t,dewpoint_c=-2,spread_c=t+2,relative_humidity=90,wind_kt=4,
                    wind_direction_degrees=180,gust_kt=8,cloud_cover_percent=50,
                    model_cloud_ceiling_height_m=100,precipitation_1h_mm=None,weather_codes=None))
        snapshots=[snapshot(target-timedelta(hours=1),1),snapshot(target,0)]
        return observations,snapshots,dict(cutoff=cutoff.isoformat(),target=target.isoformat(),lead_hours=lead)

    def test_all_horizons_keep_forecast_and_observed_semantics_separate(self):
        for lead in native.HORIZONS:
            obs,fc,kw=self.data(lead);result=native.build(obs,fc,**kw);v=dict(zip(native.FEATURES,result['features']))
            self.assertEqual(result['contract']['lead_hours'],lead)
            self.assertEqual(v['obs_ceiling_ft'],1400);self.assertNotIn('fcst_ceiling_ft',v)
            self.assertEqual(v['fcst_cloud_cover_percent'],50)
            self.assertIsNone(v['fcst_precipitation_1h_mm']);self.assertIsNone(v['fcst_rain'])
            self.assertIsNone(v['aircraft_exposure_hours']);self.assertIsNone(result['provenance']['parking'])
            self.assertEqual(v['fcst_temperature_change_c'],-1)

    def test_future_observations_and_later_aircraft_context_cannot_leak(self):
        obs,fc,kw=self.data();before=native.build(obs,fc,**kw)
        obs.append(dict(obs[0],utc=kw['target'],temperature_c=-999))
        after=native.build(obs,fc,**kw,aircraft=dict(recorded_at_utc=kw['target'],prior_pretreat=True,exposure_hours=10))
        self.assertEqual(before,after)
        obs[0]['received_at_utc']=kw['target']
        self.assertNotEqual(before['features'],native.build(obs,fc,**kw)['features'])

    def test_time_availability_station_nonfinite_and_horizon_guards(self):
        for field,value in [('available_by_utc','2026-01-02T08:00:00Z'),('issued_at_utc','2026-01-02T08:00:00Z'),('station',dict(weather.STATION,icao='KORD'))]:
            obs,fc,kw=self.data();fc[-1][field]=value
            with self.assertRaises(ValueError):native.build(obs,fc,**kw)
        obs,fc,kw=self.data();fc[-1]['values']['temperature_c']=float('nan')
        with self.assertRaises(ValueError):native.build(obs,fc,**kw)
        obs,fc,kw=self.data();kw['lead_hours']=3
        with self.assertRaises(ValueError):native.build(obs,fc,**kw)
        with self.assertRaises(ValueError):native.contract(True)

    def test_missing_is_not_zero_and_late_trend_is_not_used(self):
        obs,fc,kw=self.data();one=native.build([],fc[-1:],**kw)
        values=dict(zip(native.FEATURES,one['features']))
        self.assertIsNone(values['obs_temperature_c']);self.assertFalse(one['core_available'])
        self.assertIsNone(values['fcst_temperature_change_c'])
        fc[-1]['values'].update(precipitation_1h_mm=0,weather_codes='')
        values=dict(zip(native.FEATURES,native.build(obs,fc,**kw)['features']))
        self.assertEqual(values['fcst_precipitation_1h_mm'],0);self.assertEqual(values['fcst_rain'],0)

    def test_out_of_range_derived_humidity_is_disposed_not_clamped(self):
        obs,fc,kw=self.data();fc[-1]['values']['relative_humidity']=100.2
        result=native.build(obs,fc,**kw)
        self.assertIsNone(result['features'][native.FEATURES.index('fcst_relative_humidity')])
        self.assertEqual(result['quality'][0]['source_value'],100.2)
        self.assertIn('fcst_relative_humidity',result['missing'])

    def test_render_and_web_requests_cannot_prepare_or_train(self):
        with patch.dict(os.environ,RENDER='true'):
            with self.assertRaises(RuntimeError):pipeline.assemble('unused','unused','unused')
            with self.assertRaises(RuntimeError):pipeline.train({},lead_hours=1,test_season='2026-2027',version='candidate')

    def test_source_tampering_and_mixed_revisions_rejected(self):
        from scripts.frost_local import write_json,write_bytes
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);raw=b'weather source';sha=vault.digest(raw)
            cycle=datetime(2026,1,1,tzinfo=timezone.utc)
            point=pipeline.sealed(dict(schema='krfd-hrrr-point-research-v1',station=weather.STATION,
                cycle_at_utc=cycle.isoformat(),point=dict(x_index=1130,y_index=677,latitude=42.203089958,longitude=-89.099534260),
                source_receipts=[dict(sha256=sha,bytes=len(raw),method='GET')]))
            write_json(root/'cycles/2026010100.json',point);write_bytes(root/'sources'/(sha+'.source'),raw)
            self.assertEqual(pipeline.checked_point(root,cycle,{})['fingerprint'],point['fingerprint'])
            write_bytes(root/'sources'/(sha+'.source'),b'changed')
            with self.assertRaisesRegex(ValueError,'hash'):pipeline.checked_point(root,cycle,{})

    def test_reviewed_season_and_single_season_cannot_claim_new_validation(self):
        rows=[dict(season='2025-2026',label=i%2) for i in range(108)]
        gate=pipeline.readiness(rows,'2025-2026')
        self.assertFalse(gate['ready']);self.assertTrue(any('independently' in r for r in gate['reasons']))

    def test_compact_outcomes_remain_separate_from_treatment_class_zero(self):
        from scripts.frost_local import write_json
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            payload=dict(schema_version='frost-outcome-evidence-v1',id='frost:1:outcome:0',
                learning_eligible=True,evidence_active=True,classification={'label':'LIKELY FROST ABSENT'})
            sha=vault.digest(vault.canonical(payload));write_json(root/'objects'/(sha+'.json'),payload)
            write_json(root/'catalog.json',pipeline.sealed(dict(schema='frost-outcome-catalog-v1',observations=[dict(source_id=payload['id'],sha256=sha,archived=True)])))
            population=pipeline.outcome_population(root)
            self.assertFalse(population[0]['native_treatment_target_eligible']);self.assertFalse(population[0]['physical_model_eligible'])
            write_json(root/'objects'/(sha+'.json'),dict(payload,learning_eligible=False))
            with self.assertRaises(ValueError):pipeline.outcome_population(root)

    def test_research_receipt_cannot_claim_approval_or_validated_cases(self):
        from app.services import frost_research as receipt
        self.assertFalse(receipt.native_summary()['approved'])
        with tempfile.TemporaryDirectory() as folder:
            from scripts.frost_local import write_json
            value=receipt.native_summary();value['approved']=True;path=Path(folder)/'receipt.json';write_json(path,value)
            with patch.object(receipt,'NATIVE_REPORT',path):
                with self.assertRaises(ValueError):receipt.native_summary()

    def test_local_training_reproducible_and_season_night_separated(self):
        rows=[]
        obs,fc,kw=self.data();base=native.build(obs,fc,**kw)
        for year in range(2022,2027):
            for i in range(100):
                target=datetime(year,10,1,tzinfo=timezone.utc)+timedelta(days=i)
                label=i%2;features=list(base['features']);features[native.FEATURES.index('fcst_temperature_c')]=label*-4+3
                rows.append(dict(base,features=features,season=f'{year}-{year+1}',night=target.date().isoformat(),label=label,
                    at=(target-timedelta(hours=1)).isoformat(),event_at=target.isoformat(),lead_hours=1))
        dataset=pipeline.sealed(dict(schema=pipeline.SCHEMA,feature_version=native.VERSION,implementation_sha256=pipeline.code_hash(),examples=rows))
        first=pipeline.train(dataset,lead_hours=1,test_season='2026-2027',version='fixture')
        second=pipeline.train(dataset,lead_hours=1,test_season='2026-2027',version='fixture')
        self.assertEqual(first['model'],second['model']);self.assertEqual(first['evaluation'],second['evaluation'])
        self.assertFalse(first['approved']);self.assertFalse(first['physical_frost_probability'])
        from app.services.frost_model import decode
        with self.assertRaises(ValueError):decode(vault.canonical(first))


from tests import test_neosubzero_release2 as release2
from app.extensions import db
from app.models import (SortDateMission,SortDateTailState,SortDateParkingAssignment,NeoSubZeroAuditEntry,NeoSubZeroArchiveDelivery,NeoSubZeroSprayRecord)
from app.services.neosubzero_departure_deice import mutate_departure_deice
from app.services.frost_operational_evidence import payloads
from app.services.neosubzero_archive import drain


class OutcomeTest(unittest.TestCase):
    def setUp(self):
        self.fixture=release2.Release2Test();self.fixture.setUp();self.f=self.fixture.f
        self.addCleanup(self.f.tearDown)
        f=self.f;self.actor=f._user('outcome-admin','simulator')
        now=datetime.utcnow();f.alpha.actual_block_out_datetime_utc=now-timedelta(minutes=5)
        self.arrival=SortDateMission(sort_date=f.operation.sort_date,gateway_code='RFD',sort_name='night',
            sort_date_operation_id=f.operation.id,mission_type='arrival',mission_source='manual',flight_number='5X201',
            origin='SDF',destination='RFD',assigned_tail_number=f.alpha.assigned_tail_number,
            actual_block_in_datetime_utc=now-timedelta(hours=2),arrival_status='arrived',departure_status='scheduled')
        self.state=SortDateTailState(sort_date=f.operation.sort_date,gateway_code='RFD',sort_name='night',
            tail_number=f.alpha.assigned_tail_number,pretreat_status=False)
        parking=SortDateParkingAssignment.query.filter_by(tail_number=f.alpha.assigned_tail_number).one()
        parking.updated_at=now-timedelta(hours=2)
        db.session.add_all([self.arrival,self.state]);db.session.commit()

    def explicit(self):
        event=mutate_departure_deice(self.f.operation,self.f.alpha,'manual_not_sprayed',{},actor_id=self.actor.id)
        db.session.commit();return event

    def test_explicit_outcome_compact_attributed_not_a_spray_and_rolls_back(self):
        f=self.f;event=self.explicit();item=next(iter(payloads(f.operation).values()))
        self.assertEqual(item['tail'],f.alpha.assigned_tail_number);self.assertEqual(item['actor_user_id'],self.actor.id)
        self.assertEqual(item['classification']['label'],'LIKELY FROST ABSENT');self.assertTrue(item['learning_eligible'])
        self.assertEqual(item['parking']['position'],'A03');self.assertFalse(item['physical_frost_confirmed'])
        self.assertNotIn('before_values',item);self.assertEqual(NeoSubZeroSprayRecord.query.count(),0)
        mutate_departure_deice(f.operation,f.alpha,'clear',{},event=event,actor_id=self.actor.id);db.session.rollback()
        self.assertEqual(event.status,'not_sprayed');self.assertEqual(len(payloads(f.operation)),1)

    def test_generic_clear_and_negative_and_missing_or_cancelled_presence_never_create_negatives(self):
        f=self.f
        event=mutate_departure_deice(f.operation,f.alpha,'clear',{},actor_id=self.actor.id);db.session.commit()
        self.assertEqual(payloads(f.operation),{})
        f.alpha.actual_block_out_datetime_utc=None;db.session.commit()
        mutate_departure_deice(f.operation,f.alpha,'initial_contact',{},actor_id=self.actor.id)
        mutate_departure_deice(f.operation,f.alpha,'set_negative',{},actor_id=self.actor.id);db.session.commit()
        self.assertEqual(payloads(f.operation),{})

    def test_prior_pretreat_unknown_exposure_and_tail_swaps_are_not_strong_negatives(self):
        self.state.pretreat_status=True;db.session.commit();self.explicit()
        item=next(iter(payloads(self.f.operation).values()))
        self.assertEqual(item['classification']['label'],'ANTICIPATED FROST RISK')
        self.arrival.tail_updated_at=datetime.utcnow();db.session.commit()
        self.explicit();item=next(iter(payloads(self.f.operation).values()))
        self.assertIsNone(item['exposure_hours']);self.assertFalse(item['learning_eligible'])

    def test_outbox_failure_retry_idempotence_and_later_spray_retraction(self):
        f=self.f;event=self.explicit()
        with patch('app.services.frost_vault.put_json',side_effect=RuntimeError('isolated outage')):
            self.assertEqual(drain(f.gateway.id),0)
        receipt=NeoSubZeroArchiveDelivery.query.one();self.assertIsNone(receipt.delivered_checksum)
        receipt.retry_at=datetime.utcnow()-timedelta(seconds=1);db.session.commit();saved=[]
        def put(category,gateway,identity,payload):saved.append(payload);return 'fixture/key'
        with patch('app.services.frost_vault.put_json',side_effect=put),patch('app.services.neoscorpion_learning_vault.export_subzero_record',return_value={'key':'spray-fixture'}):
            self.assertEqual(drain(f.gateway.id),1);self.assertEqual(drain(f.gateway.id),0)
            event.pass1_started_at_utc=datetime.utcnow();db.session.commit()
            self.assertEqual(drain(f.gateway.id),2)
        self.assertTrue(saved[0]['evidence_active']);self.assertFalse(saved[1]['evidence_active'])
        self.assertEqual(NeoSubZeroArchiveDelivery.query.filter(NeoSubZeroArchiveDelivery.source_id.like('frost:%')).count(),1)

    def test_memory_deferral_never_loses_pending_observation(self):
        self.explicit()
        with patch('app.services.frost_history.memory_allows_work',return_value=False),patch('app.services.frost_vault.put_json') as put:
            self.assertEqual(drain(self.f.gateway.id),0);put.assert_not_called()
        row=NeoSubZeroArchiveDelivery.query.one()
        self.assertIsNone(row.delivered_checksum);self.assertIn('memory safety',row.last_error)

    def test_authorized_bounded_catalog_download_and_permissions(self):
        from flask import g
        f=self.f;self.explicit();client=self.fixture.client('master');g.pop('_login_user',None)
        with patch('app.services.frost_vault.put_json',return_value='fixture/key'):drain(f.gateway.id)
        page=client.get('/neosubzero/frost-vault/observations');self.assertEqual(page.status_code,200)
        entry=page.json['observations'][0];item=next(iter(payloads(f.operation).values()))
        with patch('app.services.frost_vault.read_json',return_value=item),patch('app.services.frost_history.memory_allows_work',return_value=True):
            self.assertEqual(client.get(entry['url']).status_code,200)
        with patch('app.services.frost_history.memory_allows_work',return_value=False):self.assertEqual(client.get(entry['url']).status_code,503)
        watcher=self.fixture.client('watcher')
        g.pop('_login_user',None)
        self.assertEqual(watcher.get('/neosubzero/frost-vault/observations').status_code,403)


from tests import test_neosubzero_ucc_postgres as pg
from types import SimpleNamespace


@unittest.skipUnless(pg.URI,'Requires disposable loopback PostgreSQL')
class PostgresOutcomeTest(OutcomeTest):
    drop_schema=pg.UccPostgresTest.drop_schema

    def setUp(self):
        # Reuse the established UUID-scoped schema fixture and original app,
        # then initialize the same outcome test data under that schema.
        pg.UccPostgresTest.setUp(self)
        existing=self.fixture
        with patch.object(release2.fixtures,'NeoSubZeroUccTest',return_value=existing),patch.object(existing,'setUp'),patch.object(existing,'tearDown'):
            OutcomeTest.setUp(self)

    def test_duplicate_outcome_actions_have_one_delivery_identity(self):
        self.explicit();self.explicit()
        self.assertEqual(len(payloads(self.f.operation)),1)
        with patch('app.services.frost_vault.put_json',return_value='isolated/immutable'):
            self.assertEqual(drain(self.f.gateway.id),1);self.assertEqual(drain(self.f.gateway.id),0)
        self.assertEqual(NeoSubZeroArchiveDelivery.query.count(),1)

    def test_two_coordinators_serialize_explicit_outcomes(self):
        from concurrent.futures import ThreadPoolExecutor
        from app.models import SortDateOperation,NeoSubZeroDepartureDeiceEvent
        operation_id,mission_id,actor_id=self.f.operation.id,self.f.alpha.id,self.actor.id
        db.session.remove()
        def save(_):
            with self.f.app.app_context():
                try:
                    event=mutate_departure_deice(db.session.get(SortDateOperation,operation_id),
                        db.session.get(SortDateMission,mission_id),'manual_not_sprayed',{},actor_id=actor_id)
                    db.session.commit();return event.id
                finally:db.session.remove()
        with ThreadPoolExecutor(max_workers=2) as pool:ids=list(pool.map(save,(0,1)))
        self.assertEqual(len(set(ids)),1)
        self.assertEqual(NeoSubZeroDepartureDeiceEvent.query.count(),1)
        self.assertEqual(len(payloads(db.session.get(SortDateOperation,operation_id))),1)


if __name__=='__main__':unittest.main()
