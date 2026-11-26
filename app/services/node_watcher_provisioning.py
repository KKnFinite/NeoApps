"""Approval-time provisioning and the one-time #112 grant reconciliation."""

from sqlalchemy import and_, literal, select, text, true
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.extensions import db
from app.models import AccountSecurityEvent, Gateway, GatewayMembership, GatewayNodeRole, NeoNode, PortalAppAccess, User
from app.services.account_authority import DEFAULT_NODE_ROLE, effective_node_role

REPAIR_ACTION = "node_watcher_baseline_reconciled"


def _eligible_memberships():
    return (select(GatewayMembership.id.label("membership_id"), GatewayMembership.user_id, GatewayMembership.gateway_id)
        .select_from(GatewayMembership)
        .join(User, User.id == GatewayMembership.user_id)
        .join(Gateway, Gateway.id == GatewayMembership.gateway_id)
        .join(PortalAppAccess, and_(PortalAppAccess.user_id == User.id, PortalAppAccess.app_code == "neogateway"))
        .where(User.is_active.is_(True), ~User.scorpion_vendor.has(), Gateway.is_active.is_(True),
               GatewayMembership.status == "approved", GatewayMembership.is_active.is_(True),
               PortalAppAccess.status == "approved", PortalAppAccess.is_active.is_(True)))


def _matrix(membership_id=None):
    eligible = _eligible_memberships()
    if membership_id is not None:
        eligible = eligible.where(GatewayMembership.id == membership_id)
    eligible = eligible.subquery()
    return (select(eligible.c.membership_id, eligible.c.user_id, eligible.c.gateway_id,
                   NeoNode.id.label("node_id"), NeoNode.code.label("node_code"),
                   GatewayNodeRole.id.label("grant_id"), GatewayNodeRole.role, GatewayNodeRole.is_active)
        .select_from(eligible)
        .join(NeoNode, NeoNode.is_active.is_(True))
        .outerjoin(GatewayNodeRole, and_(GatewayNodeRole.gateway_membership_id == eligible.c.membership_id,
                                        GatewayNodeRole.node_id == NeoNode.id)))


def _insert_missing(matrix):
    missing = matrix.where(GatewayNodeRole.id.is_(None)).subquery()
    rows = select(missing.c.membership_id, missing.c.node_id, literal(DEFAULT_NODE_ROLE), true()).where(true())
    insert = postgres_insert if db.engine.dialect.name == "postgresql" else sqlite_insert
    statement = insert(GatewayNodeRole).from_select(
        ["gateway_membership_id", "node_id", "role", "is_active"], rows
    ).on_conflict_do_nothing(index_elements=["gateway_membership_id", "node_id"])
    return db.session.execute(statement.returning(GatewayNodeRole.id)).scalars().all()


def provision_watcher_nodes(membership):
    """Insert missing assignments inside the approval action's transaction."""
    db.session.flush()
    inserted = _insert_missing(_matrix(membership.id))
    if inserted:
        from app.services.request_cache import clear_request_cache
        clear_request_cache()
    return len(inserted)


def _summarize(rows, members):
    return dict(
        eligible_accounts=len({row.user_id for row in members}),
        eligible_gateway_memberships=len(members),
        active_nodes=db.session.scalar(select(db.func.count()).select_from(NeoNode).where(NeoNode.is_active.is_(True))),
        eligible_account_node_pairs=len(rows),
        missing_assignments=sum(row.grant_id is None for row in rows),
        missing_sektor_assignments=sum(row.grant_id is None and row.node_code == "sektor" for row in rows),
        higher_roles=sum(row.grant_id is not None and row.is_active and row.role != DEFAULT_NODE_ROLE for row in rows),
        explicit_disabled=sum(row.grant_id is not None and not row.is_active for row in rows),
        explicit_watcher=sum(row.is_active and row.role == DEFAULT_NODE_ROLE for row in rows if row.grant_id is not None),
    )


def reconcile_watcher_nodes(bootstrap_actor, gateway):
    """Lock, reconcile, and audit in the existing PostgreSQL deployment transaction."""
    from app.services.account_scope_runtime import scoped_active

    if db.engine.dialect.name != "postgresql" or not scoped_active():
        return None, None
    prior = AccountSecurityEvent.query.filter_by(action=REPAIR_ACTION).first()
    if prior:
        return dict(audit_id=prior.id, **prior.details["summary"]), prior.details["verification"]
    db.session.execute(text("SET LOCAL lock_timeout = '5s'"))
    NeoNode.query.filter_by(code="motherbrain").with_for_update().first()
    db.session.execute(text(
        "LOCK TABLE users, portal_app_accesses, gateways, gateway_memberships, neo_nodes, "
        "gateway_node_roles, neoscorpion_vendor_accounts, account_authorization_states, account_security_events "
        "IN SHARE ROW EXCLUSIVE MODE"
    ))
    prior = AccountSecurityEvent.query.filter_by(action=REPAIR_ACTION).first()
    if prior:
        return dict(audit_id=prior.id, **prior.details["summary"]), prior.details["verification"]

    if not bootstrap_actor.is_active or bootstrap_actor.role != "grandmaster" or bootstrap_actor.scorpion_vendor:
        raise RuntimeError("Watcher reconciliation requires the configured active bootstrap administrator.")
    members = db.session.execute(_eligible_memberships()).all()
    before = db.session.execute(_matrix()).all()
    summary = _summarize(before, members)
    inserted = _insert_missing(_matrix())
    after = db.session.execute(_matrix()).all()
    before_by_pair = {(row.membership_id, row.node_id): row for row in before}
    inserted_ids = set(inserted)
    if len(inserted_ids) != summary["missing_assignments"] or len(before) != len(after):
        raise RuntimeError("Watcher reconciliation changed the eligible matrix during the repair.")
    for row in after:
        prior_row = before_by_pair[(row.membership_id, row.node_id)]
        if prior_row.grant_id is None:
            if row.grant_id not in inserted_ids or not row.is_active or effective_node_role(row) != DEFAULT_NODE_ROLE:
                raise RuntimeError("An eligible account-node pair is missing its Watcher assignment.")
        elif (row.grant_id, row.role, row.is_active) != (prior_row.grant_id, prior_row.role, prior_row.is_active):
            raise RuntimeError("Watcher reconciliation encountered a changed explicit role or restriction.")

    summary.update(assignments_corrected=len(inserted_ids), missing_after=0)
    verification = verify_sektor_watcher_access(gateway)
    receipt = AccountSecurityEvent(
        actor_user_id=bootstrap_actor.id, subject_user_id=bootstrap_actor.id, action=REPAIR_ACTION,
        details=dict(executed_by="deployment bootstrap", authorization="NeoApps #112",
                     summary=summary, verification=verification, inserted_grant_ids=sorted(inserted_ids)),
    )
    db.session.add(receipt)
    db.session.flush([receipt])
    return dict(audit_id=receipt.id, **summary), verification


def verify_sektor_watcher_access(gateway):
    """Evaluate deployed authorization and native ULD projection on real saved data."""
    from flask import current_app

    from app.neonodes.neoraiden.services import board_sources, can_open_raiden, gateway_operations
    from app.services.access_control import (
        get_user_node_role, prime_lightweight_live_request_scope, prime_user_node_roles_for_request,
    )
    from app.services.account_scope_runtime import authority, scoped_minimum
    from app.services.account_authority import Scope
    from app.services.operation_lifecycle import current_existing_operational_sort_operations
    from app.services.permission_rules import get_permission_rule, user_can
    from app.models import SortDateOperation

    node = NeoNode.query.filter_by(code="sektor", is_active=True).one_or_none()
    if not node:
        return dict(status="sektor_inactive")
    scope = Scope("node", "neogateway", gateway.id, node.id)
    sektor = _matrix().where(NeoNode.id == node.id).subquery()
    candidates = db.session.execute(
        select(sektor.c.user_id, sektor.c.role, sektor.c.grant_id).where(
            sektor.c.gateway_id == gateway.id, sektor.c.is_active.is_(True)
        ).order_by(sektor.c.user_id)
    ).all()
    watcher = next((row for row in candidates if row.role == DEFAULT_NODE_ROLE), None)
    higher = next((row for row in candidates if row.role != DEFAULT_NODE_ROLE), None)
    if not watcher:
        return dict(status="no_eligible_sektor_watcher")

    keys = ("neosektor.dashboard.view", "neosektor.live_counts.view", "neosektor.ebm.edit")
    viewer = db.session.get(User, watcher.user_id)
    with current_app.test_request_context():
        ordinary_role = get_user_node_role(viewer, gateway.code, "sektor")
    with current_app.test_request_context():
        batch_role = prime_user_node_roles_for_request(viewer, gateway.code, ("sektor",))["sektor"]
    with current_app.test_request_context("/neosektor/live-counts/state"):
        live_role = prime_lightweight_live_request_scope(viewer, "sektor").node_role
        if any(role != DEFAULT_NODE_ROLE for role in (ordinary_role, batch_role, live_role, authority(viewer).role(scope))):
            raise RuntimeError("Production NeoSektor Watcher authority adapters disagree.")
        decisions = {key: user_can(key, user=viewer) for key in keys}
        minimums = {}
        for key in keys:
            rule = get_permission_rule(key)
            minimums[key] = scoped_minimum(scope, key, rule.minimum_role) if rule else None
        if not all(decisions[key] for key in keys[:2]) or decisions[keys[2]] or not can_open_raiden(viewer, gateway):
            raise RuntimeError("Production NeoSektor Watcher read/edit decisions failed verification.")
        current = current_existing_operational_sort_operations(gateway)
        operations = gateway_operations(gateway).order_by(
            SortDateOperation.sort_date.desc(), SortDateOperation.id.desc()
        ).limit(8).all()
        existing = tuple(op for op in current if op.gateway_code == gateway.code and op.gateway_id in (None, gateway.id))
        source = None
        used_operation = None
        for operation in (*existing, *operations):
            _, source = board_sources(viewer, gateway, operation)
            if source is not None:
                used_operation = operation
                break
        match = None
        if used_operation is not None and higher:
            other = db.session.get(User, higher.user_id)
            _, higher_source = board_sources(other, gateway, used_operation)
            match = source == higher_source
            if not match:
                raise RuntimeError("NeoRaiden ULD data diverged for production Watcher and higher-role viewers.")
    return dict(status="verified", gateway=gateway.code,
                dashboard=decisions[keys[0]], live_counts=decisions[keys[1]], ebm_edit=decisions[keys[2]],
                configured_minimums=minimums,
                uld_source_available=source is not None, uld_matches_higher_role=match,
                uld_current_sort=used_operation is not None and any(op.id == used_operation.id for op in existing))
