from app.extensions import db
from app.models import MotherBrainParkingRule, MotherBrainParkingSettings
from app.services.parking_aircraft import (
    PARKING_AIRCRAFT_TYPE_OPTIONS,
    normalize_parking_aircraft_type,
)


ORIGIN_RAMP_RESTRICTION = "origin_ramp_restriction"
ORIGIN_RAMP_PREFERENCE = "origin_ramp_preference"
ORIGIN_RAMP_REQUIREMENT = ORIGIN_RAMP_PREFERENCE
AIRCRAFT_TYPE_RAMP_RESTRICTION = "aircraft_type_ramp_restriction"
AIRCRAFT_TYPE_RAMP_PREFERENCE = "aircraft_type_ramp_preference"
BLOCKED_PARKING_POSITION = "blocked_parking_position"

PARKING_RULE_CATEGORIES = (
    ORIGIN_RAMP_RESTRICTION,
    ORIGIN_RAMP_PREFERENCE,
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

PHYSICAL_PARKING_RULES = (
    "Normal ramp banks are 01-04 and 05-08.",
    "Normal ramp banks use hard fill order within each bank.",
    "Remote R01-R04 uses hard fill order.",
    "09/10 throat parking is optional, with 10 filled before 9.",
    "767 aircraft use a two-slot footprint only inside normal banks.",
    "767 aircraft do not block adjacent positions in Remote, 09, or 10.",
)

DEFAULT_DEICE_SPACING_THRESHOLD_MINUTES = 15


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
        )
        db.session.add(settings)
        db.session.flush()
        return settings

    settings.gateway_code = gateway.code
    if settings.deice_spacing_threshold_minutes is None:
        settings.deice_spacing_threshold_minutes = DEFAULT_DEICE_SPACING_THRESHOLD_MINUTES
    return settings


def parking_rules_context(gateway):
    settings = ensure_parking_settings(gateway)
    rules = (
        MotherBrainParkingRule.query.filter_by(gateway_id=gateway.id)
        .order_by(
            MotherBrainParkingRule.rule_category.asc(),
            MotherBrainParkingRule.subject_value.asc(),
            MotherBrainParkingRule.ramp_code.asc(),
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
        "aircraft_type_options": PARKING_AIRCRAFT_TYPE_OPTIONS,
        "physical_rules": PHYSICAL_PARKING_RULES,
    }


def save_parking_rules_from_form(gateway, form):
    settings = ensure_parking_settings(gateway)
    settings.include_remote_default = form.get("include_remote_default") == "1"
    settings.include_throat_default = form.get("include_throat_default") == "1"
    settings.deice_spacing_threshold_minutes = _nonnegative_int(
        form.get("deice_spacing_threshold_minutes"),
        default=DEFAULT_DEICE_SPACING_THRESHOLD_MINUTES,
    )
    settings.preferred_max_per_ramp = _optional_nonnegative_int(
        form.get("preferred_max_per_ramp")
    )

    _update_existing_rules(gateway, form)
    for category in PARKING_RULE_CATEGORIES:
        _add_new_rule(gateway, category, form)

    db.session.flush()
    return settings


def _update_existing_rules(gateway, form):
    rule_ids = form.getlist("rule_ids")
    if not rule_ids:
        return

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
        if form.get(f"delete_rule_{rule.id}") == "1":
            db.session.delete(rule)
            continue
        subject = _normalize_subject(
            rule.subject_type,
            form.get(f"subject_value_{rule.id}"),
            existing_value=rule.subject_value,
        )
        ramp_code = (
            _ramp_for_position(subject)
            if rule.rule_category == BLOCKED_PARKING_POSITION
            else _normalize_ramp(form.get(f"ramp_code_{rule.id}"))
        )
        if not subject or not ramp_code:
            db.session.delete(rule)
            continue
        rule.subject_value = subject
        rule.ramp_code = ramp_code
        if rule.rule_category == ORIGIN_RAMP_REQUIREMENT:
            rule.rule_behavior = "required"
        rule.active = form.get(f"active_{rule.id}") == "1"
        rule.note = _clean_note(form.get(f"note_{rule.id}"))


def _add_new_rule(gateway, category, form):
    subject_type = _subject_type_for_category(category)
    subject = _normalize_subject(subject_type, form.get(f"new_{category}_subject"))
    ramp_code = (
        _ramp_for_position(subject)
        if category == BLOCKED_PARKING_POSITION
        else _normalize_ramp(form.get(f"new_{category}_ramp"))
    )
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


def _subject_type_for_category(category):
    if category in (ORIGIN_RAMP_RESTRICTION, ORIGIN_RAMP_PREFERENCE):
        return "origin"
    if category == BLOCKED_PARKING_POSITION:
        return "position"
    return "aircraft_type"


def _behavior_for_category(category):
    if category == ORIGIN_RAMP_REQUIREMENT:
        return "required"
    if category in (
        ORIGIN_RAMP_RESTRICTION,
        AIRCRAFT_TYPE_RAMP_RESTRICTION,
        BLOCKED_PARKING_POSITION,
    ):
        return "forbidden"
    return "preferred"


def _normalize_subject(subject_type, value, existing_value=None):
    text = str(value or "").strip().upper()
    if not text:
        return ""
    if subject_type == "origin":
        return "".join(character for character in text if character.isalnum())[:8]
    if subject_type == "position":
        return normalize_parking_position_code(text)
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


def _parking_rule_report(settings, grouped):
    active_blocked = _active_rule_summaries(grouped.get(BLOCKED_PARKING_POSITION, []))
    active_origin_requirements = _active_rule_summaries(grouped.get(ORIGIN_RAMP_PREFERENCE, []))
    active_aircraft_restrictions = _active_rule_summaries(
        grouped.get(AIRCRAFT_TYPE_RAMP_RESTRICTION, [])
    )
    active_aircraft_preferences = _active_rule_summaries(
        grouped.get(AIRCRAFT_TYPE_RAMP_PREFERENCE, [])
    )
    return {
        "hard_rules": (
            "Physical fill order",
            "ETA order",
            "767 footprint",
            "Slot 2 overflow rules",
            "Remote toggle behavior",
            "9/10 toggle behavior",
            "Origin ramp requirements",
            "Aircraft type restrictions",
            "Hard-blocked parking positions",
        ),
        "soft_rules": (
            "Aircraft type preferences",
            "Ramp balancing: active soft rule across Alpha, Bravo, Charlie, Delta, and Echo",
            "Preferred Max Per Ramp: active soft limit when set; it can be exceeded if needed",
            "757 preferred on 04/08 positions: active soft rule",
            "Avoid 04/08 when valid alternatives exist: active soft rule",
            "Blocked-position relief for 04/08: active soft rule",
            "Deice spacing setting/status",
        ),
        "current_settings": {
            "include_remote_default": "ON" if settings.include_remote_default else "OFF",
            "include_throat_default": "ON" if settings.include_throat_default else "OFF",
            "deice_threshold": f"{settings.deice_spacing_threshold_minutes} min",
            "preferred_max_per_ramp": (
                str(settings.preferred_max_per_ramp)
                if settings.preferred_max_per_ramp is not None
                else "NONE"
            ),
            "active_blocked_positions": active_blocked or ("NONE",),
            "active_origin_requirements": active_origin_requirements or ("NONE",),
            "active_aircraft_restrictions": active_aircraft_restrictions or ("NONE",),
            "active_aircraft_preferences": active_aircraft_preferences or ("NONE",),
        },
    }


def _active_rule_summaries(rules):
    summaries = []
    for rule in rules:
        if not rule.active:
            continue
        subject = str(rule.subject_value or "").strip().upper()
        ramp = str(rule.ramp_code or "").strip().upper()
        if rule.rule_category == BLOCKED_PARKING_POSITION:
            summaries.append(subject)
        elif rule.rule_category == ORIGIN_RAMP_PREFERENCE:
            summaries.append(f"{subject} -> {ramp}")
        elif rule.rule_category == AIRCRAFT_TYPE_RAMP_RESTRICTION:
            summaries.append(f"{subject} not allowed on {ramp}")
        elif rule.rule_category == AIRCRAFT_TYPE_RAMP_PREFERENCE:
            summaries.append(f"{subject} prefers {ramp}")
    return tuple(sorted(summaries))


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
