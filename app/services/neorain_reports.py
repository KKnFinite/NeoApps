"""Sort-scoped Rainrock facts and copy-ready briefing projection.

Every field is resolved independently. Google is a dated, frozen fallback, not
an authority over a valid Neo value or an operator entry.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import lru_cache
from hashlib import sha256
import re
import time

from flask import current_app
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload, selectinload

from app.extensions import db
from app.models import Gateway, NeoRainReportEntry, SortDateMission, StaffingAttendanceSummary, StaffingUnit
from app.services.neostaffing import attendance_operation_department_counts
from app.services.google_motherbrain_sheets import (
    _configured_reader_inputs, _create_gspread_client, _google_call,
)
from app.services.time_display import format_local_hhmm
from app.services.google_rain_integration_mode import rain_operation_is_native
from app.services.neorain_performance import completed_actual, completed_variance_minutes, eligible_for_performance, mission_cancelled


RFD_RECAP_SPREADSHEET_ID = "1oDK5jGcF58QYx5CYQmdcG2A2fQlCIkdxk-zrP9hyu5s"
REPORT_FIELDS = {
    "planned_volume": ("Planned HPS volume", "Inputs!B18", "number"),
    "actual_volume": ("Actual volume", "Inputs!B19", "number"),
    "smalls_percent": ("Smalls processed %", "Inputs!B20", "percent"),
    "planned_fph": ("Planned FPH", "Inputs!B21", "number"),
    "actual_fph": ("Actual FPH", "Inputs!B22", "number"),
    "hub_planned_payroll": ("Hub / Shift planned payroll", "Inputs!B3", "number"),
    "hub_actual_payroll": ("Hub / Shift actual payroll", "Inputs!B4", "number"),
    "hub_planned_working": ("Hub / Shift planned working", "Inputs!B5", "number"),
    "hub_actual_working": ("Hub / Shift actual working", "Inputs!B6", "number"),
    "ramp_planned_payroll": ("Ramp planned payroll", "Inputs!B9", "number"),
    "ramp_actual_payroll": ("Ramp actual payroll", "Inputs!C10", "number"),
    "ramp_planned_working": ("Ramp planned working", "Inputs!B11", "number"),
    "ramp_actual_working": ("Ramp actual working", "Inputs!C12", "number"),
    **{f"note_{n}": (f"Additional sort note {n}", f"Inputs!B{23+n}", "note") for n in range(1, 6)},
}
GOOGLE_RANGES = ("'Completed Form'!B2", *(spec[1] for spec in REPORT_FIELDS.values()))
INVALID_GOOGLE = {"#VALUE!", "#REF!", "#N/A", "#DIV/0!", "#ERROR!", "#NUM!", "#NAME?"}


def normalize_report_value(field, raw):
    """Return a storable value, preserving numeric zero; None means unavailable."""
    kind = REPORT_FIELDS[field][2]
    value = str(raw if raw is not None else "").strip()
    if not value or value.upper() in INVALID_GOOGLE or value.startswith("#"):
        return None
    if kind == "note":
        if len(value) > 2000:
            raise ValueError("A sort note cannot exceed 2,000 characters.")
        return value
    if "%" in value:
        if kind != "percent" or not value.endswith("%") or value.count("%") != 1:
            raise ValueError("Enter a valid number.")
        value = value[:-1]
    try:
        number = Decimal(value.replace(",", ""))
    except InvalidOperation as exc:
        raise ValueError("Enter a valid number.") from exc
    if not number.is_finite() or number < 0:
        raise ValueError("Enter a nonnegative finite number.")
    if kind == "number" and number != number.to_integral_value():
        raise ValueError("Enter a whole-number count.")
    if kind == "percent":
        if number > 100:
            raise ValueError("Enter a percentage from 0 to 100.")
    if number > 999999999:
        raise ValueError("The value is too large.")
    if kind == "number":
        return str(int(number))
    return format(number, "f")


def _parsed_report_date(value):
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(value).strip(), fmt).date()
        except ValueError:
            continue
    return None


def _range_first(response, index):
    try:
        return response["valueRanges"][index].get("values", [[""]])[0][0]
    except (IndexError, KeyError, TypeError):
        return ""


def _report_gateway_id(operation):
    return operation.gateway_id or db.session.query(Gateway.id).filter_by(code=operation.gateway_code).scalar()


@lru_cache(maxsize=4)
def _cached_google_recap(bucket, credential_fingerprint):
    # Existing reader configuration gives the service account read-only scope.
    credentials, _ = _configured_reader_inputs(current_app.config)
    client = _create_gspread_client(credentials, current_app.config)
    spreadsheet = _google_call("open_rfd_recap", lambda: client.open_by_key(RFD_RECAP_SPREADSHEET_ID))
    return _google_call("read_rfd_recap", lambda: spreadsheet.values_batch_get(
        list(GOOGLE_RANGES),
        params={"valueRenderOption": "FORMATTED_VALUE", "dateTimeRenderOption": "FORMATTED_STRING"},
    ))


def read_google_recap():
    # No entire-workbook download: one bounded batch per process/credential/2 min.
    from app.services.google_motherbrain_sheets import _credential_json
    _configured_reader_inputs(current_app.config)
    credential, _ = _credential_json(current_app.config)
    fingerprint = sha256(str(credential).encode("utf-8")).hexdigest()
    return _cached_google_recap(int(time.monotonic() // 120), fingerprint)


def snapshot_current_google(operation):
    """Refresh only the selected current sort; historical snapshots never drift."""
    if rain_operation_is_native(operation):
        return "Native NeoRain"
    if str(operation.gateway_code or "").upper() != "RFD" or operation.sort_name.casefold() != "night":
        return "RFD Recap applies only to RFD Night Sorts"
    try:
        response = read_google_recap()
    except Exception:
        current_app.logger.warning("NeoRain recap fallback unavailable", exc_info=True)
        return "Google unavailable"
    report_date = _parsed_report_date(_range_first(response, 0))
    if report_date != operation.sort_date:
        return "Google recap date does not match this Sort; fallback withheld"
    gateway_id = _report_gateway_id(operation)
    if gateway_id is None:
        return "Neo gateway unavailable; Google fallback withheld"
    existing = {row.field_name: row for row in NeoRainReportEntry.query.filter_by(
        sort_date_operation_id=operation.id, data_source="google").all()}
    changed = False
    for index, field in enumerate(REPORT_FIELDS, start=1):
        try:
            value = normalize_report_value(field, _range_first(response, index))
        except ValueError:
            value = None
        row = existing.get(field)
        if value is None:
            if row is not None and row.value:
                row.value = ""
                row.version += 1
                changed = True
            continue
        if row is None:
            db.session.add(NeoRainReportEntry(
                gateway_id=gateway_id, sort_date_operation_id=operation.id,
                sort_date=operation.sort_date, sort_name=operation.sort_name,
                field_name=field, value=value, data_source="google",
                source_report_date=report_date,
            ))
            changed = True
        elif row.value != value or row.source_report_date != report_date:
            row.value = value
            row.source_report_date = report_date
            row.version += 1
            changed = True
    if changed:
        try:
            db.session.commit()
        except IntegrityError:
            # Another report reader may have inserted the same unique snapshot.
            db.session.rollback()
            return "Google snapshot refreshed by another request"
    return "Google recap date verified"


def _canonical_hub_working(operation, *, current=False):
    # Finalized summary is stable across transfers and remains authoritative.
    summaries = (db.session.query(StaffingAttendanceSummary, StaffingUnit)
                 .join(StaffingUnit, StaffingUnit.id == StaffingAttendanceSummary.scope_unit_id)
                 .filter(StaffingAttendanceSummary.sort_date_operation_id == operation.id,
                         StaffingAttendanceSummary.scope_type == "operation").all())
    for summary, unit in summaries:
        if str(unit.name or "").strip().casefold() == "hub":
            return summary.worked_count
    if current:
        try:
            counts = attendance_operation_department_counts(operation)
        except ValueError:
            return None
        for row in counts["scopes"]:
            if (row["scope"].unit_type == "operation"
                    and str(row["scope"].name or "").strip().casefold() == "hub"
                    and row["on_payroll"] > 0 and row["unmarked"] == 0):
                return row["working"]
    return None


def resolved_report_fields(operation, *, current=False):
    native = rain_operation_is_native(operation)
    google_status = "Native NeoRain" if native else "Historical Google snapshot only"
    if current and not native:
        google_status = snapshot_current_google(operation)
    rows = NeoRainReportEntry.query.filter_by(sort_date_operation_id=operation.id).all()
    entries = {(row.field_name, row.data_source): row for row in rows}
    gateway_id = _report_gateway_id(operation)
    neo = {"hub_actual_working": _canonical_hub_working(operation, current=current)}
    result = {}
    for field, (label, _cell, kind) in REPORT_FIELDS.items():
        canonical = neo.get(field)
        if canonical is not None:
            result[field] = {"label": label, "value": str(canonical), "source": "NeoStaffing", "editable": False, "version": None}
            continue
        manual = entries.get((field, "manual"))
        google = entries.get((field, "google")) if not native else None
        if google and (google.source_report_date != operation.sort_date
                       or google.sort_name.casefold() != operation.sort_name.casefold()
                       or google.gateway_id != gateway_id
                       or operation.gateway_code.upper() != "RFD"
                       or operation.sort_name.casefold() != "night"):
            google = None
        row = (manual if manual and manual.value else None) or (google if google and google.value else None)
        result[field] = {
            "label": label, "value": row.value if row else None,
            "source": ("NeoRain entry" if row.data_source == "manual" else "Google RFD Recap") if row else "Unavailable",
            "editable": True, "version": manual.version if manual else 0,
            "manual_value": manual.value if manual else "",
            "kind": kind,
        }
    return result, google_status


def save_report_entry(operation, field, value, expected_version, user_id, *, current=False):
    if field not in REPORT_FIELDS:
        raise ValueError("Unknown recap field.")
    if field == "hub_actual_working" and _canonical_hub_working(operation, current=current) is not None:
        raise ValueError("NeoStaffing owns this completed working count.")
    normalized = normalize_report_value(field, value)
    gateway_id = _report_gateway_id(operation)
    if gateway_id is None:
        raise ValueError("This Sort does not have a valid gateway.")
    row = NeoRainReportEntry.query.filter_by(
        sort_date_operation_id=operation.id, field_name=field, data_source="manual"
    ).with_for_update().first()
    if (row.version if row else 0) != expected_version:
        raise ValueError("This recap value changed. Reload and try again.")
    if normalized is None:
        if row is not None:
            # A blank manual value deliberately falls back; retain its audit
            # identity/version so the clearing action remains attributable.
            row.value = ""
            row.version += 1
            row.updated_by_user_id = user_id
    elif row is None:
        db.session.add(NeoRainReportEntry(
            gateway_id=gateway_id, sort_date_operation_id=operation.id,
            sort_date=operation.sort_date, sort_name=operation.sort_name,
            field_name=field, value=normalized, data_source="manual",
            updated_by_user_id=user_id,
        ))
    else:
        row.value = normalized
        row.version += 1
        row.updated_by_user_id = user_id
    db.session.commit()


def _mission_rows(operation):
    missions = (SortDateMission.query.options(joinedload(SortDateMission.delay_info_rows),
                    selectinload(SortDateMission.neorain_mission_notes))
                .filter_by(sort_date_operation_id=operation.id).all())
    inbound = sorted((mission for mission in missions if mission.mission_type == "arrival"),
                     key=lambda mission: (mission.planned_datetime_utc or datetime.max, mission.id))
    outbound = sorted((mission for mission in missions if mission.mission_type == "departure"),
                      key=lambda mission: (mission.planned_datetime_utc or datetime.max, mission.id))
    return inbound, outbound


def _report_row(mission, *, arrival):
    row = {
        "mission_id": mission.id,
        "flight": mission.flight_number,
        "cancelled": mission_cancelled(mission),
        "mission_notes": tuple(note.text for note in mission.neorain_mission_notes),
    }
    if row["cancelled"]:
        return row
    actual = completed_actual(mission)
    minutes = completed_variance_minutes(mission)
    delays = tuple(sorted(mission.delay_info_rows, key=lambda item: item.id))
    return dict(row, tail=mission.assigned_tail_number, place=mission.origin if arrival else mission.destination,
        scheduled=format_local_hhmm(mission.planned_datetime_utc, mission.timezone or None),
        actual=format_local_hhmm(actual, mission.timezone or None),
        variance="—" if minutes is None else f"+{minutes}" if minutes > 0 else str(minutes),
        delay_details=delays, delays=tuple((item.code, item.notes or "") for item in delays))


def report_context(operation, *, current=False, missions=None):
    from app.neonodes.neorain.services import _inbound_late_summary, _outbound_late_summary

    fields, google_status = resolved_report_fields(operation, current=current)
    inbound, outbound = missions if missions is not None else _mission_rows(operation)
    report = {"operation": operation, "fields": fields, "google_status": google_status}
    for side, rows, summary in (("inbound", inbound, _inbound_late_summary),
                                ("outbound", outbound, _outbound_late_summary)):
        eligible = [mission for mission in rows if eligible_for_performance(mission)]
        report[side + "_rows"] = [_report_row(mission, arrival=side == "inbound") for mission in rows]
        report[side + "_late"] = summary(rows)["total"]
        report["eligible_" + side] = len(eligible)
        report["recorded_" + side] = sum(completed_actual(mission) is not None for mission in eligible)
    return report


def briefing_text(report):
    fields = report["fields"]
    def value(field):
        raw = fields[field]["value"]
        return f"{int(Decimal(raw)):,}" if raw is not None else "UNAVAILABLE"
    def average(summary):
        count = summary["aircraft_late"]
        return str((Decimal(summary["late_minutes"]) / Decimal(count)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)) if count else "0"
    lines = [
        f"{report['operation'].gateway_code} {report['operation'].sort_name}:", "",
        f"• HPS Plan {value('planned_volume')} vs Actual {value('actual_volume')}",
        "• Inbound",
        f"    ○ {report['inbound_late']['aircraft_late']} of {report['eligible_inbound']} flights arrived late for an average of {average(report['inbound_late'])} minutes late",
        "• Departures",
        f"    ○ {report['outbound_late']['aircraft_late']} of {report['eligible_outbound']} flights departed late for an average of {average(report['outbound_late'])} minutes late",
    ]
    if report["recorded_inbound"] < report["eligible_inbound"] or report["recorded_outbound"] < report["eligible_outbound"]:
        lines.append("    ○ IN PROGRESS — some actual Block-In/Block-Out times are not recorded")
    for row in report["inbound_rows"] + report["outbound_rows"]:
        if row["cancelled"]:
            lines.append(f"{row['flight']} — CANCELLED")
    for row in report["outbound_rows"]:
        if row["cancelled"] or not row["actual"]:
            continue
        flight = re.sub(r"^UPS0+(?=\d)", "UPS", row["flight"] or "", flags=re.IGNORECASE)
        details = f"{flight} {report['operation'].gateway_code}-{row['place'] or '—'} ATD: {row['actual']}"
        delays = [f"{code} {note}" if code and note else (code or note)
                  for code, note in row["delays"] if code or note]
        if delays:
            details += ", " + "; ".join(delays)
        lines.append("        ▪ " + details)
    return "\n".join(lines)
