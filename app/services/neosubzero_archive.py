"""Retryable SubZero delivery through the existing SPEAR Vault worker boundary."""
from datetime import datetime, timedelta
import hashlib
import json
from sqlalchemy import or_
from flask import current_app
from app.extensions import db
from app.models import (SortDateOperation, Gateway,NeoSubZeroArchiveDelivery as Delivery,
                        NeoSubZeroDepartureDeiceEvent, NeoSubZeroPretreatState)
from app.services.neosubzero_reporting import actual_passes, archive_payload, learning_context,gateway_operations,operation_gateway_id


def checksum(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def payloads_for_operation(op):
    """A retracted START is an audited correction, not a fictitious application.

    Keep earlier immutable evidence and explicitly supersede it. Never archive an
    ordinary unsprayed CLEAR/NEGATIVE that did not previously have spray evidence.
    """
    from app.models import NeoSubZeroAuditEntry
    from app.services.neosubzero_evidence import evidence_values
    payloads={row['id']:archive_payload(row) for row in actual_passes(op)}
    for receipt in Delivery.query.filter_by(operation_id=op.id).all():
        if receipt.source_id in payloads: continue
        kind,identifier,_,number=receipt.source_id.split(':')
        model=NeoSubZeroPretreatState if kind=='pretreat' else NeoSubZeroDepartureDeiceEvent
        parent=db.session.get(model,int(identifier))
        if parent is None: continue  # FK-preserved source disappearance requires administrator investigation.
        audits=NeoSubZeroAuditEntry.query.filter_by(sort_date_operation_id=op.id,tail_number=parent.tail_number).order_by(NeoSubZeroAuditEntry.id).all()
        payloads[receipt.source_id]=dict(schema_version='subzero-spray/v1',record_type='spray_evidence_correction',
            id=receipt.source_id,gateway_id=operation_gateway_id(op),gateway=op.gateway_code,operation_id=op.id,
            sort_date=op.sort_date.isoformat(),sort_name=op.sort_name,activity=kind,tail=parent.tail_number,
            pass_number=int(number),state='start_retracted',revision_utc=parent.updated_at.isoformat(),
            current_source=evidence_values(parent),audit_provenance=[evidence_values(a) for a in audits])
    return payloads


def reconcile(gateway_id):
    """Recover missed enqueues, including earlier sorts and audited corrections."""
    departure=db.session.query(NeoSubZeroDepartureDeiceEvent.sort_date_operation_id).filter(
        NeoSubZeroDepartureDeiceEvent.pass1_started_at_utc.is_not(None))
    pretreat=db.session.query(NeoSubZeroPretreatState.sort_date_operation_id).filter(
        NeoSubZeroPretreatState.pass1_started_at_utc.is_not(None))
    for op in gateway_operations(db.session.get(Gateway,gateway_id)).filter(
            or_(SortDateOperation.id.in_(departure.union(pretreat)),SortDateOperation.id.in_(db.session.query(Delivery.operation_id)))).order_by(SortDateOperation.id).all():
        for source_id,payload in payloads_for_operation(op).items():
            digest = checksum(payload)
            delivery = Delivery.query.filter_by(operation_id=op.id, source_id=source_id).with_for_update().one_or_none()
            if delivery is None:
                db.session.add(Delivery(gateway_id=gateway_id, operation_id=op.id, source_id=source_id, content_checksum=digest))
            elif delivery.content_checksum != digest:
                delivery.content_checksum = digest
                delivery.retry_at = datetime.utcnow()
                delivery.attempts = 0
                delivery.last_error = None
    db.session.commit()


def drain(gateway_id, *, limit=3):
    from app.services.neoscorpion_learning_vault import export_subzero_record
    reconcile(gateway_id)
    now = datetime.utcnow()
    pending = Delivery.query.filter(Delivery.gateway_id == gateway_id, Delivery.retry_at <= now,
        or_(Delivery.delivered_checksum.is_(None), Delivery.delivered_checksum != Delivery.content_checksum)).order_by(
        Delivery.retry_at, Delivery.id).limit(limit).with_for_update(skip_locked=True).all()
    claims = []
    for delivery in pending:
        delivery.attempts += 1
        # Lease survives process termination. No remote I/O in an operational transaction.
        delivery.retry_at = now + timedelta(seconds=min(3600, 30 * 2 ** min(delivery.attempts, 6)))
        claims.append((delivery.id, delivery.content_checksum))
    db.session.commit()
    delivered = 0
    for identifier, expected in claims:
        item = db.session.get(Delivery, identifier)
        try:
            op = db.session.get(SortDateOperation, item.operation_id)
            payload = payloads_for_operation(op)[item.source_id]
            if checksum(payload) != expected:
                db.session.rollback()
                continue  # A concurrent correction is reconciled on the next pass.
            db.session.rollback()  # Release read snapshot before provider calls.
            saved = export_subzero_record(payload)
            learning = export_subzero_record(learning_context(payload), learning=True)
            item = Delivery.query.filter_by(id=identifier).with_for_update().one()
            # A newer correction must never be marked delivered by an older request.
            if item.content_checksum == expected:
                item.delivered_checksum = expected
                item.vault_key = saved['key']
                item.learning_key = learning['key']
                item.delivered_at = datetime.utcnow()
                item.last_error = None
                delivered += 1
            db.session.commit()
        except Exception:
            db.session.rollback()
            item = db.session.get(Delivery, identifier)
            item.last_error = 'Archive synchronization failed; automatic retry pending. Operational work is unaffected.'
            db.session.commit()
            current_app.logger.warning('SubZero archive synchronization deferred: delivery=%s', identifier, exc_info=True)
    return delivered


def status(operation):
    from app.services.neoscorpion_learning_vault import learning_vault_status
    rows = Delivery.query.filter_by(operation_id=operation.id).all() if operation else []
    passes = payloads_for_operation(operation) if operation else {}
    by_source = {row.source_id: row for row in rows}
    pending = sum(not (by_source.get(source) and by_source[source].delivered_checksum == checksum(payload)) for source,payload in passes.items())
    return dict(configured=learning_vault_status().configured, pending=pending,
                errors=sum(bool(row.last_error) for row in rows), total=len(passes))
