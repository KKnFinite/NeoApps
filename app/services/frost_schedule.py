"""Two FROST cycles from canonical Sort windows and gateway weekdays."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from app.extensions import db
from app.models import GatewaySortMatrix, SortTimelineSortSetting, SortTimelineSettings
from app.services.gateway_matrix import gateway_timezone

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
            SortTimelineSettings.gateway_id == gateway.id, GatewaySortMatrix.is_active.is_(True),
            SortTimelineSortSetting.sort_window_start_local.is_not(None),
            SortTimelineSortSetting.sort_window_end_local.is_not(None))).all()
        return cls(zone, tuple(rows))

    def windows(self, day):
        for weekday, start, end in self.rows:
            if day.strftime('%A').lower() != weekday:
                continue
            first = datetime.combine(day, start, self.zone)
            last = datetime.combine(day, end, self.zone)
            if end <= start:
                last += timedelta(days=1)
            yield first.astimezone(UTC), last.astimezone(UTC)

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
        values={}
        for start,end in self.windows(day):
            if end<=start:continue
            middle=start+(end-start)/2
            for due,following in ((start,middle),(middle,end)):
                cycle=Cycle(due,min(due+timedelta(hours=1),following),start,end)
                previous=values.get(due)
                values[due]=Cycle(due,max(previous.deadline,cycle.deadline),
                    min(previous.sort_start,start),max(previous.sort_end,end)) if previous else cycle
        return tuple(values[key] for key in sorted(values))

    def sort_cycles(self, at):
        if not self.zone or not self.rows:return ()
        day=at.astimezone(self.zone).date()
        return tuple(cycle for date in (day-timedelta(days=1),day)
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
