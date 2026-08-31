"""Offline historical frost-training ingestion for NeoSubZero.

This module intentionally has no Flask, database, or network dependency. It
normalizes exported Cryotech application rows and historical KRFD observations
into compact records that a later training task can consume.
"""

import csv
import io
import math
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo


RFD_TIMEZONE = "America/Chicago"
NORMAL_NIGHT_WEEKDAYS = frozenset({0, 1, 2, 3})  # Monday through Thursday.
DEFAULT_NEGATIVE_WINDOW_START = time(2, 0)
DEFAULT_NEGATIVE_WINDOW_END = time(4, 0)
class NeoSubZeroFrostHistoryError(ValueError):
    """A safe historical-import validation error."""


@dataclass(frozen=True)
class CryotechApplicationRow:
    source_name: str
    source_row: int
    application_id: str | None
    application_date: date
    start_at_local: datetime
    end_at_local: datetime | None
    operational_night: date
    tail_number: str | None
    truck_number: str | None
    fluid_type: str | None
    surface_area: str | None
    reason_for_application: str | None
    active_precipitation: str | None
    gallons: float | None
    concentration_percent: float | None
    notes: str | None
    outcome: str


@dataclass(frozen=True)
class CryotechImportIssue:
    source_row: int
    message: str


@dataclass(frozen=True)
class CryotechImportResult:
    rows: tuple[CryotechApplicationRow, ...]
    issues: tuple[CryotechImportIssue, ...]
    mapped_columns: dict[str, str]


@dataclass(frozen=True)
class HistoricalWeatherObservation:
    station: str
    observed_at: datetime
    temperature_f: float | None = None
    dewpoint_f: float | None = None
    relative_humidity: float | None = None
    wind_speed_kt: float | None = None
    wind_gust_kt: float | None = None
    wind_direction_degrees: float | None = None
    sky_condition: str | None = None
    visibility_sm: float | None = None
    reported_weather: str | None = None
    source_name: str = "historical_weather"
    source_row: int | None = None

    @property
    def dewpoint_spread_f(self):
        if self.temperature_f is None or self.dewpoint_f is None:
            return None
        return self.temperature_f - self.dewpoint_f


class HistoricalWeatherProvider(Protocol):
    """Offline provider contract for later ASOS/IEM adapters."""

    def observations(self, start_at, end_at):
        """Return normalized observations in the requested aware interval."""


@dataclass(frozen=True)
class FrostTrainingRecord:
    operational_night: date
    exposure_timestamp_local: datetime
    exposure_window_start_local: datetime
    exposure_window_end_local: datetime
    frost_label: str
    outcome: str
    tail_number: str | None = None
    truck_number: str | None = None
    fluid_type: str | None = None
    surface_area: str | None = None
    reason_for_application: str | None = None
    active_precipitation: str | None = None
    gallons: float | None = None
    concentration_percent: float | None = None
    application_start_local: datetime | None = None
    application_end_local: datetime | None = None
    notes: str | None = None
    weather_observed_at: datetime | None = None
    temperature_f: float | None = None
    dewpoint_f: float | None = None
    dewpoint_spread_f: float | None = None
    relative_humidity: float | None = None
    wind_speed_kt: float | None = None
    wind_gust_kt: float | None = None
    wind_direction_degrees: float | None = None
    sky_condition: str | None = None
    visibility_sm: float | None = None
    reported_weather: str | None = None
    temperature_change_3h_f: float | None = None
    dewpoint_spread_change_3h_f: float | None = None
    relative_humidity_change_3h: float | None = None
    cryotech_source: str | None = None
    cryotech_source_row: int | None = None
    weather_source: str | None = None
    weather_source_row: int | None = None

    def to_dict(self):
        payload = asdict(self)
        for key, value in tuple(payload.items()):
            if isinstance(value, (date, datetime)):
                payload[key] = value.isoformat()
        return payload


_CRYOTECH_ALIASES = {
    "application_id": (
        "application id", "event id", "record id", "transaction id", "application number",
    ),
    "application_date": (
        "application date", "treatment date", "service date", "date",
    ),
    "start_time": (
        "start time", "application start", "fluid start time", "start", "start datetime",
    ),
    "end_time": (
        "end time", "application end", "fluid end time", "end", "end datetime",
    ),
    "tail_number": (
        "tail number", "tail", "aircraft tail", "registration", "aircraft",
    ),
    "truck_number": (
        "truck number", "truck", "vehicle number", "vehicle", "unit number",
    ),
    "fluid_type": (
        "fluid type", "application type", "product type", "fluid", "type",
    ),
    "surface_area": (
        "surface area", "surface", "application area", "treated area",
    ),
    "reason_for_application": (
        "reason for application", "application reason", "reason",
    ),
    "active_precipitation": (
        "active precipitation", "precipitation", "weather", "precip",
    ),
    "gallons": (
        "gallons", "gallons applied", "quantity gallons", "volume", "quantity",
    ),
    "concentration_percent": (
        "concentration percent", "concentration %", "concentration", "mixture percent",
    ),
    "notes": (
        "notes", "comments", "remarks", "comment",
    ),
}

_WEATHER_ALIASES = {
    "observed_at": (
        "valid", "observation time", "observed at", "timestamp", "datetime", "date time",
    ),
    "station": ("station", "station id", "icao", "airport"),
    "temperature_f": ("tmpf", "temperature f", "temp f", "temperature"),
    "dewpoint_f": ("dwpf", "dew point f", "dewpoint f", "dew point", "dewpoint"),
    "relative_humidity": ("relh", "relative humidity", "humidity", "rh"),
    "wind_speed_kt": ("sknt", "wind speed kt", "wind speed", "wind knots"),
    "wind_gust_kt": ("gust", "wind gust kt", "wind gust", "gust kt"),
    "wind_direction_degrees": ("drct", "wind direction", "wind direction degrees"),
    "visibility_sm": ("vsby", "visibility", "visibility sm"),
    "reported_weather": ("wxcodes", "present weather", "reported weather", "weather"),
}


def parse_cryotech_csv(source, *, source_name=None, timezone_name=RFD_TIMEZONE):
    """Normalize a Cryotech CSV without persisting its raw contents."""
    text, resolved_name = _read_text_source(source, source_name)
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise NeoSubZeroFrostHistoryError("Cryotech CSV has no header row.")
    columns = _map_columns(reader.fieldnames, _CRYOTECH_ALIASES)
    if "application_date" not in columns or "start_time" not in columns:
        raise NeoSubZeroFrostHistoryError(
            "Cryotech CSV requires an application date and start time column."
        )
    zone = ZoneInfo(timezone_name)
    rows = []
    issues = []
    for source_row, raw in enumerate(reader, start=2):
        if not any(str(value or "").strip() for value in raw.values()):
            continue
        try:
            application_date = _parse_date(_value(raw, columns, "application_date"))
            start_at = _parse_local_datetime(
                application_date,
                _value(raw, columns, "start_time"),
                zone,
            )
            end_at = _optional_local_datetime(
                application_date,
                _value(raw, columns, "end_time"),
                zone,
            )
            if end_at is not None and end_at < start_at:
                end_at += timedelta(days=1)
            reason = _clean_text(_value(raw, columns, "reason_for_application"))
            outcome = _application_outcome(reason)
            rows.append(
                CryotechApplicationRow(
                    source_name=resolved_name,
                    source_row=source_row,
                    application_id=_clean_text(_value(raw, columns, "application_id")),
                    application_date=application_date,
                    start_at_local=start_at,
                    end_at_local=end_at,
                    operational_night=operational_night_for_timestamp(start_at),
                    tail_number=_normalize_tail(_value(raw, columns, "tail_number")),
                    truck_number=_normalize_identifier(_value(raw, columns, "truck_number")),
                    fluid_type=_normalize_fluid(_value(raw, columns, "fluid_type")),
                    surface_area=_clean_text(_value(raw, columns, "surface_area")),
                    reason_for_application=_normalize_reason(reason),
                    active_precipitation=_clean_text(
                        _value(raw, columns, "active_precipitation")
                    ),
                    gallons=_optional_number(_value(raw, columns, "gallons")),
                    concentration_percent=_optional_number(
                        _value(raw, columns, "concentration_percent"),
                        strip_percent=True,
                    ),
                    notes=_clean_text(_value(raw, columns, "notes")),
                    outcome=outcome,
                )
            )
        except NeoSubZeroFrostHistoryError as exc:
            issues.append(CryotechImportIssue(source_row=source_row, message=str(exc)))
    return CryotechImportResult(
        rows=tuple(rows),
        issues=tuple(issues),
        mapped_columns=columns,
    )


class CsvHistoricalWeatherProvider:
    """Offline ASOS/IEM-style CSV adapter implementing the provider contract."""

    def __init__(
        self,
        source,
        *,
        source_name=None,
        timestamp_timezone="UTC",
        default_station="KRFD",
    ):
        text, resolved_name = _read_text_source(source, source_name)
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            raise NeoSubZeroFrostHistoryError("Historical weather CSV has no header row.")
        columns = _map_columns(reader.fieldnames, _WEATHER_ALIASES)
        if "observed_at" not in columns:
            raise NeoSubZeroFrostHistoryError(
                "Historical weather CSV requires an observation timestamp column."
            )
        source_zone = ZoneInfo(timestamp_timezone)
        station_filter = _normalize_identifier(default_station)
        observations = []
        for source_row, raw in enumerate(reader, start=2):
            if not any(str(value or "").strip() for value in raw.values()):
                continue
            observed_at = _parse_source_datetime(
                _value(raw, columns, "observed_at"), source_zone
            )
            temperature = _optional_number(_value(raw, columns, "temperature_f"))
            dewpoint = _optional_number(_value(raw, columns, "dewpoint_f"))
            humidity = _optional_number(_value(raw, columns, "relative_humidity"))
            if humidity is None:
                humidity = _relative_humidity_f(temperature, dewpoint)
            sky = _sky_from_weather_row(raw)
            station = _normalize_identifier(
                _value(raw, columns, "station")
            ) or station_filter
            if station_filter and station != station_filter:
                continue
            observations.append(
                HistoricalWeatherObservation(
                    station=station,
                    observed_at=observed_at,
                    temperature_f=temperature,
                    dewpoint_f=dewpoint,
                    relative_humidity=humidity,
                    wind_speed_kt=_optional_number(
                        _value(raw, columns, "wind_speed_kt")
                    ),
                    wind_gust_kt=_optional_number(
                        _value(raw, columns, "wind_gust_kt")
                    ),
                    wind_direction_degrees=_optional_number(
                        _value(raw, columns, "wind_direction_degrees")
                    ),
                    sky_condition=sky,
                    visibility_sm=_optional_number(
                        _value(raw, columns, "visibility_sm")
                    ),
                    reported_weather=_clean_text(
                        _value(raw, columns, "reported_weather")
                    ),
                    source_name=resolved_name,
                    source_row=source_row,
                )
            )
        self._observations = tuple(sorted(observations, key=lambda row: row.observed_at))

    def observations(self, start_at, end_at):
        return tuple(
            row
            for row in self._observations
            if start_at <= row.observed_at < end_at
        )


def normal_operational_nights(start_date, end_date):
    """Return Monday-through-Thursday operational-night dates inclusively."""
    start_date = _coerce_date(start_date)
    end_date = _coerce_date(end_date)
    if end_date < start_date:
        raise NeoSubZeroFrostHistoryError("End date must not precede start date.")
    return tuple(
        day
        for offset in range((end_date - start_date).days + 1)
        if (day := start_date + timedelta(days=offset)).weekday()
        in NORMAL_NIGHT_WEEKDAYS
    )


def operational_night_for_timestamp(timestamp):
    """Map post-midnight activity to the preceding operational night."""
    local = timestamp
    return local.date() - timedelta(days=1) if local.time() < time(12, 0) else local.date()


def default_negative_exposure_window(operational_night, *, timezone_name=RFD_TIMEZONE):
    """Return the 0200-0400 local exposure window following one night date."""
    operational_night = _coerce_date(operational_night)
    zone = ZoneInfo(timezone_name)
    window_date = operational_night + timedelta(days=1)
    return (
        datetime.combine(window_date, DEFAULT_NEGATIVE_WINDOW_START, tzinfo=zone),
        datetime.combine(window_date, DEFAULT_NEGATIVE_WINDOW_END, tzinfo=zone),
    )


def build_frost_training_dataset(
    cryotech_rows,
    weather_provider,
    *,
    start_date,
    end_date,
    departure_exposure_nights=(),
    timezone_name=RFD_TIMEZONE,
):
    """Build positive, evidence-backed negative, and explicit unlabeled records.

    ``departure_exposure_nights`` must come from an operational flight/exposure
    source. An event-free night absent from that set is never forced negative.
    """
    zone = ZoneInfo(timezone_name)
    start_date = _coerce_date(start_date)
    end_date = _coerce_date(end_date)
    exposure_nights = {_coerce_date(value) for value in departure_exposure_nights}
    rows = tuple(cryotech_rows or ())
    frost_nights = {row.operational_night for row in rows if row.outcome == "departure_frost"}
    records = []

    for row in rows:
        if not (start_date <= row.operational_night <= end_date):
            continue
        if row.outcome == "departure_frost":
            label = "positive"
        else:
            label = "unlabeled"
        records.append(
            _event_training_record(row, label, weather_provider, zone)
        )

    for night in normal_operational_nights(start_date, end_date):
        if night in frost_nights:
            continue
        window_start, window_end = default_negative_exposure_window(
            night, timezone_name=timezone_name
        )
        has_exposure = night in exposure_nights
        records.append(
            _window_training_record(
                night,
                window_start,
                window_end,
                "negative" if has_exposure else "unlabeled",
                "no_frost_exposure" if has_exposure else "no_exposure",
                weather_provider,
            )
        )
    records.sort(
        key=lambda row: (
            row.operational_night,
            row.exposure_timestamp_local,
            row.cryotech_source_row or 0,
        )
    )
    return tuple(records)


def _event_training_record(row, label, weather_provider, zone):
    window_start = row.start_at_local
    window_end = row.end_at_local or row.start_at_local
    features = _weather_features(weather_provider, row.start_at_local, zone)
    return FrostTrainingRecord(
        operational_night=row.operational_night,
        exposure_timestamp_local=row.start_at_local,
        exposure_window_start_local=window_start,
        exposure_window_end_local=window_end,
        frost_label=label,
        outcome=row.outcome,
        tail_number=row.tail_number,
        truck_number=row.truck_number,
        fluid_type=row.fluid_type,
        surface_area=row.surface_area,
        reason_for_application=row.reason_for_application,
        active_precipitation=row.active_precipitation,
        gallons=row.gallons,
        concentration_percent=row.concentration_percent,
        application_start_local=row.start_at_local,
        application_end_local=row.end_at_local,
        notes=row.notes,
        cryotech_source=row.source_name,
        cryotech_source_row=row.source_row,
        **features,
    )


def _window_training_record(
    night,
    window_start,
    window_end,
    label,
    outcome,
    weather_provider,
):
    anchor = window_start + (window_end - window_start) / 2
    features = _weather_features(
        weather_provider,
        anchor,
        window_start.tzinfo,
    )
    return FrostTrainingRecord(
        operational_night=night,
        exposure_timestamp_local=anchor,
        exposure_window_start_local=window_start,
        exposure_window_end_local=window_end,
        frost_label=label,
        outcome=outcome,
        **features,
    )


def _weather_features(provider, local_timestamp, zone):
    if provider is None:
        return {}
    target = local_timestamp.astimezone(timezone.utc)
    observations = provider.observations(
        target - timedelta(hours=5),
        target + timedelta(hours=2),
    )
    current = _nearest_observation(observations, target, timedelta(minutes=90))
    if current is None:
        return {}
    lookback_target = target - timedelta(hours=3)
    lookback = _nearest_observation(
        observations,
        lookback_target,
        timedelta(minutes=90),
    )
    return {
        "weather_observed_at": current.observed_at.astimezone(zone),
        "temperature_f": current.temperature_f,
        "dewpoint_f": current.dewpoint_f,
        "dewpoint_spread_f": current.dewpoint_spread_f,
        "relative_humidity": current.relative_humidity,
        "wind_speed_kt": current.wind_speed_kt,
        "wind_gust_kt": current.wind_gust_kt,
        "wind_direction_degrees": current.wind_direction_degrees,
        "sky_condition": current.sky_condition,
        "visibility_sm": current.visibility_sm,
        "reported_weather": current.reported_weather,
        "temperature_change_3h_f": _difference(
            current.temperature_f,
            getattr(lookback, "temperature_f", None),
        ),
        "dewpoint_spread_change_3h_f": _difference(
            current.dewpoint_spread_f,
            getattr(lookback, "dewpoint_spread_f", None),
        ),
        "relative_humidity_change_3h": _difference(
            current.relative_humidity,
            getattr(lookback, "relative_humidity", None),
        ),
        "weather_source": current.source_name,
        "weather_source_row": current.source_row,
    }


def _nearest_observation(observations, target, tolerance):
    candidates = [
        row for row in observations if abs(row.observed_at - target) <= tolerance
    ]
    return min(candidates, key=lambda row: abs(row.observed_at - target)) if candidates else None


def _difference(current, previous):
    return current - previous if current is not None and previous is not None else None


def _read_text_source(source, source_name):
    if hasattr(source, "read"):
        return source.read(), source_name or getattr(source, "name", "memory.csv")
    if isinstance(source, Path):
        return source.read_text(encoding="utf-8-sig"), source_name or source.name
    text = str(source)
    if "\n" not in text and "\r" not in text:
        path = Path(text)
        if path.exists():
            return path.read_text(encoding="utf-8-sig"), source_name or path.name
    return text.lstrip("\ufeff"), source_name or "memory.csv"


def _map_columns(fieldnames, aliases):
    normalized = {_normalize_header(name): name for name in fieldnames or ()}
    mapped = {}
    for key, names in aliases.items():
        for alias in (key.replace("_", " "), *names):
            actual = normalized.get(_normalize_header(alias))
            if actual is not None:
                mapped[key] = actual
                break
    return mapped


def _value(row, columns, key):
    column = columns.get(key)
    return row.get(column) if column else None


def _normalize_header(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _parse_date(value):
    text = str(value or "").strip()
    for pattern in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    raise NeoSubZeroFrostHistoryError(f"Invalid application date: {text or 'blank'}.")


def _parse_local_datetime(application_date, value, zone):
    text = str(value or "").strip()
    parsed = _try_datetime(text)
    if parsed is not None:
        return parsed.replace(tzinfo=zone) if parsed.tzinfo is None else parsed.astimezone(zone)
    parsed_time = _try_time(text)
    if parsed_time is None:
        raise NeoSubZeroFrostHistoryError(f"Invalid application start time: {text or 'blank'}.")
    return datetime.combine(application_date, parsed_time, tzinfo=zone)


def _optional_local_datetime(application_date, value, zone):
    return (
        _parse_local_datetime(application_date, value, zone)
        if str(value or "").strip()
        else None
    )


def _parse_source_datetime(value, source_zone):
    text = str(value or "").strip()
    parsed = _try_datetime(text)
    if parsed is None:
        raise NeoSubZeroFrostHistoryError(
            f"Invalid historical weather timestamp: {text or 'blank'}."
        )
    return parsed.replace(tzinfo=source_zone) if parsed.tzinfo is None else parsed


def _try_datetime(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.time() != time(0, 0) or "T" in str(value) or " " in str(value):
            return parsed
    except ValueError:
        pass
    for pattern in (
        "%m/%d/%Y %H:%M", "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %I:%M %p", "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S",
    ):
        try:
            return datetime.strptime(str(value), pattern)
        except ValueError:
            continue
    return None


def _try_time(value):
    for pattern in ("%H:%M", "%H:%M:%S", "%H%M", "%H%M%S", "%I:%M %p"):
        try:
            return datetime.strptime(str(value), pattern).time()
        except ValueError:
            continue
    return None


def _normalize_reason(value):
    text = _clean_text(value)
    if not text:
        return None
    if re.search(r"\bfrost\b", text, re.IGNORECASE):
        return "Frost"
    compact = re.sub(r"[^a-z0-9]+", "", text.casefold())
    if compact in {"pretreat", "pretreatment"}:
        return "Pretreat"
    return text


def _application_outcome(reason):
    normalized = _normalize_reason(reason)
    if normalized == "Frost":
        return "departure_frost"
    if normalized == "Pretreat":
        return "pretreat"
    return "other_spray"


def _normalize_fluid(value):
    text = _clean_text(value)
    if not text:
        return None
    compact = re.sub(r"[^a-z0-9]+", "", text.casefold())
    if compact in {"1", "i", "type1", "typei"}:
        return "Type I"
    if compact in {"4", "iv", "type4", "typeiv"}:
        return "Type IV"
    return text


def _normalize_tail(value):
    text = _normalize_identifier(value)
    return re.sub(r"\s+", "", text) if text else None


def _normalize_identifier(value):
    text = _clean_text(value)
    return text.upper() if text else None


def _clean_text(value):
    text = str(value or "").strip()
    return text or None


def _optional_number(value, *, strip_percent=False):
    text = str(value or "").strip().replace(",", "")
    if strip_percent:
        text = text.rstrip("%").strip()
    if not text or text.casefold() in {"m", "na", "n/a", "none", "null"}:
        return None
    try:
        return float(text)
    except ValueError as exc:
        raise NeoSubZeroFrostHistoryError(f"Invalid numeric value: {value}.") from exc


def _sky_from_weather_row(row):
    normalized = {_normalize_header(key): value for key, value in row.items()}
    values = []
    for key, value in normalized.items():
        if key.startswith(("skyc", "skycover", "cloud")):
            text = _clean_text(value)
            if text and text.upper() not in values:
                values.append(text.upper())
    return " ".join(values) or None


def _relative_humidity_f(temperature_f, dewpoint_f):
    if temperature_f is None or dewpoint_f is None:
        return None
    temperature_c = (temperature_f - 32) * 5 / 9
    dewpoint_c = (dewpoint_f - 32) * 5 / 9
    numerator = math.exp((17.625 * dewpoint_c) / (243.04 + dewpoint_c))
    denominator = math.exp((17.625 * temperature_c) / (243.04 + temperature_c))
    return max(0, min(100, 100 * numerator / denominator))


def _coerce_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return _parse_date(value)
