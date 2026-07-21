from app.extensions import db
from app.models import MotherBrainParkingRule, NeoErmacBuildingLineup
from app.services.neoermac_building_lineup import (
    BUILDING_LINEUP_BELT_GROUPS,
    DESTINATION_FIELDS,
    normalize_destination,
)


BUILDING_LINEUP_BELT_PARKING_PREFERENCE = "building_lineup_belt_parking_preference"
BELT_PAIR_SUBJECT_TYPE = "building_lineup_belt_pair"
BELT_PAIR_PREFERENCE_BEHAVIOR = "preferred"

BELT_PAIR_RAMP_OPTIONS = (
    ("R", "Remote"),
    ("A", "Alpha"),
    ("B", "Bravo"),
    ("C", "Charlie"),
    ("D", "Delta"),
    ("E", "Echo"),
)
BELT_PAIR_RAMP_CODES = {code for code, _label in BELT_PAIR_RAMP_OPTIONS}


def _door_number(door):
    return str(door or "").strip().upper().removeprefix("D")


BUILDING_LINEUP_PARKING_BELT_PAIRS = tuple(
    {
        "runout_key": runout_key,
        "pair_key": f"{_door_number(start_door)}/{_door_number(end_door)}",
        "label": f"{_door_number(start_door)}/{_door_number(end_door)}",
        "start_door": start_door,
        "end_door": end_door,
        "belt_names": belt_names,
    }
    for runout_key, start_door, end_door, belt_names in BUILDING_LINEUP_BELT_GROUPS
    if end_door != "D37"
)
VALID_BELT_PAIR_KEYS = {pair["pair_key"] for pair in BUILDING_LINEUP_PARKING_BELT_PAIRS}
RUNOUT_KEY_TO_PAIR = {
    pair["runout_key"]: pair["pair_key"] for pair in BUILDING_LINEUP_PARKING_BELT_PAIRS
}


def parking_belt_preference_form_field(pair_key):
    pair_key = normalize_belt_pair_key(pair_key)
    if not pair_key:
        return ""
    return f"building_lineup_belt_preference_{pair_key.replace('/', '_')}"


def active_belt_pair_preference_map(gateway):
    preferences = {
        pair["pair_key"]: []
        for pair in BUILDING_LINEUP_PARKING_BELT_PAIRS
    }
    rules = MotherBrainParkingRule.query.filter_by(
        gateway_id=gateway.id,
        rule_category=BUILDING_LINEUP_BELT_PARKING_PREFERENCE,
        subject_type=BELT_PAIR_SUBJECT_TYPE,
        rule_behavior=BELT_PAIR_PREFERENCE_BEHAVIOR,
        active=True,
    ).all()
    for rule in rules:
        pair_key = normalize_belt_pair_key(rule.subject_value)
        ramp = normalize_belt_pair_ramp(rule.ramp_code)
        if not pair_key or not ramp:
            continue
        if ramp not in preferences[pair_key]:
            preferences[pair_key].append(ramp)
    return {
        pair_key: tuple(ramps)
        for pair_key, ramps in preferences.items()
    }


def belt_pair_preferences_for_context(gateway):
    preferences = active_belt_pair_preference_map(gateway)
    return [
        {
            **pair,
            "field_name": parking_belt_preference_form_field(pair["pair_key"]),
            "selected_ramps": preferences.get(pair["pair_key"], ()),
        }
        for pair in BUILDING_LINEUP_PARKING_BELT_PAIRS
    ]


def save_belt_pair_preferences_from_form(gateway, form):
    single_pair_key = normalize_belt_pair_key(
        form.get("building_lineup_belt_preference_pair")
    )
    if single_pair_key:
        return _save_single_belt_pair_preference_from_form(
            gateway,
            form,
            single_pair_key,
        )

    if "building_lineup_belt_preferences_present" not in form:
        return active_belt_pair_preference_map(gateway)

    selected = {}
    for pair in BUILDING_LINEUP_PARKING_BELT_PAIRS:
        pair_key = pair["pair_key"]
        field_name = parking_belt_preference_form_field(pair_key)
        ramps = []
        for raw_ramp in form.getlist(field_name):
            ramp = normalize_belt_pair_ramp(raw_ramp)
            if ramp and ramp not in ramps:
                ramps.append(ramp)
        selected[pair_key] = tuple(ramps)

    for pair_key, ramps in selected.items():
        _sync_belt_pair_rules(gateway, pair_key, ramps)
    db.session.flush()
    return selected


def _save_single_belt_pair_preference_from_form(gateway, form, pair_key):
    field_name = parking_belt_preference_form_field(pair_key)
    ramps = []
    for raw_ramp in form.getlist(field_name):
        ramp = normalize_belt_pair_ramp(raw_ramp)
        if ramp and ramp not in ramps:
            ramps.append(ramp)

    _sync_belt_pair_rules(gateway, pair_key, ramps)
    db.session.flush()
    preferences = active_belt_pair_preference_map(gateway)
    preferences[pair_key] = tuple(ramps)
    return preferences


def _sync_belt_pair_rules(gateway, pair_key, selected_ramps):
    normalized_ramps = []
    for ramp in selected_ramps:
        normalized = normalize_belt_pair_ramp(ramp)
        if normalized and normalized not in normalized_ramps:
            normalized_ramps.append(normalized)
    selected_ramps = tuple(normalized_ramps)
    remaining_ramps = list(selected_ramps)
    existing_rules = MotherBrainParkingRule.query.filter_by(
        gateway_id=gateway.id,
        rule_category=BUILDING_LINEUP_BELT_PARKING_PREFERENCE,
        subject_type=BELT_PAIR_SUBJECT_TYPE,
        subject_value=pair_key,
    ).all()

    for rule in existing_rules:
        ramp = normalize_belt_pair_ramp(rule.ramp_code)
        if ramp in remaining_ramps:
            rule.gateway_code = gateway.code
            rule.ramp_code = ramp
            rule.rule_behavior = BELT_PAIR_PREFERENCE_BEHAVIOR
            rule.active = True
            rule.note = ""
            remaining_ramps.remove(ramp)
        else:
            db.session.delete(rule)

    for ramp in remaining_ramps:
        db.session.add(
            MotherBrainParkingRule(
                gateway_id=gateway.id,
                gateway_code=gateway.code,
                rule_category=BUILDING_LINEUP_BELT_PARKING_PREFERENCE,
                subject_type=BELT_PAIR_SUBJECT_TYPE,
                subject_value=pair_key,
                ramp_code=ramp,
                rule_behavior=BELT_PAIR_PREFERENCE_BEHAVIOR,
                active=True,
                note="",
            )
        )


def building_lineup_destination_belt_pair_map(gateway):
    rows = NeoErmacBuildingLineup.query.filter_by(gateway_id=gateway.id).all()
    candidate_pairs_by_destination = {}
    for row in rows:
        pair_key = RUNOUT_KEY_TO_PAIR.get(str(row.runout_key or "").strip())
        if not pair_key:
            continue
        for field_name in DESTINATION_FIELDS:
            destination = normalize_destination(getattr(row, field_name, None))
            if not destination:
                continue
            candidate_pairs_by_destination.setdefault(destination, set()).add(pair_key)

    return {
        destination: next(iter(pair_keys))
        for destination, pair_keys in candidate_pairs_by_destination.items()
        if len(pair_keys) == 1
    }


def building_lineup_destination_conflicts(gateway):
    rows = NeoErmacBuildingLineup.query.filter_by(gateway_id=gateway.id).all()
    candidate_pairs_by_destination = {}
    for row in rows:
        pair_key = RUNOUT_KEY_TO_PAIR.get(str(row.runout_key or "").strip())
        if not pair_key:
            continue
        for field_name in DESTINATION_FIELDS:
            destination = normalize_destination(getattr(row, field_name, None))
            if not destination:
                continue
            candidate_pairs_by_destination.setdefault(destination, set()).add(pair_key)
    return {
        destination: tuple(sorted(pair_keys))
        for destination, pair_keys in candidate_pairs_by_destination.items()
        if len(pair_keys) > 1
    }


def normalize_belt_pair_key(value):
    text = str(value or "").strip().upper().replace("D", "").replace("-", "/")
    text = "/".join(part for part in text.split("/") if part)
    if text in VALID_BELT_PAIR_KEYS:
        return text
    return ""


def normalize_belt_pair_ramp(value):
    text = str(value or "").strip().upper()
    return text if text in BELT_PAIR_RAMP_CODES else ""


def belt_pair_ramp_label(ramp):
    ramp = normalize_belt_pair_ramp(ramp)
    return dict(BELT_PAIR_RAMP_OPTIONS).get(ramp, ramp)
