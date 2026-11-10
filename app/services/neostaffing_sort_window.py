"""Strict RFD Night Sort-window gate shared by NeoStaffing Google publishers.

The current/scheduled operation selector alone does NOT prove the sort is underway.
Fail closed when configured start/end times are missing; never use the legacy
all-day fallback for an external staffing publication.
"""
from app.services import neostaffing as staffing
from app.services import gateway_matrix


def active_night_operation(*, local_now=None):
    gateway = staffing._attendance_gateway()
    if gateway is None or str(gateway.code).upper() != 'RFD':
        return None
    operation = staffing.current_night_attendance_operation(gateway)
    if (operation is None or str(operation.gateway_code).upper() != 'RFD'
            or staffing._normalize_staffing_sort_name(operation.sort_name) != 'night'
            or operation.archived_at_utc is not None):
        return None
    setting = gateway_matrix._sort_timeline_sort_setting(gateway, operation.sort_name)
    if (setting is None or setting.sort_window_start_local is None
            or setting.sort_window_end_local is None):
        return None
    local_now = (local_now if local_now is not None
                 else gateway_matrix.current_gateway_local_datetime(gateway))
    return operation if gateway_matrix.operation_is_active_at(operation, local_now, gateway) else None
