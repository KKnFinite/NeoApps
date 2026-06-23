import json
import os
import re
import uuid
from datetime import date, datetime, time, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import or_

from app.extensions import db
from app.models import (
    FlightApiReviewItem,
    SortDateMission,
    SortDateOperation,
    SortTimelineSettings,
)
from app.services.gateway_matrix import current_operations_for_gateway, gateway_timezone
from app.services.sort_date_operations import (
    create_default_crew_assignments_for_mission,
    ensure_tail_state_for_mission,
)
from app.services.sort_timeline import (
    api_schedule_for_gateway,
    ensure_sort_timeline_settings,
    record_sort_timeline_api_attempt,
    sort_settings_by_name,
    sort_timeline_context,
)


AIRPORT_CODE = "RFD"
DEFAULT_API_KEY_ENV_VAR = "AERODATABOX_API_KEY"
AUTO_POLL_LOCK_STALE_AFTER_MINUTES = 30
RAPIDAPI_HOST = "aerodatabox.p.rapidapi.com"
RAPIDAPI_USER_AGENT = "NeoGateway/1.0"
RAPIDAPI_ACCEPT = "application/json"
RAPIDAPI_QUERY_PARAMS = (
    ("withLeg", "true"),
    ("direction", "Both"),
    ("withCancelled", "true"),
    ("withCodeshared", "true"),
    ("withCargo", "true"),
    ("withPrivate", "true"),
    ("withLocation", "false"),
)
API_STATUS_SCHEDULED = "Scheduled"
API_STATUS_IN_AIR = "In Air"
API_STATUS_ON_GROUND = "On Ground"
API_STATUS_ASSUMED_ARRIVED = "Assumed Arrived"
DEPARTURE_TIME_MATCH_TOLERANCE_MINUTES = 90


class FlightApiDisabledError(RuntimeError):
    pass


class FlightApiConfigurationError(RuntimeError):
    pass


class FlightApiProviderError(FlightApiConfigurationError):
    def __init__(self, message, diagnostics=None):
        super().__init__(message)
        self.diagnostics = diagnostics or {}


class RapidApiFlightClient:
    def fetch_fids(self, gateway_code, start_local, end_local, api_key):
        request, diagnostics = build_rapidapi_request(
            gateway_code,
            start_local,
            end_local,
            api_key,
        )
        try:
            with urlopen(request, timeout=20) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            diagnostics["provider_status_code"] = error.code
            diagnostics["provider_response_snippet"] = _safe_provider_response_snippet(
                error,
                api_key,
            )
            message = f"Provider returned {error.code} {error.reason or 'error'}."
            if error.code == 403:
                message += (
                    " RapidAPI playground may work while the app request is rejected "
                    "if host/header/path/query differ or Render is using stale env/deploy."
                )
            raise FlightApiProviderError(
                message,
                diagnostics=diagnostics,
            ) from error
        except (URLError, TimeoutError) as error:
            raise FlightApiProviderError(
                f"Provider request failed: {error}",
                diagnostics=diagnostics,
            ) from error
        except json.JSONDecodeError as error:
            raise FlightApiProviderError(
                "Provider returned invalid JSON.",
                diagnostics=diagnostics,
            ) from error


def build_rapidapi_request(gateway_code, start_local, end_local, api_key):
    details = rapidapi_request_details(gateway_code, start_local, end_local)
    normalized_key, key_diagnostics = normalize_api_key(api_key)
    request = Request(
        details["url"],
        headers={
            "User-Agent": RAPIDAPI_USER_AGENT,
            "Accept": RAPIDAPI_ACCEPT,
            "X-RapidAPI-Key": normalized_key,
            "X-RapidAPI-Host": RAPIDAPI_HOST,
        },
    )
    diagnostics = {
        "provider_status_code": None,
        "request_host": details["host"],
        "request_path_query": details["path_query"],
        "user_agent_sent": True,
        "accept_header_sent": True,
        "provider_response_snippet": None,
        **key_diagnostics,
    }
    return request, diagnostics


def normalize_api_key(api_key):
    if api_key is None:
        raw_value = ""
    else:
        raw_value = str(api_key)
    normalized_value = raw_value.strip()
    appears_quoted = (
        len(normalized_value) >= 2
        and normalized_value[0] == normalized_value[-1]
        and normalized_value[0] in ("'", '"')
    )
    return normalized_value, {
        "api_key_present": bool(normalized_value),
        "api_key_normalized": raw_value != normalized_value,
        "api_key_appears_quoted": appears_quoted,
    }


def rapidapi_request_details(gateway_code, start_local, end_local):
    start_value = _format_provider_datetime(start_local)
    end_value = _format_provider_datetime(end_local)
    path = (
        f"/flights/airports/iata/"
        f"{str(gateway_code or AIRPORT_CODE).upper()}/{start_value}/{end_value}"
    )
    query = urlencode(RAPIDAPI_QUERY_PARAMS)
    return {
        "host": RAPIDAPI_HOST,
        "path": path,
        "query": query,
        "path_query": f"{path}?{query}",
        "url": f"https://{RAPIDAPI_HOST}{path}?{query}",
    }


def run_flight_api_import(gateway, operation=None, client=None, now=None):
    settings = ensure_sort_timeline_settings(gateway)
    if not settings.provider_enabled:
        return _empty_result(
            gateway,
            operation,
            provider_enabled=False,
            message="Provider disabled. No polling or imports were attempted.",
        )

    operation = operation or current_sort_operation(gateway)
    if not operation:
        return _empty_result(
            gateway,
            operation,
            provider_enabled=True,
            message="No current sort operation is available.",
        )

    lookup_window = sort_flight_lookup_window_snapshot(operation, settings)
    polling_window = api_polling_window_snapshot(operation, settings)
    ops_window = ops_node_online_window_snapshot(operation, settings)
    api_key_env_var = DEFAULT_API_KEY_ENV_VAR
    raw_api_key = os.environ.get(api_key_env_var)
    api_key, key_diagnostics = normalize_api_key(raw_api_key)
    request_diagnostics = _safe_request_diagnostics(
        gateway.code,
        lookup_window["lookup_window_start_local"],
        lookup_window["lookup_window_end_local"],
        raw_api_key,
    )
    if not api_key and client is None:
        result = _empty_result(
            gateway,
            operation,
            provider_enabled=True,
            message=f"API key env var {api_key_env_var} is not set.",
        )
        result.update(
            {
                "provider_error": True,
                "api_key_env_var": api_key_env_var,
                **request_diagnostics,
                **key_diagnostics,
                **lookup_window,
                **polling_window,
                **ops_window,
                **_poll_time_diagnostics(gateway, _utc_naive(now)),
            }
        )
        return result

    attempted_at = _utc_naive(now)
    usage_units_consumed = int(settings.units_per_poll or 2)
    usage_counter = record_sort_timeline_api_attempt(
        gateway,
        attempted_at,
        units_consumed=usage_units_consumed,
    )
    try:
        payload = fetch_live_provider_payload(
            gateway,
            lookup_window,
            api_key,
            client=client,
        )
    except FlightApiConfigurationError as error:
        request_diagnostics = {
            **request_diagnostics,
            **(getattr(error, "diagnostics", {}) or {}),
        }
        result = _empty_result(
            gateway,
            operation,
            provider_enabled=True,
            message=f"Flight API provider error: {error}",
        )
        result.update(
            {
                "attempted": True,
                "provider_error": True,
                "api_key_env_var": api_key_env_var,
                "usage_units_consumed": usage_units_consumed,
                "usage_polls_used": usage_counter.attempted_call_count,
                "provider_status_code": request_diagnostics.get("provider_status_code"),
                "request_host": request_diagnostics.get("request_host"),
                "request_path_query": request_diagnostics.get("request_path_query"),
                "user_agent_sent": request_diagnostics.get("user_agent_sent"),
                "accept_header_sent": request_diagnostics.get("accept_header_sent"),
                "api_key_present": request_diagnostics.get(
                    "api_key_present",
                    key_diagnostics["api_key_present"],
                ),
                "api_key_normalized": bool(
                    key_diagnostics["api_key_normalized"]
                    or request_diagnostics.get("api_key_normalized")
                ),
                "api_key_appears_quoted": bool(
                    key_diagnostics["api_key_appears_quoted"]
                    or request_diagnostics.get("api_key_appears_quoted")
                ),
                "provider_response_snippet": request_diagnostics.get("provider_response_snippet"),
                **lookup_window,
                **polling_window,
                **ops_window,
                **_poll_time_diagnostics(gateway, attempted_at),
            }
        )
        record_flight_api_poll_state(
            gateway,
            operation,
            attempted_at,
            success=False,
            summary=result["message"],
            settings=settings,
        )
        db.session.flush()
        return result

    result = process_provider_payload(
        payload,
        gateway,
        operation,
        settings=settings,
        apply=True,
        source="live",
        now=now,
    )
    result.update(
        {
            "provider_enabled": True,
            "attempted": True,
            "api_key_env_var": api_key_env_var,
            "usage_units_consumed": usage_units_consumed,
            "usage_polls_used": usage_counter.attempted_call_count,
            **request_diagnostics,
            **lookup_window,
            **polling_window,
            **ops_window,
            **_poll_time_diagnostics(gateway, attempted_at),
        }
    )
    record_flight_api_poll_state(
        gateway,
        operation,
        attempted_at,
        success=True,
        summary="Flight API provider poll completed.",
        settings=settings,
    )
    db.session.flush()
    return result


def fetch_live_provider_payload(gateway, lookup_window, api_key, client=None):
    return (client or RapidApiFlightClient()).fetch_fids(
        gateway.code,
        lookup_window["lookup_window_start_local"],
        lookup_window["lookup_window_end_local"],
        api_key,
    )


def run_flight_api_replay(gateway, operation=None, payload_text="", now=None):
    settings = ensure_sort_timeline_settings(gateway)
    operation = operation or current_sort_operation(gateway)
    if not operation:
        return _replay_result(
            _empty_result(
                gateway,
                operation,
                provider_enabled=bool(settings.provider_enabled),
                message="No current sort operation is available.",
            ),
            gateway,
            operation,
            settings,
            now,
            provider_error=True,
        )

    try:
        payload = json.loads(payload_text or "")
    except json.JSONDecodeError as error:
        return _replay_result(
            _empty_result(
                gateway,
                operation,
                provider_enabled=bool(settings.provider_enabled),
                message=f"Replay JSON parse error: {error.msg} at line {error.lineno}, column {error.colno}.",
            ),
            gateway,
            operation,
            settings,
            now,
            provider_error=True,
        )

    result = process_provider_payload(
        payload,
        gateway,
        operation,
        settings=settings,
        apply=False,
        source="replay",
        now=now,
    )
    result["message"] = (
        "Replay mode preview completed. No external request was made and no data was changed."
    )
    return _replay_result(result, gateway, operation, settings, now)


def process_provider_payload(
    payload,
    gateway,
    operation,
    settings=None,
    apply=False,
    source="replay",
    now=None,
):
    api_flights = extract_api_flights(payload, gateway)
    return process_api_flights_for_operation(
        gateway,
        operation,
        api_flights,
        settings=settings,
        apply=apply,
        source=source,
        now=now,
    )


def import_api_flights_for_operation(gateway, operation, api_flights, settings=None, now=None):
    return process_api_flights_for_operation(
        gateway,
        operation,
        api_flights,
        settings=settings,
        apply=True,
        source="live",
        now=now,
    )


def process_api_flights_for_operation(
    gateway,
    operation,
    api_flights,
    settings=None,
    apply=False,
    source="replay",
    now=None,
):
    settings = settings or ensure_sort_timeline_settings(gateway)
    now_utc = _utc_naive(now)
    missions = SortDateMission.query.filter_by(sort_date_operation_id=operation.id).all()
    matched = []
    review_items = []
    ignored_count = 0
    suppressed_review_count = 0
    non_ups_ignored = 0
    diagnostics = _empty_import_count_diagnostics()
    replaced_review_count = replace_active_review_queue_for_operation(operation) if apply else 0
    ups_departure_candidates = []

    for api_flight in api_flights:
        mission_type = _mission_type(api_flight)
        _increment_count(diagnostics, "raw", mission_type)
        normalized = normalize_api_flight(api_flight, operation, gateway)
        _track_provider_departure_time(diagnostics, normalized)
        if not is_ups_flight(api_flight):
            _increment_count(diagnostics, "non_ups_ignored", mission_type)
            non_ups_ignored += 1
            continue

        _increment_count(diagnostics, "ups", mission_type)
        normalized_type = normalized.get("mission_type") or mission_type
        if normalized_type == "departure":
            ups_departure_candidates.append(normalized)
        mission, match_detail = match_api_flight_to_mission_with_reason(
            normalized,
            missions,
        )
        if mission:
            if match_detail:
                normalized["match_diagnostic"] = match_detail
            if normalized_type == "departure":
                normalized["matched_mission_id"] = mission.id
                normalized["matched_mission_flight_number"] = mission.flight_number
                normalized["tail_update_diagnostic"] = departure_tail_update_diagnostic(
                    mission,
                    normalized,
                )
                normalized["departure_time_difference_minutes"] = (
                    departure_time_difference_minutes(mission, normalized)
                )
            if apply:
                apply_api_data_to_mission(mission, normalized, settings, now=now_utc)
            matched.append(
                {
                    "mission": mission,
                    "api_flight": normalized,
                    "match_diagnostic": match_detail,
                    "match_reason": match_detail or "matched",
                    "display_tail": (
                        mission.assigned_tail_number
                        or normalized.get("tail_number")
                    ),
                    "display_model": (
                        mission.api_aircraft_model
                        or normalized.get("aircraft_model")
                    ),
                    "display_status": (
                        mission.api_status
                        or map_api_status(normalized, settings, now=now_utc)
                    ),
                }
            )
            _increment_count(diagnostics, "matched", normalized_type)
            continue

        normalized["unmatched_reason"] = match_detail
        if normalized_type == "departure":
            audit_mission = departure_audit_candidate_mission(normalized, missions)
            normalized["candidate_mission_id"] = getattr(audit_mission, "id", None)
            normalized["candidate_mission_flight_number"] = getattr(
                audit_mission,
                "flight_number",
                None,
            )
            normalized["departure_time_difference_minutes"] = (
                departure_time_difference_minutes(audit_mission, normalized)
                if audit_mission
                else None
            )
        if apply:
            review_item, was_ignored = upsert_review_item(gateway, operation, normalized)
        else:
            review_item, was_ignored = preview_review_item_for_normalized(
                gateway,
                operation,
                normalized,
            )
        if was_ignored:
            suppressed_review_count += 1
            if review_item and _record_value(review_item, "review_status") == "ignored":
                ignored_count += 1
        elif review_item:
            review_items.append(review_item)
            _increment_count(diagnostics, "unmatched", normalized_type)

    db.session.flush()
    departure_match_audit = build_departure_match_audit(
        missions,
        ups_departure_candidates,
        diagnostics,
    )
    return {
        "provider_enabled": bool(settings.provider_enabled),
        "attempted": False,
        "operation": operation,
        "matched": matched,
        "review_items": review_items,
        "ignored_count": ignored_count,
        "suppressed_review_count": suppressed_review_count,
        "non_ups_ignored": non_ups_ignored,
        "review_queue_replaced": bool(apply),
        "replaced_review_count": replaced_review_count,
        "source": source,
        "replay_preview": source == "replay",
        "api_ups_departures": [
            api_ups_departure_audit_row(normalized)
            for normalized in ups_departure_candidates
        ],
        "departure_match_audit": departure_match_audit,
        **diagnostics,
    }


def accept_review_item(review_item, settings=None, now=None):
    if review_item.review_status == "accepted" and review_item.accepted_mission:
        return review_item.accepted_mission
    if review_item.review_status == "ignored":
        review_item.review_status = "pending"

    operation = db.session.get(SortDateOperation, review_item.sort_date_operation_id)
    if not operation:
        raise ValueError("Sort operation not found for review item.")

    settings = settings or SortTimelineSettings.query.filter_by(
        gateway_id=review_item.gateway_id,
    ).first()
    payload = json.loads(review_item.raw_payload or "{}")
    normalized = normalize_api_flight(payload, operation, operation.gateway or None)
    _fill_normalized_from_review_item(normalized, review_item)
    mission = build_api_added_mission(operation, normalized)
    db.session.add(mission)
    db.session.flush()
    if settings:
        apply_api_data_to_mission(mission, normalized, settings, now=now)
    tail_state = ensure_tail_state_for_mission(mission)
    aircraft_type = (
        getattr(tail_state, "aircraft_type", None)
        or normalized.get("aircraft_model")
        or "unknown"
    )
    create_default_crew_assignments_for_mission(mission, aircraft_type)
    review_item.review_status = "accepted"
    review_item.accepted_mission_id = mission.id
    db.session.flush()
    return mission


def _fill_normalized_from_review_item(normalized, review_item):
    fallback_values = {
        "mission_type": review_item.mission_type,
        "review_key": review_item.review_key,
        "flight_number": review_item.flight_number,
        "call_sign": review_item.call_sign,
        "origin": review_item.origin,
        "destination": review_item.destination,
        "revised_time_utc": review_item.revised_time_utc,
        "scheduled_time_utc": review_item.revised_time_utc,
        "runway_time_utc": review_item.runway_time_utc,
        "tail_number": review_item.tail_number,
        "aircraft_model": review_item.aircraft_model,
        "api_status_raw": review_item.api_status,
    }
    for key, value in fallback_values.items():
        if not normalized.get(key) and value is not None:
            normalized[key] = value


def ignore_review_item(review_item):
    review_item.review_status = "ignored"
    db.session.flush()
    return review_item


def current_sort_operation(gateway, sort_date=None, sort_name=None):
    if sort_date is None:
        operations = current_operations_for_gateway(gateway)
        if sort_name:
            sort_name = str(sort_name).strip().lower()
            operations = [
                operation
                for operation in operations
                if operation.sort_name == sort_name
            ]
        return operations[0] if operations else None

    query = SortDateOperation.query.filter_by(
        gateway_code=gateway.code,
        sort_date=sort_date,
    ).filter(SortDateOperation.archived_at_utc.is_(None))
    if sort_name:
        query = query.filter_by(sort_name=str(sort_name).strip().lower())
    return (
        query.order_by(SortDateOperation.generated_at_utc.desc(), SortDateOperation.id.desc())
        .first()
    )


def flight_api_auto_poll_status(gateway, operation=None, now=None):
    settings = ensure_sort_timeline_settings(gateway)
    now_utc = _utc_naive(now)
    timezone_name = gateway_timezone(gateway)
    local_now = _utc_to_local_naive(now_utc, timezone_name)
    operation = operation or current_sort_operation(gateway)
    status = _base_auto_poll_status(gateway, operation, settings, now_utc, local_now)

    if not settings.provider_enabled:
        return _auto_poll_not_eligible(status, "provider disabled")
    if not operation:
        return _auto_poll_not_eligible(status, "no current sort operation")
    if not _operation_is_active_for_local_time(operation, settings, local_now):
        return _auto_poll_not_eligible(status, "operation is not current active operation")
    api_schedule_enabled = _api_enabled_for_operation_day(gateway, operation)
    status["api_schedule_enabled"] = api_schedule_enabled
    if not api_schedule_enabled:
        return _auto_poll_not_eligible(status, "API polling disabled for this sort/day")

    polling_start_local, polling_end_local = api_polling_window_for_operation(operation, settings)
    polling_start_utc = _local_datetime_to_utc_naive(polling_start_local, timezone_name)
    polling_end_utc = _local_datetime_to_utc_naive(polling_end_local, timezone_name)
    status["polling_window_start_utc"] = polling_start_utc
    status["polling_window_end_utc"] = polling_end_utc
    status["polling_window_start_local"] = polling_start_local
    status["polling_window_end_local"] = polling_end_local

    budget_summary = _auto_poll_budget_summary(gateway, operation, settings, now_utc)
    status.update(budget_summary)

    if int(status["polls_remaining"] or 0) <= 0 or int(status["units_remaining"] or 0) <= 0:
        return _auto_poll_not_eligible(status, "monthly API budget exhausted")

    actual_interval = status["actual_interval_minutes"]
    if not actual_interval:
        return _auto_poll_not_eligible(status, "auto poll interval unavailable")

    next_eligible_utc = _next_auto_poll_eligible_utc(
        operation,
        now_utc,
        polling_start_utc,
        actual_interval,
    )
    status["next_eligible_time_utc"] = next_eligible_utc
    status["next_eligible_time_local"] = _utc_to_local_naive(next_eligible_utc, timezone_name)

    if local_now < polling_start_local:
        return _auto_poll_not_eligible(status, "before API Polling Window")
    if local_now > polling_end_local:
        return _auto_poll_not_eligible(status, "outside API Polling Window")
    if now_utc < next_eligible_utc:
        return _auto_poll_not_eligible(status, "waiting for auto poll interval")

    status["eligible"] = True
    status["reason"] = "eligible"
    return status


def record_flight_api_poll_state(
    gateway,
    operation,
    attempted_at_utc,
    success,
    summary="",
    settings=None,
):
    if not operation:
        return None
    settings = settings or ensure_sort_timeline_settings(gateway)
    attempted_at_utc = _utc_naive(attempted_at_utc)
    interval_minutes = (
        _actual_auto_poll_interval_minutes_for_operation(
            gateway,
            operation,
            settings,
            attempted_at_utc,
        )
        or int(getattr(settings, "minimum_auto_poll_interval_minutes", 10) or 10)
    )
    operation.flight_api_last_attempted_poll_at_utc = attempted_at_utc
    operation.flight_api_next_auto_poll_eligible_at_utc = attempted_at_utc + timedelta(
        minutes=interval_minutes,
    )
    operation.flight_api_last_poll_status = "success" if success else "failed"
    operation.flight_api_last_poll_summary = str(summary or "")[:255]
    if success:
        operation.flight_api_last_successful_poll_at_utc = attempted_at_utc
    else:
        operation.flight_api_last_failed_poll_at_utc = attempted_at_utc
    db.session.flush()
    return operation


def acquire_flight_api_auto_poll_lock(operation, now=None):
    if not operation:
        return None
    now_utc = _utc_naive(now)
    lock_token = uuid.uuid4().hex
    stale_before = now_utc - timedelta(minutes=AUTO_POLL_LOCK_STALE_AFTER_MINUTES)
    updated_count = (
        SortDateOperation.query.filter(
            SortDateOperation.id == operation.id,
            or_(
                SortDateOperation.flight_api_auto_poll_in_progress_at_utc.is_(None),
                SortDateOperation.flight_api_auto_poll_in_progress_at_utc < stale_before,
            ),
        )
        .update(
            {
                SortDateOperation.flight_api_auto_poll_in_progress_at_utc: now_utc,
                SortDateOperation.flight_api_auto_poll_lock_token: lock_token,
            },
            synchronize_session=False,
        )
    )
    db.session.flush()
    if not updated_count:
        return None

    operation.flight_api_auto_poll_in_progress_at_utc = now_utc
    operation.flight_api_auto_poll_lock_token = lock_token
    return lock_token


def release_flight_api_auto_poll_lock(operation, lock_token=None):
    if not operation:
        return False
    query = SortDateOperation.query.filter(SortDateOperation.id == operation.id)
    if lock_token:
        query = query.filter(SortDateOperation.flight_api_auto_poll_lock_token == lock_token)
    updated_count = query.update(
        {
            SortDateOperation.flight_api_auto_poll_in_progress_at_utc: None,
            SortDateOperation.flight_api_auto_poll_lock_token: "",
        },
        synchronize_session=False,
    )
    db.session.flush()
    if updated_count:
        operation.flight_api_auto_poll_in_progress_at_utc = None
        operation.flight_api_auto_poll_lock_token = ""
    return bool(updated_count)


def _base_auto_poll_status(gateway, operation, settings, now_utc, local_now):
    timezone_name = gateway_timezone(gateway)
    status = {
        "eligible": False,
        "reason": "",
        "provider_enabled": bool(settings.provider_enabled),
        "api_schedule_enabled": False,
        "now_utc": now_utc,
        "now_local": local_now,
        "timezone": timezone_name,
        "operation": operation,
        "operation_id": operation.id if operation else None,
        "operation_sort_name": operation.sort_name if operation else None,
        "operation_sort_date": operation.sort_date if operation else None,
        "last_attempted_poll_utc": None,
        "last_attempted_poll_local": None,
        "last_successful_poll_utc": None,
        "last_successful_poll_local": None,
        "last_failed_poll_utc": None,
        "last_failed_poll_local": None,
        "last_poll_status": "",
        "last_poll_summary": "",
        "next_eligible_time_utc": None,
        "next_eligible_time_local": None,
        "actual_interval_minutes": None,
        "remaining_polls": 0,
        "polls_remaining": 0,
        "units_remaining": 0,
    }
    if not operation:
        return status

    for status_key, field_name in (
        ("last_attempted_poll", "flight_api_last_attempted_poll_at_utc"),
        ("last_successful_poll", "flight_api_last_successful_poll_at_utc"),
        ("last_failed_poll", "flight_api_last_failed_poll_at_utc"),
    ):
        utc_value = getattr(operation, field_name, None)
        status[f"{status_key}_utc"] = utc_value
        status[f"{status_key}_local"] = (
            _utc_to_local_naive(utc_value, timezone_name)
            if utc_value
            else None
        )
    status["last_poll_status"] = operation.flight_api_last_poll_status or ""
    status["last_poll_summary"] = operation.flight_api_last_poll_summary or ""
    if operation.flight_api_next_auto_poll_eligible_at_utc:
        status["next_eligible_time_utc"] = operation.flight_api_next_auto_poll_eligible_at_utc
        status["next_eligible_time_local"] = _utc_to_local_naive(
            operation.flight_api_next_auto_poll_eligible_at_utc,
            timezone_name,
        )
    return status


def _auto_poll_not_eligible(status, reason):
    status["eligible"] = False
    status["reason"] = reason
    return status


def _auto_poll_budget_summary(gateway, operation, settings, now_utc):
    local_now = _utc_to_local_naive(now_utc, gateway_timezone(gateway))
    context = sort_timeline_context(
        gateway,
        local_now.strftime("%Y-%m"),
        now=now_utc.replace(tzinfo=timezone.utc),
    )
    summary = context["summary"]
    sort_preview = context["preview_by_sort"].get(operation.sort_name, {})
    actual_interval = sort_preview.get("actual_auto_poll_interval_minutes")
    if actual_interval is None:
        actual_interval = summary.get("actual_auto_poll_interval_minutes")
    return {
        "actual_interval_minutes": actual_interval,
        "remaining_polls": summary.get("polls_remaining", 0),
        "polls_remaining": summary.get("polls_remaining", 0),
        "units_remaining": summary.get("units_remaining", 0),
        "monthly_poll_limit": summary.get("monthly_poll_limit", 0),
        "adjusted_daily_poll_cap": summary.get("adjusted_daily_poll_cap", 0),
    }


def _actual_auto_poll_interval_minutes_for_operation(gateway, operation, settings, now_utc):
    status = _auto_poll_budget_summary(gateway, operation, settings, now_utc)
    return status.get("actual_interval_minutes")


def _next_auto_poll_eligible_utc(operation, now_utc, polling_start_utc, actual_interval_minutes):
    last_attempt = operation.flight_api_last_attempted_poll_at_utc
    if last_attempt:
        return max(
            polling_start_utc,
            _utc_naive(last_attempt) + timedelta(minutes=int(actual_interval_minutes)),
        )
    if now_utc >= polling_start_utc:
        return now_utc
    return polling_start_utc


def _operation_is_active_for_local_time(operation, settings, local_now):
    start_local, end_local = sort_flight_lookup_window_for_operation(operation, settings)
    return bool(start_local and end_local and start_local <= local_now < end_local)


def _api_enabled_for_operation_day(gateway, operation):
    schedule = api_schedule_for_gateway(gateway)
    operation_day = operation.sort_date.strftime("%A").lower()
    return (operation_day, operation.sort_name) in schedule["enabled_cells"]


def sort_flight_lookup_window_for_operation(operation, settings):
    sort_settings = sort_settings_by_name(settings)
    sort_setting = sort_settings.get(operation.sort_name)
    return _window_for_operation(
        operation,
        sort_setting,
        "sort_window_start_local",
        "sort_window_end_local",
        default_start=time(0, 0),
        default_end=time(23, 59),
    )


def api_polling_window_for_operation(operation, settings):
    sort_settings = sort_settings_by_name(settings)
    sort_setting = sort_settings.get(operation.sort_name)
    return _window_for_operation(
        operation,
        sort_setting,
        "polling_start_local",
        "polling_end_local",
        default_start=time(0, 0),
        default_end=time(23, 59),
    )


def ops_node_online_window_for_operation(operation, settings):
    sort_settings = sort_settings_by_name(settings)
    sort_setting = sort_settings.get(operation.sort_name)
    return _window_for_operation(
        operation,
        sort_setting,
        "ops_window_start_local",
        "ops_window_end_local",
        default_start=None,
        default_end=None,
    )


def api_window_for_operation(operation, settings):
    return api_polling_window_for_operation(operation, settings)


def sort_flight_lookup_window_snapshot(operation, settings):
    start_local, end_local = sort_flight_lookup_window_for_operation(operation, settings)
    return _window_snapshot(operation, start_local, end_local, "lookup_window")


def api_polling_window_snapshot(operation, settings):
    start_local, end_local = api_polling_window_for_operation(operation, settings)
    return _window_snapshot(operation, start_local, end_local, "polling_window")


def ops_node_online_window_snapshot(operation, settings):
    start_local, end_local = ops_node_online_window_for_operation(operation, settings)
    return _window_snapshot(operation, start_local, end_local, "ops_window")


def api_window_snapshot(operation, settings):
    return api_polling_window_snapshot(operation, settings)


def _window_for_operation(
    operation,
    sort_setting,
    start_attr,
    end_attr,
    default_start=None,
    default_end=None,
):
    start_time = getattr(sort_setting, start_attr, None) if sort_setting else None
    end_time = getattr(sort_setting, end_attr, None) if sort_setting else None
    start_time = start_time or default_start
    end_time = end_time or default_end
    if not start_time or not end_time:
        return None, None
    start_local = datetime.combine(operation.sort_date, start_time)
    end_local = datetime.combine(operation.sort_date, end_time)
    if end_local <= start_local:
        end_local += timedelta(days=1)
    return start_local, end_local


def _window_snapshot(operation, start_local, end_local, prefix):
    timezone_name = gateway_timezone(operation.gateway)
    if not start_local or not end_local:
        return {
            f"{prefix}_start_local": None,
            f"{prefix}_end_local": None,
            f"{prefix}_start_utc": None,
            f"{prefix}_end_utc": None,
            f"{prefix}_timezone": timezone_name,
        }
    return {
        f"{prefix}_start_local": start_local,
        f"{prefix}_end_local": end_local,
        f"{prefix}_start_utc": _local_datetime_to_utc_naive(start_local, timezone_name),
        f"{prefix}_end_utc": _local_datetime_to_utc_naive(end_local, timezone_name),
        f"{prefix}_timezone": timezone_name,
    }


def extract_api_flights(payload, gateway=None):
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    flights = []
    for key, mission_type in (("arrivals", "arrival"), ("departures", "departure")):
        for item in payload.get(key) or []:
            if isinstance(item, dict):
                item = dict(item)
                item.setdefault("_mission_type", mission_type)
                flights.append(item)
    for key, mission_type in (("arrival", "arrival"), ("departure", "departure")):
        nested = payload.get(key)
        if isinstance(nested, list):
            for item in nested:
                if isinstance(item, dict):
                    item = dict(item)
                    item.setdefault("_mission_type", mission_type)
                    flights.append(item)
    for key in ("flights", "items"):
        for item in payload.get(key) or []:
            if isinstance(item, dict):
                flights.append(dict(item))
    for key in ("data", "result", "response"):
        nested = payload.get(key)
        if nested is payload:
            continue
        flights.extend(extract_api_flights(nested, gateway))
    return flights


def is_ups_flight(api_flight):
    airline = _airline_info(api_flight)
    call_sign = _api_call_sign(api_flight)
    return (
        str(airline.get("icao") or "").upper() == "UPS"
        or str(airline.get("iata") or "").upper() == "5X"
        or call_sign.startswith("UPS")
    )


def normalize_api_flight(api_flight, operation=None, gateway=None):
    mission_type = _mission_type(api_flight)
    provider_flight_number = _api_declared_flight_number(api_flight)
    call_sign = _api_call_sign(api_flight)
    flight_number = provider_flight_number or call_sign
    departure = _as_dict(api_flight.get("departure"))
    arrival = _as_dict(api_flight.get("arrival"))
    origin = _airport_code(departure.get("airport")) or (AIRPORT_CODE if mission_type == "departure" else "")
    destination = _airport_code(arrival.get("airport")) or (AIRPORT_CODE if mission_type == "arrival" else "")
    revised_time_local = _leg_time(arrival if mission_type == "arrival" else departure, "revisedTime")
    scheduled_time_local = _leg_time(arrival if mission_type == "arrival" else departure, "scheduledTime")
    runway_time_local = _leg_time(arrival if mission_type == "arrival" else departure, "runwayTime")
    timezone_name = gateway_timezone(gateway) if gateway else "America/Chicago"
    revised_time_utc = _parse_provider_datetime(revised_time_local, timezone_name)
    scheduled_time_utc = _parse_provider_datetime(scheduled_time_local, timezone_name)
    runway_time_utc = _parse_provider_datetime(runway_time_local, timezone_name)
    aircraft = _as_dict(api_flight.get("aircraft"))
    status = _clean_text(api_flight.get("status"))

    return {
        "raw": api_flight,
        "mission_type": mission_type,
        "flight_number": flight_number,
        "flight_variants": _flight_number_variants(flight_number, call_sign),
        "call_sign": call_sign,
        "provider_flight_number": provider_flight_number,
        "normalized_cores_tried": _ups_numeric_cores_from_values(
            provider_flight_number,
            call_sign,
        ),
        "origin": origin,
        "destination": destination,
        "revised_time_utc": revised_time_utc,
        "scheduled_time_utc": scheduled_time_utc,
        "runway_time_utc": runway_time_utc,
        "tail_number": _clean_upper(
            aircraft.get("reg")
            or aircraft.get("registration")
            or aircraft.get("regNumber")
            or api_flight.get("reg")
            or api_flight.get("aircraftRegistration")
        ),
        "aircraft_model": _clean_text(
            aircraft.get("model")
            or aircraft.get("type")
            or aircraft.get("icao")
            or aircraft.get("icaoCode")
            or aircraft.get("modelCode")
        ),
        "api_status_raw": status,
        "review_key": _review_key(mission_type, flight_number, call_sign, origin, destination),
    }


def match_api_flight_to_mission(normalized, missions):
    mission, _reason = match_api_flight_to_mission_with_reason(normalized, missions)
    return mission


def match_api_flight_to_mission_with_reason(normalized, missions):
    mission_type = normalized["mission_type"]
    candidates = [mission for mission in missions if mission.mission_type == mission_type]
    if mission_type == "departure":
        return _match_api_departure_to_mission_with_reason(normalized, candidates)

    provider_key = _ups_numeric_core(normalized.get("provider_flight_number"))
    call_sign_key = _ups_numeric_core(normalized.get("call_sign"))
    call_sign_is_ups = _clean_flight_number(normalized.get("call_sign")).startswith("UPS")

    if not provider_key and not call_sign_key:
        return None, "unsupported/blank flight identity"

    if call_sign_is_ups and call_sign_key:
        call_sign_matches = _missions_matching_ups_key(candidates, call_sign_key)
        if len(call_sign_matches) > 1:
            return None, "ambiguous callsign match"
        if len(call_sign_matches) == 1:
            return call_sign_matches[0], "matched by callsign"

    if provider_key:
        provider_matches = _missions_matching_ups_key(candidates, provider_key)
        if len(provider_matches) > 1:
            return None, "ambiguous flight number match"
        if len(provider_matches) == 1:
            if call_sign_key and call_sign_key != provider_key:
                return provider_matches[0], "matched by provider flight fallback"
            return provider_matches[0], "matched by provider flight"

    if not call_sign_is_ups and call_sign_key:
        call_sign_matches = _missions_matching_ups_key(candidates, call_sign_key)
        if len(call_sign_matches) > 1:
            return None, "ambiguous callsign match"
        if len(call_sign_matches) == 1:
            return call_sign_matches[0], "matched by callsign"

    return None, "no matching mission"


def _match_api_departure_to_mission_with_reason(normalized, candidates):
    provider_key = _ups_numeric_core(normalized.get("provider_flight_number"))
    call_sign_key = _ups_numeric_core(normalized.get("call_sign"))

    if provider_key:
        provider_matches = _missions_matching_ups_key(candidates, provider_key)
        if len(provider_matches) > 1:
            return None, "ambiguous flight number match"
        if len(provider_matches) == 1:
            return _departure_match_or_rejection(
                provider_matches[0],
                normalized,
                "matched by provider flight",
            )

    if call_sign_key:
        call_sign_matches = _missions_matching_ups_key(candidates, call_sign_key)
        if len(call_sign_matches) > 1:
            return None, "ambiguous callsign match"
        if len(call_sign_matches) == 1:
            return _departure_match_or_rejection(
                call_sign_matches[0],
                normalized,
                "matched by callsign",
            )

    fallback_matches = _departure_destination_time_matches(candidates, normalized)
    if len(fallback_matches) > 1:
        return None, "ambiguous destination/time fallback"
    if len(fallback_matches) == 1:
        return fallback_matches[0], "matched by destination/time fallback"

    if not provider_key and not call_sign_key:
        return None, "unsupported/blank flight identity"
    return None, "no matching mission"


def _departure_match_or_rejection(mission, normalized, match_reason):
    if _departure_destination_mismatches(mission, normalized):
        return None, "destination mismatch"
    if _departure_time_mismatches(mission, normalized):
        return None, "departure time mismatch"
    return mission, match_reason


def _departure_destination_mismatches(mission, normalized):
    api_destination = _clean_upper(normalized.get("destination"))
    mission_destination = _clean_upper(getattr(mission, "destination", None))
    return bool(api_destination and mission_destination and api_destination != mission_destination)


def _departure_time_mismatches(mission, normalized):
    api_time = flight_api_provider_time_utc(normalized)
    mission_time = getattr(mission, "planned_datetime_utc", None)
    if not api_time or not mission_time:
        return False
    return _datetime_difference_minutes(api_time, mission_time) > DEPARTURE_TIME_MATCH_TOLERANCE_MINUTES


def _departure_destination_time_matches(candidates, normalized):
    api_destination = _clean_upper(normalized.get("destination"))
    api_time = flight_api_provider_time_utc(normalized)
    if not api_destination or not api_time:
        return []
    matches = []
    for mission in candidates:
        mission_destination = _clean_upper(getattr(mission, "destination", None))
        mission_time = getattr(mission, "planned_datetime_utc", None)
        if not mission_destination or not mission_time:
            continue
        if mission_destination != api_destination:
            continue
        if _datetime_difference_minutes(api_time, mission_time) <= DEPARTURE_TIME_MATCH_TOLERANCE_MINUTES:
            matches.append(mission)
    return matches


def _datetime_difference_minutes(left, right):
    return abs((_utc_naive(left) - _utc_naive(right)).total_seconds()) / 60


def departure_time_difference_minutes(mission, normalized):
    if not mission:
        return None
    api_time = flight_api_provider_time_utc(normalized)
    mission_time = getattr(mission, "planned_datetime_utc", None)
    if not api_time or not mission_time:
        return None
    return int(round(_datetime_difference_minutes(api_time, mission_time)))


def departure_tail_update_diagnostic(mission, normalized):
    api_tail = normalized.get("tail_number")
    if not api_tail:
        return "matched but no API tail/registration available"
    if not getattr(mission, "assigned_tail_number", None):
        return "tail updated"
    if getattr(mission, "tail_source", None) == "api":
        return "API-owned tail refreshed"
    return "tail update blocked because current tail is manual/non-API"


def departure_audit_candidate_mission(normalized, missions):
    candidates = [
        mission for mission in missions if getattr(mission, "mission_type", None) == "departure"
    ]
    provider_key = _ups_numeric_core(normalized.get("provider_flight_number"))
    call_sign_key = _ups_numeric_core(normalized.get("call_sign"))
    for match_key in (provider_key, call_sign_key):
        if not match_key:
            continue
        matches = _missions_matching_ups_key(candidates, match_key)
        if len(matches) == 1:
            return matches[0]
    fallback_matches = _departure_destination_time_matches(candidates, normalized)
    if len(fallback_matches) == 1:
        return fallback_matches[0]
    return None


def build_departure_match_audit(missions, ups_departure_candidates, diagnostics=None):
    diagnostics = diagnostics or {}
    departures = sorted(
        [mission for mission in missions if getattr(mission, "mission_type", None) == "departure"],
        key=lambda mission: (
            getattr(mission, "planned_datetime_utc", None) or datetime.max,
            getattr(mission, "id", 0) or 0,
        ),
    )
    rows = []
    for mission in departures:
        current_key = _ups_numeric_core_from_values(*_mission_flight_identity_values(mission))
        matched_api = next(
            (
                candidate
                for candidate in ups_departure_candidates
                if candidate.get("matched_mission_id") == mission.id
            ),
            None,
        )
        key_candidates = [
            candidate
            for candidate in ups_departure_candidates
            if current_key and current_key in (candidate.get("normalized_cores_tried") or [])
        ]
        api_candidate = matched_api or (key_candidates[0] if key_candidates else None)
        if matched_api:
            reason = ""
        elif api_candidate:
            reason = departure_audit_reason(api_candidate)
        elif diagnostics.get("raw_departures_count") and not ups_departure_candidates:
            reason = "parser did not produce departure candidate"
        else:
            reason = "provider did not return this departure"

        rows.append(
            {
                "current_flight_number": mission.flight_number,
                "current_flight_key": current_key or "-",
                "current_destination": mission.destination,
                "current_std_utc": mission.planned_datetime_utc,
                "current_tail": mission.assigned_tail_number,
                "api_candidate_found": bool(api_candidate),
                "matched": bool(matched_api),
                "matched_api_flight_number": (
                    api_candidate.get("provider_flight_number") if api_candidate else None
                ),
                "matched_api_call_sign": api_candidate.get("call_sign") if api_candidate else None,
                "matched_api_normalized_keys": normalized_cores_display(
                    api_candidate.get("normalized_cores_tried") if api_candidate else None
                ),
                "matched_api_destination": api_candidate.get("destination") if api_candidate else None,
                "matched_api_departure_time_utc": (
                    flight_api_provider_time_utc(api_candidate) if api_candidate else None
                ),
                "matched_api_tail": api_candidate.get("tail_number") if api_candidate else None,
                "reason": reason,
                "minute_difference": (
                    departure_time_difference_minutes(mission, api_candidate)
                    if api_candidate
                    else None
                ),
                "time_tolerance_minutes": DEPARTURE_TIME_MATCH_TOLERANCE_MINUTES,
                "tail_update_reason": (
                    api_candidate.get("tail_update_diagnostic")
                    if api_candidate
                    else None
                ),
            }
        )
    return rows


def api_ups_departure_audit_row(normalized):
    return {
        "provider_flight_number": normalized.get("provider_flight_number")
        or normalized.get("flight_number"),
        "call_sign": normalized.get("call_sign"),
        "normalized_keys": normalized_cores_display(normalized.get("normalized_cores_tried")),
        "destination": normalized.get("destination"),
        "departure_time_utc": flight_api_provider_time_utc(normalized),
        "tail_number": normalized.get("tail_number"),
        "api_status": normalized.get("api_status_raw"),
        "matched_current_mission": normalized.get("matched_mission_flight_number")
        or normalized.get("candidate_mission_flight_number"),
        "matched": bool(normalized.get("matched_mission_id")),
        "unmatched_reason": (
            "" if normalized.get("matched_mission_id") else departure_audit_reason(normalized)
        ),
        "tail_update_reason": normalized.get("tail_update_diagnostic"),
        "minute_difference": normalized.get("departure_time_difference_minutes"),
        "time_tolerance_minutes": DEPARTURE_TIME_MATCH_TOLERANCE_MINUTES,
    }


def departure_audit_reason(normalized):
    reason = normalized.get("unmatched_reason") or "flight key not found"
    if reason == "no matching mission":
        return "flight key not found"
    if reason == "ambiguous flight number match":
        return "ambiguous flight key match"
    return reason


def apply_api_data_to_mission(mission, normalized, settings, now=None):
    now_utc = _utc_naive(now)
    mission.api_status = map_api_status(normalized, settings, now=now_utc)
    mission.api_runway_time_utc = normalized["runway_time_utc"]
    mission.api_assumed_arrived_time_utc = assumed_arrived_time(normalized, settings)
    mission.api_aircraft_model = normalized["aircraft_model"] or mission.api_aircraft_model
    mission.api_last_seen_at_utc = now_utc

    if mission.mission_type == "arrival" and normalized["revised_time_utc"]:
        mission.eta_datetime_utc = normalized["revised_time_utc"]
        mission.eta_source = "api"

    if normalized["tail_number"] and (
        not mission.assigned_tail_number
        or mission.tail_source == "api"
    ):
        mission.assigned_tail_number = normalized["tail_number"]
        mission.tail_source = "api"
        mission.tail_updated_at = now_utc

    tail_state = ensure_tail_state_for_mission(mission)
    if tail_state and normalized["aircraft_model"] and tail_state.aircraft_type_source != "manual":
        tail_state.aircraft_type = normalized["aircraft_model"]
        tail_state.aircraft_type_source = "api"

    return mission


def map_api_status(normalized, settings, now=None):
    runway_time = normalized.get("runway_time_utc")
    if runway_time:
        assumed_time = assumed_arrived_time(normalized, settings)
        if assumed_time and _utc_naive(now) >= assumed_time:
            return API_STATUS_ASSUMED_ARRIVED
        return API_STATUS_ON_GROUND

    raw = str(normalized.get("api_status_raw") or "").strip().lower().replace("_", " ")
    if raw in {"expected", "scheduled", "expected/scheduled"}:
        return API_STATUS_SCHEDULED
    if "en route" in raw or "enroute" in raw or raw.startswith("departed"):
        return API_STATUS_IN_AIR
    return API_STATUS_SCHEDULED


def assumed_arrived_time(normalized, settings):
    runway_time = normalized.get("runway_time_utc")
    if not runway_time:
        return None
    return runway_time + timedelta(minutes=taxi_to_ramp_minutes(settings))


def taxi_to_ramp_minutes(settings):
    value = getattr(settings, "taxi_to_ramp_minutes", 10)
    if value is None:
        return 10
    try:
        return int(value)
    except (TypeError, ValueError):
        return 10


def flight_api_operational_time_utc(record, settings):
    mission_type = _record_value(record, "mission_type")
    if mission_type == "arrival":
        runway_time = _record_value(record, "runway_time_utc")
        if runway_time:
            return runway_time + timedelta(minutes=taxi_to_ramp_minutes(settings))
    return (
        _record_value(record, "revised_time_utc")
        or _record_value(record, "scheduled_time_utc")
    )


def flight_api_provider_time_utc(record):
    return (
        _record_value(record, "runway_time_utc")
        or _record_value(record, "revised_time_utc")
        or _record_value(record, "scheduled_time_utc")
    )


def format_flight_api_local_time(value, gateway=None, timezone_name=None):
    if not value:
        return "-"
    timezone_name = timezone_name or (gateway_timezone(gateway) if gateway else "America/Chicago")
    local_value = _utc_to_local_naive(_utc_naive(value), timezone_name)
    return f"{local_value:%H:%M Local %b} {local_value.day}"


def upsert_review_item(gateway, operation, normalized):
    existing = FlightApiReviewItem.query.filter_by(
        sort_date_operation_id=operation.id,
        review_key=normalized["review_key"],
    ).first()
    if existing and existing.review_status in {"ignored", "accepted"}:
        return existing, True

    item = existing or FlightApiReviewItem(
        sort_date_operation_id=operation.id,
        gateway_id=gateway.id if gateway else operation.gateway_id,
        gateway_code=operation.gateway_code,
        sort_date=operation.sort_date,
        sort_name=operation.sort_name,
        mission_type=normalized["mission_type"],
        review_key=normalized["review_key"],
    )
    item.gateway_id = gateway.id if gateway else operation.gateway_id
    item.gateway_code = operation.gateway_code
    item.sort_date = operation.sort_date
    item.sort_name = operation.sort_name
    item.review_status = "pending"
    item.flight_number = normalized["flight_number"]
    item.call_sign = normalized["call_sign"]
    item.origin = normalized["origin"]
    item.destination = normalized["destination"]
    item.revised_time_utc = normalized["revised_time_utc"] or normalized["scheduled_time_utc"]
    item.runway_time_utc = normalized["runway_time_utc"]
    item.tail_number = normalized["tail_number"]
    item.aircraft_model = normalized["aircraft_model"]
    item.api_status = normalized["api_status_raw"]
    item.raw_payload = json.dumps(normalized["raw"], sort_keys=True, default=str)
    item.review_reason = normalized.get("unmatched_reason") or "no matching mission"
    item.normalized_cores_tried = normalized_cores_display(
        normalized.get("normalized_cores_tried")
    )
    if not existing:
        db.session.add(item)
    return item, False


def preview_review_item_for_normalized(gateway, operation, normalized):
    existing = FlightApiReviewItem.query.filter_by(
        sort_date_operation_id=operation.id,
        review_key=normalized["review_key"],
    ).first()
    if existing and existing.review_status in {"ignored", "accepted"}:
        return existing, True
    return {
        "sort_date_operation_id": operation.id,
        "gateway_id": gateway.id if gateway else operation.gateway_id,
        "gateway_code": operation.gateway_code,
        "sort_date": operation.sort_date,
        "sort_name": operation.sort_name,
        "mission_type": normalized["mission_type"],
        "review_key": normalized["review_key"],
        "review_status": "preview",
        "flight_number": normalized["flight_number"],
        "call_sign": normalized["call_sign"],
        "origin": normalized["origin"],
        "destination": normalized["destination"],
        "revised_time_utc": normalized["revised_time_utc"] or normalized["scheduled_time_utc"],
        "scheduled_time_utc": normalized["scheduled_time_utc"],
        "runway_time_utc": normalized["runway_time_utc"],
        "tail_number": normalized["tail_number"],
        "aircraft_model": normalized["aircraft_model"],
        "api_status": normalized["api_status_raw"],
        "review_reason": normalized.get("unmatched_reason") or "no matching mission",
        "normalized_cores_tried": normalized_cores_display(
            normalized.get("normalized_cores_tried")
        ),
    }, False


def replace_active_review_queue_for_operation(operation):
    if not operation:
        return 0
    deleted = (
        FlightApiReviewItem.query.filter_by(
            sort_date_operation_id=operation.id,
            review_status="pending",
        )
        .delete(synchronize_session=False)
    )
    db.session.flush()
    return int(deleted or 0)


def build_api_added_mission(operation, normalized):
    timezone_name = gateway_timezone(operation.gateway)
    planned_utc = (
        normalized.get("revised_time_utc")
        or normalized.get("scheduled_time_utc")
        or _local_datetime_to_utc_naive(
            datetime.combine(operation.sort_date, time(0, 0)),
            timezone_name,
        )
    )
    planned_local = _utc_to_local_naive(planned_utc, timezone_name)
    mission = SortDateMission(
        sort_date_operation=operation,
        sort_date=operation.sort_date,
        gateway_code=operation.gateway_code,
        sort_name=operation.sort_name,
        mission_type=normalized["mission_type"],
        mission_source="api",
        wave="1",
        master_flight_schedule_id=None,
        flight_number=normalized["flight_number"],
        origin=normalized["origin"] or AIRPORT_CODE,
        destination=normalized["destination"] or AIRPORT_CODE,
        timezone=timezone_name,
        planned_datetime_local=planned_local,
        planned_datetime_utc=planned_utc,
        planned_source="api",
        assigned_tail_number=normalized["tail_number"] or None,
        tail_source="api" if normalized["tail_number"] else "unknown",
        tail_updated_at=datetime.utcnow() if normalized["tail_number"] else None,
        api_status=normalized.get("api_status_raw") or None,
        api_aircraft_model=normalized["aircraft_model"] or None,
        api_added_current_sort_only=True,
    )
    if mission.mission_type == "arrival":
        mission.arrival_status = "scheduled"
    else:
        mission.departure_status = "loading"
    return mission


def pending_review_items_for_operation(operation):
    if not operation:
        return []
    items = (
        FlightApiReviewItem.query.filter_by(
            sort_date_operation_id=operation.id,
            review_status="pending",
        )
        .order_by(
            FlightApiReviewItem.mission_type.asc(),
            FlightApiReviewItem.revised_time_utc.asc(),
            FlightApiReviewItem.id.asc(),
        )
        .all()
    )
    missions = SortDateMission.query.filter_by(sort_date_operation_id=operation.id).all()
    for item in items:
        item.review_reason = review_reason_for_item(item, missions)
        item.normalized_cores_tried = normalized_cores_display(
            _ups_numeric_cores_from_values(item.flight_number, item.call_sign)
        )
    return items


def review_reason_for_item(review_item, missions):
    raw_payload = _review_item_raw_payload(review_item)
    normalized = {
        "mission_type": review_item.mission_type,
        "flight_number": review_item.flight_number,
        "call_sign": review_item.call_sign,
        "provider_flight_number": _api_declared_flight_number(raw_payload),
        "origin": review_item.origin,
        "destination": review_item.destination,
        "revised_time_utc": review_item.revised_time_utc,
        "scheduled_time_utc": review_item.revised_time_utc,
        "runway_time_utc": review_item.runway_time_utc,
        "flight_variants": _flight_number_variants(
            review_item.flight_number,
            review_item.call_sign,
        ),
    }
    _mission, reason = match_api_flight_to_mission_with_reason(normalized, missions)
    if _mission:
        return reason or "matching mission now exists"
    return reason or "no matching mission"


def normalized_cores_display(cores):
    values = [str(core) for core in (cores or []) if core is not None and str(core)]
    return ", ".join(values) if values else "-"


def _review_item_raw_payload(review_item):
    try:
        payload = json.loads(review_item.raw_payload or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def review_item_or_404(gateway, review_item_id):
    return FlightApiReviewItem.query.filter_by(
        id=review_item_id,
        gateway_code=gateway.code,
    ).first_or_404()


def _empty_result(gateway, operation, provider_enabled, message):
    return {
        "provider_enabled": provider_enabled,
        "attempted": False,
        "gateway": gateway,
        "operation": operation,
        "matched": [],
        "review_items": [],
        "ignored_count": 0,
        "suppressed_review_count": 0,
        "non_ups_ignored": 0,
        "review_queue_replaced": False,
        "replaced_review_count": 0,
        "usage_units_consumed": 0,
        "usage_polls_used": None,
        "provider_error": False,
        "provider_status_code": None,
        "request_host": None,
        "request_path_query": None,
        "user_agent_sent": None,
        "accept_header_sent": None,
        "api_key_present": None,
        "api_key_normalized": None,
        "api_key_appears_quoted": None,
        "provider_response_snippet": None,
        "poll_rfd_local_time": None,
        "poll_utc_time": None,
        "source": "live",
        "replay_preview": False,
        "message": message,
        **_empty_import_count_diagnostics(),
    }


def _empty_import_count_diagnostics():
    diagnostics = {}
    for prefix in ("raw", "ups", "matched", "unmatched", "non_ups_ignored"):
        diagnostics[f"{prefix}_arrivals_count"] = 0
        diagnostics[f"{prefix}_departures_count"] = 0
    diagnostics["first_provider_departure_time_utc"] = None
    diagnostics["last_provider_departure_time_utc"] = None
    return diagnostics


def _increment_count(diagnostics, prefix, mission_type):
    type_key = "departures" if mission_type == "departure" else "arrivals"
    key = f"{prefix}_{type_key}_count"
    diagnostics[key] = int(diagnostics.get(key, 0) or 0) + 1


def _replay_result(
    result,
    gateway,
    operation,
    settings,
    now=None,
    provider_error=False,
):
    attempted_at = _utc_naive(now)
    windows = {}
    if operation:
        windows.update(sort_flight_lookup_window_snapshot(operation, settings))
        windows.update(api_polling_window_snapshot(operation, settings))
        windows.update(ops_node_online_window_snapshot(operation, settings))
    result.update(
        {
            "provider_enabled": bool(settings.provider_enabled),
            "attempted": True,
            "provider_error": provider_error,
            "api_key_env_var": DEFAULT_API_KEY_ENV_VAR,
            "usage_units_consumed": 0,
            "usage_polls_used": None,
            "provider_status_code": None,
            "request_host": None,
            "request_path_query": "Replay mode: no external request",
            "user_agent_sent": False,
            "accept_header_sent": False,
            "api_key_present": None,
            "api_key_normalized": None,
            "api_key_appears_quoted": None,
            "provider_response_snippet": None,
            "review_queue_replaced": False,
            "replaced_review_count": 0,
            "source": "replay",
            "replay_preview": True,
            **windows,
            **_poll_time_diagnostics(gateway, attempted_at),
        }
    )
    return result


def _track_provider_departure_time(diagnostics, normalized):
    if normalized.get("mission_type") != "departure":
        return
    provider_time = flight_api_provider_time_utc(normalized)
    if not provider_time:
        return
    first_time = diagnostics.get("first_provider_departure_time_utc")
    last_time = diagnostics.get("last_provider_departure_time_utc")
    if first_time is None or provider_time < first_time:
        diagnostics["first_provider_departure_time_utc"] = provider_time
    if last_time is None or provider_time > last_time:
        diagnostics["last_provider_departure_time_utc"] = provider_time


def _poll_time_diagnostics(gateway, attempted_at):
    return {
        "poll_utc_time": attempted_at,
        "poll_rfd_local_time": _utc_to_local_naive(
            attempted_at,
            gateway_timezone(gateway) if gateway else "America/Chicago",
        ),
    }


def _record_value(record, key):
    if isinstance(record, dict):
        return record.get(key)
    return getattr(record, key, None)


def _safe_request_diagnostics(gateway_code, start_local, end_local, api_key):
    details = rapidapi_request_details(gateway_code, start_local, end_local)
    _normalized_key, key_diagnostics = normalize_api_key(api_key)
    return {
        "provider_status_code": None,
        "request_host": details["host"],
        "request_path_query": details["path_query"],
        "user_agent_sent": False,
        "accept_header_sent": False,
        "provider_response_snippet": None,
        **key_diagnostics,
    }


def _safe_provider_response_snippet(error, api_key=None, limit=300):
    raw_body = b""
    fp = getattr(error, "fp", None)
    if fp:
        try:
            raw_body = fp.read(limit * 4)
        except Exception:
            raw_body = b""
    if isinstance(raw_body, bytes):
        body = raw_body.decode("utf-8", errors="replace")
    else:
        body = str(raw_body or "")
    body = " ".join(body.split())
    if not body:
        return None
    body = _redact_provider_response(body, api_key)
    if len(body) > limit:
        body = f"{body[:limit].rstrip()}..."
    return body


def _redact_provider_response(body, api_key=None):
    normalized_key, _diagnostics = normalize_api_key(api_key)
    redacted = body
    if normalized_key:
        redacted = redacted.replace(normalized_key, "[redacted]")
    redacted = re.sub(
        r"(?i)(x-rapidapi-key|aerodatabox_api_key|rapidapi[_ -]?key|api[_ -]?key)"
        r"(\s*[=:]\s*)"
        r"([\"']?)[^\"'\s,;{}<>]+",
        r"\1\2\3[redacted]",
        redacted,
    )
    redacted = re.sub(
        r"(?i)(authorization)(\s*[=:]\s*)([\"']?)(bearer\s+)?[^\"'\s,;{}<>]+",
        r"\1\2\3\4[redacted]",
        redacted,
    )
    return redacted


def _mission_type(api_flight):
    value = str(
        api_flight.get("_mission_type")
        or api_flight.get("direction")
        or api_flight.get("type")
        or ""
    ).lower()
    if "dep" in value:
        return "departure"
    return "arrival"


def _api_declared_flight_number(api_flight):
    flight = _as_dict(api_flight.get("flight"))
    number = (
        api_flight.get("number")
        or api_flight.get("flightNumber")
        or api_flight.get("iataNumber")
        or api_flight.get("icaoNumber")
        or flight.get("number")
        or flight.get("iataNumber")
        or flight.get("icaoNumber")
        or flight.get("iata")
        or flight.get("icao")
    )
    return _clean_flight_number(number)


def _api_call_sign(api_flight):
    flight = _as_dict(api_flight.get("flight"))
    return _clean_upper(
        api_flight.get("callSign")
        or api_flight.get("callsign")
        or api_flight.get("call_sign")
        or flight.get("callSign")
        or flight.get("callsign")
        or flight.get("call_sign")
        or flight.get("icaoNumber")
        or flight.get("icao")
    )


def _airline_info(api_flight):
    airline = _as_dict(api_flight.get("airline"))
    flight = _as_dict(api_flight.get("flight"))
    flight_airline = _as_dict(flight.get("airline"))
    return {
        "icao": airline.get("icao") or flight_airline.get("icao") or api_flight.get("airlineIcao"),
        "iata": airline.get("iata") or flight_airline.get("iata") or api_flight.get("airlineIata"),
    }


def _flight_number_variants(flight_number, call_sign):
    variants = []
    for value in (flight_number, call_sign):
        cleaned = _clean_flight_number(value)
        if cleaned and cleaned not in variants:
            variants.append(cleaned)
        if cleaned.startswith("UPS"):
            converted = f"5X{cleaned[3:]}"
            if converted not in variants:
                variants.append(converted)
        if cleaned.startswith("5X"):
            converted = f"UPS{cleaned[2:]}"
            if converted not in variants:
                variants.append(converted)
    return variants


def _ups_numeric_core_from_values(*values):
    cores = _ups_numeric_cores_from_values(*values)
    return cores[0] if cores else None


def _missions_matching_ups_key(missions, match_key):
    return [
        mission
        for mission in missions
        if _ups_numeric_core_from_values(*_mission_flight_identity_values(mission)) == match_key
    ]


def _ups_numeric_cores_from_values(*values):
    cores = []
    for value in values:
        core = _ups_numeric_core(value)
        if core and core not in cores:
            cores.append(core)
    return cores


def _ups_numeric_core(value):
    cleaned = _clean_flight_number(value)
    if not cleaned:
        return None
    if cleaned.startswith("UPS"):
        cleaned = cleaned[3:]
    elif cleaned.startswith("5X"):
        cleaned = cleaned[2:]
    digits = "".join(re.findall(r"\d", cleaned))
    if not digits:
        return None
    return str(int(digits)).zfill(4)


def _mission_flight_identity_values(mission):
    return [getattr(mission, "flight_number", None)]


def _review_key(mission_type, flight_number, call_sign, origin, destination):
    key_flight = _clean_flight_number(flight_number or call_sign)
    return "|".join(
        (
            mission_type,
            key_flight,
            _clean_upper(call_sign),
            _clean_upper(origin),
            _clean_upper(destination),
        )
    )


def _airport_code(airport):
    if isinstance(airport, str):
        return _clean_upper(airport)
    if not isinstance(airport, dict):
        return ""
    return _clean_upper(
        airport.get("iata")
        or airport.get("icao")
        or airport.get("code")
        or airport.get("localCode")
    )


def _leg_time(leg, field_name):
    field = leg.get(field_name) or {}
    if isinstance(field, dict):
        value = field.get("local") or field.get("utc")
        if value:
            return value
    if field:
        return field
    local_key = f"{field_name}Local"
    utc_key = f"{field_name}Utc"
    snake_key = _camel_to_snake(field_name)
    return (
        leg.get(local_key)
        or leg.get(utc_key)
        or leg.get(f"{snake_key}_local")
        or leg.get(f"{snake_key}_utc")
    )


def _as_dict(value):
    return value if isinstance(value, dict) else {}


def _camel_to_snake(value):
    output = []
    for character in str(value or ""):
        if character.isupper() and output:
            output.append("_")
        output.append(character.lower())
    return "".join(output)
    return field


def _parse_provider_datetime(value, timezone_name):
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            try:
                parsed = datetime.strptime(text, "%Y-%m-%d %H:%M")
            except ValueError:
                return None
    if parsed.tzinfo:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return _local_datetime_to_utc_naive(parsed, timezone_name)


def _local_datetime_to_utc_naive(value, timezone_name=None):
    timezone_name = timezone_name or "America/Chicago"
    try:
        localized = value.replace(tzinfo=ZoneInfo(timezone_name))
        return localized.astimezone(timezone.utc).replace(tzinfo=None)
    except ZoneInfoNotFoundError:
        if timezone_name == "America/Chicago":
            offset_hours = -5 if _is_us_central_daylight_time(value) else -6
            return value - timedelta(hours=offset_hours)
        return value


def _utc_to_local_naive(value, timezone_name=None):
    timezone_name = timezone_name or "America/Chicago"
    try:
        return value.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(timezone_name)).replace(tzinfo=None)
    except ZoneInfoNotFoundError:
        if timezone_name == "America/Chicago":
            standard_local = value - timedelta(hours=6)
            if _is_us_central_daylight_time(standard_local):
                return value - timedelta(hours=5)
        return value


def _utc_naive(value=None):
    if value is None:
        return datetime.utcnow()
    if value.tzinfo:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _gateway_today(gateway):
    try:
        return datetime.now(ZoneInfo(gateway_timezone(gateway))).date()
    except ZoneInfoNotFoundError:
        if gateway_timezone(gateway) == "America/Chicago":
            now_utc = datetime.utcnow()
            standard_local = now_utc - timedelta(hours=6)
            if _is_us_central_daylight_time(standard_local):
                return (now_utc - timedelta(hours=5)).date()
        return date.today()


def _format_provider_datetime(value):
    return value.replace(second=0, microsecond=0).isoformat(timespec="minutes")


def _clean_upper(value):
    return str(value or "").strip().upper()


def _clean_text(value):
    return str(value or "").strip()


def _clean_flight_number(value):
    return _clean_upper(value).replace(" ", "")


def _is_us_central_daylight_time(local_datetime):
    year = local_datetime.year
    dst_start = _nth_weekday_of_month(year, 3, 6, 2).replace(hour=2)
    dst_end = _nth_weekday_of_month(year, 11, 6, 1).replace(hour=2)
    return dst_start <= local_datetime < dst_end


def _nth_weekday_of_month(year, month, weekday, occurrence):
    candidate = datetime(year, month, 1)
    days_until_weekday = (weekday - candidate.weekday()) % 7
    return candidate + timedelta(days=days_until_weekday + (occurrence - 1) * 7)
