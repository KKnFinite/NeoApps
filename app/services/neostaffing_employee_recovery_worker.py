"""Restart-safe expiry sweep; no request polling or database startup dependency."""
from threading import Event, Thread
from sqlalchemy import func, select
from app.extensions import db


def start_recovery_worker(app):
    if app.testing or 'sqlalchemy' not in app.extensions or app.extensions.get('employee_recovery_worker'):
        return
    wake=Event()
    app.extensions['employee_recovery_worker']=wake

    def run():
        # Let the web worker start without opening a database connection.
        delay=10
        while True:
            wake.wait(delay);wake.clear()
            with app.app_context():
                try:
                    from app.models.staffing_employee_recovery import StaffingEmployeeRecovery as Recovery
                    from app.services.neostaffing_employee_recovery import purge_expired,now_utc
                    # Bounded transactions and SKIP LOCKED permit several web
                    # workers without duplicate purge or blocking a restore.
                    count=purge_expired(isolated=True);db.session.commit()
                    deadline=db.session.scalar(select(func.min(Recovery.expires_at)).where(
                        Recovery.state.in_(('deleted','restored'))))
                    remaining=(deadline-now_utc()).total_seconds() if deadline else None
                    delay=.1 if count==25 else min(60,max(.1,remaining)) if remaining is not None and remaining>0 else 30 if deadline else 60
                except Exception as error:
                    db.session.rollback()
                    # No SQL, profile data, file keys, signatures or secrets.
                    app.logger.error('Employee recovery purge failed: %s; retrying',type(error).__name__)
                    delay=30
                finally:
                    db.session.remove()
    Thread(target=run,daemon=True,name='employee-recovery-expiry').start()


def notify_recovery_worker(app):
    wake=app.extensions.get('employee_recovery_worker')
    if wake: wake.set()
