"""Shared, cache-bounded KRFD weather projection for NeoSubZero."""

import hashlib
import json
import math
import re
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from flask import current_app

from app.models import GatewaySortMatrix
from app.services.gateway_matrix import (
    current_gateway_local_datetime,
    gateway_timezone,
    sort_lookup_window_for_operation,
)
from app.services.memory_diagnostics import memory_diagnostics
from app.services.bounded_http import ExternalResponseError, read_json


KRFD_ICAO = "KRFD"
KRFD_LATITUDE = 42.1915
KRFD_LONGITUDE = -89.0876
AVIATION_WEATHER_METAR_URL = "https://aviationweather.gov/api/data/metar"
NWS_POINTS_URL = "https://api.weather.gov/points/{latitude},{longitude}"
METAR_TTL = timedelta(minutes=5)
METAR_STALE_TTL = timedelta(hours=6)
FORECAST_TTL = timedelta(minutes=30)
FORECAST_STALE_TTL = timedelta(hours=24)
FORECAST_CALENDAR_DAYS = 7
TV_UPCOMING_SORT_COUNT = 4
POINTS_TTL = timedelta(days=1)
POINTS_STALE_TTL = timedelta(days=7)
# Single-station observations and point metadata are small. Hourly forecasts
# include a full NWS horizon; grid data contains many time series. These bounds
# apply independently to compressed and decoded bytes, without truncation.
METAR_MAX_BYTES = 256 * 1024
POINTS_MAX_BYTES = 256 * 1024
HOURLY_MAX_BYTES = 1024 * 1024
GRID_MAX_BYTES = 4 * 1024 * 1024
FAILURE_BACKOFF_SECONDS = 60
MAX_FAILURE_BACKOFF_SECONDS = 15 * 60
_CACHE = {}
_FAILURES = {}
_INFLIGHT = set()
_CACHE_LOCK = threading.RLock()
_ISO_DURATION = re.compile(r"^PT(?:(\d+)H)?(?:(\d+)M)?$")


class NeoSubZeroWeatherError(RuntimeError):
    """Safe weather-provider failure."""


@memory_diagnostics("neosubzero_weather_context")
def neosubzero_weather_context(gateway, operation=None, *, now=None):
    """Return current KRFD observation and operational-sort forecasts."""
    snapshot = _weather_snapshot(now=now)
    current = parse_aviation_weather_metar(snapshot["metar"].get("value"))
    forecast = snapshot["forecast"].get("value")
    local_now = current_gateway_local_datetime(gateway, now=now)
    start_date = operation.sort_date if operation is not None else local_now.date()
    forecast_end = start_date + timedelta(days=FORECAST_CALENDAR_DAYS)
    forecast_cards = _forecast_cards(
        gateway,
        operation,
        forecast,
        now=local_now,
        upcoming_limit=TV_UPCOMING_SORT_COUNT,
    )
    # The management view retains its original date horizon and provider-error state.
    management_cards = (
        tuple(card for card in forecast_cards if card["sort_date"] < forecast_end)
        if isinstance(forecast, dict) else ()
    )
    current.update(
        {
            "stale": snapshot["metar"]["stale"],
            "error": snapshot["metar"].get("error"),
        }
    )
    forecast_status = {
        "stale": snapshot["forecast"]["stale"],
        "error": snapshot["forecast"].get("error"),
        "issued_at_label": _forecast_issued_label(forecast),
    }
    current_sort = (
        next(
            (
                card
                for card in management_cards
                if card["sort_date"] == operation.sort_date
            ),
            None,
        )
        if operation is not None
        else None
    )
    future_forecast = tuple(
        card
        for card in management_cards
        if operation is None or card["sort_date"] != operation.sort_date
    )
    return {
        "station": KRFD_ICAO,
        "current": current,
        "theme": current_weather_theme(current),
        "current_sort": current_sort,
        "future_forecast": future_forecast,
        "tv_forecast_extra": tuple(
            card for card in forecast_cards
            if card.get("upcoming_on_tv") and (not isinstance(forecast, dict) or card["sort_date"] >= forecast_end)
        ),
        "forecast_status": forecast_status,
        "revision": _weather_revision(snapshot),
        "sources": {
            "current": "AviationWeather.gov",
            "forecast": "NOAA / National Weather Service",
        },
    }


def neosubzero_weather_revision(*, now=None):
    """Refresh cached provider state and return its stable content revision."""
    return _weather_revision(_weather_snapshot(now=now))


def clear_neosubzero_weather_cache():
    """Clear the process-local provider cache (primarily for focused tests)."""
    with _CACHE_LOCK:
        _CACHE.clear()
        _FAILURES.clear()


def parse_aviation_weather_metar(payload):
    """Normalize the latest AviationWeather JSON METAR into display values."""
    row = payload[0] if isinstance(payload, list) and payload else None
    if not isinstance(row, dict):
        return _unavailable_current()
    temperature_c = _number(row.get("temp"))
    dewpoint_c = _number(row.get("dewp"))
    temperature_f = _c_to_f(temperature_c)
    dewpoint_f = _c_to_f(dewpoint_c)
    humidity = _relative_humidity(temperature_c, dewpoint_c)
    spread = (
        temperature_f - dewpoint_f
        if temperature_f is not None and dewpoint_f is not None
        else None
    )
    clouds = []
    for cloud in row.get("clouds") or ():
        if not isinstance(cloud, dict):
            continue
        cover = str(cloud.get("cover") or "").strip().upper()
        base = _number(cloud.get("base"))
        if cover:
            clouds.append(f"{cover} {int(base):,}" if base is not None else cover)
    observed_at = _parse_datetime(row.get("reportTime"))
    wind_speed = _number(row.get("wspd"))
    wind_gust = _number(row.get("wgst"))
    return {
        "available": True,
        "observed_at": observed_at,
        "observed_at_label": _utc_label(observed_at),
        "temperature": _degree_label(temperature_f),
        "temperature_value": temperature_f,
        "dewpoint": _degree_label(dewpoint_f),
        "dewpoint_value": dewpoint_f,
        "spread": _degree_label(spread),
        "spread_value": spread,
        "relative_humidity": _percent_label(humidity),
        "relative_humidity_value": humidity,
        "wind": _metar_wind(row),
        "wind_speed_value": wind_speed,
        "wind_gust_value": wind_gust,
        "visibility": _visibility_label(row.get("visib")),
        "conditions": str(row.get("wxString") or "None reported").strip(),
        "sky": " · ".join(clouds) or str(row.get("cover") or "Clear").upper(),
        "flight_category": str(row.get("fltCat") or "-").strip().upper(),
        "raw_observation": str(row.get("rawOb") or "").strip(),
    }


def preliminary_frost_risk(
    *, temperature_f=None, dewpoint_spread_f=None, relative_humidity=None,
    wind_mph=None, conditions="",
):
    """Compatibility shape only: weather heuristics are not model predictions.

    Frozen experimental H0/H1/H3/H6 scores are displayed from verified research
    evidence in FROST, never inferred from a DUCC weather forecast.
    """
    return dict(level="UNAVAILABLE", score=None, physical_frost_probability=False,
        rationale="No verified experimental model score is attached to this weather hour.",
        explanation="UNAVAILABLE — weather conditions are not a trained FROST prediction.",
        explanation_reasons=())


def preliminary_frost_trends(hours):
    """Do not turn legacy heuristic levels into apparent prediction trends."""
    return tuple(dict(row, frost_risk=dict(preliminary_frost_risk(), trend=None, trend_label=""))
        for row in (hours or ()))


def current_weather_theme(current):
    """Classify ambient UCC styling from the actual KRFD observation only."""
    current = current or {}
    text = " ".join(
        str(current.get(key) or "")
        for key in ("conditions", "sky", "raw_observation")
    ).casefold()
    if any(token in text for token in ("fzra", "fzdz", "freezing rain", "freezing drizzle")):
        return "freezing-rain"
    if any(token in text for token in (" snow", "sn", "snow")):
        return "snow"
    if any(token in text for token in ("fzfg", "freezing fog")):
        return "freezing-fog"
    if any(token in text for token in (" fog", "fg", "mist")):
        return "fog"
    if any(token in text for token in (" rain", "drizzle", " ra", " dz")):
        return "rain"
    if (_number(current.get("wind_gust_value")) or 0) >= 25 or (
        _number(current.get("wind_speed_value")) or 0
    ) >= 20:
        return "windy"
    if any(token in text for token in ("ovc", "bkn", "overcast")):
        return "overcast"
    if (_number(current.get("temperature_value")) or 99) <= 36:
        return "clear-cold"
    return "neutral"


def _weather_snapshot(*, now=None):
    now = _utc_now(now)
    if not _weather_enabled():
        empty = {"value": None, "stale": False, "error": None}
        return {"metar": dict(empty), "forecast": dict(empty)}
    metar = _cached_fetch(
        "krfd_metar",
        METAR_TTL,
        METAR_STALE_TTL,
        _fetch_metar,
        now=now,
    )
    forecast = _cached_fetch(
        "krfd_forecast",
        FORECAST_TTL,
        FORECAST_STALE_TTL,
        _fetch_forecast,
        now=now,
    )
    return {"metar": metar, "forecast": forecast}


def _weather_enabled():
    configured = current_app.config.get("NEOSUBZERO_WEATHER_ENABLED")
    return bool(configured if configured is not None else not current_app.testing)


def _cached_fetch(key, ttl, stale_ttl, fetcher, *, now):
    # Also used by the bounded FROST observation reader with epoch seconds.
    at = now.timestamp() if isinstance(now, datetime) else float(now)
    ttl_seconds = ttl.total_seconds() if isinstance(ttl, timedelta) else float(ttl)
    stale_seconds = stale_ttl.total_seconds() if isinstance(stale_ttl, timedelta) else float(stale_ttl)
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached and at - cached["fetched_at"] < ttl_seconds:
            return {"value": cached["value"], "stale": False, "error": None}
        failure = _FAILURES.get(key)
        if key in _INFLIGHT or (failure and at < failure['next_retry_at']):
            return _last_good(cached, at, stale_seconds)
        _INFLIGHT.add(key)
    # No provider I/O under the cache lock. Other operators return immediately
    # with bounded-age evidence/unavailable; unrelated providers remain usable.
    try:
        value = fetcher(now=now)
    except Exception:
        with _CACHE_LOCK:
            attempts = min((_FAILURES.get(key) or {}).get('attempts', 0) + 1, 16)
            delay = min(FAILURE_BACKOFF_SECONDS * 2 ** (attempts - 1), MAX_FAILURE_BACKOFF_SECONDS)
            _FAILURES[key] = dict(attempts=attempts, last_failed_at=at, next_retry_at=at + delay)
            return _last_good(_CACHE.get(key), at, stale_seconds)
    else:
        with _CACHE_LOCK:
            _CACHE[key] = {"value": value, "fetched_at": at}
            _FAILURES.pop(key, None)
        return {"value": value, "stale": False, "error": None}
    finally:
        with _CACHE_LOCK:
            _INFLIGHT.discard(key)


def _last_good(cached, at, stale_seconds):
    if cached and at - cached['fetched_at'] < stale_seconds:
        return dict(value=cached['value'], stale=True,
                    error="Latest weather refresh failed; showing last good data.")
    return dict(value=None, stale=False, error="Weather data is temporarily unavailable.")


def _fetch_metar(*, now=None):
    query = urlencode({"ids": KRFD_ICAO, "format": "json", "hours": 2})
    payload = _request_json(f"{AVIATION_WEATHER_METAR_URL}?{query}", max_bytes=METAR_MAX_BYTES)
    if not isinstance(payload, list) or not payload or any(
        not isinstance(row, dict) or row.get('icaoId') != KRFD_ICAO for row in payload
    ):
        raise NeoSubZeroWeatherError("KRFD METAR is unavailable.")
    return payload


def _fetch_points(*, now=None):
    payload = _request_json(NWS_POINTS_URL.format(latitude=KRFD_LATITUDE, longitude=KRFD_LONGITUDE),
                            max_bytes=POINTS_MAX_BYTES)
    properties = payload.get('properties') if isinstance(payload, dict) else None
    if not isinstance(properties, dict) or not all(
        isinstance(properties.get(key), str) and properties[key].startswith('https://api.weather.gov/')
        for key in ('forecastHourly', 'forecastGridData')
    ):
        raise NeoSubZeroWeatherError('NWS point response is invalid.')
    return payload


def _fetch_forecast(*, now):
    points = _cached_fetch(
        "krfd_nws_points",
        POINTS_TTL,
        POINTS_STALE_TTL,
        _fetch_points,
        now=now,
    )
    properties = (points.get("value") or {}).get("properties") or {}
    hourly_url = properties.get("forecastHourly")
    grid_url = properties.get("forecastGridData")
    if not hourly_url or not grid_url:
        raise NeoSubZeroWeatherError("NWS forecast grid is unavailable.")
    hourly = _request_json(hourly_url, max_bytes=HOURLY_MAX_BYTES)
    grid = _request_json(grid_url, max_bytes=GRID_MAX_BYTES)
    for payload in (hourly, grid):
        if not isinstance(payload, dict) or not isinstance(payload.get('properties'), dict):
            raise NeoSubZeroWeatherError('NWS forecast response is invalid.')
    periods = hourly['properties'].get('periods')
    if not isinstance(periods, list) or not periods or not all(isinstance(row, dict) for row in periods):
        raise NeoSubZeroWeatherError('NWS hourly periods are unavailable.')
    return {"hourly": hourly, "grid": grid}


def _request_json(url, *, max_bytes=GRID_MAX_BYTES):
    request = Request(
        url,
        headers={
            "Accept": "application/geo+json, application/json",
            "Accept-Encoding": "identity",
            "User-Agent": current_app.config.get(
                "NEOSUBZERO_WEATHER_USER_AGENT",
                "NeoAppsWeather/1.0 (https://github.com/KKnFinite/NeoApps)",
            ),
        },
    )
    try:
        with urlopen(request, timeout=8) as response:
            payload = read_json(response, maximum=max_bytes)
            if not isinstance(payload, (dict, list)):
                raise ExternalResponseError('Official weather response structure is invalid.')
            return payload
    except HTTPError as exc:
        exc.close()
        raise NeoSubZeroWeatherError("Official weather source is unavailable.") from None
    except (URLError, OSError, ExternalResponseError) as exc:
        raise NeoSubZeroWeatherError("Official weather source is unavailable.") from None


def _forecast_cards(gateway, operation, payload, *, now=None, upcoming_limit=0):
    has_forecast = isinstance(payload, dict)
    if not has_forecast and not upcoming_limit:
        return ()
    payload = payload if has_forecast else {}
    periods = ((payload.get("hourly") or {}).get("properties") or {}).get("periods") or ()
    grid_properties = (payload.get("grid") or {}).get("properties") or {}
    gust_intervals = _grid_intervals(grid_properties.get("windGust"))
    timezone_name = gateway_timezone(gateway)
    zone = ZoneInfo(timezone_name)
    sort_name = str(getattr(operation, "sort_name", "night") or "night").strip().lower()
    local_now = current_gateway_local_datetime(gateway, now=now)
    start_date = operation.sort_date if operation is not None else local_now.date()
    forecast_end = start_date + timedelta(days=FORECAST_CALENDAR_DAYS)
    active_days = {
        row.day_of_week
        for row in GatewaySortMatrix.query.filter_by(
            gateway_id=gateway.id,
            sort_name=sort_name,
            is_active=True,
        ).all()
    }
    sort_dates = [
        start_date + timedelta(days=offset)
        # Even a sort scheduled once a week has four future windows in this span.
        for offset in range(FORECAST_CALENDAR_DAYS * (upcoming_limit + 1))
        if (start_date + timedelta(days=offset)).strftime("%A").casefold()
        in active_days
    ]
    if operation is not None and operation.sort_date not in sort_dates:
        sort_dates.insert(0, operation.sort_date)

    hourly_rows = [
        row
        for period in periods
        if isinstance(period, dict)
        and (row := _forecast_hour(period, gust_intervals, zone)) is not None
    ]
    cards = []
    for sort_date in sort_dates:
        management_window = has_forecast and sort_date < forecast_end
        if not management_window and not upcoming_limit:
            break
        candidate = SimpleNamespace(
            sort_date=sort_date,
            sort_name=sort_name,
            gateway=gateway,
        )
        start_local, end_local = sort_lookup_window_for_operation(candidate, gateway)
        future_window = sort_date > operation.sort_date if operation is not None else end_local > local_now
        upcoming_on_tv = bool(upcoming_limit and future_window)
        if not management_window and not upcoming_on_tv:
            continue
        start = start_local.replace(tzinfo=zone)
        end = end_local.replace(tzinfo=zone)
        hours = tuple(
            row for row in hourly_rows
            if row["start"] < end and row["end"] > start
        )
        card = _forecast_card(sort_date, start, end, hours)
        card["upcoming_on_tv"] = upcoming_on_tv
        cards.append(card)
        if upcoming_on_tv:
            upcoming_limit -= 1
    return tuple(cards)


def _forecast_hour(period, gust_intervals, zone):
    start = _parse_datetime(period.get("startTime"))
    end = _parse_datetime(period.get("endTime"))
    if start is None or end is None:
        return None
    temperature = _temperature_f(period.get("temperature"), period.get("temperatureUnit"))
    dewpoint = _quantity_f(period.get("dewpoint"))
    spread = temperature - dewpoint if temperature is not None and dewpoint is not None else None
    gust = _grid_value_at(gust_intervals, start)
    if gust is not None:
        gust = gust * 0.621371
    probability = _number((period.get("probabilityOfPrecipitation") or {}).get("value"))
    humidity = _number((period.get("relativeHumidity") or {}).get("value"))
    wind_speed = _wind_speed_mph(period.get("windSpeed"))
    start_local = start.astimezone(zone)
    row = {
        "start": start_local,
        "end": end.astimezone(zone),
        "time": start_local.strftime("%H%M"),
        "temperature_value": temperature,
        "temperature": _degree_label(temperature),
        "dewpoint_value": dewpoint,
        "dewpoint": _degree_label(dewpoint),
        "spread_value": spread,
        "spread": _degree_label(spread),
        "probability_value": probability,
        "probability": _percent_label(probability),
        "humidity": _percent_label(humidity),
        "humidity_value": humidity,
        "wind": " ".join(
            piece for piece in (
                str(period.get("windDirection") or "").strip(),
                str(period.get("windSpeed") or "").strip(),
            ) if piece
        ) or "-",
        "gust_value": gust,
        "gust": f"G{_rounded(gust)} mph" if gust is not None else "-",
        "condition": str(period.get("shortForecast") or "-").strip(),
    }
    row["frost_risk"] = preliminary_frost_risk(
        temperature_f=temperature,
        dewpoint_spread_f=spread,
        relative_humidity=humidity,
        wind_mph=wind_speed,
        conditions=row["condition"],
    )
    return row


def _forecast_card(sort_date, start, end, hours):
    hours = preliminary_frost_trends(hours)
    temperatures = [row["temperature_value"] for row in hours if row["temperature_value"] is not None]
    dewpoints = [row["dewpoint_value"] for row in hours if row["dewpoint_value"] is not None]
    spreads = [row["spread_value"] for row in hours if row["spread_value"] is not None]
    probabilities = [row["probability_value"] for row in hours if row["probability_value"] is not None]
    gusts = [row["gust_value"] for row in hours if row["gust_value"] is not None]
    conditions = []
    winds = []
    for row in hours:
        condition = row["condition"]
        if condition != "-" and (not conditions or conditions[-1] != condition):
            conditions.append(condition)
        wind = row["wind"]
        if wind != "-" and (not winds or winds[-1] != wind):
            winds.append(wind)
    return {
        "sort_date": sort_date,
        "date_label": sort_date.strftime("%a %b %d").upper(),
        "window_label": f"{start:%H:%M}–{end:%H:%M}",
        "temperature_range": _range_label(temperatures, "°F"),
        "dewpoint_range": _range_label(dewpoints, "°F"),
        "spread_range": _range_label(spreads, "°F"),
        "precipitation_probability": _percent_label(max(probabilities)) if probabilities else "-",
        "conditions": " → ".join(conditions[:3]) or "Forecast unavailable",
        "wind": " → ".join(winds[:3]) or "-",
        "gust": f"G{_rounded(max(gusts))} mph" if gusts else "-",
        "hours": hours,
    }


def _frost_explanation(temperature, spread, humidity, wind, conditions):
    pieces = []
    if temperature is not None:
        pieces.append(_degree_label(temperature))
    if spread is not None:
        pieces.append(f"spread {_degree_label(spread)}")
    if humidity is not None and humidity >= 80:
        pieces.append(f"RH {_percent_label(humidity)}")
    if wind is not None:
        pieces.append(
            "light wind"
            if wind <= 5
            else "strong wind"
            if wind >= 15
            else f"{_rounded(wind)} mph wind"
        )
    condition = str(conditions or "").strip()
    if condition and condition != "-":
        pieces.append(condition.casefold())
    return " · ".join(pieces) or "Forecast inputs unavailable"


def _grid_intervals(quantity):
    if not isinstance(quantity, dict):
        return ()
    intervals = []
    for row in quantity.get("values") or ():
        if not isinstance(row, dict):
            continue
        start, end = _valid_time_range(row.get("validTime"))
        value = _number(row.get("value"))
        if start and end and value is not None:
            intervals.append((start, end, value))
    return tuple(intervals)


def _valid_time_range(value):
    try:
        start_raw, duration_raw = str(value or "").split("/", 1)
    except ValueError:
        return None, None
    start = _parse_datetime(start_raw)
    match = _ISO_DURATION.fullmatch(duration_raw)
    if not start or not match:
        return None, None
    duration = timedelta(
        hours=int(match.group(1) or 0),
        minutes=int(match.group(2) or 0),
    )
    return start, start + duration


def _grid_value_at(intervals, target):
    for start, end, value in intervals:
        if start <= target < end:
            return value
    return None


def _weather_revision(snapshot):
    metar = snapshot["metar"].get("value") or ()
    metar_row = metar[0] if isinstance(metar, list) and metar else {}
    forecast = snapshot["forecast"].get("value") or {}
    hourly_properties = (forecast.get("hourly") or {}).get("properties") or {}
    payload = {
        "metar": metar_row.get("reportTime") if isinstance(metar_row, dict) else None,
        "forecast": hourly_properties.get("generatedAt") or hourly_properties.get("updateTime"),
        "metar_stale": snapshot['metar']['stale'],
        "forecast_stale": snapshot['forecast']['stale'],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _forecast_issued_label(payload):
    if not isinstance(payload, dict):
        return "-"
    properties = (payload.get("hourly") or {}).get("properties") or {}
    value = properties.get("generatedAt") or properties.get("updateTime")
    return _utc_label(_parse_datetime(value))


def _unavailable_current():
    return {
        "available": False,
        "observed_at": None,
        "observed_at_label": "-",
        "temperature": "-",
        "temperature_value": None,
        "dewpoint": "-",
        "dewpoint_value": None,
        "spread": "-",
        "spread_value": None,
        "relative_humidity": "-",
        "relative_humidity_value": None,
        "wind": "-",
        "wind_speed_value": None,
        "wind_gust_value": None,
        "visibility": "-",
        "conditions": "Unavailable",
        "sky": "-",
        "flight_category": "-",
        "raw_observation": "",
    }


def _metar_wind(row):
    direction = row.get("wdir")
    direction_label = (
        f"{int(direction):03d}°"
        if isinstance(direction, (int, float))
        else str(direction or "VRB").strip().upper()
    )
    speed = _number(row.get("wspd"))
    gust = _number(row.get("wgst"))
    pieces = [direction_label, f"{_rounded(speed)} kt" if speed is not None else "CALM"]
    if gust is not None:
        pieces.append(f"G{_rounded(gust)} kt")
    return " ".join(pieces)


def _visibility_label(value):
    text = str(value or "").strip()
    return f"{text} SM" if text else "-"


def _wind_speed_mph(value):
    match = re.search(r"(\d+(?:\.\d+)?)", str(value or ""))
    return float(match.group(1)) if match else None


def _relative_humidity(temperature_c, dewpoint_c):
    if temperature_c is None or dewpoint_c is None:
        return None
    numerator = math.exp((17.625 * dewpoint_c) / (243.04 + dewpoint_c))
    denominator = math.exp((17.625 * temperature_c) / (243.04 + temperature_c))
    return max(0, min(100, 100 * numerator / denominator))


def _temperature_f(value, unit):
    number = _number(value)
    if number is None:
        return None
    return _c_to_f(number) if str(unit or "").upper() == "C" else number


def _quantity_f(quantity):
    if not isinstance(quantity, dict):
        return None
    value = _number(quantity.get("value"))
    if value is None:
        return None
    unit = str(quantity.get("unitCode") or "")
    return _c_to_f(value) if "degC" in unit else value


def _c_to_f(value):
    return value * 9 / 5 + 32 if value is not None else None


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _rounded(value):
    return str(int(round(value)))


def _degree_label(value):
    return f"{_rounded(value)}°F" if value is not None else "-"


def _percent_label(value):
    return f"{_rounded(value)}%" if value is not None else "-"


def _range_label(values, suffix):
    if not values:
        return "-"
    low, high = round(min(values)), round(max(values))
    return f"{low}{suffix}" if low == high else f"{low}–{high}{suffix}"


def _parse_datetime(value):
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _utc_label(value):
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%MZ") if value else "-"


def _utc_now(value=None):
    value = value or datetime.now(timezone.utc)
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
