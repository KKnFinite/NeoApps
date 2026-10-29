"""SPEAR decision observations. No ranking, operational writes or synchronous Vault I/O."""
from dataclasses import asdict
from datetime import datetime, timedelta
from decimal import Decimal
import hashlib
import json
import threading
import time
from uuid import uuid4

from flask import current_app, has_request_context, request
from flask_login import current_user
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy import select, func

from app.extensions import db
from app.services.memory_diagnostics import observe_process_memory
from app.models.neoscorpion_spear_observation import NeoScorpionSpearObservation as Observation
from app.models.neoscorpion_spear_observation import NeoScorpionSpearFleetObservation as FleetObservation
from app.services.neoscorpion_learning_vault import learning_vault_status
from app.services.neoscorpion_spear_learning import SPEAR_ALGORITHM_VERSION, TEACH_SPEAR_REASON_CODES

VERSION = 'spear-decision/v1'
MAX_REFERENCE_AGE_SECONDS = 1800
MAX_RECORD_BYTES = 64000
_worker_lock = threading.Lock()
_last_worker = {}


def _plain(value):
    if isinstance(value, datetime):
        return value.isoformat(timespec='microseconds') + 'Z'
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def _hash(value):
    return hashlib.sha256(json.dumps(_plain(value), sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def scope(operation, mission, assignment, revision, *, operational=None):
    """Take BEFORE mutation, using already-locked canonical objects."""
    return _plain(dict(operation_id=operation.id, mission_id=mission.id,
        revision=int(revision or 0), operational=operational, tail=mission.assigned_tail_number,
        required=mission.planned_fuel_load, departure=mission.eta_datetime_utc or mission.planned_datetime_utc,
        mission_version=mission.updated_at, cycle=int(assignment.current_cycle_number or 1) if assignment else 1,
        work_id=assignment.active_work_state_id if assignment else None,
        fueler_id=assignment.assigned_fueler_user_id if assignment else None,
        truck_id=assignment.assigned_truck_id if assignment else None))


def operational_fingerprint(operation):
    """Bounded canonical inputs also catch mission updates outside fueling revision."""
    from app.models import SortDateMission, SortDateParkingAssignment, NeoScorpionSettings
    missions = db.session.execute(select(SortDateMission.id, SortDateMission.assigned_tail_number,
        SortDateMission.updated_at, SortDateMission.planned_datetime_utc, SortDateMission.eta_datetime_utc,
        SortDateMission.actual_block_in_datetime_utc, SortDateMission.arrival_status,
        SortDateMission.planned_fuel_load).where(SortDateMission.sort_date_operation_id == operation.id)
        .order_by(SortDateMission.id)).all()
    parking = db.session.execute(select(SortDateParkingAssignment.tail_number,
        SortDateParkingAssignment.position_code).where(SortDateParkingAssignment.sort_date_operation_id == operation.id)
        .order_by(SortDateParkingAssignment.id)).all()
    settings = db.session.get(NeoScorpionSettings, db.session.query(NeoScorpionSettings.id)
        .filter_by(gateway_id=operation.gateway_id).scalar())
    return _hash([list(map(tuple, missions)), list(map(tuple, parking)),
        {c.name:getattr(settings,c.name) for c in settings.__table__.columns} if settings else None])


def prepare_scope(operation, mission, assignment, revision):
    result = scope(operation, mission, assignment, revision)
    try:
        with db.session.begin_nested():
            if enabled(operation.gateway_id):
                result['operational'] = operational_fingerprint(operation)
    except Exception:
        current_app.logger.warning('SPEAR pre-decision conditions unavailable; capture remains unverified')
    return result


def enabled(gateway_id):
    from app.models import NeoScorpionSettings
    return bool(db.session.query(NeoScorpionSettings.spear_learning_capture_enabled)
        .filter_by(gateway_id=gateway_id).scalar()) and learning_vault_status().configured


def attach_references(context, gateway):
    """Sign exactly the rendered SPEAR evidence. Never write snapshots on page GET."""
    for row in context['rows']:
        row['spear_decision_reference'] = ''
        row['spear_last_decision_id'] = None
    context['spear_decision_reasons'] = TEACH_SPEAR_REASON_CODES
    context['spear_automatic_reference'] = ''
    if not context.get('operation') or not context['spear_settings'].learning_capture_enabled:
        return
    if not learning_vault_status().configured:
        return
    operation = context['operation']
    signer = URLSafeSerializer(current_app.secret_key, salt='spear-rendered-decision-v1')
    actor = getattr(current_user, 'id', None)
    now = datetime.utcnow()
    plan = context.get('spear_plan')
    from app.services.neoscorpion import _fuel_assignments_revision_for_operation
    revision = _fuel_assignments_revision_for_operation(operation)
    operational = operational_fingerprint(operation)
    for row in context['rows']:
        step = row.get('spear_step')
        evidence = dict(gateway_id=gateway.id, actor_id=actor, generated_at=_plain(now),
            plan_token=plan.token if plan else None, algorithm_version=getattr(plan, "algorithm_version", SPEAR_ALGORITHM_VERSION),
            scope=scope(operation, row['mission'], row.get('assignment'), revision, operational=operational),
            recommendation=_plain(asdict(step)) if step else None,
            alternatives=_plain((step.explanation or {}).get('alternatives', [])[1:4]) if step else [],
            conditions=_plain(dict(parking=row.get('parking_position'), demand=row.get('planning_demand_gallons'),
                arrival_status=row.get('arrival_status'), required=row.get('required_fuel_lbs'),
                inbound=row.get('inbound_fuel_lbs'), problem=row.get('spear_problem'),
                aircraft_type=row.get('detailed_aircraft_type'), cycle_type=row.get('cycle_type'),
                arrival_eta=getattr(row.get('arrival_mission'), 'eta_datetime_utc', None),
                arrival_block_in=getattr(row.get('arrival_mission'), 'actual_block_in_datetime_utc', None),
                calibration=context['spear_settings'].live_calibration_mode)))
        if len(json.dumps(evidence)) <= MAX_RECORD_BYTES // 2:
            row['spear_decision_reference'] = signer.dumps(evidence)
    from app.services.neoscorpion_spear import first_automatic_step
    automatic = first_automatic_step(plan) if plan else None
    if automatic:
        context['spear_automatic_reference'] = next((r['spear_decision_reference'] for r in context['rows']
            if r['mission'].id == automatic.mission_id), '')
    recent = db.session.execute(select(Observation.id, Observation.mission_id, Observation.cycle_number).where(Observation.operation_id == operation.id,
        Observation.kind == 'decision').order_by(Observation.created_at.desc(), Observation.id.desc())).all()
    latest = {}
    for item in recent:
        latest.setdefault((item.mission_id, item.cycle_number), item.id)
    for row in context['rows']:
        assignment = row.get('assignment')
        row['spear_last_decision_id'] = latest.get((row['mission'].id,
            int(assignment.current_cycle_number or 1) if assignment else 1))


def _reference(raw, gateway, actor, before, now):
    if not raw:
        return None, 'missing'
    try:
        if len(raw) > MAX_RECORD_BYTES * 2:
            raise ValueError
        evidence = URLSafeSerializer(current_app.secret_key, salt='spear-rendered-decision-v1').loads(raw)
        if evidence['gateway_id'] != gateway.id or evidence['actor_id'] != getattr(actor, 'id', None):
            raise ValueError
        original = evidence['scope']
        if any(original[k] != before[k] for k in ('operation_id', 'mission_id')):
            raise ValueError
        age = (now - datetime.fromisoformat(evidence['generated_at'].rstrip('Z'))).total_seconds()
        stale = age < -5 or age > MAX_REFERENCE_AGE_SECONDS or original != before
        return evidence, 'stale' if stale else 'current'
    except (BadSignature, KeyError, TypeError, ValueError):
        return None, 'invalid'


def classify(evidence, state, fueler_id, truck_id):
    recommendation = (evidence or {}).get('recommendation')
    if not recommendation or recommendation.get('action_type') != 'assign':
        return 'no_recommendation'
    if fueler_id is None or truck_id is None:
        return 'partial_selection'
    if not recommendation.get('fueler_id') or not recommendation.get('truck_id'):
        return 'incomplete_recommendation'
    if state != 'current':
        return 'stale_recommendation' if state == 'stale' else 'unverified_recommendation'
    return 'matched' if (fueler_id, truck_id) == (recommendation['fueler_id'], recommendation['truck_id']) else 'different'


def _enqueue(kind, assignment, gateway_id, payload, event_key, *, now=None):
    identifier = uuid4().hex
    record = dict(schema_version=VERSION, record_type=kind, id=identifier, gateway_id=gateway_id,
        operation_id=assignment.sort_date_operation_id, mission_id=assignment.sort_date_mission_id,
        assignment_id=assignment.id, cycle_number=int(assignment.current_cycle_number or 1),
        captured_at=_plain(now or datetime.utcnow()), training_eligible=False, payload=_plain(payload))
    validate_record(record)
    existing = db.session.query(Observation.id).filter_by(event_key=event_key).first()
    if existing:
        return existing[0]
    db.session.add(Observation(id=identifier, kind=kind, gateway_id=gateway_id,
        operation_id=record['operation_id'], mission_id=record['mission_id'], assignment_id=assignment.id,
        cycle_number=record['cycle_number'], payload=record, event_key=event_key,
        created_at=now or datetime.utcnow()))
    db.session.flush()
    return identifier


def observe(gateway, assignment, before, action, *, actor=None, reference=None, now=None):
    """Call after successful canonical flush; the savepoint isolates learning failures."""
    try:
        with db.session.begin_nested():
            if not enabled(gateway.id):
                return None
            actor = actor or current_user
            now = now or datetime.utcnow()
            raw = reference if reference is not None else (request.form.get('spear_decision_reference') if has_request_context() else None)
            evidence, state = _reference(raw, gateway, actor, before, now)
            if evidence and (evidence['scope']['cycle'] != int(assignment.current_cycle_number or 1) or evidence['scope']['tail'] != assignment.confirmed_tail_number):
                state = 'stale'
            selected = dict(fueler_id=assignment.assigned_fueler_user_id, truck_id=assignment.assigned_truck_id)
            payload = dict(action=action, actor_id=getattr(actor, 'id', None),
                before=before, selected=selected, work_id=assignment.active_work_state_id,
                tail=assignment.confirmed_tail_number, original_spear=evidence,
                reference_state=state, comparison=classify(evidence, state, **selected))
            return _enqueue('decision', assignment, gateway.id, payload,
                _hash([assignment.id, before, action, selected, _plain(now)]), now=now)
    except Exception:
        current_app.logger.exception('SPEAR decision capture failed safely; canonical action preserved')
        return None


def add_reason(gateway, actor, decision_id, reason, note):
    from app.models import NeoScorpionFuelAssignment
    if not enabled(gateway.id):
        raise ValueError('SPEAR Learning Capture is off or its Vault is unavailable.')
    original = db.session.get(Observation, decision_id)
    if not original or original.kind != 'decision' or original.gateway_id != gateway.id:
        raise ValueError('Select a valid decision observation.')
    if reason and reason not in TEACH_SPEAR_REASON_CODES:
        raise ValueError('Choose a known optional SPEAR reason.')
    note = str(note or '').strip()
    if len(note) > 1000:
        raise ValueError('Decision note must be at most 1000 characters.')
    assignment = db.session.get(NeoScorpionFuelAssignment, original.assignment_id)
    payload = dict(decision_id=original.id, actor_id=actor.id, reason=reason or None, note=note or None)
    return _enqueue('reason', assignment, gateway.id, payload, _hash([original.id, payload]))


def validate_record(record):
    keys = {'schema_version','record_type','id','gateway_id','operation_id','mission_id','assignment_id',
            'cycle_number','captured_at','training_eligible','payload'}
    if (not isinstance(record, dict) or set(record) != keys or record['schema_version'] != VERSION
            or record['record_type'] not in ('decision','outcome','reason','fleet','fleet_outcome') or record['training_eligible'] is not False):
        raise ValueError('Invalid SPEAR decision record contract.')
    for key in ('gateway_id','operation_id','mission_id','assignment_id','cycle_number'):
        if record['record_type'] in ('fleet','fleet_outcome') and key in ('mission_id','assignment_id','cycle_number') and record[key] is None:
            continue
        if type(record[key]) is not int or record[key] <= 0:
            raise ValueError('Invalid SPEAR decision identity.')
    if len(record['id']) != 32 or not all(c in '0123456789abcdef' for c in record['id']):
        raise ValueError('Invalid SPEAR record id.')
    datetime.fromisoformat(record['captured_at'].rstrip('Z'))
    if not isinstance(record['payload'], dict) or len(json.dumps(record, allow_nan=False)) > MAX_RECORD_BYTES:
        raise ValueError('Invalid SPEAR decision evidence size.')
    required = {'decision': {'action','actor_id','before','selected','work_id','tail','original_spear','reference_state','comparison'},
                'outcome': {'decision_id','event_ids','qualified','exclusions','comparison_conclusion'},
                'reason': {'decision_id','actor_id','reason','note'},
                'fleet': {'audit_id','sent_event_id','actor_id','before','original_spear','reference_state','executed_step'},
                'fleet_outcome': {'decision_id','audit_id','sent_event_id','returned_event_id','minutes','gallons','qualified','exclusions'}}
    expected=required[record['record_type']]
    optional={'predicted_return_at','roundtrip_policy'} if record['record_type'] in ('fleet','fleet_outcome') else set()
    if set(record['payload']) not in (expected,expected|optional):
        raise ValueError('Invalid SPEAR observation payload.')
    payload = record['payload']
    if 'predicted_return_at' in payload:
        if payload['predicted_return_at'] is not None:
            datetime.fromisoformat(payload['predicted_return_at'].rstrip('Z'))
        policy=payload['roundtrip_policy']
        if policy is not None and (not isinstance(policy,dict) or type(policy.get('minutes')) not in (int,float)
                or policy['minutes']<=0):
            raise ValueError('Invalid immutable TOP OFF forecast.')
    def identifier(value, optional=False):
        return (optional and value is None) or (type(value) is int and value > 0)
    if record['record_type'] == 'decision':
        if (payload['action'] not in {'initial_assignment','reassignment','unassignment','fueler_swap','truck_swap'}
                or payload['reference_state'] not in {'current','stale','missing','invalid'}
                or payload['comparison'] not in {'matched','different','partial_selection','no_recommendation',
                    'incomplete_recommendation','stale_recommendation','unverified_recommendation'}
                or not identifier(payload['actor_id'], True) or not identifier(payload['work_id'], True)
                or not isinstance(payload['before'], dict) or not isinstance(payload['selected'], dict)
                or set(payload['selected']) != {'fueler_id','truck_id'}
                or not all(identifier(v, True) for v in payload['selected'].values())):
            raise ValueError('Invalid SPEAR resource decision.')
        original = payload['original_spear']
        if original is not None:
            if (not isinstance(original, dict) or original.get('gateway_id') != record['gateway_id']
                    or original.get('scope', {}).get('mission_id') != record['mission_id']
                    or not isinstance(original.get('algorithm_version'), str)
                    or not isinstance(original.get('alternatives'), list) or len(original['alternatives']) > 3):
                raise ValueError('Invalid original SPEAR evidence.')
            for alternative in original['alternatives']:
                if not isinstance(alternative, dict) or not all(identifier(alternative.get(k)) for k in ('fueler_id','truck_id')):
                    raise ValueError('Invalid evaluated SPEAR alternative.')
    elif record['record_type'] == 'outcome':
        if (not isinstance(payload['decision_id'], str) or len(payload['decision_id']) != 32
                or type(payload['qualified']) is not bool or payload['comparison_conclusion'] != 'observed_only'
                or not isinstance(payload['event_ids'], list) or not all(identifier(v) for v in payload['event_ids'])
                or not isinstance(payload['exclusions'], list) or not all(isinstance(v,str) for v in payload['exclusions'])):
            raise ValueError('Invalid SPEAR outcome observation.')
    elif record['record_type'] == 'reason':
        if (not identifier(payload['actor_id']) or not isinstance(payload['decision_id'], str)
                or payload['reason'] not in (*TEACH_SPEAR_REASON_CODES, None)
                or (payload['note'] is not None and (not isinstance(payload['note'],str) or len(payload['note']) > 1000))):
            raise ValueError('Invalid supplementary decision reason.')
    else:
        if not identifier(payload['audit_id']) or not identifier(payload['sent_event_id']):
            raise ValueError('Invalid fleet-action identity.')
        if record['record_type'] == 'fleet':
            if (not identifier(payload['actor_id']) or payload['reference_state'] not in {'current','stale','missing','invalid'}
                    or not isinstance(payload['executed_step'],dict) or payload['executed_step'].get('action_type') != 'top_off'):
                raise ValueError('Invalid approved fleet action.')
        elif (not identifier(payload['returned_event_id']) or type(payload['qualified']) is not bool
                or type(payload['gallons']) is not int or payload['gallons'] < 0
                or not isinstance(payload['minutes'],(int,float)) or payload['minutes'] <= 0
                or not isinstance(payload['exclusions'],list)):
            raise ValueError('Invalid fleet outcome.')


def observe_fleet(gateway, operation, audit, step, actor, before):
    """Approved TOP OFF evidence is independent of aircraft fueling assignments."""
    from app.models import NeoScorpionTopOffEvent
    try:
        with db.session.begin_nested():
            if not enabled(gateway.id):
                return
            sent = db.session.scalars(select(NeoScorpionTopOffEvent).where(
                NeoScorpionTopOffEvent.sort_date_operation_id == operation.id,
                NeoScorpionTopOffEvent.fuel_truck_id == step.truck_id, NeoScorpionTopOffEvent.kind == 'sent')
                .order_by(NeoScorpionTopOffEvent.id.desc()).limit(1)).first()
            if not sent:
                return
            evidence,state = _reference(request.form.get('spear_decision_reference'),gateway,actor,before,datetime.utcnow())
            identifier=uuid4().hex
            record=dict(schema_version=VERSION,record_type='fleet',id=identifier,gateway_id=gateway.id,
                operation_id=operation.id,mission_id=step.mission_id,assignment_id=None,cycle_number=None,
                captured_at=_plain(datetime.utcnow()),training_eligible=False,payload=_plain(dict(audit_id=audit.id,
                    sent_event_id=sent.id,actor_id=actor.id,before=before,original_spear=evidence,
                    reference_state=state,executed_step=asdict(step),
                    predicted_return_at=sent.spear_predicted_return_at_utc,roundtrip_policy=sent.spear_roundtrip_policy)))
            validate_record(record)
            db.session.add(FleetObservation(id=identifier,kind='fleet',audit_id=audit.id,
                sent_event_id=sent.id,gateway_id=gateway.id,payload=record))
            db.session.flush()
    except Exception:
        current_app.logger.exception('SPEAR fleet learning capture deferred safely')


def reconcile_fleet(gateway_id):
    from sqlalchemy.orm import aliased
    from sqlalchemy import exists
    from app.models import NeoScorpionTopOffEvent
    sent=aliased(NeoScorpionTopOffEvent)
    returned=aliased(NeoScorpionTopOffEvent)
    outcome=aliased(FleetObservation)
    rows=db.session.execute(select(FleetObservation,sent,returned)
        .join(sent,sent.id==FleetObservation.sent_event_id)
        .join(returned,returned.sent_event_id==sent.id)
        .where(FleetObservation.gateway_id==gateway_id,FleetObservation.kind=='fleet',
            ~exists(select(outcome.id).where(outcome.audit_id==FleetObservation.audit_id,outcome.kind=='fleet_outcome')))
        .order_by(FleetObservation.created_at).limit(20)).all()
    for decision,start,end in rows:
        minutes=(end.occurred_at_utc-start.occurred_at_utc).total_seconds()/60
        if minutes <= 0 or end.gallons is None:
            continue
        exclusions=[] if start.inventory_verified and end.inventory_verified and minutes <= 240 else ['unqualified_roundtrip']
        identifier=uuid4().hex
        record={**decision.payload,'id':identifier,'record_type':'fleet_outcome','captured_at':_plain(end.occurred_at_utc),
            'payload':dict(decision_id=decision.id,audit_id=decision.audit_id,sent_event_id=start.id,
                returned_event_id=end.id,minutes=minutes,gallons=end.gallons,qualified=not exclusions,exclusions=exclusions,
                predicted_return_at=_plain(start.spear_predicted_return_at_utc),roundtrip_policy=start.spear_roundtrip_policy)}
        validate_record(record)
        with db.session.begin_nested():
            db.session.add(FleetObservation(id=identifier,kind='fleet_outcome',audit_id=decision.audit_id,
                sent_event_id=start.id,gateway_id=gateway_id,payload=record))
            db.session.flush()
    db.session.commit()


def reconcile(gateway_id, *, limit=40):
    """Only captured decisions; exact cycle/tail/visit/resource event evidence, no invented history."""
    from app.models import NeoScorpionFuelAssignment, NeoScorpionFuelingEvent, NeoScorpionFuelWorkState
    from app.services.neoscorpion_prof import event_math
    now = datetime.utcnow()
    decisions = db.session.scalars(select(Observation).where(Observation.gateway_id == gateway_id,
        Observation.kind == 'decision', Observation.reconcile_after <= now)
        .order_by(Observation.reconcile_after, Observation.id).limit(limit)).all()
    ids = {item.assignment_id for item in decisions}
    if not ids:
        return
    cohort = list(db.session.scalars(select(Observation).where(Observation.assignment_id.in_(ids),
        Observation.kind == 'decision')))
    assignments = {a.id:a for a in db.session.scalars(select(NeoScorpionFuelAssignment).where(NeoScorpionFuelAssignment.id.in_(ids)))}
    events = list(db.session.scalars(select(NeoScorpionFuelingEvent).where(NeoScorpionFuelingEvent.fuel_assignment_id.in_(ids))))
    work_ids = {e.fuel_work_state_id for e in events if e.fuel_work_state_id}
    works = {w.id:w for w in db.session.scalars(select(NeoScorpionFuelWorkState).where(NeoScorpionFuelWorkState.id.in_(work_ids)))} if work_ids else {}
    for item in decisions:
        item.reconcile_after = now + timedelta(minutes=5)
        assignment = assignments[item.assignment_id]
        payload = item.payload['payload']
        relevant = [e for e in events if e.fuel_assignment_id == item.assignment_id
            and int(e.cycle_number or 1) == item.cycle_number and e.tail_number == payload['tail']]
        terminal = assignment.review_status == 'complete' or int(assignment.current_cycle_number or 1) > item.cycle_number
        next_at = min((other.created_at for other in cohort if other.assignment_id == item.assignment_id
            and other.cycle_number == item.cycle_number and other.created_at > item.created_at), default=datetime.max)
        selected = payload['selected']
        matching = [e for e in relevant if e.fueler_user_id == selected['fueler_id'] and e.fuel_truck_id == selected['truck_id']
            and (not payload['work_id'] or e.fuel_work_state_id == payload['work_id'])
            and e.started_at_utc and item.created_at <= e.started_at_utc < next_at]
        if not terminal and not matching:
            continue
        exclusions = []
        if not matching:
            exclusions.append('no_matching_physical_event')
        for event in matching:
            work = works.get(event.fuel_work_state_id)
            if not work or not work.physical_finalized_at_utc:
                exclusions.append('physical_visit_not_finalized')
            math = event_math(event)
            exclusions.extend([*math['fueler_reasons'], *math['truck_reasons']])
        # Nonterminal evidence can acquire its final physical event later.
        if not terminal and exclusions:
            continue
        record = dict(decision_id=item.id, event_ids=[e.id for e in matching], qualified=not exclusions,
            exclusions=sorted(set(exclusions)), comparison_conclusion='observed_only')
        with db.session.begin_nested():
            # Preserve the original cycle even after rollover.
            proxy = type('Identity', (), dict(id=item.assignment_id, sort_date_operation_id=item.operation_id,
                sort_date_mission_id=item.mission_id, current_cycle_number=item.cycle_number))()
            _enqueue('outcome', proxy, gateway_id, record, _hash(['outcome', item.id, record]))
    db.session.commit()


def drain(gateway_id, *, limit=3):
    """Bounded retryable delivery; invoked outside the operational request transaction."""
    from app.services.neoscorpion_learning_vault import export_decision_record
    if not enabled(gateway_id):
        return 0
    reconcile(gateway_id)
    reconcile_fleet(gateway_id)
    now = datetime.utcnow()
    rows = db.session.scalars(select(Observation).where(Observation.gateway_id == gateway_id,
        Observation.delivered_at.is_(None), Observation.retry_at <= now)
        .order_by(Observation.created_at, Observation.id).limit(limit).with_for_update(skip_locked=True)).all()
    fleet = db.session.scalars(select(FleetObservation).where(FleetObservation.gateway_id == gateway_id,
        FleetObservation.delivered_at.is_(None), FleetObservation.retry_at <= now)
        .order_by(FleetObservation.created_at,FleetObservation.id).limit(limit).with_for_update(skip_locked=True)).all()
    rows = sorted([*rows,*fleet],key=lambda item:(item.created_at,item.id))[:limit]
    ids = [(type(item),item.id) for item in rows]
    for item in rows:
        item.attempts += 1
        item.retry_at = now + timedelta(seconds=min(3600, 30 * 2 ** min(item.attempts, 6)))
    db.session.commit()  # Claim bounded work before any external I/O; retry leases survive crashes.
    delivered = 0
    for model,identifier in ids:
        item = db.session.get(model, identifier)
        try:
            saved = export_decision_record(item.payload)
            item.vault_key = saved['key']
            item.delivered_at = datetime.utcnow()
            db.session.commit()
            delivered += 1
        except Exception:
            db.session.rollback()
            current_app.logger.warning('SPEAR observation delivery deferred: id=%s', identifier)
    return delivered


def schedule_delivery(app, gateway_id):
    if app.config.get('TESTING') or time.monotonic() - _last_worker.get(gateway_id, 0) < 30:
        return
    if not _worker_lock.acquire(blocking=False):
        return
    _last_worker[gateway_id] = time.monotonic()
    def run():
        try:
            with app.app_context(),observe_process_memory('archive_delivery_cycle'):
                try:
                    try:
                        drain(gateway_id)
                    except Exception:
                        db.session.rollback()
                        app.logger.warning('SPEAR archive delivery deferred safely', exc_info=True)
                    from app.services.neosubzero_archive import drain as drain_subzero
                    try:
                        drain_subzero(gateway_id)
                    except Exception:
                        db.session.rollback()
                        app.logger.warning('SubZero archive delivery deferred safely',exc_info=True)
                    from app.services.frost_documents import drain as drain_frost_documents
                    drain_frost_documents(gateway_id)
                    from app.services.frost_auto import tick as capture_frost
                    capture_frost(gateway_id)
                    from app.services.frost_xchange_links import drain as link_xchange
                    link_xchange(gateway_id)
                    from app.services.frost_departures import drain as reconcile_departures
                    reconcile_departures(gateway_id)
                    # Historical weather/features are workstation-only. The
                    # live outbox retains original and operational archival work.
                finally:
                    db.session.remove()
        except Exception:
            app.logger.warning('SPEAR outbox reconciliation deferred safely', exc_info=True)
        finally:
            _worker_lock.release()
    threading.Thread(target=run, daemon=True, name='spear-learning-outbox').start()


def initialize_delivery_heartbeat(app):
    """Wake the existing leased archive worker even with no open browser.

    No provider I/O on a web request/startup, no new queue/service or operational
    mutations. Durable task leases coordinate multiple gunicorn processes.
    """
    if app.config.get('TESTING') or not app.config.get('SQLALCHEMY_DATABASE_URI') or app.extensions.get('archive_delivery_heartbeat'):
        return
    app.extensions['archive_delivery_heartbeat'] = True
    def wake():
        try:
            with app.app_context():
                from app.models import FrostDocument,Gateway
                gateways = sorted({item[0] for item in db.session.query(FrostDocument.gateway_id).distinct().all()} |
                    {item[0] for item in db.session.query(Gateway.id).filter(func.upper(Gateway.code)=='RFD').all()})
                db.session.remove()
                for gateway_id in gateways:
                    schedule_delivery(app,gateway_id)
        except Exception:
            app.logger.warning('Archive delivery wake-up deferred safely',exc_info=True)
        finally:
            timer = threading.Timer(60,wake)
            timer.daemon = True
            timer.start()
    timer = threading.Timer(10,wake)
    timer.daemon = True
    timer.start()
