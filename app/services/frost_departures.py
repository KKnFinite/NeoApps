"""Compact MotherBrain facts, never a second departure authority.

Canonical ORM transactions stage revisions atomically; the existing outbox
delivers them later. Missing treatment evidence is ALWAYS UNKNOWN. Historical
discovery is keyset-bounded and never reconstructs a tail from today's roster.
"""
from datetime import datetime, timedelta, timezone
import gc
from types import SimpleNamespace
from zoneinfo import ZoneInfo
from sqlalchemy import event, inspect, select, insert, update
from sqlalchemy.orm import Session
from flask import has_app_context

from app.extensions import db
from app.models import (FrostHistoryTask as Task, Gateway, SortDateMission as Mission,
    SortDateParkingAssignment as Parking, SortDateOperation as Operation)
from app.services import frost_vault as vault

VERSION = 'frost-departures-v1'
POLICY = 'tail-unique-start-minus6h-plus2h-pretreat24h-v1'
SOURCES = frozenset(('manual', 'neorain', 'google_rain', 'google_motherbrain'))
FIELDS = ('sort_date_operation_id', 'flight_number', 'assigned_tail_number',
    'actual_block_out_datetime_utc', 'actual_block_out_source', 'departure_status',
    'sort_date', 'gateway_code', 'sort_name', 'mission_type', 'tail_updated_at')


def stamp(value):
    return value.replace(tzinfo=timezone.utc).isoformat() if value else None


def stage(connection, mission, *, deleted=False, historical=False):
    """Only compact facts in Neon. No provider I/O or outbox commit here."""
    # Discovery may have loaded a mission before another writer committed.
    # Serialize in canonical mission -> compact index order and re-read facts.
    fresh=connection.execute(select(*(getattr(Mission,key) for key in ('id',)+FIELDS))
        .where(Mission.id==mission.id).with_for_update()).mappings().first()
    if fresh:mission=SimpleNamespace(**fresh)
    elif not deleted:return
    table = Task.__table__
    gateway_id = connection.scalar(select(Gateway.id).where(Gateway.code == 'RFD'))
    if not gateway_id:return
    identity = str(mission.id)
    where = (table.c.gateway_id == gateway_id, table.c.kind == 'departure_index',
        table.c.identity == identity, table.c.version == VERSION)
    old = connection.execute(select(table).where(*where).with_for_update()).mappings().first()
    if not old and (mission.gateway_code != 'RFD' or mission.mission_type != 'departure'):return
    at = mission.actual_block_out_datetime_utc
    if not old and not at:return
    previous = (old['summary'] or {}).get('payload', {}) if old else {}
    tail = (mission.assigned_tail_number or '').strip().upper() or None
    source = mission.actual_block_out_source or 'unknown'
    # Once recorded, a later roster swap cannot rewrite the departed aircraft.
    same_actual = at and previous.get('block_out_utc') == stamp(at)
    if (same_actual or not at) and previous.get('tail'):
        tail = previous['tail']
        tail_quality = previous['tail_quality']
    else:
        tail_quality = 'canonical_at_actual' if (at and tail and mission.tail_updated_at and
            mission.tail_updated_at <= at) else 'requires_review'
    state = 'qualified'
    operation=connection.execute(select(Operation.gateway_id,Operation.gateway_code,Operation.sort_date,Operation.sort_name)
        .where(Operation.id==mission.sort_date_operation_id)).first()
    if (source not in SOURCES or at and at > datetime.utcnow() or tail_quality != 'canonical_at_actual' or
            not operation or operation.gateway_code!='RFD' or operation.gateway_id not in (None,gateway_id) or
            operation.sort_date!=mission.sort_date or operation.sort_name!=mission.sort_name):state = 'requires_review'
    # A changed time accompanied by a changed tail is not proof of a correction
    # to the physical aircraft. Keep the old revision and require review.
    if previous.get('tail') and tail != previous['tail']:state = 'requires_review'
    if same_actual and (previous.get('operation_id') != mission.sort_date_operation_id or
            previous.get('flight') != mission.flight_number):state = 'requires_review'
    if (deleted or not at or mission.departure_status == 'cancelled' or
        mission.gateway_code != 'RFD' or mission.mission_type != 'departure'):state = 'withdrawn'
    parking = previous.get('parking') if same_actual else None
    if at and tail and parking is None:
        row = connection.execute(select(Parking.ramp_code, Parking.position_code, Parking.lane_number)
            .where(Parking.sort_date_operation_id == mission.sort_date_operation_id,
                Parking.tail_number == tail, Parking.updated_at <= at)).first()
        if row:parking = dict(ramp=row.ramp_code, position=row.position_code,lane=row.lane_number)
    exposure = previous.get('exposure') if same_actual else None
    if at and tail and exposure is None:
        arrivals=connection.execute(select(Mission.id,Mission.actual_block_in_datetime_utc,
            Mission.actual_block_in_source).where(Mission.sort_date_operation_id==mission.sort_date_operation_id,
                Mission.mission_type=='arrival',Mission.assigned_tail_number==tail,
                Mission.arrival_status.is_distinct_from('cancelled'),
                Mission.actual_block_in_source.in_(SOURCES),
                Mission.actual_block_in_datetime_utc<=at,
                Mission.actual_block_in_datetime_utc>=at-timedelta(hours=24),
                Mission.tail_updated_at<=Mission.actual_block_in_datetime_utc).limit(2)).all()
        if len(arrivals)==1:
            arrival=arrivals[0]
            exposure=dict(arrival_mission_id=arrival.id,block_in_utc=stamp(arrival.actual_block_in_datetime_utc),
                source=arrival.actual_block_in_source,minutes=(at-arrival.actual_block_in_datetime_utc).total_seconds()/60)
    payload = dict(schema=VERSION, mission_id=mission.id, operation_id=mission.sort_date_operation_id,
        gateway='RFD', sort_date=str(mission.sort_date), sort_name=mission.sort_name,
        flight=mission.flight_number, tail=tail, tail_quality=tail_quality,
        block_out_utc=stamp(at), source=source, state=state, parking=parking,
        exposure=exposure, exposure_quality='same_operation_single_verified_arrival' if exposure else 'not_verified', outcome='UNKNOWN')
    digest = vault.digest(vault.canonical(payload))
    if old and old['input_hash'] == digest:return
    now = datetime.utcnow()
    revision = vault.digest(vault.canonical([identity, old['input_hash'] if old else None, digest,
        (old['summary'] or {}).get('revision') if old else None]))[:32]
    receipt = dict(payload=payload, observed_at_utc=stamp(now), historical_discovery=historical,
        previous_revision=(old['summary'] or {}).get('revision') if old else None)
    values = dict(gateway_id=gateway_id, kind='departure_revision', identity=revision, version=VERSION,
        input_hash=vault.digest(vault.canonical(receipt)), status='pending', summary=receipt,
        attempts=0, retry_at=now, updated_at=now)
    connection.execute(insert(table).values(**values))
    connection.execute(update(table).where(table.c.gateway_id==gateway_id,
        table.c.kind=='departure_match', table.c.version==VERSION,
        table.c.summary['mission_id'].as_integer()==mission.id).values(status='superseded'))
    index = dict(input_hash=digest, status=state, summary=dict(payload=payload, revision=revision),
        retry_at=at or now, updated_at=now)
    if old:connection.execute(update(table).where(table.c.id == old['id']).values(**index))
    else:connection.execute(insert(table).values(gateway_id=gateway_id, kind='departure_index', identity=identity,
        version=VERSION, attempts=0, **index))


@event.listens_for(Session, 'before_flush')
def remember(session, _context, _instances):
    if not has_app_context():return
    changed = [m for m in session.new.union(session.dirty).union(session.deleted)
        if isinstance(m, Mission) and (m.actual_block_out_datetime_utc or
            inspect(m).attrs.actual_block_out_datetime_utc.history.has_changes() or m in session.deleted) and
            (m in session.new or m in session.deleted or
            any(inspect(m).attrs[key].history.has_changes() for key in FIELDS))]
    if changed:session.info['frost_departures'] = [(m, m in session.deleted) for m in changed]


@event.listens_for(Session, 'after_flush_postexec')
def record(session, _context):
    for mission, deleted in sorted(session.info.pop('frost_departures', []),key=lambda item:item[0].id):
        stage(session.connection(), mission, deleted=deleted)


@event.listens_for(Session, 'after_soft_rollback')
def discard(session, _previous):
    session.info.pop('frost_departures', None)


def correlate(gateway_id, outcome):
    """One RecordID/revision, conservative unique time relation, not tail/date.

    Departure application START may precede stand Block-Out by <=6h or follow
    it by <=2h (pad). Pretreat may precede Block-Out by <=24h, never follows it.
    Multiple eligible departures => review, never nearest-time guessing.
    """
    from app.services import frost_auto as auto, frost_history as jobs
    data = outcome.summary or {}
    row = jobs.task(gateway_id, 'departure_match', outcome.identity, VERSION)
    at = auto.clock.instant(data['event_at_utc']) if data.get('event_at_utc') else None
    pretreat = data.get('classification', {}).get('activity') == 'PRETREAT'
    candidates = []
    if at and data.get('tail') and outcome.status != 'requires_review':
        lower = at if pretreat else at-timedelta(hours=2)
        upper = at+timedelta(hours=24 if pretreat else 6)
        candidates = Task.query.filter_by(gateway_id=gateway_id, kind='departure_index',
            version=VERSION, status='qualified').filter(
            Task.summary['payload']['tail'].as_string() == data['tail'],
            Task.retry_at >= auto.naive(lower), Task.retry_at <= auto.naive(upper)).order_by(Task.id).limit(6).with_for_update().all()
    match = candidates[0] if len(candidates) == 1 else None
    state = 'matched' if match else 'requires_review' if len(candidates)>1 or outcome.status=='requires_review' else 'unknown'
    row.input_hash = outcome.input_hash
    row.source_key = outcome.source_key
    summary = dict(policy=POLICY, record_identity=outcome.identity, source=data.get('source'),
        source_revision=outcome.input_hash, event_at_utc=data.get('event_at_utc'),
        independent_night=(at.astimezone(ZoneInfo('America/Chicago'))-timedelta(hours=12)).date().isoformat() if at else None,
        classification=data.get('classification'), candidate_count=len(candidates),
        departure_revision=match.summary['revision'] if match else None,
        mission_id=match.summary['payload']['mission_id'] if match else None,
        departure_fingerprint=match.input_hash if match else None,
        capture_ids=data.get('capture_ids', []), outcome='UNKNOWN' if not match else 'RECORDED TREATMENT')
    digest=vault.digest(vault.canonical(dict(state=state,summary=summary)))
    if row.summary.get('decision_hash') != digest:
        receipt=jobs.task(gateway_id,'departure_link',vault.digest(
            (outcome.identity+digest).encode())[:32],VERSION)
        if not receipt.input_hash:
            receipt.summary=dict(schema=VERSION,state=state,decision=summary,created_at_utc=auto.stamp(auto.now()))
            receipt.input_hash=vault.digest(vault.canonical(receipt.summary))
    row.status=state;row.summary=dict(summary,decision_hash=digest)
    row.updated_at = datetime.utcnow()
    return row


def drain(gateway_id):
    """One shared lease, <=50 discovery rows, <=1 archive delivery per wake."""
    from app.services import frost_auto as auto, frost_history as jobs
    if not jobs.memory_allows_work():return 0
    gateway = db.session.get(Gateway, gateway_id)
    if not gateway or gateway.code != 'RFD':return 0
    batch = jobs.task(gateway_id, 'worker', 'memory-budget', 'frost-worker-v1')
    batch_id = batch.id; token = jobs.claim(batch)
    if not token:return 0
    try:
        cursor = jobs.task(gateway_id, 'departure_control', 'discovery', VERSION)
        after = (cursor.summary or {}).get('last_mission_id', 0)
        missions = Mission.query.filter(Mission.id>after, Mission.gateway_code=='RFD',
            Mission.mission_type=='departure').order_by(Mission.id).limit(50).all()
        for mission in missions:stage(db.session.connection(), mission, historical=True)
        cursor.summary = dict(last_mission_id=missions[-1].id if missions else after,
            discovery_complete=bool((cursor.summary or {}).get('discovery_complete') or len(missions)<50),
            last_reconciliation_utc=auto.stamp(auto.now()))
        db.session.commit()
        receipt = Task.query.filter_by(gateway_id=gateway_id, version=VERSION).filter(
            Task.kind.in_(('departure_revision','departure_link')),
            Task.status.in_(('pending','retry')), Task.retry_at<=datetime.utcnow()).order_by(Task.id).first()
        if receipt:
            identifier=receipt.id; lease=jobs.claim(receipt)
            if lease:
                try:
                    payload=receipt.summary
                    with vault.transport_scope():
                        key=vault.put_json('frost-observations',gateway_id,'departure-'+receipt.identity,payload)
                        if vault.digest(vault.canonical(vault.read_json(key,gateway_id,max_bytes=16384)))!=receipt.input_hash:
                            raise ValueError('Departure archive read-back mismatch.')
                    jobs.finish(identifier,lease,status='ready',source_key=key,result_key=key)
                except Exception as error:jobs.defer(identifier,lease,error)
                finally:gc.collect()  # Closed SDK graphs must not accumulate between minute wakes.
        # Corrections/late departures reconcile a bounded keyset of existing
        # Xchange identities. No source-page downloads or historical analysis.
        cursor = jobs.task(gateway_id, 'departure_control', 'outcomes', VERSION)
        after = (cursor.summary or {}).get('last_outcome_id',0)
        outcomes=Task.query.filter_by(gateway_id=gateway_id,kind='xchange_outcome',version=auto.VERSION).filter(
            Task.id>after).order_by(Task.id).limit(50).all()
        for outcome in outcomes:correlate(gateway_id,outcome)
        cursor.summary=dict(last_outcome_id=outcomes[-1].id if len(outcomes)==50 else 0)
        db.session.commit()
        return len(missions)+bool(receipt)
    finally:jobs.finish(batch_id,token,status='ready',retry_at=datetime.utcnow())


def status(gateway_id):
    from sqlalchemy import func
    from sqlalchemy.orm import aliased
    def counts(kind):return dict(db.session.query(Task.status,func.count(Task.id)).filter_by(
        gateway_id=gateway_id,kind=kind,version=VERSION).group_by(Task.status).all())
    departures=counts('departure_index');matches=counts('departure_match')
    qualified=departures.get('qualified',0)
    matched=Task.query.filter_by(gateway_id=gateway_id,kind='departure_match',version=VERSION,status='matched')
    # Count unique aircraft departures, not truck-level Xchange contributions.
    linked=db.session.query(func.count(func.distinct(Task.summary['mission_id'].as_integer()))).filter(
        Task.gateway_id==gateway_id,Task.kind=='departure_match',Task.version==VERSION,Task.status=='matched').scalar()
    frost=matched.filter(Task.summary['classification']['label'].as_string()=='LIKELY FROST PRESENT').count()
    other=matched.filter(Task.summary['classification']['label'].as_string()=='NON-FROST TREATMENT').count()
    cursor=Task.query.filter_by(gateway_id=gateway_id,kind='departure_control',identity='discovery',version=VERSION).one_or_none()
    from app.services.frost_inspections import population
    physical=population(db.session.get(Gateway,gateway_id))
    from app.models import (NeoSubZeroAuditEntry as Audit, SortDateOperation as Operation,
        NeoSubZeroDepartureDeiceEvent as Event)
    no_spray=db.session.query(func.count(func.distinct(Audit.after_values['frost_learning']['event_id'].as_integer()))).join(
        Operation,Operation.id==Audit.sort_date_operation_id).join(Event,
        Event.id==Audit.after_values['frost_learning']['event_id'].as_integer()).filter(Operation.gateway_id==gateway_id,
        Event.status.in_(('not_sprayed','cleared')),Event.pass1_started_at_utc.is_(None),
        Audit.after_values['frost_learning']['classification']['label'].as_string()=='LIKELY FROST ABSENT').scalar()
    forecast=aliased(Task)
    forecast_matches=db.session.query(func.count(func.distinct(forecast.summary['capture_id'].as_integer()))).select_from(Task).join(
        forecast,forecast.summary['record_identity'].as_string()==Task.identity).filter(
            Task.gateway_id==gateway_id,Task.kind=='departure_match',Task.version==VERSION,Task.status=='matched',
            forecast.gateway_id==gateway_id,forecast.kind=='auto_match',forecast.status=='active')
    linked_forecasts=forecast_matches.scalar()
    scored=forecast_matches.filter(forecast.summary['score_available'].as_boolean().is_(True)).scalar()
    discovery_complete=bool(cursor and (cursor.summary or {}).get('discovery_complete'))
    return dict(state=('DEPARTURE EVIDENCE REQUIRES REVIEW' if departures.get('requires_review',0) else
            'DEPARTURE DISCOVERY IN PROGRESS' if cursor and not discovery_complete else 'NO DEPARTURES RECORDED') if not qualified else
        'DEPARTURES WITH VERIFIED OUTCOME EVIDENCE' if linked else 'DEPARTURES RECORDED — TREATMENT OUTCOME UNKNOWN',
        actual=qualified,linked=linked,unknown=max(0,qualified-linked),frost=frost,other=other,
        review=departures.get('requires_review',0)+matches.get('requires_review',0),
        withdrawn=departures.get('withdrawn',0),archived=counts('departure_revision').get('ready',0),
        physical_present=physical['physical_present'],physical_absent=physical['physical_absent'],no_spray=no_spray,
        forecasts=linked_forecasts,scored_forecasts=scored,unavailable_forecasts=max(0,linked_forecasts-scored),
        discovery='Initial bounded discovery complete' if discovery_complete else 'Initial discovery pending/in progress; counts are indexed evidence so far, not all historical departures',
        last=(cursor.summary or {}).get('last_reconciliation_utc') if cursor else None)
