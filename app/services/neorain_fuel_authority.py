"""Independent authoritative fuel values for NeoRain outbound missions."""

from __future__ import annotations

import hashlib

from sqlalchemy import event as orm_event, inspect, select, or_
from sqlalchemy.orm import Session

from app.extensions import db
from app.models import (
    MotherBrainGoogleIntegrationSetting,
    NeoRainFuelReviewAcknowledgement,
    NeoRainFuelPublication,
    NeoRainGoogleFuelValue,
    NeoScorpionFuelAssignment,
    NeoScorpionFuelingEvent,
    NeoScorpionDirectEntry,
    SortDateMission,
)
from app.services.google_rain_integration_mode import (
    DEFAULT_RAIN_SORT,
    ensure_rain_integration_setting,
)


RAIN_FUEL_SOURCE_GOOGLE = "google"
RAIN_FUEL_SOURCE_NEO = "neo"
RAIN_FUEL_SOURCES = (RAIN_FUEL_SOURCE_GOOGLE, RAIN_FUEL_SOURCE_NEO)


def rain_fuel_data_source(gateway, sort_name=DEFAULT_RAIN_SORT):
    if gateway is None:
        return RAIN_FUEL_SOURCE_GOOGLE
    row = MotherBrainGoogleIntegrationSetting.query.filter_by(
        gateway_id=gateway.id, sort_name=_sort_name(sort_name)
    ).first()
    return _source(getattr(row, "rain_fuel_data_source", None))


def rain_fuel_data_source_status(gateway, sort_name=DEFAULT_RAIN_SORT):
    source = rain_fuel_data_source(gateway, sort_name)
    return {
        "source": source,
        "source_label": source.upper(),
        "sources": (
            {"value": RAIN_FUEL_SOURCE_GOOGLE, "label": "GOOGLE"},
            {"value": RAIN_FUEL_SOURCE_NEO, "label": "NEO"},
        ),
    }


def set_rain_fuel_data_source(gateway, sort_name, source):
    normalized = _source(source, strict=True)
    setting = ensure_rain_integration_setting(gateway, sort_name)
    setting.rain_fuel_data_source = normalized
    db.session.flush()
    return setting


def completed_scorpion_fuel_by_mission(operation, *, include_identity=False):
    """Return final fuel snapshots keyed only by canonical mission id.

    A Scorpion event is publishable only when its exact assignment is complete;
    fueler OFF alone deliberately cannot pass this boundary.
    """
    if operation is None:
        return {}
    rows = (
        db.session.query(NeoScorpionFuelAssignment, NeoScorpionFuelingEvent)
        .join(
            NeoScorpionFuelingEvent,
            NeoScorpionFuelingEvent.fuel_assignment_id == NeoScorpionFuelAssignment.id,
        )
        .filter(
            NeoScorpionFuelAssignment.sort_date_operation_id == operation.id,
            NeoScorpionFuelAssignment.completed_at_utc.isnot(None),
        )
        .order_by(
            NeoScorpionFuelAssignment.sort_date_mission_id,
            NeoScorpionFuelingEvent.cycle_number.desc(),
            NeoScorpionFuelingEvent.sequence_number.desc(),
            NeoScorpionFuelingEvent.id.desc(),
        )
        .populate_existing().all()
    )
    publications = {
        row.sort_date_mission_id: row
        for row in NeoRainFuelPublication.query.filter_by(sort_date_operation_id=operation.id).populate_existing().all()
    }

    values = {}
    for assignment, event in rows:
        mission_id = assignment.sort_date_mission_id
        if mission_id in values:
            continue
        if event.neo_fuel_lbs is None and event.center_fuel_lbs is None:
            continue

        publication = publications.get(mission_id)
        change_number = int(publication.change_number) if publication else 0
        # Read-only fallback for a legacy/raw writer. The normal ORM write path
        # persists every value change atomically. Never infer a change from time.
        if publication and (event.neo_fuel_lbs, event.center_fuel_lbs) != (
                publication.neo_fuel_lbs, publication.center_fuel_lbs):
            change_number += 1
        revision = _fuel_revision(mission_id, change_number, event.neo_fuel_lbs, event.center_fuel_lbs)
        values[mission_id] = {
            "neo_fuel": _fuel_value(event.neo_fuel_lbs),
            "center_fuel": _fuel_value(event.center_fuel_lbs),
            "revision": revision,
            "is_correction": change_number > 0,
            **({"tail": event.tail_number, "cycle": event.cycle_number} if include_identity else {}),
        }
    # Direct-entry attestations are a canonical completed source, but never a
    # physical fueling event or inventory transfer. Unknown APU stays unknown.
    from app.services.neoscorpion_direct_entry import completed_values
    for record,mission in db.session.query(NeoScorpionDirectEntry,SortDateMission).join(
            SortDateMission,SortDateMission.id==NeoScorpionDirectEntry.sort_date_mission_id).filter(
            NeoScorpionDirectEntry.sort_date_operation_id==operation.id,
            NeoScorpionDirectEntry.gateway_id==operation.gateway_id,
            SortDateMission.fuel_status=='complete').all():
        if record.tail_number != str(mission.assigned_tail_number or '').strip().upper():
            continue
        neo,center=completed_values(record)
        values.setdefault(mission.id,dict(neo_fuel=_fuel_value(neo),center_fuel=_fuel_value(center),
            revision=_fuel_revision(mission.id,0,neo,center),is_correction=False,
            **({'tail':record.tail_number,'cycle':1,'source':'dispatcher_direct_entry'} if include_identity else {})))
    return values


def scorpion_board_fuel_by_mission(operation, missions):
    """Read-only board display; canonical Scorpion states, never tail-only joins.

    Keep Rain's source-selected Center, review/publication and reporting data
    independent. Only an administratively completed current aircraft/cycle may
    display a final Neo quantity; an in-progress uplift cannot reuse old fuel.
    """
    if operation is None or not missions:
        return {}
    missions = tuple(m for m in missions if m.sort_date_operation_id == operation.id and m.mission_type == 'departure')
    if not missions:
        return {}
    from app.models import NeoScorpionSettings
    from app.services import neoscorpion as scorpion
    assignments = scorpion._assignments_by_mission(operation)
    works = scorpion._fuel_work_states_by_assignment_tail(assignments.values())
    settings = NeoScorpionSettings.query.filter_by(gateway_id=operation.gateway_id).first()
    rates, limits = scorpion._effective_aircraft_fuel_configuration(operation.gateway_id)
    rows = scorpion._fuel_rows(operation, missions, assignments_by_mission=assignments,
        fuel_work_states_by_assignment_tail=works, status_settings=settings,
        apu_rates_by_aircraft_type=rates, lateral_imbalance_limits_by_aircraft_type=limits)
    finals = completed_scorpion_fuel_by_mission(operation, include_identity=True)
    result = {}
    for row in rows:
        mission = row['mission']; assignment = assignments.get(mission.id)
        final = finals.get(mission.id, {})
        verified = bool(row['administratively_complete'] and assignment and final.get('neo_fuel')
            and scorpion._normalize_tail(final.get('tail')) == scorpion._normalize_tail(mission.assigned_tail_number)
            and int(final.get('cycle') or 1) == int(assignment.current_cycle_number or 1))
        quantity = scorpion.format_display_thousands(str(final['neo_fuel']).replace(',', '')) if verified else None
        # No-truck FOB has no physical transfer event. Use only Scorpion's
        # completed, verified measured/inherited assessment, never its estimate.
        if row['fuel_on_board_complete'] and row['fob_verified'] and row['fob_projected_neo_lbs'] is not None:
            verified = True
            quantity = scorpion.format_display_thousands(row['fob_projected_neo_lbs'])
        if row.get('direct_entry_record') and row['administratively_complete'] and row['neo_fuel_lbs'] is not None:
            verified=True
            quantity=scorpion.format_display_thousands(row['neo_fuel_lbs'])
        result[mission.id] = dict(
            scorpion_fuel_display=quantity if verified else row['dispatch_status_label'],
            scorpion_fuel_status=row['dispatch_status_key'], scorpion_fuel_final=verified,
            scorpion_fuel_detail=row.get('mission_data_summary') or row.get('dispatch_status_detail') or '')
    return result


def google_rain_fuel_by_mission(operation):
    if operation is None:
        return {}
    return {
        row.sort_date_mission_id: {
            "neo_fuel": row.neo_fuel or "",
            "center_fuel": row.center_fuel or "",
            "revision": "",
        }
        for row in NeoRainGoogleFuelValue.query.filter_by(
            sort_date_operation_id=operation.id
        ).all()
    }


def record_google_rain_fuel_value(operation, mission, neo_fuel, center_fuel):
    """Stage Google-owned display values; callers retain the commit boundary."""
    row = NeoRainGoogleFuelValue.query.filter_by(
        sort_date_mission_id=mission.id
    ).first()
    if row is None:
        row = NeoRainGoogleFuelValue(
            sort_date_operation_id=operation.id,
            sort_date_mission_id=mission.id,
        )
        db.session.add(row)
    row.neo_fuel = _display_text(neo_fuel)
    row.center_fuel = _display_text(center_fuel)
    db.session.flush()
    return row


def fuel_review_pending_by_mission(operation, fuel_by_mission):
    if operation is None or not fuel_by_mission:
        return {}
    acknowledgements = {
        (row.sort_date_mission_id, row.fuel_revision)
        for row in NeoRainFuelReviewAcknowledgement.query.filter(
            NeoRainFuelReviewAcknowledgement.sort_date_operation_id == operation.id,
            NeoRainFuelReviewAcknowledgement.sort_date_mission_id.in_(fuel_by_mission),
        ).all()
    }
    # Never demand review for a first publication or an unchanged later cycle.
    # An acknowledged correction stays acknowledged until a NEW fuel revision.
    return {
        mission_id: bool(
            value.get("is_correction")
            and (mission_id, value["revision"]) not in acknowledgements
        )
        for mission_id, value in fuel_by_mission.items()
    }


def acknowledge_fuel_review(operation, mission_id, fuel_revision, user):
    value = str(fuel_revision or "").strip()
    if not value:
        raise ValueError("The current fuel revision is required.")
    current = completed_scorpion_fuel_by_mission(operation).get(mission_id)
    if current is None or current["revision"] != value:
        raise ValueError("Fuel data changed before it could be reviewed. Please review the current values.")
    row = NeoRainFuelReviewAcknowledgement.query.filter_by(
        sort_date_mission_id=mission_id, fuel_revision=value
    ).with_for_update().first()
    if row is None:
        row = NeoRainFuelReviewAcknowledgement(
            sort_date_operation_id=operation.id,
            sort_date_mission_id=mission_id,
            fuel_revision=value,
            reviewed_by_user_id=user.id,
        )
        db.session.add(row)
        db.session.flush()
    return row


def _fuel_revision(mission_id, change_number, neo_fuel_lbs, center_fuel_lbs):
    # Missing and zero are distinct. A change back to an acknowledged amount
    # receives a new generation, so an earlier ACK cannot acknowledge it.
    payload = repr(('rain-fuel-values-v1', mission_id, change_number, neo_fuel_lbs, center_fuel_lbs))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def _capture_completed_publication(connection, assignment_id, initial_missions, *, first_publication=False):
    """Observe stored final values within the original fueling transaction.

    No calculations, commits or changes to physical fueling state. A legacy
    completed event without a captured baseline starts from its known values;
    its original, unrecorded publication is never reconstructed from timestamps.
    """
    assignment = NeoScorpionFuelAssignment.__table__
    fuel = NeoScorpionFuelingEvent.__table__
    publication = NeoRainFuelPublication.__table__
    row = connection.execute(select(assignment.c.sort_date_mission_id,
        assignment.c.sort_date_operation_id, fuel.c.neo_fuel_lbs, fuel.c.center_fuel_lbs)
        .join(fuel, fuel.c.fuel_assignment_id == assignment.c.id)
        .where(assignment.c.id == assignment_id, assignment.c.completed_at_utc.isnot(None),
            or_(fuel.c.neo_fuel_lbs.isnot(None), fuel.c.center_fuel_lbs.isnot(None)))
        .order_by(fuel.c.cycle_number.desc(), fuel.c.sequence_number.desc(), fuel.c.id.desc()).limit(1)).first()
    if row is None:
        return
    if connection.dialect.name == 'postgresql':
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    statement = insert(publication).values(sort_date_mission_id=row.sort_date_mission_id,
        sort_date_operation_id=row.sort_date_operation_id,
        baseline_neo_fuel_lbs=row.neo_fuel_lbs, baseline_center_fuel_lbs=row.center_fuel_lbs,
        neo_fuel_lbs=row.neo_fuel_lbs, center_fuel_lbs=row.center_fuel_lbs, change_number=0)
    inserted = connection.execute(statement.on_conflict_do_nothing(
        index_elements=['sort_date_mission_id']).returning(publication.c.sort_date_mission_id)).scalar()
    if inserted is not None:
        if first_publication:
            initial_missions.add(inserted)
        return
    changes = dict(neo_fuel_lbs=row.neo_fuel_lbs, center_fuel_lbs=row.center_fuel_lbs)
    if row.sort_date_mission_id in initial_missions:
        # Multiple flushes in the first publication transaction still constitute
        # ONE first published value, not a correction to an uncommitted amount.
        changes.update(baseline_neo_fuel_lbs=row.neo_fuel_lbs,
            baseline_center_fuel_lbs=row.center_fuel_lbs, change_number=0)
    else:
        changes['change_number'] = publication.c.change_number + 1
    connection.execute(publication.update().where(
        publication.c.sort_date_mission_id == row.sort_date_mission_id,
        or_(publication.c.neo_fuel_lbs.is_distinct_from(row.neo_fuel_lbs),
            publication.c.center_fuel_lbs.is_distinct_from(row.center_fuel_lbs))).values(**changes))


@orm_event.listens_for(Session, 'before_flush')
def _before_publication_flush(session, flush_context, instances):
    touched = []
    for target in session.new.union(session.dirty):
        if isinstance(target, NeoScorpionFuelingEvent):
            if target in session.new or any(inspect(target).attrs[name].history.has_changes()
                    for name in ('neo_fuel_lbs', 'center_fuel_lbs')):
                touched.append(target)
        elif isinstance(target, NeoScorpionFuelAssignment):
            history = inspect(target).attrs.completed_at_utc.history
            if (target.completed_at_utc is not None or any(value is not None for value in history.deleted)) and (
                    target in session.new or history.has_changes()):
                touched.append(target)
    if not touched:
        return
    initial = session.info.setdefault('rain_initial_publications', set())
    # Read old committed snapshots before ANY assignment/event updates in this
    # flush. This also captures a legacy baseline before its first correction.
    ids = {item.id if isinstance(item, NeoScorpionFuelAssignment) else item.fuel_assignment_id
        for item in touched}
    for assignment_id in sorted(ids - {None}):
        _capture_completed_publication(session.connection(), assignment_id, initial)
    session.info['rain_publication_flush'] = touched


@orm_event.listens_for(Session, 'after_flush_postexec')
def _after_publication_flush(session, flush_context):
    touched = session.info.pop('rain_publication_flush', ())
    ids = {item.id if isinstance(item, NeoScorpionFuelAssignment) else item.fuel_assignment_id
        for item in touched}
    for assignment_id in sorted(ids - {None}):
        _capture_completed_publication(session.connection(), assignment_id,
            session.info.setdefault('rain_initial_publications', set()), first_publication=True)


@orm_event.listens_for(Session, 'after_commit')
@orm_event.listens_for(Session, 'after_rollback')
def _clear_publication_transaction(session):
    if session.in_nested_transaction():
        return
    session.info.pop('rain_initial_publications', None)
    session.info.pop('rain_publication_flush', None)


def _fuel_value(value):
    return f"{int(value):,}" if value is not None else ""


def _display_text(value):
    return str(value or "").strip() or None


def _source(value, *, strict=False):
    normalized = str(value or RAIN_FUEL_SOURCE_GOOGLE).strip().lower()
    if normalized in RAIN_FUEL_SOURCES:
        return normalized
    if strict:
        raise ValueError("Choose GOOGLE or NEO for Rain Fuel Data Source.")
    return RAIN_FUEL_SOURCE_GOOGLE


def _sort_name(value):
    return str(value or DEFAULT_RAIN_SORT).strip().lower() or DEFAULT_RAIN_SORT
