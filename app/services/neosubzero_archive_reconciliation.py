"""Bounded discovery for the existing archive worker; no provider I/O.

Existing append-only audits supply transaction-durable change hints. A finite
keyset sweep independently recovers missing signals, out-of-order audit commits,
and lost receipts. Only queue metadata is locked; operators never touch it.
"""
from datetime import datetime, timedelta
from sqlalchemy import select, func
from flask import current_app

from app.extensions import db
from app.models import (Gateway, SortDateOperation, NeoSubZeroAuditEntry as Audit,
    NeoSubZeroDepartureDeiceEvent, NeoSubZeroPretreatState, NeoSubZeroSprayRecord,
    NeoSubZeroArchiveDelivery as Delivery, NeoSubZeroArchiveCursor as Cursor,
    NeoSubZeroArchiveCheckpoint as Checkpoint)
from app.services.neosubzero_reporting import gateway_operations

HISTORY_BATCH = 4
CHANGE_BATCH = 1
RECENT_BATCH = 2
RETRY_BATCH = 2
MAX_OPERATIONS = HISTORY_BATCH + RECENT_BATCH + 2 * CHANGE_BATCH + RETRY_BATCH
FINGERPRINT_VERSION = 'subzero-archive-discovery-v1'


def _history_ids(gateway, *, after=0, until=None, descending=False, limit=HISTORY_BATCH):
    """Two indexed ownership streams, merged in bounded memory (legacy included)."""
    result = []
    for ownership in (SortDateOperation.gateway_id == gateway.id,
            (SortDateOperation.gateway_id.is_(None) &
             (func.upper(SortDateOperation.gateway_code) == gateway.code.upper()))):
        query = select(SortDateOperation.id).where(ownership, SortDateOperation.id > after)
        if until is not None:
            query = query.where(SortDateOperation.id <= until)
        query = query.order_by(SortDateOperation.id.desc() if descending else SortDateOperation.id).limit(limit)
        result.extend(db.session.scalars(query))
    return sorted(set(result), reverse=descending)[:limit]


def _inventories(ids):
    inventory = {identifier:[] for identifier in ids}
    for operation_id, identifier, source_id, target in db.session.execute(select(Delivery.operation_id, Delivery.id,
            Delivery.source_id, Delivery.content_checksum).where(Delivery.operation_id.in_(ids)).order_by(Delivery.id)):
        inventory[operation_id].append([identifier, source_id, target])
    return inventory


def _versions(operations, digest):
    """Scalar ID/revision reads for selected operations, never historical payloads.

    Track each revision, not MAX(updated_at), which can hide changes to an older
    row. Delivery status/retry fields are deliberately absent from the inventory.
    """
    ids = [op.id for op in operations]
    source = {op.id:[[FINGERPRINT_VERSION, op.id, op.gateway_id, op.gateway_code, op.sort_date.isoformat(), op.sort_name]]
              for op in operations}
    for model, stamp in ((NeoSubZeroDepartureDeiceEvent, 'updated_at'),
                         (NeoSubZeroPretreatState, 'updated_at'), (NeoSubZeroSprayRecord, 'updated_at'),
                         (Audit, 'recorded_at_utc')):
        for operation_id, identifier, at in db.session.execute(select(model.sort_date_operation_id, model.id,
                getattr(model, stamp)).where(model.sort_date_operation_id.in_(ids)).order_by(model.id)):
            source[operation_id].append([model.__tablename__, identifier, at.isoformat() if at else None])
    inventory = _inventories(ids)
    return {identifier:dict(source=digest(source[identifier]),
                           all=digest([digest(source[identifier]), inventory[identifier]])) for identifier in ids}


def _cursor(gateway_id):
    if db.session.get(Cursor, gateway_id) is None:
        dialect = db.session.get_bind().dialect.name
        if dialect == 'postgresql':
            from sqlalchemy.dialects.postgresql import insert
        else:
            from sqlalchemy.dialects.sqlite import insert
        db.session.execute(insert(Cursor).values(gateway_id=gateway_id, operation_after=0, sweep_until=0,
                                                audit_after=0).on_conflict_do_nothing(index_elements=['gateway_id']))
    return db.session.scalars(select(Cursor).where(Cursor.gateway_id == gateway_id)
        .with_for_update(skip_locked=True).execution_options(populate_existing=True)).first()


def reconcile(gateway_id, payloads_for_operation, digest):
    stats = dict(examined=0, reconstructed=0, receipts_changed=0, failed=0)
    gateway = db.session.get(Gateway, gateway_id)
    if gateway is None:
        return stats
    # sqlite3 legacy transaction mode does not BEGIN for SELECT/SAVEPOINT.
    # Keep cursor/checkpoint/receipt changes in one real outer transaction.
    connection = db.session.connection()
    if connection.dialect.name == 'sqlite' and not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql('BEGIN')
    cursor = _cursor(gateway_id)
    if cursor is None:
        db.session.rollback()
        return stats  # Another worker owns this gateway's short discovery batch.
    now = datetime.utcnow()
    # Raw PK pages are bounded before gateway filtering, so another gateway's
    # large audit history cannot turn a hint query into an unbounded join/scan.
    recent = db.session.execute(select(Audit.id, Audit.sort_date_operation_id)
        .order_by(Audit.id.desc()).limit(CHANGE_BATCH)).all()
    changes = db.session.execute(select(Audit.id, Audit.sort_date_operation_id)
        .where(Audit.id > cursor.audit_after).order_by(Audit.id).limit(CHANGE_BATCH)).all()
    retries = list(db.session.scalars(select(Checkpoint.operation_id).where(Checkpoint.gateway_id == gateway_id,
        Checkpoint.last_error.is_not(None), Checkpoint.retry_at <= now)
        .order_by(Checkpoint.retry_at, Checkpoint.operation_id).limit(RETRY_BATCH)))
    after = cursor.operation_after
    ceiling = cursor.sweep_until
    if not ceiling or after >= ceiling:
        after = 0
        ceiling = next(iter(_history_ids(gateway, descending=True, limit=1)), 0)
    historical = _history_ids(gateway, after=after, until=ceiling)
    if not historical and after:
        after = 0
        ceiling = next(iter(_history_ids(gateway, descending=True, limit=1)), 0)
        historical = _history_ids(gateway, until=ceiling)
    # Recent canonical operations also receive reserved slots independently of
    # another gateway's audit traffic or a missing enqueue/audit hint.
    newest_operations = _history_ids(gateway, descending=True, limit=RECENT_BATCH)
    candidates = list(dict.fromkeys([op for _, op in recent] + [op for _, op in changes]
                                   + newest_operations + retries + historical))
    operations = list(gateway_operations(gateway).filter(SortDateOperation.id.in_(candidates)).all())
    by_id = {op.id:op for op in operations}
    versions = _versions(operations, digest) if operations else {}
    checkpoints = {row.operation_id:row for row in db.session.scalars(
        select(Checkpoint).where(Checkpoint.operation_id.in_(by_id)))} if operations else {}
    planned, failures = {}, set()
    for identifier in candidates:
        if identifier not in by_id:
            continue
        stats['examined'] += 1
        op = by_id[identifier]
        checkpoint = checkpoints.get(identifier)
        version = versions[identifier]
        if checkpoint and checkpoint.gateway_id != gateway_id:
            # An operation's canonical gateway was changed; don't silently
            # reinterpret the other gateway's receipt/provenance.
            stats['failed'] += 1
            continue
        if checkpoint and checkpoint.source_fingerprint == version['all'] and not checkpoint.last_error:
            continue
        if (checkpoint and checkpoint.retry_at and checkpoint.retry_at > now
                and checkpoint.attempted_fingerprint == version['all']):
            continue
        try:
            stats['reconstructed'] += 1
            planned[identifier] = {source_id:digest(payload)
                                   for source_id,payload in payloads_for_operation(op).items()}
        except Exception:
            failures.add(identifier)
    # Finish all potentially expensive evidence/JSON work BEFORE receipt or
    # checkpoint inserts acquire FK locks on canonical operations. The short
    # write phase below never rebuilds payloads or calls a provider.
    final = _versions([by_id[identifier] for identifier in planned], digest) if planned else {}
    for identifier in list(planned):
        if versions[identifier]['source'] != final[identifier]['source']:
            failures.add(identifier)
            del planned[identifier]
    for identifier, targets in planned.items():
        checkpoint = checkpoints.get(identifier)
        try:
            with db.session.begin_nested():
                receipts = {row.source_id:row for row in Delivery.query.filter_by(operation_id=identifier)
                            .with_for_update().all()}
                changed = 0
                for source_id, checksum in targets.items():
                    receipt = receipts.get(source_id)
                    if receipt is None:
                        receipt = Delivery(gateway_id=gateway_id, operation_id=identifier, source_id=source_id,
                                           content_checksum=checksum)
                        db.session.add(receipt)
                        changed += 1
                    elif receipt.gateway_id != gateway_id:
                        raise ValueError('Archive receipt gateway requires review.')
                    elif receipt.content_checksum != checksum:
                        receipt.content_checksum = checksum
                        receipt.retry_at = now
                        # Monotonic attempt fencing also rejects A -> B -> A
                        # corrections while an earlier upload remains in flight.
                        receipt.attempts += 1
                        receipt.last_error = None
                        changed += 1
                db.session.flush()
                stamp = digest([versions[identifier]['source'], _inventories([identifier])[identifier]])
                if checkpoint is None:
                    checkpoint = Checkpoint(gateway_id=gateway_id, operation_id=identifier)
                    db.session.add(checkpoint)
                checkpoint.source_fingerprint = stamp
                checkpoint.attempted_fingerprint = stamp
                checkpoint.attempts = 0
                checkpoint.retry_at = None
                checkpoint.last_error = None
                db.session.flush()
                stats['receipts_changed'] += changed
        except Exception:
            failures.add(identifier)
    for identifier in failures:
        # A bad old operation must neither starve the cursor nor roll back
        # successful siblings. Its durable bounded retry survives restarts.
        checkpoint = db.session.get(Checkpoint, identifier)
        if checkpoint is None:
            checkpoint = Checkpoint(gateway_id=gateway_id, operation_id=identifier, attempts=0)
            db.session.add(checkpoint)
        if checkpoint.gateway_id != gateway_id:
            continue
        checkpoint.attempts += 1
        checkpoint.attempted_fingerprint = versions[identifier]['all']
        checkpoint.retry_at = now + timedelta(seconds=min(900, 60 * 2 ** min(checkpoint.attempts - 1, 4)))
        checkpoint.last_error = 'Archive discovery deferred; evidence preserved for retry.'
        stats['failed'] += 1
        current_app.logger.warning('SubZero archive discovery deferred: operation=%s', identifier)
    cursor.operation_after = historical[-1] if historical else 0
    cursor.sweep_until = ceiling
    if changes:
        cursor.audit_after = changes[-1][0]
    db.session.commit()
    return stats
