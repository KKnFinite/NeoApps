from app.extensions import db
from app.models import MasterFlightSchedule, MotherBrainParkingRule, MotherBrainParkingSettings
from app.services.parking_aircraft import (
    PARKING_AIRCRAFT_TYPE_OPTIONS,
    normalize_parking_aircraft_type,
)
from app.services.building_lineup_parking_preferences import (
    BELT_PAIR_SUBJECT_TYPE,
    BUILDING_LINEUP_BELT_PARKING_PREFERENCE,
    BELT_PAIR_PREFERENCE_BEHAVIOR,
    BELT_PAIR_RAMP_OPTIONS,
    active_belt_pair_preference_map,
    belt_pair_preferences_for_context,
    belt_pair_ramp_label,
    normalize_belt_pair_key,
    save_belt_pair_preferences_from_form,
)


ORIGIN_RAMP_RESTRICTION = "origin_ramp_restriction"
ORIGIN_RAMP_PREFERENCE = "origin_ramp_preference"
ORIGIN_RAMP_REQUIREMENT = ORIGIN_RAMP_PREFERENCE
ARRIVAL_PARKING_PREFERENCE = "arrival_parking_preference"
ARRIVAL_PARKING_REQUIREMENT = "arrival_parking_requirement"
DEPARTURE_PARKING_PREFERENCE = "departure_parking_preference"
DEPARTURE_PARKING_REQUIREMENT = "departure_parking_requirement"
AIRCRAFT_TYPE_RAMP_RESTRICTION = "aircraft_type_ramp_restriction"
AIRCRAFT_TYPE_RAMP_PREFERENCE = "aircraft_type_ramp_preference"
BLOCKED_PARKING_POSITION = "blocked_parking_position"

PARKING_RULE_CATEGORIES = (
    ORIGIN_RAMP_RESTRICTION,
    ORIGIN_RAMP_PREFERENCE,
    ARRIVAL_PARKING_PREFERENCE,
    ARRIVAL_PARKING_REQUIREMENT,
    DEPARTURE_PARKING_PREFERENCE,
    DEPARTURE_PARKING_REQUIREMENT,
    AIRCRAFT_TYPE_RAMP_RESTRICTION,
    AIRCRAFT_TYPE_RAMP_PREFERENCE,
    BLOCKED_PARKING_POSITION,
    BUILDING_LINEUP_BELT_PARKING_PREFERENCE,
)

PARKING_RULE_EDITABLE_CATEGORIES = (
    ARRIVAL_PARKING_PREFERENCE,
    ARRIVAL_PARKING_REQUIREMENT,
    DEPARTURE_PARKING_PREFERENCE,
    DEPARTURE_PARKING_REQUIREMENT,
    AIRCRAFT_TYPE_RAMP_RESTRICTION,
    AIRCRAFT_TYPE_RAMP_PREFERENCE,
    BLOCKED_PARKING_POSITION,
)

RAMP_OPTIONS = (
    ("A", "Alpha"),
    ("B", "Bravo"),
    ("C", "Charlie"),
    ("D", "Delta"),
    ("E", "Echo"),
    ("R", "Remote"),
    ("THROAT", "09/10 Throat"),
)

RAMP_CODES = {code for code, _label in RAMP_OPTIONS}
VALID_PARKING_POSITIONS = {
    *(f"{ramp}{number:02d}" for ramp in ("A", "B", "C", "D", "E") for number in range(1, 11)),
    *(f"R{number:02d}" for number in range(1, 5)),
}
PARKING_TARGET_OPTIONS = RAMP_OPTIONS + tuple(
    (position, position)
    for position in sorted(
        VALID_PARKING_POSITIONS,
        key=lambda item: (0 if item[0] != "R" else 1, item[0], int(item[1:])),
    )
)
PARKING_POSITION_OPTIONS = tuple(
    (position, position)
    for position in sorted(
        VALID_PARKING_POSITIONS,
        key=lambda item: (0 if item[0] != "R" else 1, item[0], int(item[1:])),
    )
)

PHYSICAL_PARKING_RULES = (
    "Normal ramp banks are 01-04 and 05-08.",
    "Normal ramp banks use hard fill order within each bank.",
    "Remote R01-R04 uses hard fill order.",
    "09/10 throat parking is optional, with 10 filled before 9.",
    "767 aircraft use a two-slot footprint only inside normal banks.",
    "767 aircraft do not block adjacent positions in Remote, 09, or 10.",
)

DEFAULT_DEICE_SPACING_THRESHOLD_MINUTES = 15
DEFAULT_INBOUND_SAME_RAMP_SPACING_MINUTES = 5
DEFAULT_PREVENT_767_ADJACENT_TO_A300 = True
DEFAULT_FORCE_767_TO_POSITION_4_8 = True
DEFAULT_PREVENT_A300_IN_POSITION_5 = True


def ensure_parking_settings(gateway):
    settings = MotherBrainParkingSettings.query.filter_by(gateway_id=gateway.id).first()
    if not settings:
        settings = MotherBrainParkingSettings(
            gateway_id=gateway.id,
            gateway_code=gateway.code,
            include_remote_default=False,
            include_throat_default=False,
            deice_spacing_threshold_minutes=DEFAULT_DEICE_SPACING_THRESHOLD_MINUTES,
            preferred_max_per_ramp=None,
            inbound_same_ramp_spacing_minutes=DEFAULT_INBOUND_SAME_RAMP_SPACING_MINUTES,
            prevent_767_adjacent_to_a300=DEFAULT_PREVENT_767_ADJACENT_TO_A300,
            force_767_to_position_4_8=DEFAULT_FORCE_767_TO_POSITION_4_8,
            prevent_a300_in_position_5=DEFAULT_PREVENT_A300_IN_POSITION_5,
        )
        db.session.add(settings)
        db.session.flush()
        return settings

    settings.gateway_code = gateway.code
    if settings.deice_spacing_threshold_minutes is None:
        settings.deice_spacing_threshold_minutes = DEFAULT_DEICE_SPACING_THRESHOLD_MINUTES
    if settings.inbound_same_ramp_spacing_minutes is None:
        settings.inbound_same_ramp_spacing_minutes = DEFAULT_INBOUND_SAME_RAMP_SPACING_MINUTES
    if settings.prevent_767_adjacent_to_a300 is None:
        settings.prevent_767_adjacent_to_a300 = DEFAULT_PREVENT_767_ADJACENT_TO_A300
    if settings.force_767_to_position_4_8 is None:
        settings.force_767_to_position_4_8 = DEFAULT_FORCE_767_TO_POSITION_4_8
    if settings.prevent_a300_in_position_5 is None:
        settings.prevent_a300_in_position_5 = DEFAULT_PREVENT_A300_IN_POSITION_5
    return settings


def parking_rules_context(gateway, operation=None):
    settings = ensure_parking_settings(gateway)
    rules = (
        MotherBrainParkingRule.query.filter_by(gateway_id=gateway.id)
        .order_by(
            MotherBrainParkingRule.ramp_code.asc(),
            MotherBrainParkingRule.rule_category.asc(),
            MotherBrainParkingRule.subject_value.asc(),
            MotherBrainParkingRule.id.asc(),
        )
        .all()
    )
    grouped = {category: [] for category in PARKING_RULE_CATEGORIES}
    for rule in rules:
        grouped.setdefault(rule.rule_category, []).append(rule)

    return {
        "settings": settings,
        "rules_by_category": grouped,
        "rule_report": _parking_rule_report(settings, grouped),
        "ramp_options": RAMP_OPTIONS,
        "parking_target_options": PARKING_TARGET_OPTIONS,
        "parking_position_options": PARKING_POSITION_OPTIONS,
        "belt_pair_ramp_options": BELT_PAIR_RAMP_OPTIONS,
        "building_lineup_belt_preferences": belt_pair_preferences_for_context(gateway),
        "arrival_rule_options": _master_plan_rule_options(gateway, operation, "arrival"),
        "departure_rule_options": _master_plan_rule_options(gateway, operation, "departure"),
        "aircraft_type_options": PARKING_AIRCRAFT_TYPE_OPTIONS,
        "physical_rules": PHYSICAL_PARKING_RULES,
    }


def save_parking_rules_from_form(gateway, form):
    settings = ensure_parking_settings(gateway)
    if _form_updates_parking_settings(form):
        settings.include_remote_default = form.get("include_remote_default") == "1"
        settings.include_throat_default = form.get("include_throat_default") == "1"
        settings.deice_spacing_threshold_minutes = _nonnegative_int(
            form.get("deice_spacing_threshold_minutes"),
            default=DEFAULT_DEICE_SPACING_THRESHOLD_MINUTES,
        )
        settings.preferred_max_per_ramp = _optional_nonnegative_int(
            form.get("preferred_max_per_ramp")
        )
        settings.inbound_same_ramp_spacing_minutes = _nonnegative_int(
            form.get("inbound_same_ramp_spacing_minutes"),
            default=DEFAULT_INBOUND_SAME_RAMP_SPACING_MINUTES,
        )
        for field_name in (
            "prevent_767_adjacent_to_a300",
            "force_767_to_position_4_8",
            "prevent_a300_in_position_5",
        ):
            if field_name in form or "parking_rule_settings_present" in form:
                setattr(settings, field_name, form.get(field_name) == "1")

    _update_existing_rules(
        gateway,
        form,
        editable_categories=PARKING_RULE_EDITABLE_CATEGORIES,
    )
    for category in PARKING_RULE_EDITABLE_CATEGORIES:
        _add_new_rule(gateway, category, form)
    save_belt_pair_preferences_from_form(gateway, form)

    db.session.flush()
    return settings


def _form_updates_parking_settings(form):
    setting_keys = {
        "include_remote_default",
        "include_throat_default",
        "deice_spacing_threshold_minutes",
        "preferred_max_per_ramp",
        "inbound_same_ramp_spacing_minutes",
        "prevent_767_adjacent_to_a300",
        "force_767_to_position_4_8",
        "prevent_a300_in_position_5",
        "parking_rule_settings_present",
    }
    return any(key in form for key in setting_keys)


def _update_existing_rules(gateway, form, editable_categories=None):
    rule_ids = form.getlist("rule_ids")
    if not rule_ids:
        return
    editable_category_set = set(editable_categories or PARKING_RULE_CATEGORIES)

    rules = {
        str(rule.id): rule
        for rule in MotherBrainParkingRule.query.filter(
            MotherBrainParkingRule.gateway_id == gateway.id,
            MotherBrainParkingRule.id.in_([_int_or_none(rule_id) for rule_id in rule_ids]),
        ).all()
    }
    for rule_id in rule_ids:
        rule = rules.get(str(rule_id))
        if not rule:
            continue
        if rule.rule_category not in editable_category_set:
            continue
        if form.get(f"delete_rule_{rule.id}") == "1":
            db.session.delete(rule)
            continue
        subject = _normalize_subject(
            rule.subject_type,
            form.get(f"subject_value_{rule.id}"),
            existing_value=rule.subject_value,
        )
        ramp_code = _normalize_rule_target(
            rule.rule_category,
            subject,
            form.get(f"ramp_code_{rule.id}"),
        )
        if not subject or not ramp_code:
            db.session.delete(rule)
            continue
        duplicate = _duplicate_existing_rule(
            gateway,
            rule,
            subject,
            ramp_code,
        )
        if duplicate is not None:
            duplicate.active = form.get(f"active_{rule.id}") == "1"
            duplicate.note = _clean_note(form.get(f"note_{rule.id}"))
            db.session.delete(rule)
            continue
        rule.subject_value = subject
        rule.ramp_code = ramp_code
        if rule.rule_category in (ORIGIN_RAMP_REQUIREMENT, ARRIVAL_PARKING_REQUIREMENT, DEPARTURE_PARKING_REQUIREMENT):
            rule.rule_behavior = "required"
        elif rule.rule_category in (ARRIVAL_PARKING_PREFERENCE, DEPARTURE_PARKING_PREFERENCE):
            rule.rule_behavior = "preferred"
        rule.active = form.get(f"active_{rule.id}") == "1"
        rule.note = _clean_note(form.get(f"note_{rule.id}"))


def _add_new_rule(gateway, category, form):
    subject_type = _subject_type_for_category(category)
    subject = _normalize_subject(subject_type, form.get(f"new_{category}_subject"))
    ramp_code = _normalize_rule_target(category, subject, form.get(f"new_{category}_ramp"))
    if not subject or not ramp_code:
        return None

    rule = MotherBrainParkingRule.query.filter_by(
        gateway_id=gateway.id,
        rule_category=category,
        subject_type=subject_type,
        subject_value=subject,
        ramp_code=ramp_code,
        rule_behavior=_behavior_for_category(category),
    ).first()
    if not rule:
        rule = MotherBrainParkingRule(
            gateway_id=gateway.id,
            gateway_code=gateway.code,
            rule_category=category,
            subject_type=subject_type,
            subject_value=subject,
            ramp_code=ramp_code,
            rule_behavior=_behavior_for_category(category),
        )
        db.session.add(rule)
    else:
        rule.active = True
    rule.note = _clean_note(form.get(f"new_{category}_note"))
    return rule


def _duplicate_existing_rule(gateway, rule, subject, ramp_code):
    return MotherBrainParkingRule.query.filter(
        MotherBrainParkingRule.gateway_id == gateway.id,
        MotherBrainParkingRule.id != rule.id,
        MotherBrainParkingRule.rule_category == rule.rule_category,
        MotherBrainParkingRule.subject_type == rule.subject_type,
        MotherBrainParkingRule.subject_value == subject,
        MotherBrainParkingRule.ramp_code == ramp_code,
        MotherBrainParkingRule.rule_behavior == rule.rule_behavior,
    ).first()


def _subject_type_for_category(category):
    if category in (ORIGIN_RAMP_RESTRICTION, ORIGIN_RAMP_PREFERENCE):
        return "origin"
    if category in (ARRIVAL_PARKING_PREFERENCE, ARRIVAL_PARKING_REQUIREMENT):
        return "arrival_plan"
    if category in (DEPARTURE_PARKING_PREFERENCE, DEPARTURE_PARKING_REQUIREMENT):
        return "departure_plan"
    if category == BLOCKED_PARKING_POSITION:
        return "position"
    if category == BUILDING_LINEUP_BELT_PARKING_PREFERENCE:
        return BELT_PAIR_SUBJECT_TYPE
    return "aircraft_type"


def _behavior_for_category(category):
    if category in (ORIGIN_RAMP_REQUIREMENT, ARRIVAL_PARKING_REQUIREMENT, DEPARTURE_PARKING_REQUIREMENT):
        return "required"
    if category in (
        ORIGIN_RAMP_RESTRICTION,
        AIRCRAFT_TYPE_RAMP_RESTRICTION,
        BLOCKED_PARKING_POSITION,
    ):
        return "forbidden"
    if category == BUILDING_LINEUP_BELT_PARKING_PREFERENCE:
        return BELT_PAIR_PREFERENCE_BEHAVIOR
    return "preferred"


def _normalize_subject(subject_type, value, existing_value=None):
    text = str(value or "").strip().upper()
    if not text:
        return ""
    if subject_type == "origin":
        return "".join(character for character in text if character.isalnum())[:8]
    if subject_type == "position":
        return normalize_parking_position_code(text)
    if subject_type in {"arrival_plan", "departure_plan"}:
        return _normalize_schedule_rule_key(text)
    if subject_type == BELT_PAIR_SUBJECT_TYPE:
        return normalize_belt_pair_key(text)
    normalized = normalize_parking_aircraft_type(text, allow_unknown=False)
    if normalized:
        return normalized
    existing = str(existing_value or "").strip().upper()
    if existing and text == existing:
        return existing[:32]
    return ""


def _normalize_ramp(value):
    text = str(value or "").strip().upper()
    return text if text in RAMP_CODES else ""


def _normalize_rule_target(category, subject, value):
    if category == BLOCKED_PARKING_POSITION:
        return _ramp_for_position(subject)
    if category in (
        ARRIVAL_PARKING_PREFERENCE,
        ARRIVAL_PARKING_REQUIREMENT,
        DEPARTURE_PARKING_PREFERENCE,
        DEPARTURE_PARKING_REQUIREMENT,
    ):
        return _normalize_parking_target(value)
    return _normalize_ramp(value)


def _normalize_parking_target(value):
    text = str(value or "").strip().upper()
    if text in RAMP_CODES:
        return text
    return normalize_parking_position_code(text)


def normalize_parking_position_code(value):
    text = "".join(character for character in str(value or "").strip().upper() if character.isalnum())
    if len(text) < 2:
        return ""
    prefix = text[:1]
    digits = text[1:]
    if prefix not in {"A", "B", "C", "D", "E", "R"} or not digits.isdigit():
        return ""
    normalized = f"{prefix}{int(digits):02d}"
    return normalized if normalized in VALID_PARKING_POSITIONS else ""


def _ramp_for_position(position):
    position = normalize_parking_position_code(position)
    return "R" if position.startswith("R") else position[:1]


def active_blocked_parking_positions(gateway):
    return {
        normalize_parking_position_code(rule.subject_value)
        for rule in MotherBrainParkingRule.query.filter_by(
            gateway_id=gateway.id,
            rule_category=BLOCKED_PARKING_POSITION,
            subject_type="position",
            active=True,
        ).all()
        if normalize_parking_position_code(rule.subject_value)
    }


def parking_schedule_rule_key(mission_type, flight_number, station):
    flight = _normalize_schedule_flight(flight_number)
    station_code = _normalize_schedule_station(station)
    if not flight or not station_code:
        return ""
    return f"{flight}|{station_code}"


def parking_schedule_rule_label(subject_value):
    key = _normalize_schedule_rule_key(subject_value)
    if not key or "|" not in key:
        return str(subject_value or "").strip().upper()
    flight, station = key.split("|", 1)
    return f"{flight} / {station}"


def _master_plan_rule_options(gateway, operation, mission_type):
    query = MasterFlightSchedule.query.filter_by(
        gateway_code=gateway.code,
        mission_type=mission_type,
        active=True,
    )
    if operation is not None:
        query = query.filter(MasterFlightSchedule.sort_name == operation.sort_name)
    schedules = query.order_by(
        MasterFlightSchedule.sort_name.asc(),
        MasterFlightSchedule.planned_time_local.asc(),
        MasterFlightSchedule.flight_number.asc(),
        MasterFlightSchedule.id.asc(),
    ).all()
    options = {}
    for schedule in schedules:
        station = schedule.origin if mission_type == "arrival" else schedule.destination
        key = parking_schedule_rule_key(mission_type, schedule.flight_number, station)
        if not key or key in options:
            continue
        label = parking_schedule_rule_label(key)
        if operation is None:
            label = f"{label} ({str(schedule.sort_name or '').upper()})"
        options[key] = label
    return tuple(options.items())


def _parking_rule_report(settings, grouped):
    active_blocked = _active_rule_summaries(grouped.get(BLOCKED_PARKING_POSITION, []))
    active_arrival_required = _active_rule_summaries(
        grouped.get(ARRIVAL_PARKING_REQUIREMENT, [])
    )
    active_arrival_preferred = _active_rule_summaries(
        grouped.get(ARRIVAL_PARKING_PREFERENCE, [])
    )
    active_departure_required = _active_rule_summaries(
        grouped.get(DEPARTURE_PARKING_REQUIREMENT, [])
    )
    active_departure_preferred = _active_rule_summaries(
        grouped.get(DEPARTURE_PARKING_PREFERENCE, [])
    )
    active_aircraft_restrictions = _active_rule_summaries(
        grouped.get(AIRCRAFT_TYPE_RAMP_RESTRICTION, [])
    )
    active_aircraft_preferences = _active_rule_summaries(
        grouped.get(AIRCRAFT_TYPE_RAMP_PREFERENCE, [])
    )
    active_belt_preferences = _belt_preference_summaries(
        active_belt_pair_preference_map(settings.gateway)
    )
    return {
        "hard_rules": (
            "Physical fill order",
            "ETA order",
            "767 footprint",
            "767 / A300 separation when enabled",
            "767 04/08 forced placement when enabled",
            "A300 Position 5 restriction when enabled",
            "Slot 2 overflow rules",
            "Remote toggle behavior",
            "9/10 toggle behavior",
            "Arrival required parking",
            "Departure required parking",
            "Aircraft type restrictions",
            "Hard-blocked parking positions",
        ),
        "soft_rules": (
            "Arrival preferred parking",
            "Departure preferred parking",
            "Aircraft type preferences",
            "Ramp balancing: active soft rule across Alpha, Bravo, Charlie, Delta, and Echo",
            "Inbound ETA same-ramp spacing: active soft rule when threshold is above 0; close arrivals prefer different ramps when alternatives exist",
            "01-04 / 05-08 side balance: active soft rule within each normal ramp",
            "Preferred Max Per Ramp: active soft limit when set; it can be exceeded if needed",
            "757 preferred on 04/08 positions: active soft rule",
            "Avoid 04/08 when valid alternatives exist: active soft rule",
            "Blocked-position relief for 04/08: active soft rule",
            "Building Lineup Belt Parking Preferences: active soft rule when a departure destination maps to one configured belt pair",
            "Deice spacing: active soft rule when threshold is above 0; disabled at 0 and skipped automatically when needed to keep suggestions responsive",
        ),
        "current_settings": {
            "include_remote_default": "ON" if settings.include_remote_default else "OFF",
            "include_throat_default": "ON" if settings.include_throat_default else "OFF",
            "deice_threshold": _deice_status_summary(settings.deice_spacing_threshold_minutes),
            "inbound_same_ramp_spacing": _inbound_spacing_summary(
                settings.inbound_same_ramp_spacing_minutes
            ),
            "preferred_max_per_ramp": (
                str(settings.preferred_max_per_ramp)
                if settings.preferred_max_per_ramp is not None
                else "NONE"
            ),
            "prevent_767_adjacent_to_a300": (
                "ON" if settings.prevent_767_adjacent_to_a300 else "OFF"
            ),
            "force_767_to_position_4_8": (
                "ON" if settings.force_767_to_position_4_8 else "OFF"
            ),
            "prevent_a300_in_position_5": (
                "ON" if settings.prevent_a300_in_position_5 else "OFF"
            ),
            "active_blocked_positions": active_blocked or ("NONE",),
            "active_arrival_required": active_arrival_required or ("NONE",),
            "active_arrival_preferred": active_arrival_preferred or ("NONE",),
            "active_departure_required": active_departure_required or ("NONE",),
            "active_departure_preferred": active_departure_preferred or ("NONE",),
            "active_aircraft_restrictions": active_aircraft_restrictions or ("NONE",),
            "active_aircraft_preferences": active_aircraft_preferences or ("NONE",),
            "active_building_lineup_belt_preferences": active_belt_preferences or ("NONE",),
        },
    }


def _belt_preference_summaries(preferences):
    summaries = []
    for pair_key, ramps in sorted((preferences or {}).items()):
        if not ramps:
            continue
        labels = ", ".join(belt_pair_ramp_label(ramp) for ramp in ramps)
        summaries.append(f"{pair_key} prefers {labels}")
    return tuple(summaries)


def _active_rule_summaries(rules):
    summaries = []
    for rule in rules:
        if not rule.active:
            continue
        subject = str(rule.subject_value or "").strip().upper()
        ramp = str(rule.ramp_code or "").strip().upper()
        if rule.rule_category == BLOCKED_PARKING_POSITION:
            summaries.append(subject)
        elif rule.rule_category in (ARRIVAL_PARKING_REQUIREMENT, DEPARTURE_PARKING_REQUIREMENT):
            summaries.append(f"{parking_schedule_rule_label(subject)} must use {_parking_target_label(ramp)}")
        elif rule.rule_category in (ARRIVAL_PARKING_PREFERENCE, DEPARTURE_PARKING_PREFERENCE):
            summaries.append(f"{parking_schedule_rule_label(subject)} prefers {_parking_target_label(ramp)}")
        elif rule.rule_category == AIRCRAFT_TYPE_RAMP_RESTRICTION:
            summaries.append(f"{subject} not allowed on {ramp}")
        elif rule.rule_category == AIRCRAFT_TYPE_RAMP_PREFERENCE:
            summaries.append(f"{subject} prefers {ramp}")
    return tuple(sorted(summaries))


def _parking_target_label(value):
    target = str(value or "").strip().upper()
    if target == "THROAT":
        return "09/10"
    return target


def _normalize_schedule_rule_key(value):
    text = str(value or "").strip().upper().replace("/", "|").replace(" ", "")
    if "|" not in text:
        return ""
    flight, station = text.split("|", 1)
    flight = _normalize_schedule_flight(flight)
    station = _normalize_schedule_station(station)
    if not flight or not station:
        return ""
    return f"{flight}|{station}"


def _normalize_schedule_flight(value):
    return "".join(character for character in str(value or "").strip().upper() if character.isalnum())[:16]


def _normalize_schedule_station(value):
    return "".join(character for character in str(value or "").strip().upper() if character.isalnum())[:8]


def _deice_status_summary(threshold_minutes):
    try:
        threshold = max(0, int(threshold_minutes))
    except (TypeError, ValueError):
        threshold = DEFAULT_DEICE_SPACING_THRESHOLD_MINUTES
    if threshold <= 0:
        return "0 min / DISABLED"
    return f"{threshold} min / SOFT SCORING ENABLED"


def _inbound_spacing_summary(threshold_minutes):
    try:
        threshold = max(0, int(threshold_minutes))
    except (TypeError, ValueError):
        threshold = DEFAULT_INBOUND_SAME_RAMP_SPACING_MINUTES
    if threshold <= 0:
        return "0 min / DISABLED"
    return f"{threshold} min / SOFT SCORING ENABLED"


def _clean_note(value):
    return str(value or "").strip()[:255]


def _nonnegative_int(value, default=0):
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _optional_nonnegative_int(value):
    text = str(value or "").strip()
    if not text:
        return None
    return _nonnegative_int(text, default=0)


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
