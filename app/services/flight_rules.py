WIDEBODY_AIRCRAFT_TYPES = {"A300", "767", "747"}
NARROWBODY_AIRCRAFT_TYPES = {"757", "727"}

WIDEBODY_CREW_SECTIONS = ("topside", "front_p", "rear_p", "ab")
NARROWBODY_CREW_SECTIONS = ("topside", "belly_31", "belly_34")
UNKNOWN_CREW_SECTIONS = ("topside", "other")


def derive_aircraft_type_from_tail_number(tail_number):
    if not tail_number:
        return "unknown"

    normalized_tail = str(tail_number).strip().upper()
    if not (normalized_tail.startswith("N") and normalized_tail.endswith("UP")):
        return "unknown"

    first_digit = next((char for char in normalized_tail if char.isdigit()), None)

    if first_digit == "1":
        return "A300"
    if first_digit in {"3", "9"}:
        return "767"
    if first_digit in {"4", "5"}:
        return "757"
    if first_digit == "6":
        return "747"

    return "unknown"


def resolve_aircraft_type_for_tail_state(tail_state):
    source = getattr(tail_state, "aircraft_type_source", None)
    aircraft_type = getattr(tail_state, "aircraft_type", None)

    if source == "manual":
        return aircraft_type or "unknown"

    if source == "api" and aircraft_type:
        return aircraft_type

    return derive_aircraft_type_from_tail_number(getattr(tail_state, "tail_number", None))


def default_required_crew_sections(aircraft_type):
    normalized_type = str(aircraft_type or "").upper()

    if normalized_type in WIDEBODY_AIRCRAFT_TYPES:
        return WIDEBODY_CREW_SECTIONS

    if normalized_type in NARROWBODY_AIRCRAFT_TYPES:
        return NARROWBODY_CREW_SECTIONS

    return UNKNOWN_CREW_SECTIONS


def crew_sections_for_tail_swap(current_sections, old_aircraft_type, new_aircraft_type):
    current_sections = tuple(current_sections or ())
    old_group = _aircraft_group(old_aircraft_type)
    new_group = _aircraft_group(new_aircraft_type)

    if old_group == new_group and old_group in {"widebody", "narrowbody"}:
        keep = current_sections
    else:
        keep = tuple(section for section in current_sections if section == "topside")

    rebuild = tuple(section for section in current_sections if section not in keep)
    return {"keep": keep, "rebuild": rebuild}


def is_deice_complete(tail_state):
    return bool(
        getattr(tail_state, "pretreat_status", False)
        or getattr(tail_state, "deice_status", None) == "cleared"
    )


def is_mission_crew_covered(assignments):
    for assignment in assignments:
        if not getattr(assignment, "required", True):
            continue

        crew = getattr(assignment, "crew", None)
        if not crew or not getattr(crew, "active", False):
            return False

    return True


def match_api_flight_number(api_flight_number, stored_flight_numbers):
    api_number = _clean_flight_number(api_flight_number)
    stored_numbers = [_clean_flight_number(number) for number in stored_flight_numbers]

    for stored_number in stored_numbers:
        if stored_number == api_number:
            return stored_number

    if not api_number or api_number.startswith("0"):
        return None

    for stored_number in stored_numbers:
        if stored_number.startswith("0") and stored_number.lstrip("0") == api_number:
            return stored_number

    return None


def _aircraft_group(aircraft_type):
    normalized_type = str(aircraft_type or "").upper()

    if normalized_type in WIDEBODY_AIRCRAFT_TYPES:
        return "widebody"

    if normalized_type in NARROWBODY_AIRCRAFT_TYPES:
        return "narrowbody"

    return "unknown"


def _clean_flight_number(flight_number):
    if flight_number is None:
        return ""

    return str(flight_number).strip()
