"""Read-only, tail-owned actual spray reporting. Never infer from current plans."""
from hashlib import sha256
from sqlalchemy import or_,and_,func
from app.models import (NeoSubZeroSprayRecord, NeoSubZeroDepartureDeiceEvent,
                        NeoSubZeroPretreatState, NeoSubZeroAuditEntry,SortDateOperation,Gateway)
from app.services.neosubzero_evidence import evidence_values
from app.services.time_display import format_local_hhmm

SURFACES = {'wings_only': 'WINGS ONLY', 'wings_tail': 'WINGS + TAIL', 'entire_aircraft': 'ENTIRE AIRCRAFT'}


def gateway_operations(gateway):
    """Legacy canonical sorts may have code evidence without the optional gateway FK."""
    return SortDateOperation.query.filter(or_(SortDateOperation.gateway_id==gateway.id,
        and_(SortDateOperation.gateway_id.is_(None),func.upper(SortDateOperation.gateway_code)==gateway.code.upper())))


def operation_gateway_id(op):
    return op.gateway_id or Gateway.query.filter(func.upper(Gateway.code)==op.gateway_code.upper()).one().id


def actual_passes(operation, *, activity='all', tail=''):
    """One stable pass identity, with zero to four separately attributable trucks."""
    if operation is None:
        return []
    records = NeoSubZeroSprayRecord.query.filter_by(sort_date_operation_id=operation.id).all()
    audits = NeoSubZeroAuditEntry.query.filter_by(sort_date_operation_id=operation.id).order_by(
        NeoSubZeroAuditEntry.recorded_at_utc, NeoSubZeroAuditEntry.id).all()
    result = []
    for kind, model in (('departure', NeoSubZeroDepartureDeiceEvent), ('pretreat', NeoSubZeroPretreatState)):
        if activity not in ('all', kind):
            continue
        for parent in model.query.filter_by(sort_date_operation_id=operation.id).all():
            for number in (1, 2):
                start = getattr(parent, f'pass{number}_started_at_utc')
                end = getattr(parent, f'pass{number}_ended_at_utc')
                if not start:
                    continue  # CLEAR/NEGATIVE/FIN labels alone are not spray evidence.
                items = [r for r in records if r.pass_number == number and
                         (r.pretreat_state_id == parent.id if kind == 'pretreat' else r.departure_deice_event_id == parent.id)
                         and r.started_at_utc and r.ended_at_utc and (not r.excluded_at_utc or r.gallons is not None)]
                items.sort(key=lambda r: (r.position_number, r.id))
                evidence = (parent.pass_evidence or {}).get(str(number), {})
                first = items[0] if items else None
                physical_tail = evidence.get('tail') or getattr(first, 'tail_number_snapshot', None) or parent.tail_number
                if tail and tail.casefold() not in str(physical_tail).casefold():
                    continue
                def frozen(key, column):
                    return evidence.get(key) if key in evidence else getattr(first, column, None)
                pass_type = frozen('pass_type', 'pass_type')
                surface = frozen('surface', 'surface_area')
                # For pre-snapshot passes, show missing evidence, never today's product/crew/weather.
                reliable_start_context=evidence.get('context_capture')=='with_start'
                row = dict(id=f'{kind}:{parent.id}:pass:{number}', activity=kind, parent=parent,
                    pass_number=number, operation=operation, tail=physical_tail,
                    flight=frozen('flight', 'flight_number_snapshot') or getattr(parent, 'flight_number_snapshot', None),
                    original_mission_id=evidence.get('original_mission_id') or getattr(parent, 'original_mission_id', None) or getattr(parent, 'sort_date_mission_id', None),
                    type={'type_i': 'TYPE I', 'type_iv': 'TYPE IV'}.get(pass_type),
                    fluid=frozen('fluid', 'fluid_name_snapshot'), concentration=frozen('concentration', 'concentration_percent_snapshot'),
                    surface=SURFACES.get(surface), ramp=frozen('ramp', 'ramp_snapshot'), location=frozen('parking', 'parking_snapshot'),
                    reason=frozen('reason', 'reason_for_application'), temperature=frozen('ambient_temperature', 'ambient_temperature') if reliable_start_context else None,
                    dew_point=frozen('dew_point', 'dew_point') if reliable_start_context else None, precipitation=frozen('active_precipitation', 'active_precipitation') if reliable_start_context else None,
                    notes=frozen('notes','notes'),weather_provenance=evidence.get('context_capture', 'legacy / pass-start timing unverified'),
                    start_utc=start, end_utc=end,
                    start=format_local_hhmm(start, getattr(first, 'timezone_snapshot', None) or getattr(parent, 'timezone', None)),
                    end=format_local_hhmm(end, getattr(first, 'timezone_snapshot', None) or getattr(parent, 'timezone', None)) or 'NOT RECORDED',
                    records=items, missing_gallons=sum(r.gallons is None for r in items),
                    audits=[a for a in audits if a.spray_record_id in {r.id for r in items} or
                            (a.tail_number == physical_tail and a.event_id == (parent.id if kind == 'departure' else None))])
                row['weather_label']={'with_start':'Operator context captured with START',
                    'with_end':'Recorded with END; pass-start weather unverified',
                    'start_not_snapshotted':'START weather not captured'}.get(row['weather_provenance'],row['weather_provenance'])
                row['copy_text'] = pass_text(row)
                result.append(row)
    return sorted(result, key=lambda r: (r['start_utc'], r['tail'], r['activity'], r['pass_number'], r['id']))


def shown(value):
    return 'NOT RECORDED' if value is None or value == '' else str(value)


def pass_text(row):
    op = row['operation']
    lines = [f"{op.gateway_code} | {op.sort_date} | {op.sort_name.upper()} | {row['activity'].upper()}",
        f"{shown(row['flight'])} | {row['tail']} | PASS {row['pass_number']} | {shown(row['ramp'])} / {shown(row['location'])}",
        f"{shown(row['type'])} | {shown(row['fluid'])} | {shown(row['concentration'])}% | {shown(row['surface'])}",
        f"START {row['start']} | END {row['end']} | UTC {row['start_utc'].isoformat()}–{row['end_utc'].isoformat() if row['end_utc'] else 'NOT RECORDED'}",
        f"TEMP {shown(row['temperature'])} | DEW {shown(row['dew_point'])} | PRECIP {shown(row['precipitation'])}",
        f"REASON {shown(row['reason'])} | WEATHER EVIDENCE {row['weather_label']}"]
    if row['notes']: lines.append('NOTES '+row['notes'])
    for record in row['records']:
        lines.append(f"TRUCK {record.truck_number_snapshot} | DRIVER {shown(record.driver_name_snapshot)} | FLYER {shown(record.flyer_name_snapshot)} | GALLONS {record.gallons if record.gallons is not None else 'NOT ENTERED'} | RECORD {record.id}")
    if not row['records']:
        lines.append('PARTICIPATION NOT RECORDED — COORDINATOR REVIEW')
    lines.append(f"SOURCE {row['id']} | ORIGINAL MISSION {shown(row['original_mission_id'])}")
    return '\n'.join(lines)


def report_text(rows):
    return '\n\n'.join(row['copy_text'] for row in rows) or 'No actual spray applications recorded.'


def report_version(operation, rows):
    audit=NeoSubZeroAuditEntry.query.filter_by(sort_date_operation_id=operation.id).order_by(NeoSubZeroAuditEntry.id.desc()).first() if operation else None
    return sha256(f"{getattr(operation,'id',None)}:{getattr(audit,'id',0)}:{report_text(rows)}".encode()).hexdigest()


def archive_payload(row):
    """Versioned operational evidence, separate from any frost training label."""
    op = row['operation']
    fields = ('id', 'activity', 'tail', 'flight', 'original_mission_id', 'pass_number', 'type', 'fluid',
              'concentration', 'surface', 'ramp', 'location', 'reason', 'temperature', 'dew_point',
              'precipitation', 'weather_provenance', 'notes')
    return dict(schema_version='subzero-spray/v1', record_type='actual_fluid_application',
        gateway_id=operation_gateway_id(op), gateway=op.gateway_code, operation_id=op.id,
        sort_date=op.sort_date.isoformat(), sort_name=op.sort_name,
        **{key: row[key] for key in fields}, start_utc=row['start_utc'].isoformat(), end_utc=row['end_utc'].isoformat() if row['end_utc'] else None,
        revision_utc=max([row['parent'].updated_at]+[r.updated_at for r in row['records']]).isoformat(),
        participants=[evidence_values(r) for r in row['records']],
        audit_provenance=[evidence_values(a) for a in row['audits']])


def learning_context(payload):
    """Treatment context only. No spray-derived positive or negative frost label."""
    return dict(schema_version='neofrost-treatment-context/v1', record_type='unlabeled_treatment_context',
        source_id=payload['id'], operation_id=payload['operation_id'], tail=payload['tail'],
        treatment=payload, frost_observed=None, training_eligible=False,
        exclusion='Spray application is not an explicit frost observation.')
