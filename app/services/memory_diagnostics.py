"""Low-overhead, opt-in process-memory observations for production triage.

The helper deliberately uses Linux procfs rather than adding a profiler or a
runtime dependency.  It is disabled by default and rate-limits each labelled
operation in every worker, so enabling it on Render gives useful RSS/high-water
signals without turning ordinary request logging into a memory stream.
"""

from __future__ import annotations

import os
import json
import sys
import threading
import time
from pathlib import Path
from contextlib import contextmanager
from functools import wraps

from flask import current_app, has_app_context


_SAMPLE_LOCK = threading.Lock()
_LAST_SAMPLE_AT: dict[str, float] = {}


def memory_diagnostics(operation):
    """Decorate a bounded operation with an optional memory observation."""

    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            with observe_process_memory(operation):
                return function(*args, **kwargs)

        return wrapped

    return decorate


@contextmanager
def observe_process_memory(operation, *, app=None):
    """Log RSS before/after one sampled operation when diagnostics are enabled."""
    resolved_app = app or (current_app if has_app_context() else None)
    if not _should_sample(operation, resolved_app):
        yield
        return

    started = time.monotonic()
    before = process_memory_snapshot()
    error_name = None
    try:
        yield
    except Exception as error:
        error_name = type(error).__name__
        raise
    finally:
        _log_sample(
            resolved_app,
            operation,
            before=before,
            after=process_memory_snapshot(),
            elapsed_seconds=time.monotonic() - started,
            error_name=error_name,
        )


def record_process_memory_checkpoint(operation, *, app=None):
    """Optionally record a single process-level checkpoint, such as startup."""
    resolved_app = app or (current_app if has_app_context() else None)
    if not _should_sample(operation, resolved_app):
        return False

    snapshot = process_memory_snapshot()
    _log_sample(
        resolved_app,
        operation,
        before=snapshot,
        after=snapshot,
        elapsed_seconds=None,
        error_name=None,
    )
    return True


def process_memory_snapshot():
    """Return best-effort Linux process RSS and high-water usage in bytes."""
    return {
        "rss_bytes": _linux_rss_bytes(),
        "high_water_bytes": _linux_high_water_bytes(),
        "pid": os.getpid(),
        "python": sys.version.split()[0],
        "container": container_memory_snapshot(),
        "heavy_modules": [name for name in ('numpy', 'pandas', 'ortools.sat.python.cp_model', 'boto3', 'sklearn') if name in sys.modules],
    }


def container_memory_snapshot(root='/sys/fs/cgroup', proc='/proc'):
    """Bounded read-only counters. RSS sums are NOT unique/container memory.

    File cache is charged to the cgroup but is not a Python-object leak. PSS
    apportions shared pages; neither quantity replaces the existing hard guard.
    No command lines, environments, credentials or heap contents are inspected.
    """
    result = {}
    try:
        result['usage_bytes'] = int((Path(root)/'memory.current').read_text())
        result['limit_bytes'] = int((Path(root)/'memory.max').read_text())
        stat = dict(line.split() for line in (Path(root)/'memory.stat').read_text().splitlines())
        result['components_bytes'] = {key:int(stat[key]) for key in ('anon','file','kernel','sock','slab') if key in stat}
    except (OSError, ValueError):
        pass
    processes = []
    try:
        for index, entry in enumerate(Path(proc).iterdir()):
            if index >= 512:break
            if not entry.name.isdigit():continue
            if len(processes)>=32:break
            try:
                status = dict(line.split(':',1) for line in (entry/'status').read_text().splitlines() if ':' in line)
                row = dict(pid=int(entry.name),ppid=int(status['PPid'].strip()),
                    rss_bytes=int(status.get('VmRSS','0 kB').split()[0])*1024)
                try:
                    rollup=dict(line.split(':',1) for line in (entry/'smaps_rollup').read_text().splitlines() if ':' in line)
                    row['pss_bytes']=int(rollup['Pss'].split()[0])*1024
                except (OSError,KeyError,ValueError):pass
                processes.append(row)
            except (OSError, KeyError, ValueError):continue
        result['processes']=processes
    except OSError:pass
    return result


def clear_memory_diagnostic_samples():
    """Clear process-local rate-limit state; used only by focused tests."""
    with _SAMPLE_LOCK:
        _LAST_SAMPLE_AT.clear()


def _should_sample(operation, app):
    if app is None or not app.config.get("NEOAPPS_MEMORY_DIAGNOSTICS_ENABLED"):
        return False

    try:
        sample_seconds = max(
            0.0,
            float(app.config.get("NEOAPPS_MEMORY_DIAGNOSTICS_SAMPLE_SECONDS", 300)),
        )
    except (TypeError, ValueError):
        sample_seconds = 300.0

    now = time.monotonic()
    with _SAMPLE_LOCK:
        previous = _LAST_SAMPLE_AT.get(operation)
        if previous is not None and now - previous < sample_seconds:
            return False
        _LAST_SAMPLE_AT[operation] = now
    return True


def _log_sample(app, operation, *, before, after, elapsed_seconds, error_name):
    if app is None:
        return

    before_rss = before.get("rss_bytes")
    after_rss = after.get("rss_bytes")
    delta = (
        after_rss - before_rss
        if isinstance(before_rss, int) and isinstance(after_rss, int)
        else None
    )
    _emit_diagnostic_line(
        "INFO NeoApps memory diagnostic "
        f"operation={operation} pid={after.get('pid')} python={after.get('python')} "
        f"rss_before_bytes={before_rss} rss_after_bytes={after_rss} "
        f"rss_delta_bytes={delta} high_water_bytes={after.get('high_water_bytes')} "
        f"elapsed_seconds={round(elapsed_seconds, 3) if elapsed_seconds is not None else None} "
        f"error={error_name}"
        f" container={json.dumps(after.get('container',{}),separators=(',',':'))}"
        f" heavy_modules={','.join(after.get('heavy_modules',[]))}"
    )


def _emit_diagnostic_line(message):
    """Write opt-in diagnostics directly to the process stream Render captures."""
    print(message, file=sys.stderr, flush=True)


def _linux_rss_bytes():
    try:
        with open("/proc/self/statm", encoding="utf-8") as statm:
            values = statm.read().split()
        return int(values[1]) * os.sysconf("SC_PAGE_SIZE")
    except (FileNotFoundError, IndexError, OSError, ValueError):
        return None


def _linux_high_water_bytes():
    try:
        with open("/proc/self/status", encoding="utf-8") as status:
            for line in status:
                if line.startswith("VmHWM:"):
                    return int(line.split()[1]) * 1024
    except (FileNotFoundError, IndexError, OSError, ValueError):
        return None
    return None
