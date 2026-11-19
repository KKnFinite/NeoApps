"""Platform-owned, audited verification for restricted Fuel Truck Driver accounts.

Company membership is explicit and dated. Unknown legacy affiliations stay unknown.
No staged scoped role is read or activated here. Callers own commit/rollback.
"""
from datetime import datetime
from hashlib import sha256
import re
import json

from flask_login import current_user
from sqlalchemy import text

from app.extensions import db
from app.models import FuelDriverIdentity, FuelVendorCompany, FuelVendorMembership, User, StaffingPerson, NeoScorpionVendorAccount, NeoScorpionVendorNight, NeoScorpionVendorRequest, NeoScorpionTruckDriver
from app.services.account_foundation import atomic_account_change, AccountConflict
from app.services.account_security import lock_administration, platform_grandmaster, record_security_change
from app.services.accounts_workspace import verify_review
from app.services.auth_session_security import rotate_user_session_version
from app.services.grandmaster_commissioning import ReauthenticationFailed


def ups_id(value):
    value = str(value or "").strip()
    if not value or len(value) > 80 or not value.isprintable():
        raise ValueError("Enter a valid UPS ID (1–80 printable characters). Leading zeros are preserved.")
    return value


def lock_identity_key(value):
    """Short transaction-only lock shared by signup and canonical ID correction."""
    if value and db.session.connection().dialect.name == "postgresql":
        key = int.from_bytes(sha256(("neoapps-employee-id:" + value.strip().lower()).encode()).digest()[:8], "big", signed=True)
        db.session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})


def guard_identity_unique(value, user_id=None):
    lock_identity_key(value)
    key = value.strip().lower()
    # Exact application normalization, without database-locale approximations.
    if any(id_ != user_id and str(stored or "").strip().lower() == key
           for id_, stored in db.session.query(User.id, User.employee_id).filter(User.employee_id.is_not(None)).all()):
        raise ValueError("That UPS / employee ID is already assigned to another account.")


def requested_driver(user):
    row = db.session.get(FuelDriverIdentity, user.id)
    return bool(row and row.requested_driver)


def identity_verified(user):
    row = db.session.get(FuelDriverIdentity, user.id)
    return bool(row and row.verified_by_user_id and row.verified_at and row.verified_ups_id
                and row.verified_ups_id == user.employee_id)


def current_membership(user_id):
    return FuelVendorMembership.query.filter_by(user_id=user_id, ended_at=None).populate_existing().first()


def supervisor_membership(account):
    if not account:
        return None
    identity = FuelDriverIdentity.query.filter_by(user_id=account.user_id).populate_existing().first()
    if identity and identity.account_type:
        return account if identity.account_type == 'vendor_supervisor' else None
    # Compatibility with an administrator's existing Supervisor grant. No
    # company verification, active-company or company-ownership authorization.
    member = current_membership(account.user_id)
    return member if member and member.is_supervisor else None


def require_supervisor(account):
    # Fresh on every HTML/API read, including capability and account revocation.
    account = NeoScorpionVendorAccount.query.filter_by(user_id=account.user_id).populate_existing().one_or_none()
    user = User.query.filter_by(id=account.user_id).populate_existing().one() if account else None
    supervisor = supervisor_membership(account) if user else None
    if not (account and account.enabled and user.is_active and user.email_verified_at and identity_verified(user)
            and supervisor and account.gateway.is_active):
        raise PermissionError("An enabled, verified Vendor Supervisor account is required.")
    return account


def _platform_actor():
    actor = lock_administration()
    if not platform_grandmaster(actor) or not actor.email_verified_at:
        raise ValueError("Active, verified platform administration is required.")
    return actor


def _reference(value):
    value = str(value or "").strip()
    if not 10 <= len(value) <= 1000:
        raise ValueError("A verification reference and reason of 10–1000 characters is required.")
    return value


@atomic_account_change
def verify_ups_id(user_id, token, value, reference, confirmed_person_id=None):
    actor = _platform_actor()
    user = User.query.filter_by(id=user_id).populate_existing().with_for_update().one()
    verify_review(user, token)
    if user.role == "grandmaster" or user.id == actor.id:
        raise ValueError("A protected administrator identity requires its existing recovery procedure.")
    value, reference = ups_id(value), _reference(reference)
    guard_identity_unique(value, user.id)
    old_key, new_key = str(user.employee_id or "").strip().lower(), value.strip().lower()
    # Never reattribute a different Staffing person's history or leadership.
    people = [(id_, employee_id.strip().lower()) for id_, employee_id in db.session.query(StaffingPerson.id,StaffingPerson.employee_id).all()
              if employee_id.strip().lower() in {old_key, new_key}]
    old_people = {id_ for id_, key in people if key == old_key}
    new_people = {id_ for id_, key in people if key == new_key}
    if old_people and old_people != new_people:
        raise ValueError("This correction changes a NeoStaffing identity link. Stop for protected person-link review.")
    if new_people and (len(new_people) != 1 or str(next(iter(new_people))) != str(confirmed_person_id or "")):
        raise ValueError("Verify and explicitly confirm the linked NeoStaffing Person ID before saving this UPS ID.")
    row = db.session.get(FuelDriverIdentity, user.id)
    if row is None:
        row = FuelDriverIdentity(user_id=user.id)
        db.session.add(row)
    else:
        row.version += 1
    before = user.employee_id
    user.employee_id = value
    row.verified_ups_id = value
    row.verified_by_user_id = actor.id
    row.verified_at = datetime.utcnow()
    row.verification_reference = reference
    rotate_user_session_version(user)
    record_security_change("fuel_driver_identity_verified", user, before=before, after=value,
        reason=reference, staffing_person_id=next(iter(new_people)) if new_people else None,
        conversion_performed=False)
    db.session.flush()


@atomic_account_change
def create_company(gateway_id, name, reference):
    actor = _platform_actor()
    from app.models import Gateway
    gateway = db.session.get(Gateway, gateway_id)
    name, reference = " ".join(str(name or "").split()), _reference(reference)
    if not gateway or not gateway.is_active or not 1 <= len(name) <= 160:
        raise ValueError("Choose an active gateway and a valid verified company name.")
    row = FuelVendorCompany(gateway_id=gateway_id, name=name, name_key=name.lower(),
        verified_by_user_id=actor.id, verification_reference=reference)
    db.session.add(row)
    db.session.flush()
    record_security_change("fuel_vendor_company_verified", actor, company_id=row.id, gateway_id=gateway_id, reason=reference)
    return row


@atomic_account_change
def assign_company(user_id, token, company_id, supervisor, reference, password, confirmed_user_id):
    # Match driver/conversion locking: current Sort before administration/user rows.
    account = db.session.get(NeoScorpionVendorAccount, user_id)
    if not account:
        raise ValueError("An approved restricted Fuel Truck Driver account is required.")
    from app.services.neoscorpion import current_sort_operation
    from app.services.neoscorpion_assets import lock_nightly_asset_scope_for_mutation
    operation = current_sort_operation(account.gateway)
    if operation:
        operation, _ = lock_nightly_asset_scope_for_mutation(operation)
        from app.services.neoscorpion_vendors import current_operation
        current_operation(account.gateway, operation.id)
    actor = _platform_actor()
    user = User.query.filter_by(id=user_id).populate_existing().with_for_update().one()
    verify_review(user, token)
    account = NeoScorpionVendorAccount.query.filter_by(user_id=user_id).populate_existing().one()
    if user.id == actor.id or company_id and (not user.is_active or not user.email_verified_at or not account.enabled or not identity_verified(user)):
        raise ValueError("Verify the enabled restricted account and its UPS ID first.")
    reference = _reference(reference)
    company = FuelVendorCompany.query.filter_by(id=company_id).populate_existing().with_for_update().one_or_none() if company_id else None
    if company_id and (not company or not company.is_active or company.gateway_id != account.gateway_id):
        raise ValueError("Select an active verified company at this account's gateway.")
    if supervisor and not company:
        raise ValueError("Vendor Supervisor requires a verified company.")
    if str(confirmed_user_id) != str(user_id) or actor.mfa_required or actor.mfa_enabled:
        raise ValueError("Confirm the target account number and reauthenticate with your current password.")
    if not isinstance(password, str) or len(password) > 1024 or not actor.check_password(password):
        raise ReauthenticationFailed("Current password could not be verified. No change was made.")
    if operation and supervisor:
        night = db.session.get(NeoScorpionVendorNight, (operation.id, user_id))
        connected = NeoScorpionTruckDriver.query.filter_by(sort_date_operation_id=operation.id, driver_user_id=user_id).first()
        pending = NeoScorpionVendorRequest.query.filter_by(sort_date_operation_id=operation.id, user_id=user_id, status="pending").first()
        if connected or pending or night and night.status == "approved":
            raise ValueError("Dispatcher must release current driver participation and pending claims before assigning read-only Supervisor access.")
    prior = current_membership(user_id)
    if prior and company and prior.company_id == company.id and prior.is_supervisor == supervisor:
        raise AccountConflict("This company and capability are already assigned.")
    if prior:
        prior.ended_at = datetime.utcnow()
        db.session.flush()
    member = None
    if company:
        member = FuelVendorMembership(user_id=user_id, company_id=company.id, is_supervisor=supervisor,
            verified_by_user_id=actor.id, verification_reference=reference)
        db.session.add(member)
        db.session.flush()
    rotate_user_session_version(user)
    record_security_change("fuel_vendor_membership_changed", user, before_membership=prior.id if prior else None,
        after_membership=member.id if member else None, company_id=company.id if company else None,
        supervisor=supervisor, reason=reference, reauthentication="current_password")
    return member


@atomic_account_change
def set_company_active(company_id, active, expected_version, reference, password):
    actor = _platform_actor()
    if actor.mfa_required or actor.mfa_enabled:
        raise ValueError("This account requires an unsupported verification step.")
    if not isinstance(password, str) or len(password) > 1024 or not actor.check_password(password):
        raise ReauthenticationFailed("Current password could not be verified. No change was made.")
    company = FuelVendorCompany.query.filter_by(id=company_id).populate_existing().with_for_update().one()
    if str(company.version) != str(expected_version):
        raise AccountConflict("Company verification changed. Reload first.")
    company.is_active = bool(active)
    company.version += 1
    record_security_change("fuel_vendor_company_status_changed", actor, company_id=company.id,
        active=company.is_active, reason=_reference(reference))


def supervisor_context(account):
    """Gateway-scoped read-only operations; never a Dispatch/Fueler context."""
    from sqlalchemy import or_
    from app.models import NeoScorpionFuelTruck, NeoScorpionFuelAssignment, NeoScorpionFuelWorkState, SortDateMission, SortDateParkingAssignment
    from app.services.neoscorpion import current_sort_operation
    require_supervisor(account)
    operation = current_sort_operation(account.gateway)
    rows = []
    if operation and not operation.archived_at_utc:
        accounts = NeoScorpionVendorAccount.query.filter(
            NeoScorpionVendorAccount.gateway_id == account.gateway_id, NeoScorpionVendorAccount.enabled.is_(True)).all()
        people = {a.user_id: a.user for a in accounts if a.user.is_active and a.user.email_verified_at and identity_verified(a.user) and not supervisor_membership(a)}
        connections = {r.driver_user_id: r for r in NeoScorpionTruckDriver.query.filter(
            NeoScorpionTruckDriver.sort_date_operation_id == operation.id, NeoScorpionTruckDriver.driver_user_id.in_(list(people))).all()}
        trucks = {t.id: t for t in NeoScorpionFuelTruck.query.filter(
            NeoScorpionFuelTruck.id.in_([r.fuel_truck_id for r in connections.values()]),
            NeoScorpionFuelTruck.gateway_id == account.gateway_id).all()}
        nights = {n.user_id: n for n in NeoScorpionVendorNight.query.filter(
            NeoScorpionVendorNight.sort_date_operation_id == operation.id, NeoScorpionVendorNight.user_id.in_(list(people))).all()}
        for user_id, user in people.items():
            night = nights.get(user_id)
            connection = connections.get(user_id) if night and night.status == "approved" else None
            truck = trucks.get(connection.fuel_truck_id) if connection else None
            missions = []
            if truck:
                fields = db.session.query(SortDateMission.id, SortDateMission.assigned_tail_number,
                    SortDateMission.flight_number, SortDateMission.destination, SortDateParkingAssignment.ramp_code,
                    SortDateParkingAssignment.position_code, SortDateMission.eta_datetime_utc,
                    SortDateMission.planned_datetime_utc).join(NeoScorpionFuelAssignment,
                        NeoScorpionFuelAssignment.sort_date_mission_id == SortDateMission.id).outerjoin(
                        NeoScorpionFuelWorkState, NeoScorpionFuelWorkState.id == NeoScorpionFuelAssignment.active_work_state_id).outerjoin(
                        SortDateParkingAssignment, (SortDateParkingAssignment.sort_date_operation_id == operation.id)
                        & (SortDateParkingAssignment.tail_number == SortDateMission.assigned_tail_number)).filter(
                        SortDateMission.sort_date_operation_id == operation.id, SortDateMission.gateway_code == account.gateway.code,
                        NeoScorpionFuelAssignment.sort_date_operation_id == operation.id,
                        NeoScorpionFuelAssignment.review_status != "complete",
                        or_(NeoScorpionFuelAssignment.assigned_truck_id == truck.id, NeoScorpionFuelWorkState.physical_truck_id == truck.id)
                    ).order_by(SortDateMission.eta_datetime_utc, SortDateMission.id).all()
                missions = [dict(id=r.id, tail=r.assigned_tail_number, flight=r.flight_number,
                    destination=r.destination, parking=(r.ramp_code or '')+(r.position_code or '') or None,
                    etd=(r.eta_datetime_utc or r.planned_datetime_utc).isoformat() if r.eta_datetime_utc or r.planned_datetime_utc else None) for r in fields]
            rows.append(dict(user_id=user_id, name=user.display_name, status=night.status if night else "pending",
                truck=truck.truck_number if truck else None, missions=missions))
        rows.sort(key=lambda r: (r["name"].lower(), r["user_id"]))
    payload = dict(gateway=account.gateway.code, operation_id=operation.id if operation and not operation.archived_at_utc else None,
        sort_label=f"{operation.sort_name} {operation.sort_date}" if operation and not operation.archived_at_utc else "NO ACTIVE SORT", rows=rows)
    revision = sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return dict(supervisor=payload, fingerprint={"supervisor_revision": revision, "operation_id": payload["operation_id"]})
