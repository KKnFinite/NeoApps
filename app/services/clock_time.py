"""Strict, display-ready parsing for user-entered local clock times.

This deliberately does not attach a date or timezone; callers retain their
existing operational-date and timezone authority.
"""

import re
from datetime import time


_COMPACT = re.compile(r"^[0-9]{3,4}$")
_COLON = re.compile(r"^[0-9]{2}:[0-9]{2}$")


def normalize_clock_time(value, *, optional=True):
    if isinstance(value, time):
        if value.second or value.microsecond:
            raise ValueError("Enter a valid time (HH:MM).")
        return value.strftime("%H:%M")
    if not isinstance(value, str) and value is not None:
        raise ValueError("Enter a valid time (HH:MM).")
    raw = (value or "").strip()
    if not raw:
        if optional:
            return None
        raise ValueError("Enter a time (HH:MM).")
    if _COMPACT.fullmatch(raw):
        digits = raw.zfill(4)
        raw = digits[:2] + ":" + digits[2:]
    if not _COLON.fullmatch(raw):
        raise ValueError("Enter a valid time (HH:MM).")
    if int(raw[:2]) > 23 or int(raw[3:]) > 59:
        raise ValueError("Enter a valid time (00:00–23:59).")
    return raw


def parse_clock_time(value, *, optional=True):
    normalized = normalize_clock_time(value, optional=optional)
    return time(int(normalized[:2]), int(normalized[3:])) if normalized else None
