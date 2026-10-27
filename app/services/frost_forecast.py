"""Bounded research forecast contracts. No model approval or operational writes.

TAF is an issued forecast, NOT a complete thermodynamic frost input. MOS cycle
time alone is NOT proof of dissemination time. Keep both limitations explicit.
"""
import ast
import csv
import io
import math
import re
from datetime import datetime, timedelta, timezone

from app.services import frost_analysis as analysis, frost_model as model, frost_weather as weather

VERSION = 'frost-forecast-research-v1'
MAX_BYTES = 2 * 1024 * 1024
UTC = timezone.utc


def instant(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Explicit UTC offset required.')
    return result.astimezone(UTC)


def station(value):
    if value != weather.STATION:
        raise ValueError('Research source must match the exact KRFD station contract.')


def verify_metar(observation):
    """Independent sampled raw-report checks, not another call to IEM parser.

    METAR T remarks carry tenths Celsius; primary groups carry whole Celsius.
    RH/spread are derived, not directly measured METAR fields. No receipt time
    is inferred from the observation timestamp or a later retrieval.
    """
    raw=observation['raw_metar']
    if not re.match(r'^(?:METAR |SPECI )?KRFD\s',raw):
        raise ValueError('Raw METAR is not KRFD.')
    at=instant(observation['utc'])
    token=re.search(r'\b(\d{6})Z\b',raw)
    if not token or token[1]!=at.strftime('%d%H%M'):
        raise ValueError('Raw METAR observation clock mismatch.')
    checks=[]
    precise=re.search(r'\bT([01])(\d{3})([01])(\d{3})\b',raw)
    primary=re.search(r'(?:^|\s)(M?\d{2})/(M?\d{2})(?:\s|$)',raw)
    if precise:
        t=(-1 if precise[1]=='1' else 1)*int(precise[2])/10
        d=(-1 if precise[3]=='1' else 1)*int(precise[4])/10
        tolerance=.06  # Archive Fahrenheit decimal precision can lose <0.1 F.
    elif primary:
        t=float(primary[1].replace('M','-'));d=float(primary[2].replace('M','-'))
        tolerance=.51  # IEM may retain sub-degree source precision.
    else:
        t=d=None;tolerance=0
    for name,value in (('temperature_c',t),('dewpoint_c',d)):
        if value is not None and observation.get(name) is not None:
            if abs(value-observation[name])>tolerance:
                raise ValueError(f'Raw METAR {name} mismatch: report={value}, normalized={observation[name]}, raw={raw}')
            checks.append(name)
    wind=re.search(r'\b(\d{3}|VRB)(\d{2,3})(?:G(\d{2,3}))?KT\b',raw)
    if wind:
        for name,value in (('wind_kt',float(wind[2])),('gust_kt',float(wind[3]) if wind[3] else None),
                ('wind_direction_degrees',float(wind[1]) if wind[1]!='VRB' and int(wind[2]) else None)):
            if value is not None:
                if observation.get(name) is None or abs(value-observation[name])>.01:
                    raise ValueError('Raw METAR '+name+' mismatch.')
                checks.append(name)
    layers=re.findall(r'\b(FEW|SCT|BKN|OVC|VV)(\d{3}|///)',raw)
    if layers:
        if [r[0] for r in layers] != observation['cloud_layers']:
            raise ValueError(f"Raw METAR cloud coverage mismatch: {layers} vs {observation['cloud_layers']}, raw={raw}")
        checks.append('cloud_layers')
        ceilings=[int(h)*100 for c,h in layers if c in ('BKN','OVC','VV') and h!='///']
        if ceilings and observation.get('ceiling_ft')!=min(ceilings):
            raise ValueError('Raw METAR ceiling mismatch.')
    for code in (observation.get('weather_codes') or '').split():
        if code not in raw.split():
            raise ValueError('Normalized precipitation/obstruction absent from raw METAR.')
    if observation.get('weather_codes'):checks.append('weather_codes')
    return dict(checked_fields=checks,temperature_precision='tenths_C_T_remark' if precise else 'whole_C_primary',
        humidity_basis='provider-derived RH from temperature/dew point, not direct METAR humidity',
        receipt_timestamp_available=False,correction='COR' in raw.split())


def _number(value):
    if value in ('', None):
        return None
    result = float(value)
    if not math.isfinite(result):
        raise ValueError('Nonfinite forecast measurement.')
    return result


def _items(value):
    result = ast.literal_eval(value or '[]')
    if not isinstance(result, list) or len(result) > 8:
        raise ValueError('Invalid forecast layers.')
    return result


def parse_taf(raw):
    """IEM CSV UTC columns; categorical/probability groups remain separate.

    No conversion of TEMPO/PROB into an unconditional deterministic forecast.
    Native TAF starts are intervals, not hourly point measurements.
    """
    if len(raw) > MAX_BYTES:
        raise ValueError('Forecast response exceeds the research bound.')
    reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig')))
    required = {'station', 'valid', 'fx_valid', 'raw', 'product_id', 'ftype'}
    if not required <= set(reader.fieldnames or []):
        raise ValueError('Unrecognized TAF archive schema.')
    rows = []
    for source in reader:
        if len(rows) >= 4096 or source['station'] != 'KRFD':
            raise ValueError('Forecast station/count mismatch.')
        # This endpoint is explicitly requested in UTC. Product IDs independently
        # retain the WMO issuance date/minute; do not use model initialization.
        issued = datetime.strptime(source['valid'], '%Y-%m-%d %H:%M').replace(tzinfo=UTC)
        valid = datetime.strptime(source['fx_valid'], '%Y-%m-%d %H:%M').replace(tzinfo=UTC)
        product = source['product_id']
        if not re.fullmatch(r'\d{12}-KLOT-FTUS\d{2}-TAFRFD(?:-[A-Z]{3})?', product):
            raise ValueError('Unexpected KRFD TAF product identity.')
        if product[:12] != issued.strftime('%Y%m%d%H%M'):
            raise ValueError('Forecast issue timestamp conflicts with product evidence.')
        covers, heights = _items(source.get('skyc')), _items(source.get('skyl'))
        ceilings = [_number(height) for cover, height in zip(covers, heights) if cover in ('BKN', 'OVC', 'VV')]
        codes = _items(source.get('presentwx'))
        rows.append(dict(issued_at_utc=issued.isoformat(), valid_at_utc=valid.isoformat(),
            product_id=product, raw_segment=source['raw'], kind=source['ftype'],
            observation=dict(utc=valid.isoformat(), temperature_c=None, dewpoint_c=None,
                spread_c=None, relative_humidity=None, wind_kt=_number(source.get('sknt')),
                wind_direction_degrees=_number(source.get('drct')), gust_kt=_number(source.get('gust')),
                visibility_sm=_number(source.get('visibility')), cloud_layers=covers,
                clear_sky=all(c in ('CLR', 'SKC', 'NSC', 'NCD') for c in covers) if covers else None,
                ceiling_ft=min((x for x in ceilings if x is not None), default=None),
                weather_codes=' '.join(codes) if codes else None,
                precipitation_since_hourly_reset_in=None)))
    return rows


def verify_bulletin(raw, product):
    if len(raw) > 32768:
        raise ValueError('TAF bulletin exceeds bound.')
    text = raw.decode('utf-8')
    issued = datetime.strptime(product[:12], '%Y%m%d%H%M').replace(tzinfo=UTC)
    if not re.search(r'\bKRFD\s+' + issued.strftime('%d%H%M') + r'Z\b', text):
        raise ValueError('Original TAF issue/station evidence mismatch.')
    # Original validity, including midnight/month/year boundaries. TAF windows
    # are at most 30 hours; resolve day-of-month around the actual issue date.
    match = re.search(r'\bKRFD\s+\d{6}Z\s+(\d{2})(\d{2})/(\d{2})(\d{2})\b', text)
    if not match:
        raise ValueError('Original TAF validity evidence missing.')
    def day_hour(day, hour):
        candidates = [issued.replace(hour=0, minute=0) + timedelta(days=i) for i in range(-1, 4)]
        candidates = [d + timedelta(hours=hour) for d in candidates if d.day == day]
        if len(candidates) != 1:
            raise ValueError('Ambiguous TAF validity date.')
        return candidates[0]
    start, end = day_hour(int(match[1]), int(match[2])), day_hour(int(match[3]), int(match[4]))
    if not timedelta(0) < end - start <= timedelta(hours=30):
        raise ValueError('Invalid TAF validity duration.')
    return dict(start=start.isoformat(), end=end.isoformat(), issued=issued.isoformat())


def select_taf(rows, bulletins, *, cutoff, target):
    """Latest proven issued bulletin by cutoff; prevailing group at target.

    Never fall back to an older bulletin to avoid an ambiguous latest one.
    A future amendment cannot leak into a historical prediction.
    """
    if cutoff.tzinfo is None or target.tzinfo is None or target < cutoff:
        raise ValueError('Invalid research cutoff/target.')
    available = [r for r in rows if instant(r['issued_at_utc']) <= cutoff]
    if not available:
        return None
    issued = max(r['issued_at_utc'] for r in available)
    products = {r['product_id'] for r in available if r['issued_at_utc'] == issued}
    if len(products) != 1:
        return None
    product = products.pop()
    if product not in bulletins:
        return None
    validity = verify_bulletin(bulletins[product], product)
    if not instant(validity['start']) <= target < instant(validity['end']):
        return None
    groups = [r for r in available if r['product_id'] == product and r['kind'] in ('Observation', 'Forecast')
              and instant(r['valid_at_utc']) <= target]
    if not groups:
        return None
    selected = max(groups, key=lambda r: r['valid_at_utc'])
    if ' '.join(selected['raw_segment'].split()) not in ' '.join(bulletins[product].decode('utf-8').split()):
        raise ValueError('Selected forecast segment is absent from the original bulletin.')
    # Unresolved transition/probability evidence remains visible, not averaged.
    return dict(selected, target_at_utc=target.isoformat(), cutoff_at_utc=cutoff.isoformat(),
        alternative_groups=[dict(kind=r['kind'], raw=r['raw_segment']) for r in available
            if r['product_id'] == product and r['kind'] not in ('Observation', 'Forecast')],
        thermodynamic_coverage=False, operationally_validated=False)


def research_vector(observations, selected, *, cutoff, target):
    """Partial TAF sensitivity experiment, NOT a contract-complete backtest.

    Past observed trends are truncated at cutoff. Only forecast-supported fields
    replace the endpoint. Unknown future T/dew/precip are NEVER observation-filled.
    Missing values use the unchanged model's documented imputation indicators.
    """
    known_at=selected.get('issued_at_utc')
    if known_at is None and selected.get('availability_basis')=='NOAA_original_object_Last-Modified':
        known_at=selected.get('available_by_utc')
    if known_at is None or instant(known_at) > cutoff or instant(selected['target_at_utc']) != target:
        raise ValueError('Future or misaligned forecast evidence.')
    valid_at=instant(selected.get('valid_at_utc') or target.isoformat())
    if valid_at>target:raise ValueError('Forecast value is valid after the requested input time.')
    past = [r for r in observations if instant(r['utc']) <= cutoff]
    features = analysis.features(cutoff.isoformat(), past)
    features['observation'] = dict(selected['observation'], utc=valid_at.isoformat())
    features['solar'] = analysis.features(target.isoformat(), [])['solar']
    # Twelve-hour history at the input endpoint contains an unknown interval.
    # Do not carry historical duration/changes forward across that gap.
    if target > cutoff:
        for key in list(features):
            if key.startswith(('cooling_rate_', 'changes_')) or key.endswith('_hours'):
                features[key] = {} if key.startswith('changes_') else None
        features['residual_moisture_proxy'] = None
    return model.vector(features)


def nws_grid(payload, *, cutoff, target):
    """Existing NWS point forecast grid for PROSPECTIVE research, never history.

    Keep updateTime as the documented grid revision timestamp and retain the
    actual retrieval receipt separately. Do not relabel it a MOS/HRRR cycle.
    Only unit-compatible variables are projected; unavailable parameters stay
    missing. No conversion of forecast QPF into an observed hourly-reset total.
    """
    geometry = payload.get('geometry') or {}
    if geometry.get('type') != 'Polygon':
        raise ValueError('NWS forecast grid geometry missing.')
    ring = geometry['coordinates'][0]
    if not 4 <= len(ring) <= 16:
        raise ValueError('Unexpected forecast cell geometry.')
    x, y = weather.STATION['longitude'], weather.STATION['latitude']
    inside = False
    for (x1,y1),(x2,y2) in zip(ring,ring[1:]):
        if not all(math.isfinite(v) for v in (x1,y1,x2,y2)) or abs(x2-x1)>.1 or abs(y2-y1)>.1:
            raise ValueError('Forecast grid is not a bounded KRFD cell.')
        if (y1>y)!=(y2>y) and x<(x2-x1)*(y-y1)/(y2-y1)+x1:
            inside=not inside
    if not inside:
        raise ValueError('Forecast grid does not contain KRFD.')
    properties=payload['properties']
    issued=instant(properties['updateTime'])
    if issued>cutoff or target<cutoff:
        raise ValueError('Future NWS forecast revision or invalid target.')
    from app.services.neosubzero_weather import _grid_intervals, _grid_value_at, FORECAST_STALE_TTL
    if cutoff-issued>FORECAST_STALE_TTL:
        raise ValueError('NWS forecast exceeds the existing stale-forecast limit.')
    def value(key, unit):
        quantity=properties.get(key) or {}
        if not quantity.get('values'):return None
        if quantity.get('uom')!=unit:
            raise ValueError('Unexpected NWS forecast units for '+key)
        return _number(_grid_value_at(_grid_intervals(quantity),target))
    t,d=value('temperature','wmoUnit:degC'),value('dewpoint','wmoUnit:degC')
    speed,gust=value('windSpeed','wmoUnit:km_h-1'),value('windGust','wmoUnit:km_h-1')
    visibility,ceiling=value('visibility','wmoUnit:m'),value('ceilingHeight','wmoUnit:m')
    return dict(issued_at_utc=issued.isoformat(),target_at_utc=target.isoformat(),
        issue_basis='NWS forecast grid updateTime; actual retrieval time retained separately',
        source='api.weather.gov / KRFD containing grid',operationally_validated=False,
        thermodynamic_coverage=t is not None and d is not None,
        observation=dict(utc=target.isoformat(),temperature_c=t,dewpoint_c=d,
            spread_c=t-d if t is not None and d is not None else None,
            relative_humidity=value('relativeHumidity','wmoUnit:percent'),
            wind_kt=speed/1.852 if speed is not None else None,gust_kt=gust/1.852 if gust is not None else None,
            wind_direction_degrees=value('windDirection','wmoUnit:degree_(angle)'),
            cloud_layers=[],clear_sky=None,ceiling_ft=ceiling/0.3048 if ceiling is not None else None,
            visibility_sm=visibility/1609.344 if visibility is not None else None,
            weather_codes=None,precipitation_since_hourly_reset_in=None),
        sky_cover_percent=value('skyCover','wmoUnit:percent'),
        limitations='Forecast sky percentage/QPF/weather categories are not substituted for measured METAR codes or reset precipitation.')
