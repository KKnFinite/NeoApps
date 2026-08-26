"""Request-local read-only operation resolution shared by live endpoints."""

from app.extensions import db
from app.models import SortDateOperation
from app.services.operation_lifecycle import (
    current_existing_operational_sort_operations,
)
from app.services.request_cache import request_cached


OPERATION_BY_ID_NAMESPACE = "operation.by_id"


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


def current_operational_sort_operation(gateway, now=None):
    """Return an existing operation only inside its active lifecycle window."""
    operations = current_existing_operational_sort_operations(gateway, now=now)
    return operations[0] if operations else None
