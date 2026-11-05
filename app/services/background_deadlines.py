"""Deadlines for the existing archive heartbeat, not a second queue or worker.

Receipts remain authoritative. Local commit hints only shorten the next scan;
ten-minute safety discovery recovers missed hints, other processes and restarts.
Sleeping timers hold no SQLAlchemy sessions or PostgreSQL LISTEN connections.
"""
from datetime import datetime, timedelta, timezone
from itertools import chain
from threading import Lock

from flask import current_app, g, has_app_context
from sqlalchemy import event, func, or_, select
from sqlalchemy.orm import Session

from app.extensions import db

SAFETY_SECONDS = 600
BUSY_SECONDS = 30
EXTENSION = 'archive_delivery_deadlines'


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def safety_deadline(at):
    """Coalesce both systems/processes onto UTC ten-minute boundaries.

    Independent relative delays can drift five minutes apart and accidentally
    keep a 300-second autosuspend timer awake despite each using 600 seconds.
    """
    return at.replace(minute=at.minute//10*10,second=0,microsecond=0)+timedelta(seconds=SAFETY_SECONDS)


def plan(at=None, *, safety=False, hinted=False, expedite=()):
    """Small receipt metadata only; never reconstruct evidence or load payloads."""
    from app.models import (Gateway, NeoScorpionSettings, NeoScorpionSpearObservation as Observation,
        NeoScorpionSpearFleetObservation as Fleet, NeoSubZeroArchiveDelivery as Delivery,
        NeoSubZeroArchiveCursor as Cursor, NeoSubZeroArchiveCheckpoint as Checkpoint,
        FrostDocument as Document, FrostHistoryTask as Task, SortDateOperation as Operation,
        NeoSubZeroAuditEntry as Audit, SortDateMission as Mission)
    from app.services import frost_auto as auto, frost_departures as departures
    at = at or utcnow()
    due, deadlines = {}, [safety_deadline(at)]
    next_frost_capture = None
    gateways = db.session.execute(select(Gateway.id, Gateway.code)).all()
    latest_audit = db.session.scalar(select(func.max(Audit.id))) or 0
    def add(gateway, kind, deadline):
        if deadline is None:
            return
        if deadline <= at:
            due.setdefault(gateway, set()).add(kind)
        else:
            deadlines.append(deadline)
    def minimum(column, *where):
        return db.session.scalar(select(func.min(column)).where(*where))
    for gateway_id, code in gateways:
        if code.upper() == 'RFD':
            from app.services.neostaffing_sheet_sync import next_deadline
            add(gateway_id, 'staffing_sheet', next_deadline(at))
        spear = db.session.scalar(select(NeoScorpionSettings.spear_learning_capture_enabled)
            .where(NeoScorpionSettings.gateway_id == gateway_id))
        if spear:
            from app.services.neoscorpion_learning_vault import learning_vault_status
            if learning_vault_status().configured:
                add(gateway_id, 'spear', minimum(Observation.retry_at,
                    Observation.gateway_id == gateway_id, Observation.delivered_at.is_(None)))
                add(gateway_id, 'spear', minimum(Fleet.retry_at,
                    Fleet.gateway_id == gateway_id, Fleet.delivered_at.is_(None)))
                add(gateway_id, 'spear', minimum(Observation.reconcile_after,
                    Observation.gateway_id == gateway_id, Observation.kind == 'decision', Observation.reconciled_at.is_(None)))
                if safety or hinted:
                    add(gateway_id, 'spear', at)  # Bounded fleet/late-evidence safety discovery.
        add(gateway_id, 'subzero_delivery', minimum(Delivery.retry_at,
            Delivery.gateway_id == gateway_id, or_(Delivery.delivered_checksum.is_(None),
                Delivery.delivered_checksum != Delivery.content_checksum)))
        add(gateway_id, 'subzero_discovery', minimum(Checkpoint.retry_at,
            Checkpoint.gateway_id == gateway_id, Checkpoint.last_error.is_not(None)))
        cursor = db.session.get(Cursor, gateway_id)
        unfinished = cursor is not None and (cursor.audit_after < latest_audit
            or gateway_id in expedite and cursor.operation_after < cursor.sweep_until)
        if unfinished or safety or hinted:
            owns = or_(Operation.gateway_id == gateway_id,
                Operation.gateway_id.is_(None) & (func.upper(Operation.gateway_code) == code.upper()))
            if db.session.scalar(select(Operation.id).where(owns).limit(1)) is not None:
                add(gateway_id, 'subzero_discovery', at)
        add(gateway_id, 'documents', minimum(Document.retry_at,
            Document.gateway_id == gateway_id, Document.archived_at_utc.is_(None)))
        if code.upper() != 'RFD':
            continue
        batch = db.session.execute(select(Task.lease_token, Task.retry_at).where(
            Task.gateway_id == gateway_id, Task.kind == 'worker', Task.identity == 'memory-budget',
            Task.version == 'frost-worker-v1')).first()
        # A different process may release the shared research lease shortly.
        # Due work retains the old <=60s contention check rather than sleeping
        # until its 15-minute crash lease and delaying an unstarted hourly slot.
        available = min(max(at, batch.retry_at), at+timedelta(seconds=60)) if batch and batch.lease_token else at
        def research(kind, deadline):
            if deadline is not None:
                add(gateway_id, kind, max(deadline, available))
        for kinds, kind in ((('xchange_page',), 'xchange'), (('departure_revision', 'departure_link'), 'departures')):
            research(kind, minimum(Task.retry_at, Task.gateway_id == gateway_id,
                Task.kind.in_(kinds), Task.version == (auto.VERSION if kind == 'xchange' else departures.VERSION),
                Task.status.in_(('pending', 'retry'))))
        if safety or hinted:
            has_missions = db.session.scalar(select(Mission.id).where(
                Mission.gateway_code == 'RFD', Mission.mission_type == 'departure').limit(1)) is not None
            has_outcomes = db.session.scalar(select(Task.id).where(
                Task.gateway_id == gateway_id, Task.kind == 'xchange_outcome').limit(1)) is not None
            if has_missions or has_outcomes:
                research('departures', at)
        else:
            discovery = db.session.execute(select(Task.summary).where(Task.gateway_id == gateway_id,
                Task.kind == 'departure_control', Task.identity == 'discovery', Task.version == departures.VERSION)).scalar()
            if discovery is not None and not discovery.get('discovery_complete'):
                research('departures', at)
        if current_app.config.get('FROST_AUTO_COLLECTION', True) is True:
            schedule=auto.Schedule.load(db.session.get(Gateway,gateway_id))
            aware=at.replace(tzinfo=timezone.utc)
            if schedule.eligible(aware):
                row = db.session.execute(select(Task.status, Task.retry_at).where(Task.gateway_id == gateway_id,
                    Task.kind == 'auto_capture', Task.version == auto.VERSION,
                    Task.identity == auto.identity(aware))).first()
                if row is None:
                    research('auto', at)
                elif row.status in ('pending', 'retry', 'staged', 'archive_retry'):
                    research('auto', row.retry_at)
            next_capture=schedule.next_capture(aware)
            if next_capture and (next_frost_capture is None or next_capture < next_frost_capture):
                next_frost_capture = next_capture
            research('auto', next_capture.replace(tzinfo=None) if next_capture else None)
            research('auto', minimum(Task.retry_at, Task.gateway_id == gateway_id,
                Task.kind == 'auto_capture', Task.version == auto.VERSION,
                Task.status.in_(('staged', 'archive_retry'))))
    g.frost_next_capture_at = next_frost_capture
    return due, min(deadlines)


def perform(gateway_id, kinds):
    """Existing bounded drains retain all fencing, formats and verification."""
    from app.services import frost_auto, frost_documents, frost_departures, frost_xchange_links
    from app.services import neoscorpion_spear_decisions as spear, neosubzero_archive as subzero
    from app.services.neostaffing_sheet_sync import drain as staffing_sheet_drain
    jobs = [('auto', frost_auto.tick), ('spear', spear.drain),
        ('documents', frost_documents.drain), ('staffing_sheet', staffing_sheet_drain)]
    progress = False
    def subzero_job(gateway):
        nonlocal progress
        if 'subzero_discovery' in kinds:
            stats = subzero.reconcile(gateway)
            progress = bool(stats['reconstructed'] or stats['failed'])
        return subzero.drain(gateway, reconcile_first=False)
    if kinds & {'subzero_delivery', 'subzero_discovery'}:
        jobs.append(('subzero', subzero_job))
    for name, job in jobs:
        if name not in kinds and name != 'subzero':
            continue
        try:
            job(gateway_id)
        except Exception as error:
            db.session.rollback()
            current_app.logger.warning('Archive task deferred safely: kind=%s error=%s', name, type(error).__name__)
        finally:
            db.session.remove()
    from app.services.frost_resources import reason
    if reason():
        return progress
    for name, job in (('xchange', frost_xchange_links.drain), ('departures', frost_departures.drain)):
        if name in kinds:
            try:
                job(gateway_id)
            except Exception as error:
                db.session.rollback()
                current_app.logger.warning('Archive task deferred safely: kind=%s error=%s', name, type(error).__name__)
            finally:
                db.session.remove()
    return progress


class ArchiveDeadlines:
    """One existing Timer at a time, coalesced hints, no idle connection."""
    def __init__(self, app, timer_factory):
        self.app, self.timer_factory = app, timer_factory
        self.lock = Lock()
        self.timer = None
        self.generation = 0
        self.deadline = utcnow() + timedelta(seconds=10)
        self.safety = self.deadline
        self.hinted = False
        self.running = False
        self.last_started = None
        self.observed = utcnow()
        self.expedite = set()
        self.arm_failed = False
        self.arm()

    def arm(self):
        # Caller owns lock after initialization. Generation fences canceled
        # callbacks that have already entered the timer's dispatch path.
        generation = self.generation + 1
        previous = self.timer
        seconds = max(.1, min(60, (self.deadline - utcnow()).total_seconds()))
        try:
            timer = self.timer_factory(seconds, lambda: self.wake(generation))
            timer.daemon = True
            timer.start()  # Do not cancel a live safety timer if allocation fails.
        except Exception:
            self.arm_failed = True
            raise
        self.arm_failed = False
        self.generation, self.timer = generation, timer
        if previous is not None:
            previous.cancel()

    def notify(self):
        with self.lock:
            self.hinted = True
            if self.running:
                return
            sooner = utcnow() + timedelta(seconds=1)
            if self.last_started:
                sooner = max(sooner, self.last_started + timedelta(seconds=BUSY_SECONDS))
            if sooner < self.deadline:
                self.deadline = sooner
                self.rearm()

    def rearm(self):
        try:
            self.arm()
        except Exception as error:
            self.app.logger.error('Archive timer allocation deferred; existing recovery clock will retry: %s',
                type(error).__name__)

    def wake(self, generation):
        with self.lock:
            if generation != self.generation or self.running:
                return
            current = utcnow()
            if current < self.observed:
                self.deadline = current  # Recompute UTC deadlines after a backward clock step.
                self.safety = current
            self.observed = current
            if utcnow() < self.deadline:
                self.rearm()  # Wall-clock check only; absolutely no database call.
                return
            self.running = True
            self.last_started = utcnow()
            hinted, self.hinted = self.hinted, False
        at = utcnow()
        safety = at >= self.safety
        next_due = at + timedelta(seconds=BUSY_SECONDS)
        try:
            from app.services.memory_diagnostics import observe_process_memory
            with self.app.app_context(), observe_process_memory('archive_delivery_cycle'):
                g.background_deadline_work = True
                from app.services.neoscorpion_spear_decisions import _worker_lock
                if not _worker_lock.acquire(blocking=False):
                    return
                try:
                    due, next_due = plan(at, safety=safety, hinted=hinted, expedite=self.expedite)
                    db.session.remove()
                    for gateway, kinds in due.items():
                        if 'subzero_discovery' in kinds:
                            self.expedite.discard(gateway)
                        if perform(gateway, kinds):
                            self.expedite.add(gateway)
                    after = utcnow()
                    if due:
                        remaining, next_due = plan(after, expedite=self.expedite)
                        if remaining:
                            next_due = min(next_due, after + timedelta(seconds=BUSY_SECONDS))
                    if safety:
                        self.safety = safety_deadline(after)
                    next_due = min(next_due, self.safety)
                    next_frost = g.get('frost_next_capture_at')
                    frost_hour = next_frost.isoformat() if next_frost else 'not scheduled'
                    self.app.logger.info('Archive deadline cycle complete: groups=%s next_utc=%s next_frost_hour_utc=%s',
                        sum(len(kinds) for kinds in due.values()), next_due.isoformat()+'Z',
                        frost_hour if self.app.config.get('FROST_AUTO_COLLECTION',True) is True else 'disabled')
                finally:
                    try:
                        db.session.remove()
                    finally:
                        _worker_lock.release()
        except Exception as error:
            self.app.logger.warning('Archive deadline discovery deferred safely: %s', type(error).__name__)
        finally:
            with self.lock:
                self.running = False
                notified_due = max(utcnow() + timedelta(seconds=1), self.last_started + timedelta(seconds=BUSY_SECONDS))
                self.deadline = min(next_due, notified_due) if self.hinted else next_due
                self.rearm()


def notify_archive_worker(app):
    worker = app.extensions.get(EXTENSION)
    if worker is not None:
        worker.notify()


def repair_failed_timer(app):
    """Use the existing recovery clock only for a known allocation failure.

    No new monitor/thread, database call, or work while a healthy timer waits.
    """
    worker = app.extensions.get(EXTENSION)
    if worker is None or not worker.arm_failed:
        return False
    with worker.lock:
        if worker.running or worker.timer is not None and worker.timer.is_alive():
            return False
        try:
            worker.arm()
            return True
        except Exception as error:
            app.logger.warning('Archive timer allocation remains deferred: %s',type(error).__name__)
            return False


_signals_installed = False


def install_commit_signals():
    global _signals_installed
    if _signals_installed:
        return
    _signals_installed = True
    event.listen(Session, 'after_flush', _changed)
    event.listen(Session, 'after_commit', _committed)
    event.listen(Session, 'after_soft_rollback', _rolled_back)


_ARCHIVE_MODELS = frozenset(('NeoScorpionSpearObservation', 'NeoScorpionSpearFleetObservation',
    'NeoScorpionSpearAuditEntry', 'NeoScorpionFuelingEvent', 'NeoScorpionFuelWorkState',
    'NeoScorpionFuelAssignment', 'NeoSubZeroAuditEntry', 'NeoSubZeroSprayRecord',
    'NeoSubZeroDepartureDeiceEvent', 'NeoSubZeroPretreatState', 'FrostDocument', 'FrostHistoryTask',
    'SortDateMission', 'SortDateParkingAssignment', 'SortDateOperation', 'NeoScorpionSettings', 'Gateway'))


def _changed(session, _context):
    if not has_app_context() or getattr(g, 'background_deadline_work', False):
        return
    changed = chain(session.new, session.deleted,
        (row for row in session.dirty if session.is_modified(row, include_collections=False)))
    names = {type(row).__name__ for row in changed}
    if 'StaffingEmployeeRecovery' in names:
        session.info['recovery_deadline_hint'] = True
    if names & _ARCHIVE_MODELS:
        session.info['archive_deadline_hint'] = True


def _committed(session):
    if session.in_nested_transaction() or not has_app_context():
        return
    if session.info.pop('recovery_deadline_hint', False):
        from app.services.neostaffing_employee_recovery_worker import notify_recovery_worker
        try:
            notify_recovery_worker(current_app._get_current_object())
        except Exception as error:
            current_app.logger.warning('Committed recovery wake deferred to safety scan: %s',type(error).__name__)
    if session.info.pop('archive_deadline_hint', False):
        try:
            notify_archive_worker(current_app._get_current_object())
        except Exception as error:
            current_app.logger.warning('Committed archive wake deferred to safety scan: %s',type(error).__name__)


def _rolled_back(session, previous):
    if previous.parent is None:
        session.info.pop('recovery_deadline_hint', None)
        session.info.pop('archive_deadline_hint', None)
