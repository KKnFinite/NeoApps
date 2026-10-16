"""User/gateway preferences only: never mutate operational revisions or missions."""
from datetime import datetime
from app.extensions import db
from app.models import NeoScorpionDispatchPreference as Preference


class DispatchPreferenceConflict(ValueError):
    pass


def _values(record=None):
    return dict(arrived_first=record.arrived_first if record else True,
                secondary_sort=record.secondary_sort if record else 'etd',
                truck_cards_collapsed=record.truck_cards_collapsed if record else False,
                spear_data_collapsed=record.spear_data_collapsed if record else False,
                version=record.version if record else 0)


def dispatch_preferences(gateway, user):
    if not getattr(user, 'is_authenticated', False):
        return _values()
    record = Preference.query.filter_by(user_id=user.id, gateway_id=gateway.id).one_or_none()
    return _values(record)


def save_dispatch_preferences(gateway, user, form):
    if (('arrived_first' in form and form['arrived_first'] not in {'0', '1'})
            or ('secondary_sort' in form and form['secondary_sort'] not in {'etd', 'arrival'})):
        raise ValueError('Select valid Dispatch sorting preferences.')
    for name in ('truck_cards_collapsed', 'spear_data_collapsed'):
        if name in form and form[name] not in {'0', '1'}:
            raise ValueError('Select valid Dispatch section preferences.')
    if not any(name in form for name in ('arrived_first', 'secondary_sort', 'truck_cards_collapsed', 'spear_data_collapsed')):
        raise ValueError('Select a Dispatch preference to save.')
    try:
        version = int(form.get('expected_version', ''))
    except (TypeError, ValueError):
        raise ValueError('Refresh Dispatch before saving preferences.') from None
    if not 0 <= version <= 2147483647:
        raise ValueError('Invalid preference version.')
    # A unique user/gateway key also protects simultaneous first saves.
    if db.session.get_bind().dialect.name == 'postgresql':
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    db.session.execute(insert(Preference).values(user_id=user.id, gateway_id=gateway.id,
        arrived_first=True, secondary_sort='etd', version=0, updated_at=datetime.utcnow())
        .on_conflict_do_nothing(index_elements=['user_id', 'gateway_id']))
    record = Preference.query.filter_by(user_id=user.id, gateway_id=gateway.id).with_for_update().populate_existing().one()
    if record.version != version:
        raise DispatchPreferenceConflict('Dispatch preferences changed in another window/device. Select again to save your choice.')
    if 'arrived_first' in form:
        record.arrived_first = form['arrived_first'] == '1'
    if 'secondary_sort' in form:
        record.secondary_sort = form['secondary_sort']
    # Older clients may still save sorting alone; retain their section settings.
    for name in ('truck_cards_collapsed', 'spear_data_collapsed'):
        if name in form:
            setattr(record, name, form[name] == '1')
    record.version += 1
    db.session.flush()
    return _values(record)
