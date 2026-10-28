"""Small checked-in research receipt only. Never downloads history or scores.

This page is a dated study, not a model release or self-declared approval gate.
The operational registry/inference service remains the sole release authority.
"""
import json
import math
import re
from pathlib import Path

from app.services import frost_forecast as forecast

REPORT = Path(__file__).resolve().parents[1] / 'data' / 'frost_forecast_research.json'
NATIVE_REPORT = REPORT.with_name('frost_forecast_native_research.json')


def native_summary():
    from app.services.frost_forecast_native import VERSION,HORIZONS
    with NATIVE_REPORT.open('rb') as handle:raw=handle.read(16385)
    if len(raw)>16384:raise ValueError('Native study exceeds bounded metadata.')
    value=json.loads(raw,parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Invalid native study number.')))
    if value.get('feature_version')!=VERSION or value.get('operationally_validated') is not False or value.get('approved') is not False:
        raise ValueError('Invalid native research-only contract.')
    forecast.instant(value['generated_at_utc'])
    for key in ('credible_likely_negatives','confirmed_physical_positives','candidate_count','eligible_cases','eligible_nights'):
        if type(value.get(key)) is not int or value[key]<0:raise ValueError('Invalid native population count.')
    if not isinstance(value.get('horizons'),list) or [r['hours'] for r in value['horizons']]!=list(HORIZONS):raise ValueError('Invalid native horizons.')
    for row in value['horizons']:
        for key in ('cases','likely_positive','other_treatment','validated_cases'):
            if type(row.get(key)) is not int or row[key]<0:raise ValueError('Invalid native horizon count.')
        if row['likely_positive']+row['other_treatment']!=row['cases'] or row['validated_cases']!=0:
            raise ValueError('Unsupported independent-validation claim.')
    for key in ('coverage','readiness','collection','limitations','outcomes'):
        if not isinstance(value.get(key),str):raise ValueError('Missing native study interpretation.')
    if 'experimental_models' in value:
        models=value['experimental_models']
        if value.get('research_policy')!='frost-native-exploratory-v2' or not isinstance(models,list) or len(models)!=8 or value['candidate_count']!=8:
            raise ValueError('Unsupported experimental research policy/count.')
        if [(r['hours'],r['kind']) for r in models]!=[(h,k) for h in HORIZONS for k in ('logistic','forest')]:
            raise ValueError('Invalid experimental horizon/model pair.')
        for row in models:
            for key in ('recall','recall_low','recall_high','fpr','brier','ece'):
                if type(row.get(key)) not in (int,float) or not math.isfinite(row[key]) or not 0<=row[key]<=1:raise ValueError('Invalid experimental metric.')
            for key in ('fit_nights','eval_nights','positive','other','missed','false_alarms'):
                if type(row.get(key)) is not int or row[key]<0:raise ValueError('Invalid experimental sample count.')
            horizon=next(h for h in value['horizons'] if h['hours']==row['hours'])
            if (row['fit_nights']!=horizon['cases'] or row['positive']+row['other']!=row['eval_nights'] or row['eval_nights']>row['fit_nights'] or
                row['missed']>row['positive'] or row['false_alarms']>row['other'] or not row['recall_low']<=row['recall']<=row['recall_high']):
                raise ValueError('Experimental counts/intervals do not reconcile.')
        if not all(isinstance(value.get(k),str) for k in ('experimental_interpretation','experimental_weaknesses')):raise ValueError('Missing experimental limitations.')
    if 'season_coverage' in value:
        coverage=value['season_coverage']
        if not isinstance(coverage,list) or not 1<=len(coverage)<=11:raise ValueError('Invalid seasonal forecast coverage.')
        seasons=[r['season'] for r in coverage]
        if seasons!=sorted(set(seasons)) or any(not re.fullmatch(r'20\d{2}-20\d{2}',s) for s in seasons):raise ValueError('Invalid forecast season identity.')
        for row in coverage:
            if any(type(row.get('h'+str(h))) is not int or row['h'+str(h)]<0 for h in HORIZONS):raise ValueError('Invalid independent-night count.')
        if any(sum(r['h'+str(h['hours'])] for r in coverage)!=h['cases'] for h in value['horizons']):raise ValueError('Seasonal nights do not reconcile with horizon cases.')
        if not re.fullmatch(r'[a-f0-9]{64}',value.get('dataset_fingerprint','')):raise ValueError('Missing sealed dataset reference.')
        if not isinstance(value.get('missing_features'),str):raise ValueError('Missing forecast quality interpretation.')
    return value


def load():
    with REPORT.open('rb') as handle:
        raw = handle.read(32769)
    if len(raw) > 32768:
        raise ValueError('Research summary exceeds bounded page metadata.')
    value = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Invalid research number.')))
    if value['schema'] != forecast.VERSION or value['station'] != 'KRFD' or value['operationally_validated'] is not False:
        raise ValueError('Invalid research-only summary.')
    forecast.instant(value['generated_at_utc'])
    for name in ('documents','seasons','weather_months','weather_samples','weather_unmatched','incomplete_windows','complete_forecast_cases'):
        if not isinstance(value[name],int) or value[name]<0:
            raise ValueError('Invalid research count.')
    if not all(isinstance(value[k],str) for k in ('forecast_coverage','shadow_state','conclusion')):
        raise ValueError('Missing research interpretation.')
    if not isinstance(value['models'],list) or len(value['models'])>8:
        raise ValueError('Invalid research candidates.')
    corrected=value.get('corrected_models',[])
    if not isinstance(corrected,list) or len(corrected)>2:raise ValueError('Invalid corrected candidates.')
    correction=value.get('weather_correction')
    if correction:
        if not all(isinstance(correction.get(k),str) for k in ('weather_version','feature_version','history_version','description')):raise ValueError('Missing correction contract.')
        for key in ('observations','affected_observations','affected_source_records','affected_fluid_windows','affected_h0_applications','affected_h1_applications'):
            if type(correction.get(key)) is not int or correction[key]<0:raise ValueError('Invalid correction count.')
    for row in value['models']+corrected:
        for key in ('model_version','test_season','forecast_result'):
            if not isinstance(row[key],str):raise ValueError('Invalid candidate description.')
        for key in ('count','above_positive','above_missed','above_count','above_nights'):
            if not isinstance(row[key],int) or row[key]<0:raise ValueError('Invalid candidate count.')
        for key in ('recall','false_positive_rate','brier','above_recall'):
            if not isinstance(row[key],(int,float)) or not math.isfinite(row[key]) or not 0<=row[key]<=1:
                raise ValueError('Invalid candidate metric.')
    if NATIVE_REPORT.exists():value['native_study']=native_summary()
    return value


def status():
    try:
        return load()
    except (OSError, ValueError, KeyError, TypeError):
        # A study receipt is noncritical. Never break CSV import/operations or
        # silently substitute a fabricated successful validation on corruption.
        return None
