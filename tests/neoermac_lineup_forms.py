"""Explicitly model the signed originals on a freshly displayed Lineup form."""
from app.services.neoermac_building_lineup import (
    DESTINATION_FIELDS, get_building_lineup_rows, lineup_field_name, lineup_original, slot_flight_value,
)


def lineup_form(gateway, values):
    values = dict(values)
    originals = {
        lineup_field_name(row, field): lineup_original(gateway, lineup_field_name(row, field), slot_flight_value(row, field))
        for row in get_building_lineup_rows(gateway, initialize=False) for field in DESTINATION_FIELDS
    }
    if 'field' in values:
        values['original'] = originals.get(values['field'], '')
    else:
        for field in list(values):
            if field in originals:
                values['original_' + field] = originals[field]
    return values
