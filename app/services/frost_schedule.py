"""Two FROST cycles from canonical Sort windows and gateway weekdays."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from app.extensions import db
from app.models import GatewaySortMatrix, SortTimelineSortSetting, SortTimelineSettings
from app.services.gateway_matrix import configured_sort_times_utc, gateway_timezone

UTC = timezone.utc


def cycle_identity(at):
    at=at.astimezone(UTC)
    fraction=f'.{at.microsecond:06d}' if at.microsecond else ''
    return at.strftime('%Y%m%dT%H%M%S')+fraction+'Z'


@dataclass(frozen=True)
class Cycle:
    start: datetime
    deadline: datetime
    sort_start: datetime
    sort_end: datetime
    windows: tuple = ()


def _coincident(cycles):
    by_start = {}
    for cycle in cycles:
        prior = by_start.get(cycle.start)
        if prior:
            windows = tuple(sorted(set(prior.windows + cycle.windows)))
            cycle = Cycle(cycle.start, max(prior.deadline, cycle.deadline),
                          min(prior.sort_start, cycle.sort_start), max(prior.sort_end, cycle.sort_end), windows)
        by_start[cycle.start] = cycle
    return tuple(by_start[key] for key in sorted(by_start))


def hourly_targets(cutoff, sort_end):
    """One prospective instant per intersecting UTC weather hour; close is exclusive."""
    hour = cutoff.replace(minute=0, second=0, microsecond=0)
    while hour < sort_end and cutoff < sort_end:
        yield max(cutoff, hour)
        hour += timedelta(hours=1)


class Schedule:
    def __init__(self, zone, rows):
        self.zone, self.rows = zone, rows

    @classmethod
    def load(cls, gateway):
        try:
            zone = ZoneInfo(gateway_timezone(gateway))
        except (ZoneInfoNotFoundError, ValueError):
            return cls(None, ())  # Missing/invalid configuration never enables collection.
        if not gateway or not gateway.is_active:
            return cls(zone, ())
        rows = db.session.execute(select(
            GatewaySortMatrix.day_of_week,
            SortTimelineSortSetting.sort_window_start_local,
            SortTimelineSortSetting.sort_window_end_local,
        ).join(SortTimelineSortSetting,
            (SortTimelineSortSetting.gateway_id == GatewaySortMatrix.gateway_id)
            & (SortTimelineSortSetting.sort_name == GatewaySortMatrix.sort_name)
        ).join(SortTimelineSettings,
            SortTimelineSettings.id == SortTimelineSortSetting.settings_id
        ).where(GatewaySortMatrix.gateway_id == gateway.id,
            SortTimelineSettings.gateway_id == gateway.id, SortTimelineSortSetting.gateway_code == gateway.code,
            GatewaySortMatrix.gateway_code == gateway.code, GatewaySortMatrix.is_active.is_(True),
            SortTimelineSortSetting.sort_window_start_local.is_not(None),
            SortTimelineSortSetting.sort_window_end_local.is_not(None))).all()
        return cls(zone, tuple(rows))

    def windows(self, day):
        for weekday, start, end in self.rows:
            if day.strftime('%A').lower() != weekday:
                continue
            first, last = configured_sort_times_utc(day, start, end, self.zone)
            if first is not None:
                yield first, last

    def eligible(self, at):
        if not self.zone or not self.rows:
            return False
        day = at.astimezone(self.zone).date()
        return any(start <= at < end for date in (day-timedelta(days=1), day)
            for start, end in self.windows(date))

    def next_capture(self, at):
        """Next opening/midpoint; midpoint uses actual elapsed time across DST."""
        if not self.zone or not self.rows:
            return None
        day = at.astimezone(self.zone).date()
        candidates = [cycle.start for offset in range(-1,8)
            for cycle in self.cycles(day+timedelta(days=offset)) if at<cycle.start]
        return min(candidates) if candidates else None

    def cycles(self, day):
        # Coincident cycles share the same weather/model computation. No
        # duplicate scoring simply because two configured windows overlap.
        cycles=[]
        for start,end in self.windows(day):
            if end<=start:continue
            middle=start+(end-start)/2
            for due,following in ((start,middle),(middle,end)):
                cycles.append(Cycle(due,min(due+timedelta(hours=1),following),start,end,((start,end),)))
        return _coincident(cycles)

    def sort_cycles(self, at):
        if not self.zone or not self.rows:return ()
        day=at.astimezone(self.zone).date()
        return _coincident(cycle for date in (day-timedelta(days=1),day)
            for cycle in self.cycles(date)
            if cycle.sort_start<=at<cycle.sort_end)

    def capture_cycles(self, at):
        return tuple(cycle for cycle in self.sort_cycles(at) if cycle.start<=at<cycle.deadline)

    def current_cycle(self, at):
        return max((cycle for cycle in self.sort_cycles(at) if cycle.start<=at),
            key=lambda cycle:cycle.start,default=None)

    def missed(self, prior, current):
        """Count scheduled cycles only, never intermediate hours/closed days."""
        if not self.zone or not self.rows:return 0
        first = prior.astimezone(self.zone).date()-timedelta(days=1)
        last = current.astimezone(self.zone).date()
        day = first
        intervals = set()
        while day <= last:
            intervals.update(cycle.start for cycle in self.cycles(day) if prior<cycle.start<current)
            day += timedelta(days=1)
        return len(intervals)
