"""Shared exact-time authority. No node ownership, rounded totals, or GET writes.

Lock order: attendance operation (when applicable), ordered employee parents,
then ordered week receipts. Archives lock only a week, never employee parents.
Every writer invalidates the same receipt before changing report facts.
"""
import calendar
import json
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select

from app.extensions import db
from app.models import StaffingPerson, StaffingUnit, StaffingWorkAssignment
from app.models.staffing_accountability import StaffingComboWorkday
from app.models.staffing_timecard import (
    StaffingTimecardWeek as Week, StaffingTimecardSlice as Slice,
    StaffingTimecardSegment as Segment, StaffingTimecardEdit as Edit,
)
from app.services.neostaffing_assignments import FT_COMBO, sort_of
from app.services.neostaffing_attendance_authority import attendance_authority


MAX_SEGMENTS = 24
CLEANUP_BATCH = 250


def week_start(day):
    return day - timedelta(days=(day.weekday() + 1) % 7)


def retention_cutoff(day):
    year, month = (day.year, day.month - 1) if day.month > 1 else (day.year - 1, 12)
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def hours(seconds):
    return (Decimal(str(seconds)) / Decimal(3600)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _ensure_week(day):
    key = week_start(day)
    dialect = db.session.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        raise RuntimeError("Unsupported timecard transaction dialect.")
    db.session.execute(insert(Week).values(week_start=key, version=0, purged_slices=0)
                       .on_conflict_do_nothing(index_elements=["week_start"]))


def lock_week(day):
    key = week_start(day)
    _ensure_week(day)
    return Week.query.filter_by(week_start=key).populate_existing().with_for_update().one()


def invalidate(week):
    week.version += 1
    week.purge_after = None


def _context(assignment, units, operation):
    chain, current = {}, units.get(assignment.work_area_unit_id)
    while current and current.unit_type not in chain:
        chain[current.unit_type] = current
        current = units.get(current.parent_id)
    from app.services.gateway_matrix import gateway_timezone
    return {
        "employee_id": assignment.person.employee_id,
        "employee": assignment.person.full_name,
        "gateway": operation.gateway_code,
        "timezone": gateway_timezone(operation.gateway),
        **{kind: getattr(chain.get(kind), "name", "") for kind in ("sort", "operation", "department", "work_area")},
    }


def audit(row, actor_id, segments):
    row.version = (row.version or 0) + 1
    row.updated_at = datetime.utcnow()
    row.updated_by_user_id = actor_id
    db.session.add(Edit(slice=row, version=row.version, actor_id=actor_id,
        snapshot_json=json.dumps({"status": row.attendance_status, "segments": segments}, separators=(",", ":"))))


def sync_attendance(operation, statuses, assignments, user_id, *, as_of, units=None, existing_rows=None):
    """Called only after validated attendance commands, under employee locks.

    Snapshot actual status before canonical rollover removes its detail. Clears
    retain prior time edits but never count them as worked. No attendance rows
    are invented, and already purged history is never recreated.
    """
    if not statuses or operation.sort_date < retention_cutoff(as_of):
        return
    rows = existing_rows if existing_rows is not None else {row.person_id: row for row in Slice.query.filter(
        Slice.person_id.in_(statuses), Slice.sort_date_operation_id == operation.id).all()}
    statuses = {person_id: status for person_id, status in statuses.items()
                if person_id not in rows or rows[person_id].attendance_status != status}
    if not statuses:
        return
    # Atomic invalidation takes the receipt lock without re-reading it. A purge
    # that won the lock makes this projection a no-op, never a resurrection.
    _ensure_week(operation.sort_date)
    if not Week.query.filter_by(week_start=week_start(operation.sort_date), purged_at=None).update(
            {Week.version: Week.version + 1, Week.purge_after: None}, synchronize_session=False):
        return
    from app.services import neostaffing as staffing
    units = units if units is not None else staffing._daily_attendance_hierarchy()["by_id"]
    for person_id, status in statuses.items():
        row = rows.get(person_id)
        if row and row.attendance_status == status:
            continue
        assignment = assignments[person_id]
        if row is None:
            sort = sort_of(units.get(assignment.work_area_unit_id), units)
            if sort is None:
                raise ValueError("Timecard source requires a canonical Sort assignment.")
            row = Slice(person_id=person_id, workday_date=operation.sort_date,
                sort_unit_id=sort.id, sort_date_operation_id=operation.id,
                work_area_unit_id=assignment.work_area_unit_id,
                context_json=json.dumps(_context(assignment, units, operation)))
            db.session.add(row)
        row.attendance_status = status
        # Status-only events explicitly retain existing segment history.
        audit(row, user_id, None)


def authorization(user, *, person_ids=None):
    """Bounded shared leadership + configured-Combo union, no role fallback."""
    from app.services import neostaffing as staffing
    hierarchy = staffing._daily_attendance_hierarchy()
    authority = attendance_authority(user, hierarchy)
    if not authority.work_area_ids:
        raise ValueError("Active NeoStaffing management leadership scope is required.")
    scoped_people = select(StaffingWorkAssignment.person_id).where(
        StaffingWorkAssignment.active.is_(True),
        StaffingWorkAssignment.work_area_unit_id.in_(authority.work_area_ids))
    query = db.session.query(StaffingWorkAssignment, StaffingPerson, StaffingComboWorkday).join(
        StaffingPerson, StaffingPerson.id == StaffingWorkAssignment.person_id).outerjoin(
        StaffingComboWorkday, StaffingComboWorkday.person_id == StaffingPerson.id).filter(
        StaffingWorkAssignment.active.is_(True), StaffingPerson.active.is_(True),
        StaffingPerson.id.in_(scoped_people))
    if person_ids is not None:
        query = query.filter(StaffingPerson.id.in_(person_ids))
    rows = query.all()
    allowed, by_person = set(), {}
    for assignment, person, combo in rows:
        by_person.setdefault(person.id, []).append((assignment, person, combo))
    for person_id, entries in by_person.items():
        for assignment, person, combo in entries:
            sort = sort_of(hierarchy["by_id"].get(assignment.work_area_unit_id), hierarchy["by_id"])
            if sort is None:
                continue
            if assignment.work_area_unit_id in authority.work_area_ids:
                allowed.add((person_id, sort.id))
            if person.classification in FT_COMBO and combo:
                pair = {combo.first_sort_id, combo.second_sort_id}
                if sort.id in pair and any(
                    other.work_area_unit_id in authority.work_area_ids
                    and getattr(sort_of(hierarchy["by_id"].get(other.work_area_unit_id), hierarchy["by_id"]), "id", None) in pair
                    for other, _, _ in entries
                ):
                    allowed.add((person_id, sort.id))
    return allowed


def parse_timestamp(value, zone):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            local = parsed.replace(tzinfo=ZoneInfo(zone))
            # Reject both DST gaps and ambiguous local clocks. Explicit offsets
            # remain accepted, so neither repeated hour is silently guessed.
            if local.utcoffset() != local.replace(fold=1).utcoffset():
                raise ValueError("Use an explicit UTC offset for a daylight-saving transition time.")
            if local.astimezone(timezone.utc).astimezone(ZoneInfo(zone)).replace(tzinfo=None) != parsed:
                raise ValueError("This local time does not exist.")
            parsed = local
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError) as error:
        raise ValueError("Enter a valid timestamp; include its UTC offset during a daylight-saving transition.") from error


def save_segments(user, commands, *, as_of):
    """Atomic bulk edit; expected versions cover attendance changes too."""
    if not isinstance(commands, list) or not 1 <= len(commands) <= 100:
        raise ValueError("Save 1–100 changed timecards at a time.")
    if any(not isinstance(item, dict) for item in commands):
        raise ValueError("Invalid timecard command.")
    ids = [int(item["id"]) for item in commands]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate timecard command.")
    source = Slice.query.filter(Slice.id.in_(ids)).all()
    if len(source) != len(ids):
        raise ValueError("Timecard was removed or expired. Reload.")
    people = {row.person_id for row in source}
    db.session.query(StaffingPerson.id).filter(StaffingPerson.id.in_(people)).order_by(
        StaffingPerson.id).with_for_update().all()
    allowed = authorization(user, person_ids=people)
    weeks = {key: lock_week(key) for key in sorted({week_start(row.workday_date) for row in source})}
    rows = {row.id: row for row in Slice.query.filter(Slice.id.in_(ids)).populate_existing().all()}
    staged = []
    for command in commands:
        row = rows.get(int(command["id"]))
        if not row or row.workday_date < retention_cutoff(as_of) or weeks[week_start(row.workday_date)].purged_at:
            raise ValueError("Timecard expired. Reload.")
        if (row.person_id, row.sort_unit_id) not in allowed:
            raise ValueError("Timecard is outside your leadership scope.")
        if row.version != int(command["version"]):
            raise ValueError("Timecard or attendance changed. Reload before saving; no edit was applied.")
        if row.attendance_status != "here":
            raise ValueError("Only Here attendance accepts worked times.")
        segments = command.get("segments")
        if not isinstance(segments, list) or len(segments) > MAX_SEGMENTS:
            raise ValueError("Use at most 24 segments per Sort slice.")
        zone = json.loads(row.context_json)["timezone"]
        parsed = []
        for part in segments:
            if not isinstance(part, dict):
                raise ValueError("Invalid segment.")
            start_value, end_value = part.get("start"), part.get("end")
            clock = r"\d{2}:\d{2}(?::\d{2})?"
            if start_value and re.fullmatch(clock, str(start_value)):
                start_value = f"{row.workday_date}T{start_value}"
            start = parse_timestamp(start_value, zone)
            if end_value and re.fullmatch(clock, str(end_value)):
                end_day = start.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(zone)).date() if start else row.workday_date
                end_value = f"{end_day}T{end_value}"
                end = parse_timestamp(end_value, zone)
                if start and end <= start:
                    end_value = f"{end_day + timedelta(days=1)}T{end_value.split('T')[1]}"
            parsed.append((start, parse_timestamp(end_value, zone)))
        parsed = [(start, end) for start, end in parsed if start or end]
        for start, end in parsed:
            if start and end and end <= start:
                raise ValueError("Segment end must follow start; include the overnight date.")
            for value in (start, end):
                if value and abs((value.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(zone)).date() - row.workday_date).days) > 2:
                    raise ValueError("Segment timestamps must belong to this Sort workday.")
        staged.append((row, parsed))
    old = {}
    for part in Segment.query.filter(Segment.slice_id.in_(ids)).order_by(Segment.id).all():
        old.setdefault(part.slice_id, []).append((part.start_utc, part.end_utc))
    changed_weeks = set()
    for row, parsed in staged:
        if old.get(row.id, []) == parsed:
            continue
        Segment.query.filter_by(slice_id=row.id).delete(synchronize_session="fetch")
        db.session.add_all(Segment(slice_id=row.id, start_utc=start, end_utc=end) for start, end in parsed)
        audit(row, user.id, [[start.isoformat() if start else None, end.isoformat() if end else None] for start, end in parsed])
        changed_weeks.add(week_start(row.workday_date))
    for key in changed_weeks:
        invalidate(weeks[key])
    return len(changed_weeks)


def read_rows(user, start, end, *, complete=False, person_ids=None, slice_ids=None):
    """One date-bounded projection plus one segment read, never per-row lookups."""
    if end < start or (end - start).days > (28 if slice_ids is not None else 6):
        raise ValueError("Choose one day or Sunday–Saturday week.")
    allowed = None if complete else authorization(user, person_ids=person_ids)
    query = Slice.query.filter(Slice.workday_date.between(start, end))
    if person_ids is not None:
        query = query.filter(Slice.person_id.in_(person_ids))
    if slice_ids is not None:
        query = query.filter(Slice.id.in_(slice_ids))
    if allowed is not None:
        from sqlalchemy import tuple_
        query = query.filter(tuple_(Slice.person_id, Slice.sort_unit_id).in_(allowed))
    slices = query.order_by(Slice.person_id, Slice.workday_date, Slice.sort_unit_id).all()
    segments, overlap_parts = {}, {}
    # Include adjacent-day segments for these employees in the same bounded
    # query, so pagination and Sunday boundaries cannot conceal overlaps.
    for part, person_id, status in db.session.query(Segment, Slice.person_id, Slice.attendance_status).join(
        Slice, Slice.id == Segment.slice_id).filter(
            Slice.person_id.in_({row.person_id for row in slices}),
            Slice.workday_date.between(start - timedelta(days=2), end + timedelta(days=2)),
        ).order_by(Segment.id).all():
        segments.setdefault(part.slice_id, []).append(part)
        if status == "here" and part.start_utc and part.end_utc:
            overlap_parts.setdefault(person_id, []).append(part)
    result = []
    for row in slices:
        parts = segments.get(row.id, [])
        seconds = sum((part.end_utc - part.start_utc).total_seconds() for part in parts if part.start_utc and part.end_utc)
        issues = []
        if row.attendance_status == "here":
            if not parts:
                issues.append("Here without times")
            if any(not part.start_utc or not part.end_utc for part in parts):
                issues.append("Incomplete segment")
            if seconds > 16 * 3600:
                issues.append("Over 16 hours; review")
        result.append({"slice": row, **json.loads(row.context_json), "segments": parts,
                       "seconds": seconds if row.attendance_status == "here" else None,
                       "hours": hours(seconds) if row.attendance_status == "here" else None,
                       "issues": issues})
    # Across-Sort overlaps are employee-wide, never silently double-paid. They
    # remain visible exceptions rather than rewritten timestamps.
    by_slice = {item["slice"].id: item for item in result}
    per_person = {}
    for item in result:
        if item["slice"].attendance_status == "here":
            person_id = item["slice"].person_id
            per_person[person_id] = [(part.start_utc, part.end_utc, by_slice.get(part.slice_id))
                                     for part in overlap_parts.get(person_id, [])]
    for parts in per_person.values():
        ordered = sorted(parts, key=lambda part: part[0])
        for index, (start_time, end_time, item) in enumerate(ordered):
            for other_start, other_end, other in ordered[index + 1:]:
                if other_start >= end_time:
                    break
                for target in (item, other):
                    if target is not None and "Overlapping segments" not in target["issues"]:
                        target["issues"].append("Overlapping segments")
    annotate_combo_exceptions(result, start, end)
    return result


def annotate_combo_exceptions(items, start, end):
    """Check real chronological partners, including just outside report dates."""
    from sqlalchemy.orm import aliased, joinedload
    from sqlalchemy import func
    from app.models import SortDateOperation
    from app.services.gateway_matrix import sort_lookup_window_for_operation
    from app.services.neostaffing_workday_identity import chronological_pair
    first, second = aliased(StaffingUnit), aliased(StaffingUnit)
    configs = {config.person_id: (first_name, second_name) for config, first_name, second_name in db.session.query(
        StaffingComboWorkday, first.name, second.name).join(first, first.id == StaffingComboWorkday.first_sort_id)
        .join(second, second.id == StaffingComboWorkday.second_sort_id).filter(
            StaffingComboWorkday.person_id.in_({item["slice"].person_id for item in items})).all()}
    if not configs:
        return
    operations = SortDateOperation.query.options(joinedload(SortDateOperation.gateway)).filter(
        SortDateOperation.gateway_code.in_({item["gateway"] for item in items}),
        func.lower(SortDateOperation.sort_name).in_({name.strip().lower() for pair in configs.values() for name in pair}),
        SortDateOperation.sort_date.between(start - timedelta(days=14), end + timedelta(days=14)),
    ).order_by(SortDateOperation.sort_date, SortDateOperation.id).all()
    templates = {}
    def window(op):
        key = (op.gateway_code, op.sort_name)
        if key not in templates:
            a, b = sort_lookup_window_for_operation(op)
            midnight = datetime.combine(op.sort_date, datetime.min.time())
            templates[key] = (a - midnight, b - midnight)
        a, b = templates[key]
        midnight = datetime.combine(op.sort_date, datetime.min.time())
        return midnight + a, midnight + b
    existing = set(db.session.query(Slice.person_id, Slice.sort_date_operation_id).filter(
        Slice.person_id.in_(configs), Slice.sort_date_operation_id.in_([op.id for op in operations])).all())
    for item in items:
        row = item["slice"]
        if row.person_id not in configs:
            continue
        first_name, second_name = configs[row.person_id]
        pair = chronological_pair([op for op in operations if op.gateway_code == item["gateway"]],
            first_name, second_name, row.sort_date_operation_id, window)
        item["partner_operation_ids"] = {op.id for op in pair} if pair else set()
        if not pair or any((row.person_id, op.id) not in existing for op in pair):
            item["issues"].append("FT Combo missing half")


def report_context(user, values, *, as_of, complete=False):
    """SQL aggregate/group/paginate before hydration; exact seconds stay source."""
    from sqlalchemy import case, cast, Integer, Float, JSON, func, tuple_
    from urllib.parse import urlencode
    day = date.fromisoformat(values.get("date") or as_of.isoformat())
    period = "week" if values.get("period") == "week" else "day"
    start = week_start(day) if period == "week" else day
    end = start + timedelta(days=6) if period == "week" else start
    view = values.get("view", "sort")
    if view not in {"sort", "employee", "exceptions", "times"}:
        raise ValueError("Unknown timecard report.")
    page = max(1, int(values.get("page", 1)))
    query = Slice.query.filter(Slice.workday_date.between(start, end))
    if not complete:
        query = query.filter(tuple_(Slice.person_id, Slice.sort_unit_id).in_(authorization(user)))
    search = str(values.get("search", "")).strip()
    if search:
        query = query.join(StaffingPerson, StaffingPerson.id == Slice.person_id).filter(or_(
            StaffingPerson.first_name.ilike(f"%{search}%"), StaffingPerson.last_name.ilike(f"%{search}%"),
            StaffingPerson.employee_id.ilike(f"%{search}%")))
    seconds = (func.extract("epoch", Segment.end_utc - Segment.start_utc)
               if db.session.get_bind().dialect.name == "postgresql"
               else (cast(func.strftime("%s", func.substr(Segment.end_utc, 1, 19)), Integer)
                     - cast(func.strftime("%s", func.substr(Segment.start_utc, 1, 19)), Integer)
                     + (cast(func.substr(Segment.end_utc, 21, 6), Float)
                        - cast(func.substr(Segment.start_utc, 21, 6), Float)) / 1000000))
    exact = case((Slice.attendance_status == "here", seconds), else_=None)
    report, rows = [], []
    if view in {"sort", "employee"}:
        keys = tuple((cast(Slice.context_json, JSON)[key].as_string()
                      if db.session.get_bind().dialect.name == "postgresql"
                      else func.json_extract(Slice.context_json, f"$.{key}")) for key in
            ("gateway", "sort", "operation", "department", "work_area")) if view == "sort" else (Slice.person_id,)
        aggregate = query.with_entities(*keys, func.sum(exact).label("seconds"),
            func.max(case((Slice.attendance_status == "here", 1), else_=0)).label("has_here"),
            (func.string_agg(func.distinct(Slice.attendance_status), ", ")
             if db.session.get_bind().dialect.name == "postgresql"
             else func.group_concat(func.distinct(Slice.attendance_status))).label("statuses")
        ).outerjoin(Segment, Segment.slice_id == Slice.id).group_by(*keys)
        total = aggregate.count()
        groups = aggregate.order_by(*keys).offset((page - 1) * 50).limit(50).all()
        if view == "employee":
            people = {person.id: person for person in StaffingPerson.query.filter(StaffingPerson.id.in_([row[0] for row in groups])).all()}
            # Statuses are actual slice facts, not synthesized zero-hour absences.
            headings = ["Employee ID", "Employee", "Hours", "Attendance statuses"]
            report = [[people[row[0]].employee_id, people[row[0]].full_name,
                hours(row.seconds or 0) if row.has_here else None, (row.statuses or "Unmarked").replace("_", " ").title()] for row in groups]
        else:
            headings = ["Gateway", "Sort", "Operation", "Department", "Work Area", "Hours", "Attendance statuses"]
            for row in groups:
                report.append(list(row[:5]) + [hours(row.seconds or 0) if row.has_here else None,
                    (row.statuses or "Unmarked").replace("_", " ").title()])
    else:
        if view == "exceptions":
            from sqlalchemy.orm import aliased
            # Filter ordinary clean slices before pagination. Combo candidates
            # still need the canonical chronological-pair resolver below; no
            # guessed date pairing is embedded in SQL.
            own, other, other_slice = aliased(Segment), aliased(Segment), aliased(Slice)
            overlap = select(own.id).select_from(own).join(other, other.id != own.id).join(
                other_slice, other_slice.id == other.slice_id).where(
                    own.slice_id == Slice.id, other_slice.person_id == Slice.person_id,
                    other_slice.attendance_status == "here",
                    other_slice.workday_date.between(start - timedelta(days=2), end + timedelta(days=2)),
                    own.start_utc < other.end_utc, other.start_utc < own.end_utc,
                ).correlate(Slice).exists()
            no_times = ~select(Segment.id).where(Segment.slice_id == Slice.id).correlate(Slice).exists()
            incomplete = select(Segment.id).where(Segment.slice_id == Slice.id,
                or_(Segment.start_utc.is_(None), Segment.end_utc.is_(None))).correlate(Slice).exists()
            duration = select(func.sum(seconds)).where(Segment.slice_id == Slice.id).correlate(Slice).scalar_subquery()
            combo = select(StaffingComboWorkday.person_id).where(
                StaffingComboWorkday.person_id == Slice.person_id).correlate(Slice).exists()
            query = query.filter(or_(combo, (Slice.attendance_status == "here") & or_(
                no_times, incomplete, duration > 16 * 3600, overlap)))
        total = query.count()
        ids = [row[0] for row in query.with_entities(Slice.id).order_by(Slice.person_id, Slice.workday_date, Slice.id).offset((page - 1) * 50).limit(50).all()]
        from app.services.neostaffing_timecard_ui import decorate
        rows = decorate(read_rows(user, start, end, complete=complete, slice_ids=ids))
        if complete and view == "times":
            try:
                editable = authorization(user, person_ids={item["slice"].person_id for item in rows})
            except ValueError:
                editable = set()
            for item in rows:
                item["editable"] = (item["slice"].person_id, item["slice"].sort_unit_id) in editable
        headings = ["Employee", "Workday", "Sort", "Work Area", "Exception"]
        report = [[item["employee"], item["slice"].workday_date, item["sort"], item["work_area"], issue] for item in rows for issue in item["issues"]]
    args = dict(values)
    previous_query = urlencode({**args, "page": page - 1})
    next_query = urlencode({**args, "page": page + 1})
    archive_week = week_start(day)
    if archive_week + timedelta(days=6) >= as_of:
        archive_week -= timedelta(days=7)
    return dict(day=day, start=start, end=end, period=period, view=view, page=page, total=total,
        has_next=page * 50 < total, previous_query=previous_query, next_query=next_query,
        rows=rows, report=report, headings=headings, archive_week=archive_week,
        receipt=db.session.get(Week, archive_week))


def cleanup(*, as_of, now=None, batch_size=CLEANUP_BATCH):
    """One oldest-first batch. Never resurrect purged data on later edits."""
    now = now or datetime.utcnow()
    batch_size = max(1, min(int(batch_size), CLEANUP_BATCH))
    # Date matching uses each receipt's explicit seven-day interval, portable
    # SQLAlchemy rather than dialect-specific date arithmetic.
    due_weeks = Week.query.filter(Week.purge_after <= now).order_by(Week.week_start).limit(8).all()
    criteria = [Slice.workday_date < retention_cutoff(as_of)]
    criteria.extend(Slice.workday_date.between(row.week_start, row.week_start + timedelta(days=6)) for row in due_weeks)
    candidates = Slice.query.filter(or_(*criteria)).order_by(Slice.workday_date, Slice.id).limit(batch_size).all()
    weeks = {key: lock_week(key) for key in sorted(
        {week_start(row.workday_date) for row in candidates} | {row.week_start for row in due_weeks})}
    ids = [row.id for row in candidates if row.workday_date < retention_cutoff(as_of)
           or (weeks[week_start(row.workday_date)].purge_after is not None
               and weeks[week_start(row.workday_date)].purge_after <= now)]
    if ids:
        Edit.query.filter(Edit.slice_id.in_(ids)).delete(synchronize_session=False)
        Segment.query.filter(Segment.slice_id.in_(ids)).delete(synchronize_session=False)
        Slice.query.filter(Slice.id.in_(ids)).delete(synchronize_session=False)
        for key, week in weeks.items():
            deleted = sum(row.id in ids and week_start(row.workday_date) == key for row in candidates)
            if deleted:
                week.purged_slices += deleted
                if week.purge_after is not None and week.purge_after <= now:
                    week.purged_at = now
                else:
                    invalidate(week)
        # Finished receipts must leave the bounded due queue, otherwise old
        # empty weeks would permanently starve later weeks of cleanup capacity.
    if weeks:
        remaining_dates = {row[0] for row in db.session.query(Slice.workday_date).filter(
            Slice.workday_date.between(min(weeks), max(weeks) + timedelta(days=6))).distinct().all()}
        for key, week in weeks.items():
            if not any(key <= day <= key + timedelta(days=6) for day in remaining_dates):
                if week.purge_after is not None and week.purge_after <= now:
                    week.purged_at = now
                week.purge_after = None
    return len(ids)


def backfill_retained_attendance(as_of):
    """Explicit bootstrap only, paged, idempotent, never invent purged status.

    Use the attendance snapshot's area, not a later employee transfer. Legacy
    rows without an operation identity cannot safely manufacture a Sort slice.
    """
    from types import SimpleNamespace
    from sqlalchemy.orm import joinedload
    from app.models import StaffingDailyAttendance, SortDateOperation
    from app.services import neostaffing as staffing
    units = staffing._daily_attendance_hierarchy()["by_id"]
    # Acquire every relevant parent before any receipt, in the same order as
    # live writers. Paging parent locks after receipt locks could deadlock a
    # bulk edit spanning two bootstrap batches. This is bootstrap-only and
    # restricted to the retained month, not historical attendance.
    retained_people = select(StaffingDailyAttendance.person_id).where(
        StaffingDailyAttendance.attendance_date >= retention_cutoff(as_of),
        StaffingDailyAttendance.sort_date_operation_id.is_not(None))
    db.session.query(StaffingPerson.id).filter(StaffingPerson.id.in_(retained_people)).order_by(
        StaffingPerson.id).with_for_update().all()
    last_id, count = 0, 0
    while True:
        records = StaffingDailyAttendance.query.options(joinedload(StaffingDailyAttendance.person)).filter(
            StaffingDailyAttendance.id > last_id,
            StaffingDailyAttendance.attendance_date >= retention_cutoff(as_of),
            StaffingDailyAttendance.sort_date_operation_id.is_not(None),
        ).order_by(StaffingDailyAttendance.id).limit(250).all()
        if not records:
            break
        last_id = records[-1].id
        records = StaffingDailyAttendance.query.options(joinedload(StaffingDailyAttendance.person)).filter(
            StaffingDailyAttendance.id.in_([record.id for record in records])).populate_existing().all()
        operations = {op.id: op for op in SortDateOperation.query.options(joinedload(SortDateOperation.gateway)).filter(
            SortDateOperation.id.in_({record.sort_date_operation_id for record in records})).all()}
        existing = {(row.person_id, row.sort_date_operation_id) for row in Slice.query.filter(
            Slice.person_id.in_({record.person_id for record in records}),
            Slice.sort_date_operation_id.in_(operations)).all()}
        grouped = {}
        for record in records:
            if (record.person_id, record.sort_date_operation_id) in existing or record.work_area_unit_id not in units:
                continue
            grouped.setdefault(record.sort_date_operation_id, []).append(record)
        for operation_id, entries in sorted(grouped.items(), key=lambda pair: operations[pair[0]].sort_date):
            op = operations[operation_id]
            # The writer acquires the receipt and skips previously purged weeks.
            assignments = {record.person_id: SimpleNamespace(work_area_unit_id=record.work_area_unit_id, person=record.person) for record in entries}
            sync_attendance(op, {record.person_id: record.status for record in entries}, assignments, None, as_of=as_of)
            count += len(entries)
        db.session.flush()
    return count
