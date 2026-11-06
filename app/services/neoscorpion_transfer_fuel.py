"""One input contract for entered T/F, independent of truck ownership/capacity."""

MAX_TRANSFER_FUEL_GALLONS = 17_000


class TransferFuelValidationError(ValueError):
    pass


def parse_transfer_fuel_gallons(value):
    """Accept missing/blank or ASCII whole gallons; never round or clamp input."""
    if value is None:
        return None
    if type(value) is int:
        if 0 <= value <= MAX_TRANSFER_FUEL_GALLONS:
            return value
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        if raw.isascii() and raw.isdigit():
            # Compare before int(): even an arbitrarily long submitted number
            # must be a controlled validation error, not a storage/parse error.
            digits = raw.lstrip('0') or '0'
            maximum = str(MAX_TRANSFER_FUEL_GALLONS)
            if len(digits) < len(maximum) or (len(digits) == len(maximum) and digits <= maximum):
                return int(digits)
    raise TransferFuelValidationError('T/F must be blank or a whole number from 0 to 17,000 gallons.')
