"""Bounded, read-only NeoStaffing Vacation report data and PDF rendering."""

from collections import defaultdict
from datetime import datetime, timedelta
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from sqlalchemy import func
from sqlalchemy.orm import selectinload

from app.models import (
    StaffingPerson,
    StaffingVacationDaySelection,
    StaffingVacationManagementSelection,
    StaffingVacationUnionCalendar,
    StaffingVacationUnionSelection,
    StaffingWorkAssignment,
)
from app.services import neostaffing_vacation as vacation_service


CALENDAR_KIND_MANAGEMENT = "management"
CALENDAR_KIND_UNION = "union"
UNION_FILTERS = frozenset({"pt", "ft", "both"})


def accessible_vacation_calendars(vacation_year, user):
    """Return accessible dynamic Management and persisted Union calendars."""
    year = vacation_service.normalize_vacation_year(vacation_year)
    management = vacation_service.management_vacation_context(year, user)
    calendars = (
        StaffingVacationUnionCalendar.query.options(
            selectinload(StaffingVacationUnionCalendar.scopes),
            selectinload(StaffingVacationUnionCalendar.shares),
        )
        .filter_by(vacation_year=year, active=True)
        .order_by(
            StaffingVacationUnionCalendar.calendar_type,
            func.lower(StaffingVacationUnionCalendar.name),
            StaffingVacationUnionCalendar.id,
        )
        .all()
    )
    hierarchy = vacation_service.vacation_hierarchy()
    return {
        "vacation_year": year,
        "management": [
            {
                "kind": CALENDAR_KIND_MANAGEMENT,
                "id": row["area"].id,
                "name": f"Management Vacation Calendar - {row['area'].name}",
                "scope": row["path"],
            }
            for row in management["areas"]
        ],
        "union": [
            {
                "kind": CALENDAR_KIND_UNION,
                "id": calendar.id,
                "name": _union_calendar_display_name(calendar, hierarchy),
                "scope": vacation_service.union_calendar_scope_label(
                    calendar, hierarchy
                ),
                "calendar_type": calendar.calendar_type,
            }
            for calendar in calendars
            if vacation_service.can_view_union_calendar(calendar, user)
        ],
    }


def vacation_calendar_report_data(kind, identifier, vacation_year, user):
    year = vacation_service.normalize_vacation_year(vacation_year)
    if kind == CALENDAR_KIND_MANAGEMENT:
        return _management_calendar_report_data(identifier, year, user)
    if kind == CALENDAR_KIND_UNION:
        return _union_calendar_report_data(identifier, year, user)
    raise ValueError("Select a valid vacation calendar.")


def union_seniority_scope_options():
    hierarchy = vacation_service.vacation_hierarchy()
    return [
        {"unit": unit, "path": vacation_service.unit_path(unit, hierarchy)}
        for unit in hierarchy["units"]
        if unit.unit_type in vacation_service.VACATION_UNION_SCOPE_TYPES
    ]


def union_seniority_report_data(scope_id, classification_filter):
    hierarchy = vacation_service.vacation_hierarchy()
    try:
        unit_id = int(scope_id)
    except (TypeError, ValueError) as error:
        raise ValueError("Select a valid Org Chart scope.") from error
    scope = hierarchy["by_id"].get(unit_id)
    if not scope or scope.unit_type not in vacation_service.VACATION_UNION_SCOPE_TYPES:
        raise ValueError("Select a valid Org Chart scope.")
    filter_value = str(classification_filter or "").strip().lower()
    if filter_value not in UNION_FILTERS:
        raise ValueError("Select PT Union, FT Union, or both.")
    classifications = set()
    if filter_value in {"pt", "both"}:
        classifications.update(vacation_service.VACATION_PT_CLASSIFICATIONS)
    if filter_value in {"ft", "both"}:
        classifications.update(vacation_service.VACATION_FT_CLASSIFICATIONS)
    work_area_ids = vacation_service._scope_work_area_ids({unit_id}, hierarchy)
    people = (
        StaffingPerson.query.join(StaffingWorkAssignment)
        .filter(
            StaffingPerson.active.is_(True),
            StaffingPerson.employee_status == "active",
            StaffingPerson.classification.in_(classifications),
            StaffingWorkAssignment.active.is_(True),
            StaffingWorkAssignment.work_area_unit_id.in_(work_area_ids or {-1}),
        )
        .order_by(
            StaffingPerson.seniority_date,
            func.lower(StaffingPerson.last_name),
            func.lower(StaffingPerson.first_name),
            StaffingPerson.id,
        )
        .all()
    )
    seen = set()
    ordered = []
    for person in people:
        if person.id not in seen:
            seen.add(person.id)
            ordered.append(person)
    filter_label = {
        "pt": "PT Union",
        "ft": "FT Union",
        "both": "PT + FT Union",
    }[filter_value]
    return {
        "title": "Union Seniority List",
        "scope": vacation_service.unit_path(scope, hierarchy),
        "classification_filter": filter_value,
        "classification_label": filter_label,
        "rows": [
            {
                "number": number,
                "last_name": person.last_name,
                "first_name": person.first_name,
                "employee_id": person.employee_id,
                "seniority_date": person.seniority_date,
            }
            for number, person in enumerate(ordered, start=1)
        ],
    }


def build_vacation_calendar_pdf(report, *, created_on=None):
    created_on = created_on or datetime.now()
    output = BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=landscape(letter),
        leftMargin=0.35 * inch,
        rightMargin=0.35 * inch,
        topMargin=0.3 * inch,
        bottomMargin=0.3 * inch,
        title=f"{report['title']} - {report['vacation_year']}",
    )
    styles = _pdf_styles()
    heading = KeepTogether(
        [
            Paragraph(
                _escape(f"{report['title']} - {report['vacation_year']}"),
                styles["title"],
            ),
            Paragraph(
                f"Created {created_on.strftime('%B %d, %Y')}", styles["created"]
            ),
            Spacer(1, 0.08 * inch),
        ]
    )
    story = [heading]
    pinned_rows = report.get("pinned_rows", ())
    if pinned_rows:
        story.extend(
            [
                Paragraph("Pinned Next Level - Read Only", styles["metadata"]),
                Table(
                    [
                        ["Pinned Person", "Approved Availability"],
                        *[
                            [
                                _escape(row["person"]),
                                _pdf_lines(row["availability"], styles["cell"]),
                            ]
                            for row in pinned_rows
                        ],
                    ],
                    colWidths=[2.25 * inch, 7.9 * inch],
                    repeatRows=1,
                    hAlign="CENTER",
                    style=_table_style(),
                ),
                Spacer(1, 0.1 * inch),
            ]
        )
    rows = [["Week Ending", "Approved Whole Weeks", "Approved Single Days"]]
    for week in report["weeks"]:
        rows.append(
            [
                week["week_ending"].strftime("%b %d, %Y"),
                _pdf_lines(week["whole_week_entries"], styles["cell"]),
                _pdf_lines(week["day_entries"], styles["cell"]),
            ]
        )
    table = Table(
        rows,
        colWidths=[1.05 * inch, 4.55 * inch, 4.55 * inch],
        repeatRows=1,
        hAlign="CENTER",
    )
    table.setStyle(_table_style())
    story.append(table)
    document.build(story)
    output.seek(0)
    return output


def build_union_seniority_pdf(report, *, created_on=None):
    created_on = created_on or datetime.now()
    output = BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=letter,
        leftMargin=0.45 * inch,
        rightMargin=0.45 * inch,
        topMargin=0.35 * inch,
        bottomMargin=0.35 * inch,
        title=report["title"],
    )
    styles = _pdf_styles()
    heading = KeepTogether(
        [
            Paragraph(report["title"], styles["title"]),
            Paragraph(
                f"Created {created_on.strftime('%B %d, %Y')}", styles["created"]
            ),
            Paragraph(
                _escape(f"{report['scope']} | {report['classification_label']}"),
                styles["metadata"],
            ),
            Spacer(1, 0.1 * inch),
        ]
    )
    rows = [["#", "Last Name", "First Name", "Employee ID", "Seniority Date"]]
    rows.extend(
        [
            row["number"],
            row["last_name"],
            row["first_name"],
            row["employee_id"],
            row["seniority_date"].strftime("%m/%d/%Y"),
        ]
        for row in report["rows"]
    )
    table = Table(
        rows,
        colWidths=[0.38 * inch, 1.75 * inch, 1.75 * inch, 1.45 * inch, 1.35 * inch],
        repeatRows=1,
        hAlign="CENTER",
    )
    table.setStyle(_table_style())
    document.build([heading, table])
    output.seek(0)
    return output


def _management_calendar_report_data(area_id, year, user):
    try:
        requested_id = int(area_id)
    except (TypeError, ValueError) as error:
        raise ValueError("Select a valid Management vacation calendar.") from error
    context = vacation_service.management_vacation_context(year, user)
    area_row = next(
        (row for row in context["areas"] if row["area"].id == requested_id), None
    )
    if not area_row:
        raise ValueError("The Management vacation calendar is not available.")
    whole_by_week = defaultdict(list)
    day_by_week = defaultdict(list)
    for person_row in area_row["person_rows"]:
        person = person_row["person"]
        label = f"{person.last_name}, {person.first_name}"
        for selection in person_row["selections"]:
            whole_by_week[selection.week_ending].append(label)
        for day in person_row["day_items"]:
            if day.status == "scheduled":
                week_ending = _week_ending(day.vacation_date)
                day_by_week[week_ending].append(
                    f"{day.vacation_date.strftime('%a %m/%d')} - {label} - "
                    f"{day.item_type.replace('_', ' ').title()}"
                )
    report = _calendar_report_contract(
        f"Management Vacation Calendar - {area_row['area'].name}",
        year,
        context["weeks"],
        whole_by_week,
        day_by_week,
    )
    report["pinned_rows"] = [
        {
            "person": (
                f"{pinned['person'].last_name}, {pinned['person'].first_name} "
                f"({pinned['person'].classification.replace('_', ' ').title()})"
            ),
            "availability": [
                (
                    f"{item['label']} - "
                    f"{item['date'].strftime('%b %d, %Y')}"
                )
                for item in pinned["availability"]
            ],
        }
        for pinned in area_row["pinned_rows"]
    ]
    return report


def _union_calendar_report_data(calendar_id, year, user):
    try:
        requested_id = int(calendar_id)
    except (TypeError, ValueError) as error:
        raise ValueError("Select a valid Union vacation calendar.") from error
    calendar = (
        StaffingVacationUnionCalendar.query.options(
            selectinload(StaffingVacationUnionCalendar.scopes),
            selectinload(StaffingVacationUnionCalendar.shares),
        )
        .filter_by(id=requested_id, vacation_year=year, active=True)
        .first()
    )
    if not calendar or not vacation_service.can_view_union_calendar(calendar, user):
        raise ValueError("The Union vacation calendar is not available.")
    hierarchy = vacation_service.vacation_hierarchy()
    members = vacation_service.union_calendar_members(calendar)
    member_ids = {person.id for person in members}
    names = {
        person.id: f"{person.last_name}, {person.first_name}" for person in members
    }
    selections = (
        StaffingVacationUnionSelection.query.filter(
            StaffingVacationUnionSelection.vacation_year == year,
            StaffingVacationUnionSelection.status == "approved",
            StaffingVacationUnionSelection.staffing_person_id.in_(member_ids or {-1}),
        )
        .order_by(
            StaffingVacationUnionSelection.week_ending,
            StaffingVacationUnionSelection.staffing_person_id,
            StaffingVacationUnionSelection.id,
        )
        .all()
    )
    days = (
        StaffingVacationDaySelection.query.filter(
            StaffingVacationDaySelection.vacation_year == year,
            StaffingVacationDaySelection.status == "scheduled",
            StaffingVacationDaySelection.staffing_person_id.in_(member_ids or {-1}),
        )
        .order_by(
            StaffingVacationDaySelection.vacation_date,
            StaffingVacationDaySelection.staffing_person_id,
            StaffingVacationDaySelection.id,
        )
        .all()
    )
    whole_by_week = defaultdict(list)
    for selection in selections:
        whole_by_week[selection.week_ending].append(names[selection.staffing_person_id])
    day_by_week = defaultdict(list)
    for day in days:
        day_by_week[_week_ending(day.vacation_date)].append(
            f"{day.vacation_date.strftime('%a %m/%d')} - "
            f"{names[day.staffing_person_id]} - {day.item_type.replace('_', ' ').title()}"
        )
    return _calendar_report_contract(
        _union_calendar_display_name(calendar, hierarchy),
        year,
        vacation_service.vacation_year_weeks(year),
        whole_by_week,
        day_by_week,
    )


def _calendar_report_contract(title, year, weeks, whole_by_week, day_by_week):
    return {
        "title": title,
        "vacation_year": year,
        "weeks": [
            {
                "week_ending": week.week_ending,
                "whole_week_entries": sorted(
                    whole_by_week.get(week.week_ending, ()), key=str.casefold
                ),
                "day_entries": sorted(
                    day_by_week.get(week.week_ending, ()), key=str.casefold
                ),
            }
            for week in weeks
        ],
    }


def _union_calendar_display_name(calendar, hierarchy):
    if calendar.calendar_type == "official":
        return vacation_service.generated_official_calendar_name(
            {scope.staffing_unit_id for scope in calendar.scopes},
            calendar.include_part_time,
            calendar.include_full_time,
            hierarchy,
        )
    return calendar.name


def _week_ending(day):
    return day + timedelta(days=(5 - day.weekday()) % 7)


def _pdf_styles():
    styles = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "VacationReportTitle",
            parent=styles["Title"],
            fontName="Helvetica-Bold",
            fontSize=13,
            leading=15,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#152b2b"),
            spaceAfter=1,
        ),
        "created": ParagraphStyle(
            "VacationReportCreated",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=7,
            leading=8,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#526565"),
        ),
        "metadata": ParagraphStyle(
            "VacationReportMetadata",
            parent=styles["Normal"],
            fontName="Helvetica-Bold",
            fontSize=7.5,
            leading=9,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#274848"),
        ),
        "cell": ParagraphStyle(
            "VacationReportCell",
            parent=styles["Normal"],
            fontName="Helvetica",
            fontSize=6.5,
            leading=8,
            textColor=colors.HexColor("#172323"),
        ),
    }


def _table_style():
    return TableStyle(
        [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#173f40")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 7),
            ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 1), (-1, -1), 6.5),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#9dafaf")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f6f6")]),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]
    )


def _pdf_lines(values, style):
    return Paragraph("<br/>".join(_escape(value) for value in values) or "-", style)


def _escape(value):
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
