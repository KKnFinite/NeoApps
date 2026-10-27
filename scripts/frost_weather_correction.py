"""LOCAL versioned weather rebuild/impact study; never rewrites prior evidence.

python -m scripts.frost_weather_correction --local rebuild
python -m scripts.frost_weather_correction --local compare
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from app.services import frost_weather as weather, frost_model as model, frost_vault as vault
from scripts.frost_local import local_only, read_json, write_json, write_bytes
from scripts.frost_forecast_validation import Evidence, cached_get


def rebuild(source, destination):
    local_only();evidence=Evidence(source);root=Path(destination)
    if root.resolve()==evidence.root.resolve():raise ValueError('Use a new derivative dataset directory.')
    counts=Counter();changes=[]
    for item in evidence.manifest['weather']:
        old=evidence.weather(item['month'])  # authenticates original hash and legacy replay
        path=evidence.root/'weather'/(item['month']+'.csv')
        raw=path.read_bytes() if path.exists() else cached_get(evidence.root.parent/'phase7'/'observation-cache',old['request_url'],maximum=16*1024*1024)[0]
        if vault.digest(raw)!=old['raw_sha256']:raise ValueError('Original weather hash changed.')
        rows=weather.parse(raw,datetime.fromisoformat(old['start_utc']),datetime.fromisoformat(old['end_utc']))
        previous={(r['utc'],r['raw_metar']):r for r in old['observations']}
        if len(rows)!=len(previous):raise ValueError('Observation identity/count changed.')
        for row in rows:
            before=previous[(row['utc'],row['raw_metar'])]
            fields={k:dict(before=before.get(k),after=v) for k,v in row.items() if before.get(k)!=v}
            if fields:changes.append(dict(month=item['month'],utc=row['utc'],raw_metar=row['raw_metar'],changes=fields))
        counts['observations']+=len(rows)
        payload=dict(old,schema_version=weather.VERSION,observations=rows,
            derived_from_sha256=item['sha256'],correction='padded cloud codes; METAR vertical visibility in hundreds of feet')
        write_json(root/'weather'/(item['month']+'.json'),payload)
        write_bytes(root/'weather'/(item['month']+'.csv'),raw)
        print('Verified and corrected weather',item['month'],len(rows),flush=True)
    for doc in evidence.manifest['source_catalog']['documents']:
        raw=(evidence.root/'originals'/(doc['sha256']+'.csv')).read_bytes()
        if vault.digest(raw)!=doc['sha256']:raise ValueError('Original CSV hash changed.')
        write_bytes(root/'originals'/(doc['sha256']+'.csv'),raw)
    write_json(root/'source-catalog.json',evidence.manifest['source_catalog'])
    from scripts.frost_dataset import assemble
    manifest=assemble(root,evidence.manifest['source_catalog'],root/'originals')
    affected=[];feature_counts=Counter();fluids_changed=0
    old_parts={p['month']:p for p in evidence.manifest['partitions']}
    for part in manifest['partitions']:
        old=evidence.object(old_parts[part['month']]['sha256'])
        new=read_json(root/'objects'/(part['sha256']+'.json'))
        previous={r['source_record_id']:r for r in old['records']}
        for row in new['records']:
            before=previous[row['source_record_id']];changed=[]
            if row['classification']!=before['classification']:raise ValueError('Weather correction changed a treatment label.')
            for i,(a,b) in enumerate(zip(before['fluids'],row['fluids'])):
                if a['weather']!=b['weather']:
                    fields=[name for name,x,y in zip(model.FEATURES,model.vector(a['weather']),model.vector(b['weather'])) if x!=y]
                    feature_counts.update(fields);fluids_changed+=1
                    changed.append(dict(fluid_index=i,vector_fields=fields))
            if changed:affected.append(dict(record_id=row['source_record_id'],sources=row['sources'],fluids=changed))
    result=dict(schema='frost-weather-correction-v1',generated_at_utc=datetime.now(timezone.utc).isoformat(),
        prior_dataset_fingerprint=evidence.manifest['fingerprint'],corrected_dataset_fingerprint=manifest['fingerprint'],
        weather_version=weather.VERSION,feature_version=model.FEATURE_VERSION,observations=counts['observations'],
        affected_observations=len(changes),observation_changes=changes,affected_source_records=len(affected),
        affected_fluid_windows=fluids_changed,changed_vector_fields=dict(feature_counts),affected_records=affected,
        old_quality=evidence.manifest['quality'],new_quality=manifest['quality'],originals_preserved=True)
    write_json(root.parent/'normalization-impact.json',result)
    print('Affected observations / source records:',len(changes),len(affected),flush=True)
    return result


def compare(source,destination):
    local_only();old=Path(source);new=Path(destination);results=[]
    for lead in (0,1):
        old_path=next((old/'candidates').glob(f'rfd-treatment-h{lead}-*-v1.json'))
        selected=read_json(new/'research-candidates.json') if (new/'research-candidates.json').exists() else None
        new_path=(new/'candidates'/(next(r['model_version'] for r in selected if r['model_version'].startswith(f'rfd-treatment-h{lead}-'))+'.json')) if selected else next((new/'candidates').glob(f'rfd-treatment-h{lead}-*-v2.json'))
        raw_old=old_path.read_bytes();raw_new=new_path.read_bytes()
        a=model.decode(raw_old);b=model.decode(raw_new)
        results.append(dict(lead_hours=lead,old=dict(version=a['model_version'],sha256=vault.digest(raw_old),
            examples=a['training']['examples'],evaluation=a['evaluation']),
            corrected=dict(version=b['model_version'],sha256=vault.digest(raw_new),bytes=len(raw_new),
                examples=b['training']['examples'],evaluation=b['evaluation'],gates=model.quality_gates(b['evaluation']))))
    write_json(new.parent/'model-comparison.json',dict(models=results,approved=False,physical_frost_probability=False))
    for result in results:print(result['lead_hours'],result['corrected']['evaluation']['test'],flush=True)
    return results


def replay(source,destination):
    """Separate changed-input sensitivity of frozen v1 models from retraining."""
    from scripts.frost_local import prepare,store_manifest,metrics
    from app.services import frost_analysis as analysis
    local_only();old=Evidence(source);new=Evidence(destination);root=Path(destination)
    previous_manifest=read_json(root/'manifest.json');previous_prepared=read_json(root/'prepared.json')
    reports=[]
    try:
        for lead in (0,1):
            data=prepare(root,lead_hours=lead)
            write_json(root/('prepared-h'+str(lead)+'.json'),data)
            old_model=next(a for a,_ in old.candidates() if a['evaluation']['lead_hours_validated']==[lead])
            changed=[];fields=Counter();labels=[];scores=[];warm_labels=[];warm_scores=[]
            for row in data['examples']:
                at=datetime.fromisoformat(row['at'])
                previous=analysis.features(at.isoformat(),old.observations(at))
                values=model.vector(previous)
                delta={n:dict(before=a,after=b) for n,a,b in zip(model.FEATURES,values,row['features']) if a!=b}
                if delta:
                    changed.append(dict(group_id=row['group_id'],sources=row['sources'],at=row['at'],changes=delta));fields.update(delta.keys())
                if row['season']==old_model['evaluation']['test_season']:
                    labels.append(row['label']);scores.append(model.score(old_model,row['features']))
                    if row['features'][0] is not None and row['features'][0]>0:
                        warm_labels.append(row['label']);warm_scores.append(scores[-1])
            reports.append(dict(lead_hours=lead,examples=len(data['examples']),changed_applications=len(changed),
                changed_fields=dict(fields),changes=changed,frozen_v1_model_on_corrected_inputs=metrics(labels,scores),
                frozen_v1_above_freezing_on_corrected_inputs=metrics(warm_labels,warm_scores),
                original_v1_evaluation=old_model['evaluation']['test'],
                interpretation='Explicit LOCAL corrected-input sensitivity experiment; v1 artifact is not re-labeled or activated with v2 features.'))
            print('Frozen replay',lead,len(changed),'changed applications',flush=True)
    finally:
        store_manifest(root,previous_manifest);write_json(root/'prepared.json',previous_prepared)
    write_json(root.parent/'frozen-replay.json',dict(reports=reports,approved=False))
    return reports


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--local',action='store_true',required=True)
    p.add_argument('--source',default='instance/frost-local/complete')
    p.add_argument('--destination',default='instance/frost-local/phase8/dataset')
    p.add_argument('command',choices=('rebuild','compare','replay','revalidate','select'))
    p.add_argument('--h0');p.add_argument('--h1');args=p.parse_args(argv);local_only()
    if args.command=='revalidate':
        from scripts.frost_dataset import assemble
        root=Path(args.destination);assemble(root,read_json(root/'source-catalog.json'),root/'originals')
    elif args.command=='select':
        selected=[]
        for lead,version in enumerate((args.h0,args.h1)):
            import re
            if not version or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',version):raise ValueError('Supply explicit local H0/H1 candidate versions.')
            raw=(Path(args.destination)/'candidates'/(version+'.json')).read_bytes();artifact=model.decode(raw)
            if artifact['evaluation']['lead_hours_validated']!=[lead]:raise ValueError('Selected candidate lead mismatch.')
            selected.append(dict(model_version=version,sha256=vault.digest(raw)))
        write_json(Path(args.destination)/'research-candidates.json',selected)
    else:dict(rebuild=rebuild,compare=compare,replay=replay)[args.command](args.source,args.destination)


if __name__=='__main__':main()
