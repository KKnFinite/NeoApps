"""User/gateway preferences only: never mutate operational revisions or missions."""
from datetime import datetime
from app.extensions import db
from app.models import NeoScorpionDispatchPreference as Preference


class DispatchPreferenceConflict(ValueError):
    pass


def dispatch_preferences(gateway, user):
    if not getattr(user, 'is_authenticated', False):
        return dict(arrived_first=True, secondary_sort='etd', version=0)
    record = Preference.query.filter_by(user_id=user.id, gateway_id=gateway.id).one_or_none()
    return dict(arrived_first=record.arrived_first, secondary_sort=record.secondary_sort,
                version=record.version) if record else dict(arrived_first=True, secondary_sort='etd', version=0)


def save_dispatch_preferences(gateway, user, form):
    if form.get('arrived_first') not in {'0', '1'} or form.get('secondary_sort') not in {'etd', 'arrival'}:
        raise ValueError('Select valid Dispatch sorting preferences.')
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
        raise DispatchPreferenceConflict('Sorting changed in another window/device. Select again to save your choice.')
    record.arrived_first = form['arrived_first'] == '1'
    record.secondary_sort = form['secondary_sort']
    record.version += 1
    db.session.flush()
    return dict(arrived_first=record.arrived_first, secondary_sort=record.secondary_sort, version=record.version)
