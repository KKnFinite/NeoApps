"""LOCAL forecast-native preparation and season-isolated research.

Reuses Phase 8 source bytes and Phase 6 fitting/metrics. No registry import or
approval command. Assembly is offline; missing cycles are explicit exclusions.
"""
import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime,timedelta,timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from functools import lru_cache
from app.services import frost_forecast_native as native,frost_vault as vault,frost_model as model
from app.services import frost_forecast as clock,frost_weather as weather
from scripts import frost_hrrr as hrrr
from scripts.frost_forecast_validation import Evidence
from scripts.frost_local import local_only,read_json,write_json,write_bytes,fit,metrics,forward_split,Remote

SCHEMA='frost-forecast-native-dataset-v1'
REVIEWED_SEASONS=('2025-2026',)


class OfflineEvidence(Evidence):
    @lru_cache(maxsize=3)
    def weather(self,month):
        if not (self.root/'weather'/(month+'.csv')).exists():
            raise ValueError('Original weather bytes missing locally; refresh verified sources explicitly before offline assembly.')
        return super().weather(month)


def code_hash():
    from app.services.frost_dataset_contract import code_hash as observation_code
    return vault.digest(observation_code().encode()+Path(__file__).read_bytes().replace(b'\r\n',b'\n')+
        Path(native.__file__).read_bytes().replace(b'\r\n',b'\n')+Path(hrrr.__file__).read_bytes().replace(b'\r\n',b'\n'))


def sealed(value):
    body={k:v for k,v in value.items() if k!='fingerprint'}
    return dict(body,fingerprint=vault.digest(vault.canonical(body)))


def verify(value):
    if sealed(value)!=value:raise ValueError('Native dataset/artifact fingerprint mismatch.')
    return value


def cached_head(root,cycle,hour):
    url='https://noaa-hrrr-bdp-pds.s3.amazonaws.com/'+cycle.strftime('hrrr.%Y%m%d/conus/hrrr.t%Hz.wrfsfcf')+f'{hour:02d}.grib2'
    path=Path(root)/'sources'/(vault.digest(('HEAD '+url).encode())+'.json')
    if not path.exists():return None
    receipt=read_json(path)
    if receipt.get('url')!=url or receipt.get('method')!='HEAD' or receipt.get('bytes')!=0 or receipt.get('sha256')!=vault.digest(b''):
        raise ValueError('Original forecast availability receipt mismatch.')
    return receipt


def checked_point(root,cycle,cache):
    name=cycle.strftime('%Y%m%d%H')
    if name in cache:return cache[name]
    path=Path(root)/'cycles'/(name+'.json')
    if not path.exists():return None
    point=verify(read_json(path));clock.station(point['station'])
    if point['schema']!=hrrr.VERSION or clock.instant(point['cycle_at_utc'])!=cycle:
        raise ValueError('Forecast point cycle/version mismatch.')
    grid=point['point']
    if (grid['x_index'],grid['y_index'])!=(1130,677) or abs(grid['latitude']-42.203089958)>.001 or abs(grid['longitude']+89.099534260)>.001:
        raise ValueError('Unverified KRFD grid cell.')
    for receipt in point['source_receipts']:
        digest=receipt['sha256']
        if not re.fullmatch('[a-f0-9]{64}',digest):raise ValueError('Unsafe source hash.')
        raw=b''
        if receipt['method']!='HEAD':
            with (Path(root)/'sources'/(digest+'.source')).open('rb') as handle:raw=handle.read(8*1024**2+1)
        if len(raw)>8*1024**2 or len(raw)!=receipt['bytes'] or vault.digest(raw)!=digest:raise ValueError('Raw forecast evidence hash mismatch.')
    if len(cache)>=3:cache.pop(next(iter(cache)))
    cache[name]=point
    return point


def outcome_population(root):
    path=Path(root)/'catalog.json'
    if not path.exists():return []
    catalog=verify(read_json(path));result=[];seen=set()
    if catalog['schema']!='frost-outcome-catalog-v1':raise ValueError('Unexpected outcome catalog.')
    for entry in catalog['observations']:
        if not entry['archived']:continue
        if entry['source_id'] in seen:raise ValueError('Duplicate outcome identity.')
        seen.add(entry['source_id']);sha=entry['sha256']
        if not re.fullmatch('[a-f0-9]{64}',sha):raise ValueError('Unsafe outcome hash.')
        payload=read_json(Path(root)/'objects'/(sha+'.json'))
        if vault.digest(vault.canonical(payload))!=sha or payload.get('schema_version') not in ('frost-outcome-evidence-v1','frost-physical-inspection-v1') or payload.get('id')!=entry['source_id']:
            raise ValueError('Operational outcome integrity/schema mismatch.')
        result.append(dict(source_sha256=sha,evidence=payload,
            native_treatment_target_eligible=False,
            physical_model_eligible=False,
            physical_inspection=payload.get('schema_version')=='frost-physical-inspection-v1',
            reason='Physical inspections/no-spray evidence are separate from other-treatment class 0. Independent forecast coverage, exposure and pretreatment quality gates are still required.'))
    return result


def assemble(directory,forecast_root,output):
    local_only();evidence=OfflineEvidence(directory)
    roots=[Path(r) for r in forecast_root] if isinstance(forecast_root,(list,tuple)) else [Path(forecast_root)]
    if not 1<=len(roots)<=24:raise ValueError('Bound the number of incremental season caches to 24.')
    prepared=read_json(evidence.root/'prepared.json')
    if prepared['manifest']['fingerprint']!=evidence.manifest['fingerprint'] or vault.digest(vault.canonical(prepared['examples']))!=evidence.manifest['eligibility']['examples_sha256']:
        raise ValueError('Historical examples no longer match sealed local evidence.')
    for document in evidence.manifest['source_catalog']['documents']:
        raw=(evidence.root/'originals'/(document['sha256']+'.csv')).read_bytes()
        if len(raw)!=document['bytes'] or vault.digest(raw)!=document['sha256']:raise ValueError('Original Xchange evidence integrity failure.')
    available={};source_cases=[];collections=[];body_hashes=set()
    for root in roots:
        original=read_json(root/'cases.json')
        source_cases.append(vault.digest((root/'cases.json').read_bytes()))
        collections.append(dict(cache=root.name,source_cases_sha256=source_cases[-1],
            nights_attempted=len({r['night'] for r in original['cases']+original.get('gaps',[])}),
            cycles_with_cases=len({r['cycle_at_utc'] for r in original['cases']}),
            cycle_gaps=original.get('gaps',[]),cases_retained=len(original['cases']),
            selection=original['selection']))
        if original['source_dataset_fingerprint']!=evidence.manifest['fingerprint']:
            from app.services.frost_dataset_contract import validate
            prior=validate(read_json(evidence.root/'manifests'/(original['source_dataset_fingerprint']+'.json')),historical=True)
            if any(prior[k]!=evidence.manifest[k] for k in ('partitions','weather','source_catalog','version','weather_version','time_contract')):
                raise ValueError('Forecast cache uses different historical sources.')
        for case in original['cases']:
            if case['input_lead_hours']!=0:continue
            key=(case['group_id'],case['warning_hours'])
            if key in available and available[key][0]!=case:raise ValueError('Conflicting overlapping forecast cases; explicit revision review required.')
            available[key]=(case,root)
    # Phase 8 selected one application/night in 2025-26. Reuse, do not silently
    # pretend other applications/seasons already have forecast evidence.
    rows=[];excluded=Counter();cache={}
    for row in prepared['examples']:
        for lead in native.HORIZONS:
            entry=available.get((row['group_id'],lead or 1))
            if not entry:excluded[f'h{lead}_no_cached_forecast_case']+=1;continue
            case,root=entry
            if any(case[k]!=row[k] for k in ('season','night','label','sources')) or case['event_at_utc']!=row['event_at']:
                raise ValueError('Historical outcome/source provenance mismatch.')
            target=clock.instant(row['event_at']);cutoff=target-timedelta(hours=lead)
            cycle=clock.instant(case['cycle_at_utc']);point=checked_point(root,cycle,cache.setdefault(str(root),{}))
            if point is None:excluded[f'h{lead}_missing_point']+=1;continue
            body_hashes.update(r['sha256'] for r in point['source_receipts'] if r['method']!='HEAD')
            if point['fingerprint']!=case['point_fingerprint']:raise ValueError('Forecast checkpoint changed.')
            valid=target.replace(minute=0,second=0,microsecond=0)
            final_hour=int((valid-cycle).total_seconds()/3600);snapshots=[]
            # Never invent availability for intermediate hours merely because
            # the cycle exists. Missing verified hours yield missing trends.
            for hour in point['forecast_hours']:
                if hour>final_hour or hour<max(1,final_hour-6):continue
                receipt=cached_head(root,cycle,hour)
                if not receipt or not hrrr.available(receipt,cycle,cutoff):continue
                snapshots.append(dict(station=weather.STATION,values=hrrr.normalized(point,hour),
                    cycle_at_utc=cycle.isoformat(),issued_at_utc=point.get('issued_at_utc'),
                    valid_at_utc=(cycle+timedelta(hours=hour)).isoformat(),
                    available_by_utc=parsedate_to_datetime(receipt['last_modified']).isoformat(),
                    availability_basis='NOAA_original_object_Last-Modified',source_sha256=point['fingerprint']))
            if not any(clock.instant(s['valid_at_utc'])==valid for s in snapshots):
                excluded[f'h{lead}_target_availability_unproven']+=1;continue
            features=native.build(evidence.observations(cutoff),snapshots,cutoff=cutoff.isoformat(),target=target.isoformat(),lead_hours=lead)
            if not features['core_available']:excluded[f'h{lead}_missing_core_weather']+=1;continue
            rows.append(dict(group_id=row['group_id'],season=row['season'],night=row['night'],label=row['label'],
                target=native.TARGET,evidence_strengths=row['classifications'],sources=row['sources'],
                at=cutoff.isoformat(),event_at=target.isoformat(),lead_hours=lead,**features))
    coverage={season:{str(h):len({r['night'] for r in rows if r['season']==season and r['lead_hours']==h}) for h in native.HORIZONS}
        for season in sorted({r['season'] for r in prepared['examples']})}
    outcomes=outcome_population(Path(output)/'outcomes')
    credible=sum(r['evidence']['learning_eligible'] and r['evidence']['evidence_active'] and
        r['evidence'].get('classification',{}).get('label')=='LIKELY FROST ABSENT' for r in outcomes)
    physical=[r['evidence'] for r in outcomes if r['physical_inspection'] and r['evidence']['evidence_active']]
    result=sealed(dict(schema=SCHEMA,feature_version=native.VERSION,implementation_sha256=code_hash(),source_dataset=evidence.manifest['fingerprint'],
        source_catalog=evidence.manifest['source_catalog']['fingerprint'],source_cases_sha256=sorted(set(source_cases)),
        observation_version=weather.VERSION,classification_version=prepared['manifest']['version'],examples=rows,excluded=dict(excluded),coverage=coverage,
        forecast_collections=collections,verified_forecast_body_hashes=len(body_hashes),
        historical_population=evidence.manifest['quality']['classifications'],
        eligible_classes={str(h):dict(Counter(str(r['label']) for r in rows if r['lead_hours']==h)) for h in native.HORIZONS},
        operational_observations=outcomes,credible_likely_negative_evidence=credible,
        operational_evidence_catalog='verified_cached' if (Path(output)/'outcomes/catalog.json').exists() else 'not_refreshed_locally',
        credible_physical_negatives=sum(r['result']=='absent' for r in physical),
        confirmed_physical_positives=sum(r['result']=='present' for r in physical),
        pretreatment_confounded_inspections=sum(r.get('prior_pretreat') is True for r in physical),
        uncertain_physical_inspections=sum(r['result']=='uncertain' for r in physical),physical_frost_training_ready=False,
        reviewed_seasons=list(REVIEWED_SEASONS),operationally_validated=False,
        limitations=['One first recorded treatment per Phase 8 selected night; treatment-selected population',
            'Other treatment reason is not a physical frost-negative label','Historical METAR receipt time unknown',
            'NOAA archive-write bound is not exact issue/operator receipt','2025-26 already reviewed; no independent final validation claim',
            'No aircraft/parking/exposure context invented']))
    destination=Path(output);write_json(destination/'datasets'/(result['fingerprint']+'.json'),result)
    write_json(destination/'dataset.json',result)
    print(json.dumps(dict(fingerprint=result['fingerprint'],coverage=coverage,classes=result['eligible_classes'],
        training={str(h):readiness([r for r in rows if r['lead_hours']==h],None) for h in native.HORIZONS})),flush=True)
    return result


def readiness(rows,test_season):
    seasons=sorted({r['season'] for r in rows});reasons=[]
    if len(seasons)<5:reasons.append('Need at least five forecast-covered seasons for development/calibration/independent test.')
    if not test_season or test_season in REVIEWED_SEASONS or test_season<=max(REVIEWED_SEASONS):
        reasons.append('Require an independently reserved season after the reviewed 2025-26 cohort.')
    if test_season not in seasons:reasons.append('Independent test season has no verified forecast cases.')
    for season in seasons:
        counts=Counter(r['label'] for r in rows if r['season']==season)
        if min(counts.get(0,0),counts.get(1,0))<20 or sum(counts.values())<100:
            reasons.append(season+': insufficient independent seasonal support (100 cases and 20 per class required).')
    return dict(ready=not reasons,reasons=reasons,seasons=seasons,physical_frost_probability=False)


def coverage_report(dataset):
    """Source-traceable counts/missingness, not a claim of forecast skill."""
    local_only();verify(dataset)
    if dataset.get('implementation_sha256')!=code_hash():raise ValueError('Reassemble cached evidence under the current implementation before reporting.')
    rows=dataset['examples'];horizons=[]
    for lead in native.HORIZONS:
        selected=[r for r in rows if r['lead_hours']==lead]
        identities=[(r['season'],r['night']) for r in selected]
        if len(set(identities))!=len(identities):raise ValueError('Duplicate operational night in a lead-specific research population.')
        horizons.append(dict(hours=lead,cases=len(selected),likely_positive=sum(r['label']==1 for r in selected),
            other_treatment=sum(r['label']==0 for r in selected),validated_cases=0,
            seasons={season:dict(nights=sum(r['season']==season for r in selected),
                classes=dict(Counter(str(r['label']) for r in selected if r['season']==season))) for season in sorted({r['season'] for r in rows})},
            missing_features=dict(Counter(name for r in selected for name in r['missing'])),
            quality_dispositions=dict(Counter(q.get('disposition','unknown') for r in selected for q in r.get('quality',[]))),
            training=readiness(selected,None)))
    return sealed(dict(schema='frost-forecast-native-coverage-v1',dataset_fingerprint=dataset['fingerprint'],
        feature_version=native.VERSION,source_catalog=dataset['source_catalog'],eligible_cases=len(rows),
        eligible_nights=len({(r['season'],r['night']) for r in rows}),horizons=horizons,
        excluded=dataset['excluded'],forecast_collections=dataset.get('forecast_collections',[]),
        verified_forecast_body_hashes=dataset.get('verified_forecast_body_hashes',0),confirmed_physical_positives=dataset['confirmed_physical_positives'],
        credible_physical_negatives=dataset['credible_physical_negatives'],
        operational_evidence_catalog=dataset.get('operational_evidence_catalog','unknown_legacy_cache'),
        physical_frost_training_ready=False,approved=False,operationally_validated=False))


def train(dataset,*,lead_hours,test_season,version):
    local_only();verify(dataset)
    if dataset['schema']!=SCHEMA or dataset['feature_version']!=native.VERSION or dataset.get('implementation_sha256')!=code_hash():raise ValueError('Unsupported/stale native dataset contract; assemble from verified caches.')
    native.contract(lead_hours)
    rows=[r for r in dataset['examples'] if r['lead_hours']==lead_hours]
    if len({(r['season'],r['night']) for r in rows})!=len(rows):raise ValueError('Duplicate operational nights cannot satisfy seasonal training gates.')
    if any(r['contract']!=native.contract(lead_hours) or len(r['features'])!=len(native.FEATURES) or
        clock.instant(r['event_at'])-clock.instant(r['at'])!=timedelta(hours=lead_hours) for r in rows):raise ValueError('Mixed/leaking horizon features.')
    gate=readiness(rows,test_season)
    if not gate['ready']:raise ValueError('Training blocked: '+' '.join(gate['reasons']))
    seasons=gate['seasons']
    if test_season!=seasons[-1]:raise ValueError('Final holdout must follow all development/calibration seasons.')
    reports=[]
    score=lambda candidate,r:model.score_vector(candidate,r['features'],len(native.FEATURES))
    reference=lambda r:.8 if r['features'][native.FEATURES.index('fcst_temperature_c')]<=2 and r['features'][native.FEATURES.index('fcst_spread_c')]<=2 else .2
    for index in range(2,len(seasons)-2):
        fitting,testing=forward_split(rows,seasons[:index],seasons[index]);fold={}
        for kind in ('logistic','forest'):
            candidate=fit(fitting,kind,feature_names=native.FEATURES)
            fold[kind]=metrics([r['label'] for r in testing],[score(candidate,r) for r in testing])
        reports.append(dict(season=seasons[index],metrics=fold))
    selected=min(('logistic','forest'),key=lambda k:sum(r['metrics'][k]['brier'] for r in reports)/len(reports))
    fitting,calibration=forward_split(rows,seasons[:-2],seasons[-2]);candidate=fit(fitting,selected,feature_names=native.FEATURES)
    from sklearn.linear_model import LogisticRegression
    import math
    logits=[[math.log(max(1e-12,p)/max(1e-12,1-p))] for p in [score(candidate,r) for r in calibration]]
    calibrator=LogisticRegression(C=1,random_state=0).fit(logits,[r['label'] for r in calibration])
    candidate['calibration']=dict(kind='sigmoid',slope=float(calibrator.coef_[0][0]),intercept=float(calibrator.intercept_[0]))
    _,testing=forward_split(rows,seasons[:-1],test_season)
    labels=[r['label'] for r in testing];prior=sum(r['label'] for r in fitting)/len(fitting)
    regimes={name:metrics([r['label'] for r in subset],[score(candidate,r) for r in subset]) for name,subset in (
        ('above_freezing',[r for r in testing if r['features'][native.FEATURES.index('fcst_temperature_c')]>0]),
        ('missing_features',[r for r in testing if r['missing']]))}
    return sealed(dict(schema='frost-forecast-native-candidate-v1',model_version=version,contract=native.contract(lead_hours),
        dataset_fingerprint=dataset['fingerprint'],target=native.TARGET,physical_frost_probability=False,
        development=reports,calibration_season=seasons[-2],test_season=test_season,
        evaluation=metrics(labels,[score(candidate,r) for r in testing]),regimes=regimes,
        forecast_reference=metrics(labels,[reference(r) for r in testing]),climatology=metrics(labels,[prior]*len(testing)),
        independent_test_nights=len({r['night'] for r in testing}),seed=0,
        approved=False,operationally_validated=False,registry_compatible=False,
        trained_at_utc=datetime.now(timezone.utc).isoformat(),**candidate))


def refresh_outcomes(remote,root):
    local_only();root=Path(root);before=None;catalog=[]
    # Finite, paginated metadata. Cached content hashes avoid duplicate downloads.
    for _ in range(100):
        page=json.loads(remote.request('GET','/neosubzero/frost-vault/observations'+(f'?before={before}' if before else '')))
        if page['schema']!='frost-outcome-catalog-v1':raise ValueError('Unsupported operational evidence catalog.')
        for row in page['observations']:
            if not re.fullmatch('[a-f0-9]{64}',row['sha256']):raise ValueError('Unsafe outcome hash.')
            catalog.append(row)
            if not row['archived']:continue
            path=root/'objects'/(row['sha256']+'.json')
            raw=path.read_bytes() if path.exists() else remote.request('GET',row['url'])
            payload=json.loads(raw)
            if vault.digest(vault.canonical(payload))!=row['sha256']:raise ValueError('Outcome source revision mismatch.')
            if not path.exists():write_json(path,payload)
        before=page['next_before']
        if not before:break
    else:raise ValueError('Outcome metadata bound reached; no unbounded download.')
    write_json(root/'catalog.json',sealed(dict(schema='frost-outcome-catalog-v1',observations=catalog)))
    return catalog


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--local',required=True,action='store_true')
    p.add_argument('--directory',default='instance/frost-local/phase8/dataset');p.add_argument('--forecasts',action='append',help='Repeat for incremental season caches; duplicates reconcile by application/horizon.')
    p.add_argument('--output',default='instance/frost-local/phase9')
    commands=p.add_subparsers(dest='command',required=True);commands.add_parser('assemble');commands.add_parser('validate');commands.add_parser('coverage')
    t=commands.add_parser('train');t.add_argument('--lead-hours',required=True,type=int,choices=native.HORIZONS);t.add_argument('--test-season',required=True);t.add_argument('--version',required=True)
    t=commands.add_parser('refresh-outcomes');t.add_argument('--origin',required=True);t.add_argument('--email',required=True)
    a=p.parse_args(argv);local_only();root=Path(a.output)
    repository=Path(__file__).resolve().parents[1]
    try:relative=root.resolve().relative_to(repository)
    except ValueError:relative=None
    if relative is not None and (not relative.parts or relative.parts[0] not in ('instance','_local_archive')):raise ValueError('Use an ignored private output directory.')
    from scripts.frost_shadow_collect import exclusive
    with exclusive(root):
        if a.command=='assemble':assemble(a.directory,a.forecasts or ['instance/frost-local/phase8/hrrr'],root)
        elif a.command=='refresh-outcomes':
            remote=Remote(a.origin,a.email)
            try:print('Verified outcome receipts:',len(refresh_outcomes(remote,root/'outcomes')))
            finally:remote.close()
        else:
            dataset=verify(read_json(root/'dataset.json'))
            if a.command=='validate':print(json.dumps({str(h):readiness([r for r in dataset['examples'] if r['lead_hours']==h],None) for h in native.HORIZONS}))
            elif a.command=='coverage':
                report=coverage_report(dataset);write_json(root/'coverage-report.json',report)
                print(json.dumps(report),flush=True)
            else:
                if not re.fullmatch('[A-Za-z0-9_-]{1,64}',a.version):raise ValueError('Unsafe model version.')
                destination=root/'candidates'/(a.version+'.json')
                if destination.exists():raise ValueError('Immutable candidate already exists; choose a new version.')
                write_json(root/'training-readiness.json',dict(dataset_fingerprint=dataset['fingerprint'],lead_hours=a.lead_hours,
                    test_season=a.test_season,**readiness([r for r in dataset['examples'] if r['lead_hours']==a.lead_hours],a.test_season)))
                artifact=train(dataset,lead_hours=a.lead_hours,test_season=a.test_season,version=a.version)
                write_json(destination,artifact);print('LOCAL research artifact SHA:',vault.digest(vault.canonical(artifact)))


if __name__=='__main__':
    try:main()
    except (ValueError,RuntimeError,OSError) as error:
        print(str(error),file=sys.stderr);raise SystemExit(2)
