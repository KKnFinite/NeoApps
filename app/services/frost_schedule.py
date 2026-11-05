"""Read-only FROST hours from canonical Sort windows and gateway weekdays."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from app.extensions import db
from app.models import GatewaySortMatrix, SortTimelineSortSetting, SortTimelineSettings
from app.services.gateway_matrix import gateway_timezone

UTC = timezone.utc


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
        """Next hourly UTC slot or configured opening; no day/time constants."""
        if not self.zone or not self.rows:
            return None
        day = at.astimezone(self.zone).date()
        next_hour = at.astimezone(UTC).replace(minute=0, second=0, microsecond=0)+timedelta(hours=1)
        candidates = []
        for offset in range(-1, 8):
            for start, end in self.windows(day+timedelta(days=offset)):
                candidate = max(start, next_hour) if start <= at else start
                if at < candidate < end:
                    candidates.append(candidate)
        return min(candidates) if candidates else None

    def missed(self, prior, current):
        """Count eligible UTC slots, never report closed days as missing captures."""
        first = prior.astimezone(self.zone).date()-timedelta(days=1)
        last = current.astimezone(self.zone).date()
        day = first
        intervals = set()
        while day <= last:
            for start, end in self.windows(day):
                hour = max(prior+timedelta(hours=1), start)
                if hour.minute or hour.second or hour.microsecond:
                    hour = hour.replace(minute=0, second=0, microsecond=0)+timedelta(hours=1)
                while hour < min(end, current):
                    intervals.add(hour)
                    hour += timedelta(hours=1)
            day += timedelta(days=1)
        return len(intervals)
