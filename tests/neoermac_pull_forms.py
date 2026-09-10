"""Construct the same signed pull originals that a freshly rendered form carries."""
from app.services.neoermac_door_view import (
    _destination_cards_for_door, current_door_view_operation, door_view_operational_state,
)


def pull_cards(gateway, door):
    operation = current_door_view_operation(gateway)
    bundle = door_view_operational_state(gateway, operation=operation, initialize_lineup=False)
    return _destination_cards_for_door(gateway, door, operation, bundle=bundle)


def pull_form(gateway, values):
    """Existing workflow fixtures explicitly start from a fresh displayed form."""
    values = dict(values)
    cards = {card['destination']: card for card in pull_cards(gateway, values.get('door', ''))}
    if values.get('action') == 'save_pulls':
        for index in range(int(values.get('destination_count', 0))):
            card = cards.get(values.get(f'destination_{index}'))
            if card:
                values[f'operation_id_{index}'] = card['operation_id']
                values[f'mission_id_{index}'] = card['mission_id']
                for key in ('pure', 'mix'):
                    values[f'original_{key}_{index}'] = card['original'][key]
    else:
        card = cards.get(values.get('destination'))
        if card:
            values.update(operation_id=card['operation_id'], mission_id=card['mission_id'],
                          original=card['original'].get(values.get('pull_key'), ''))
    return values
