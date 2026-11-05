"""Optional event-owned observation; official treatment and frozen learning stay separate."""
from datetime import datetime, timedelta
from app.extensions import db
from app.models import NeoSubZeroAuditEntry
from app.services.live_collaboration import version_conflict
from app.services.neosubzero_evidence import NeoSubZeroEvidenceError, audit_change, normalized_tail, application_context
from app.services.neosubzero_spray import DEFAULT_DEPARTURE_REASON
from app.services import frost_analysis

ACTION = 'frost_treatment_observation'
LABEL = 'NO FROST PRESENT — FLIGHT CREW REQUESTED TREATMENT'


def is_frost(reason):
    return frost_analysis.classification(reason)['normalized_reason'] == 'Frost'


def project(entry):
    if entry is None:
        return None
    return dict((entry.after_values or {})['observation'], source_audit_id=entry.id)


def latest(operation, event):
    return NeoSubZeroAuditEntry.query.filter_by(sort_date_operation_id=operation.id,
        event_id=event.id, action=ACTION).order_by(NeoSubZeroAuditEntry.id.desc()).first()


def save(operation, mission, event, values, *, actor_id):
    """Caller holds the existing operation/mission/event locks; never commits."""
    if not actor_id or event is None or event.sort_date_operation_id != operation.id or mission.sort_date_operation_id != operation.id:
        raise NeoSubZeroEvidenceError('Choose the current aircraft deicing event.')
    if str(event.id) != str(values.get('event_id') or '') or normalized_tail(event.tail_number) != normalized_tail(mission.assigned_tail_number) or normalized_tail(values.get('physical_tail')) != normalized_tail(event.tail_number):
        raise NeoSubZeroEvidenceError('Aircraft/event identity changed. Reload current values.')
    if mission.departure_status == 'cancelled':
        raise NeoSubZeroEvidenceError('Cancelled missions cannot record a current observation.')
    expected = str(values.get('expected_version') or '')
    if not expected or version_conflict(event, expected):
        raise NeoSubZeroEvidenceError('Departure deice changed. Reload current values before saving this observation.')
    entry = latest(operation, event)
    if str(entry.id if entry else '') != str(values.get('expected_observation_id') or ''):
        raise NeoSubZeroEvidenceError('The observation changed. Reload current values before saving.')
    raw = str(values.get('no_frost_present') or '')
    if raw not in ('', 'yes'):
        raise NeoSubZeroEvidenceError('Invalid frost observation.')
    checked = raw == 'yes'
    official_reason = event.reason_for_application or application_context(operation)['reason_for_application'] or DEFAULT_DEPARTURE_REASON
    if checked and not is_frost(official_reason):
        raise NeoSubZeroEvidenceError('This observation is available only for Reason for Application FROST.')
    previous = project(entry)
    if checked == bool(previous and previous['state'] == 'absent'):
        return entry  # Unchecked legacy/default and identical saves are no-ops.
    reason = str(values.get('correction_reason') or '').strip()
    if previous and not reason:
        raise NeoSubZeroEvidenceError('A correction reason is required to change the saved observation.')
    now = datetime.utcnow()
    payload = dict(event_id=event.id, tail=event.tail_number,
        state='absent' if checked else 'unknown', observer_user_id=actor_id,
        observed_at_utc=now.isoformat()+'Z', official_reason=official_reason,
        statement=LABEL if checked else 'OBSERVATION WITHDRAWN — PHYSICAL FROST UNKNOWN',
        testing_phase=True, training_eligible=False, learning_eligible=False,
        outcome_validation_eligible=False,
        exclusion='Recorded during NeoSubZero TESTING; permanently excluded from training and outcome validation.')
    # Advance the normal event revision without changing any physical milestone,
    # reason, pass, gallons or immutable snapshot. Keep version monotonic.
    event.updated_at = max(now, event.updated_at + timedelta(microseconds=1)) if event.updated_at else now
    return audit_change(operation, ACTION, actor_id=actor_id, mission=mission, event=event,
        reason=reason, before={'observation':previous}, after={'observation':payload})


def decorate(operation, rows):
    entries = NeoSubZeroAuditEntry.query.filter_by(sort_date_operation_id=operation.id,
        action=ACTION).order_by(NeoSubZeroAuditEntry.id).all() if operation else ()
    by_event = {entry.event_id:entry for entry in entries}
    for row in rows:
        observation = project(by_event.get(getattr(row.get('event'), 'id', None)))
        row['frost_treatment_observation'] = observation
        row['frost_observation_available'] = bool(row.get('event') and is_frost(row.get('reason_for_application')))


def interpretation(reason, observation):
    """Separate physical evidence from the official reason; no training activation."""
    original = frost_analysis.classification(reason)
    if not is_frost(reason):
        return original
    absent = bool(observation and observation.get('state') == 'absent')
    return dict(label='OBSERVED FROST ABSENT' if absent else 'UNKNOWN',
        physical_frost_confirmed=False if absent else None,
        evidence_strength='explicit_coordinator_observation' if absent else 'no_explicit_physical_observation',
        activity='DEPARTURE', physically_confirmed=absent)
