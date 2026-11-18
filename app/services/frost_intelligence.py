"""Persistent opt-ins, separate from weather and frozen historical research.

Uses the existing compact FROST control catalog and immutable security audit.
No training imports, worker, provider I/O or settings writes on reads.
"""
from datetime import datetime, timezone

from app.extensions import db
from app.models import AccountSecurityEvent, FrostHistoryTask, Gateway
from app.services.account_security import lock_administration, platform_grandmaster

VERSION = 'frost-intelligence-v1'
KIND = 'intelligence_control'
KEYS = ('learn_from_live_operations', 'automatic_retraining', 'operational_predictions')
LABELS = ('Learn from Live Operations', 'Automatic Retraining', 'Operational Predictions & Alerts')


def can_edit(actor):
    if not platform_grandmaster(actor) or not actor.email_verified_at:
        return False
    from app.services.account_scope_runtime import scoped_active, is_platform_admin
    return not scoped_active() or is_platform_admin(actor)


def read(gateway_id):
    with db.session.no_autoflush:
        row = FrostHistoryTask.query.filter_by(gateway_id=gateway_id, kind=KIND,
            identity='settings', version=VERSION).populate_existing().one_or_none()
    value = (row.summary or {}) if row else {}
    return dict(schema=VERSION, gateway_id=gateway_id, revision=value.get('revision', 0),
        learning_since=value.get('learning_since'),
        **{key: value.get(key) is True for key in KEYS})


def operational_prerequisite(gateway_id):
    from app.services.frost_model_registry import current_release
    release = current_release(gateway_id)
    # Current registry releases approve treatment-cohort RESEARCH only. The
    # current scorer has no independently validated physical-frost contract.
    # Do not reinterpret those approvals or metadata flags as operational ones.
    if release and release.artifact_id:
        return 'The approved model is research-only. No independently validated, explicitly approved operational model is compatible with the current FROST runtime.'
    return 'No independently validated, explicitly approved compatible operational FROST model is available. Historical-model research is not operational approval.'


def save(gateway_id, values, expected_revision, *, confirm_operational=False):
    actor = lock_administration()
    if not can_edit(actor):
        raise PermissionError('Only an active, verified NeoApps platform Grandmaster may change FROST Intelligence.')
    Gateway.query.filter_by(id=gateway_id).with_for_update().one()
    before = read(gateway_id)
    if str(expected_revision) != str(before['revision']):
        raise ValueError('FROST Intelligence changed in another session. Reload its current settings before saving.')
    if set(values) != set(KEYS) or any(type(values[key]) is not bool for key in KEYS):
        raise ValueError('Supply all three FROST settings as true or false.')
    if values['operational_predictions'] and not before['operational_predictions']:
        if confirm_operational is not True:
            raise ValueError('Explicitly confirm activation of operational predictions and alerts.')
        raise ValueError(operational_prerequisite(gateway_id))
    changed = {key: dict(previous=before[key], new=values[key]) for key in KEYS if before[key] != values[key]}
    if not changed:
        return before
    at = datetime.now(timezone.utc).isoformat()
    after = dict(before, **values, revision=before['revision'] + 1)
    if 'learn_from_live_operations' in changed:
        after['learning_since'] = at if values['learn_from_live_operations'] else None
    row = FrostHistoryTask.query.filter_by(gateway_id=gateway_id, kind=KIND,
        identity='settings', version=VERSION).one_or_none()
    if row is None:
        row = FrostHistoryTask(gateway_id=gateway_id, kind=KIND, identity='settings',
            version=VERSION, status='configured')
        db.session.add(row)
    row.summary = after
    row.updated_at = datetime.utcnow()
    db.session.add(AccountSecurityEvent(actor_user_id=actor.id, subject_user_id=actor.id,
        action='frost_intelligence_changed', details=dict(gateway_id=gateway_id,
            changes=changed, before=before, after=after, recorded_at_utc=at)))
    return after


def stamp_learning(operation, payload, *, previous=None):
    """Seal capture-time eligibility; enabling later never relabels old tests.

    A new opt-in epoch only admits new-sort, explicit physical observations.
    Nightly treatment/no-spray records alone cannot establish training truth.
    Eligibility is permission to consider evidence, not model validation.
    """
    policy = read(operation.gateway_id) if operation.gateway_id else None
    control = dict(schema=VERSION, gateway_id=operation.gateway_id,
        revision=policy['revision'] if policy else 0,
        enabled=bool(policy and policy['learn_from_live_operations']),
        since=policy['learning_since'] if policy else None,
        operation_created_at_utc=operation.created_at.replace(tzinfo=timezone.utc).isoformat() if operation.created_at else None)
    if previous is not None:
        # Corrections retain the original capture's epoch, including exclusions.
        control = dict(previous.get('learning_control') or dict(control, enabled=False, since=None))
    value = dict(payload, learning_control=control)
    value['testing_phase'] = bool(payload.get('testing_phase') is True or (previous or {}).get('testing_phase') is True
        or not (control['enabled'] and control.get('since')))
    value['learning_eligible'] = bool(policy and policy['learn_from_live_operations'] and eligible_evidence(value))
    return value


def eligible_evidence(value):
    """Pure consumer guard, also used by offline validation of archived receipts."""
    control = value.get('learning_control') or {}
    if (control.get('schema') != VERSION or control.get('enabled') is not True
            or value.get('testing_phase') is True or value.get('evidence_active') is False
            or value.get('record_type') != 'direct_aircraft_frost_inspection'
            or value.get('result') not in ('present', 'absent') or not value.get('observer_user_id')
            or value.get('source') != 'explicit direct visual inspection attestation'
            or value.get('evidence_strength') != 'direct_physical_inspection'
            or not (value.get('weather') or {}).get('source_sha256')):
        return False
    try:
        since = datetime.fromisoformat(control['since'])
        observed = datetime.fromisoformat(value['observed_at_utc'])
        created = datetime.fromisoformat(control['operation_created_at_utc'])
        return bool(since.tzinfo and observed.tzinfo and created.tzinfo and created >= since and observed >= since)
    except (KeyError, TypeError, ValueError):
        return False


def operational_prediction(gateway_id, **_kwargs):
    # Fail before model/weather loading, cached inference, scoring or alerts.
    policy = read(gateway_id)
    return dict(enabled=False, state=operational_prerequisite(gateway_id) if policy['operational_predictions']
                else 'OPERATIONAL PREDICTIONS & ALERTS OFF', physical_frost_probability=False)
