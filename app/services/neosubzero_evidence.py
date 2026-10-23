"""Tail ownership, shared context and append-only evidence for existing records."""
from datetime import datetime
from decimal import Decimal
from sqlalchemy import func
from app.extensions import db
from app.models import NeoSubZeroDepartureDeiceEvent, NeoSubZeroOperationContext, NeoSubZeroAuditEntry
from app.services.live_collaboration import entity_version, version_conflict


class NeoSubZeroEvidenceError(ValueError):
    pass


def normalized_tail(value):
    return str(value or '').strip().upper()


def select_tail_event(events):
    active=[event for event in events if event.status != 'cleared']
    if len(active)>1:
        return None,'Multiple active departure records belong to this tail. Explicit coordinator reconciliation is required.'
    if active: return active[0],None
    return max(events,key=lambda event:(event.cleared_at_utc is not None,event.cleared_at_utc or datetime.min,event.id or 0),default=None),None


def departure_event_for_tail(operation, mission, *, lock=False):
    if operation is None or mission is None: return None
    query=NeoSubZeroDepartureDeiceEvent.query.filter(
        NeoSubZeroDepartureDeiceEvent.sort_date_operation_id==operation.id,
        func.upper(func.trim(NeoSubZeroDepartureDeiceEvent.tail_number))==normalized_tail(mission.assigned_tail_number))
    if lock: query=query.with_for_update()
    event,review=select_tail_event(query.all())
    if review: raise NeoSubZeroEvidenceError(review)
    return event


def evidence_values(entity):
    def value(item):
        if isinstance(item,datetime): return item.isoformat()
        if isinstance(item,Decimal): return str(item)
        return item
    return {column.name:value(getattr(entity,column.name)) for column in entity.__table__.columns} if entity is not None else None


def audit_change(operation, action, *, actor_id=None, mission=None, event=None, record=None, tail=None, reason=None, before=None, after=None):
    if len(str(reason or '').strip())>500:
        raise NeoSubZeroEvidenceError('Correction reason must be 500 characters or fewer.')
    db.session.flush()
    entry=NeoSubZeroAuditEntry(sort_date_operation_id=operation.id,mission_id=getattr(mission,'id',None),
        tail_number=normalized_tail(tail or getattr(event,'tail_number',None) or getattr(record,'tail_number_snapshot',None)) or None,
        event_id=getattr(event,'id',None),spray_record_id=getattr(record,'id',None),actor_user_id=actor_id,
        action=action,reason=str(reason or '').strip()[:500] or None,before_values=before,after_values=after)
    db.session.add(entry)
    return entry


def application_context(operation):
    row=NeoSubZeroOperationContext.query.filter_by(sort_date_operation_id=operation.id).one_or_none() if operation else None
    return {**{key:getattr(row,key,None) or '' for key in ('reason_for_application','active_precipitation','ambient_temperature','dew_point','notes')},'version':entity_version(row)}


def save_application_context(operation, values, *, actor_id):
    row=NeoSubZeroOperationContext.query.filter_by(sort_date_operation_id=operation.id).with_for_update().one_or_none()
    expected=str(values.get('expected_version') or '').strip()
    if (row and (not expected or version_conflict(row,expected))) or (row is None and expected):
        raise NeoSubZeroEvidenceError('Application Context changed. Your draft is kept; reload current values before saving.')
    before=evidence_values(row)
    if row is None:
        row=NeoSubZeroOperationContext(sort_date_operation_id=operation.id);db.session.add(row)
    for key,maximum in (('reason_for_application',120),('active_precipitation',120),('ambient_temperature',32),('dew_point',32),('notes',2000)):
        value=str(values.get(key) or '').strip()
        if len(value)>maximum: raise NeoSubZeroEvidenceError(key.replace('_',' ').title()+' is too long.')
        setattr(row,key,value or None)
    row.updated_by_user_id=actor_id
    audit_change(operation,'application_context',actor_id=actor_id,before=before,after=evidence_values(row))
    return row
