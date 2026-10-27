"""LOCAL ONLY Phase 7 audit, issued-forecast diagnostics and shadow capture.

python -m scripts.frost_forecast_validation --local --help
Never retrains, approves, imports production credentials or mutates spray data.
"""
import argparse
import json
import re
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from app.services import frost_analysis as analysis, frost_forecast as forecast
from app.services import frost_model as model, frost_vault as vault, frost_weather as weather
from scripts.frost_local import local_only, read_json, write_json, write_bytes, metrics, weather_reference, validate_manifest

UTC = timezone.utc


class Evidence:
    def __init__(self, directory):
        local_only()
        self.root = Path(directory)
        self.manifest = validate_manifest(read_json(self.root / 'manifest.json'))

    def object(self, sha):
        raw = (self.root / 'objects' / (sha + '.json')).read_bytes()
        if vault.digest(raw) != sha:
            raise ValueError('Local object SHA mismatch.')
        return json.loads(raw)

    @lru_cache(maxsize=3)
    def weather(self, month):
        entry = next(r for r in self.manifest['weather'] if r['month'] == month)
        path = self.root / 'weather' / (month + '.json')
        if not path.exists():
            path = self.root / 'objects' / (entry['sha256'] + '.json')
        raw = path.read_bytes()
        if vault.digest(raw) != entry['sha256']:
            raise ValueError('Monthly weather manifest SHA mismatch.')
        payload = json.loads(raw)
        forecast.station(payload['station'])
        source_path = self.root / 'weather' / (month + '.csv')
        if source_path.exists():
            source = source_path.read_bytes()
        else:
            # Earlier R2-derived monthly packages retained raw hash+METARs, not
            # the CSV bytes locally. Retrieve the exact recorded public request
            # locally and accept ONLY its original immutable content hash.
            url = payload['request_url']
            if not url.startswith(weather.URL + '?'):
                raise ValueError('Untrusted original observation request.')
            source, _ = cached_get(self.root.parent / 'phase7' / 'observation-cache',url,maximum=16*1024*1024)
        if vault.digest(source) != payload['raw_sha256'] or payload['schema_version'] != weather.VERSION:
            raise ValueError('Raw weather hash/parser mismatch.')
        parsed = weather.parse(source, forecast.instant(payload['start_utc']), forecast.instant(payload['end_utc']))
        if parsed != payload['observations']:
            raise ValueError('Raw source normalization differs from archived observations.')
        return payload

    def observations(self, at):
        months = {at.strftime('%Y-%m'), (at-timedelta(hours=12)).strftime('%Y-%m')}
        available = {r['month'] for r in self.manifest['weather']}
        return [r for month in sorted(months & available) for r in self.weather(month)['observations']]

    def candidates(self):
        result = []
        for path in sorted((self.root / 'candidates').glob('rfd-treatment-h[01]-*.json')):
            raw = path.read_bytes()
            result.append((model.decode(raw), vault.digest(raw)))
        if len(result) != 2:
            raise ValueError('Require exactly the existing zero/one-hour research candidates.')
        return result


def sample_cases(examples):
    """Deterministic regime/season and DST-neighborhood sample, not score selection."""
    selected = {}
    seasons = sorted({r['season'] for r in examples})
    for season in seasons:
        pool = sorted((r for r in examples if r['season'] == season), key=lambda r: (r['event_at'], r['group_id']))
        tests = [lambda r: r['label'] == 1, lambda r: r['label'] == 0,
            lambda r: r['features'][0] is not None and r['features'][0] > 0,
            lambda r: r['features'][0] is not None and r['features'][0] < -10,
            lambda r: r['features'][10] == 1,
            lambda r: any((r['features'][i] or 0) > 0 for i in (29, 30, 31, 32)),
            lambda r: forecast.instant(r['event_at']).month == 11 and forecast.instant(r['event_at']).day <= 10,
            lambda r: forecast.instant(r['event_at']).month == 3 and 5 <= forecast.instant(r['event_at']).day <= 15]
        for index, predicate in enumerate(tests):
            match = next((r for r in pool if predicate(r)), None)
            if match:
                selected.setdefault(match['group_id'], dict(match, audit_strata=[]))['audit_strata'].append(index)
    return sorted(selected.values(), key=lambda r: (r['event_at'], r['group_id']))


def audit(evidence, output):
    original = []
    for source in evidence.manifest['source_catalog']['documents']:
        raw = (evidence.root / 'originals' / (source['sha256'] + '.csv')).read_bytes()
        if len(raw) != source['bytes'] or vault.digest(raw) != source['sha256']:
            raise ValueError('Original Xchange source integrity failure.')
        original.append(dict(document_id=source['id'], sha256=source['sha256'], source_rows=source['source_rows']))
    weather_checks = []
    for entry in evidence.manifest['weather']:
        payload = evidence.weather(entry['month'])
        weather_checks.append(dict(month=entry['month'], sha256=entry['sha256'], raw_sha256=payload['raw_sha256'],
            observations=len(payload['observations']), request_url=payload['request_url'], parser=payload['schema_version']))
    prepared = read_json(evidence.root / 'prepared.json')
    selected = sample_cases(prepared['examples'])
    sources = {(s['document_id'], s['source_row']) for r in selected for s in r['sources']}
    source_rows = {}
    counts = Counter()
    for partition in evidence.manifest['partitions']:
        result = evidence.object(partition['sha256'])
        for row in result['records']:
            counts[row['classification']['label']] += 1
            for source in row['sources']:
                key = (source['document_id'], source['source_row'])
                if key in sources:
                    source_rows[key] = row
    samples, normalization_findings = [], []
    for example in selected:
        # A multi-truck application can begin on a later source row. Locate
        # the actual earliest fluid record, not the first CSV contribution.
        matches = [(s, source_rows[(s['document_id'],s['source_row'])], fluid)
            for s in example['sources'] for fluid in source_rows[(s['document_id'],s['source_row'])]['fluids']
            if fluid.get('occurred_at_utc') == example['event_at']]
        if not matches:
            raise ValueError('No source fluid substantiates the grouped treatment START.')
        source, row, fluid = matches[0]
        at = forecast.instant(example['event_at'])
        obs = evidence.observations(at)
        features = analysis.features(at.isoformat(), obs)
        converted, quality = analysis.local_instant(row['application_date'], fluid['start_local_clock'])
        if converted != example['event_at'] or quality != 'verified_local_calendar':
            raise ValueError('Sample source clock differs from training event.')
        current = features['observation']
        try:
            independent = dict(state='CHECKED',**forecast.verify_metar(current))
        except ValueError as exc:
            # An independently discovered measurement discrepancy is a study
            # finding, never a reason to rewrite old evidence or quietly relax
            # a check. Preserve the exact source and the unchanged candidates.
            independent=dict(state='REQUIRES REVIEW',error=str(exc))
            normalization_findings.append(dict(source=source,record_id=row['source_record_id'],error=str(exc)))
        age = (at - forecast.instant(current['utc'])).total_seconds() / 60
        if not features['matched'] or age > 90 or age < 0:
            raise ValueError('Sample violates preceding-observation age policy.')
        samples.append(dict(season=example['season'], source=source, record_id=row['source_record_id'],
            source_local_date=row['application_date'], source_local_clock=fluid['start_local_clock'], event_at_utc=converted,
            observation=current, age_minutes=age, preceding_12h_count=features['observation_count_12h'],
            independent_raw_checks=independent,
            incomplete_window=features['incomplete_window'], solar=features['solar'],
            trends={k:v for k,v in features.items() if k.startswith(('cooling_rate_', 'changes_'))},
            observation_receipt_time='not retained by this IEM export; observation-time audit, not proof of exact receipt',
            raw_correction='COR' in current['raw_metar'].split()))
    report = dict(schema=forecast.VERSION, generated_at_utc=datetime.now(UTC).isoformat(), station=weather.STATION,
        source_catalog_fingerprint=evidence.manifest['source_catalog']['fingerprint'], dataset_fingerprint=evidence.manifest['fingerprint'],
        originals=original, monthly_weather=weather_checks, classifications=dict(counts), samples=samples,
        seasons=sorted({r['season'] for r in samples}), source_records=sum(counts.values()),
        quality=evidence.manifest['quality'], sample_selection='first per season/regime/DST-neighborhood; no score-dependent selection',
        normalization_findings=normalization_findings,
        availability_limitation='METAR report time is not an archived operator receipt timestamp; late corrections can limit availability claims.')
    write_json(Path(output) / 'weather-audit.json', report)
    write_json(Path(output) / 'sample-cases.json', selected)
    print(json.dumps(dict(sources=len(original), months=len(weather_checks), samples=len(samples), seasons=report['seasons'])), flush=True)
    return report


def cached_get(root, url, *, maximum=forecast.MAX_BYTES):
    """Content-addressed, hash-checked, timeout/size-bounded public-source GET.

    No aggressive automatic retries: failures preserve successful checkpoints;
    rerun after provider Retry-After. Connections are closed on every exit.
    """
    local_only()
    key = vault.digest(url.encode())
    receipt = Path(root) / (key + '.json')
    if receipt.exists():
        saved = read_json(receipt)
        raw = (Path(root) / (saved['sha256'] + '.source')).read_bytes()
        if saved['url'] != url or len(raw) > maximum or vault.digest(raw) != saved['sha256']:
            raise ValueError('Cached provider evidence hash mismatch.')
        return raw, saved
    time.sleep(1.1)
    with urlopen(Request(url, headers={'User-Agent':'NeoApps-FROST local forecast research/1.0', 'Accept':'application/geo+json,application/json,text/plain,*/*'}), timeout=30) as response:
        raw = response.read(maximum + 1)
        if len(raw) > maximum:
            raise ValueError('Provider response exceeds bounded research retrieval.')
    saved = dict(url=url, retrieved_at_utc=datetime.now(UTC).isoformat(), sha256=vault.digest(raw), bytes=len(raw))
    write_bytes(Path(root) / (saved['sha256'] + '.source'), raw)
    write_json(receipt, saved)
    return raw, saved


def taf_day(cache, at):
    start = at.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    url = 'https://mesonet.agron.iastate.edu/cgi-bin/request/taf.py?' + urlencode(dict(
        station='RFD', sts=start.isoformat(), ets=(start+timedelta(days=2)).isoformat(), tz='UTC', fmt='csv'))
    raw, receipt = cached_get(cache, url)
    rows = forecast.parse_taf(raw)
    return rows, receipt


def taf_case(cache, rows, cutoff, target):
    available = [r for r in rows if forecast.instant(r['issued_at_utc']) <= cutoff]
    if not available:
        return None
    last = max(r['issued_at_utc'] for r in available)
    products = {r['product_id'] for r in available if r['issued_at_utc'] == last}
    bulletins = {}
    for product in products:
        raw, _ = cached_get(cache, 'https://mesonet.agron.iastate.edu/api/1/nwstext/' + product, maximum=32768)
        bulletins[product] = raw
    return forecast.select_taf(rows, bulletins, cutoff=cutoff, target=target)


def evaluate(evidence, output, *, retrieve=False):
    local_only()
    root = Path(output)
    samples = read_json(root / 'sample-cases.json')
    artifacts = evidence.candidates()
    prepared = read_json(evidence.root / 'prepared.json')
    results = []
    for artifact, sha in artifacts:
        lead = artifact['evaluation']['lead_hours_validated'][0]
        test = [r for r in prepared['examples'] if r['season'] == artifact['evaluation']['test_season']]
        observed = []
        for row in test:
            at = forecast.instant(row['event_at']) - timedelta(hours=lead)
            f = analysis.features(at.isoformat(), evidence.observations(at))
            if f['matched'] and not f['incomplete_window']:
                values = model.vector(f)
                observed.append((row, values, model.score(artifact, values)))
        observed_metrics = metrics([r['label'] for r, _, _ in observed], [s for _, _, s in observed])
        if observed_metrics['count'] != artifact['evaluation']['test']['count'] or abs(observed_metrics['brier']-artifact['evaluation']['test']['brier']) > 1e-10:
            raise ValueError(f"Unchanged candidate observation result differs: {observed_metrics['count']}/{observed_metrics['brier']} vs {artifact['evaluation']['test']['count']}/{artifact['evaluation']['test']['brier']}")
        above = [(r, v, s) for r, v, s in observed if v[0] is not None and v[0] > 0]
        common_above = [(r, v, s) for r, v, s in observed if model.vector(analysis.features(r['event_at'], evidence.observations(forecast.instant(r['event_at']))))[0] > 0]
        result = dict(model_version=artifact['model_version'], sha256=sha, model_lead_hours=lead,
            observed_test=observed_metrics, observed_weather_reference=metrics([r['label'] for r,_,_ in observed], [weather_reference(v) for _,v,_ in observed]),
            above_freezing_at_input=metrics([r['label'] for r,_,_ in above],[s for _,_,s in above]),
            common_above_freezing_at_event=metrics([r['label'] for r,_,_ in common_above],[s for _,_,s in common_above]),
            above_freezing_nights=len({r['night'] for r,_,_ in above}),
            feature_importance=sorted(artifact['evaluation']['feature_importance'],key=lambda r:abs(r['value']),reverse=True)[:10],
            forecast_diagnostics=[], complete_forecast_cases=0)
        if retrieve:
            for horizon in (1, 3, 6):
                cases = []
                gaps = 0
                for index, row in enumerate(samples):
                    event = forecast.instant(row['event_at'])
                    # Preserve model input-time contract: h1 expects weather at
                    # event-1h, never plug target-event weather into that model.
                    target = event-timedelta(hours=lead)
                    cutoff = min(target, event-timedelta(hours=horizon))
                    rows, receipt = taf_day(root / 'forecast-cache', cutoff)
                    selected = taf_case(root / 'forecast-cache', rows, cutoff, target)
                    if selected is None:
                        gaps += 1
                        continue
                    obs = evidence.observations(cutoff)
                    vector = forecast.research_vector(obs, selected, cutoff=cutoff, target=target)
                    observed_values = model.vector(analysis.features(target.isoformat(), evidence.observations(target)))
                    case = dict(group_id=row['group_id'], season=row['season'], sources=row['sources'],
                        label=row['label'], event_at_utc=event.isoformat(), prediction_cutoff_utc=cutoff.isoformat(),
                        model_input_at_utc=target.isoformat(), requested_warning_hours=horizon,
                        taf=selected, source_receipt=receipt,
                        score=model.score(artifact,vector), observed_score=model.score(artifact,observed_values),
                        reference_score=weather_reference(vector), missing_features=[n for n,v in zip(model.FEATURES,vector) if v is None],
                        domain='PARTIAL TAF DIAGNOSTIC; missing thermodynamics; NOT validated advance-warning skill')
                    cases.append(case)
                write_json(root / f"{artifact['model_version']}-taf-{horizon}h.json",cases)
                final = [r for r in cases if r['season'] == artifact['evaluation']['test_season']]
                result['forecast_diagnostics'].append(dict(warning_hours=horizon, sampled_cases=len(cases), gaps=gaps,
                    test_cases=len(final), test=metrics([r['label'] for r in final],[r['score'] for r in final]),
                    paired_observed=metrics([r['label'] for r in final],[r['observed_score'] for r in final]),
                    reference=metrics([r['label'] for r in final],[r['reference_score'] for r in final]),
                    missing_critical=['temperature','dewpoint','humidity','measured precipitation amount'],
                    interpretation='diagnostic imputation stress test, not contract-complete forecast validation'))
                print(f"{artifact['model_version']} {horizon}h: {len(cases)} partial TAF cases, {gaps} gaps",flush=True)
        results.append(result)
    report = dict(schema=forecast.VERSION, generated_at_utc=datetime.now(UTC).isoformat(), models=results,
        station='KRFD', no_retraining=True, physical_frost_probability=False, operationally_validated=False,
        forecast_complete_cases=0, sample_selection='weather-audit deterministic sample, distinct held-out test results only',
        parking_adjustments=None, untreated_controls=0,
        limitations=['Treatment-selected cohort, not the population of untreated aircraft.',
            'TAF lacks temperature/dew point; partial diagnostics cannot establish frost-warning skill.',
            'TEMPO/PROB groups preserved but not reduced to unconditional values.',
            'Observation timestamps do not establish exact historical receipt times.',
            'Frozen model analysis on older fit/calibration seasons is descriptive, not independent validation.'])
    write_json(root / 'validation.json', report)
    print(json.dumps({r['model_version']:dict(test=r['observed_test']['count'],recall=r['observed_test']['recall'],above=r['above_freezing_at_input']['recall']) for r in results}),flush=True)
    return report


def shadow(evidence, output):
    """One bounded local capture. No scheduler, no production candidate scoring.

    Only records a RESEARCH result. A later command must join credible outcomes;
    absence of a future treatment record must never create a negative label.
    """
    local_only()
    now = datetime.now(UTC)
    root = Path(output)
    url = 'https://aviationweather.gov/api/data/metar?' + urlencode(dict(ids='KRFD', format='json', hours=14))
    capture_cache = root / 'shadow-cache' / now.strftime('%Y%m%d_%H%M')
    raw, receipt = cached_get(capture_cache, url)
    from app.services.frost_inference import normalize_metars
    observations = normalize_metars(json.loads(raw))
    rows, taf_receipt = taf_day(root / 'shadow-cache', now)
    points_raw, points_receipt = cached_get(capture_cache,'https://api.weather.gov/points/42.1915,-89.0876')
    points=json.loads(points_raw)
    point=points['geometry']
    if point['type']!='Point' or any(abs(a-b)>.0001 for a,b in zip(point['coordinates'],[-89.0876,42.19151])):
        raise ValueError('NWS points response does not identify KRFD.')
    grid_url=points['properties']['forecastGridData']
    if not re.fullmatch(r'https://api.weather.gov/gridpoints/LOT/\d+,\d+',grid_url):
        raise ValueError('Unexpected NWS forecast location/source.')
    grid_raw,grid_receipt=cached_get(capture_cache,grid_url)
    grid=json.loads(grid_raw)
    now = datetime.now(UTC)  # Actual prospective knowledge cutoff, after receipt.
    predictions = []
    for artifact, sha in evidence.candidates():
        lead = artifact['evaluation']['lead_hours_validated'][0]
        f = analysis.features(now.isoformat(), observations)
        selected = taf_case(root / 'shadow-cache', rows, now, now+timedelta(hours=1))
        windows=[]
        for hours in (1,3,6):
            event=now+timedelta(hours=hours)
            target=event-timedelta(hours=lead)
            selected_grid=forecast.nws_grid(grid,cutoff=now,target=target)
            vector=forecast.research_vector(observations,selected_grid,cutoff=now,target=target)
            windows.append(dict(warning_hours=hours,event_at_utc=event.isoformat(),model_input_at_utc=target.isoformat(),
                forecast=selected_grid,score=model.score(artifact,vector),
                missing_features=[n for n,v in zip(model.FEATURES,vector) if v is None],
                state='SHADOW ONLY; forecast-domain skill not validated'))
        predictions.append(dict(model_version=artifact['model_version'], sha256=sha, target_at_utc=(now+timedelta(hours=lead)).isoformat(),
            observed_score=model.score(artifact,model.vector(f)) if f['matched'] and not f['incomplete_window'] else None,
            state='RESEARCH / NOT OPERATIONAL', forecast=selected, forecast_windows=windows,outcome=None,
            outcome_policy='UNKNOWN until explicit source-traceable treatment or credible observation; no inferred no-frost nights'))
    snapshot = dict(schema=forecast.VERSION, captured_at_utc=datetime.now(UTC).isoformat(), prediction_cutoff_utc=now.isoformat(),
        station=weather.STATION, observations=observations, observation_receipt=receipt, forecast_receipt=taf_receipt,
        points_receipt=points_receipt,grid_receipt=grid_receipt,
        predictions=predictions, physical_frost_probability=False, approved=False)
    write_json(root / 'shadow' / (vault.digest(vault.canonical(snapshot))+'.json'),snapshot)
    print('Local shadow snapshot saved. No model approved, no operational forecast displayed.',flush=True)
    return snapshot


def sources(evidence, output):
    """Small operational-source availability probes, never entire GRIB fields."""
    local_only()
    root=Path(output)
    sample=read_json(root/'sample-cases.json')
    first={}
    for row in sample:first.setdefault(row['season'],row)
    probes=[]
    for season,row in sorted(first.items()):
        at=forecast.instant(row['event_at'])-timedelta(hours=12)
        cycle=at.replace(hour=at.hour//6*6,minute=0,second=0,microsecond=0)
        url='https://mesonet.agron.iastate.edu/api/1/mos.json?'+urlencode(dict(station='KRFD',model='GFS',runtime=cycle.isoformat()))
        try:
            raw,receipt=cached_get(root/'source-probes',url)
            rows=json.loads(raw)['data']
            if any(r.get('station')!='KRFD' for r in rows):raise ValueError('MOS station mismatch.')
            probes.append(dict(source='IEM GFS MOS',season=season,receipt=receipt,rows=len(rows),cycle=cycle.isoformat(),
                actual_issue_time=None,skill_eligible=False,reason='runtime is model cycle, not proven dissemination/issue time',
                populated_fields=sorted(k for k in (rows[0] if rows else {}) if any(r.get(k) is not None for r in rows))))
        except HTTPError as exc:
            probes.append(dict(source='IEM GFS MOS',season=season,http_status=exc.code,skill_eligible=False))
    for day in ('20151030','20201201','20251201'):
        url=f'https://noaa-hrrr-bdp-pds.s3.amazonaws.com/hrrr.{day}/conus/hrrr.t00z.wrfsfcf01.grib2.idx'
        try:
            raw,receipt=cached_get(root/'source-probes',url,maximum=256*1024)
            text=raw.decode()
            probes.append(dict(source='NOAA HRRR GRIB index only',date=day,receipt=receipt,
                parameters=[s for s in text.splitlines() if any(':'+v+':' in s for v in ('TMP','DPT','RH','TCDC','UGRD','VGRD','APCP','VIS'))],
                skill_eligible=False,reason='Grid not downloaded/extracted; original forecast dissemination time not independently established by index'))
        except HTTPError as exc:
            probes.append(dict(source='NOAA HRRR GRIB index only',date=day,http_status=exc.code,skill_eligible=False,
                reason='Probe unavailable; HTTP failure does not establish absence from all operational archives'))
    write_json(root/'source-availability.json',dict(schema=forecast.VERSION,checked_at_utc=datetime.now(UTC).isoformat(),probes=probes))
    print(json.dumps([dict(source=r['source'],season=r.get('season'),date=r.get('date'),rows=r.get('rows'),status=r.get('http_status',200)) for r in probes]),flush=True)


def contributions(evidence, output):
    """Frozen-model held-out night-block permutation, not model selection.

    Correlated-variable sensitivity is descriptive, not a causal attribution or
    physical surface measurement. No row/night is added to a fit or calibration.
    """
    local_only()
    examples=read_json(evidence.root/'prepared.json')['examples']
    reports=[]
    groups=dict(thermodynamics=[0,1,2,3],wind=[4,5,6,7,28,37,39],cloud_visibility=[8,9,10,24,36],
        solar=[11,12,13,19],temperature_trends=[14,15,16,17],
        moisture_precipitation=[18,20,25,29,30,31,33,34],fog=[21,32])
    for artifact,sha in evidence.candidates():
        lead=artifact['evaluation']['lead_hours_validated'][0]
        rows=[]
        for source in examples:
            if source['season']!=artifact['evaluation']['test_season']:continue
            at=forecast.instant(source['event_at'])-timedelta(hours=lead)
            f=analysis.features(at.isoformat(),evidence.observations(at))
            rows.append(dict(source,features=model.vector(f)))
        by_night=defaultdict(list)
        for row in rows:by_night[row['night']].append(row)
        nights=sorted(by_night)
        baseline=metrics([r['label'] for r in rows],[model.score(artifact,r['features']) for r in rows])
        sensitivity={}
        for group,indexes in groups.items():
            changes=[]
            for shift in (1,max(1,len(nights)//3),max(1,len(nights)//2)):
                labels,scores=[],[]
                for i,night in enumerate(nights):
                    donor=by_night[nights[(i+shift)%len(nights)]]
                    for j,row in enumerate(by_night[night]):
                        values=list(row['features'])
                        for k in indexes:values[k]=donor[j%len(donor)]['features'][k]
                        labels.append(row['label']);scores.append(model.score(artifact,values))
                changes.append(metrics(labels,scores)['brier']-baseline['brier'])
            sensitivity[group]=dict(brier_increase_mean=sum(changes)/len(changes),rotations=changes)
        above=[r for r in rows if r['label']==1 and r['features'][0] is not None and r['features'][0]>0]
        regimes={}
        for index,name in ((0,'temperature_c'),(1,'dewpoint_c'),(2,'spread_c'),(4,'wind_kt'),(14,'cooling_rate_3h')):
            values=sorted(r['features'][index] for r in above if r['features'][index] is not None)
            regimes[name]=dict(count=len(values),minimum=min(values) if values else None,maximum=max(values) if values else None,
                median=values[len(values)//2] if values else None)
        reports.append(dict(model_version=artifact['model_version'],sha256=sha,test_season=artifact['evaluation']['test_season'],
            applications=len(rows),nights=len(nights),baseline=baseline,night_block_permutation=sensitivity,
            above_freezing_frost_reason=dict(applications=len(above),nights=len({r['night'] for r in above}),
                night_cases=sum(r['features'][11]==1 for r in above),clear_cases=sum(r['features'][10]==1 for r in above),regimes=regimes),
            historical_forward_folds=[dict(season=r['test_season'],metrics=r['metrics'][artifact['model']['kind']]) for r in artifact['evaluation']['development']]))
        print('Frozen-model night-block sensitivity completed: '+artifact['model_version'],flush=True)
    write_json(Path(output)/'contributions.json',dict(schema=forecast.VERSION,models=reports,
        basis='Three deterministic cross-night feature-block rotations; correlated proxies, not causal importance; no retraining'))


def compare_shadow(evidence, output):
    """Event-aligned LOCAL replay of previously captured prospective evidence.

    Uses only the observations/grid bytes actually captured before the event.
    A later verified CSV supplies an explicit treatment-reason outcome, never
    weather features. No treatment record => UNKNOWN, not a negative example.
    No arbitrary hourly-bin event labels and no modern forecast reconstruction.
    """
    local_only();root=Path(output)
    prepared=read_json(evidence.root/'prepared.json')
    snapshots=[]
    for path in sorted((root/'shadow').glob('*.json')):
        raw=path.read_bytes()
        if vault.digest(raw)!=path.stem:raise ValueError('Shadow snapshot hash mismatch.')
        item=json.loads(raw);forecast.station(item['station'])
        if item['schema']!=forecast.VERSION or item['approved'] is not False:raise ValueError('Invalid shadow evidence.')
        snapshots.append((path.stem,item))
    comparisons=[]
    for artifact,sha in evidence.candidates():
        lead=artifact['evaluation']['lead_hours_validated'][0]
        for hours in (1,3,6):
            pairs=[]
            for event in prepared['examples']:
                at=forecast.instant(event['event_at']);input_at=at-timedelta(hours=lead)
                available=[(key,s) for key,s in snapshots if forecast.instant(s['prediction_cutoff_utc'])<=at-timedelta(hours=max(hours,lead))
                    and any(p['sha256']==sha for p in s['predictions'])]
                if not available:continue
                key,snapshot=max(available,key=lambda entry:entry[1]['prediction_cutoff_utc'])
                cutoff=forecast.instant(snapshot['prediction_cutoff_utc'])
                # Only the captured near-term research domain, never extrapolate
                # a months-old snapshot or imply validated three/six-hour skill.
                if at-cutoff>timedelta(hours=6):continue
                receipt=snapshot['grid_receipt']
                sources=list((root/'shadow-cache').rglob(receipt['sha256']+'.source'))
                if not sources:raise ValueError('Captured forecast source missing.')
                raw=sources[0].read_bytes()
                if vault.digest(raw)!=receipt['sha256']:raise ValueError('Captured forecast source hash mismatch.')
                selected=forecast.nws_grid(json.loads(raw),cutoff=cutoff,target=input_at)
                vector=forecast.research_vector(snapshot['observations'],selected,cutoff=cutoff,target=input_at)
                pairs.append(dict(group_id=event['group_id'],sources=event['sources'],classifications=event['classifications'],
                    season=event['season'],night=event['night'],label=event['label'],event_at_utc=at.isoformat(),
                    snapshot_sha256=key,prediction_cutoff_utc=cutoff.isoformat(),model_input_at_utc=input_at.isoformat(),
                    actual_warning_hours=(at-cutoff).total_seconds()/3600,score=model.score(artifact,vector)))
            comparisons.append(dict(model_version=artifact['model_version'],sha256=sha,minimum_warning_hours=hours,
                explicit_treatment_pairs=len(pairs),metrics=metrics([p['label'] for p in pairs],[p['score'] for p in pairs]),pairs=pairs))
    result=dict(schema=forecast.VERSION,dataset_fingerprint=evidence.manifest['fingerprint'],snapshot_count=len(snapshots),
        comparisons=comparisons,no_record_outcome='UNKNOWN; never frost-negative',operationally_validated=False,
        interpretation='Research replay only; treatment-selected cohort and forecast-domain/missing-parameter limitations remain')
    write_json(root/'shadow-comparison.json',result)
    print(json.dumps(dict(snapshots=len(snapshots),pairs=sum(r['explicit_treatment_pairs'] for r in comparisons),
        no_record_outcome='UNKNOWN')),flush=True)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--local',action='store_true',required=True)
    parser.add_argument('--directory',default='instance/frost-local/complete')
    parser.add_argument('--output',default='instance/frost-local/phase7')
    parser.add_argument('command',choices=['audit','evaluate','forecast','shadow','sources','contributions','compare-shadow'])
    args = parser.parse_args(argv)
    local_only()
    evidence = Evidence(args.directory)
    if args.command == 'audit': audit(evidence,args.output)
    elif args.command == 'shadow': shadow(evidence,args.output)
    elif args.command == 'sources': sources(evidence,args.output)
    elif args.command == 'contributions': contributions(evidence,args.output)
    elif args.command == 'compare-shadow': compare_shadow(evidence,args.output)
    else: evaluate(evidence,args.output,retrieve=args.command=='forecast')


if __name__ == '__main__':
    main()
