"""Explicit bootstrap-only migration of legacy sort-scoped door preferences."""

import json

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.extensions import db
from app.models import Gateway, NeoErmacDoorPreference, NeoErmacDoorSupervision, SortDateOperation
from app.services.neoermac_building_lineup import OUTBOUND_DOOR_OPTIONS
from app.services.neoermac_door_supervision import _sort_doors
from app.services.neoermac_door_view import normalize_door


def backfill_neoermac_door_preferences(table_names):
    """Insert latest valid user/gateway history only; never replace a saved preference.

    A valid explicit empty selection is preserved. Malformed JSON or selections
    containing no remaining valid doors do not displace older usable history.
    Legacy operations lacking gateway_id resolve through their gateway code.
    The caller owns schema creation and the bootstrap transaction/commit.
    """
    legacy = NeoErmacDoorSupervision.__table__
    operation = SortDateOperation.__table__
    gateway = Gateway.__table__
    preference = NeoErmacDoorPreference.__table__
    if not {legacy.name, operation.name, gateway.name, preference.name}.issubset(table_names):
        return  # Partial legacy databases may not have operational history yet.
    existing = select(preference.c.id).where(
        preference.c.user_id == legacy.c.user_id,
        preference.c.gateway_id == gateway.c.id,
    ).correlate(legacy, gateway).exists()
    rows = db.session.execute(
        select(legacy, gateway.c.id.label("preference_gateway_id"))
        .select_from(legacy.join(operation, legacy.c.sort_date_operation_id == operation.c.id).join(
            gateway, or_(
                operation.c.gateway_id == gateway.c.id,
                and_(operation.c.gateway_id.is_(None), operation.c.gateway_code == gateway.c.code),
            ),
        ))
        .where(~existing)
        .order_by(legacy.c.user_id, gateway.c.id, legacy.c.updated_at.desc(), legacy.c.id.desc())
    ).mappings()
    insert = postgres_insert if db.session.get_bind().dialect.name == "postgresql" else sqlite_insert
    migrated = set()
    for row in rows:
        key = (row.user_id, row.preference_gateway_id)
        if key in migrated:
            continue
        try:
            values = json.loads(row.selected_doors_json)
        except (TypeError, ValueError):
            continue
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            continue
        selected = _sort_doors(values, OUTBOUND_DOOR_OPTIONS)
        if values and not selected:
            continue
        active = normalize_door(row.active_door)
        if active not in selected:
            active = selected[0] if selected else None
        db.session.execute(insert(preference).values(
            user_id=key[0], gateway_id=key[1], selected_doors_json=json.dumps(selected),
            active_door=active, created_at=row.created_at, updated_at=row.updated_at,
        ).on_conflict_do_nothing(index_elements=["user_id", "gateway_id"]))
        migrated.add(key)
