"""Independent authoritative fuel values for NeoRain outbound missions."""

from __future__ import annotations

import hashlib

from app.extensions import db
from app.models import (
    MotherBrainGoogleIntegrationSetting,
    NeoRainFuelReviewAcknowledgement,
    NeoRainGoogleFuelValue,
    NeoScorpionFuelAssignment,
    NeoScorpionFuelingEvent,
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


def completed_scorpion_fuel_by_mission(operation):
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
        .all()
    )
    values = {}
    for assignment, event in rows:
        mission_id = assignment.sort_date_mission_id
        if mission_id in values:
            continue
        if event.neo_fuel_lbs is None and event.center_fuel_lbs is None:
            continue
        revision = _fuel_revision(assignment, event)
        values[mission_id] = {
            "neo_fuel": _fuel_value(event.neo_fuel_lbs),
            "center_fuel": _fuel_value(event.center_fuel_lbs),
            "revision": revision,
            "is_correction": bool(
                assignment.completed_at_utc
                and event.updated_at
                and event.updated_at > assignment.completed_at_utc
            ),
        }
    return values


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
    # The initial publication is not a correction. A revision becomes pending
    # only after this mission has had an earlier acknowledgement.
    acknowledged_missions = {
        mission_id for mission_id, _revision in acknowledgements
    }
    return {
        mission_id: (
            (value.get("is_correction") or mission_id in acknowledged_missions)
            and (mission_id, value["revision"]) not in acknowledgements
        )
        for mission_id, value in fuel_by_mission.items()
    }


def acknowledge_fuel_review(operation, mission_id, fuel_revision, user):
    value = str(fuel_revision or "").strip()
    if not value:
        raise ValueError("The current fuel revision is required.")
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


def _fuel_revision(assignment, event):
    payload = "|".join(
        str(value or "")
        for value in (
            assignment.id,
            assignment.completed_at_utc,
            event.id,
            event.updated_at,
            event.neo_fuel_lbs,
            event.center_fuel_lbs,
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:64]


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
