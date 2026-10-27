"""Small, declarative FROST artifacts and shared features. No training imports.

Scores concern the documented treatment cohort, not physical frost probability.
No aircraft surface temperatures or parking adjustments are manufactured.
"""
import json
import math
import re
from datetime import datetime, timezone

from app.services import frost_analysis as history

VERSION = 'frost-model-v1'
FEATURE_VERSION = 'frost-weather-vector-v1'
TARGET = 'frost_reason_among_recorded_departure_treatments'
MAX_BYTES = 256 * 1024
LIMITATIONS = (
    'Treatment-selected cohort; not a probability of physical frost.',
    'Pretreatment and missing records are not frost-negative observations.',
    'No validated aircraft surface temperature or parking-position adjustment.',
    'Observed-weather backtests do not validate future forecast-weather skill.',
)
FEATURES = (
    'temperature_c', 'dewpoint_c', 'spread_c', 'relative_humidity', 'wind_kt',
    'gust_kt', 'wind_sin', 'wind_cos', 'ceiling_ft', 'visibility_sm', 'clear_sky',
    'night', 'solar_elevation', 'hours_since_sunset', 'cooling_rate_3h_c_per_hour',
    'cooling_rate_6h_c_per_hour', 'cooling_rate_12h_c_per_hour',
    'below_freezing_proxy_hours', 'near_saturation_proxy_hours',
    'clear_night_proxy_hours', 'precipitation_report_proxy_hours', 'fog_mist_proxy_hours',
    'thermal_coverage_proxy_hours', 'wind_coverage_proxy_hours', 'cloud_coverage_proxy_hours',
    'residual_moisture_proxy', 'dewpoint_change_3h', 'spread_change_3h', 'wind_change_3h',
    'rain_reported', 'snow_reported', 'freezing_precip_reported', 'freezing_fog_reported',
    'precipitation_intensity_code', 'precipitation_since_hourly_reset_in', 'cloud_cover_category',
    'wind_direction_change_3h', 'dewpoint_change_6h', 'spread_change_6h', 'wind_change_6h',
)
FEATURE_DEFINITIONS = {name:dict(source='frost_analysis.features / observation',
    unit='C/hour' if 'cooling_rate' in name else 'hours' if 'hours' in name or name=='hours_since_sunset' else
    'C' if name in ('temperature_c','dewpoint_c','spread_c','dewpoint_change_3h','spread_change_3h','dewpoint_change_6h','spread_change_6h') else
    'knots' if name in ('wind_kt','gust_kt','wind_change_3h','wind_change_6h') else 'feet' if name=='ceiling_ft' else
    'statute_miles' if name=='visibility_sm' else 'percent' if name=='relative_humidity' else
    'degrees' if name in ('solar_elevation','wind_direction_change_3h') else 'inches_since_reset' if name=='precipitation_since_hourly_reset_in' else
    'observed_category_not_physical_rate_or_fraction' if name in ('cloud_cover_category','precipitation_intensity_code') else 'dimensionless',
    missing='preserved; training-fold mean imputation plus explicit missing indicator') for name in FEATURES}


def finite(value):
    if value is None:
        return None
    if not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError('Nonfinite or nonnumeric FROST feature/parameter.')
    return float(value)


def vector(features):
    """Same vector contract for archived training features and live inference."""
    current, solar = features.get('observation') or {}, features.get('solar') or {}
    values = {name: current.get(name) for name in FEATURES[:6]}
    values.update(ceiling_ft=current.get('ceiling_ft'), visibility_sm=current.get('visibility_sm'),
        clear_sky=current.get('clear_sky'), night=solar.get('night'),
        solar_elevation=solar.get('elevation_degrees'), hours_since_sunset=solar.get('hours_since_sunset'))
    direction = finite(current.get('wind_direction_degrees'))
    values.update(wind_sin=math.sin(math.radians(direction)) if direction is not None else None,
        wind_cos=math.cos(math.radians(direction)) if direction is not None else None)
    for name in FEATURES[14:26]:
        values[name] = features.get(name)
    changes = features.get('changes_3h') or {}
    values.update(dewpoint_change_3h=changes.get('dewpoint_c'), spread_change_3h=changes.get('spread_c'),
        wind_change_3h=changes.get('wind_kt'))
    codes=current.get('weather_codes')
    values.update(rain_reported='RA' in codes if codes is not None else None,
        snow_reported='SN' in codes if codes is not None else None,
        freezing_precip_reported=any(c in codes for c in ('FZRA','FZDZ')) if codes is not None else None,
        freezing_fog_reported='FZFG' in codes if codes is not None else None,
        precipitation_intensity_code=(-1 if codes.startswith('-') else 1 if codes.startswith('+') else 0) if codes and any(c in codes for c in ('RA','SN','DZ','PL')) else None,
        precipitation_since_hourly_reset_in=current.get('precipitation_since_hourly_reset_in'),
        wind_direction_change_3h=changes.get('wind_direction_degrees'))
    cover_codes={'CLR':0,'SKC':0,'NSC':0,'NCD':0,'FEW':1,'SCT':2,'BKN':3,'OVC':4,'VV':5}
    covers=[cover_codes[c] for c in current.get('cloud_layers',[]) if c in cover_codes]
    values['cloud_cover_category']=max(covers) if covers else None
    changes6=features.get('changes_6h') or {}
    values.update(dewpoint_change_6h=changes6.get('dewpoint_c'),spread_change_6h=changes6.get('spread_c'),wind_change_6h=changes6.get('wind_kt'))
    return [finite(values.get(name)) for name in FEATURES]


def from_observations(at, observations):
    return vector(history.features(at, observations))


def training_label(row):
    """Other explicit departure causes are comparators, NOT frost-absence labels."""
    if row.get('requires_review') or row.get('revision_conflict'):
        return None
    evidence = row.get('classification') or {}
    if evidence.get('activity') != 'DEPARTURE':
        return None
    if evidence.get('label') == 'LIKELY FROST PRESENT' and evidence.get('evidence_strength') == 'strong_reason_not_physical_confirmation':
        return 1
    if evidence.get('label') == 'NON-FROST TREATMENT' and evidence.get('evidence_strength') == 'explicit_other_reason':
        return 0
    return None


def quality_gates(evaluation):
    """Research promotion policy, not operational dispatch/safety thresholds."""
    final = evaluation.get('test') or {}
    gates = {
        'season_isolation': evaluation.get('split') == 'forward_seasons_calibration_then_untouched_test',
        'five_seasons': len(set(evaluation.get('seasons') or [])) >= 5,
        'heldout_samples': final.get('count', 0) >= 100 and min(final.get('positive', 0), final.get('negative', 0)) >= 20,
        'recall': final.get('recall') is not None and final['recall'] >= .7,
        'false_alarm_rate': final.get('false_positive_rate') is not None and final['false_positive_rate'] <= .3,
        'calibration': final.get('ece') is not None and final['ece'] <= .1,
        'beats_climatology': final.get('brier') is not None and final.get('climatology_brier') is not None and final['brier'] < final['climatology_brier'],
        'beats_weather_reference': final.get('brier') is not None and final.get('weather_brier') is not None and final['brier'] <= final['weather_brier'],
        'no_fitting_on_test': bool(evaluation.get('test_season')) and evaluation.get('test_season') not in evaluation.get('fit_seasons', []) + [evaluation.get('calibration_season')],
    }
    return gates


def decode(raw, expected_sha=None):
    from app.services.frost_vault import digest
    if not raw or len(raw) > MAX_BYTES or expected_sha and digest(raw) != expected_sha:
        raise ValueError('Model size or SHA-256 does not match.')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result: raise ValueError('Duplicate model field.')
            result[key] = value
        return result
    try:
        value = json.loads(raw, object_pairs_hook=unique,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite model value.')))
        validate(value)
        return value
    except (TypeError, KeyError, IndexError, RecursionError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('Malformed or incompatible FROST model.') from exc


def validate(artifact):
    if artifact['schema'] != VERSION or artifact['feature_version'] != FEATURE_VERSION or artifact['features'] != list(FEATURES):
        raise ValueError('Unsupported model or feature contract.')
    if artifact['feature_definitions'] != FEATURE_DEFINITIONS: raise ValueError('Feature units/definitions changed.')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', artifact['model_version']):
        raise ValueError('Invalid model version.')
    if artifact['target'] != TARGET or artifact['physical_frost_probability'] is not False:
        raise ValueError('Only the explicitly limited treatment-cohort target is supported.')
    if artifact['dataset']['version'] != history.VERSION or artifact['classification_version'] != history.VERSION:
        raise ValueError('Historical/classification version mismatch.')
    if artifact['dataset']['time_contract'] != history.TIME_CONTRACT or not re.fullmatch(r'[0-9a-f]{64}', artifact['dataset']['fingerprint']):
        raise ValueError('Historical identity/time contract mismatch.')
    if artifact['runtime'] != {'python_min': '3.11', 'format': 'bounded_json', 'training_dependencies': False}:
        raise ValueError('Runtime compatibility mismatch.')
    if not set(LIMITATIONS) <= set(artifact['limitations']):
        raise ValueError('Required evidence limitations are missing.')
    if artifact.get('forecast_validated') is not False or artifact.get('parking_adjustments') is not None:
        raise ValueError('Forecast/parking refinements require a separately validated contract.')
    trained = datetime.fromisoformat(artifact['trained_at_utc'])
    if trained.tzinfo is None: raise ValueError('Training time must be timezone-aware.')
    evaluation=artifact['evaluation'];seasons=evaluation['seasons']
    if artifact['seasons']!=seasons or seasons!=sorted(set(seasons)) or any(not re.fullmatch(r'[0-9]{4}-[0-9]{4}',s) for s in seasons):
        raise ValueError('Invalid season attribution.')
    if len(seasons)<5 or evaluation['fit_seasons']!=seasons[:-2] or evaluation['calibration_season']!=seasons[-2] or evaluation['test_season']!=seasons[-1]:
        raise ValueError('Calibration/test seasons are not isolated chronologically.')
    if len(evaluation['lead_hours_validated'])!=1 or evaluation['lead_hours_validated'][0] not in (0,1,3,6):raise ValueError('Unvalidated lead-time contract.')
    final=evaluation['test']
    if any(type(final[k]) is not int or final[k]<0 for k in ('count','positive','negative')) or final['count']!=final['positive']+final['negative']:
        raise ValueError('Evaluation counts do not reconcile.')
    for key in ('recall','false_positive_rate','brier','ece','climatology_brier','weather_brier'):
        if final.get(key) is not None and not 0<=finite(final[key])<=1: raise ValueError('Invalid validation metric.')
    training=artifact['training']
    if type(training['examples']) is not int or type(training['source_records']) is not int or not final['count']<=training['examples']<=training['source_records']:
        raise ValueError('Training/evaluation counts do not reconcile.')
    n = len(FEATURES)
    for name in ('means', 'scales'):
        values = artifact['preprocessing'][name]
        if len(values) != n or any(finite(v) is None or abs(v) > 1e9 for v in values):
            raise ValueError('Invalid preprocessing dimensions/values.')
    if any(v <= 0 for v in artifact['preprocessing']['scales']):
        raise ValueError('Invalid feature scaling.')
    model = artifact['model']
    if model['kind'] == 'logistic':
        if len(model['coefficients']) != 2*n: raise ValueError('Invalid model dimensions.')
        values = model['coefficients'] + [model['intercept']]
    elif model['kind'] == 'forest':
        if not 1 <= len(model['trees']) <= 32: raise ValueError('Forest is too large.')
        values = []
        for tree in model['trees']:
            if not 1 <= len(tree) <= 31: raise ValueError('Tree is too large.')
            reachable = {0}
            for index, node in enumerate(tree):
                if index not in reachable: raise ValueError('Unreachable tree node.')
                if 'probability' in node:
                    if not 0 <= finite(node['probability']) <= 1: raise ValueError('Invalid leaf.')
                else:
                    if type(node['feature']) is not int or not 0 <= node['feature'] < 2*n: raise ValueError('Invalid split feature.')
                    values.append(node['threshold'])
                    for child in ('left', 'right'):
                        if type(node[child]) is not int or not index < node[child] < len(tree): raise ValueError('Invalid/cyclic tree.')
                        reachable.add(node[child])
    else:
        raise ValueError('Unsupported model algorithm.')
    if any(finite(v) is None or abs(v) > 1e9 for v in values): raise ValueError('Invalid model parameters.')
    calibration = artifact['calibration']
    if calibration['kind'] not in ('none', 'sigmoid'): raise ValueError('Unsupported calibration.')
    if calibration['kind'] == 'sigmoid':
        for name in ('slope', 'intercept'):
            if finite(calibration[name]) is None or abs(calibration[name]) > 1e6: raise ValueError('Invalid calibrator.')
    checks=artifact['parity_checks']
    if not 1<=len(checks)<=8:raise ValueError('Runtime compatibility examples are required.')
    uncalibrated=dict(artifact,calibration=dict(kind='none'))
    for check in checks:
        expected=finite(check['score'])
        if expected is None or not 0<=expected<=1 or abs(score(uncalibrated,check['features'])-expected)>1e-8:
            raise ValueError('Runtime/estimator numerical parity check failed.')
    return artifact


def sigmoid(value):
    return 1 / (1 + math.exp(-max(-700, min(700, value))))


def score(artifact, values):
    if len(values) != len(FEATURES): raise ValueError('Feature dimensions changed.')
    means, scales = artifact['preprocessing']['means'], artifact['preprocessing']['scales']
    inputs = [(finite(v) - means[i])/scales[i] if v is not None else 0.0 for i, v in enumerate(values)]
    inputs += [float(v is None) for v in values]
    model = artifact['model']
    if model['kind'] == 'logistic':
        result = sigmoid(model['intercept'] + sum(v*w for v, w in zip(inputs, model['coefficients'])))
    else:
        results = []
        for tree in model['trees']:
            index = 0
            while 'probability' not in tree[index]:
                node = tree[index]
                index = node['left'] if inputs[node['feature']] <= node['threshold'] else node['right']
            results.append(tree[index]['probability'])
        result = sum(results)/len(results)
    calibrator = artifact['calibration']
    if calibrator['kind'] == 'sigmoid':
        logit = math.log(max(1e-12, result)/max(1e-12, 1-result))
        result = sigmoid(calibrator['slope']*logit + calibrator['intercept'])
    return result
