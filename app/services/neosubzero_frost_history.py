"""Offline historical frost-training ingestion for NeoSubZero.

This module intentionally has no Flask, database, or network dependency. It
normalizes exported Cryotech application rows and historical KRFD observations
into compact records that a later training task can consume.
"""

import csv
import io
import math
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo


RFD_TIMEZONE = "America/Chicago"
NORMAL_NIGHT_WEEKDAYS = frozenset({0, 1, 2, 3})  # Monday through Thursday.
DEFAULT_NEGATIVE_WINDOW_START = time(2, 0)
DEFAULT_NEGATIVE_WINDOW_END = time(4, 0)
INFERRED_EVENT_MAX_START_SPREAD = timedelta(minutes=30)
CRYOTECH_REASON_DESCRIPTIONS = {
    "FG": ("Fog",),
    "FZFG": ("Freezing Fog",),
    "FZDZ": ("Freezing Drizzle",),
    "FZRA": ("Freezing Rain",),
    "GR": ("Hail",),
    "GS": ("Small Hail", "Snow Pellets"),
    "PL": ("Ice Pellets",),
    "IC": ("Ice Crystals",),
    "SG": ("Snow Grains",),
    "SN": ("Snow",),
    "DZ": ("Drizzle",),
    "CS": ("Cold Soak",),
    "F": ("Frost",),
    "P": ("Preventative De-Ice/Anti-Ice",),
}


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
    reason_code_raw: str | None
    reason_description_raw: str | None
    reason_description_normalized: str | None
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
class CryotechTreatmentEvent:
    """One aircraft treatment, retaining references to every source row."""

    event_key: str
    grouping_method: str
    application_id: str | None
    operational_night: date
    start_at_local: datetime
    end_at_local: datetime | None
    tail_number: str | None
    reason_codes_raw: tuple[str, ...]
    reason_descriptions_raw: tuple[str, ...]
    reason_descriptions_normalized: tuple[str, ...]
    reason_for_application: str | None
    outcome: str
    truck_numbers: tuple[str, ...]
    fluid_types: tuple[str, ...]
    surface_areas: tuple[str, ...]
    active_precipitation: tuple[str, ...]
    total_gallons: float | None
    raw_source_rows: tuple[int, ...]
    source_names: tuple[str, ...]

    @property
    def raw_application_count(self):
        return len(self.raw_source_rows)

    def to_dict(self):
        return _serialized_dataclass(self)


@dataclass(frozen=True)
class FrostNightEvidence:
    operational_night: date
    evidence_class: str
    confirmed_departure_exposure: bool
    number_departure_opportunities: int | None
    number_frost_treated_events: int
    frost_treated_percentage: float | None
    pretreat_occurred: bool
    number_pretreat_treated_events: int
    weak_frost_evidence: bool
    broader_frost_treatment: bool
    pretreat_and_frost: bool
    pretreat_before_frost: bool
    number_treatment_events: int
    number_raw_application_rows: int

    def to_dict(self):
        return _serialized_dataclass(self)


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
    evidence_class: str
    outcome: str
    event_key: str | None = None
    grouping_method: str | None = None
    raw_application_count: int = 0
    tail_number: str | None = None
    truck_number: str | None = None
    fluid_type: str | None = None
    surface_area: str | None = None
    reason_code: str | None = None
    reason_description: str | None = None
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
    number_departure_opportunities: int | None = None
    number_frost_treated_events: int = 0
    frost_treated_percentage: float | None = None
    pretreat_occurred: bool = False
    number_pretreat_treated_events: int = 0
    weak_frost_evidence: bool = False
    broader_frost_treatment: bool = False
    pretreat_and_frost: bool = False
    pretreat_before_frost: bool = False

    @property
    def frost_label(self):
        """Compatibility label for first-foundation consumers."""
        return {
            "confirmed_positive": "positive",
            "clean_negative": "negative",
        }.get(self.evidence_class, "unlabeled")

    def to_dict(self):
        payload = _serialized_dataclass(self)
        payload["frost_label"] = self.frost_label
        return payload


@dataclass(frozen=True)
class FrostHistoryDataset:
    raw_application_rows: tuple[CryotechApplicationRow, ...]
    treatment_events: tuple[CryotechTreatmentEvent, ...]
    night_evidence: tuple[FrostNightEvidence, ...]
    training_records: tuple[FrostTrainingRecord, ...]

    def to_dict(self):
        return {
            "schema_version": 3,
            "raw_application_rows": [
                _serialized_dataclass(row) for row in self.raw_application_rows
            ],
            "treatment_events": [row.to_dict() for row in self.treatment_events],
            "night_evidence": [row.to_dict() for row in self.night_evidence],
            "training_records": [row.to_dict() for row in self.training_records],
        }


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
    "reason_code": (
        "reason code", "application reason code", "reason abbreviation",
    ),
    "reason_description": (
        "reason description", "application reason description", "reason text",
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
            reason = _parse_cryotech_reason(
                _value(raw, columns, "reason_for_application"),
                _value(raw, columns, "reason_code"),
                _value(raw, columns, "reason_description"),
            )
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
                    reason_code_raw=reason["code_raw"],
                    reason_description_raw=reason["description_raw"],
                    reason_description_normalized=reason["description_normalized"],
                    reason_for_application=reason["canonical_reason"],
                    active_precipitation=_clean_text(
                        _value(raw, columns, "active_precipitation")
                    ),
                    gallons=_optional_number(_value(raw, columns, "gallons")),
                    concentration_percent=_optional_number(
                        _value(raw, columns, "concentration_percent"),
                        strip_percent=True,
                    ),
                    notes=_clean_text(_value(raw, columns, "notes")),
                    outcome=reason["outcome"],
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
    """Return reconstructed Monday-through-Thursday sort nights inclusively."""
    start_date = _coerce_date(start_date)
    end_date = _coerce_date(end_date)
    if end_date < start_date:
        raise NeoSubZeroFrostHistoryError("End date must not precede start date.")
    return tuple(
        day
        for offset in range((end_date - start_date).days + 1)
        if (day := start_date + timedelta(days=offset)).weekday()
        in NORMAL_NIGHT_WEEKDAYS
        and day not in historical_no_sort_dates(day.year)
    )


def historical_no_sort_dates(year):
    """Return the authoritative historical no-sort dates for one year."""
    year = int(year)
    thanksgiving = _nth_weekday_of_month(year, 11, 3, 4)
    dates = {
        _last_weekday_of_month(year, 5, 0),  # Memorial Day
        _nth_weekday_of_month(year, 9, 0, 1),  # Labor Day
        date(year, 7, 4),
        thanksgiving,
        thanksgiving - timedelta(days=1),
        date(year, 12, 24),
        date(year, 12, 25),
        date(year, 12, 31),
        date(year, 1, 1),
    }
    if year >= 2025:
        dates.add(_nth_weekday_of_month(year, 1, 0, 3))  # MLK Day
    return frozenset(dates)


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


def group_cryotech_treatment_events(
    cryotech_rows,
    *,
    inferred_max_start_spread=INFERRED_EVENT_MAX_START_SPREAD,
):
    """Collapse truck rows into aircraft events without discarding raw detail.

    Stable Cryotech application IDs take precedence. Without one, rows group
    only when operational night, tail, and normalized reason match and their
    start times remain inside one bounded window. Missing tails never infer a
    multi-row aircraft event.
    """
    rows = tuple(
        sorted(
            cryotech_rows or (),
            key=lambda row: (
                row.operational_night,
                row.start_at_local,
                row.source_row,
            ),
        )
    )
    stable_groups = {}
    inferred_groups = []
    for row in rows:
        if row.application_id:
            key = (
                row.operational_night,
                row.tail_number,
                _normalize_identifier(row.application_id),
            )
            stable_groups.setdefault(key, []).append(row)
            continue
        matched = None
        if row.tail_number:
            for group in reversed(inferred_groups):
                first = group[0]
                if first.operational_night != row.operational_night:
                    continue
                if first.tail_number != row.tail_number:
                    continue
                if _reason_group_key(first) != _reason_group_key(row):
                    continue
                if row.start_at_local - first.start_at_local <= inferred_max_start_spread:
                    matched = group
                    break
        if matched is None:
            inferred_groups.append([row])
        else:
            matched.append(row)

    events = []
    for stable_key, group in stable_groups.items():
        events.append(
            _treatment_event_from_rows(
                group,
                event_key=(
                    f"cryotech:{stable_key[0].isoformat()}:"
                    f"{stable_key[1] or 'UNKNOWN'}:{stable_key[2]}"
                ),
                grouping_method="application_id",
            )
        )
    for group in inferred_groups:
        first = group[0]
        events.append(
            _treatment_event_from_rows(
                group,
                event_key=(
                    f"inferred:{first.operational_night.isoformat()}:"
                    f"{first.tail_number or 'ROW-' + str(first.source_row)}:"
                    f"{first.outcome}:{first.start_at_local.isoformat()}"
                ),
                grouping_method="bounded_time" if len(group) > 1 else "single_row",
            )
        )
    return tuple(
        sorted(
            events,
            key=lambda row: (
                row.operational_night,
                row.start_at_local,
                row.event_key,
            ),
        )
    )


def build_frost_history_dataset(
    cryotech_rows,
    weather_provider,
    *,
    start_date,
    end_date,
    departure_exposure_nights=(),
    departure_opportunities_by_night=None,
    timezone_name=RFD_TIMEZONE,
):
    """Build raw, event, night-evidence, and training-record layers.

    ``departure_exposure_nights`` confirms relevant exposure but need not carry
    a count. ``departure_opportunities_by_night`` may provide exact counts for
    later percentage/weight analysis. Event-free nights lacking either form of
    exposure evidence remain unlabeled.
    """
    start_date = _coerce_date(start_date)
    end_date = _coerce_date(end_date)
    raw_rows = tuple(cryotech_rows or ())
    events = tuple(
        row
        for row in group_cryotech_treatment_events(raw_rows)
        if start_date <= row.operational_night <= end_date
    )
    exposure_nights, opportunity_counts = _departure_exposure_evidence(
        departure_exposure_nights,
        departure_opportunities_by_night,
    )
    reconstructed_exposure_nights = set(
        normal_operational_nights(start_date, end_date)
    )
    valid_manifest_nights = {
        night
        for night in exposure_nights
        if night not in historical_no_sort_dates(night.year)
    }
    valid_exposure_nights = reconstructed_exposure_nights | valid_manifest_nights
    event_nights = {row.operational_night for row in events}
    evidence_nights = valid_exposure_nights | event_nights
    events_by_night = {
        night: tuple(row for row in events if row.operational_night == night)
        for night in evidence_nights
    }
    night_evidence = tuple(
        _night_evidence(
            night,
            events_by_night[night],
            confirmed_departure_exposure=night in valid_exposure_nights,
            departure_opportunities=(
                opportunity_counts.get(night)
                if night in valid_exposure_nights
                else None
            ),
        )
        for night in sorted(evidence_nights)
    )
    evidence_by_night = {row.operational_night: row for row in night_evidence}
    records = []

    for event in events:
        records.append(
            _event_training_record(
                event,
                evidence_by_night[event.operational_night],
                weather_provider,
                ZoneInfo(timezone_name),
            )
        )

    for evidence in night_evidence:
        if evidence.number_frost_treated_events or evidence.pretreat_occurred:
            continue
        if evidence.operational_night not in valid_exposure_nights:
            continue
        window_start, window_end = default_negative_exposure_window(
            evidence.operational_night,
            timezone_name=timezone_name,
        )
        records.append(
            _window_training_record(
                window_start,
                window_end,
                evidence,
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
    return FrostHistoryDataset(
        raw_application_rows=raw_rows,
        treatment_events=events,
        night_evidence=night_evidence,
        training_records=tuple(records),
    )


def build_frost_training_dataset(*args, **kwargs):
    """Compatibility wrapper returning only the model-ready record layer."""
    return build_frost_history_dataset(*args, **kwargs).training_records


def _event_training_record(event, evidence, weather_provider, zone):
    window_start = event.start_at_local
    window_end = event.end_at_local or event.start_at_local
    features = _weather_features(weather_provider, event.start_at_local, zone)
    if event.outcome == "departure_frost":
        evidence_class = "confirmed_positive"
    elif event.outcome == "pretreat":
        evidence_class = "uncertain_pretreat"
    else:
        evidence_class = "unlabeled"
    return FrostTrainingRecord(
        operational_night=event.operational_night,
        exposure_timestamp_local=event.start_at_local,
        exposure_window_start_local=window_start,
        exposure_window_end_local=window_end,
        evidence_class=evidence_class,
        outcome=event.outcome,
        event_key=event.event_key,
        grouping_method=event.grouping_method,
        raw_application_count=event.raw_application_count,
        tail_number=event.tail_number,
        truck_number=" / ".join(event.truck_numbers) or None,
        fluid_type=" / ".join(event.fluid_types) or None,
        surface_area=" / ".join(event.surface_areas) or None,
        reason_code=" / ".join(event.reason_codes_raw) or None,
        reason_description=(
            " / ".join(event.reason_descriptions_normalized) or None
        ),
        reason_for_application=event.reason_for_application,
        active_precipitation=" / ".join(event.active_precipitation) or None,
        gallons=event.total_gallons,
        application_start_local=event.start_at_local,
        application_end_local=event.end_at_local,
        cryotech_source=" / ".join(event.source_names) or None,
        cryotech_source_row=min(event.raw_source_rows),
        **_night_evidence_fields(evidence),
        **features,
    )


def _window_training_record(window_start, window_end, evidence, weather_provider):
    anchor = window_start + (window_end - window_start) / 2
    features = _weather_features(
        weather_provider,
        anchor,
        window_start.tzinfo,
    )
    return FrostTrainingRecord(
        operational_night=evidence.operational_night,
        exposure_timestamp_local=anchor,
        exposure_window_start_local=window_start,
        exposure_window_end_local=window_end,
        evidence_class=evidence.evidence_class,
        outcome=(
            "no_frost_exposure"
            if evidence.evidence_class == "clean_negative"
            else "no_exposure"
        ),
        **_night_evidence_fields(evidence),
        **features,
    )


def _treatment_event_from_rows(rows, *, event_key, grouping_method):
    ordered = tuple(sorted(rows, key=lambda row: (row.start_at_local, row.source_row)))
    outcomes = {row.outcome for row in ordered}
    if "departure_frost" in outcomes:
        outcome = "departure_frost"
        reason = "Frost"
    elif "pretreat" in outcomes:
        outcome = "pretreat"
        reason = "Pretreat"
    else:
        outcome = ordered[0].outcome
        reason = ordered[0].reason_for_application
    end_values = tuple(row.end_at_local for row in ordered if row.end_at_local)
    gallon_values = tuple(row.gallons for row in ordered if row.gallons is not None)
    return CryotechTreatmentEvent(
        event_key=event_key,
        grouping_method=grouping_method,
        application_id=ordered[0].application_id,
        operational_night=ordered[0].operational_night,
        start_at_local=min(row.start_at_local for row in ordered),
        end_at_local=max(end_values) if end_values else None,
        tail_number=ordered[0].tail_number,
        reason_codes_raw=_unique_text(row.reason_code_raw for row in ordered),
        reason_descriptions_raw=_unique_text(
            row.reason_description_raw for row in ordered
        ),
        reason_descriptions_normalized=_unique_text(
            row.reason_description_normalized for row in ordered
        ),
        reason_for_application=reason,
        outcome=outcome,
        truck_numbers=_unique_text(row.truck_number for row in ordered),
        fluid_types=_unique_text(row.fluid_type for row in ordered),
        surface_areas=_unique_text(row.surface_area for row in ordered),
        active_precipitation=_unique_text(
            row.active_precipitation for row in ordered
        ),
        total_gallons=sum(gallon_values) if gallon_values else None,
        raw_source_rows=tuple(row.source_row for row in ordered),
        source_names=_unique_text(row.source_name for row in ordered),
    )


def _reason_group_key(row):
    return (
        row.outcome,
        str(row.reason_code_raw or "").strip().upper(),
        str(row.reason_description_normalized or "").strip().casefold(),
        str(row.reason_for_application or "").strip().casefold(),
    )


def _unique_text(values):
    result = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return tuple(result)


def _departure_exposure_evidence(exposure_nights, opportunities_by_night):
    counts = {}
    confirmed = set()
    if isinstance(exposure_nights, Mapping):
        opportunities_by_night = {
            **exposure_nights,
            **(opportunities_by_night or {}),
        }
    else:
        confirmed.update(_coerce_date(value) for value in exposure_nights or ())
    for night, count in (opportunities_by_night or {}).items():
        normalized_night = _coerce_date(night)
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise NeoSubZeroFrostHistoryError(
                "Departure opportunity counts must be non-negative whole numbers."
            )
        counts[normalized_night] = count
        if count > 0:
            confirmed.add(normalized_night)
    return confirmed, counts


def _night_evidence(
    night,
    events,
    *,
    confirmed_departure_exposure,
    departure_opportunities,
):
    frost_events = tuple(row for row in events if row.outcome == "departure_frost")
    pretreat_events = tuple(row for row in events if row.outcome == "pretreat")
    frost_count = len(frost_events)
    pretreat_count = len(pretreat_events)
    confirmed_exposure = confirmed_departure_exposure or bool(frost_events)
    if frost_count:
        evidence_class = "confirmed_positive"
    elif pretreat_count:
        evidence_class = "uncertain_pretreat"
    elif confirmed_exposure:
        evidence_class = "clean_negative"
    else:
        evidence_class = "unlabeled"
    percentage = None
    if departure_opportunities:
        percentage = round(100 * frost_count / departure_opportunities, 2)
    first_frost = min(
        (row.start_at_local for row in frost_events),
        default=None,
    )
    pretreat_before_frost = bool(
        first_frost
        and any(row.start_at_local < first_frost for row in pretreat_events)
    )
    return FrostNightEvidence(
        operational_night=night,
        evidence_class=evidence_class,
        confirmed_departure_exposure=confirmed_exposure,
        number_departure_opportunities=departure_opportunities,
        number_frost_treated_events=frost_count,
        frost_treated_percentage=percentage,
        pretreat_occurred=bool(pretreat_events),
        number_pretreat_treated_events=pretreat_count,
        weak_frost_evidence=frost_count in {1, 2} and not pretreat_events,
        broader_frost_treatment=frost_count >= 3,
        pretreat_and_frost=bool(pretreat_events and frost_events),
        pretreat_before_frost=pretreat_before_frost,
        number_treatment_events=len(events),
        number_raw_application_rows=sum(
            row.raw_application_count for row in events
        ),
    )


def _night_evidence_fields(evidence):
    return {
        "number_departure_opportunities": evidence.number_departure_opportunities,
        "number_frost_treated_events": evidence.number_frost_treated_events,
        "frost_treated_percentage": evidence.frost_treated_percentage,
        "pretreat_occurred": evidence.pretreat_occurred,
        "number_pretreat_treated_events": evidence.number_pretreat_treated_events,
        "weak_frost_evidence": evidence.weak_frost_evidence,
        "broader_frost_treatment": evidence.broader_frost_treatment,
        "pretreat_and_frost": evidence.pretreat_and_frost,
        "pretreat_before_frost": evidence.pretreat_before_frost,
    }


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


def _parse_cryotech_reason(combined_value, code_value, description_value):
    combined = _clean_text(combined_value)
    code_raw = _clean_text(code_value)
    description_raw = _clean_text(description_value)
    if not code_raw and combined:
        combined_code = combined.upper()
        if combined_code in CRYOTECH_REASON_DESCRIPTIONS:
            code_raw = combined
        else:
            combined_match = re.match(
                r"^([A-Za-z0-9]{1,4})\s*[-–—:]\s*(.+)$",
                combined,
            )
            if (
                combined_match
                and combined_match.group(1).upper()
                in CRYOTECH_REASON_DESCRIPTIONS
            ):
                code_raw = combined_match.group(1)
                if not description_raw:
                    description_raw = combined_match.group(2)
            elif not description_raw:
                description_raw = combined
    elif code_raw and combined and not description_raw:
        if combined.strip().upper() != code_raw.strip().upper():
            description_raw = combined

    code = _normalize_identifier(code_raw)
    description_normalized = _normalize_reason_description(
        code,
        description_raw,
    )
    if code == "F" or description_normalized == "Frost":
        canonical_reason = "Frost"
    elif code == "P" or _is_pretreat_description(description_normalized):
        canonical_reason = "Pretreat"
    else:
        canonical_reason = description_normalized or code
    return {
        "code_raw": code_raw,
        "description_raw": description_raw,
        "description_normalized": description_normalized,
        "canonical_reason": canonical_reason,
        "outcome": _application_outcome(canonical_reason),
    }


def _normalize_reason_description(code, value):
    text = _clean_text(value)
    if text:
        compact = _compact_reason_text(text)
        for descriptions in CRYOTECH_REASON_DESCRIPTIONS.values():
            for description in descriptions:
                if compact == _compact_reason_text(description):
                    return description
        if compact in {"pretreat", "pretreatment", "preventative"}:
            return "Preventative De-Ice/Anti-Ice"
        return re.sub(r"\s+", " ", text)
    descriptions = CRYOTECH_REASON_DESCRIPTIONS.get(code, ())
    return descriptions[0] if len(descriptions) == 1 else None


def _compact_reason_text(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _is_pretreat_description(value):
    compact = _compact_reason_text(value)
    return compact in {
        "pretreat",
        "pretreatment",
        "preventative",
        "preventativedeiceantiice",
    }


def _normalize_reason(value):
    text = _clean_text(value)
    if not text:
        return None
    if re.search(r"\bfrost\b", text, re.IGNORECASE):
        return "Frost"
    if _is_pretreat_description(text):
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


def _serialized_dataclass(value):
    return _serialize_value(asdict(value))


def _serialize_value(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _serialize_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize_value(item) for item in value]
    return value


def _nth_weekday_of_month(year, month, weekday, occurrence):
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (occurrence - 1))


def _last_weekday_of_month(year, month, weekday):
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    last = next_month - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _coerce_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return _parse_date(value)
