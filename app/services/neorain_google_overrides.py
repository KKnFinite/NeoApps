"""Approved, one-shot Google I/O. No polling writer, outbox or automatic retry.

Live writers remain disabled unless their manual-input contract is certified.
The certificate never bypasses runtime identity, date or formula checks.
ETD deliberately has NO Google destination.
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from flask import current_app
from app.extensions import db
from app.models import NeoRainMissionOverride, NeoRainOverrideAudit, SortDateMission, SortDateGoogleMissionLink
from app.services.mission_identity import flight_identity
from app.services.neorain_overrides import FIELDS, pack, unpack, operator_projection

MB = '10Il5VRW-O3-T9RhrVPvvDphUh03vD-heMbqJwxxmyDg'
RAIN = '13ub-NCOHzUpgAvxzKHK_noRTficTSFqufkNNOKKfPcM'
REPTILE = '1Vc3hKTLCTe9YDtBe5i9zMOs6dr58FTLfu0ZZn8WjQP0'
APPROVED = {
    'sta': (MB, 'Inbound', 'AB', 4, 50, 'P'),
    'block_in': (REPTILE, 'Sim Unload', 'T', 4, 53, 'A'),
    'elmac': (REPTILE, 'Sim Load', 'M', 4, 53, 'A'),
    'ramp_load_complete': (RAIN, 'Outbound', 'M', 3, 50, 'A'),
    'crew_load_complete': (RAIN, 'Outbound', 'N', 3, 50, 'A'),
    'official_block_out': (RAIN, 'Outbound', 'O', 3, 50, 'A'),
    'included': (RAIN, 'Outbound', 'Q', 3, 50, 'A'),
    'no_return': (RAIN, 'Outbound', 'S', 3, 50, 'A'),
}
EPOCH = datetime(1899, 12, 30)


class GoogleOverrideBlocked(ValueError):
    pass


class GoogleWriteUncertain(GoogleOverrideBlocked):
    """A write may have reached Google; never retry or claim NOT_WRITTEN."""


def cell_number(field, row):
    _book, _tab, col, start, end, _flight = APPROVED[field]
    if type(row) is not int or not start <= row <= end:
        raise GoogleOverrideBlocked('Matched flight is outside the approved range.')
    return f'{col}{row}'


def google_value(mission, field, values):
    if field == 'included':
        value = values['late_metrics_included_override']
        if type(value) is not bool:
            raise GoogleOverrideBlocked('A typed inclusion checkbox is required.')
        return not value  # Google Q TRUE = excluded.
    if field == 'no_return':
        from app.services.departure_progress import DEPARTURE_STATUS_RANK
        status = values.get('departure_status')
        if not isinstance(status, str) or status not in DEPARTURE_STATUS_RANK:
            raise GoogleOverrideBlocked('A confirmed No Return checkbox state is required; blank or unknown is not FALSE.')
        return status == 'departed'
    attr = FIELDS[field][1][0]
    value = unpack(attr, values.get(attr))
    if not isinstance(value, datetime):
        raise GoogleOverrideBlocked('Blank values are never written to Google.')
    local = value.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(mission.timezone)).replace(tzinfo=None)
    return (local-EPOCH).total_seconds()/86400 if field == 'sta' else (local.hour*3600+local.minute*60)/86400


def validate_manual_cell(cell, *, spill_owned=False):
    entered = cell.get('userEnteredValue', {})
    if spill_owned or 'formulaValue' in entered:
        raise GoogleOverrideBlocked('Formula or array-spill ownership: Google write withheld.')
    # A rendered nonblank value without a direct constant is generated, not input.
    if cell.get('effectiveValue') and not entered:
        raise GoogleOverrideBlocked('Generated cell is not a manual input.')
    if entered and not set(entered).issubset({'numberValue', 'boolValue', 'stringValue'}):
        raise GoogleOverrideBlocked('Unknown cell ownership.')


def match_row(mission, rows):
    matched = [r for r in rows if flight_identity(r.get('flight')) == flight_identity(mission.flight_number)]
    if len(matched) != 1:
        raise GoogleOverrideBlocked('Missing or ambiguous Google flight identity.')
    row = matched[0]
    if row.get('direction') != mission.mission_type or row.get('gateway') != mission.gateway_code:
        raise GoogleOverrideBlocked('Google direction/gateway mismatch.')
    if row.get('sort_date') != mission.sort_date.isoformat() or row.get('sort_name', '').lower() != mission.sort_name.lower():
        raise GoogleOverrideBlocked('Google operational sort/date mismatch.')
    place = mission.origin if mission.mission_type == 'arrival' else mission.destination
    if row.get('place', '').upper() != (place or '').upper() or row.get('tail', '').upper() != (mission.assigned_tail_number or '').upper():
        raise GoogleOverrideBlocked('Google tail or route identity changed.')
    return row


def certified_write(mission, field, values):
    """Adapter is admitted only after a staged manual-range/protection audit.

    No credentials, inferred row offsets or unapproved ETD mapping are accepted.
    A production certificate/adapter must provide bounded, fresh observations
    and verified service-account permission; it is not created by this code.
    """
    if field not in APPROVED:
        raise GoogleOverrideBlocked('No approved Google destination for this field.')
    adapter = current_app.config.get('NEORAIN_CERTIFIED_GOOGLE_ADAPTER')
    if adapter is None:
        from app.services.neorain_google_runtime import RuntimeGoogleAdapter, RACE_REASON
        adapter = RuntimeGoogleAdapter(current_app.config)
        if field not in adapter.certified_fields:
            raise GoogleOverrideBlocked(RACE_REASON)
    if adapter is None or field not in getattr(adapter, 'certified_fields', ()):
        raise GoogleOverrideBlocked('Manual input protection, sort mapping and service-account write permission are not certified for this range.')
    book, tab, _col, _start, _end, _flight = APPROVED[field]
    if not adapter.permission_verified(book):
        raise GoogleOverrideBlocked('Production service-account write access is not verified.')
    row = match_row(mission, adapter.read_identities(book, tab))
    cell = cell_number(field, row['row'])
    observation = adapter.inspect_input(book, tab, cell)
    if not observation.get('manual_contract_verified'):
        raise GoogleOverrideBlocked('Manual-input/formula-spill contract cannot be proven.')
    validate_manual_cell(observation['cell'], spill_owned=observation.get('spill_owned', True))
    value = google_value(mission, field, values)
    # Recheck identity/ownership immediately before the sole write. The certified
    # adapter must enforce the contract atomically on the Google side as well.
    row_again = match_row(mission, adapter.read_identities(book, tab))
    if row_again['row'] != row['row']:
        raise GoogleOverrideBlocked('Google rows changed during verification.')
    observation_again = adapter.inspect_input(book, tab, cell)
    if not observation_again.get('manual_contract_verified'):
        raise GoogleOverrideBlocked('Manual-input contract changed during verification.')
    validate_manual_cell(observation_again['cell'], spill_owned=observation_again.get('spill_owned', True))
    try:
        adapter.write_input_once(book, tab, cell, value)
    except GoogleOverrideBlocked:
        raise
    except Exception:
        raise GoogleWriteUncertain('Google write result is uncertain. No automatic retry.') from None
    try:
        readback = adapter.read_input(book, tab, cell)
    except Exception:
        raise GoogleWriteUncertain('Google readback unavailable after write. No automatic retry.') from None
    if isinstance(value, bool):
        confirmed = type(readback) is bool and readback is value
    else:
        confirmed = type(readback) in (int, float) and readback == value
    if not confirmed:
        raise GoogleWriteUncertain('Google readback did not confirm the value. No automatic retry.')
    return f'{tab}!{cell}'


def attempt_once(override_id, generation):
    row = db.session.get(NeoRainMissionOverride, override_id)
    if row is None:
        return
    mission = SortDateMission.query.filter_by(id=row.sort_date_mission_id).populate_existing().with_for_update().one()
    row = NeoRainMissionOverride.query.filter_by(id=override_id).populate_existing().with_for_update().one()
    if not row.active or row.generation != generation or row.sync_status != 'PENDING':
        db.session.rollback()
        return  # No stale, deleted or already attempted generation may replay.
    link = SortDateGoogleMissionLink.query.filter_by(sort_date_mission_id=mission.id,
        sort_date_operation_id=row.sort_date_operation_id, mission_type=mission.mission_type).first()
    row.sync_attempted_at = datetime.utcnow()
    try:
        from app.services.google_rain_integration_mode import rain_integration_mode, NEO_ONLY
        if rain_integration_mode(mission.sort_date_operation.gateway, mission.sort_name) == NEO_ONLY:
            row.sync_status, row.sync_detail = 'NEO_ONLY', 'Google integration is disabled for this sort.'
        elif row.field == 'etd':
            row.sync_status, row.sync_detail = 'NEO_ONLY', 'ETD has no approved Google write range.'
        elif link is None:
            row.sync_status, row.sync_detail = 'NEO_ONLY', 'No verified Google mission link; no Google read or write.'
        else:
            destination = certified_write(mission, row.field, row.value)
            row.sync_status, row.sync_detail = 'SYNCED', destination
    except GoogleWriteUncertain as exc:
        row.sync_status, row.sync_detail = 'UNCERTAIN', str(exc)[:300]
    except GoogleOverrideBlocked as exc:
        row.sync_status, row.sync_detail = 'NOT_WRITTEN', str(exc)[:300]
    except Exception:
        row.sync_status, row.sync_detail = 'FAILED', 'Google attempt failed. Neo remains saved. No automatic retry.'
    db.session.add(NeoRainOverrideAudit(override_id=row.id, generation=row.generation,
        action='SYNC', actor_user_id=row.modified_by_user_id, after_value=row.value,
        detail=row.sync_status + ': ' + (row.sync_detail or '')))
    db.session.commit()


def refresh_reset(override_id, generation):
    """Resume upstream without stale write replay. Readers never call writers."""
    row = db.session.get(NeoRainMissionOverride, override_id)
    if row is None:
        return
    mission = SortDateMission.query.filter_by(id=row.sort_date_mission_id).populate_existing().with_for_update().one()
    row = NeoRainMissionOverride.query.filter_by(id=override_id).populate_existing().with_for_update().one()
    if row.active or row.generation != generation:
        db.session.rollback()
        return
    link = SortDateGoogleMissionLink.query.filter_by(sort_date_mission_id=mission.id,
        sort_date_operation_id=row.sort_date_operation_id).first()
    reader = current_app.config.get('NEORAIN_FRESH_UPSTREAM_READER') or read_fresh_upstream
    if link and reader:
        try:
            fresh = reader(mission, row.field)
            with operator_projection(db.session):
                for attr in FIELDS[row.field][1]:
                    setattr(mission, attr, unpack(attr, fresh.get(attr)))
                row.upstream = {key:pack(value) for key,value in fresh.items()}
                row.sync_detail = 'Fresh upstream value read; no Google write.'
                db.session.flush()
        except Exception:
            row.sync_detail = 'Upstream read unavailable. Regular Google polling resumes; no write or retry.'
            # Never restore a deleted Neo value from stale cached upstream data.
            # STD/Master remains available for planned baselines; physical times
            # stay unknown until a fresh matching upstream fact arrives.
            if row.field not in ('sta', 'no_return'):
                with operator_projection(db.session):
                    for attr in FIELDS[row.field][1]:
                        setattr(mission, attr, 'unknown' if attr.endswith('_source') else None)
                    db.session.flush()
    db.session.commit()


def read_fresh_upstream(mission, field):
    """Bounded existing readers, exact operation date and matching flight/tail.

    This is read-only even for uncertified write ranges. Neo-only missions never
    reach it (refresh_reset checks the existing canonical Google link first).
    """
    from app.services.google_motherbrain_sheets import read_google_motherbrain_envelope, read_google_motherbrain_live_rows
    from app.services.google_motherbrain_live_missions import _parse_optional_live_datetime
    from app.services.google_rain_sheets import read_google_rain_outbound_milestones
    operation = mission.sort_date_operation
    if field == 'etd':
        return {'eta_datetime_utc':None, 'eta_source':'unknown'}  # Normal estimated/planned fallback.
    envelope = read_google_motherbrain_envelope()
    if envelope['gateway_code'] != mission.gateway_code or envelope['sort_name'].lower() != mission.sort_name.lower() or envelope['sort_date'] != mission.sort_date.isoformat():
        raise GoogleOverrideBlocked('Upstream operational sort/date mismatch.')
    live = read_google_motherbrain_live_rows()
    direction = 'inbound' if mission.mission_type == 'arrival' else 'outbound'
    matches = [r for r in live[direction+'_rows'] if flight_identity(r.get('P')) == flight_identity(mission.flight_number)]
    if len(matches) != 1:
        raise GoogleOverrideBlocked('Fresh upstream flight match is missing or ambiguous.')
    matched = matches[0]
    place = mission.origin if mission.mission_type == 'arrival' else mission.destination
    if matched.get('Q','').upper() != (mission.assigned_tail_number or '').upper() or matched.get('R','').upper() != (place or '').upper():
        raise GoogleOverrideBlocked('Fresh upstream tail/route mismatch.')
    if mission.mission_type == 'arrival':
        raw = matched.get('T') if field == 'sta' else matched.get('U')
        if field == 'block_in' and matched.get('W','').upper() not in ('ARRIVED','UNLOADED'):
            raw = None  # ETA is not an actual Block-In.
        local, utc = _parse_optional_live_datetime(raw, operation, field.upper())
        attrs = FIELDS[field][1]
        return {attrs[0]:utc, **({'planned_datetime_local':local} if field == 'sta' else {}), attrs[-1]:'google_motherbrain' if utc else 'unknown'}
    rows = read_google_rain_outbound_milestones()
    matches = [r for r in rows if flight_identity(r.get('flight_number')) == flight_identity(mission.flight_number)]
    if len(matches) != 1 or matches[0]['destination'].upper() != (mission.destination or '').upper():
        raise GoogleOverrideBlocked('Fresh Rain row identity is missing or ambiguous.')
    row = matches[0]
    from app.services.google_rain_live_milestones import _google_checkbox_state, _missing_no_return_prerequisites
    if field == 'included':
        value = _google_checkbox_state(row.get('removed'))
        if value is None:
            raise GoogleOverrideBlocked('Upstream inclusion is unavailable.')
        return {'late_metrics_included_override':not value}
    if field == 'no_return':
        value = _google_checkbox_state(row.get('no_return'))
        if value is None or (value and _missing_no_return_prerequisites(mission)):
            raise GoogleOverrideBlocked('Upstream No Return lacks valid prerequisites.')
        return {'departure_status':'departed' if value else ('blocked_out' if mission.actual_block_out_datetime_utc else 'scheduled'), 'departure_status_source':'google_rain'}
    key = 'block' if field == 'official_block_out' else field
    _local, utc = _parse_optional_live_datetime(row.get(key), operation, field.upper())
    attrs = FIELDS[field][1]
    return {attrs[0]:utc, attrs[-1]:'google_rain' if utc else 'unknown'}
