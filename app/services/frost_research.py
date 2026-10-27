"""Small checked-in research receipt only. Never downloads history or scores.

This page is a dated study, not a model release or self-declared approval gate.
The operational registry/inference service remains the sole release authority.
"""
import json
import math
from pathlib import Path

from app.services import frost_forecast as forecast

REPORT = Path(__file__).resolve().parents[1] / 'data' / 'frost_forecast_research.json'


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
    return value


def status():
    try:
        return load()
    except (OSError, ValueError, KeyError, TypeError):
        # A study receipt is noncritical. Never break CSV import/operations or
        # silently substitute a fabricated successful validation on corruption.
        return None
