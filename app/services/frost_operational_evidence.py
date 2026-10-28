"""Compact outcome evidence, not a second permanent aircraft activity log.

Captured in the existing authorized transaction/audit; delivered by the existing
SubZero outbox. No network or added coordinator controls in CLEAR/FIN.
"""
from datetime import timezone
from sqlalchemy import func
from app.models import (NeoSubZeroAuditEntry, NeoSubZeroDepartureDeiceEvent,
    NeoSubZeroPretreatState, NeoSubZeroSprayRecord, SortDateMission,
    SortDateParkingAssignment, SortDateTailState)
from app.services import frost_analysis as analysis

VERSION='frost-outcome-evidence-v1'


def stamp(at):
    return at.replace(tzinfo=timezone.utc).isoformat() if at else None


def capture(operation,mission,event,*,actor_id,action,previous):
    # Generic CLEAR, NEGATIVE, missing treatment, cancellation and plans are NOT
    # no-frost observations. Require the explicit existing NOT SPRAYED action.
    explicit=action=='manual_not_sprayed' or (action=='clear' and previous and previous.get('status')=='not_sprayed')
    if not explicit or not actor_id or not event.tail_number:return None
    tail=event.tail_number.strip().upper()
    block=mission.actual_block_out_datetime_utc
    if str(mission.assigned_tail_number or '').strip().upper()!=tail or mission.departure_status=='cancelled' or not block:
        return None
    from datetime import datetime
    now=datetime.utcnow()
    if block>now or (mission.tail_updated_at and mission.tail_updated_at>block):return None
    events=NeoSubZeroDepartureDeiceEvent.query.filter_by(sort_date_operation_id=operation.id).filter(
        func.upper(NeoSubZeroDepartureDeiceEvent.tail_number)==tail).all()
    if any(e.pass1_started_at_utc or e.pass2_started_at_utc for e in events):return None
    if NeoSubZeroSprayRecord.query.filter_by(sort_date_operation_id=operation.id,tail_number_snapshot=tail).filter(
        NeoSubZeroSprayRecord.departure_deice_event_id.is_not(None),NeoSubZeroSprayRecord.started_at_utc.is_not(None)).first():return None
    state=SortDateTailState.query.filter_by(sort_date=operation.sort_date,gateway_code=operation.gateway_code,
        sort_name=operation.sort_name,tail_number=tail).one_or_none()
    pretreat=NeoSubZeroPretreatState.query.filter_by(sort_date_operation_id=operation.id,tail_number=tail).first()
    actual_prior=bool(pretreat and (pretreat.pass1_started_at_utc or pretreat.pass2_started_at_utc))
    prior=True if actual_prior or (state and state.pretreat_status) else False if state else None
    # A same-tail actual inbound is evidence of presence, not a guessed parking
    # duration. A later tail swap invalidates that arrival attribution.
    arrivals=SortDateMission.query.filter_by(sort_date_operation_id=operation.id,mission_type='arrival',assigned_tail_number=tail).all()
    arrivals=[r for r in arrivals if r.arrival_status!='cancelled' and r.actual_block_in_datetime_utc and
        r.actual_block_in_datetime_utc<block and (not r.tail_updated_at or r.tail_updated_at<=r.actual_block_in_datetime_utc)]
    arrival=max(arrivals,key=lambda r:r.actual_block_in_datetime_utc) if arrivals else None
    parking=SortDateParkingAssignment.query.filter_by(sort_date_operation_id=operation.id,tail_number=tail).one_or_none()
    if parking and (not parking.updated_at or parking.updated_at>block):parking=None
    exposure=(block-arrival.actual_block_in_datetime_utc).total_seconds()/3600 if arrival else None
    label=analysis.classification(None,actual_spray=False,verified_not_sprayed=bool(arrival),prior_pretreat=prior)
    return dict(schema_version=VERSION,record_type='explicit_no_treatment_evidence',
        operation_id=operation.id,sort_date=operation.sort_date.isoformat(),sort_name=operation.sort_name,
        tail=tail,mission_id=mission.id,outcome='explicit_not_sprayed',actor_user_id=actor_id,
        observed_at_utc=stamp(now),block_out_at_utc=stamp(block),block_out_source=mission.actual_block_out_source,
        arrival_mission_id=arrival.id if arrival else None,
        block_in_at_utc=stamp(arrival.actual_block_in_datetime_utc) if arrival else None,
        block_in_source=arrival.actual_block_in_source if arrival else None,
        presence_basis='canonical actual Block-Out with unchanged physical tail',
        exposure_hours=exposure,exposure_basis='same-tail actual Block-In to Block-Out; not measured surface exposure' if arrival else None,
        parking=dict(ramp=parking.ramp_code,position=parking.position_code,recorded_at_utc=stamp(parking.updated_at),
            basis='canonical assignment last updated before Block-Out; exact duration at stand unknown') if parking else None,
        prior_pretreat=prior,prior_treatment_scope='current canonical sort only; outside-system history unknown',
        classification=label,physical_frost_confirmed=None,
        learning_eligible=bool(arrival and prior is not None),
        limitation='No measured aircraft surface condition. Pretreatment is a confounder; outcome is likely absence evidence, not physical confirmation.')


def payloads(operation):
    """Only explicitly captured prospective observations; no legacy backfill."""
    audits=NeoSubZeroAuditEntry.query.filter_by(sort_date_operation_id=operation.id).filter(
        NeoSubZeroAuditEntry.action.in_(('departure_manual_not_sprayed','departure_clear'))).order_by(NeoSubZeroAuditEntry.id).all()
    latest={}
    for audit in audits:
        evidence=(audit.after_values or {}).get('frost_learning')
        if evidence and evidence.get('schema_version')==VERSION:
            latest[audit.event_id]=(audit,evidence)
    result={}
    for event_id,(audit,evidence) in latest.items():
        event=NeoSubZeroDepartureDeiceEvent.query.filter_by(id=event_id,sort_date_operation_id=operation.id).one_or_none()
        active=bool(event and event.status in ('not_sprayed','cleared') and not event.pass1_started_at_utc and not event.pass2_started_at_utc)
        # Keep prior immutable revisions but supersede them if later work began.
        other=NeoSubZeroDepartureDeiceEvent.query.filter_by(sort_date_operation_id=operation.id,tail_number=evidence['tail']).all()
        active=active and not any(e.pass1_started_at_utc or e.pass2_started_at_utc for e in other)
        mission=SortDateMission.query.filter_by(id=evidence['mission_id'],sort_date_operation_id=operation.id).one_or_none()
        active=active and bool(mission and mission.departure_status!='cancelled')
        result[f'frost:{event_id}:outcome:0']=dict(evidence,id=f'frost:{event_id}:outcome:0',
            source_audit_id=audit.id,source_event_id=event_id,evidence_active=active,
            learning_eligible=active and evidence['learning_eligible'])
    return result
