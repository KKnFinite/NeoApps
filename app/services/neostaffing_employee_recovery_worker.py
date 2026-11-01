"""Restart-safe expiry sweep; no request polling or database startup dependency."""
from datetime import timedelta
from threading import Event, Thread
from sqlalchemy import func, select
from flask import g
from app.extensions import db

def recovery_cycle():
    """Return a UTC deadline, with no empty purge transaction or idle commit."""
    from app.models.staffing_employee_recovery import StaffingEmployeeRecovery as Recovery
    from app.services.neostaffing_employee_recovery import purge_expired, now_utc
    from app.services.background_deadlines import safety_deadline
    def earliest():
        return db.session.scalar(select(func.min(Recovery.expires_at)).where(
            Recovery.state.in_(('deleted', 'restored'))))
    at = now_utc()
    deadline = earliest()
    count = 0
    if deadline is not None and deadline <= at:
        count = purge_expired(isolated=True)
        if count:
            db.session.commit()
        else:
            db.session.rollback()
        deadline = earliest()
    if deadline is not None and deadline <= at:
        return at + timedelta(seconds=.1 if count == 25 else 30)
    safety = safety_deadline(at)
    return min(safety, deadline) if deadline else safety


def start_recovery_worker(app):
    if app.testing or 'sqlalchemy' not in app.extensions or app.extensions.get('employee_recovery_worker'):
        return
    wake=Event()
    app.extensions['employee_recovery_worker']=wake
    from app.services.background_deadlines import install_commit_signals
    install_commit_signals()

    def run():
        # Let the web worker start without opening a database connection.
        delay=10
        deadline=None
        observed=None
        while True:
            notified=wake.wait(min(delay,60));wake.clear()
            from app.services.background_deadlines import repair_failed_timer
            repair_failed_timer(app)  # Pure memory; no query on idle clock wakes.
            from app.services.neostaffing_employee_recovery import now_utc
            # Cheap wall-clock checks do not open a session/connection. They
            # detect clock jumps without minute-level PostgreSQL activity.
            at=now_utc()
            if observed is not None and at<observed:
                deadline=None
            observed=at
            if deadline is not None and at<deadline and not notified:
                delay=(deadline-now_utc()).total_seconds()
                continue
            try:
                with app.app_context():
                    g.background_deadline_work = True
                    try:
                        # Bounded transactions and SKIP LOCKED permit several web
                        # workers without duplicate purge or blocking a restore.
                        deadline=recovery_cycle()
                        delay=max(.1,(deadline-now_utc()).total_seconds())
                        app.logger.info('Employee recovery deadline scheduled: next_utc=%s',deadline.isoformat()+'Z')
                    except Exception as error:
                        try:
                            db.session.rollback()
                        except Exception as rollback_error:
                            app.logger.warning('Employee recovery rollback deferred: %s',type(rollback_error).__name__)
                        # No SQL, profile data, file keys, signatures or secrets.
                        app.logger.error('Employee recovery purge failed: %s; retrying',type(error).__name__)
                        delay=30
                        deadline=now_utc()+timedelta(seconds=delay)
                    finally:
                        try:
                            db.session.remove()
                        except Exception as cleanup_error:
                            app.logger.warning('Employee recovery session cleanup deferred: %s',type(cleanup_error).__name__)
            except Exception as context_error:
                # A database outage can also fail Flask/SQLAlchemy teardown.
                # Keep the same worker alive rather than losing expiry forever.
                app.logger.warning('Employee recovery context deferred: %s',type(context_error).__name__)
                delay=30
                deadline=now_utc()+timedelta(seconds=delay)
    Thread(target=run,daemon=True,name='employee-recovery-expiry').start()


def notify_recovery_worker(app):
    wake=app.extensions.get('employee_recovery_worker')
    if wake: wake.set()
