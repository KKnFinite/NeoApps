"""Unified NeoApps account classification for Scorpion fuel truck drivers.

This changes access, never user identity or historical work. All transitions
must occur in an administrator-authorized database transaction.
"""
from datetime import datetime
from sqlalchemy import and_, func, or_

from app.extensions import db
from app.models import (
    User, Gateway, GatewayMembership, GatewayNodeRole, NeoNode, PortalAppAccess,
    NeoScorpionVendorAccount, NeoScorpionVendorNight,
    NeoScorpionSortFueler, NeoScorpionFuelAssignment,
)
from app.services.access_control import ensure_user_gateway_membership, ensure_user_app_access
from app.services.auth_session_security import rotate_user_session_version
from app.services import neoscorpion_vendors as vendors
from app.services.request_cache import clear_request_cache


def lock_account_grants(user):
    """Serialize staff grant edits and account classification in one order."""
    # The shared node lock also serializes checks that count active Grandmasters.
    NeoNode.query.with_entities(NeoNode.id).filter_by(code="motherbrain").with_for_update(key_share=True).first()
    user = User.query.filter_by(id=user.id).with_for_update(key_share=True).populate_existing().one()
    db.session.expire(user, ["scorpion_vendor"])
    clear_request_cache()
    return user


def _guard_last_grandmaster(user):
    if (user.role == "grandmaster" and User.query.filter_by(role="grandmaster", is_active=True).filter(
            ~NeoScorpionVendorAccount.query.filter(NeoScorpionVendorAccount.user_id == User.id).exists()
    ).count() <= 1):
        raise ValueError("Cannot convert the last active NeoApps Grandmaster.")
    active = GatewayNodeRole.query.join(GatewayMembership).join(NeoNode).join(
        User, User.id == GatewayMembership.user_id,
    ).outerjoin(PortalAppAccess, and_(PortalAppAccess.user_id == User.id,
                               PortalAppAccess.app_code == "neogateway"),
    ).filter(NeoNode.code == "motherbrain", NeoNode.is_active.is_(True),
        GatewayNodeRole.role == "grandmaster", GatewayNodeRole.is_active.is_(True),
        GatewayMembership.status == "approved", GatewayMembership.is_active.is_(True), User.is_active.is_(True),
        or_(PortalAppAccess.id.is_(None), and_(
            PortalAppAccess.status == "approved", PortalAppAccess.is_active.is_(True))),
        ~NeoScorpionVendorAccount.query.filter(NeoScorpionVendorAccount.user_id == User.id).exists())
    gateway_ids = active.filter(GatewayMembership.user_id == user.id).with_entities(GatewayMembership.gateway_id)
    if active.filter(GatewayMembership.gateway_id.in_(gateway_ids)).with_entities(GatewayMembership.gateway_id).group_by(
            GatewayMembership.gateway_id).having(func.count() <= 1).first():
        raise ValueError("Cannot remove the last active Grandmaster gateway access.")


def convert_account(gateway, actor, user, account_type, *, can_change_role=None, authorize=None):
    """Classify an existing identity; never issue a second login or email identity.

    * vendor: approve the identity for restricted driver use, revoke all app/
      node rights and retain current-sort dispatcher authorization as separate.
    * full: remove vendor-mode marker, revoke nightly activity and require
      normal staff/app/node approval before full NeoApps access is granted.
    """
    account_type = 'fuel_driver' if account_type == 'vendor' else account_type
    if account_type not in {"fuel_driver", "vendor_supervisor", "full"}:
        raise ValueError("Choose Full NeoApps, Fuel Truck Driver or Vendor Supervisor.")
    if actor is None or user is None or actor.id == user.id:
        raise ValueError("An administrator cannot change their own account type.")
    # Match the nightly driver mutation lock order (Sort first, then
    # vendor/identity rows) so conversion cannot deadlock against a live claim.
    from app.services.neoscorpion import current_sort_operation
    from app.services.neoscorpion_assets import lock_nightly_asset_scope_for_mutation, record_nightly_operational_change
    scopes = Gateway.query.filter_by(is_active=True).all() if account_type != "full" else [gateway]

    def active_operations():
        candidates = [current_sort_operation(scope) for scope in scopes]
        return {item.id: item for item in candidates if item and not item.archived_at_utc}

    active = active_operations()
    locked = [lock_nightly_asset_scope_for_mutation(active[operation_id]) for operation_id in sorted(active)]
    operation, state = next(((op, rev) for op, rev in locked if op.gateway_id == gateway.id), (None, None))

    user = lock_account_grants(user)
    if authorize is not None:
        authorize(user)
    if active.keys() != active_operations().keys():
        raise ValueError("The active Sort changed. Refresh the account and try again.")
    if not user.is_active:
        raise ValueError("Activate this user before changing their account type.")
    if not user.email_verified_at:
        raise ValueError("Verify this user's email before changing account type.")

    profile = NeoScorpionVendorAccount.query.filter_by(
        user_id=user.id
    ).with_for_update().populate_existing().first()
    if profile is not None and profile.gateway_id != gateway.id:
        raise ValueError("Existing driver account belongs to a different gateway.")

    from app.services.fuel_driver_accounts import supervisor_membership
    prior_type = ('vendor_supervisor' if supervisor_membership(profile) else 'fuel_driver') if profile else 'full'
    if account_type != "full" and profile is not None and prior_type == account_type:
        if not profile.enabled:
            raise ValueError("Driver account is disabled; enable it in Nightly Trucks.")
        return False
    if account_type == "full" and profile is None:
        return False

    if account_type != "full":
        _guard_last_grandmaster(user)
        from app.services.account_scope_runtime import revoke_gateway_ranks
        revoke_gateway_ranks(user.id,'Account converted to restricted Fuel Truck Driver.',actor=actor)
        current_ids = [op.id for op, _rev in locked if not op.archived_at_utc]
        if NeoScorpionSortFueler.query.filter(
            NeoScorpionSortFueler.sort_date_operation_id.in_(current_ids), NeoScorpionSortFueler.user_id == user.id,
        ).first():
            raise ValueError(
                "Remove this user from Nightly Fuelers before converting to Fuel Truck Driver."
            )
        if NeoScorpionFuelAssignment.query.filter(
            NeoScorpionFuelAssignment.sort_date_operation_id.in_(current_ids),
            NeoScorpionFuelAssignment.assigned_fueler_user_id == user.id,
            NeoScorpionFuelAssignment.review_status != "complete",
        ).first():
            raise ValueError(
                "Complete or reassign unfinished fueling work before converting to Fuel Truck Driver."
            )
        if profile and prior_type != account_type:
            from app.models import NeoScorpionTruckDriver, NeoScorpionVendorRequest
            if (NeoScorpionTruckDriver.query.filter(NeoScorpionTruckDriver.sort_date_operation_id.in_(current_ids),
                    NeoScorpionTruckDriver.driver_user_id==user.id).first()
                or NeoScorpionVendorRequest.query.filter(NeoScorpionVendorRequest.sort_date_operation_id.in_(current_ids),
                    NeoScorpionVendorRequest.user_id==user.id,NeoScorpionVendorRequest.status=='pending').first()):
                raise ValueError('Dispatcher must release active truck claims and pending work before changing restricted account type.')

    now = datetime.utcnow()
    memberships = GatewayMembership.query.filter_by(user_id=user.id).populate_existing().all()
    membership = next((grant for grant in memberships if grant.gateway_id == gateway.id), None)
    if membership is None:
        membership = ensure_user_gateway_membership(user, gateway)
        memberships.append(membership)
    apps = PortalAppAccess.query.filter_by(user_id=user.id).populate_existing().all()
    if not any(grant.app_code == "neogateway" for grant in apps):
        # Otherwise a legacy approved gateway membership could silently
        # auto-seed an approved NeoGateway grant on a later access check.
        apps.append(ensure_user_app_access(user, "neogateway"))
    nodes = GatewayNodeRole.query.join(GatewayMembership).filter(
        GatewayMembership.user_id == user.id).populate_existing().all()

    if account_type != "full":
        roles = {user.role} | {grant.role for grant in (*apps, *nodes) if grant.is_active}
        if can_change_role and any(not can_change_role(role) for role in roles):
            raise ValueError("You cannot convert a user with a role equal to or higher than your own role.")
        # A former employee must not carry any implicit staff role into vendor
        # operation; historical role/audit rows remain but all grants are inert.
        for grant in apps:
            grant.status = "denied"
            grant.is_active = False
        for grant in nodes:
            grant.is_active = False
        for grant in memberships:
            if grant is not membership:
                grant.status = "denied"
                grant.is_active = False
        membership.status = "approved"
        membership.is_active = True
        membership.approved_by_user_id = actor.id
        membership.approved_at = now
        membership.denied_by_user_id = None
        membership.denied_at = None
        membership.denial_notes = None
        membership.approval_notes = "Restricted NeoScorpion Fuel Truck Driver account."
        user.role = "watcher"
        user.is_management = False
        user.management_level = None
        if profile is None:
            profile = NeoScorpionVendorAccount(user_id=user.id, gateway_id=gateway.id, invited_by_user_id=actor.id,enabled=True)
            db.session.add(profile)
        for op, revision in locked:
            night=db.session.get(NeoScorpionVendorNight,(op.id,user.id))
            if night and night.status in {'pending','approved'}:
                night.status='revoked';night.version+=1;night.changed_by_user_id=actor.id;night.updated_at=now
                record_nightly_operational_change(revision,op.id)
        vendors.audit(gateway, actor, "account_converted_vendor", subject=user,
                      identity_preserved=True, staff_grants_revoked=True,account_type=account_type,previous_account_type=prior_type)
    else:
        # A later conversion back to a restricted account must not resurrect
        # an earlier company's Supervisor capability without a new decision.
        from app.models import FuelVendorMembership, AccountSecurityEvent
        affiliation = FuelVendorMembership.query.filter_by(user_id=user.id, ended_at=None).with_for_update().first()
        if affiliation:
            affiliation.ended_at = now
            db.session.add(AccountSecurityEvent(actor_user_id=actor.id, subject_user_id=user.id,
                action="fuel_vendor_membership_closed_by_conversion", details={
                    "before_membership": affiliation.id, "after_membership": None,
                    "reason": "Restricted account converted to full NeoApps account."}))
        if operation is not None and not operation.archived_at_utc:
            vendors.disconnect(gateway, operation, actor, user_id=user.id)
            vendors.expire_user_requests(gateway, operation, actor, user.id,
                                         "account_converted_full")
            night = db.session.get(NeoScorpionVendorNight, (operation.id, user.id))
            if night is not None:
                night.status = "revoked"
                night.version = int(night.version or 0) + 1
                night.changed_by_user_id = actor.id
                night.updated_at = now
            record_nightly_operational_change(state, operation.id)
        # No automatic restoration of historical employee privileges.
        for grant in apps:
            grant.status = "pending" if grant.app_code == "neogateway" else "denied"
            grant.is_active = grant.app_code == "neogateway"
        for grant in nodes:
            grant.is_active = False
        for grant in memberships:
            if grant is not membership:
                grant.status = "denied"
                grant.is_active = False
        membership.status = "pending"
        membership.is_active = True
        membership.approved_by_user_id = None
        membership.approved_at = None
        membership.approval_notes = None
        membership.denied_by_user_id = None
        membership.denied_at = None
        membership.denial_notes = None
        membership.approval_email_sent_at = None
        user.role = "watcher"
        user.is_management = False
        user.management_level = None
        vendors.audit(gateway, actor, "account_converted_full", subject=user,
                      identity_preserved=True, staff_reapproval_required=True)
        db.session.delete(profile)

    # Missing node rows would otherwise be implicitly seeded on a later app
    # approval. An inactive row requires a separate explicit role decision.
    existing = {(grant.gateway_membership_id, grant.node_id) for grant in nodes}
    active_nodes = NeoNode.query.filter_by(is_active=True).all()
    for grant in memberships:
        for node in active_nodes:
            if (grant.id, node.id) not in existing:
                db.session.add(GatewayNodeRole(gateway_membership_id=grant.id, node_id=node.id,
                    role="watcher", is_active=False))

    # Existing browser sessions cannot continue with privileges cached from
    # the previous account classification.
    from app.models import FuelDriverIdentity
    identity=db.session.get(FuelDriverIdentity,user.id)
    if identity is None:
        identity=FuelDriverIdentity(user_id=user.id)
        db.session.add(identity)
    else:identity.version+=1
    identity.account_type=account_type
    identity.requested_driver=False
    rotate_user_session_version(user)
    db.session.flush()
    db.session.expire(user, ["scorpion_vendor"])
    clear_request_cache()
    return True
