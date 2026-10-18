"""Read-only evaluation of contemporaneous SPEAR evidence, never a new planner.

One earliest qualifying prediction per physical visit/roundtrip. Missing evidence
is disclosed, not scored. Current mission/resource labels are not historical proof.
"""
from collections import Counter
from datetime import timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.extensions import db
from app.models import NeoScorpionTopOffEvent, NeoScorpionFuelTruck, NeoScorpionFuelingEvent, SortDateOperation, User
from app.models.neoscorpion_spear_observation import NeoScorpionSpearObservation
from app.services.neoscorpion_prof import event_math, timestamp, minutes, number
from app.services.neoscorpion_spear import _risk
from app.services.neoscorpion_spear_decisions import classify, validate_record

LIMIT = 2000


def off_result(event, *, identity_reasons=()):
    facts = event.performance_snapshot if isinstance(event.performance_snapshot, dict) else {}
    reasons = list(identity_reasons)
    try:
        reasons.extend(event_math(event)['fueler_reasons'])
    except (ValueError, TypeError, AttributeError):
        reasons.append('Malformed historical performance evidence')
    start, end = timestamp(event.started_at_utc), timestamp(event.ended_at_utc)
    candidates = []
    forecasts = facts.get('forecasts') if isinstance(facts.get('forecasts'), list) else []
    for forecast in forecasts:
        if not isinstance(forecast, dict):
            continue
        observed, predicted = timestamp(forecast.get('observed_at')), timestamp(forecast.get('likely'))
        if (start and end and observed and predicted and start <= observed < end
                and predicted >= observed and forecast.get('source') in {'BASELINE', 'QUALIFIED SERVICE HISTORY'}):
            candidates.append((observed, predicted, forecast))
    physical_reasons = list(reasons)
    first = min(candidates, key=lambda item: (item[0], item[1])) if candidates else None
    if first and len({p[1] for p in candidates if p[0] == first[0]}) > 1:
        reasons.append('Conflicting predictions share the earliest capture timestamp')
        first = None
    if not first:
        reasons.append('No valid contemporaneous OFF prediction')
    return dict(key=f'fuel:{event.id}', event=event, at=end, actual=end,
                observed=first[0] if first else None, predicted=first[1] if first else None,
                source=first[2]['source'] if first else 'Unavailable', revisions=len(forecasts),
                error=minutes(first[1], end) if first and not reasons else None,
                reasons=list(dict.fromkeys(reasons)), physical_reasons=physical_reasons)


def top_off_result(sent, returned):
    reasons = []
    start, end, predicted = map(timestamp, (sent.occurred_at_utc,
        returned.occurred_at_utc if returned else None, sent.spear_predicted_return_at_utc))
    if not returned:
        reasons.append('No recorded RETURN')
    elif (returned.fuel_truck_id != sent.fuel_truck_id
          or returned.sort_date_operation_id != sent.sort_date_operation_id
          or returned.sent_event_id != sent.id):
        reasons.append('Roundtrip identity mismatch')
    if not start or not end or end <= start:
        reasons.append('Physical roundtrip boundaries unavailable or invalid')
    if not predicted or not start or predicted <= start or not sent.spear_roundtrip_policy:
        reasons.append('Frozen TOP OFF prediction/policy unavailable')
    if not sent.inventory_verified or not returned or not returned.inventory_verified:
        reasons.append('Roundtrip verification incomplete')
    return dict(key=f'topoff:{sent.id}', at=start, truck_id=sent.fuel_truck_id,
                predicted=predicted, actual=end, predicted_minutes=minutes(start, predicted),
                actual_minutes=minutes(start, end), error=minutes(predicted, end) if not reasons else None,
                reasons=reasons)


def summary(rows):
    values = [row['error'] for row in rows if row['error'] is not None]
    return dict(total=len(rows), verified=len(values), unavailable=len(rows)-len(values),
                bias=sum(values)/len(values) if values else None,
                mae=sum(abs(v) for v in values)/len(values) if values else None,
                within5=sum(abs(v) <= 5 for v in values) if values else None)


def trend_summary(rows):
    verified = sorted((r for r in rows if r['error'] is not None), key=lambda r:r['at'])
    half = len(verified)//2
    return dict(earlier=summary(verified[:half]), recent=summary(verified[half:])) if half >= 3 else None


def decision_result(record, outcomes, events):
    """Compare only an executed, matched recommendation with its exact outcome.

    An alternative resource choice is observable, not counterfactual evidence.
    Risk uses the departure deadline captured at decision time, not today's ETD.
    """
    payload = record['payload']
    original = payload.get('original_spear') or {}
    rec, scope = original.get('recommendation') or {}, original.get('scope') or {}
    selected = payload.get('selected') or {}
    comparison = classify(original, payload.get('reference_state'), selected.get('fueler_id'), selected.get('truck_id'))
    reasons = []
    latest = max((timestamp(o.get('captured_at')) for o in outcomes
                  if o['payload'].get('decision_id') == record['id']), default=None)
    matching = [o['payload'] for o in outcomes if o['payload'].get('decision_id') == record['id']
                and timestamp(o.get('captured_at')) == latest]
    qualified = matching if matching and all(o.get('qualified') and not o.get('exclusions') for o in matching) else []
    ids = {i for o in qualified for i in o.get('event_ids', [])}
    event = events.get(next(iter(ids))) if len(ids) == 1 else None
    departure = timestamp(scope.get('departure'))
    generated = timestamp(original.get('generated_at'))
    captured = timestamp(record.get('captured_at'))
    predicted_risk = rec.get('risk')
    actual_risk = None
    if comparison != 'matched':
        reasons.append('No comparable executed recommendation (not a counterfactual score)')
    if not event:
        reasons.append('No unique verified physical outcome linked to this decision')
    else:
        if (record['assignment_id'] != event.fuel_assignment_id or record['cycle_number'] != event.cycle_number
                or event.tail_number != payload.get('tail') or scope.get('tail') != event.tail_number
                or selected.get('truck_id') != event.fuel_truck_id or selected.get('fueler_id') != event.fueler_user_id
                or (payload.get('work_id') and payload['work_id'] != event.fuel_work_state_id)
                or scope.get('required') != event.required_fuel_lbs):
            reasons.append('Physical identity or fuel requirement changed')
        if (not captured or not generated or not timestamp(event.started_at_utc)
                or not generated <= captured <= timestamp(event.started_at_utc)
                or captured-generated > timedelta(minutes=30)):
            reasons.append('Prediction timing not proven before physical work')
    if (not departure or predicted_risk not in {'COVERED', 'AT RISK', 'LATE'}
            or _risk(timestamp(rec.get('projected_complete_at_utc')), departure) != predicted_risk):
        reasons.append('Captured departure/risk evidence unavailable or inconsistent')
    required = number(scope.get('required'))
    if required is None or required <= 0:
        reasons.append('Captured fuel requirement unavailable')
    if departure and captured and captured >= departure:
        reasons.append('Risk observation was not captured before departure')
    if event and not reasons:
        actual_risk = _risk(timestamp(event.ended_at_utc), departure)
    return dict(id=record['id'], at=captured, comparison=comparison, selected=selected,
                recommended=dict(truck_id=rec.get('truck_id'), fueler_id=rec.get('fueler_id')),
                predicted_risk=predicted_risk, actual_risk=actual_risk, reasons=reasons,
                work_id=event.fuel_work_state_id if event else payload.get('work_id'),
                tail=payload.get('tail'), departure=departure, vault_key=None)


def risk_summary(decisions, label):
    rows = [d for d in decisions if d['actual_risk'] is not None]
    tp = sum(d['predicted_risk'] == label and d['actual_risk'] == label for d in rows)
    fp = sum(d['predicted_risk'] == label and d['actual_risk'] != label for d in rows)
    fn = sum(d['predicted_risk'] != label and d['actual_risk'] == label for d in rows)
    tn = len(rows)-tp-fp-fn
    return dict(label=label, n=len(rows), tp=tp, fp=fp, fn=fn, tn=tn,
                precision=100*tp/(tp+fp) if tp+fp else None,
                recall=100*tp/(tp+fn) if tp+fn else None)


def report_context(gateway, operation, *, truck_id=None, fueler_id=None, archive=None):
    if operation is not None and operation.gateway_id != gateway.id:
        raise ValueError('Sort does not belong to this gateway.')
    jobs = db.session.query(NeoScorpionFuelingEvent).join(SortDateOperation,
        SortDateOperation.id == NeoScorpionFuelingEvent.sort_date_operation_id).filter(
        SortDateOperation.gateway_id == gateway.id,
        NeoScorpionFuelingEvent.sort_date_operation_id == operation.id).order_by(
        NeoScorpionFuelingEvent.ended_at_utc.desc(), NeoScorpionFuelingEvent.id.desc()).limit(LIMIT+1).all() if operation else []
    truncated = len(jobs) > LIMIT
    jobs = jobs[:LIMIT]
    # Resolve historical visit identity in one bounded query, not current assignment labels.
    from app.models import NeoScorpionFuelWorkState
    work_ids = {job.fuel_work_state_id for job in jobs}
    works = {w.id:w for w in NeoScorpionFuelWorkState.query.filter(
        NeoScorpionFuelWorkState.id.in_(work_ids)).all()} if work_ids else {}
    off = []
    events = {}
    for e in jobs:
        w = works.get(e.fuel_work_state_id)
        reasons = []
        if (not w or not w.physical_finalized_at_utc or w.ended_early_at_utc
                or w.tail_number != e.tail_number or w.physical_cycle_number != e.cycle_number
                or w.physical_truck_id != e.fuel_truck_id or w.physical_fueler_user_id != e.fueler_user_id
                or timestamp(w.off_at_utc) != timestamp(e.ended_at_utc)
                or timestamp(w.manual_on_at_utc or w.confirmed_on_at_utc) != timestamp(e.started_at_utc)):
            reasons.append('Finalized physical visit identity/timing not verified')
        row = off_result(e, identity_reasons=reasons)
        off.append(row)
        if not row['physical_reasons']:
            events[e.id] = e
    archive_note = 'Canonical database evidence; no Vault fetch required.'
    if archive is not None:
        if (archive['gateway']['id'] != gateway.id or not operation
                or archive['sort']['operation_id'] != operation.id):
            raise ValueError('Vault record does not belong to the selected gateway and sort.')
        timelines = {t['event_id']:t for t in archive.get('operational_timelines', [])}
        present = {j['event'].id for j in off}
        added = 0
        for saved in archive.get('fuel_events', []):
            if saved['id'] in present:
                continue  # R2 copies are evidence, not additional outcomes.
            timeline = timelines.get(saved['id'], {})
            e = SimpleNamespace(id=saved['id'], fuel_assignment_id=archive['assignment']['id'],
                fuel_work_state_id=saved['work_state_id'], tail_number=saved['tail_number'],
                fuel_truck_id=saved['truck_id'], fueler_user_id=saved['fueler_user_id'],
                cycle_number=saved['cycle_number'], required_fuel_lbs=saved['required_fuel_lbs'],
                started_at_utc=timestamp(saved['started_at_utc']), ended_at_utc=timestamp(saved['ended_at_utc']),
                event_type=saved['event_type'], transfer_fuel_gallons=saved['transfer_fuel_gallons'],
                performance_snapshot=timeline.get('facts') or {})
            reasons = list(archive.get('exclusion_reasons', [])) + list(timeline.get('exclusions', []))
            if not archive.get('training_eligible'):
                reasons.append('Archived record is not verified for learning')
            off.append(off_result(e, identity_reasons=reasons)); present.add(e.id); added += 1
        archive_note = f'Checksum-validated R2 record loaded: {added} additional events; canonical duplicates excluded.'
    top_rows = NeoScorpionTopOffEvent.query.filter_by(sort_date_operation_id=operation.id).order_by(
        NeoScorpionTopOffEvent.occurred_at_utc, NeoScorpionTopOffEvent.id).limit(LIMIT+1).all() if operation else []
    truncated |= len(top_rows) > LIMIT
    top_rows = top_rows[:LIMIT]
    returns = {r.sent_event_id:r for r in top_rows if r.kind == 'returned' and r.sent_event_id}
    top = [top_off_result(s, returns.get(s.id)) for s in top_rows if s.kind == 'sent']
    records = NeoScorpionSpearObservation.query.filter_by(gateway_id=gateway.id, operation_id=operation.id).filter(
        NeoScorpionSpearObservation.kind.in_(['decision', 'outcome'])).order_by(
        NeoScorpionSpearObservation.created_at, NeoScorpionSpearObservation.id).limit(LIMIT+1).all() if operation else []
    truncated |= len(records) > LIMIT
    outcomes, decisions, invalid = [], [], 0
    valid = []
    for item in records[:LIMIT]:
        try:
            validate_record(item.payload)
            if (item.payload['gateway_id'] != gateway.id or item.payload['operation_id'] != operation.id
                    or item.payload['id'] != item.id or item.payload['assignment_id'] != item.assignment_id
                    or item.payload['mission_id'] != item.mission_id or item.payload['cycle_number'] != item.cycle_number
                    or item.payload['record_type'] != item.kind):
                raise ValueError('Observation identity mismatch')
            valid.append(item)
        except (ValueError, TypeError, KeyError, AttributeError):
            invalid += 1
    outcomes = [i.payload for i in valid if i.kind == 'outcome']
    for item in valid:
        if item.kind == 'decision':
            try:
                d = decision_result(item.payload, outcomes, events)
                d['vault_key'] = item.vault_key
                decisions.append(d)
            except (ValueError, TypeError, KeyError, AttributeError):
                invalid += 1
    # Multiple decisions referring to one outcome must not overweight risk results.
    decisions.sort(key=lambda d:(d['at'] or timestamp('1900-01-01'), d['id']))
    seen = set()
    for d in decisions:
        if d['actual_risk'] is not None:
            if d['work_id'] in seen:
                d['actual_risk'] = None; d['reasons'].append('Visit already represented by an earlier decision')
            else:
                seen.add(d['work_id'])
    trucks = {i.id:i.truck_number for i in NeoScorpionFuelTruck.query.filter_by(gateway_id=gateway.id).all()
              if i.id in {r['event'].fuel_truck_id for r in off} | {r['truck_id'] for r in top}
              | {d['selected'].get('truck_id') for d in decisions}}
    fueler_ids = {r['event'].fueler_user_id for r in off if r['event'].fueler_user_id} | {
        d['selected'].get('fueler_id') for d in decisions if d['selected'].get('fueler_id')}
    fuelers = {u.id:u.display_name for u in User.query.filter(User.id.in_(fueler_ids)).all()} if fueler_ids else {}
    if truck_id is not None and truck_id not in trucks:
        raise ValueError('No recorded truck evidence for this selection.')
    if fueler_id is not None and fueler_id not in fuelers:
        raise ValueError('No recorded Fueler evidence for this selection.')
    off = [r for r in off if (truck_id is None or r['event'].fuel_truck_id == truck_id)
           and (fueler_id is None or r['event'].fueler_user_id == fueler_id)]
    top = [r for r in top if truck_id is None or r['truck_id'] == truck_id] if fueler_id is None else []
    decisions = [d for d in decisions if (truck_id is None or d['selected'].get('truck_id') == truck_id)
                 and (fueler_id is None or d['selected'].get('fueler_id') == fueler_id)]
    off.sort(key=lambda r:(r['at'] or timestamp('1900-01-01'), r['key']))
    trend = trend_summary(off)
    from app.services.gateway_matrix import gateway_timezone
    tz = ZoneInfo(gateway_timezone(gateway))
    def clock(value):
        from datetime import timezone
        return timestamp(value).replace(tzinfo=timezone.utc).astimezone(tz).strftime('%m/%d %H:%M %Z') if timestamp(value) else '—'
    sent_ids = {s.id for s in top_rows if s.kind == 'sent'}
    return dict(off=off, top=top, decisions=decisions, off_summary=summary(off), top_summary=summary(top),
                risks=[risk_summary(decisions, label) for label in ('AT RISK', 'LATE')],
                comparisons=Counter(d['comparison'] for d in decisions), invalid=invalid,
                trucks=trucks, fuelers=fuelers, truncated=truncated, trend=trend, clock=clock,
                archive_note=archive_note, fueler_id=fueler_id, truck_id=truck_id,
                orphan_returns=sum(r.kind == 'returned' and r.sent_event_id not in sent_ids
                                   and (truck_id is None or r.fuel_truck_id == truck_id)
                                   for r in top_rows) if fueler_id is None else 0)
