"""LOCAL prospective research: frozen artifacts, pre-event captures and outcomes.

No training, registry writes, approval, production credentials or cloud worker.
Run ``python -m scripts.frost_prospective --local --help``.
"""
import argparse
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.services import frost_analysis as analysis, frost_forecast as clock
from app.services import frost_forecast_native as native, frost_model as model
from app.services import frost_vault as vault, frost_weather as weather
from scripts.frost_local import local_only, read_json, write_bytes, write_json, Remote
from scripts.frost_forecast_native import sealed, verify

VERSION = 'frost-prospective-v1'
POLICY = dict(version='frost-prospective-matching-v1', timezone='America/Chicago',
    season='2026-2027', target_window_minutes=60,
    unit='physical tail / Central noon-to-noon night / horizon / model / evidence cohort',
    selection='First subsequent eligible outcome per tail/night/cohort; latest preceding target window; never select by score',
    primary='First matched physical tail by outcome timestamp then source hash per night/cohort/model/horizon',
    unknown='Missing, cancelled, generic CLEAR and absence of treatment remain UNKNOWN',
    physical='Treatment-reason scores are transfer diagnostics, not calibrated physical-frost probabilities',
    independence='Only predictions durably recorded after baseline freeze and before outcomes; reviewed historical seasons excluded',
    clock='Workstation UTC clock and immutable hashes; not an independent trusted timestamp authority')


def utcnow():
    return datetime.now(timezone.utc)


def immutable(path, value):
    """Exclusive creation; restart may reuse identical bytes, never revise evidence."""
    path = Path(path); raw = vault.canonical(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    from uuid import uuid4
    temporary=path.with_name(path.name+'.'+uuid4().hex+'.partial')
    try:
        with temporary.open('xb') as handle:
            handle.write(raw); handle.flush()
            import os
            os.fsync(handle.fileno())
        try: os.link(temporary,path)  # Atomic no-replace commit, including on Windows.
        except FileExistsError:
            if path.read_bytes() != raw: raise ValueError('Immutable prospective evidence already exists with different bytes.')
    finally:
        if temporary.exists(): temporary.unlink()  # Only this call's temporary file.
    return value


def freeze(candidates, reports, output):
    local_only(); output = Path(output); entries = []
    if (output/'baseline.json').exists(): return baseline(output/'baseline.json')
    for hours in native.HORIZONS:
        report_paths = list(Path(reports).glob(f'rfd-native-h{hours}-*-experimental-v1.json'))
        if len(report_paths) != 1: raise ValueError('Select exactly one original Phase 11 report per horizon.')
        report_raw = report_paths[0].read_bytes(); report = verify(json.loads(report_raw))
        if report['runtime']['threshold']!=.5:raise ValueError('Original threshold differs from the fixed prospective threshold.')
        for kind in ('logistic', 'forest'):
            paths = list(Path(candidates).glob(f'rfd-native-h{hours}-*-experimental-v1-{kind}.json'))
            if len(paths) != 1: raise ValueError('Select exactly one original Phase 11 model per horizon/kind.')
            raw = paths[0].read_bytes(); artifact = verify(json.loads(raw))
            check_artifact(artifact, hours)
            digest = vault.digest(raw)
            destination = output/'artifacts'/(digest+'.json')
            if destination.exists() and destination.read_bytes()!=raw: raise ValueError('Frozen model bytes changed.')
            if not destination.exists(): write_bytes(destination, raw)
            report_digest = vault.digest(report_raw)
            report_destination = output/'reports'/(report_digest+'.json')
            if report_destination.exists() and report_destination.read_bytes()!=report_raw: raise ValueError('Frozen report bytes changed.')
            if not report_destination.exists(): write_bytes(report_destination, report_raw)
            for check in artifact['parity_checks']:
                if abs(model.score_vector(artifact, check['features'], len(native.FEATURES))-check['score'])>1e-9:
                    raise ValueError('Frozen artifact numerical self-test failed.')
            entries.append(dict(model_version=artifact['model_version'], sha256=digest,
                artifact_fingerprint=artifact['fingerprint'], bytes=len(raw), hours=hours, kind=kind,
                threshold=.5, report_sha256=report_digest, original_evaluation=artifact['evaluation'],
                training=artifact['training'], dataset_fingerprint=artifact['dataset_fingerprint']))
    result = sealed(dict(schema=VERSION, frozen_at_utc=utcnow().isoformat(), feature_version=native.VERSION,
        research_policy='frost-native-exploratory-v2', target=native.TARGET, matching_policy=POLICY,
        seasons=report['eligibility']['seasons'],
        models=entries, approved=False, operationally_validated=False, physical_frost_probability=False))
    immutable(output/'baseline.json', result)
    return baseline(output/'baseline.json')


def check_artifact(artifact, hours):
    if (artifact.get('schema')!='frost-native-experimental-candidate-v2' or
        artifact.get('contract')!=native.contract(hours) or artifact.get('policy')!='frost-native-exploratory-v2' or
        artifact.get('target')!=native.TARGET or any(artifact.get(k) is not False for k in
            ('approved','operationally_validated','physical_frost_probability','registry_compatible'))):
        raise ValueError('Only the exact unapproved Phase 11 research contract is supported.')


def baseline(path):
    local_only(); path = Path(path); value = verify(read_json(path))
    if value['schema']!=VERSION or value['matching_policy']!=POLICY or value['feature_version']!=native.VERSION:
        raise ValueError('Prospective baseline/policy mismatch; create a separate version, never retune this baseline.')
    if [(r['hours'],r['kind']) for r in value['models']]!=[(h,k) for h in native.HORIZONS for k in ('logistic','forest')]:
        raise ValueError('Baseline must retain all eight horizon-specific models.')
    for entry in value['models']:
        if not re.fullmatch('[a-f0-9]{64}',entry['sha256']) or entry['threshold']!=.5: raise ValueError('Frozen identity/threshold changed.')
        raw = (path.parent/'artifacts'/(entry['sha256']+'.json')).read_bytes()
        if len(raw)!=entry['bytes'] or vault.digest(raw)!=entry['sha256']: raise ValueError('Frozen artifact hash mismatch.')
        artifact = verify(json.loads(raw)); check_artifact(artifact, entry['hours'])
        if artifact['fingerprint']!=entry['artifact_fingerprint'] or artifact['model_version']!=entry['model_version']:
            raise ValueError('Frozen model identity mismatch.')
        report_raw=(path.parent/'reports'/(entry['report_sha256']+'.json')).read_bytes()
        if vault.digest(report_raw)!=entry['report_sha256']: raise ValueError('Original evaluation report changed.')
        report=verify(json.loads(report_raw))
        if report['runtime']['threshold']!=entry['threshold'] or report['eligibility']['seasons']!=value['seasons']:
            raise ValueError('Original threshold or training-season evidence differs from the frozen baseline.')
    return value


def acquire(root):
    """Bounded, source-preserving LOCAL acquisition; no outcome-selected dates."""
    local_only()
    from scripts.frost_forecast_validation import cached_get, taf_day
    from scripts import frost_hrrr as hrrr
    from app.services.frost_inference import normalize_metars
    root=Path(root); root.mkdir(parents=True,exist_ok=True); errors=[]; receipts=[]; observations=[]; snapshots=[]
    try:
        raw, receipt=cached_get(root/'weather','https://aviationweather.gov/api/data/metar?ids=KRFD&format=json&hours=14')
        observations=normalize_metars(json.loads(raw)); receipts.append(receipt)
        for row in observations: row['received_at_utc']=receipt['retrieved_at_utc']
    except Exception as error: errors.append(dict(source='KRFD METAR',error_type=type(error).__name__))
    # NWS/TAF are retained as context, never silently substituted for native HRRR fields.
    try:
        raw, receipt=cached_get(root/'weather','https://api.weather.gov/points/42.1915,-89.0876')
        points=json.loads(raw); coordinates=points['geometry']['coordinates']
        if abs(coordinates[0]+89.08760)>.0001 or abs(coordinates[1]-42.19151)>.0001: raise ValueError('Wrong NWS point.')
        url=points['properties']['forecastGridData']
        if not re.fullmatch(r'https://api.weather.gov/gridpoints/LOT/\d+,\d+',url): raise ValueError('Wrong KRFD forecast grid.')
        receipts.append(receipt); _, receipt=cached_get(root/'weather',url); receipts.append(receipt)
    except Exception as error: errors.append(dict(source='NWS context',error_type=type(error).__name__))
    try:
        _, receipt=taf_day(root/'weather',utcnow()); receipts.append(receipt)
    except Exception as error: errors.append(dict(source='TAF context',error_type=type(error).__name__))
    client=hrrr.Sources(root/'hrrr'/'sources',32)
    try:
        # One conservative completed cycle, point chunks only; bounded 32 MiB.
        cycle=utcnow().replace(minute=0,second=0,microsecond=0)-timedelta(hours=3)
        point=sealed(hrrr.cycle_point(client,cycle)); write_json(root/'hrrr'/'cycles'/(cycle.strftime('%Y%m%d%H')+'.json'),point)
        for hour in point['forecast_hours']:
            if not 1<=hour<=10: continue
            url='https://noaa-hrrr-bdp-pds.s3.amazonaws.com/'+cycle.strftime('hrrr.%Y%m%d/conus/hrrr.t%Hz.wrfsfcf')+f'{hour:02d}.grib2'
            try:
                _, receipt=client.get(url,head=True)
                if not hrrr.available(receipt,cycle,utcnow()): continue
                receipts.append(receipt)
                snapshots.append(dict(station=weather.STATION,values=hrrr.normalized(point,hour),
                    cycle_at_utc=cycle.isoformat(),issued_at_utc=point.get('issued_at_utc'),
                    valid_at_utc=(cycle+timedelta(hours=hour)).isoformat(),
                    available_by_utc=parsedate_to_datetime(receipt['last_modified']).isoformat(),
                    availability_basis='NOAA_original_object_Last-Modified',source_sha256=point['fingerprint']))
            except Exception as error: errors.append(dict(source=f'HRRR f{hour}',error_type=type(error).__name__))
    except Exception as error: errors.append(dict(source='HRRR point',error_type=type(error).__name__))
    finally: client.close()
    return dict(station=weather.STATION,observations=observations,forecasts=snapshots,receipts=receipts,errors=errors)


def capture(baseline_path, output, *, interval=3600, provider=None):
    local_only()
    from scripts.frost_shadow_collect import exclusive
    if not 300<=interval<=86400: raise ValueError('Bound the capture cadence.')
    frozen=baseline(baseline_path); root=Path(output)
    with exclusive(root):
        now=utcnow(); slot=int(now.timestamp())//interval
        destination=root/'captures'/(frozen['fingerprint']+f'-{interval}-{slot}.json')
        if destination.exists():
            existing=verify(read_json(destination))
            # A power loss after snapshot fsync but before its receipt does not
            # justify backdating publication. Recover with today's honest time.
            receipt_path=root/'receipts'/(existing['fingerprint']+'.json')
            if not receipt_path.exists(): immutable(receipt_path,dict(snapshot=existing['fingerprint'],committed_at_utc=utcnow().isoformat()))
            return existing
        if now<clock.instant(frozen['frozen_at_utc']): raise ValueError('Clock precedes the frozen baseline.')
        try:
            source=(provider or acquire)(root/'sources'/str(slot)); clock.station(source['station'])
        except Exception as error:
            failed=dict(schema=VERSION,attempted_at_utc=now.isoformat(),state='FAILED',error_type=type(error).__name__)
            immutable(root/'attempts'/(vault.digest(vault.canonical(failed))+'.json'),failed)
            raise
        cutoff=utcnow(); rows=[]
        for entry in frozen['models']:
            hours=entry['hours']; target=cutoff+timedelta(hours=hours)
            row=dict(model_version=entry['model_version'],model_sha256=entry['sha256'],threshold=entry['threshold'],
                hours=hours,target_at_utc=target.isoformat(),target=native.TARGET,physical_frost_probability=False,
                state='UNAVAILABLE',score=None,classification=None)
            try:
                snapshots=[s for s in source['forecasts'] if clock.instant(s['valid_at_utc'])<=target]
                vector=native.build(source['observations'],snapshots,cutoff=cutoff.isoformat(),target=target.isoformat(),lead_hours=hours)
                allowed=[r for r in source['observations'] if clock.instant(r['utc'])<=cutoff and
                    (not r.get('received_at_utc') or clock.instant(r['received_at_utc'])<=cutoff)]
                if not vector['core_available'] or analysis.features(cutoff.isoformat(),allowed)['incomplete_window']:
                    raise ValueError('Missing core weather or incomplete preceding observation window.')
                artifact=read_json(Path(baseline_path).parent/'artifacts'/(entry['sha256']+'.json'))
                score=model.score_vector(artifact,vector['features'],len(native.FEATURES))
                if not math.isfinite(score) or not 0<=score<=1: raise ValueError('Invalid research score.')
                row.update(state='SCORED',score=score,classification=score>=entry['threshold'],vector=vector)
            except (ValueError,KeyError,TypeError) as error:
                row.update(reason=str(error)[:160],missing_inputs='No prediction; source gaps retained, never filled using later weather')
            rows.append(row)
        prior_path=root/'collector-state.json'; prior=read_json(prior_path) if prior_path.exists() else {}
        previous=prior.get('last_capture_at_utc'); missed=0
        if previous: missed=max(0,int((now-clock.instant(previous)).total_seconds()//interval)-1)
        # This is the creation boundary, not the provider's issue time. Evaluation
        # additionally requires the journal receipt to precede each actual outcome.
        result=sealed(dict(schema=VERSION,baseline=frozen['fingerprint'],policy=POLICY,
            capture_started_at_utc=now.isoformat(),captured_at_utc=utcnow().isoformat(),cutoff_at_utc=cutoff.isoformat(),
            interval_seconds=interval,slot=slot,missed_intervals=missed,previous_capture_at_utc=previous,
            source=source,predictions=rows,approved=False,operationally_validated=False))
        immutable(destination,result)
        immutable(root/'receipts'/(result['fingerprint']+'.json'),
            dict(snapshot=result['fingerprint'],committed_at_utc=utcnow().isoformat()))
        write_json(prior_path,dict(last_capture_at_utc=result['captured_at_utc'],fingerprint=result['fingerprint']))
        return result


def refresh_outcomes(remote, directory):
    """Existing authenticated private archive; no DB credentials, no operational writes."""
    local_only(); root=Path(directory); entries={}; before=None
    while True:
        url='/neosubzero/frost-vault/prospective-outcomes'+(f'?before={before}' if before else '')
        page=json.loads(remote.request('GET',url))
        if page['schema']!=VERSION: raise ValueError('Unexpected prospective archive catalog.')
        for entry in page['observations']:
            if not re.fullmatch('[a-f0-9]{64}',entry['sha256']): raise ValueError('Unsafe evidence hash.')
            if entry['source_id'] in entries: raise ValueError('Duplicate outcome identity across pages; retain the previous complete catalog.')
            destination=root/'objects'/(entry['sha256']+'.json')
            if entry['archived']:
                if not destination.exists():
                    if not re.fullmatch(r'/neosubzero/frost-vault/prospective-outcomes/\d+',entry['url']): raise ValueError('Unsafe archive URL.')
                    payload=json.loads(remote.request('GET',entry['url']))
                    if vault.digest(vault.canonical(payload))!=entry['sha256']: raise ValueError('Outcome revision hash mismatch.')
                    immutable(destination,payload)
                if vault.digest(destination.read_bytes())!=entry['sha256']: raise ValueError('Local evidence changed.')
            entries[entry['source_id']]=entry
        following=page['next_before']
        if following is None: break
        if type(following) is not int or (before is not None and following>=before): raise ValueError('Invalid archive pagination.')
        before=following
    result=sealed(dict(schema=VERSION,retrieved_at_utc=utcnow().isoformat(),observations=sorted(entries.values(),key=lambda r:r['source_id'])))
    immutable(root/'catalogs'/(result['fingerprint']+'.json'),result);write_json(root/'catalog.json',result)
    return result


def outcome(payload, source_hash):
    """Explicit evidence only. No negative labels from missing activity."""
    schema=payload.get('schema_version'); cohort=None; label=None; confounded=False
    time_contract='explicit source offset'
    if schema=='xchange-prospective-outcome-v1':
        interpreted=analysis.classification(payload['reason'])
        cohort='anticipated_pretreatment' if interpreted['activity']=='PRETREAT' else 'treatment_reason'
        label=1 if interpreted['label']=='LIKELY FROST PRESENT' else 0 if interpreted['label']=='NON-FROST TREATMENT' else None
        confounded=cohort=='anticipated_pretreatment';at=payload['start_utc']
        time_contract=analysis.TIME_CONTRACT
    elif schema=='subzero-spray/v1' and payload.get('record_type')=='actual_fluid_application' and payload.get('activity') in ('departure','pretreat'):
        from app.services.frost_analysis import classification
        # Canonical Xchange reason mapping is reused, never guessed from absence.
        interpreted=classification(payload.get('reason'))
        if interpreted['label']=='LIKELY FROST PRESENT': label=1
        elif interpreted['label']=='NON-FROST TREATMENT': label=0
        cohort='treatment_reason' if payload['activity']=='departure' else 'anticipated_pretreatment'
        if cohort=='anticipated_pretreatment':label=None;confounded=True
        at=payload['start_utc']
        # Existing canonical DateTime columns and archive_payload's explicitly
        # named start_utc field are UTC even when isoformat omits the suffix.
        # This narrow schema contract must not relax forecast/inspection clocks.
        parsed=datetime.fromisoformat(at)
        if parsed.tzinfo is None:
            at=parsed.replace(tzinfo=timezone.utc).isoformat()
            time_contract='subzero-spray/v1 canonical start_utc; naive database UTC normalized, original preserved'
    elif schema=='frost-physical-inspection-v1':
        if not payload.get('evidence_active'): return None
        cohort='physical_inspection'; at=payload['observed_at_utc']
        label=1 if payload.get('result')=='present' else 0 if payload.get('result')=='absent' else None
        confounded=payload.get('prior_pretreat') is True
    elif schema=='frost-outcome-evidence-v1':
        if not payload.get('evidence_active'): return None
        cohort='likely_no_treatment'; at=payload['observed_at_utc']
        confounded=payload.get('prior_pretreat') is True
        if payload.get('outcome')=='explicit_not_sprayed' and payload.get('learning_eligible') and payload.get('prior_pretreat') is False: label=0
    else: return None
    if not payload.get('tail'): return None
    return dict(source_sha256=source_hash,source_id=payload['id'],tail=payload['tail'],at=clock.instant(at).isoformat(),
        cohort=cohort,label=label,confounded=confounded,prior_pretreat=payload.get('prior_pretreat'),time_contract=time_contract,
        context=payload,unknown=label is None)


def refresh_auto(remote,directory):
    """Incremental authenticated verified snapshots/pages; no production DB access."""
    local_only();root=Path(directory);entries={};before=None
    while True:
        page=json.loads(remote.request('GET','/neosubzero/frost-vault/auto-evidence'+(f'?before={before}' if before else '')))
        if page['schema']!='frost-auto-v1':raise ValueError('Unexpected autonomous evidence catalog.')
        for entry in page['evidence']:
            digest=entry['sha256']
            if not re.fullmatch('[a-f0-9]{64}',digest) or not re.fullmatch(r'/neosubzero/frost-vault/auto-evidence/\d+',entry['url']):raise ValueError('Unsafe evidence reference.')
            destination=root/'objects'/(digest+'.json')
            if not destination.exists():
                raw=remote.request('GET',entry['url'])
                if len(raw)>1024*1024 or vault.digest(vault.canonical(json.loads(raw)))!=digest:raise ValueError('Autonomous evidence checksum/size mismatch.')
                immutable(destination,json.loads(raw))
            if vault.digest(destination.read_bytes())!=digest:raise ValueError('Local autonomous evidence changed.')
            entries[entry['id']]=entry
        following=page['next_before']
        if following is None:break
        if type(following) is not int or before is not None and following>=before:raise ValueError('Invalid evidence pagination.')
        before=following
    result=sealed(dict(schema='frost-auto-v1',retrieved_at_utc=utcnow().isoformat(),evidence=sorted(entries.values(),key=lambda r:r['id'])))
    immutable(root/'catalogs'/(result['fingerprint']+'.json'),result);write_json(root/'catalog.json',result)
    return result


def assemble_auto(baseline_path,directory,output):
    """Adapt verified autonomous/Xchange evidence to the existing local evaluator.

    Source objects remain untouched. Derived adapters retain exact archive hashes.
    No guessed negatives, historical features, refitting, or model activation.
    """
    import base64
    local_only();root=Path(directory);frozen=baseline(baseline_path)
    catalog=verify(read_json(root/'catalog.json'));records=[];captures=[]
    adapted=Path(output)/'verified-adapters';entries=[]
    for entry in catalog['evidence']:
        raw=(root/'objects'/(entry['sha256']+'.json')).read_bytes()
        if vault.digest(raw)!=entry['sha256']:raise ValueError('Archived evidence hash mismatch.')
        payload=json.loads(raw)
        if payload['schema_version' if entry['kind']=='auto_capture' else 'schema']!='frost-auto-v1':raise ValueError('Evidence contract mismatch.')
        if entry['kind']=='xchange_page':records.extend(payload['rows']);continue
        if entry['kind']!='auto_capture' or payload['baseline']!=frozen['fingerprint']:raise ValueError('Frozen autonomous baseline changed.')
        source=payload['source'];clock.station(source['station']);cutoff=clock.instant(source['cutoff_at_utc'])
        if clock.instant(payload['committed_at_utc'])<cutoff:raise ValueError('Capture commit predates acquisition.')
        for receipt in source['sources']:
            original=base64.b64decode(receipt['raw_base64'],validate=True)
            if len(original)!=receipt['bytes'] or vault.digest(original)!=receipt['sha256'] or clock.instant(receipt['retrieved_at_utc'])>cutoff:raise ValueError('Original forecast evidence mismatch.')
        if any(p['state']=='SCORED' for p in payload['predictions']):
            raise ValueError('Native scores require LOCAL HRRR source-proof reconciliation; no unsupported scoring adapter.')
        snapshot=sealed(dict(schema=VERSION,baseline=frozen['fingerprint'],slot=entry['identity'],
            cutoff_at_utc=source['cutoff_at_utc'],captured_at_utc=payload['captured_at_utc'],original_capture_sha256=entry['sha256'],
            source=dict(observations=source['observations'],forecasts=[],receipts=[]),predictions=payload['predictions']))
        immutable(adapted/'captures'/(snapshot['fingerprint']+'.json'),snapshot)
        immutable(adapted/'receipts'/(snapshot['fingerprint']+'.json'),dict(snapshot=snapshot['fingerprint'],committed_at_utc=payload['committed_at_utc']))
    selected,counts=analysis.deduplicate(records)
    for item in selected:
        starts=[f['occurred_at_utc'] for f in item['fluids'] if f.get('occurred_at_utc')]
        if item['requires_review'] or not starts:continue
        payload=dict(schema_version='xchange-prospective-outcome-v1',id='xchange:'+item['source_record_id'],
            tail=item['tail'],reason=item['reason_code'],start_utc=min(starts),source_record=item,
            prior_pretreat=None)
        digest=vault.digest(vault.canonical(payload));immutable(adapted/'outcomes'/'objects'/(digest+'.json'),payload)
        entries.append(dict(source_id=payload['id'],sha256=digest,archived=True))
    write_json(adapted/'outcomes'/'catalog.json',sealed(dict(schema=VERSION,observations=entries,source_catalog=catalog['fingerprint'],deduplication=counts)))
    return assemble(baseline_path,adapted,adapted/'outcomes',Path(output)/'dataset')


def night(at):
    return (clock.instant(at).astimezone(ZoneInfo('America/Chicago'))-timedelta(hours=12)).date().isoformat()


def verify_sources(root, source, cutoff):
    """Recheck original bytes, not merely the enclosing snapshot fingerprint."""
    from scripts import frost_hrrr as hrrr
    root=Path(root)
    for receipt in source['receipts']:
        digest=receipt['sha256']
        if not re.fullmatch('[a-f0-9]{64}',digest): raise ValueError('Unsafe weather source hash.')
        if clock.instant(receipt['retrieved_at_utc'])>cutoff: raise ValueError('Source retrieved after prediction cutoff.')
        path=root/('hrrr/sources' if receipt.get('method') else 'weather')/(digest+'.source')
        raw=b'' if receipt.get('method')=='HEAD' else path.read_bytes()
        if len(raw)!=receipt['bytes'] or len(raw)>8*1024**2 or vault.digest(raw)!=digest: raise ValueError('Raw weather source hash mismatch.')
    for cycle_text in {f['cycle_at_utc'] for f in source['forecasts']}:
        cycle=clock.instant(cycle_text);path=root/'hrrr'/'cycles'/(cycle.strftime('%Y%m%d%H')+'.json')
        point=verify(read_json(path));clock.station(point['station'])
        if point['schema']!=hrrr.VERSION or point['cycle_at_utc']!=cycle_text: raise ValueError('Forecast cycle identity mismatch.')
        if (point['point']['x_index'],point['point']['y_index'])!=(1130,677): raise ValueError('Wrong KRFD grid.')
        for receipt in point['source_receipts']:
            if not re.fullmatch('[a-f0-9]{64}',receipt['sha256']): raise ValueError('Unsafe HRRR raw hash.')
            raw=(root/'hrrr'/'sources'/(receipt['sha256']+'.source')).read_bytes()
            if len(raw)!=receipt['bytes'] or vault.digest(raw)!=receipt['sha256']: raise ValueError('Raw HRRR source hash mismatch.')
        for forecast in (f for f in source['forecasts'] if f['cycle_at_utc']==cycle_text):
            hour=int((clock.instant(forecast['valid_at_utc'])-cycle).total_seconds()/3600)
            if forecast['source_sha256']!=point['fingerprint'] or forecast['values']!=hrrr.normalized(point,hour): raise ValueError('Forecast differs from its original source.')


def assemble(baseline_path, captures, outcomes, output):
    local_only(); frozen=baseline(baseline_path); catalog=verify(read_json(Path(outcomes)/'catalog.json'))
    candidates=[]; excluded=Counter(); selected={}; pretreatments=[]
    for entry in catalog['observations']:
        if not re.fullmatch('[a-f0-9]{64}',entry['sha256']): raise ValueError('Unsafe outcome content hash.')
        if not entry['archived']: excluded['archive_pending']+=1; continue
        raw=(Path(outcomes)/'objects'/(entry['sha256']+'.json')).read_bytes()
        if vault.digest(raw)!=entry['sha256']: raise ValueError('Outcome source hash mismatch.')
        payload=json.loads(raw)
        if payload.get('id')!=entry['source_id']: raise ValueError('Outcome source identity mismatch.')
        item=outcome(payload,entry['sha256'])
        if not item: excluded['not_eligible_evidence']+=1; continue
        if item['cohort']=='anticipated_pretreatment':pretreatments.append(item)
        if clock.instant(item['at'])<=clock.instant(frozen['frozen_at_utc']): excluded['pre_baseline']+=1; continue
        local=clock.instant(item['at']).astimezone(ZoneInfo('America/Chicago'))
        if not datetime(2026,7,1,tzinfo=local.tzinfo)<=local<datetime(2027,7,1,tzinfo=local.tzinfo): excluded['outside_preregistered_season']+=1; continue
        item['night']=night(item['at']); key=(item['tail'],item['night'],item['cohort'])
        if key not in selected or (item['at'],item['source_sha256'])<(selected[key]['at'],selected[key]['source_sha256']): selected[key]=item
    for item in selected.values():
        prior=[p for p in pretreatments if p['tail']==item['tail'] and p['context'].get('operation_id')==item['context'].get('operation_id') and
            clock.instant(p['at'])<=clock.instant(item['at'])]
        if prior:
            item['prior_pretreat']=True
            item['pretreat_sources']=sorted({p['source_sha256'] for p in prior})
            if item['cohort']!='treatment_reason':item['confounded']=True
    capture_hashes=[]; later_observations=[]
    for path in sorted((Path(captures)/'captures').glob('*.json')):
        snapshot=verify(read_json(path))
        if snapshot['baseline']!=frozen['fingerprint']: continue
        capture_hashes.append(snapshot['fingerprint']); later_observations.extend(snapshot['source']['observations'])
        receipt=read_json(Path(captures)/'receipts'/(snapshot['fingerprint']+'.json'))
        if receipt['snapshot']!=snapshot['fingerprint']: raise ValueError('Capture commit receipt mismatch.')
        created=clock.instant(receipt['committed_at_utc']); cutoff=clock.instant(snapshot['cutoff_at_utc'])
        if created<clock.instant(snapshot['captured_at_utc']) or cutoff<clock.instant(frozen['frozen_at_utc']): raise ValueError('Invalid pre-event capture clock.')
        if snapshot['source']['receipts']: verify_sources(Path(captures)/'sources'/str(snapshot['slot']),snapshot['source'],cutoff)
        if [p['model_version'] for p in snapshot['predictions']]!=[m['model_version'] for m in frozen['models']]: raise ValueError('Missing or duplicated frozen-model predictions.')
        for prediction in snapshot['predictions']:
            entry=next((m for m in frozen['models'] if m['model_version']==prediction['model_version']),None)
            if not entry or entry['sha256']!=prediction['model_sha256'] or entry['hours']!=prediction['hours'] or entry['threshold']!=prediction['threshold']:
                raise ValueError('Prediction differs from frozen baseline.')
            if clock.instant(prediction['target_at_utc'])-cutoff!=timedelta(hours=entry['hours']): raise ValueError('Horizon leakage.')
            if prediction['state']=='SCORED':
                vector=prediction['vector']
                if vector['contract']!=native.contract(entry['hours']) or vector['cutoff_at_utc']!=snapshot['cutoff_at_utc'] or vector['target_at_utc']!=prediction['target_at_utc']:
                    raise ValueError('Prediction feature contract changed.')
                derived=native.build(snapshot['source']['observations'],
                    [s for s in snapshot['source']['forecasts'] if clock.instant(s['valid_at_utc'])<=clock.instant(prediction['target_at_utc'])],
                    cutoff=snapshot['cutoff_at_utc'],target=prediction['target_at_utc'],lead_hours=entry['hours'])
                if derived!=vector:raise ValueError('Frozen features differ from pre-cutoff source evidence.')
                artifact=read_json(Path(baseline_path).parent/'artifacts'/(entry['sha256']+'.json'))
                score=model.score_vector(artifact,vector['features'],len(native.FEATURES))
                if score!=prediction['score'] or prediction['classification']!=(score>=entry['threshold']): raise ValueError('Frozen score/threshold mismatch.')
            candidates.append(dict(prediction,capture_sha256=snapshot['fingerprint'],created_at_utc=created.isoformat(),outcomes=[]))
    for item in sorted(selected.values(),key=lambda r:(r['at'],r['source_sha256'])):
        for entry in frozen['models']:
            eligible=[p for p in candidates if p['model_version']==entry['model_version'] and p['state']=='SCORED' and
                clock.instant(p['created_at_utc'])<clock.instant(item['at']) and
                timedelta(0)<=clock.instant(item['at'])-clock.instant(p['target_at_utc'])<timedelta(minutes=POLICY['target_window_minutes'])]
            if not eligible: excluded['outcome_model_without_pre_event_prediction']+=1; continue
            prediction=max(eligible,key=lambda p:(p['target_at_utc'],p['capture_sha256']))
            prediction['outcomes'].append(dict(item,actual_lead_minutes=(clock.instant(item['at'])-clock.instant(prediction['created_at_utc'])).total_seconds()/60,
                subsequent_weather=analysis.features(item['at'],later_observations)))
    result=sealed(dict(schema=VERSION,baseline=frozen['fingerprint'],policy=POLICY,assembled_at_utc=utcnow().isoformat(),
        capture_hashes=capture_hashes,outcome_catalog=catalog['fingerprint'],predictions=candidates,
        unique_outcomes=len(selected),excluded=dict(excluded),approved=False,operationally_validated=False))
    immutable(Path(output)/(result['fingerprint']+'.json'),result);write_json(Path(output)/'dataset.json',result)
    return result


def evaluate(dataset, output):
    local_only(); dataset=verify(dataset)
    from scripts.frost_native_research import describe
    groups=defaultdict(list); all_rows=[]
    for prediction in dataset['predictions']:
        for evidence in prediction['outcomes']:
            all_rows.append(dict(prediction=prediction,evidence=evidence))
            if evidence['label'] is None or evidence['confounded']: continue
            key=(prediction['model_version'],evidence['cohort'])
            groups[key].append(dict(night=evidence['night'],season=POLICY['season'],label=evidence['label'],score=prediction['score'],
                at=evidence['at'],source=evidence['source_sha256'],lead_minutes=evidence['actual_lead_minutes'],
                features=prediction['vector']['features']))
    metrics=[]
    for (version,cohort), rows in sorted(groups.items()):
        primary={}
        for row in sorted(rows,key=lambda r:(r['at'],r['source'])): primary.setdefault(row['night'],row)
        sample=list(primary.values()); values=describe(sample)
        if cohort!='treatment_reason':
            values.update(brier=None,ece=None,log_loss=None,reliability_bins=[],brier_interval95=None)
        def subset(name, predicate):
            chosen=[r for r in sample if predicate(dict(zip(native.FEATURES,r['features'])))]
            stats=describe(chosen)
            if cohort!='treatment_reason': stats.update(brier=None,ece=None,log_loss=None,reliability_bins=[],brier_interval95=None)
            return dict(name=name,nights=len(chosen),metrics=stats)
        def wind_shift(values):
            keys=('obs_wind_direction_sin','obs_wind_direction_cos','fcst_wind_direction_sin','fcst_wind_direction_cos')
            if any(values[k] is None for k in keys):return False
            dot=values[keys[0]]*values[keys[2]]+values[keys[1]]*values[keys[3]]
            return math.degrees(math.acos(max(-1,min(1,dot))))>=45
        regimes=[subset('above_freezing',lambda v:v['obs_temperature_c'] is not None and v['obs_temperature_c']>0),
            subset('clear_humid_forecast',lambda v:v['fcst_cloud_cover_percent'] is not None and v['fcst_cloud_cover_percent']<=25 and v['fcst_relative_humidity'] is not None and v['fcst_relative_humidity']>=90),
            subset('low_wind',lambda v:v['fcst_wind_kt'] is not None and v['fcst_wind_kt']<=5),
            subset('rapid_observed_cooling',lambda v:v['obs_cooling_3h_c_per_hour'] is not None and v['obs_cooling_3h_c_per_hour']<=-.5),
            subset('forecast_cloud_clearing',lambda v:v['fcst_cloud_change_percentage_points'] is not None and v['fcst_cloud_change_percentage_points']<=-25),
            subset('forecast_observed_wind_shift',wind_shift),
            subset('recent_wetting',lambda v:v['obs_wetting_report_hours'] is not None and v['obs_wetting_report_hours']>0)]
        metrics.append(dict(model_version=version,cohort=cohort,primary_independent_nights=len(sample),
            actual_lead_minutes=[min(r['lead_minutes'] for r in sample),max(r['lead_minutes'] for r in sample)],regimes=regimes,
            aircraft_cases=len(rows),metrics=values,interpretation='Treatment-reason research only' if cohort=='treatment_reason' else
            'Transfer diagnostic only; not a physical-frost probability or operational validation'))
    unique={r['evidence']['source_sha256']:r['evidence'] for r in all_rows}
    result=sealed(dict(schema=VERSION,dataset=dataset['fingerprint'],evaluated_at_utc=utcnow().isoformat(),
        metrics=metrics,matched_aircraft_model_pairs=len(all_rows),unique_outcomes=dataset['unique_outcomes'],
        unknown_predictions=sum(not p['outcomes'] for p in dataset['predictions']),
        unavailable_predictions=sum(p['state']!='SCORED' for p in dataset['predictions']),
        matched_unique_outcomes=len(unique),
        horizons=[dict(hours=h,scored=sum(p['hours']==h and p['state']=='SCORED' for p in dataset['predictions']),
            unavailable=sum(p['hours']==h and p['state']!='SCORED' for p in dataset['predictions']),
            matched_unique_outcomes=len({e['source_sha256'] for p in dataset['predictions'] if p['hours']==h for e in p['outcomes']})) for h in native.HORIZONS],
        outcome_counts=dict(Counter(r['cohort']+(':confounded' if r['confounded'] else ':unknown' if r['label'] is None else ':positive' if r['label'] else ':negative') for r in unique.values())),
        validation_eligible=False,reason='Accumulating prospective evidence; no automatic operational approval. Physical target requires an independently designed and powered comparison population.',
        approved=False,operationally_validated=False))
    immutable(Path(output)/(result['fingerprint']+'.json'),result);write_json(Path(output)/'evaluation.json',result)
    return result


def scheduler_xml(baseline_path, output, destination, *, user, interval=3600):
    """Generate only. Interactive-token scheduling never embeds a password."""
    local_only(); baseline(baseline_path)
    import subprocess
    import sys
    import xml.etree.ElementTree as ET
    if not user.strip() or not 300<=interval<=86400: raise ValueError('Supply the interactive Windows account and bounded cadence.')
    namespace='http://schemas.microsoft.com/windows/2004/02/mit/task'
    ET.register_namespace('',namespace)
    def child(parent,name,text=None,**attrs):
        node=ET.SubElement(parent,'{'+namespace+'}'+name,attrs)
        if text is not None:node.text=str(text)
        return node
    task=ET.Element('{'+namespace+'}Task',version='1.4')
    registration=child(task,'RegistrationInfo');child(registration,'Description','FROST LOCAL research capture only. No training, approval or production credentials.')
    triggers=child(task,'Triggers');trigger=child(triggers,'TimeTrigger');repetition=child(trigger,'Repetition')
    child(repetition,'Interval',f'PT{interval}S');child(repetition,'StopAtDurationEnd','false')
    child(trigger,'StartBoundary',(utcnow()+timedelta(minutes=5)).isoformat());child(trigger,'Enabled','true')
    principals=child(task,'Principals');principal=child(principals,'Principal',id='Author')
    child(principal,'UserId',user);child(principal,'LogonType','InteractiveToken');child(principal,'RunLevel','LeastPrivilege')
    settings=child(task,'Settings');child(settings,'MultipleInstancesPolicy','IgnoreNew')
    child(settings,'DisallowStartIfOnBatteries','false');child(settings,'StopIfGoingOnBatteries','false')
    child(settings,'StartWhenAvailable','true');child(settings,'RunOnlyIfNetworkAvailable','true')
    child(settings,'WakeToRun','false');child(settings,'ExecutionTimeLimit','PT20M')
    actions=child(task,'Actions',Context='Author');execute=child(actions,'Exec')
    child(execute,'Command',sys.executable)
    child(execute,'Arguments',subprocess.list2cmdline(['-m','scripts.frost_shadow_collect','--local',
        '--baseline',str(Path(baseline_path).resolve()),'--output',str(Path(output).resolve()),
        '--interval-seconds',str(interval),'--once']))
    child(execute,'WorkingDirectory',str(Path(__file__).resolve().parents[1]))
    write_bytes(destination,ET.tostring(task,encoding='utf-16',xml_declaration=True))
    return dict(state='XML GENERATED ONLY — NOT INSTALLED',path=str(destination))


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--local',required=True,action='store_true')
    sub=p.add_subparsers(dest='command',required=True)
    f=sub.add_parser('freeze');f.add_argument('--candidates',required=True);f.add_argument('--reports',required=True);f.add_argument('--output',required=True)
    c=sub.add_parser('capture');c.add_argument('--baseline',required=True);c.add_argument('--output',required=True)
    r=sub.add_parser('refresh-outcomes');r.add_argument('--origin',required=True);r.add_argument('--email',required=True);r.add_argument('--output',required=True)
    r=sub.add_parser('refresh-auto');r.add_argument('--origin',required=True);r.add_argument('--email',required=True);r.add_argument('--output',required=True)
    a=sub.add_parser('assemble-auto');a.add_argument('--baseline',required=True);a.add_argument('--evidence',required=True);a.add_argument('--output',required=True)
    a=sub.add_parser('assemble');a.add_argument('--baseline',required=True);a.add_argument('--captures',required=True);a.add_argument('--outcomes',required=True);a.add_argument('--output',required=True)
    e=sub.add_parser('evaluate');e.add_argument('--dataset',required=True);e.add_argument('--output',required=True)
    s=sub.add_parser('scheduler-xml');s.add_argument('--baseline',required=True);s.add_argument('--output',required=True);s.add_argument('--xml',required=True);s.add_argument('--user',required=True)
    args=p.parse_args(argv);local_only()
    if args.command=='freeze': result=freeze(args.candidates,args.reports,args.output)
    elif args.command=='capture': result=capture(args.baseline,args.output)
    elif args.command in ('refresh-outcomes','refresh-auto'):
        remote=Remote(args.origin,args.email)
        try: result=(refresh_auto if args.command=='refresh-auto' else refresh_outcomes)(remote,args.output)
        finally: remote.close()
    elif args.command=='assemble-auto':result=assemble_auto(args.baseline,args.evidence,args.output)
    elif args.command=='assemble': result=assemble(args.baseline,args.captures,args.outcomes,args.output)
    elif args.command=='evaluate': result=evaluate(read_json(args.dataset),args.output)
    else: result=scheduler_xml(args.baseline,args.output,args.xml,user=args.user)
    print(result.get('fingerprint',result))


if __name__=='__main__': main()
