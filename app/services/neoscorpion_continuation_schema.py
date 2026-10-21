"""Release the legacy single continuation slot without changing any records."""
from sqlalchemy import inspect, text


def migrate_continuation_queue(connection):
    table='neoscorpion_pending_uplifts'
    inspector=inspect(connection)
    if table not in inspector.get_table_names():return
    indexes={index['name']:index for index in inspector.get_indexes(table)}
    old=indexes.get('uq_scorpion_pending_uplift_outstanding')
    if old:
        if not old['unique'] or old['column_names']!=['mission_id','parent_cycle_number']:
            raise RuntimeError('Unexpected legacy uplift index; manual schema review required.')
        connection.execute(text('DROP INDEX uq_scorpion_pending_uplift_outstanding'))
    from app.models import NeoScorpionPendingUplift
    for index in NeoScorpionPendingUplift.__table__.indexes:
        if index.name=='uq_scorpion_pending_legacy_uplift':index.create(connection,checkfirst=True)
