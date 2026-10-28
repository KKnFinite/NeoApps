"""Optional physical inspections in the existing append-only SubZero audit/outbox.

No spray/state transitions, network requests, surface-temperature guesses or
model-derived labels. Physical observations and treatment-reason targets differ.
"""
from datetime import datetime, timezone, timedelta
from uuid import UUID, uuid4
from sqlalchemy import select, func, case
from app.extensions import db
from app.models import (NeoSubZeroAuditEntry, NeoSubZeroPretreatState,
    SortDateParkingAssignment, SortDateMission, SortDateOperation)
from app.services.neosubzero_evidence import audit_change, normalized_tail, NeoSubZeroEvidenceError
from app.services import frost_analysis, frost_vault

VERSION = 'frost-physical-inspection-v1'
ACTION = 'frost_physical_inspection'


def audits(operation, tail=None):
    query = NeoSubZeroAuditEntry.query.filter_by(sort_date_operation_id=operation.id, action=ACTION)
    if tail: query = query.filter_by(tail_number=normalized_tail(tail))
    return query.order_by(NeoSubZeroAuditEntry.id).all()


def cached_weather(observed):
    """Reference only already acquired, preceding KRFD evidence; never fetch."""
    from app.services import neosubzero_weather as weather
    with weather._CACHE_LOCK:
        entry = weather._CACHE.get('krfd_metar')
        rows = entry.get('value') if entry else None
        row = dict(rows[0]) if isinstance(rows, list) and rows and isinstance(rows[0], dict) else None
        fetched = entry.get('fetched_at') if entry else None
    if not row or row.get('icaoId') != 'KRFD' or not row.get('rawOb'): return None
    if len(frost_vault.canonical(row)) > 16384: return None
    at = weather._parse_datetime(row.get('reportTime'))
    if at is None or not timedelta(0) <= observed-at <= timedelta(minutes=90): return None
    # Preserve the provider row/raw METAR as a small snapshot, not an invented
    # reference to a provider object that may not exist tomorrow.
    return dict(station='KRFD', provider='AviationWeather METAR shared cache',
        observed_at_utc=at.isoformat(), retrieved_at_utc=fetched.isoformat() if fetched else None,
        raw_metar=row['rawOb'], source_sha256=frost_vault.digest(frost_vault.canonical(row)),
        hash_contract='canonical provider row JSON, not original HTTP wire bytes',source=row)


def record(operation, mission, values, *, actor_id):
    # Serialize with canonical tail swaps/current-sort operational changes.
    SortDateOperation.query.filter_by(id=operation.id).with_for_update().one()
    mission = SortDateMission.query.filter_by(id=mission.id, sort_date_operation_id=operation.id, mission_type='departure').populate_existing().with_for_update().one()
    tail = normalized_tail(values.get('physical_tail'))
    if not actor_id or not tail or tail != normalized_tail(mission.assigned_tail_number):
        raise NeoSubZeroEvidenceError('Aircraft identity changed. Keep your draft and reload the actual tail.')
    if mission.departure_status == 'cancelled': raise NeoSubZeroEvidenceError('Cancelled aircraft cannot establish a current inspection. Review the mission first.')
    try: nonce = str(UUID(str(values.get('inspection_request_id', ''))))
    except ValueError: raise NeoSubZeroEvidenceError('Inspection request expired. Reload current values; your draft is kept.')
    result = values.get('frost_result')
    if result not in ('present', 'absent', 'uncertain') or values.get('inspection_confirmed') != 'yes':
        raise NeoSubZeroEvidenceError('Choose the actual inspection result and confirm that you inspected this physical tail. Predictions are not inspections.')
    text = str(values.get('inspected_local') or '')
    instant, quality = frost_analysis.local_instant(text[:10], text[11:]) if len(text) == 16 and text[10] == 'T' else (None, 'invalid_clock')
    if instant is None: raise NeoSubZeroEvidenceError('Enter an unambiguous actual Central calendar date/time (DST folds/gaps require review).')
    observed = datetime.fromisoformat(instant); now = datetime.now(timezone.utc)
    if observed > now: raise NeoSubZeroEvidenceError('Inspection time cannot be in the future.')
    # This is current-sort evidence, not an unaudited historical backfill form.
    if abs((observed.astimezone(frost_analysis.ZONE).date()-operation.sort_date).days) > 1:
        raise NeoSubZeroEvidenceError('Inspection must belong to the current operational sort dates.')
    location = str(values.get('inspection_location') or '').strip()
    notes = str(values.get('inspection_notes') or '').strip()
    reason = str(values.get('correction_reason') or '').strip()
    if not location or len(location) > 120 or len(notes) > 500 or len(reason) > 500:
        raise NeoSubZeroEvidenceError('Enter the actual inspection location (120 characters); notes/correction reason may use 500 characters.')
    request_body = dict(tail=tail, result=result, instant=instant, location=location, notes=notes,
        verify_parking=values.get('verify_parking') == 'yes', correction=values.get('inspection_action') == 'correct',
        expected=str(values.get('expected_inspection_id') or ''), reason=reason)
    digest = frost_vault.digest(frost_vault.canonical(request_body))
    entries = audits(operation, tail)
    for entry in entries:
        prior = entry.after_values or {}
        if prior.get('request_id') == nonce:
            if prior.get('request_sha256') != digest or entry.actor_user_id != actor_id:
                raise NeoSubZeroEvidenceError('This inspection request was already used with different values. Reload current values.')
            return entry  # Exactly-once retry after a lost response.
    latest = entries[-1] if entries else None
    if str(latest.id if latest else '') != request_body['expected']:
        raise NeoSubZeroEvidenceError('Another inspection was saved. Your draft is kept; reload current values before resolving it.')
    if request_body['correction'] and (latest is None or not reason):
        raise NeoSubZeroEvidenceError('Choose an existing inspection and give a correction reason.')
    previous = (latest.after_values or {}).get('inspection') if latest else None
    if not request_body['correction'] and mission.tail_updated_at and mission.tail_updated_at.replace(tzinfo=timezone.utc)>observed:
        raise NeoSubZeroEvidenceError('The mission tail changed after this inspection time. Current assignment cannot prove historical mission context; review the actual tail/time.')
    parking = SortDateParkingAssignment.query.filter_by(sort_date_operation_id=operation.id, tail_number=tail).one_or_none()
    block = mission.actual_block_out_datetime_utc
    block = block if block and block.replace(tzinfo=timezone.utc)<=observed and (not mission.tail_updated_at or mission.tail_updated_at<=block) else None
    verified = None
    if request_body['verify_parking']:
        if not parking or not parking.updated_at or parking.updated_at.replace(tzinfo=timezone.utc) > observed:
            raise NeoSubZeroEvidenceError('The current parking record does not establish that historical stand. Leave parking unverified or resolve the canonical assignment.')
        if block and parking.updated_at<=block:
            raise NeoSubZeroEvidenceError('The aircraft blocked out after that parking assignment. Record its actual inspection location; leave the old stand unverified.')
        verified = dict(ramp=parking.ramp_code, position=parking.position_code,
            assignment_updated_at_utc=parking.updated_at.replace(tzinfo=timezone.utc).isoformat(),
            basis='observer explicitly verified canonical stand at inspection; duration unknown')
    pretreat = NeoSubZeroPretreatState.query.filter_by(sort_date_operation_id=operation.id, tail_number=tail).first()
    starts = [getattr(pretreat, key) for key in ('pass1_started_at_utc', 'pass2_started_at_utc') if pretreat and getattr(pretreat, key)]
    prior_pretreat = True if any(at.replace(tzinfo=timezone.utc) <= observed for at in starts) else None
    arrivals = SortDateMission.query.filter_by(sort_date_operation_id=operation.id, mission_type='arrival', assigned_tail_number=tail).all()
    arrivals = [r for r in arrivals if r.arrival_status != 'cancelled' and r.actual_block_in_datetime_utc and
        r.actual_block_in_datetime_utc.replace(tzinfo=timezone.utc) <= observed and
        (not r.tail_updated_at or r.tail_updated_at <= r.actual_block_in_datetime_utc)]
    arrival = max(arrivals, key=lambda r:r.actual_block_in_datetime_utc) if arrivals else None
    # An explicit inspection establishes aircraft presence, but NOT its exposure
    # duration or an absence of treatment outside the known canonical records.
    payload = dict(schema_version=VERSION, record_type='direct_aircraft_frost_inspection',
        operation_id=operation.id, sort_date=operation.sort_date.isoformat(), sort_name=operation.sort_name,
        tail=tail, original_mission_id=previous['original_mission_id'] if request_body['correction'] else mission.id,
        original_flight=previous['original_flight'] if request_body['correction'] else mission.flight_number,
        result=result, observed_at_utc=instant, time_quality=quality, location=location,
        observer_user_id=previous['observer_user_id'] if request_body['correction'] else actor_id,
        recorded_by_user_id=actor_id, source='explicit direct visual inspection attestation',
        notes=notes, parking=verified, prior_pretreat=prior_pretreat,
        prior_pretreat_evidence=dict(state_id=pretreat.id,
            preceding_start_times_utc=[at.replace(tzinfo=timezone.utc).isoformat() for at in starts if at.replace(tzinfo=timezone.utc)<=observed]) if prior_pretreat else None,
        pretreat_scope='recorded actual applications preceding inspection in this sort; other treatment unknown',
        block_in_at_utc=arrival.actual_block_in_datetime_utc.replace(tzinfo=timezone.utc).isoformat() if arrival else None,
        arrival_mission_id=arrival.id if arrival else None, block_in_source=arrival.actual_block_in_source if arrival else None,
        block_out_at_utc=block.replace(tzinfo=timezone.utc).isoformat() if block else None,
        block_out_source=mission.actual_block_out_source if block else None,
        exposure_hours=(observed-arrival.actual_block_in_datetime_utc.replace(tzinfo=timezone.utc)).total_seconds()/3600 if arrival else None,
        exposure_basis='same-tail canonical Block-In proxy; actual surface exposure unknown' if arrival else None,
        weather=cached_weather(observed), evidence_strength='direct_physical_inspection' if result != 'uncertain' else 'explicit_uncertainty',
        physical_frost_confirmed={'present':True, 'absent':False, 'uncertain':None}[result],
        physical_label={'present':'OBSERVED FROST PRESENT', 'absent':'OBSERVED FROST ABSENT', 'uncertain':'UNKNOWN'}[result],
        treatment_reason_target_eligible=False, learning_eligible=result != 'uncertain')
    entry = audit_change(operation, ACTION, actor_id=actor_id, mission=mission, tail=tail, reason=reason,
        before=dict(previous_audit_id=latest.id, inspection=previous) if request_body['correction'] else None,
        after={})
    db.session.flush()
    root = (latest.after_values or {})['root_audit_id'] if request_body['correction'] else entry.id
    entry.after_values = dict(inspection=payload, root_audit_id=root, request_id=nonce, request_sha256=digest)
    return entry


def payloads(operation):
    groups = {}
    for entry in audits(operation):
        value = entry.after_values or {}
        if value.get('inspection', {}).get('schema_version') == VERSION:
            groups.setdefault(value['root_audit_id'], []).append(entry)
    # One bounded immutable source per audit revision. Even corrections made
    # before the first delivery retain every original snapshot in the Vault.
    return {f'frost:inspection:{entry.id}:0':dict(entry.after_values['inspection'],
        id=f'frost:inspection:{entry.id}:0', root_audit_id=root, source_audit_id=entry.id,
        evidence_active=entry.id == entries[-1].id,
        superseded_by_audit_id=entries[-1].id if entry.id != entries[-1].id else None,
        correction_reason=entry.reason, recorded_at_utc=entry.recorded_at_utc.replace(tzinfo=timezone.utc).isoformat(),
        previous_audit_id=(entry.before_values or {}).get('previous_audit_id'))
        for root, entries in groups.items() for entry in entries}


def decorate(operation, rows):
    latest = {}
    for entry in audits(operation): latest[entry.tail_number] = entry
    for row in rows:
        entry = latest.get(row['tail'])
        last = (entry.after_values or {}).get('inspection') if entry else None
        row['frost_inspection'] = dict(expected=str(entry.id) if entry else '', nonce=str(uuid4()),
            correction_nonce=str(uuid4()), last=last,
            last_time=datetime.fromisoformat(last['observed_at_utc']).astimezone(frost_analysis.ZONE).strftime('%Y-%m-%d %H:%M %Z') if last else None)


def population(gateway):
    """SQL-only aggregate; never load archived observations/weather on Render."""
    from app.services.neosubzero_reporting import gateway_operations
    audit=NeoSubZeroAuditEntry
    latest=select(func.max(audit.id)).where(audit.action==ACTION,
        audit.sort_date_operation_id.in_(gateway_operations(gateway).with_entities(SortDateOperation.id).statement)).group_by(
            audit.after_values['root_audit_id'].as_integer())
    result=audit.after_values['inspection']['result'].as_string()
    pretreat=audit.after_values['inspection']['prior_pretreat'].as_boolean()
    row=db.session.query(func.count(audit.id),
        *[func.sum(case((result==kind,1),else_=0)) for kind in ('present','absent','uncertain')],
        func.sum(case((pretreat.is_(True),1),else_=0)),func.max(audit.recorded_at_utc)).filter(audit.id.in_(latest)).one()
    return dict(inspections=row[0],physical_present=int(row[1] or 0),physical_absent=int(row[2] or 0),
        uncertain=int(row[3] or 0),prior_pretreated=int(row[4] or 0),
        last_recorded_at_utc=row[5].replace(tzinfo=timezone.utc).isoformat() if row[5] else None,
        physical_model_ready=False,limitation='Recorded inspections, not a representative exposure cohort. Pretreatment and unknown exposure remain confounders; forecast pairing and independent validation required.')
