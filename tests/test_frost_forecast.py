"""Research-only forecast provenance, leakage and bounded local execution."""
import copy
import csv
import io
import os
import tempfile
import unittest
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app.services import frost_forecast as f, frost_analysis as a, frost_model as model, frost_weather as weather
from scripts import frost_forecast_validation as cli
from app.services import frost_research

UTC = timezone.utc
AT = datetime(2025, 12, 1, 6, tzinfo=UTC)
PRODUCT = '202512010529-KLOT-FTUS43-TAFRFD'
BULLETIN = b'FTUS43 KLOT 010529\nTAFRFD\nTAF\nKRFD 010529Z 0106/0206 18005KT P6SM SCT035\n FM011200 19008KT P6SM BKN015='


def csv_taf(**changes):
    row = dict(station='KRFD',valid='2025-12-01 05:29',fx_valid='2025-12-01 06:00',raw='18005KT P6SM SCT035',
        product_id=PRODUCT,ftype='Forecast',skyc="['SCT']",skyl='[3500]',presentwx='[]',sknt='5',drct='180',gust='',visibility='6.01')
    row.update(changes)
    output = io.StringIO()
    writer = csv.DictWriter(output,fieldnames=list(row));writer.writeheader();writer.writerow(row)
    return output.getvalue().encode()


class ForecastTest(unittest.TestCase):
    def test_exact_station_contract_rejects_other_airport_or_coordinates(self):
        f.station(weather.STATION)
        for key,value in [('icao','KORD'),('provider_id','ORD'),('network','IA_ASOS'),('latitude',42.2),('longitude',-89.0),('timezone','UTC')]:
            bad=dict(weather.STATION);bad[key]=value
            with self.assertRaises(ValueError):f.station(bad)
        with self.assertRaises(ValueError):f.parse_taf(csv_taf(station='KORD'))

    def test_issue_and_valid_are_distinct_and_verified_against_original(self):
        row=f.parse_taf(csv_taf())[0]
        self.assertEqual(row['issued_at_utc'],'2025-12-01T05:29:00+00:00')
        self.assertEqual(row['valid_at_utc'],'2025-12-01T06:00:00+00:00')
        self.assertEqual(f.verify_bulletin(BULLETIN,PRODUCT)['end'],'2025-12-02T06:00:00+00:00')
        with self.assertRaises(ValueError):f.parse_taf(csv_taf(valid='2025-12-01 00:00'))
        with self.assertRaises(ValueError):f.verify_bulletin(BULLETIN.replace(b'KRFD',b'KORD'),PRODUCT)
        with self.assertRaises(ValueError):f.verify_bulletin(BULLETIN.replace(b'010529Z',b'010530Z'),PRODUCT)

    def test_cycle_time_without_actual_issue_proof_is_not_eligible(self):
        with self.assertRaises(ValueError):f.parse_taf(b'station,runtime,ftime,tmp\nKRFD,2025-12-01,2025-12-02,30')

    def test_cutoff_excludes_future_amendment_and_end_boundary(self):
        rows=f.parse_taf(csv_taf())
        future=copy.deepcopy(rows[0]);future.update(issued_at_utc=(AT+timedelta(hours=1)).isoformat(),product_id='202512010700-KLOT-FTUS43-TAFRFD-AAA')
        selected=f.select_taf(rows+[future],{PRODUCT:BULLETIN},cutoff=AT,target=AT+timedelta(hours=2))
        self.assertEqual(selected['product_id'],PRODUCT)
        self.assertIsNone(f.select_taf(rows,{PRODUCT:BULLETIN},cutoff=AT,target=AT+timedelta(days=1)))
        self.assertIsNone(f.select_taf(rows,{},cutoff=AT,target=AT))
        self.assertIsNone(f.select_taf(rows,{PRODUCT:BULLETIN},cutoff=AT-timedelta(hours=1),target=AT))

    def test_tempo_is_not_substituted_for_prevailing_forecast(self):
        rows=f.parse_taf(csv_taf())+f.parse_taf(csv_taf(ftype='Temporary',raw='TEMPO SN',sknt='40'))
        selected=f.select_taf(rows,{PRODUCT:BULLETIN},cutoff=AT,target=AT)
        self.assertEqual(selected['observation']['wind_kt'],5)
        self.assertEqual(selected['alternative_groups'][0]['kind'],'Temporary')
        self.assertFalse(selected['thermodynamic_coverage'])

    def test_month_year_rollover_and_dst_are_explicit(self):
        product='202512312329-KLOT-FTUS43-TAFRFD'
        raw=b'KRFD 312329Z 0100/0200 18005KT P6SM SKC='
        self.assertEqual(f.verify_bulletin(raw,product)['end'],'2026-01-02T00:00:00+00:00')
        self.assertEqual(a.local_instant('2025-03-09','02:30')[1],'nonexistent_dst')
        self.assertEqual(a.local_instant('2025-11-02','01:30')[1],'ambiguous_dst')
        with self.assertRaises(ValueError):f.instant('2025-12-01T06:00:00')

    def test_partial_forecast_never_fills_thermodynamics_from_future_observations(self):
        selected=f.select_taf(f.parse_taf(csv_taf()),{PRODUCT:BULLETIN},cutoff=AT,target=AT+timedelta(hours=2))
        obs=[dict(utc=(AT-timedelta(hours=i)).isoformat(),temperature_c=-5,dewpoint_c=-6,wind_kt=3,clear_sky=True) for i in range(13)]
        leaked=dict(utc=(AT+timedelta(hours=2)).isoformat(),temperature_c=99,dewpoint_c=88,wind_kt=99)
        first=f.research_vector(obs,selected,cutoff=AT,target=AT+timedelta(hours=2))
        second=f.research_vector(obs+[leaked],selected,cutoff=AT,target=AT+timedelta(hours=2))
        self.assertEqual(first,second)
        for name in ('temperature_c','dewpoint_c','spread_c','relative_humidity','cooling_rate_3h_c_per_hour','precipitation_report_proxy_hours'):
            self.assertIsNone(first[model.FEATURES.index(name)])
        self.assertEqual(first[model.FEATURES.index('wind_kt')],5)

    def test_nonfinite_malformed_oversize_and_untrusted_product_rejected(self):
        for raw in (csv_taf(sknt='nan'),csv_taf(drct='Infinity'),csv_taf(skyc='{}'),csv_taf(product_id='../secret'),b'x'*(f.MAX_BYTES+1)):
            with self.assertRaises(ValueError):f.parse_taf(raw)

    def test_local_capture_and_audit_forbidden_on_render(self):
        with patch.dict(os.environ,{'RENDER':'true'}):
            with self.assertRaises(RuntimeError):cli.Evidence('does-not-exist')
            with self.assertRaises(RuntimeError):cli.cached_get('ignored','https://example.test')

    def test_provider_cache_checks_hash_and_closes_connections(self):
        class Response:
            closed=False
            def __enter__(self):return self
            def __exit__(self,*args):self.closed=True
            def read(self,size):return b'original evidence'
        with tempfile.TemporaryDirectory() as folder:
            response=Response()
            with patch.object(cli,'urlopen',return_value=response) as request,patch.object(cli.time,'sleep'):
                raw,receipt=cli.cached_get(folder,'https://example.test')
                again,_=cli.cached_get(folder,'https://example.test')
                self.assertEqual(raw,again);self.assertEqual(request.call_count,1);self.assertTrue(response.closed)
            Path(folder,receipt['sha256']+'.source').write_bytes(b'tampered')
            with self.assertRaises(ValueError):cli.cached_get(folder,'https://example.test')

    def test_no_outcome_is_inferred_from_no_treatment(self):
        self.assertEqual(a.classification(None,actual_spray=False)['label'],'UNKNOWN')
        self.assertEqual(a.classification('P')['label'],'ANTICIPATED FROST RISK')
        self.assertEqual(a.classification('SN')['label'],'NON-FROST TREATMENT')
        self.assertIsNone(model.training_label(dict(classification=a.classification('P'))))

    def test_observation_age_rejection_includes_exact_ninety_minute_boundary(self):
        ob=dict(utc=(AT-timedelta(minutes=90)).isoformat(),temperature_c=0,dewpoint_c=-1)
        self.assertTrue(a.features(AT.isoformat(),[ob])['matched'])
        self.assertFalse(a.features((AT+timedelta(seconds=1)).isoformat(),[ob])['matched'])

    def test_independent_raw_metar_audit_exposes_trailing_vertical_visibility(self):
        row=dict(utc=AT.isoformat(),raw_metar='KRFD 010600Z 18005KT 1SM -SN VV014 M01/M03',
            temperature_c=-1,dewpoint_c=-3,wind_kt=5,wind_direction_degrees=180,gust_kt=None,
            cloud_layers=['VV '],ceiling_ft=None,weather_codes='-SN')
        with self.assertRaisesRegex(ValueError,'cloud coverage mismatch'):f.verify_metar(row)
        row.update(cloud_layers=['VV'],ceiling_ft=1400)
        self.assertIn('cloud_layers',f.verify_metar(row)['checked_fields'])
        row['raw_metar']=row['raw_metar'].replace('KRFD','KORD')
        with self.assertRaises(ValueError):f.verify_metar(row)

    def test_nws_prospective_grid_rejects_future_wrong_location_and_units(self):
        def quantity(unit,value):return dict(uom=unit,values=[dict(validTime=AT.isoformat()+'/PT12H',value=value)])
        properties=dict(updateTime=(AT-timedelta(minutes=10)).isoformat(),temperature=quantity('wmoUnit:degC',-1),
            dewpoint=quantity('wmoUnit:degC',-2),relativeHumidity=quantity('wmoUnit:percent',90),
            windSpeed=quantity('wmoUnit:km_h-1',18.52),windDirection=quantity('wmoUnit:degree_(angle)',180))
        grid=dict(geometry=dict(type='Polygon',coordinates=[[[-89.10,42.18],[-89.07,42.18],[-89.07,42.21],[-89.10,42.21],[-89.10,42.18]]]),properties=properties)
        result=f.nws_grid(grid,cutoff=AT,target=AT+timedelta(hours=1))
        self.assertAlmostEqual(result['observation']['wind_kt'],10)
        self.assertTrue(result['thermodynamic_coverage'])
        self.assertIsNone(result['observation']['precipitation_since_hourly_reset_in'])
        for mutation in (lambda x:x['properties'].update(updateTime=(AT+timedelta(hours=1)).isoformat()),
                lambda x:x['properties']['windSpeed'].update(uom='wmoUnit:m_s-1'),
                lambda x:x['geometry'].update(coordinates=[[[0,0],[.01,0],[.01,.01],[0,.01],[0,0]]])):
            bad=copy.deepcopy(grid);mutation(bad)
            with self.assertRaises(ValueError):f.nws_grid(bad,cutoff=AT,target=AT+timedelta(hours=1))

    def test_render_receipt_is_bounded_metadata_not_a_training_path(self):
        receipt=frost_research.load()
        self.assertFalse(receipt['operationally_validated'])
        self.assertEqual(receipt['complete_forecast_cases'],0)
        self.assertLess(len(frost_research.REPORT.read_bytes()),32768)
        with tempfile.TemporaryDirectory() as folder:
            bad=Path(folder)/'report.json';bad.write_text('{"operationally_validated":NaN}')
            with patch.object(frost_research,'REPORT',bad):self.assertIsNone(frost_research.status())

    def test_shadow_replay_uses_only_captured_inputs_and_explicit_later_outcomes(self):
        from app.services import frost_vault as vault
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'shadow').mkdir();(root/'shadow-cache').mkdir()
            grid_raw=b'{"captured":"forecast"}';grid_sha=vault.digest(grid_raw)
            (root/'shadow-cache'/(grid_sha+'.source')).write_bytes(grid_raw)
            snapshot=dict(schema=f.VERSION,station=weather.STATION,approved=False,prediction_cutoff_utc=AT.isoformat(),
                predictions=[dict(sha256='a'*64)],grid_receipt=dict(sha256=grid_sha),observations=[])
            raw=vault.canonical(snapshot);path=root/'shadow'/(vault.digest(raw)+'.json');path.write_bytes(raw)
            artifact=dict(model_version='research-fixture',evaluation=dict(lead_hours_validated=[0]))
            evidence=SimpleNamespace(root=root,manifest=dict(fingerprint='b'*64),candidates=lambda:[(artifact,'a'*64)])
            cli.write_json(root/'prepared.json',dict(examples=[]))
            result=cli.compare_shadow(evidence,root)
            self.assertTrue(all(r['explicit_treatment_pairs']==0 for r in result['comparisons']))
            self.assertIn('UNKNOWN',result['no_record_outcome'])
            event=dict(event_at=(AT+timedelta(minutes=90)).isoformat(),group_id='real-source-id',sources=[dict(document_id=1,source_row=2)],
                classifications=[a.classification('F')],label=1,season='2025-2026',night='2025-11-30')
            cli.write_json(root/'prepared.json',dict(examples=[event]))
            selected=dict(issued_at_utc=(AT-timedelta(minutes=30)).isoformat(),target_at_utc=event['event_at'],
                observation=dict(temperature_c=-1,dewpoint_c=-2,wind_kt=3))
            with patch.object(f,'nws_grid',return_value=selected) as captured,patch.object(model,'score',return_value=.7):
                result=cli.compare_shadow(evidence,root)
            self.assertEqual(result['comparisons'][0]['explicit_treatment_pairs'],1)
            self.assertEqual(result['comparisons'][0]['pairs'][0]['sources'],event['sources'])
            self.assertEqual(captured.call_args.kwargs['cutoff'],AT)
            self.assertFalse(result['operationally_validated'])
            path.write_bytes(raw+b' ')
            with self.assertRaisesRegex(ValueError,'snapshot hash'):cli.compare_shadow(evidence,root)


class ResearchRouteTest(unittest.TestCase):
    from tests.test_frost_vault import FrostVaultTest as _fixture
    setUp=_fixture.setUp
    client=_fixture.client

    def test_authorized_read_only_research_and_no_operational_activation(self):
        from flask import g
        from app.models import FrostModelRelease,NeoSubZeroSprayRecord
        client=self.client('master');g.pop('_login_user',None)
        before=(FrostModelRelease.query.count(),NeoSubZeroSprayRecord.query.count())
        with (patch.object(weather,'fetch',side_effect=AssertionError('Historical download on Render')),
                patch.object(model,'score',side_effect=AssertionError('Candidate scoring on Render'))):
            page=client.get('/neosubzero/frost-vault')
            self.assertEqual(page.status_code,200)
            self.assertIn(b'FORECAST VALIDATION',page.data)
            response=client.get('/neosubzero/frost-vault/forecast-validation')
        self.assertEqual(response.status_code,200)
        self.assertIn('private',response.headers['Cache-Control'])
        self.assertFalse(response.json['operationally_validated'])
        self.assertEqual(before,(FrostModelRelease.query.count(),NeoSubZeroSprayRecord.query.count()))
        self.assertEqual(client.post('/neosubzero/frost-vault/forecast-validation').status_code,405)
        watcher=self.client('watcher');g.pop('_login_user',None)
        self.assertEqual(watcher.get('/neosubzero/frost-vault/forecast-validation').status_code,403)

    def test_unavailable_study_does_not_break_csv_import_or_claim_success(self):
        from flask import g
        client=self.client('master');g.pop('_login_user',None)
        with patch.object(frost_research,'status',return_value=None):
            response=client.get('/neosubzero/frost-vault')
            self.assertEqual(response.status_code,200)
            self.assertIn(b'IMPORT CSV',response.data)
            self.assertIn(b'Research summary unavailable',response.data)
            self.assertEqual(client.get('/neosubzero/frost-vault/forecast-validation').status_code,503)


from tests import test_neosubzero_ucc_postgres as pg


@unittest.skipUnless(pg.URI,'Requires disposable loopback PostgreSQL')
class ResearchPostgresTest(ResearchRouteTest):
    drop_schema=pg.UccPostgresTest.drop_schema
    def setUp(self):
        pg.UccPostgresTest.setUp(self)
        self.f=self.fixture
