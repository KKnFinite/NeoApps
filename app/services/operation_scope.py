"""Request-local read-only operation resolution shared by live endpoints."""

from sqlalchemy import or_

from app.extensions import db
from app.models import SortDateOperation
from app.services.request_cache import request_cached


OPERATION_BY_ID_NAMESPACE = "operation.by_id"
CURRENT_OPERATION_NAMESPACE = "operation.current_latest"


def operation_by_id(operation_id):
    try:
        normalized_id = int(operation_id)
    except (TypeError, ValueError):
        return None
    return request_cached(
        OPERATION_BY_ID_NAMESPACE,
        normalized_id,
        lambda: db.session.get(SortDateOperation, normalized_id),
    )


def current_unarchived_operation(gateway):
    cache_key = (gateway.id, gateway.code)
    return request_cached(
        CURRENT_OPERATION_NAMESPACE,
        cache_key,
        lambda: (
            SortDateOperation.query.filter(
                SortDateOperation.archived_at_utc.is_(None),
                or_(
                    SortDateOperation.gateway_id == gateway.id,
                    SortDateOperation.gateway_code == gateway.code,
                ),
            )
            .order_by(
                SortDateOperation.sort_date.desc(),
                SortDateOperation.generated_at_utc.desc(),
                SortDateOperation.id.desc(),
            )
            .first()
        ),
    )
