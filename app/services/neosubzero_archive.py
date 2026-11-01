"""Retryable SubZero delivery through the existing SPEAR Vault worker boundary."""
from datetime import datetime, timedelta
import hashlib
import json
from sqlalchemy import or_, select, func
from flask import current_app
from app.extensions import db
from app.models import (SortDateOperation, NeoSubZeroAuditEntry, NeoSubZeroArchiveDelivery as Delivery,
                        NeoSubZeroDepartureDeiceEvent, NeoSubZeroPretreatState)
from app.services.neosubzero_reporting import actual_passes, archive_payload,operation_gateway_id


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
    from app.services.frost_operational_evidence import payloads as outcome_payloads
    payloads.update(outcome_payloads(op))
    from app.services.frost_inspections import payloads as inspection_payloads
    payloads.update(inspection_payloads(op))
    for receipt in Delivery.query.filter_by(operation_id=op.id).all():
        if receipt.source_id in payloads: continue
        kind,identifier,_,number=receipt.source_id.split(':')
        if kind=='frost':continue
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
    """Recover changes and missed enqueues with durable bounded discovery."""
    from app.services.neosubzero_archive_reconciliation import reconcile as discover
    return discover(gateway_id, payloads_for_operation, checksum)


def _audit_version(operation_id):
    return tuple(db.session.execute(select(func.count(NeoSubZeroAuditEntry.id),
        func.coalesce(func.max(NeoSubZeroAuditEntry.id), 0)).where(
            NeoSubZeroAuditEntry.sort_date_operation_id == operation_id)).one())


def drain(gateway_id, *, limit=3):
    from app.services.neoscorpion_learning_vault import export_subzero_pair
    reconcile(gateway_id)
    now = datetime.utcnow()
    pending = Delivery.query.filter(Delivery.gateway_id == gateway_id, Delivery.retry_at <= now,
        or_(Delivery.delivered_checksum.is_(None), Delivery.delivered_checksum != Delivery.content_checksum)).order_by(
        Delivery.retry_at, Delivery.id).limit(limit).with_for_update(skip_locked=True).all()
    claims = []
    for delivery in pending:
        delivery.attempts += 1
        # Lease survives process termination. No remote I/O in an operational transaction.
        delivery.retry_at = now + timedelta(minutes=5)
        claims.append((delivery.id, delivery.content_checksum, delivery.attempts))
    db.session.commit()
    delivered = 0
    for identifier, expected, attempt in claims:
        item = db.session.get(Delivery, identifier)
        try:
            op = db.session.get(SortDateOperation, item.operation_id)
            audit_version = _audit_version(op.id)
            payload = payloads_for_operation(op)[item.source_id]
            if checksum(payload) != expected or audit_version != _audit_version(op.id):
                db.session.rollback()
                continue  # A concurrent correction is reconciled on the next pass.
            operation_id = op.id
            db.session.rollback()  # Release read snapshot before provider calls.
            if payload.get('schema_version') in ('frost-outcome-evidence-v1','frost-physical-inspection-v1'):
                from app.services.frost_history import memory_allows_work
                if not memory_allows_work():
                    item=db.session.get(Delivery,identifier)
                    if item.content_checksum==expected and item.attempts==attempt:
                        item.last_error='FROST observation delivery deferred for memory safety; automatic retry pending. Operational work is unaffected.'
                    db.session.commit()
                    continue
                from app.services.frost_vault import put_json
                key=put_json('frost-observations',gateway_id,payload['id'].replace(':','-'),payload)
                saved=learning=dict(key=key,verified=True)
            else:
                saved, learning = export_subzero_pair(payload)
            if saved.get('verified') is not True or learning.get('verified') is not True:
                raise ValueError('Archive pair read-back is not verified.')
            # Check the append-only canonical audit fence in the UPDATE itself,
            # even when the correction has not reached discovery yet. Count
            # also catches an earlier sequence ID that committed out of order.
            count = select(func.count(NeoSubZeroAuditEntry.id)).where(
                NeoSubZeroAuditEntry.sort_date_operation_id == operation_id).scalar_subquery()
            newest = select(func.coalesce(func.max(NeoSubZeroAuditEntry.id), 0)).where(
                NeoSubZeroAuditEntry.sort_date_operation_id == operation_id).scalar_subquery()
            verified_at = datetime.utcnow()
            delivered += Delivery.query.filter(Delivery.id == identifier, Delivery.content_checksum == expected,
                Delivery.attempts == attempt, count == audit_version[0], newest == audit_version[1]).update(dict(
                    delivered_checksum=expected, vault_key=saved['key'], learning_key=learning['key'],
                    delivered_at=verified_at, verified_at=verified_at, last_error=None), synchronize_session=False)
            db.session.commit()
        except Exception:
            db.session.rollback()
            item = db.session.get(Delivery, identifier)
            if item.content_checksum == expected and item.attempts == attempt:
                item.last_error = 'Archive content is unverified; automatic retry pending. Operational work is unaffected.'
                item.retry_at = datetime.utcnow() + timedelta(seconds=min(3600, 30 * 2 ** min(attempt, 6)))
            db.session.commit()
            current_app.logger.warning('SubZero archive synchronization deferred: delivery=%s', identifier)
    return delivered


def status(operation):
    from app.services.neoscorpion_learning_vault import learning_vault_status
    rows = Delivery.query.filter_by(operation_id=operation.id).all() if operation else []
    passes = payloads_for_operation(operation) if operation else {}
    by_source = {row.source_id: row for row in rows}
    pending = sum(not (by_source.get(source) and by_source[source].delivered_checksum == checksum(payload)) for source,payload in passes.items())
    verified = sum(bool(by_source.get(source) and by_source[source].verified_at and
                       by_source[source].delivered_checksum == checksum(payload)) for source,payload in passes.items())
    return dict(configured=learning_vault_status().configured, pending=pending,
                verified=verified, legacy=len(passes)-pending-verified,
                errors=sum(bool(row.last_error) for row in rows), total=len(passes))
