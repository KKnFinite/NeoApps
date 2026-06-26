PARKING_AIRCRAFT_TYPE_OPTIONS = ("A300", "747", "757", "767")
UNKNOWN_PARKING_AIRCRAFT_TYPE = "UNKNOWN"

_PARKING_AIRCRAFT_TYPE_BY_FIRST_DIGIT = {
    "1": "A300",
    "3": "767",
    "4": "757",
    "5": "747",
    "6": "747",
    "9": "767",
}


def resolve_parking_aircraft_type_from_tail(tail_number):
    """Resolve parking aircraft type from the UPS tail number convention."""
    normalized_tail = str(tail_number or "").strip().upper()
    first_digit = next((character for character in normalized_tail if character.isdigit()), "")
    return _PARKING_AIRCRAFT_TYPE_BY_FIRST_DIGIT.get(
        first_digit,
        UNKNOWN_PARKING_AIRCRAFT_TYPE,
    )


def normalize_parking_aircraft_type(value, allow_unknown=True):
    text = str(value or "").strip().upper().replace("-", "")
    if text == "A300":
        return "A300"
    if text in {"747", "757", "767"}:
        return text
    if allow_unknown and text == UNKNOWN_PARKING_AIRCRAFT_TYPE:
        return UNKNOWN_PARKING_AIRCRAFT_TYPE
    return ""
