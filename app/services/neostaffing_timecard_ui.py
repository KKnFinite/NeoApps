"""Thin shared presentation adapter; nodes only choose roster scope/theme."""
from datetime import date, timezone
from zoneinfo import ZoneInfo

from flask import abort, render_template, request

from app.services import neostaffing_timecards as timecards
from app.services.gateway_matrix import current_gateway_local_datetime


def local_segment(part, zone):
    return {key: value.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(zone)).isoformat() if value else ""
            for key, value in (("start", part.start_utc), ("end", part.end_utc))}


def decorate(rows):
    for item in rows:
        item["edit_segments"] = [local_segment(part, item["timezone"]) for part in item["segments"]]
    return rows


def node_workspace(user, attendance, *, workspace, scope_label, back_url, node_area=None):
    weekly = request.args.get("period") == "week"
    try:
        day = date.fromisoformat(request.args.get("date") or str(
            attendance["operation"].sort_date if attendance.get("operation") else current_gateway_local_datetime().date()))
    except ValueError:
        abort(400, "Choose a valid workday date.")
    person_ids = None if weekly else {row["person"].id for row in attendance["here"]}
    # Canonical chronological pairing can cross midnight. Include only the
    # actual partner operation, not every adjacent-day slice of that employee.
    rows = timecards.read_rows(user, timecards.week_start(day) if weekly else day, day, person_ids=person_ids,
                              node_workspace=workspace, node_area=node_area)
    included = {item["slice"].id for item in rows}
    wanted = {(item["slice"].person_id, operation_id) for item in rows
              for operation_id in item.get("partner_operation_ids", ())}
    if wanted and not weekly:
        from sqlalchemy import tuple_
        from app.models.staffing_timecard import StaffingTimecardSlice as Slice
        partners = Slice.query.with_entities(Slice.id, Slice.workday_date).filter(
            tuple_(Slice.person_id, Slice.sort_date_operation_id).in_(wanted),
            Slice.id.notin_(included)).all()
        if partners:
            rows.extend(timecards.read_rows(user, min(part.workday_date for part in partners),
                max(part.workday_date for part in partners), person_ids=person_ids,
                slice_ids=[part.id for part in partners], node_workspace=workspace, node_area=node_area))
    represented = {item["slice"].person_id for item in rows}
    missing = [] if weekly else [row for row in attendance["here"] if row["person"].id not in represented]
    return render_template("neostaffing/timecards_node.html", title="EMPLOYEES", rows=decorate(rows),
        missing=missing, day=day, workspace=workspace, weekly=weekly, node_area=node_area,
        scope_label="THIS WEEK — AUTHORIZED EMPLOYEES" if weekly else scope_label, back_url=back_url)
