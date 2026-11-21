"""Retire a confirmed test through MotherBrain's existing archived lifecycle."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_

from app.extensions import db
from app.models import (
    GatewaySortMatrix, NeoErmacDoorPull, NeoScorpionFuelAssignment, NeoScorpionFuelingEvent,
    NeoScorpionInventoryReading, NeoSektorUldOnTheWayEvent, NeoSubZeroDepartureDeiceEvent,
    NeoSubZeroSprayRecord, SortDateMission, SortDateOperation,
)
from app.models.sort_date_operation import ManualTestSortArchive
from app.services.account_security import platform_grandmaster
from app.services.gateway_matrix import current_gateway_local_datetime
from app.services.master_schedule_sync import lock_schedule_gateway


def archive_manual_test_operation(gateway, operation_id, actor, reason):
    """Caller commits the lifecycle change and audit receipt in one transaction."""
    if not platform_grandmaster(actor):
        raise PermissionError("Platform Grandmaster access is required.")
    reason = (reason or "").strip()
    if not 1 <= len(reason) <= 240:
        raise ValueError("Enter a test designation reason (up to 240 characters).")
    lock_schedule_gateway(gateway.code)
    operation = SortDateOperation.query.filter_by(
        id=operation_id, gateway_id=gateway.id, gateway_code=gateway.code,
    ).populate_existing().with_for_update().one_or_none()
    today = current_gateway_local_datetime(gateway).date()
    if (not operation or operation.generated_by_user_id is None or operation.locked_at_utc
            or operation.archived_at_utc or operation.sort_date not in (today, today - timedelta(days=1))):
        raise ValueError("Select a current, open, manually generated test operation.")
    cell = GatewaySortMatrix.query.filter_by(
        gateway_id=gateway.id, day_of_week=operation.sort_date.strftime("%A").lower(),
        sort_name=operation.sort_name,
    ).populate_existing().with_for_update().one_or_none()
    if not cell or cell.gateway_code != gateway.code or cell.is_active is not False:
        raise ValueError("The Gateway Sort Matrix must explicitly mark this Sort as off.")
    missions = SortDateMission.query.filter_by(sort_date_operation_id=operation_id)
    # Serialize actuals and child inserts against the archive's evidence check.
    missions.with_entities(SortDateMission.id).order_by(SortDateMission.id).with_for_update().all()
    mission = SortDateMission
    milestones = (
        mission.actual_block_in_datetime_utc, mission.actual_block_out_datetime_utc,
        mission.ramp_load_completed_at_utc, mission.crew_load_completed_at_utc,
        mission.fuel_completed_at_utc, mission.elmac_completed_at_utc, mission.last_uld_enroute_at_utc,
        mission.actual_mix_pull_time_local, mission.actual_pure_pull_time_local,
    )
    has_activity = missions.filter(or_(
        *(field.isnot(None) for field in milestones),
        mission.arrival_status.notin_(("scheduled", "cancelled")),
        mission.departure_status.notin_(("scheduled", "cancelled")),
    )).with_entities(mission.id).first()
    records = (NeoErmacDoorPull, NeoScorpionFuelAssignment, NeoScorpionFuelingEvent,
               NeoScorpionInventoryReading, NeoSektorUldOnTheWayEvent,
               NeoSubZeroDepartureDeiceEvent, NeoSubZeroSprayRecord)
    if has_activity or any(db.session.query(model.query.filter_by(
        sort_date_operation_id=operation_id).exists()).scalar() for model in records):
        raise ValueError("Operational activity is recorded. This Sort must remain protected.")
    stamp = datetime.now(timezone.utc).replace(tzinfo=None)
    operation.archived_at_utc = stamp
    db.session.add(ManualTestSortArchive(operation_id=operation.id, actor_user_id=actor.id,
                                        reason=reason, archived_at_utc=stamp))
