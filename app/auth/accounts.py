"""Unified Accounts & Access presentation over the existing security services."""
from uuid import uuid4
from urllib.parse import urlencode

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import exists, func, literal, or_, select, union_all
from sqlalchemy.exc import SQLAlchemyError

from app.auth import bp
from app.auth import routes as legacy
from app.extensions import db
from app.models import AccountAccessDecision, AccountAccessRequest, AccountSecurityEvent, GatewayMembership, GatewayNodeRole, NeoNode, PortalAppAccess, User, FuelDriverIdentity, FuelVendorMembership
from app.models.user import MANAGEMENT_LEVELS
from app.services.account_foundation import AccountConflict
from app.services.account_security import guard_node_change, lock_administration, platform_grandmaster, record_security_change
from app.services.access_control import get_current_gateway
from app.services.auth_session_security import rotate_user_session_version
from app.services.accounts_workspace import (CRYOCHECK_URL, authorized_applications, available_applications,
    application_decision_reason, close_open_attempt, lock_self, new_attempt, preference, require_available_app, review_token, save_preferences, verify_review)
from app.services.grandmaster_commissioning import may_commission
from app.services.permission_rules import user_can
from app.services.request_cache import clear_request_cache


@bp.app_context_processor
def account_pagination_context():
    def pagination_query(page):
        args = request.args.to_dict()
        args["page"] = page
        return urlencode(args)
    return {"pagination_query": pagination_query}


def _tabs(active):
    choices = [("people", "People", "auth.accounts_people", legacy.USER_MANAGEMENT_VIEW_PERMISSION),
               ("requests", "Requests", "auth.accounts_requests", legacy.ACCESS_REQUESTS_VIEW_PERMISSION)]
    rows = [(key, label, url_for(endpoint)) for key, label, endpoint, permission in choices if user_can(permission)]
    if platform_grandmaster(current_user):
        rows.append(("security", "Security & Permissions", url_for("auth.accounts_security")))
    return dict(workspace_tabs=rows, active_tab=active, browser_tab_title="Accounts & Access | NeoApps",
        can_search_people=user_can(legacy.USER_MANAGEMENT_VIEW_PERMISSION))


def _page():
    return max(1, request.args.get("page", 1, type=int))


def _decisions(attempts):
    return {decision.request_id: {"reason": decision.reason, "outcome": decision.outcome,
            "actor": actor, "at": decision.created_at}
        for decision, actor in db.session.query(AccountAccessDecision, AccountSecurityEvent.actor_user_id).join(
            AccountSecurityEvent, AccountSecurityEvent.id == AccountAccessDecision.security_event_id).filter(
            AccountAccessDecision.request_id.in_([a.id for a in attempts])).all()}


@bp.get("/accounts")
@legacy.portal_permission_required(legacy.USER_MANAGEMENT_VIEW_PERMISSION)
def accounts_people():
    from app.services.account_deletion import visible_accounts
    query = User.query.filter(visible_accounts())
    search = request.args.get("q", "").strip()[:160]
    state = request.args.get("status", "all")
    kind = request.args.get("type", "all")
    app = request.args.get("app", "")
    if search:
        pattern = "%" + search.replace("%", "\\%").replace("_", "\\_") + "%"
        query = query.filter(or_(*[field.ilike(pattern, escape="\\") for field in
            (User.first_name, User.last_name, User.full_name, User.email, User.employee_id, User.username)]))
    if state == "active":
        query = query.filter(User.is_active.is_(True))
    elif state == "inactive":
        query = query.filter(User.is_active.is_(False))
    elif state == "unverified":
        query = query.filter(User.email_verified_at.is_(None))
    from app.services.account_types import TYPES
    supervisor = exists().where(FuelDriverIdentity.user_id==User.id, FuelDriverIdentity.account_type=='vendor_supervisor')
    legacy_supervisor = exists().where(FuelVendorMembership.user_id==User.id, FuelVendorMembership.ended_at.is_(None), FuelVendorMembership.is_supervisor.is_(True)) & ~exists().where(FuelDriverIdentity.user_id==User.id, FuelDriverIdentity.account_type.is_not(None))
    if kind in {'vendor','full','fuel_driver','vendor_supervisor'}:
        query=query.filter(~User.scorpion_vendor.has() if kind=='full' else User.scorpion_vendor.has())
        if kind=='vendor_supervisor':query=query.filter(supervisor | legacy_supervisor)
        elif kind=='fuel_driver':query=query.filter(~(supervisor | legacy_supervisor))
    if app in {a["code"] for a in available_applications()}:
        query = query.filter(User.portal_app_accesses.any(app_code=app, status="approved", is_active=True))
    page = query.order_by(User.last_name.asc(), User.first_name.asc(), User.id.asc()).paginate(page=_page(), per_page=25, error_out=False)
    users = page.items
    grants = {}
    for access in PortalAppAccess.query.filter(PortalAppAccess.user_id.in_([u.id for u in users])).all():
        grants.setdefault(access.user_id, []).append(access)
    ids=[u.id for u in users]
    identities={r.user_id:r.account_type for r in FuelDriverIdentity.query.filter(FuelDriverIdentity.user_id.in_(ids)).all()}
    legacy_supervisors={r.user_id for r in FuelVendorMembership.query.filter(FuelVendorMembership.user_id.in_(ids),FuelVendorMembership.ended_at.is_(None),FuelVendorMembership.is_supervisor.is_(True)).all()}
    types={u.id:('vendor_supervisor' if identities.get(u.id)=='vendor_supervisor' or identities.get(u.id) is None and u.id in legacy_supervisors else 'fuel_driver') if u.scorpion_vendor else 'full' for u in users}
    return render_template("auth/accounts/people.html", page=page, people=users, grants=grants,account_types=types,account_type_labels=TYPES,
        search=search, selected_status=state, selected_type=kind, selected_app=app,
        applications=available_applications(), **_tabs("people"))


@bp.get("/accounts/requests")
@legacy.portal_permission_required(legacy.ACCESS_REQUESTS_VIEW_PERMISSION)
def accounts_requests():
    state = request.args.get("status", "pending")
    if state not in {"pending", "approved", "denied", "all"}:
        state = "pending"
    app = request.args.get("app", "")
    codes = [a["code"] for a in available_applications()]
    attempts = select(AccountAccessRequest.user_id.label("user_id"), AccountAccessRequest.app_code.label("app_code"),
        AccountAccessRequest.state.label("state"), AccountAccessRequest.created_at.label("created_at"),
        literal("request").label("source"), AccountAccessRequest.id.label("reference"))
    # Old pending rows are shown honestly, without creating historical attempts on GET.
    old = select(PortalAppAccess.user_id, PortalAppAccess.app_code, PortalAppAccess.status.label("state"),
        PortalAppAccess.created_at, literal("legacy").label("source"), PortalAppAccess.id.label("reference")).where(
            PortalAppAccess.is_active.is_(True),
            ~exists().where(AccountAccessRequest.user_id == PortalAppAccess.user_id,
                AccountAccessRequest.app_code == PortalAppAccess.app_code))
    memberships = select(GatewayMembership.user_id, literal("neogateway").label("app_code"), GatewayMembership.status.label("state"),
        GatewayMembership.created_at, literal("membership").label("source"), GatewayMembership.id.label("reference")).where(
            GatewayMembership.gateway_id == get_current_gateway().id, GatewayMembership.status == "pending", GatewayMembership.is_active.is_(True),
            ~exists().where(PortalAppAccess.user_id == GatewayMembership.user_id, PortalAppAccess.app_code == "neogateway"),
            ~exists().where(AccountAccessRequest.user_id == GatewayMembership.user_id, AccountAccessRequest.app_code == "neogateway", AccountAccessRequest.state == "pending"))
    source = union_all(attempts, old, memberships).subquery()
    from app.services.account_deletion import visible_accounts
    query = db.session.query(source, User).join(User, User.id == source.c.user_id).filter(visible_accounts(),
        source.c.app_code.in_(codes), ~User.scorpion_vendor.has())
    pending_counts = dict(db.session.query(source.c.app_code, func.count()).join(User, User.id == source.c.user_id).filter(
        source.c.app_code.in_(codes), source.c.state == "pending", ~User.scorpion_vendor.has(),visible_accounts()).group_by(source.c.app_code).all())
    search = request.args.get("q", "").strip()[:160]
    if search:
        pattern = "%" + search.replace("%", "\\%").replace("_", "\\_") + "%"
        query = query.filter(or_(User.email.ilike(pattern, escape="\\"), User.full_name.ilike(pattern, escape="\\"), User.first_name.ilike(pattern, escape="\\"), User.last_name.ilike(pattern, escape="\\")))
    if state != "all":
        query = query.filter(source.c.state == state)
    if app in codes:
        query = query.filter(source.c.app_code == app)
    page = query.order_by(source.c.created_at.asc(), source.c.user_id.asc(), source.c.reference.asc()).paginate(page=_page(), per_page=25, error_out=False)
    return render_template("auth/accounts/requests.html", page=page, requests=page.items,
        selected_status=state, selected_app=app, search=search, pending_counts=pending_counts,
        applications=available_applications(), **_tabs("requests"))


@bp.get("/accounts/security")
@login_required
def accounts_security():
    if not platform_grandmaster(current_user):
        abort(403)
    events = AccountSecurityEvent.query.order_by(AccountSecurityEvent.id.desc()).paginate(page=_page(), per_page=25, error_out=False)
    return render_template("auth/accounts/security.html", page=events, **_tabs("security"))


def _profile(user_id, status=200, error=None):
    target = db.get_or_404(User, user_id)
    from app.services.account_deletion import receipt, can_delete
    deletion=receipt(target.id)
    from app.services.account_types import administrator,current_type,requested_type,TYPES
    kind=current_type(target)
    gateway = get_current_gateway()
    membership = GatewayMembership.query.filter_by(user_id=target.id, gateway_id=gateway.id).first()
    app_rows = legacy._portal_app_access_rows(target)
    attempts = AccountAccessRequest.query.filter_by(user_id=target.id).order_by(AccountAccessRequest.id.desc()).limit(40).all()
    events = AccountSecurityEvent.query.filter_by(subject_user_id=target.id).order_by(AccountSecurityEvent.id.desc()).limit(40).all()
    from app.services.fuel_driver_accounts import identity_verified
    tab = request.args.get("tab", "identity")
    if tab not in {"identity", "access", "security", "history"}:
        tab = "identity"
    return render_template("auth/accounts/profile.html", target_user=target, gateway=gateway, membership=membership,
        node_rows=legacy._node_role_rows(target, membership), app_access_rows=app_rows,
        attempts=attempts, decisions=_decisions(attempts), events=events, profile_tab=tab, review=review_token(target),
        form=legacy._user_edit_form_from_request(target),
        management_level_choices=MANAGEMENT_LEVELS, role_choices=legacy.ROLE_CHOICES,
        can_edit_user=not deletion and user_can(legacy.USER_MANAGEMENT_EDIT_PERMISSION),
        can_edit_access=not deletion and user_can(legacy.PORTAL_MANAGEMENT_EDIT_PERMISSION),
        can_resend=not deletion and user_can(legacy.EMAIL_VERIFICATION_RESEND_PERMISSION),
        driver_identity=db.session.get(FuelDriverIdentity, target.id), ups_verified=identity_verified(target),
        can_commission_grandmaster=not deletion and may_commission(current_user, target),
        can_delete_account=can_delete(current_user), deletion=deletion, error=error,
        account_type_labels=TYPES,current_account_type=kind,
        selected_account_type=requested_type(target) if driver_identity_pending(target) else kind,
        can_decide_account_type=not deletion and administrator(current_user) and current_user.id!=target.id and target.role!='grandmaster',
        **_tabs("people")), status


def driver_identity_pending(target):
    row=db.session.get(FuelDriverIdentity,target.id)
    return bool(row and row.requested_driver)


@bp.post('/accounts/people/<int:user_id>/account-type')
@login_required
def account_type_decision(user_id):
    from app.services.account_types import administrator,decide
    if not administrator(current_user):abort(403)
    db.get_or_404(User,user_id)
    try:
        decide(user_id,request.form.get('review_token',''),request.form.get('account_type'),request.form.get('outcome'),
            request.form.get('employee_id'),request.form.get('identity_confirmed'),request.form.get('staffing_person_id'),
            request.form.getlist('app_codes'),{app['code']:request.form.get('role_'+app['code'],'watcher') for app in available_applications()},
            request.form.get('reason',''))
        db.session.commit()
    except PermissionError:
        db.session.rollback();abort(403)
    except (ValueError,SQLAlchemyError) as error:
        db.session.rollback()
        return _profile(user_id,409 if isinstance(error,AccountConflict) else 400 if isinstance(error,ValueError) else 503,
            error=str(error) if isinstance(error,ValueError) else 'No changes saved. Please retry.')
    flash('Account decision saved. Identity and operational history preserved.','success')
    return redirect(url_for('auth.account_profile',user_id=user_id,tab='access'))


@bp.post("/accounts/people/<int:user_id>/delete")
@login_required
def delete_account(user_id):
    from app.services.account_deletion import delete_account as remove, can_delete
    if not can_delete(current_user):
        abort(403)
    db.get_or_404(User,user_id)
    try:
        target=remove(user_id,request.form.get('review_token',''),
            request.form.get('confirm_delete'),request.form.get('confirmed_user_id',''))
        db.session.commit()
    except PermissionError:
        db.session.rollback();abort(403)
    except (ValueError,SQLAlchemyError) as error:
        db.session.rollback()
        message=str(error) if isinstance(error,ValueError) else 'Account deletion failed. No changes saved.'
        return _profile(user_id,409 if isinstance(error,AccountConflict) else 400 if isinstance(error,ValueError) else 500,error=message)
    flash(f'Account {target.id} deleted. Login and access revoked; historical records preserved.','success')
    return redirect(url_for('auth.accounts_people'))


@bp.get("/accounts/people/<int:user_id>")
@legacy.portal_permission_required(legacy.USER_MANAGEMENT_VIEW_PERMISSION)
def account_profile(user_id):
    return _profile(user_id)


@bp.post("/accounts/people/<int:user_id>/<action>")
@login_required
def account_action(user_id, action):
    permission = legacy.USER_MANAGEMENT_EDIT_PERMISSION if action == "identity" else legacy.PORTAL_MANAGEMENT_EDIT_PERMISSION
    if not user_can(permission):
        abort(403)
    target = db.get_or_404(User, user_id)
    try:
        actor = lock_administration()
        if not user_can(permission, user=actor):
            abort(403)
        target = legacy._lock_staff_account(target)
        verify_review(target, request.form.get("review_token", ""))
        if action == "identity":
            legacy._apply_user_edit_form(target, legacy._user_edit_form_from_request(target))
            record_security_change("account_identity_updated", target, fields=["name", "email", "employee_id", "work_area", "management"])
        elif action == "application":
            code = request.form.get("app_code", "")
            require_available_app(code)
            access = PortalAppAccess.query.filter_by(user_id=target.id, app_code=code).with_for_update().first()
            outcome = request.form.get("outcome", "")
            reason = application_decision_reason(outcome, request.form.get("reason", ""))
            pending = AccountAccessRequest.query.filter_by(user_id=target.id, app_code=code, state="pending").first()
            if current_user.id == target.id:
                raise ValueError("You cannot decide your own application access.")
            if outcome in {"approved", "denied"}:
                if not access:
                    membership = legacy._current_gateway_membership_for_user(target, get_current_gateway()) if code == "neogateway" else None
                    if not pending and not (membership and membership.status == "pending"):
                        raise ValueError("No application request exists for this account.")
                    access = PortalAppAccess(user_id=target.id, app_code=code, role="watcher", status="pending", is_active=True)
                    db.session.add(access)
                    db.session.flush()
                before = {"status": access.status, "role": access.role, "active": access.is_active}
                if outcome == "approved":
                    if not target.is_active:
                        raise ValueError("An inactive account cannot be approved.")
                    legacy._approve_portal_app_access(access, request.form.get("role", access.role), reason,
                        bypass_email_verification=legacy._email_verification_bypass_requested())
                else:
                    legacy._deny_portal_app_access(access, reason)
                close_open_attempt(target, code, outcome, reason)
                record_security_change("application_access_changed", target, app_code=code, before=before,
                    after={"status": access.status, "role": access.role, "active": access.is_active}, reason=reason)
            elif outcome == "revoked" and access:
                from app.services.account_security import guard_app_role_change
                guard_app_role_change(target, access, access.role, granting=False)
                if code == "neogateway":
                    from app.services.account_scope_runtime import revoke_gateway_ranks
                    revoke_gateway_ranks(target.id,reason)
                    legacy._guard_last_grandmaster_gateway_change(target)
                    membership = legacy._current_gateway_membership_for_user(target, get_current_gateway())
                    if membership:
                        membership.is_active = False
                access.is_active = False
                close_open_attempt(target, code, "denied", reason)
                record_security_change("application_access_revoked", target, app_code=code, reason=reason)
            else:
                raise ValueError("Choose a valid application decision.")
            rotate_user_session_version(target)
        elif action == "node":
            node = db.get_or_404(NeoNode, request.form.get("node_id", type=int))
            membership = legacy._current_gateway_membership_for_user(target, get_current_gateway())
            if not legacy._membership_is_approved_active(membership) or not node.is_active:
                raise ValueError("Active gateway membership and node are required.")
            existing = GatewayNodeRole.query.filter_by(gateway_membership_id=membership.id, node_id=node.id).with_for_update().first()
            role = request.form.get("role", "")
            effective = existing or legacy.SimpleNamespace(role=legacy._neogateway_seed_role_for_membership(target, membership), is_active=True)
            guard_node_change(actor, target, node, effective, role)
            if role != "disabled":
                legacy._guard_role_assignment_allowed(role, existing, node=node)
            if legacy._would_remove_last_grandmaster(node, role, existing):
                raise ValueError("Cannot remove the last active MotherBrain Grandmaster.")
            if not existing:
                existing = GatewayNodeRole(gateway_membership_id=membership.id, node_id=node.id, role=effective.role)
                db.session.add(existing)
            before = {"role": effective.role, "active": effective.is_active}
            existing.is_active = role != "disabled"
            if role != "disabled":
                existing.role = role
            record_security_change("node_role_changed", target, node=node.code, before=before,
                after={"role": existing.role, "active": existing.is_active})
            rotate_user_session_version(target)
        else:
            abort(404)
        db.session.commit()
        clear_request_cache()
    except (ValueError, SQLAlchemyError) as error:
        db.session.rollback()
        code = 409 if isinstance(error, AccountConflict) else 400 if isinstance(error, ValueError) else 503
        # Do not replace a stale review token with an actionable fresh one while retaining stale input.
        return render_template("auth/accounts/conflict.html", target_user=target,
            error=str(error) if isinstance(error, ValueError) else "Nothing was saved. Please retry.",
            draft={key: value for key, value in request.form.items() if key not in {"csrf_token", "review_token"} and "password" not in key.lower()},
            **_tabs("people")), code
    flash("Account change saved.", "info")
    return redirect(url_for("auth.account_profile", user_id=target.id, tab="identity" if action == "identity" else "access"))


@bp.get("/my-account")
@login_required
def my_account():
    from app.services.accounts_workspace import gateway_membership_needs_review
    restricted = bool(current_user.scorpion_vendor)
    apps = [] if restricted else available_applications()
    accesses = {a.app_code: a for a in PortalAppAccess.query.filter_by(user_id=current_user.id).all()} if not restricted else {}
    attempts = AccountAccessRequest.query.filter_by(user_id=current_user.id).order_by(AccountAccessRequest.id.desc()).limit(40).all() if not restricted else []
    return render_template("auth/accounts/my_account.html", applications=apps, accesses=accesses, attempts=attempts,
        browser_tab_title="My Account | NeoApps",
        decisions=_decisions(attempts), authorized=authorized_applications(current_user), preference=preference(current_user), restricted=restricted,
        review=None if restricted else review_token(current_user), request_key=str(uuid4()),
        gateway_review=False if restricted else gateway_membership_needs_review(current_user))


@bp.post("/my-account/app-access")
@login_required
def my_account_request():
    if current_user.scorpion_vendor or not user_can(legacy.PORTAL_REQUEST_ACCESS_PERMISSION):
        abort(403)
    try:
        actor = lock_self()
        if not user_can(legacy.PORTAL_REQUEST_ACCESS_PERMISSION, user=actor):
            abort(403)
        verify_review(actor, request.form.get("review_token", ""))
        new_attempt(actor, request.form.get("app_code", ""), request.form.get("reason", ""), request.form.get("request_key"))
        db.session.commit()
    except (ValueError, SQLAlchemyError) as error:
        db.session.rollback()
        return render_template("auth/accounts/conflict.html", target_user=None,
            error=str(error) if isinstance(error, ValueError) else "Request could not be saved. Try again.",
            draft={"reason": request.form.get("reason", "")}, **_tabs("")), 409 if isinstance(error, AccountConflict) else 400
    flash("Application access requested. Your previous decisions remain in history.", "info")
    return redirect(url_for("auth.my_account", _anchor="app-access"))


@bp.post("/my-account/preferences")
@login_required
def my_account_preferences():
    if current_user.scorpion_vendor:
        abort(403)
    try:
        save_preferences(request.form.get("review_token", ""), request.form.get("preferred_app", ""), request.form.get("show_cryocheck") == "1")
        db.session.commit()
    except (ValueError, SQLAlchemyError) as error:
        db.session.rollback()
        return render_template("auth/accounts/conflict.html", target_user=None, error=str(error) if isinstance(error, ValueError) else "Preferences could not be saved.", draft={}, **_tabs("")), 409 if isinstance(error, AccountConflict) else 400
    flash("Preferences saved.", "info")
    return redirect(url_for("auth.my_account", _anchor="preferences"))
