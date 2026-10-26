"""Versioned research transforms. No operational writes or predictive model.

Source dates AND clocks are Central local calendar values (owner confirmed
2026-10-26). Never use creation time, schedule, or missing spray as an outcome.
"""
import math
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.services import frost_vault as vault
from app.services.neosubzero_frost_history import _parse_cryotech_reason, CRYOTECH_REASON_DESCRIPTIONS

VERSION = 'frost-history-v1'
TIME_CONTRACT = 'owner-confirmed-2026-10-26-local-calendar-America_Chicago'
ZONE = ZoneInfo('America/Chicago')
UTC = timezone.utc


def local_instant(day, clock):
    """Reject both DST folds and gaps; never silently choose an offset."""
    if not day or not clock:
        return None, 'missing_clock'
    try:
        naive = datetime.combine(date.fromisoformat(day), datetime.strptime(clock, '%H:%M').time())
    except ValueError:
        return None, 'invalid_clock'
    choices = {naive.replace(tzinfo=ZONE, fold=fold).astimezone(UTC) for fold in (0, 1)
               if naive.replace(tzinfo=ZONE, fold=fold).astimezone(UTC).astimezone(ZONE).replace(tzinfo=None) == naive}
    if len(choices) != 1:
        return None, 'ambiguous_dst' if choices else 'nonexistent_dst'
    return choices.pop().isoformat(), 'verified_local_calendar'


def classification(reason, *, actual_spray=True, verified_not_sprayed=False, prior_pretreat=None):
    parsed = _parse_cryotech_reason(reason, None, None)
    label, strength, activity = 'UNKNOWN', 'unknown', 'UNKNOWN'
    if actual_spray:
        if parsed['canonical_reason'] == 'Frost':
            label, strength, activity = 'LIKELY FROST PRESENT', 'strong_reason_not_physical_confirmation', 'DEPARTURE'
        elif parsed['canonical_reason'] == 'Pretreat':
            label, strength, activity = 'ANTICIPATED FROST RISK', 'explicit_preventative_reason', 'PRETREAT'
        elif parsed['code_raw'] in CRYOTECH_REASON_DESCRIPTIONS or parsed['description_normalized'] in {
                item for values in CRYOTECH_REASON_DESCRIPTIONS.values() for item in values}:
            label, strength, activity = 'NON-FROST TREATMENT', 'explicit_other_reason', 'DEPARTURE'
    elif verified_not_sprayed and prior_pretreat is False:
        label, strength = 'LIKELY FROST ABSENT', 'verified_no_treatment_not_physical_confirmation'
    elif verified_not_sprayed and prior_pretreat is True:
        label, strength = 'ANTICIPATED FROST RISK', 'prior_pretreatment_not_negative'
    return dict(label=label, evidence_strength=strength, activity=activity, exact_reason=reason,
                normalized_reason=parsed['canonical_reason'], physically_confirmed=False)


def modified_utc(raw):
    for key in ('LastModifiedUTC',):
        value = raw.get(key)
        if value:
            try:
                return datetime.strptime(value, '%m/%d/%Y %H:%M').replace(tzinfo=UTC).isoformat()
            except ValueError:
                pass
    return None


def compact_row(row, document):
    raw = row['raw']
    result = {key: row.get(key) for key in ('source_record_id', 'application_number', 'application_date',
        'tail', 'truck', 'reason_code', 'temperature', 'dew_point', 'temperature_unit', 'precipitation', 'fluids')}
    result.update(row_hash=vault.digest(vault.canonical(raw)), modified_utc=modified_utc(raw),
        source=dict(document_id=document.id, source_row=row['source_row'], document_sha256=document.sha256,
                    extracted_key=document.extracted_key), requires_review=bool(row.get('requires_review')),
        parking=None, parking_quality='not_recorded_in_source', aircraft_surface_temperature=None,
        prior_pretreatment='unknown', time_contract=TIME_CONTRACT)
    result['classification'] = classification(row.get('reason_code'), actual_spray=bool(row.get('fluids')) and not result['requires_review'])
    result['fluids'] = [dict(item) for item in row.get('fluids', [])]
    for fluid in result['fluids']:
        start, quality = local_instant(row.get('application_date'), fluid.get('start_local_clock'))
        end_day = row.get('application_date')
        if end_day and fluid.get('start_local_clock') and fluid.get('end_local_clock') and fluid['end_local_clock'] < fluid['start_local_clock']:
            end_day = (date.fromisoformat(end_day) + timedelta(days=1)).isoformat()
        end, end_quality = local_instant(end_day, fluid.get('end_local_clock'))
        fluid.update(occurred_at_utc=start, ended_at_utc=end, time_quality=quality, end_time_quality=end_quality)
    return result


def deduplicate(records):
    """RecordID is Xchange's row identity, not tail/date. Preserve every source.

    Different content with no unique latest source modification time is a
    conflict, not an import-order last-writer-wins operation.
    """
    groups = defaultdict(list)
    for row in records:
        identity = row.get('source_record_id') or 'missing-{}-{}'.format(row['source']['document_id'], row['source']['source_row'])
        groups[identity].append(row)
    selected, overlaps, revisions, conflicts = [], 0, 0, 0
    for identity, rows in sorted(groups.items()):
        variants = defaultdict(list)
        for row in rows:
            variants[row['row_hash']].append(row)
        overlaps += len(rows) - len(variants)
        revisions += max(0, len(variants) - 1)
        latest = max((row.get('modified_utc') or '' for row in rows), default='')
        winners = {row['row_hash'] for row in rows if latest and row.get('modified_utc') == latest}
        conflict = len(variants) > 1 and len(winners) != 1
        winner = sorted(winners)[0] if len(winners) == 1 else sorted(variants)[0]
        row = dict(variants[winner][0])
        row['sources'] = [item['source'] for item in sorted(rows, key=lambda item: (item['source']['document_id'], item['source']['source_row']))]
        row['revision_evidence'] = [dict(row_hash=key, modified_utc=items[0].get('modified_utc'),
            sources=[item['source'] for item in items]) for key, items in sorted(variants.items())]
        row['revision_conflict'] = conflict
        row['requires_review'] = row['requires_review'] or conflict or not row.get('source_record_id')
        if conflict:
            conflicts += 1
            row['classification'] = classification(None)
            row['fluids'] = []  # Candidate revisions remain traceable; do not weather-match an arbitrary winner.
        selected.append(row)
    return selected, dict(source_rows=len(records), deduplicated_records=len(selected),
        overlapping_rows=overlaps, revisions=revisions, revision_conflicts=conflicts)


def solar(at):
    """NOAA approximate solar geometry, not measured irradiance or surface heat."""
    at = at.astimezone(UTC)
    gamma = 2 * math.pi / (366 if at.year % 4 == 0 and (at.year % 100 != 0 or at.year % 400 == 0) else 365) * (
        at.timetuple().tm_yday - 1 + (at.hour - 12) / 24)
    eq = 229.18 * (.000075 + .001868*math.cos(gamma) - .032077*math.sin(gamma)
        - .014615*math.cos(2*gamma) - .040849*math.sin(2*gamma))
    dec = .006918 - .399912*math.cos(gamma) + .070257*math.sin(gamma) - .006758*math.cos(2*gamma) + .000907*math.sin(2*gamma) - .002697*math.cos(3*gamma) + .00148*math.sin(3*gamma)
    lat, lon = math.radians(42.19151), -89.08760
    hour_angle = math.radians(((at.hour*60 + at.minute + eq + 4*lon) % 1440) / 4 - 180)
    elevation = math.degrees(math.asin(math.sin(lat)*math.sin(dec) + math.cos(lat)*math.cos(dec)*math.cos(hour_angle)))
    ha = math.degrees(math.acos(max(-1, min(1, math.cos(math.radians(90.833))/(math.cos(lat)*math.cos(dec)) - math.tan(lat)*math.tan(dec)))))
    midnight = at.replace(hour=0, minute=0, second=0, microsecond=0)
    return dict(elevation_degrees=round(elevation, 3), night=elevation < -.833,
        sunrise_utc=(midnight + timedelta(minutes=720 - 4*(lon+ha) - eq)).isoformat(),
        sunset_utc=(midnight + timedelta(minutes=720 - 4*(lon-ha) - eq)).isoformat(), basis='NOAA approximate geometry; not measured solar exposure')


def features(at, observations):
    """Only observations available at/before the application, never future leakage.

    Durations are observed-interval proxies, capped at 90-minute gaps; they do
    not imply continuous measured coverage. No sum of cumulative METAR p01i.
    """
    target = datetime.fromisoformat(at)
    conflicting = [row for row in observations if row.get('conflicting_same_time') and target - timedelta(hours=12) <= datetime.fromisoformat(row['utc']) <= target]
    window = sorted((row for row in observations if not row.get('conflicting_same_time') and target - timedelta(hours=12) <= datetime.fromisoformat(row['utc']) <= target), key=lambda row: row['utc'])
    current = window[-1] if window and (target - datetime.fromisoformat(window[-1]['utc'])).total_seconds() <= 5400 else None
    result = dict(matched=bool(current and current.get('temperature_c') is not None and current.get('dewpoint_c') is not None),
        observation=current, solar=solar(target), observation_count_12h=len(window),
        window_start_utc=(target-timedelta(hours=12)).isoformat(), max_observation_age_minutes=90,
        excluded_conflicting_observations=len(conflicting),
        aircraft_surface_temperature=None, cold_soak='unknown_aircraft_history',
        parking_adjustment=None, feature_kind='observational_and_physical_proxies_not_surface_measurements')
    covered, freezing, saturation, clear_night, precip, fog = 0, 0, 0, 0, 0, 0
    thermal_coverage, wind_coverage, cloud_coverage = 0, 0, 0
    for index, row in enumerate(window):
        start = datetime.fromisoformat(row['utc'])
        end = datetime.fromisoformat(window[index+1]['utc']) if index+1 < len(window) else target
        hours = min(max(0, (end-start).total_seconds()/3600), 1.5)
        covered += hours
        temp, dew = row.get('temperature_c'), row.get('dewpoint_c')
        if temp is not None and dew is not None: thermal_coverage += hours
        if row.get('wind_kt') is not None: wind_coverage += hours
        if row.get('clear_sky') is not None: cloud_coverage += hours
        if temp is not None and temp <= 0: freezing += hours
        if temp is not None and dew is not None and 0 <= temp-dew <= 2: saturation += hours
        if solar(start)['night'] and row.get('clear_sky') is True: clear_night += hours
        codes = row.get('weather_codes') or ''
        if any(code in codes for code in ('RA','SN','DZ','PL','IC','GR','GS','UP')): precip += hours
        if any(code in codes for code in ('FG','BR')): fog += hours
    result.update(observed_interval_hours_12h=round(covered, 3), below_freezing_proxy_hours=round(freezing, 3),
        near_saturation_proxy_hours=round(saturation, 3), clear_night_proxy_hours=round(clear_night, 3),
        precipitation_report_proxy_hours=round(precip, 3), fog_mist_proxy_hours=round(fog, 3),
        thermal_coverage_proxy_hours=round(thermal_coverage,3),wind_coverage_proxy_hours=round(wind_coverage,3),
        cloud_coverage_proxy_hours=round(cloud_coverage,3),
        incomplete_window=covered < 10.5 or thermal_coverage < 10.5, residual_moisture_proxy=True if precip > 0 else
            False if covered >= 10.5 and all(row.get('precipitation_since_hourly_reset_in') is not None for row in window) else None)
    result['radiational_cooling_context'] = dict(night=result['solar']['night'],
        clear_sky=current.get('clear_sky') if current else None, wind_kt=current.get('wind_kt') if current else None,
        spread_c=current.get('spread_c') if current else None,
        basis='Physical context only; cloud longwave and wind heat-exchange proxies, not measured aircraft cooling')
    for hours in (3, 6, 12):
        anchor = target-timedelta(hours=hours)
        candidates = [row for row in window if abs((datetime.fromisoformat(row['utc'])-anchor).total_seconds()) <= 3600]
        old = min(candidates, key=lambda row: abs((datetime.fromisoformat(row['utc'])-anchor).total_seconds())) if candidates else None
        delta = None
        if current and old and current.get('temperature_c') is not None and old.get('temperature_c') is not None:
            elapsed = (datetime.fromisoformat(current['utc'])-datetime.fromisoformat(old['utc'])).total_seconds()/3600
            if elapsed > 0: delta = (current['temperature_c']-old['temperature_c'])/elapsed
        result[f'cooling_rate_{hours}h_c_per_hour'] = delta
        changes = {}
        for field in ('dewpoint_c','spread_c','relative_humidity','wind_kt'):
            changes[field] = current[field]-old[field] if current and old and current.get(field) is not None and old.get(field) is not None else None
        if current and old and current.get('wind_direction_degrees') is not None and old.get('wind_direction_degrees') is not None:
            changes['wind_direction_degrees'] = (current['wind_direction_degrees']-old['wind_direction_degrees']+180)%360-180
        changes['clear_sky_before'] = old.get('clear_sky') if old else None
        changes['clear_sky_current'] = current.get('clear_sky') if current else None
        result[f'changes_{hours}h'] = changes
    sunset = datetime.fromisoformat(result['solar']['sunset_utc'])
    if sunset > target: sunset = datetime.fromisoformat(solar(target-timedelta(days=1))['sunset_utc'])
    result['solar']['hours_since_sunset'] = (target-sunset).total_seconds()/3600 if result['solar']['night'] else None
    return result


def analyze(records, observations):
    rows, counts = [], Counter()
    for source in records:
        row = dict(source)
        row['fluids'] = [dict(fluid, weather=features(fluid['occurred_at_utc'], observations)
            if fluid.get('occurred_at_utc') and not row['requires_review'] else dict(matched=False, reason='source_or_time_requires_review'))
            for fluid in source['fluids']]
        counts[row['classification']['label']] += 1
        counts['review_records'] += int(row['requires_review'] or any(fluid['time_quality'] != 'verified_local_calendar' for fluid in row['fluids']))
        counts['weather_matched'] += int(bool(row['fluids']) and all(fluid['weather']['matched'] for fluid in row['fluids']))
        counts['weather_unmatched'] += int(not row['fluids'] or any(not fluid['weather']['matched'] for fluid in row['fluids']))
        counts['incomplete_windows'] += int(any(fluid['weather'].get('incomplete_window', True) for fluid in row['fluids']))
        rows.append(row)
    return dict(schema_version=VERSION, time_contract=TIME_CONTRACT, records=rows, summary=dict(counts),
        descriptive_cohorts=cohorts(rows),
        learning_status='research_only_no_validated_model', uncertainty='Treatment-selected cohort; no inferred unsprayed controls or causal/predictive claims')


def cohorts(rows):
    """One aircraft/application per class/date, not one vote per truck/fluid.

    Descriptive associations only. Aircraft identity/application missing => do
    not coalesce unrelated records. No inferred control population.
    """
    groups, seen = defaultdict(lambda: defaultdict(list)), set()
    for row in rows:
        if row['requires_review']: continue
        label = row['classification']['label']
        identity = (row.get('application_number') or row['source_record_id'],row.get('tail'),row.get('application_date'),label)
        if identity in seen: continue
        seen.add(identity)
        fluid = next((item for item in row['fluids'] if item['weather'].get('matched')),None)
        if not fluid: continue
        context = fluid['weather']
        ob = context['observation']
        bucket = groups[label]
        bucket['applications'].append(identity)
        for key in ('temperature_c','dewpoint_c','spread_c','wind_kt','gust_kt','relative_humidity','ceiling_ft'):
            if ob.get(key) is not None: bucket[key].append(ob[key])
        bucket['night'].append(context['solar']['night'])
        if ob.get('clear_sky') is not None: bucket['clear_sky'].append(ob['clear_sky'])
        if ob.get('wind_direction_degrees') is not None: bucket['wind_direction_degrees'].append(ob['wind_direction_degrees'])
        for key in ('cooling_rate_3h_c_per_hour','near_saturation_proxy_hours','precipitation_report_proxy_hours'):
            if context.get(key) is not None: bucket[key].append(context[key])
    result = {}
    for label,bucket in groups.items():
        values = dict(applications=len(bucket['applications']))
        for key,items in bucket.items():
            if key == 'applications': continue
            if key == 'wind_direction_degrees':
                sectors = Counter(('N','NE','E','SE','S','SW','W','NW')[int((angle+22.5)%360//45)] for angle in items)
                values[key] = dict(samples=len(items),sectors=dict(sectors))
            else:
                values[key] = dict(samples=len(items),mean=sum(items)/len(items),minimum=min(items),maximum=max(items))
        result[label] = values
    return result
