"""LOCAL exploratory evaluation, distinct from operational model approval.

Uses the existing sealed forecast dataset, fitter and bounded JSON scorer.
No registry import, approval, source retrieval or operational inference path.
"""
import math
import random
import platform
from pathlib import Path
from collections import Counter
from datetime import datetime, timezone

from app.services import frost_forecast_native as native, frost_model as model
from scripts.frost_local import local_only, fit, metrics, forward_split

POLICY = 'frost-native-exploratory-v2'
KINDS = ('logistic', 'forest')


def population(rows):
    return dict(nights=len(rows),frost_reason=sum(r['label']==1 for r in rows),
        other_treatment=sum(r['label']==0 for r in rows))


def eligibility(rows):
    """Feasibility floor, NOT a power calculation or a promotion threshold.

    Pool small real seasons for fitting, never invent 100 nights in every year.
    150 total/30 per class, four seasons, two evaluable forward folds permit
    exploratory comparisons only. Fold uncertainty is always reported.
    """
    local_only()
    reasons=[];seasons=sorted({r['season'] for r in rows});folds=[]
    if len({r['night'] for r in rows})!=len(rows):reasons.append('Duplicate operational nights.')
    if any(type(r['label']) is not int or r['label'] not in (0,1) for r in rows):reasons.append('Invalid treatment-reason labels.')
    counts=population(rows)
    if len(seasons)<4:reasons.append('Need at least four chronological seasons.')
    if len(rows)<150 or min(counts['frost_reason'],counts['other_treatment'])<30:
        reasons.append('Need 150 real nights and 30 examples per treatment-reason class in aggregate.')
    for index in range(2,len(seasons)):
        training,testing=forward_split(rows,seasons[:index],seasons[index])
        a,b=population(training),population(testing)
        eligible=(a['nights']>=60 and min(a['frost_reason'],a['other_treatment'])>=15 and
            b['nights']>=20 and min(b['frost_reason'],b['other_treatment'])>=5)
        folds.append(dict(fit_seasons=seasons[:index],evaluation_season=seasons[index],
            fitting=a,evaluation=b,eligible=eligible,
            reason=None if eligible else 'Insufficient fold support: fitting 60/15 per class; evaluation 20/5 per class.'))
    if sum(f['eligible'] for f in folds)<2:reasons.append('Need two supported forward-season evaluation folds.')
    return dict(policy=POLICY,ready=not reasons,reasons=reasons,counts=counts,seasons=seasons,folds=folds,
        purpose='EXPERIMENTAL RESEARCH: frost reason among recorded treatments',
        operational_approval_eligible=False,physical_frost_probability=False,
        independent_validation='Not established: all historical seasons are retrospective/reviewed; new prospective outcome evidence required.')


def wilson(success,total):
    if not total:return None
    z=1.959963984540054;p=success/total;denominator=1+z*z/total
    middle=(p+z*z/(2*total))/denominator
    radius=z*math.sqrt(p*(1-p)/total+z*z/(4*total*total))/denominator
    return [max(0,middle-radius),min(1,middle+radius)]


def describe(records):
    """One prediction per operational night. Intervals are conditional research.

    Wilson rate intervals assume independent nights; weather serial dependence
    can widen real uncertainty. Brier bootstrap resamples nights within seasons,
    preserving season sizes. It does not estimate unseen-season uncertainty.
    """
    value=metrics([r['label'] for r in records],[r['score'] for r in records])
    value['recall_interval95']=wilson(value['positive']-value['missed'],value['positive'])
    value['false_positive_interval95']=wilson(value['false_alarms'],value['negative'])
    if records:
        rng=random.Random(110);groups={s:[(r['score']-r['label'])**2 for r in records if r['season']==s] for s in sorted({r['season'] for r in records})}
        boot=sorted(sum(rng.choice(g) for g in groups.values() for _ in g)/len(records) for _ in range(1000))
        value['brier_interval95']=[boot[24],boot[974]]
    else:value['brier_interval95']=None
    return value


def weather_reference(row):
    values=dict(zip(native.FEATURES,row['features']))
    t,s=values['fcst_temperature_c'],values['fcst_spread_c']
    # Pre-specified diagnostic score, not a calibrated frost probability.
    return .8 if t is not None and s is not None and t<=2 and s<=2 else .2


def evaluate(dataset,lead_hours,version):
    local_only()
    from scripts import frost_forecast_native as pipeline
    pipeline.verify(dataset);native.contract(lead_hours)
    if dataset['schema']!=pipeline.SCHEMA or dataset['feature_version']!=native.VERSION or dataset.get('implementation_sha256')!=pipeline.code_hash():
        raise ValueError('Reassemble verified cached evidence under the current implementation.')
    rows=[r for r in dataset['examples'] if r['lead_hours']==lead_hours]
    gate=eligibility(rows)
    if not gate['ready']:raise ValueError('Exploratory fitting blocked: '+' '.join(gate['reasons']))
    for row in rows:
        from datetime import timedelta
        if (row['contract']!=native.contract(lead_hours) or row.get('target')!=native.TARGET or
            len(row['features'])!=len(native.FEATURES) or
            pipeline.clock.instant(row['event_at'])-pipeline.clock.instant(row['at'])!=timedelta(hours=lead_hours) or
            any(v is not None and (type(v) not in (int,float) or not math.isfinite(v)) for v in row['features'])):
            raise ValueError('Mixed, nonfinite or leaking horizon/target features.')
    predictions={k:[] for k in KINDS+('weather_reference','climatology')};folds=[]
    for fold in gate['folds']:
        if not fold['eligible']:continue
        training,testing=forward_split(rows,fold['fit_seasons'],fold['evaluation_season'])
        prior=sum(r['label'] for r in training)/len(training);scores={}
        for kind in KINDS:
            fitted=fit(training,kind,feature_names=native.FEATURES)
            scores[kind]=[model.score_vector(fitted,r['features'],len(native.FEATURES)) for r in testing]
        scores['weather_reference']=[weather_reference(r) for r in testing]
        scores['climatology']=[prior]*len(testing)
        results={}
        for kind,values in scores.items():
            records=[dict(night=r['night'],season=r['season'],label=r['label'],score=p,
                features=r['features'],missing=r['missing']) for r,p in zip(testing,values)]
            predictions[kind].extend(records);results[kind]=describe(records)
        folds.append(dict(fold,metrics=results))
    summaries={k:describe(v) for k,v in predictions.items()}
    regimes={}
    feature_index={name:i for i,name in enumerate(native.FEATURES)}
    for name,predicate in (
        ('above_freezing',lambda r:r['features'][feature_index['fcst_temperature_c']] is not None and r['features'][feature_index['fcst_temperature_c']]>0),
        ('clear_humid',lambda r:all(r['features'][feature_index[n]] is not None for n in ('fcst_cloud_cover_percent','fcst_relative_humidity')) and r['features'][feature_index['fcst_cloud_cover_percent']]<=25 and r['features'][feature_index['fcst_relative_humidity']]>=85),
        ('calm',lambda r:r['features'][feature_index['fcst_wind_kt']] is not None and r['features'][feature_index['fcst_wind_kt']]<=3),
        ('precipitation',lambda r:any(r['features'][feature_index[n]]==1 for n in ('fcst_rain','fcst_snow','fcst_freezing_rain'))),
        ('cooling',lambda r:r['features'][feature_index['fcst_temperature_change_c']] is not None and r['features'][feature_index['fcst_temperature_change_c']]<0),
        ('missing_features',lambda r:bool(r['missing']))):
        regimes[name]={k:describe([r for r in v if predicate(r)]) for k,v in predictions.items()}
    candidates=[]
    for kind in KINDS:
        fitted=fit(rows,kind,feature_names=native.FEATURES)
        candidates.append(pipeline.sealed(dict(schema='frost-native-experimental-candidate-v2',model_version=version+'-'+kind,
            policy=POLICY,contract=native.contract(lead_hours),dataset_fingerprint=dataset['fingerprint'],
            target=native.TARGET,training=population(rows),evaluation=summaries[kind],
            evaluation_basis='Retrospective forward-season out-of-fold; final candidate refit uses all historical nights. NOT independent validation.',
            approved=False,operationally_validated=False,registry_compatible=False,physical_frost_probability=False,
            **fitted)))
    import sklearn, numpy, scipy
    report=pipeline.sealed(dict(schema='frost-native-exploratory-report-v2',policy=POLICY,model_version=version,
        feature_version=native.VERSION,lead_hours=lead_hours,dataset_fingerprint=dataset['fingerprint'],
        generated_at_utc=datetime.now(timezone.utc).isoformat(),eligibility=gate,folds=folds,summary=summaries,regimes=regimes,
        predictions={k:[{name:r[name] for name in ('night','season','label','score')} for r in v] for k,v in predictions.items()},
        runtime=dict(seed=0,threshold=.5,python=platform.python_version(),sklearn=sklearn.__version__,numpy=numpy.__version__,scipy=scipy.__version__,
            research_code_sha256=pipeline.vault.digest(Path(__file__).read_bytes().replace(b'\r\n',b'\n')),
            preprocessing='Fitting-fold-only means/scales + missingness indicators; no post-hoc calibration or threshold tuning.'),
        limitations=['Treatment-selected sample; other treatments are not physical frost-negative controls.',
            '2025-26 reviewed; all results exploratory, including earlier small seasons. No unseen final holdout.',
            'Wilson intervals assume independent nights; conditional stratified-night bootstrap does not cover unseen-season uncertainty.',
            '44 meteorological/context features plus missingness versus small sample: regularized/shallow models can still overfit.',
            'H0 uses a pre-cutoff issued forecast for current time, not a validated physical nowcast.',
            'No model selected for approval. No physical probability calibration or parking adjustments.'],
        approved=False,operationally_validated=False,physical_frost_probability=False))
    return report,candidates
