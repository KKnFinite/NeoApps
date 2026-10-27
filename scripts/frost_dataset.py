"""Local dataset assembly for the existing frost_local training pipeline.

Sources and cached results are immutable/content addressed. Existing monthly
results are reused only after exact row/provenance reconciliation with originals.
"""
from collections import Counter,defaultdict
from datetime import datetime,timezone
from pathlib import Path
from types import SimpleNamespace
import json
import zipfile
from app.services import frost_analysis as analysis,frost_weather as weather,frost_vault as vault
from app.services import frost_dataset_contract as contract
from app.services.frost_xchange import extract,PARSER_VERSION
from app.services.frost_history import month_bounds


def assemble(directory,catalog,originals, *, caches=()):
    from scripts.frost_local import local_only,read_json,write_json,write_bytes,store_manifest
    local_only();root=Path(directory);originals=Path(originals)
    if (root/'manifest.json').exists() and read_json(root/'manifest.json').get('weather_version',weather.VERSION)!=weather.VERSION:
        raise ValueError('Preserve the legacy dataset; rebuild corrected weather in a new versioned directory.')
    if contract.seal(catalog)['fingerprint']!=catalog['fingerprint']:raise ValueError('Source catalog failed SHA-256.')
    rows=[]
    for item in catalog['documents']:
        if '/' in item['filename'] or '\\' in item['filename'] or ':' in item['filename']:raise ValueError('Unsafe source filename.')
        path=originals/(item['sha256']+'.csv')
        if not path.exists():path=originals/item['filename']
        raw=path.read_bytes()
        if len(raw)!=item['bytes'] or vault.digest(raw)!=item['sha256']:raise ValueError('Original hash mismatch: '+item['filename'])
        parsed=extract(raw,gateway_code=catalog['gateway_code'])
        if parsed['source_row_count']!=item['source_rows'] or parsed['status']!='extracted':raise ValueError('Original extraction count/status mismatch.')
        doc=SimpleNamespace(id=item['id'],sha256=item['sha256'],extracted_key=item['extracted_key'])
        rows.extend(analysis.compact_row(r,doc) for r in parsed['rows'])
        print('Verified original',item['id'],item['source_rows'],flush=True)
    rows,stats=analysis.deduplicate(rows);groups=defaultdict(list)
    for row in rows:groups[(row.get('application_date') or 'unknown')[:7]].append(row)
    del rows
    # Index only locations, not a decade of expanded results in memory.
    indexed={};weather_files={}
    for folder in (root,*map(Path,caches)):
        if not folder.exists():continue
        for path in folder.rglob('*.json'):
            if path.parent.name=='objects' or path.parent.name=='weather':
                try:
                    payload=read_json(path)
                    if payload.get('schema_version')==analysis.VERSION and 'records' in payload:
                        month=(payload['records'][0].get('application_date') or 'unknown')[:7] if payload['records'] else None
                        if month:indexed.setdefault(month,('json',path))
                    elif payload.get('schema_version')==weather.VERSION and payload.get('station')==weather.STATION:
                        weather_files.setdefault(payload['start_utc'][:7],path)
                except (ValueError,KeyError,TypeError):continue
        for path in folder.glob('result-*.zip'):
            month=path.name[7:14]
            if month in indexed:continue
            receipt=folder.parent/'publication-manifest.json'
            if receipt.exists():
                known=next((r for r in read_json(receipt) if Path(r['file']).name==path.name),None)
                if folder.name=='publishable-results' and (not known or vault.digest(path.read_bytes())!=known['sha256']):raise ValueError('Saved local result receipt failed SHA-256.')
            with zipfile.ZipFile(path) as archive:
                payload=json.loads(archive.read('result.json'))
                if payload['schema_version']!=analysis.VERSION or payload['time_contract']!=analysis.TIME_CONTRACT:continue
            indexed[month]=('zip',path)
    from functools import lru_cache
    @lru_cache(maxsize=3)
    def observations(month):
        start,end=month_bounds(month);cache=root/'weather'/(month+'.json');raw_path=root/'weather'/(month+'.csv')
        known=weather_files.get(month)
        if known:
            payload=read_json(known)
            # Existing provider read-back evidence retains its content hash.
            if known.parent.name=='objects' and known.stem!=vault.digest(known.read_bytes()):raise ValueError('Archived weather hash mismatch.')
            if payload['station']!=weather.STATION or datetime.fromisoformat(payload['start_utc'])!=start or datetime.fromisoformat(payload['end_utc'])!=end:raise ValueError('Weather station/time contract changed.')
            if known.with_suffix('.csv').exists() and payload['raw_sha256']!=vault.digest(known.with_suffix('.csv').read_bytes()):raise ValueError('Raw weather source hash mismatch.')
            if not cache.exists():write_json(cache,payload)
            weather_files[month]=cache
            return payload['observations']
        if cache.exists() and raw_path.exists():
            payload=read_json(cache);raw=raw_path.read_bytes()
            if payload.get('schema_version')!=weather.VERSION:raise ValueError('Legacy weather cache requires a versioned local rebuild; original retained.')
            if payload['raw_sha256']!=vault.digest(raw):raise ValueError('Local raw weather integrity mismatch.')
        else:
            raw,url=weather.fetch(start,end);observed=weather.parse(raw,start,end)
            payload=dict(schema_version=weather.VERSION,station=weather.STATION,start_utc=start.isoformat(),end_utc=end.isoformat(),
                raw_sha256=vault.digest(raw),request_url=url,fetched_at_utc=datetime.now(timezone.utc).isoformat(),observations=observed)
            write_bytes(raw_path,raw);write_json(cache,payload)
            print('LOCAL weather retrieved',month,len(observed),flush=True)
        weather_files[month]=cache
        return payload['observations']
    parts=[];quality=Counter();labels=Counter();excluded=[];reused=0;computed=0
    for month,expected in sorted(groups.items()):
        source_hash=vault.digest(vault.canonical(expected));checkpoint=root/'months'/(month+'.json')
        result=None;origin=None
        if checkpoint.exists():
            prior=read_json(checkpoint)
            candidate=root/'objects'/(prior['sha256']+'.json')
            if candidate.exists():
                if vault.digest(candidate.read_bytes())!=prior['sha256']:raise ValueError('Local completed result hash mismatch.')
                result=read_json(candidate);origin=prior['origin']
                if result.get('schema_version')!=analysis.VERSION:result=None;origin=None
        if result is None and month in indexed:
            kind,path=indexed[month]
            if kind=='zip':
                with zipfile.ZipFile(path) as archive:result=json.loads(archive.read('result.json'))
                origin='existing_local_result'
            else:
                raw=path.read_bytes()
                if path.stem!=vault.digest(raw):raise ValueError('Archived result object failed SHA-256.')
                result=json.loads(raw);origin='previously_archived_result'
        if result is not None:
            original=[dict(r,fluids=[{k:v for k,v in f.items() if k!='weather'} for f in r['fluids']]) for r in result['records']]
            found={vault.digest(vault.canonical(source)):record for source,record in zip(original,result['records'])}
        else:found={}
        missing=[r for r in expected if vault.digest(vault.canonical(r)) not in found]
        if missing:
            from app.services.frost_local_backfill import months_needed
            needed=months_needed(missing)
            calculated=analysis.analyze(missing,[r for m in needed for r in observations(m)])
            for source,record in zip(missing,calculated['records']):found[vault.digest(vault.canonical(source))]=record
            if result is None:origin='missing_features_reconstructed_locally'
            else:origin='incrementally_reconciled_local_evidence'
            computed+=len(missing)
        reused+=len(expected)-len(missing)
        reconciled=[found[vault.digest(vault.canonical(r))] for r in expected]
        # Summary is cheap local reconciliation, never a second feature pass.
        totals=Counter()
        for r in reconciled:
            totals[r['classification']['label']]+=1
            totals['review_records']+=int(r['requires_review'] or any(f['time_quality']!='verified_local_calendar' for f in r['fluids']))
            totals['weather_matched']+=int(bool(r['fluids']) and all(f['weather']['matched'] for f in r['fluids']))
            totals['weather_unmatched']+=int(not r['fluids'] or any(not f['weather']['matched'] for f in r['fluids']))
            totals['incomplete_windows']+=int(any(f['weather'].get('incomplete_window',True) for f in r['fluids']))
        result=dict(schema_version=analysis.VERSION,time_contract=analysis.TIME_CONTRACT,records=reconciled,
            summary=dict(totals),descriptive_cohorts=analysis.cohorts(reconciled))
        if result['schema_version']!=analysis.VERSION or result['time_contract']!=analysis.TIME_CONTRACT:raise ValueError('Feature contract mismatch.')
        for row in result['records']:
            labels[row['classification']['label']]+=1
            fluids=row['fluids'];matched=bool(fluids) and all(f['weather']['matched'] for f in fluids)
            quality['weather_matched' if matched else 'weather_unmatched']+=1
            reasons=[]
            if not matched:reasons.append('weather_unmatched')
            if any(f['weather'].get('incomplete_window',True) for f in fluids):reasons.append('incomplete_window')
            if row['requires_review'] or any(f['time_quality']!='verified_local_calendar' for f in fluids):reasons.append('requires_review')
            for reason in reasons:
                if reason!='weather_unmatched':quality[reason]+=1
                excluded.append(dict(record_id=row['source_record_id'],reason=reason,sources=row['sources'],
                    disposition='excluded_from_weather_dependent_fitting; original preserved',
                    weather_policy=dict(max_preceding_age_minutes=90,no_future_observations=True,no_interpolation=True),
                    event_times=[f.get('occurred_at_utc') for f in fluids]))
            for fluid in fluids:
                feature=fluid['weather'];observed=feature.get('observation')
                if feature['matched'] and (not observed or not 0<=(datetime.fromisoformat(fluid['occurred_at_utc'])-datetime.fromisoformat(observed['utc'])).total_seconds()<=5400):raise ValueError('Future/stale weather in cached result.')
        raw=vault.canonical(result);digest=vault.digest(raw);destination=root/'objects'/(digest+'.json')
        if not destination.exists():write_bytes(destination,raw)
        part=dict(month=month,status='local_verified',records=len(expected),sha256=digest,source_rows_sha256=source_hash,origin=origin)
        write_json(checkpoint,part);parts.append(part)
        print('LOCAL verified month',month,len(expected),origin,flush=True)
    seasons=sorted({f'{int(m[:4])-(int(m[5:7])<7)}-{int(m[:4])-(int(m[5:7])<7)+1}' for m in groups if m!='unknown'})
    stats.update(documents=len(catalog['documents']),seasons=seasons)
    weather_manifest=[]
    for month,path in sorted(weather_files.items()):
        raw=path.read_bytes();digest=vault.digest(raw);target=root/'objects'/(digest+'.json')
        if not target.exists():write_bytes(target,raw)
        weather_manifest.append(dict(month=month,sha256=digest))
    manifest=contract.seal(dict(schema=contract.VERSION,version=analysis.VERSION,time_contract=analysis.TIME_CONTRACT,
        parser_version=PARSER_VERSION,weather_version=weather.VERSION,implementation_sha256=contract.code_hash(),
        source_origin='authenticated_frost_vault',source_catalog=catalog,catalog=stats,partitions=parts,weather=weather_manifest,
        quality=dict(weather_matched=quality['weather_matched'],weather_unmatched=quality['weather_unmatched'],incomplete_window=quality['incomplete_window'],
            requires_review=quality['requires_review'],classifications=dict(labels),exclusions=excluded),
        ready=True,state='LOCAL DATA READY',assembly=dict(
            reused_records=sum(p['records'] for p in parts if p['origin']!='missing_features_reconstructed_locally'),
            reconstructed_records=sum(p['records'] for p in parts if p['origin']=='missing_features_reconstructed_locally'))))
    contract.validate(manifest);store_manifest(root,manifest);write_json(root/'source-catalog.json',catalog)
    print('LOCAL DATA READY',stats['deduplicated_records'],'reused',reused,'reconstructed',computed,flush=True)
    return manifest
