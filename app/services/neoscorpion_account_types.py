"""Unified NeoApps account classification for Scorpion truck drivers.

This changes access, never user identity or historical work. All transitions
must occur in an administrator-authorized database transaction.
"""
from datetime import datetime

from app.extensions import db
from app.models import (
    User, GatewayMembership, GatewayNodeRole, PortalAppAccess,
    NeoScorpionVendorAccount, NeoScorpionVendorNight,
    NeoScorpionSortFueler, NeoScorpionFuelAssignment,
)
from app.services.access_control import ensure_user_gateway_membership, ensure_user_app_access
from app.services.auth_session_security import rotate_user_session_version
from app.services import neoscorpion_vendors as vendors


def convert_account(gateway, actor, user, account_type):
    """Classify an existing identity; never issue a second login or email identity.

    * vendor: approve the identity for restricted driver use, revoke all app/
      node rights and retain current-sort dispatcher authorization as separate.
    * full: remove vendor-mode marker, revoke nightly activity and require
      normal staff/app/node approval before full NeoApps access is granted.
    """
    if account_type not in {"vendor", "full"}:
        raise ValueError("Choose FULL NEO ACCOUNT or TRUCK DRIVER.")
    if actor is None or user is None or actor.id == user.id:
        raise ValueError("An administrator cannot change their own account type.")
    if not user.is_active:
        raise ValueError("Activate this user before changing their account type.")
    if not user.email_verified_at:
        raise ValueError("Verify this user's email before changing account type.")

    # Match the nightly driver mutation lock order (Sort first, then
    # vendor/identity rows) so conversion cannot deadlock against a live claim.
    operation, state = None, None
    if account_type in {"vendor", "full"}:
        from app.services.neoscorpion import current_sort_operation
        from app.services.neoscorpion_assets import (
            lock_nightly_asset_scope_for_mutation,
            record_nightly_operational_change,
        )
        operation = current_sort_operation(gateway)
        if operation is not None and not operation.archived_at_utc:
            operation, state = lock_nightly_asset_scope_for_mutation(operation)

    membership = GatewayMembership.query.filter_by(
        gateway_id=gateway.id, user_id=user.id
    ).with_for_update().first()
    if membership is None:
        membership = ensure_user_gateway_membership(user, gateway)
        db.session.flush()

    profile = NeoScorpionVendorAccount.query.filter_by(
        user_id=user.id
    ).with_for_update().first()
    if profile is not None and profile.gateway_id != gateway.id:
        raise ValueError("Existing driver account belongs to a different gateway.")

    if account_type == "vendor" and profile is not None:
        if not profile.enabled:
            raise ValueError("Driver account is disabled; enable it in Nightly Trucks.")
        return False
    if account_type == "full" and profile is None:
        return False

    if account_type == "vendor" and operation is not None and not operation.archived_at_utc:
        if NeoScorpionSortFueler.query.filter_by(
            sort_date_operation_id=operation.id, user_id=user.id,
        ).first():
            raise ValueError(
                "Remove this user from Nightly Fuelers before converting to Truck Driver."
            )
        if NeoScorpionFuelAssignment.query.filter_by(
            sort_date_operation_id=operation.id, assigned_fueler_user_id=user.id,
        ).filter(NeoScorpionFuelAssignment.review_status != "complete").first():
            raise ValueError(
                "Complete or reassign unfinished fueling work before converting to Truck Driver."
            )

    now = datetime.utcnow()
    apps = PortalAppAccess.query.filter_by(user_id=user.id).with_for_update().all()
    if not any(grant.app_code == "neogateway" for grant in apps):
        # Otherwise a legacy approved gateway membership could silently
        # auto-seed an approved NeoGateway grant on a later access check.
        apps.append(ensure_user_app_access(user, "neogateway"))
    nodes = GatewayNodeRole.query.filter_by(
        gateway_membership_id=membership.id
    ).with_for_update().all()

    if account_type == "vendor":
        # A former employee must not carry any implicit staff role into vendor
        # operation; historical role/audit rows remain but all grants are inert.
        for grant in apps:
            grant.status = "denied"
            grant.is_active = False
        for grant in nodes:
            grant.is_active = False
        membership.status = "approved"
        membership.is_active = True
        membership.approved_by_user_id = actor.id
        membership.approved_at = now
        membership.denied_by_user_id = None
        membership.denied_at = None
        membership.denial_notes = None
        membership.approval_notes = "Restricted NeoScorpion Truck Driver account."
        user.role = "watcher"
        user.is_management = False
        user.management_level = None
        profile = NeoScorpionVendorAccount(
            user_id=user.id, gateway_id=gateway.id, invited_by_user_id=actor.id,
            enabled=True,
        )
        db.session.add(profile)
        vendors.audit(gateway, actor, "account_converted_vendor", subject=user,
                      identity_preserved=True, staff_grants_revoked=True)
    else:
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
        membership.status = "pending"
        membership.is_active = True
        membership.approved_by_user_id = None
        membership.approved_at = None
        membership.approval_notes = None
        membership.denied_by_user_id = None
        membership.denied_at = None
        membership.denial_notes = None
        user.role = "watcher"
        user.is_management = False
        user.management_level = None
        vendors.audit(gateway, actor, "account_converted_full", subject=user,
                      identity_preserved=True, staff_reapproval_required=True)
        db.session.delete(profile)

    # Existing browser sessions cannot continue with privileges cached from
    # the previous account classification.
    rotate_user_session_version(user)
    db.session.flush()
    return True
