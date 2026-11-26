"""One bounded KRFD acquisition shared by workers on the Starter instance."""
from base64 import b64decode, b64encode
from datetime import datetime, timedelta, timezone
import json
from math import isfinite
import os
from pathlib import Path
import tempfile
import sys
from threading import Lock

from flask import current_app, has_app_context

KRFD = "KRFD"
METAR_URL = "https://aviationweather.gov/api/data/metar?ids=KRFD&format=json&hours=14"
MAX_BYTES = 128 * 1024
CADENCE = timedelta(minutes=5)
EXPIRY = timedelta(hours=6)
UTC = timezone.utc
_render_lock = Lock()
_last_rendered = None


def enabled():
    return (has_app_context() and current_app.config.get("NEOAPPS_SHARED_METAR_ENABLED", False)
            and (not current_app.testing or bool(current_app.config.get("NEOAPPS_METAR_CACHE_DIR"))))


def _directory():
    return Path(current_app.config.get("NEOAPPS_METAR_CACHE_DIR") or Path(tempfile.gettempdir()) / "neoapps-krfd-v1")


def _read():
    try:
        with (_directory() / "observation.json").open("rb") as file:
            value = json.loads(file.read(2 * MAX_BYTES + 1))
        if not isinstance(value, dict) or any(
            type(value.get(key, 0)) not in (int, float) or not isfinite(value.get(key, 0))
            for key in ("fetched_at", "retry_at", "failures")
        ):
            return {}
        return value
    except (OSError, ValueError):
        return {}


def schedule_revision():
    try:
        return (_directory() / "schedule-revision").stat().st_mtime_ns
    except FileNotFoundError:
        return 0
    except OSError:
        return None


def invalidate_schedule():
    directory = _directory()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    (directory / "schedule-revision").touch()


def _write(directory, state):
    with tempfile.NamedTemporaryFile(dir=directory, delete=False, mode="w", encoding="utf-8") as file:
        path = Path(file.name)
        try:
            json.dump(state, file, allow_nan=False, separators=(",", ":"))
            file.close()
            path.replace(directory / "observation.json")
        finally:
            path.unlink(missing_ok=True)


def _try_lock(file):
    # Closing the handle releases the OS lock, including after a worker dies.
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def observed_at(row):
    try:
        value = datetime.fromisoformat(str(row.get("reportTime", "")).replace("Z", "+00:00"))
        if value.tzinfo is not None:
            return value.astimezone(UTC)
    except (ValueError, OverflowError):
        pass
    try:
        return datetime.fromtimestamp(float(row["obsTime"]), UTC)
    except (TypeError, ValueError, KeyError, OverflowError, OSError):
        return None


def latest(now):
    state = _read()
    row = state.get("latest")
    if not isinstance(row, dict) or row.get("icaoId") != KRFD:
        return None
    observed = observed_at(row)
    fetched = state.get("fetched_at", 0)
    if observed is None or observed > now or not 0 <= now.timestamp() - fetched < EXPIRY.total_seconds():
        return None
    if now >= observed + EXPIRY:
        return None
    return dict(value=[row], fetched_at=fetched)


def source(now, *, max_age=CADENCE):
    state = _read()
    fetched = state.get("fetched_at")
    if fetched is None or not 0 <= now.timestamp() - fetched < max_age.total_seconds():
        raise ValueError("Scheduled KRFD observations are unavailable or stale.")
    return b64decode(state["raw"], validate=True), state["receipt"]


def next_deadline(schedule, now, *, not_before=None):
    if schedule is None or not schedule.zone or not schedule.rows:
        return None
    retry_at = _read().get("retry_at", 0)
    due = max(now, datetime.fromtimestamp(retry_at, UTC), not_before or now)
    local_day = now.astimezone(schedule.zone).date()
    candidates = [max(start, due) for offset in range(-1, 8)
                  for start, end in schedule.windows(local_day + timedelta(days=offset)) if max(start, due) < end]
    return min(candidates, default=None)


def note_raiden_render(observation, windows):
    global _last_rendered
    if not enabled() or not observation or observation["stale"]:
        return
    current = datetime.now(UTC).timestamp() * 1000
    if not any(start <= current < end for start, end in windows):
        return
    with _render_lock:
        if observation["observed_at"] == _last_rendered:
            return
        _last_rendered = observation["observed_at"]
    print(
        f'NeoRaiden served shared KRFD METAR: observed_utc={observation["observed_at"]} '
        f'temperature={observation["temperature"]} dewpoint={observation["dewpoint"]} '
        f'wind={observation["wind"]} visibility={observation["visibility"]}',
        file=sys.stderr, flush=True,
    )


def refresh(schedule, now, *, revision=None):
    """Only called by the archive deadline worker. Viewers never acquire."""
    from app.services.frost_auto import fetch_weather_source

    at = now()
    if not schedule.eligible(at):
        return
    directory = _directory()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (directory / "acquire.lock").open("a+b") as lock:
        if not _try_lock(lock):
            return
        state = _read()
        at = now()
        if (not schedule.eligible(at) or at.timestamp() < state.get("retry_at", 0)
                or revision is not None and schedule_revision() != revision):
            return
        # Retain the last good payload through a crash or interrupted attempt.
        state["retry_at"] = at.timestamp() + 60
        _write(directory, state)
        if not schedule.eligible(now()) or revision is not None and schedule_revision() != revision:
            return
        try:
            # A short operational observation must be available even while live
            # viewers keep the research-only foreground-priority gate closed.
            raw, receipt = fetch_weather_source(METAR_URL, MAX_BYTES)
            payload = json.loads(raw)
            if not isinstance(payload, list) or not payload or any(
                    not isinstance(row, dict) or row.get("icaoId") != KRFD for row in payload):
                raise ValueError("Invalid KRFD station payload.")
            finished = now()
            candidates = [(observed, row) for row in payload if (observed := observed_at(row)) is not None
                          and finished - EXPIRY < observed <= finished]
            observed, row = max(candidates, key=lambda item: item[0])
            prior = observed_at(state.get("latest", {}))
            if prior and observed < prior:
                raise ValueError("KRFD observation is older than the last good report.")
            state = dict(raw=b64encode(raw).decode("ascii"), receipt=receipt, latest=row,
                         fetched_at=finished.timestamp(), retry_at=(finished + CADENCE).timestamp())
            _write(directory, state)
            print(f"Shared KRFD METAR acquired: provider_requests=1 observed_utc={observed.isoformat()}",
                  file=sys.stderr, flush=True)
        except (OSError, ValueError, TypeError, KeyError) as error:
            failures = min(state.get("failures", 0) + 1, 5)
            state.update(failures=failures, retry_at=now().timestamp() + min(60 * 2 ** (failures - 1), 900))
            _write(directory, state)
            current_app.logger.warning("Shared KRFD METAR unavailable: provider_requests=1 error=%s",
                                       type(error).__name__)
