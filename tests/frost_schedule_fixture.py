"""Explicit database schedules for isolated hourly-capture contract tests."""
from datetime import time
from app.extensions import db
from app.models import GatewaySortMatrix, SortTimelineSettings, SortTimelineSortSetting


def configure(gateway, *, days=None, start=time(0), end=time(0)):
    days = days if days is not None else ('monday','tuesday','wednesday','thursday','friday','saturday','sunday')
    settings = SortTimelineSettings.query.filter_by(gateway_id=gateway.id).first()
    if settings is None:
        settings = SortTimelineSettings(gateway_id=gateway.id,gateway_code=gateway.code)
        db.session.add(settings);db.session.flush()
    row = SortTimelineSortSetting.query.filter_by(settings_id=settings.id,sort_name='night').first()
    if row is None:
        row = SortTimelineSortSetting(settings_id=settings.id,gateway_id=gateway.id,gateway_code=gateway.code,sort_name='night')
        db.session.add(row)
    row.sort_window_start_local, row.sort_window_end_local = start, end
    for day in ('monday','tuesday','wednesday','thursday','friday','saturday','sunday'):
        cell = GatewaySortMatrix.query.filter_by(gateway_id=gateway.id,day_of_week=day,sort_name='night').first()
        if cell is None:
            cell = GatewaySortMatrix(gateway_id=gateway.id,gateway_code=gateway.code,day_of_week=day,sort_name='night')
            db.session.add(cell)
        cell.is_active = day in days
    db.session.commit()
