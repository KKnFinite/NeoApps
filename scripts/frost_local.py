"""Local-only FROST preparation/training/evaluation CLI; never imported by app.

Run python -m scripts.frost_local --local --help. No production DB credentials.
Only bounded JSON coefficients/trees leave this workstation, never pickle.
"""
import argparse
import csv
import getpass
import hashlib
import json
import math
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from app.services import frost_analysis as analysis, frost_model as model, frost_vault as vault

PIPELINE_VERSION = 'frost-local-training-v1'


def local_only():
    from flask import has_request_context
    if os.environ.get('RENDER') or os.environ.get('RENDER_SERVICE_ID') or has_request_context():
        raise RuntimeError('FROST training/data preparation is workstation-only; forbidden on Render and in web requests.')


def read_json(path):
    return json.loads(Path(path).read_bytes(),parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite local evidence.')))


def write_json(path, value):
    write_bytes(path,vault.canonical(value))


def write_bytes(path, raw):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    temporary=path.with_suffix(path.suffix+'.partial')
    temporary.write_bytes(raw)
    if os.name!='nt':temporary.chmod(0o600)
    temporary.replace(path)


def store_manifest(directory,manifest):
    """Immutable local versions plus a replaceable current pointer."""
    root=Path(directory);versioned=root/'manifests'/(manifest['fingerprint']+'.json')
    raw=vault.canonical(manifest)
    if versioned.exists() and versioned.read_bytes()!=raw:raise ValueError('Immutable local manifest was altered.')
    if not versioned.exists():write_bytes(versioned,raw)
    write_bytes(root/'manifest.json',raw)


def validate_manifest(manifest, *, historical=False):
    from app.services import frost_dataset_contract as contract
    if manifest.get('schema')==contract.VERSION:return contract.validate(manifest,historical=historical)
    body={k:v for k,v in manifest.items() if k!='fingerprint'}
    versions=(analysis.VERSION,'frost-history-v2') if historical else (analysis.VERSION,)
    if vault.digest(vault.canonical(body))!=manifest['fingerprint'] or manifest['version'] not in versions or manifest['time_contract']!=analysis.TIME_CONTRACT:
        raise ValueError('Dataset manifest integrity/version mismatch.')
    if manifest.get('source_origin')!='authenticated_frost_vault':raise ValueError('Dataset source is not authenticated FROST evidence.')
    return manifest


class CsrfParser(HTMLParser):
    token=None
    def handle_starttag(self,tag,attributes):
        attributes=dict(attributes)
        if tag=='input' and attributes.get('name')=='csrf_token':self.token=attributes.get('value')


class Remote:
    def __init__(self,base,email,password=None):
        local_only()
        import requests
        self.base=base.rstrip('/')
        parsed=urlsplit(self.base)
        if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('','/'):
            raise ValueError('Use a bare application origin without embedded credentials.')
        if parsed.scheme!='https' and not (parsed.scheme=='http' and parsed.hostname in ('127.0.0.1','localhost')):
            raise ValueError('Authenticated dataset access requires HTTPS.')
        self.session=requests.Session()
        try:
            page=self.request('GET','/login')
            parser=CsrfParser();parser.feed(page.decode())
            response=self.session.post(self.base+'/login',data=dict(email=email,password=password if password is not None else getpass.getpass('NeoApps password: '),csrf_token=parser.token),timeout=30,allow_redirects=False)
            if response.status_code not in (302,303) or urlsplit(urljoin(self.base,response.headers.get('Location',''))).netloc!=parsed.netloc:
                raise ValueError('Sign-in failed or additional authentication is required. No credentials were saved.')
            response.close()
            result=json.loads(self.request('GET','/neosubzero/frost-vault/dataset-manifest'))
            self.csrf=result['csrf_token']
        except Exception:
            self.close();raise

    def request(self,method,path,**kwargs):
        if not path.startswith('/neosubzero/frost-vault/') and path!='/login':raise ValueError('Unapproved dataset endpoint.')
        url=urljoin(self.base,path)
        if urlsplit(url).netloc!=urlsplit(self.base).netloc:raise ValueError('Cross-origin evidence URL rejected.')
        with self.session.request(method,url,timeout=(10,60),stream=True,allow_redirects=False,**kwargs) as response:
            if response.status_code!=200:raise ValueError(f'Authenticated request failed: HTTP {response.status_code}. No automatic retry.')
            chunks=[];length=0
            for chunk in response.iter_content(65536):
                length+=len(chunk)
                if length>32*1024*1024:raise ValueError('Evidence response exceeds bounded monthly limit.')
                chunks.append(chunk)
            return b''.join(chunks)

    def close(self):self.session.close()


def refresh(remote, directory, *, with_weather=False):
    local_only();directory=Path(directory)
    manifest=validate_manifest(json.loads(remote.request('GET','/neosubzero/frost-vault/dataset-manifest'))['manifest'],historical=True)
    downloaded,reused=0,0
    source=manifest.get('source_catalog')
    if source:
        from app.services.frost_dataset_contract import seal
        if seal(source)['fingerprint']!=source['fingerprint']:raise ValueError('Remote source catalog hash mismatch.')
        for document in source['documents']:
            digest=document['sha256']
            if len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):raise ValueError('Unsafe original evidence hash.')
            destination=directory/'originals'/(digest+'.csv')
            if destination.exists() and vault.digest(destination.read_bytes())==digest:
                reused+=1;continue
            if not document['verified']:raise ValueError('Original off-site verification is pending; refresh later.')
            raw=remote.request('GET','/neosubzero/frost-vault/documents/'+str(int(document['id'])))
            if len(raw)!=document['bytes'] or vault.digest(raw)!=digest:raise ValueError('Original document failed SHA-256.')
            write_bytes(destination,raw);downloaded+=1
        write_json(directory/'source-catalog.json',source)
    for item in manifest['partitions']+(manifest.get('weather',[]) if with_weather else []):
        if item['status'] in ('pending','retry','superseded') or not item['sha256']:continue
        digest=item['sha256']
        if len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):raise ValueError('Unsafe evidence hash.')
        destination=directory/'objects'/(digest+'.json')
        if destination.exists() and vault.digest(destination.read_bytes())==digest:
            reused+=1;continue
        import re
        if not re.fullmatch(r'/neosubzero/frost-vault/(?:history|weather)/[0-9]+',item['url']):raise ValueError('Unsafe evidence URL.')
        raw=remote.request('GET',item['url'])
        if vault.digest(raw)!=digest:raise ValueError('Historical partition changed or failed SHA-256 verification; refresh the manifest.')
        payload=json.loads(raw)
        if '/weather/' in item['url']:
            from app.services import frost_weather
            if payload['schema_version']!=item.get('version',frost_weather.VERSION) or payload['schema_version'] not in (frost_weather.VERSION,frost_weather.LEGACY_VERSION) or payload['station']!=frost_weather.STATION:raise ValueError('Historical weather station/version mismatch.')
        elif payload['schema_version']!=manifest['version'] or payload['time_contract']!=analysis.TIME_CONTRACT:raise ValueError('Historical feature contract changed.')
        write_bytes(destination,raw);downloaded+=1
        print(f"Verified {item['month']}: {item.get('records','weather')} source rows",flush=True)
    current=validate_manifest(json.loads(remote.request('GET','/neosubzero/frost-vault/dataset-manifest'))['manifest'],historical=True)
    if current['fingerprint']!=manifest['fingerprint']:raise ValueError('Dataset changed during download. Verified objects are retained; refresh again.')
    write_json(directory/('remote-manifest.json' if source else 'manifest.json'),manifest)
    print(f'Downloaded {downloaded}; reused {reused}; readiness: {manifest["state"]}',flush=True)
    return manifest


def prepare(directory, *, lead_hours=0):
    local_only();directory=Path(directory)
    manifest=validate_manifest(read_json(directory/'manifest.json'))
    if manifest.get('schema')=='frost-local-verified-v2' and (directory/'source-catalog.json').exists():
        if read_json(directory/'source-catalog.json')!=manifest['source_catalog']:raise ValueError('Local sources changed; assemble newly imported evidence first.')
    if not manifest['ready']:raise ValueError('Phase 5 data-quality/readiness gate blocks training: '+manifest['state'])
    if lead_hours not in (0,1,3,6):raise ValueError('Supported observation-based leads: 0, 1, 3 or 6 hours.')
    from functools import lru_cache
    weather_index={w['month']:w for w in manifest.get('weather',[])}
    @lru_cache(maxsize=2)
    def month_weather(month):
        if month not in weather_index:raise ValueError('Lead-time weather evidence is missing. Refresh with --with-weather.')
        item=weather_index[month];path=directory/'objects'/(item['sha256']+'.json')
        if not path.exists() or vault.digest(path.read_bytes())!=item['sha256']:raise ValueError('Local weather evidence missing/corrupt. Refresh with --with-weather.')
        return read_json(path)['observations']
    groups=defaultdict(list);excluded=Counter();source_count=0
    for partition in manifest['partitions']:
        if partition['status'] not in ('ready','local_verified'):raise ValueError('Historical partition is not data-ready.')
        path=directory/'objects'/(partition['sha256']+'.json')
        if vault.digest(path.read_bytes())!=partition['sha256']:raise ValueError('Local dataset object failed SHA-256.')
        payload=read_json(path)
        if len(payload['records'])!=partition['records']:raise ValueError('Historical row reconciliation failed.')
        for row in payload['records']:
            source_count+=1
            if not row.get('sources') or not row.get('tail') or not row.get('application_number'):
                excluded['missing_identity_or_provenance']+=1;continue
            starts=[datetime.fromisoformat(f['occurred_at_utc']) for f in row.get('fluids',[]) if f.get('occurred_at_utc')]
            if not starts:excluded['source_rows_missing_time']+=1;continue
            physical_night=(min(starts).astimezone(analysis.ZONE)-timedelta(hours=12)).date().isoformat()
            identity=(physical_night,row['tail'],row['application_number'])
            groups[identity].append(row)
    if source_count!=manifest['catalog']['deduplicated_records']:raise ValueError('Dataset is incomplete; distinct source count does not reconcile.')
    examples=[]
    for identity, rows in sorted(groups.items()):
        if manifest.get('schema')=='frost-local-verified-v2' and any(not f.get('weather',{}).get('matched') or f.get('weather',{}).get('incomplete_window',True) for row in rows for f in row.get('fluids',[])):
            excluded['source_weather_quality_exclusion']+=1;continue
        labels={model.training_label(row) for row in rows}
        if len(labels)!=1 or None in labels:
            excluded['pretreat_unknown_or_ambiguous_outcome']+=1;continue
        fluids=[f for row in rows for f in row.get('fluids',[]) if f.get('occurred_at_utc')]
        if not fluids:excluded['missing_time']+=1;continue
        first=min(fluids,key=lambda f:datetime.fromisoformat(f['occurred_at_utc']))
        event_at=datetime.fromisoformat(first['occurred_at_utc'])
        at=event_at-timedelta(hours=lead_hours)
        weather=first.get('weather') or {}
        if lead_hours:
            months={at.strftime('%Y-%m'),(at-timedelta(hours=12)).strftime('%Y-%m')}
            weather=analysis.features(at.isoformat(),[row for month in sorted(months) for row in month_weather(month)])
        if not weather.get('matched') or weather.get('incomplete_window',True):excluded['missing_weather']+=1;continue
        observation=datetime.fromisoformat(weather['observation']['utc'])
        if at.tzinfo is None or observation>at:raise ValueError('Future observation or uncertain time entered training.')
        night=(event_at.astimezone(analysis.ZONE)-timedelta(hours=12)).date()
        season=night.year-(night.month<7)
        examples.append(dict(group_id=vault.digest(vault.canonical(identity)),night=night.isoformat(),
            season=f'{season}-{season+1}',at=at.isoformat(),event_at=event_at.isoformat(),label=next(iter(labels)),features=model.vector(weather),
            sources=[source for row in rows for source in row['sources']],
            classifications=[row['classification'] for row in rows]))
    dataset=dict(schema=PIPELINE_VERSION,manifest=manifest,feature_version=model.FEATURE_VERSION,
        source_records=source_count,excluded=dict(excluded),examples=examples,lead_hours=lead_hours)
    from app.services import frost_dataset_contract as contract
    if manifest.get('schema')==contract.VERSION:
        manifest=contract.seal(dict(manifest,eligibility=dict(examples=len(examples),classes=dict(Counter(str(r['label']) for r in examples)),
            examples_sha256=vault.digest(vault.canonical(examples)),lead_hours=lead_hours,excluded=dict(excluded))))
        contract.validate(manifest,training=True);dataset['manifest']=manifest
        store_manifest(directory,manifest)
    write_json(directory/'prepared.json',dataset)
    print(f'Prepared {len(examples)} independent aircraft/applications from {source_count} rows; exclusions {dict(excluded)}',flush=True)
    return dataset


def weather_reference(values):
    # Research comparator only; not an operational frost rule or surface reading.
    t,spread=values[0],values[2]
    return .8 if t is not None and spread is not None and t<=2 and spread<=2 else .2


def metrics(labels,scores):
    n=len(labels);positive=sum(labels);negative=n-positive
    tp=sum(y==1 and p>=.5 for y,p in zip(labels,scores));fp=sum(y==0 and p>=.5 for y,p in zip(labels,scores))
    ece=0;bins=[]
    for index in range(10):
        rows=[(y,p) for y,p in zip(labels,scores) if min(9,int(p*10))==index]
        if not rows:continue
        predicted=sum(p for _,p in rows)/len(rows);observed=sum(y for y,_ in rows)/len(rows)
        ece+=len(rows)/max(1,n)*abs(predicted-observed)
        bins.append(dict(count=len(rows),predicted=predicted,observed=observed))
    return dict(count=n,positive=positive,negative=negative,recall=tp/positive if positive else None,
        false_positive_rate=fp/negative if negative else None,false_alarms=fp,missed=positive-tp,
        brier=sum((p-y)**2 for y,p in zip(labels,scores))/n if n else None,
        log_loss=-sum(y*math.log(max(1e-12,p))+(1-y)*math.log(max(1e-12,1-p)) for y,p in zip(labels,scores))/n if n else None,
        ece=ece if n else None,reliability_bins=bins)


def fit(rows,kind,*,feature_names=None):
    local_only()
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    from sklearn.ensemble import RandomForestClassifier
    raw=np.array([[np.nan if v is None else v for v in row['features']] for row in rows],dtype=float)
    means=np.array([np.mean(column[np.isfinite(column)]) if np.isfinite(column).any() else 0 for column in raw.T])
    filled=np.where(np.isnan(raw),means,raw)
    scales=np.std(filled,axis=0);scales[scales==0]=1
    x=np.column_stack(((filled-means)/scales,np.isnan(raw).astype(float)))
    y=[row['label'] for row in rows]
    if len(set(y))!=2:raise ValueError('Both explicit treatment-reason classes are required in every fitting fold.')
    estimator=LogisticRegression(C=1,solver='lbfgs',max_iter=500,random_state=0) if kind=='logistic' else RandomForestClassifier(n_estimators=32,max_depth=3,min_samples_leaf=5,n_jobs=1,random_state=0)
    names=tuple(feature_names) if feature_names is not None else model.FEATURES
    if any(len(row['features'])!=len(names) for row in rows):raise ValueError('Fitting feature contract mismatch.')
    print(f'Fitting {kind}: {len(rows)} applications, {len(names)} weather features',flush=True)
    estimator.fit(x,y)
    if kind=='logistic':
        result=dict(kind=kind,coefficients=estimator.coef_[0].tolist(),intercept=float(estimator.intercept_[0]))
    else:
        trees=[]
        for item in estimator.estimators_:
            tree=item.tree_;nodes=[]
            for i in range(tree.node_count):
                if tree.children_left[i]<0:
                    counts=tree.value[i][0];nodes.append(dict(probability=float(counts[1]/sum(counts))))
                else:nodes.append(dict(feature=int(tree.feature[i]),threshold=float(tree.threshold[i]),left=int(tree.children_left[i]),right=int(tree.children_right[i])))
            trees.append(nodes)
        result=dict(kind=kind,trees=trees)
    checks=[dict(features=rows[i]['features'],score=float(estimator.predict_proba(x[i:i+1])[0][1])) for i in range(min(4,len(rows)))]
    importance=estimator.coef_[0].tolist() if kind=='logistic' else estimator.feature_importances_.tolist()
    return dict(model=result,preprocessing=dict(means=means.tolist(),scales=scales.tolist()),calibration=dict(kind='none'),
        parity_checks=checks,importance=importance)


def forward_split(rows,seasons,heldout):
    train=[r for r in rows if r['season'] in seasons];test=[r for r in rows if r['season']==heldout]
    if not train or not test:raise ValueError('Empty temporal fold.')
    boundary=min(datetime.fromisoformat(r['at']) for r in test)-timedelta(hours=24)
    train=[r for r in train if datetime.fromisoformat(r['at'])<boundary]
    if {r['night'] for r in train}&{r['night'] for r in test}:raise ValueError('Night leakage detected.')
    return train,test


def train(dataset,version,checkpoint=None):
    local_only()
    rows=dataset['examples'];manifest=validate_manifest(dataset['manifest'])
    if not manifest['ready']:raise ValueError('Phase 5 readiness gate blocks training.')
    from app.services import frost_dataset_contract as contract
    if manifest.get('schema')==contract.VERSION:
        contract.validate(manifest,training=True)
        if manifest['eligibility']['examples_sha256']!=vault.digest(vault.canonical(rows)) or manifest['eligibility']['lead_hours']!=dataset['lead_hours']:
            raise ValueError('Prepared training examples differ from the validated local manifest.')
    if dataset['feature_version']!=model.FEATURE_VERSION:raise ValueError('Prepared feature version changed.')
    seasons=sorted({r['season'] for r in rows})
    if len(seasons)<5:raise ValueError('At least five independent seasons are required for development/calibration/test isolation.')
    import sklearn
    import numpy,scipy,platform
    reports=[]
    for index in range(2,len(seasons)-2):
        training,testing=forward_split(rows,seasons[:index],seasons[index])
        checkpoint_key=dict(dataset_fingerprint=manifest['fingerprint'],pipeline=PIPELINE_VERSION,
            code_sha256=vault.digest(Path(__file__).read_bytes()),sklearn=sklearn.__version__,lead_hours=dataset.get('lead_hours',0))
        checkpoint_path=Path(checkpoint)/(f'{seasons[index]}-fold.json') if checkpoint else None
        if checkpoint_path and checkpoint_path.exists():
            cached=read_json(checkpoint_path)
            if all(cached.get(k)==v for k,v in checkpoint_key.items()):
                reports.append(cached['fold']);print('Reused verified development checkpoint '+seasons[index],flush=True);continue
        fold=dict(fit_seasons=seasons[:index],test_season=seasons[index],metrics={})
        for kind in ('logistic','forest'):
            candidate=fit(training,kind)
            fold['metrics'][kind]=metrics([r['label'] for r in testing],[model.score(candidate,r['features']) for r in testing])
        fold['metrics']['weather_reference']=metrics([r['label'] for r in testing],[weather_reference(r['features']) for r in testing])
        reports.append(fold)
        if checkpoint_path:write_json(checkpoint_path,dict(checkpoint_key,fold=fold))
        print('Backtested held-out season '+seasons[index],flush=True)
    selected=min(('logistic','forest'),key=lambda k:sum(f['metrics'][k]['brier'] for f in reports)/len(reports))
    fit_seasons=seasons[:-2];training,calibration=forward_split(rows,fit_seasons,seasons[-2])
    candidate=fit(training,selected)
    if len(calibration)>=100 and min(Counter(r['label'] for r in calibration).values())>=20 and len({r['label'] for r in calibration})==2:
        from sklearn.linear_model import LogisticRegression
        scores=[model.score(candidate,r['features']) for r in calibration]
        logits=[[math.log(max(1e-12,p)/max(1e-12,1-p))] for p in scores]
        calibrator=LogisticRegression(C=1,solver='lbfgs',random_state=0).fit(logits,[r['label'] for r in calibration])
        candidate['calibration']=dict(kind='sigmoid',slope=float(calibrator.coef_[0][0]),intercept=float(calibrator.intercept_[0]))
    _,test=forward_split(rows,seasons[:-1],seasons[-1])
    final=metrics([r['label'] for r in test],[model.score(candidate,r['features']) for r in test])
    prior=sum(r['label'] for r in training)/len(training)
    final['climatology_brier']=metrics([r['label'] for r in test],[prior]*len(test))['brier']
    final['weather_brier']=metrics([r['label'] for r in test],[weather_reference(r['features']) for r in test])['brier']
    regimes={}
    for name,predicate in [('freezing',lambda r:r['features'][0] is not None and r['features'][0]<=0),
            ('above_freezing',lambda r:r['features'][0] is not None and r['features'][0]>0),
            ('missing_features',lambda r:None in r['features'])]:
        subset=[r for r in test if predicate(r)]
        regimes[name]=metrics([r['label'] for r in subset],[model.score(candidate,r['features']) for r in subset])
    evaluation=dict(split='forward_seasons_calibration_then_untouched_test',seasons=seasons,fit_seasons=fit_seasons,
        calibration_season=seasons[-2],test_season=seasons[-1],development=reports,test=final,weather_regimes=regimes,
        lead_hours_validated=[dataset.get('lead_hours',0)],forecast_weather_backtested=False,selection='development-season Brier only; final test never selects or refits the model')
    importance=candidate.pop('importance')
    evaluation['feature_importance']=[dict(feature=name,value=value) for name,value in
        zip(list(model.FEATURES)+['missing_'+n for n in model.FEATURES],importance)]
    evaluation['feature_importance_basis']='Standardized logistic coefficients or forest impurity importance; conditional associations, not causal physical effects.'
    artifact=dict(schema=model.VERSION,feature_version=model.FEATURE_VERSION,features=list(model.FEATURES),
        feature_definitions=model.FEATURE_DEFINITIONS,model_version=version,target=model.TARGET,physical_frost_probability=False,
        dataset={k:manifest[k] for k in ('version','time_contract','fingerprint','source_origin')},
        classification_version=analysis.VERSION,seasons=seasons,trained_at_utc=datetime.now(timezone.utc).isoformat(),
        runtime=dict(python_min='3.11',format='bounded_json',training_dependencies=False),
        training=dict(pipeline=PIPELINE_VERSION,sklearn=sklearn.__version__,seed=0,examples=len(rows),
            code_sha256=vault.digest(Path(__file__).read_bytes()),
            numpy=numpy.__version__,scipy=scipy.__version__,python=platform.python_version(),
            excluded=dataset['excluded'],source_records=dataset['source_records']),
        limitations=list(model.LIMITATIONS),evaluation=evaluation,forecast_validated=False,parking_adjustments=None,**candidate)
    if manifest.get('schema')==contract.VERSION:artifact['local_dataset_manifest']=manifest
    model.decode(vault.canonical(artifact))
    print('Promotion gates: '+json.dumps(model.quality_gates(evaluation)),flush=True)
    return artifact


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--local',action='store_true',required=True,help='Confirm execution on the development workstation.')
    parser.add_argument('--directory',default='instance/frost-local')
    commands=parser.add_subparsers(dest='command',required=True)
    for name in ('refresh','submit'):
        item=commands.add_parser(name);item.add_argument('--origin',required=True);item.add_argument('--email',required=True)
        if name=='submit':item.add_argument('--artifact',required=True)
        else:item.add_argument('--with-weather',action='store_true')
    for name in ('validate','prepare'):
        item=commands.add_parser(name);item.add_argument('--lead-hours',type=int,choices=(0,1,3,6),default=0)
    item=commands.add_parser('assemble',help='Verify originals and reuse/reconstruct local monthly evidence; never publish rows.')
    item.add_argument('--catalog',required=True);item.add_argument('--originals',required=True)
    item.add_argument('--cache',action='append',default=[])
    for name in ('train','backtest'):
        item=commands.add_parser(name);item.add_argument('--version',required=True);item.add_argument('--lead-hours',type=int,choices=(0,1,3,6),default=0)
    item=commands.add_parser('infer');item.add_argument('--artifact',required=True);item.add_argument('--features')
    args=parser.parse_args(argv);local_only();directory=Path(args.directory)
    # User-selected private external locations are allowed; inside the checkout
    # keep all source records/candidates beneath the established ignored tree.
    repository=Path(__file__).resolve().parents[1]
    try:relative=directory.resolve().relative_to(repository)
    except ValueError:relative=None
    if relative is not None and (not relative.parts or relative.parts[0] not in ('instance','_local_archive')):
        raise ValueError('Use an ignored instance/ or _local_archive/ directory for private training files.')
    if args.command in ('refresh','submit'):
        remote=Remote(args.origin,args.email)
        try:
            if args.command=='refresh':refresh(remote,directory,with_weather=args.with_weather)
            else:
                raw=Path(args.artifact).read_bytes();model.decode(raw)
                result=remote.request('POST','/neosubzero/frost-vault/models',data=dict(action='import',csrf_token=remote.csrf),files=dict(model=('model.json',raw,'application/json')))
                print(result.decode())
        finally:remote.close()
    elif args.command=='assemble':
        from scripts.frost_dataset import assemble
        assemble(directory,read_json(args.catalog),args.originals,caches=args.cache)
    elif args.command in ('validate','prepare'):
        data=prepare(directory,lead_hours=args.lead_hours);print(json.dumps(dict(examples=len(data['examples']),seasons=sorted({r['season'] for r in data['examples']}))))
    elif args.command in ('train','backtest'):
        data=prepare(directory,lead_hours=args.lead_hours);artifact=train(data,args.version,directory/'checkpoints')
        write_json(directory/'candidates'/(args.version+'.json'),artifact)
        print('Artifact SHA-256: '+vault.digest(vault.canonical(artifact)))
    else:
        artifact=model.decode(Path(args.artifact).read_bytes());values=read_json(args.features) if args.features else artifact['parity_checks'][0]['features']
        print(json.dumps(dict(target=artifact['target'],score=model.score(artifact,values),physical_frost_probability=False,
            source='supplied feature vector' if args.features else 'artifact numerical self-test, not a current forecast')))


if __name__=='__main__':
    import tracemalloc
    tracemalloc.start()
    try:main()
    except (ValueError,RuntimeError,OSError) as exc:print(str(exc),file=sys.stderr);sys.exit(2)
    finally:
        _,peak=tracemalloc.get_traced_memory()
        print(f'Local command peak traced allocations: {peak/1024**2:.2f} MiB (not total process RSS).',file=sys.stderr)
