"""NeoSubZero current-sort trucks, spray gallons, and Deice Log projections."""

from decimal import Decimal, InvalidOperation

from sqlalchemy import func
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models import (
    NeoSubZeroDepartureDeiceEvent,
    NeoSubZeroSprayRecord,
    NeoSubZeroUccAssignment,
    NeoSubZeroUccTruckAssignment,
    StaffingPerson,
)
from app.services.live_collaboration import entity_version
from app.services.neosubzero_constants import RAMP_ORDER
from app.services.neosubzero_staffing import current_subzero_staffing_pool
from app.services.time_display import format_local_hhmm


DEICER_REFRESH_KEY = "neosubzero.deicer_mobile"
DEFAULT_DEPARTURE_REASON = "Frost"


class NeoSubZeroSprayError(ValueError):
    """Safe operator-facing truck/gallons validation error."""


def departure_deice_reason(event):
    """Return the event-scoped explicit reason or the departure default."""
    if event is None:
        return ""
    return str(event.reason_for_application or "").strip() or DEFAULT_DEPARTURE_REASON


def set_departure_deice_reason(event, reason):
    """Stage one departure event's explicit reason without committing."""
    if event is None:
        raise NeoSubZeroSprayError("Choose a current departure-deice event.")
    normalized = _short_text(reason, "Reason for Application", 120)
    event.reason_for_application = normalized or None
    return event


def set_neosubzero_ucc_truck(
    operation,
    ramp,
    position_number,
    truck_number,
    *,
    user_id=None,
    assignment=None,
):
    """Stage the current truck for one ramp position without committing."""
    ramp, position = _slot(ramp, position_number)
    if operation is None:
        raise NeoSubZeroSprayError("No current sort is available.")
    truck = _short_text(truck_number, "Truck number", 32)
    from app.models import SortDateOperation
    from app.services.neosubzero_ucc import validate_ucc_truck_unique
    SortDateOperation.query.filter_by(id=operation.id).with_for_update().one()
    validate_ucc_truck_unique(operation,truck,assignment)
    if assignment is not None and (
        assignment.sort_date_operation_id != operation.id
        or assignment.ramp != ramp
        or assignment.position_number != position
    ):
        raise NeoSubZeroSprayError("Truck assignment does not match this position.")
    if assignment is None:
        if not truck:
            return None
        assignment = NeoSubZeroUccTruckAssignment(
            sort_date_operation_id=operation.id,
            ramp=ramp,
            position_number=position,
        )
        db.session.add(assignment)
    assignment.truck_number = truck or None
    assignment.updated_by_user_id = user_id
    return assignment


def decorate_departure_rows_with_spray(operation, rows, *, kind='departure'):
    """Combine current shared staffing with independently frozen participation."""
    from app.services.neosubzero_participation import pass_types
    from app.services.neosubzero_evidence import application_context
    if operation is None or not rows: return rows
    shared_context=application_context(operation)
    staffing={(r.ramp,r.position_number,r.team_role):r for r in NeoSubZeroUccAssignment.query.options(joinedload(NeoSubZeroUccAssignment.person)).filter_by(sort_date_operation_id=operation.id).all()}
    trucks={(r.ramp,r.position_number):r for r in NeoSubZeroUccTruckAssignment.query.filter_by(sort_date_operation_id=operation.id).all()}
    records=NeoSubZeroSprayRecord.query.filter_by(sort_date_operation_id=operation.id).all()
    for row in rows:
        parent=row.get('state') if kind=='pretreat' else row.get('event')
        ramp=row.get('ramp')
        owners=[]
        for slot in range(1,5):
            driver,flyer=staffing.get((ramp,slot,'driver')),staffing.get((ramp,slot,'flyer'))
            truck=trucks.get((ramp,slot))
            owners.append(dict(position=slot,driver=getattr(driver,'person',None),flyer=getattr(flyer,'person',None),
                truck=truck,truck_number=getattr(truck,'truck_number',None) or '',truck_version=entity_version(truck)))
        row['spray_positions']=tuple(owners)
        if kind=='departure':
            row['reason_for_application']=getattr(parent,'reason_for_application',None) or shared_context['reason_for_application'] or (DEFAULT_DEPARTURE_REASON if parent else '')
        passes=[]
        for number,type_name in enumerate(pass_types(parent,kind) if parent else (),1):
            saved=[r for r in records if (r.pretreat_state_id==parent.id if kind=='pretreat' else r.departure_deice_event_id==parent.id) and r.pass_number==number]
            positions=[]
            for record in sorted(saved,key=lambda r:r.position_number):
                positions.append(dict(position=record.position_number,record=record,record_version=entity_version(record),
                    driver=record.driver_person,flyer=record.flyer_person,driver_name=record.driver_name_snapshot,
                    flyer_name=record.flyer_name_snapshot,truck_number=record.truck_number_snapshot,
                    recorded_truck=record.truck_number_snapshot,gallons=_decimal_display(record.gallons),
                    official=bool(record.official_at_utc or record.gallons),excluded=bool(record.excluded_at_utc),
                    selected=bool(record.participation_selected)))
            end=getattr(parent,f'pass{number}_ended_at_utc')
            passes.append(dict(number=number,type=type_name,surface=getattr(parent,f'pass{number}_surface_area'),
                start=row.get(f'pass{number}_start'),end=row.get(f'pass{number}_end'),positions=tuple(positions),
                implicit=tuple(owner for owner in owners if owner['truck_number']) if not saved and not end else (),
                participation_review=bool(end and not any(p['official'] and (not p['excluded'] or p['gallons']) for p in positions))))
        row['application_passes']=tuple(passes)
        row['participation_kind']=kind
    return rows


def set_neosubzero_spray_gallons(operation, mission, event, pass_number, position_number, gallons, *,
    fluid_settings=None, application_context=None, user_id=None, record=None, correction_reason=None):
    """Change only a documented participant's measurement; never resnapshot crew."""
    from app.services.neosubzero_evidence import audit_change,evidence_values
    if operation is None or record is None or record.sort_date_operation_id!=operation.id:
        raise NeoSubZeroSprayError('Record the actual pass participants before entering gallons.')
    if record.pass_number!=int(pass_number) or record.position_number!=int(position_number):
        raise NeoSubZeroSprayError('Spray record does not match this pass participant.')
    if not record.started_at_utc or not record.ended_at_utc or not (record.official_at_utc or record.gallons):
        raise NeoSubZeroSprayError('The coordinator must save this pass END and official participation before gallons entry.')
    if event is None or event.sort_date_operation_id!=operation.id or (record.departure_deice_event_id is not None and record.departure_deice_event_id!=event.id) or (record.pretreat_state_id is not None and record.pretreat_state_id!=event.id) or (record.tail_number_snapshot and record.tail_number_snapshot.strip().upper()!=event.tail_number.strip().upper()):
        raise NeoSubZeroSprayError('Spray record does not match this physical treatment.')
    before=evidence_values(record)
    raw=str(gallons or '').strip()
    if not raw:
        if record.gallons: raise NeoSubZeroSprayError('Blank gallons cannot erase applied fuel. Enter a corrected positive amount.')
        return record
    try: amount=Decimal(raw)
    except InvalidOperation as exc: raise NeoSubZeroSprayError('Gallons must be a positive number.') from exc
    if not amount.is_finite() or amount<=0 or amount.as_tuple().exponent < -2 or amount>=Decimal('1000000'):
        raise NeoSubZeroSprayError('Gallons must be positive, below 1,000,000 and have no more than two decimal places.')
    record.gallons=amount;record.recorded_by_user_id=user_id
    if record.excluded_at_utc: raise NeoSubZeroSprayError('This participant was excluded. A coordinator must resolve participation before entering gallons.')
    audit_change(operation,'spray_gallons',actor_id=user_id,mission=mission,
        event=event if record.departure_deice_event_id else None,record=record,
        reason=correction_reason,before=before,after=evidence_values(record))
    return record


def current_user_ucc_assignment(operation, user):
    """Resolve the linked active person's current UCC slot, if any."""
    employee_id = str(getattr(user, "employee_id", None) or "").strip().casefold()
    if operation is None or not employee_id:
        return None
    person = StaffingPerson.query.filter(
        StaffingPerson.active.is_(True),
        func.lower(func.trim(StaffingPerson.employee_id)) == employee_id,
    ).one_or_none()
    if person is None:
        return None
    eligible_ids = {row["person"].id for row in current_subzero_staffing_pool(operation)}
    if person.id not in eligible_ids:
        return None
    return (
        NeoSubZeroUccAssignment.query.options(joinedload(NeoSubZeroUccAssignment.person))
        .filter_by(sort_date_operation_id=operation.id, person_id=person.id)
        .one_or_none()
    )


def user_staffing_person(user):
    employee=str(getattr(user,'employee_id',None) or '').strip().casefold()
    return StaffingPerson.query.filter(StaffingPerson.active.is_(True),func.lower(func.trim(StaffingPerson.employee_id))==employee).one_or_none() if employee else None


def driver_owns_participation(user, record):
    person=user_staffing_person(user)
    return bool(person and record and record.driver_person_id==person.id and record.ended_at_utc
        and (record.official_at_utc or record.gallons) and not record.excluded_at_utc)


def neosubzero_deice_log(operation):
    """Actual physical applications only, including missing-gallon evidence."""
    if operation is None: return ()
    records=NeoSubZeroSprayRecord.query.options(joinedload(NeoSubZeroSprayRecord.mission),
        joinedload(NeoSubZeroSprayRecord.departure_deice_event),joinedload(NeoSubZeroSprayRecord.pretreat_state)).filter_by(sort_date_operation_id=operation.id).all()
    grouped={}
    for record in records:
        if not record.started_at_utc or not record.ended_at_utc or (record.excluded_at_utc and not record.gallons): continue
        parent=record.pretreat_state or record.departure_deice_event
        tail=record.tail_number_snapshot or getattr(parent,'tail_number',None)
        if not tail: continue  # Missing ownership must never be borrowed from a replacement tail.
        key=(record.truck_number_snapshot.casefold(),'pretreat' if record.pretreat_state_id else 'departure',getattr(parent,'id',None),tail)
        grouped.setdefault(key,[]).append(record)
    result=[]
    for (_truck,kind,_event,tail),items in grouped.items():
        items.sort(key=lambda r:(r.started_at_utc,r.pass_number,r.position_number))
        first=items[0];parent=first.pretreat_state or first.departure_deice_event
        operators=list(dict.fromkeys(name for r in items for name in (r.driver_name_snapshot,r.flyer_name_snapshot) if name))
        result.append(dict(truck=first.truck_number_snapshot,tail=tail,
            flight=first.flight_number_snapshot or getattr(parent,'flight_number_snapshot',None) or 'ORIGINAL FLIGHT NOT SNAPSHOTTED',
            original_mission_id=getattr(parent,'sort_date_mission_id',None) or getattr(parent,'original_mission_id',None),
            kind=kind,reason=first.reason_for_application or '—',precipitation=first.active_precipitation or '—',
            ambient_temperature=first.ambient_temperature or '—',dew_point=first.dew_point or '—',notes=first.notes or '',
            operators=tuple(operators),sort_time=first.started_at_utc,
            applications=tuple(dict(pass_number=r.pass_number,type='Type IV' if r.pass_type=='type_iv' else 'Type I',
                fluid=r.fluid_name_snapshot,concentration=r.concentration_percent_snapshot,gallons=_decimal_display(r.gallons) or 'NOT ENTERED',
                surface=(r.surface_area or 'unrecorded').replace('_',' ').title(),
                start=format_local_hhmm(r.started_at_utc,r.timezone_snapshot or r.mission.timezone or None),
                end=format_local_hhmm(r.ended_at_utc,r.timezone_snapshot or r.mission.timezone or None),
                duration=int((r.ended_at_utc-r.started_at_utc).total_seconds()//60),position=r.position_number,record=r,
                driver=r.driver_name_snapshot,flyer=r.flyer_name_snapshot,ramp=r.ramp_snapshot,parking=r.parking_snapshot)
                for r in items)))
    result.sort(key=lambda r:(r['truck'].casefold(),r['sort_time'],r['tail']))
    return tuple(result)


def _mission_ramp_from_rows(operation, mission):
    from app.models import SortDateParkingAssignment
    from app.neonodes.neosubzero.services import _tail
    from app.services.neosubzero_departure_deice import _ramp_name

    tail = _tail(mission.assigned_tail_number)
    parking = SortDateParkingAssignment.query.filter(
        SortDateParkingAssignment.sort_date_operation_id == operation.id,
        func.upper(func.trim(SortDateParkingAssignment.tail_number)) == tail,
    ).one_or_none()
    ramp = _ramp_name(getattr(parking, "ramp_code", None))
    if ramp not in RAMP_ORDER:
        raise NeoSubZeroSprayError("This departure does not have a current UCC ramp.")
    return ramp


def _slot(ramp, position_number):
    normalized_ramp = str(ramp or "").strip().title()
    try:
        position = int(position_number)
    except (TypeError, ValueError) as exc:
        raise NeoSubZeroSprayError("Choose a valid ramp position.") from exc
    if normalized_ramp not in RAMP_ORDER or position not in range(1, 5):
        raise NeoSubZeroSprayError("Choose a valid ramp position.")
    return normalized_ramp, position


def _short_text(value, label, maximum):
    normalized = str(value or "").strip()
    if len(normalized) > maximum:
        raise NeoSubZeroSprayError(f"{label} must be {maximum} characters or fewer.")
    return normalized


def _notes(value):
    normalized = str(value or "").strip()
    if len(normalized) > 2000:
        raise NeoSubZeroSprayError("Notes must be 2000 characters or fewer.")
    return normalized or None


def _decimal_display(value):
    if value is None:
        return ""
    normalized = format(Decimal(value), "f")
    if '.' in normalized: normalized=normalized.rstrip('0').rstrip('.')
    return normalized or "0"
