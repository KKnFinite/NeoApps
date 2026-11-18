"""Platform-owned driver identity/company controls; operational approval stays in Scorpion."""
from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import SQLAlchemyError

from app.auth import bp
from app.auth.accounts import _tabs
from app.extensions import db
from app.models import FuelVendorCompany, User
from app.services.access_control import get_current_gateway
from app.services.account_security import platform_grandmaster
from app.services.account_foundation import AccountConflict
from app.services.auth_rate_limits import client_ip_for_request, login_is_limited, record_login_failure
from app.services.fuel_driver_accounts import verify_ups_id, assign_company, create_company, set_company_active
from app.services.grandmaster_commissioning import ReauthenticationFailed


def _guard():
    if not platform_grandmaster(current_user):
        abort(403)


def _failure(error, target=None):
    db.session.rollback()
    if isinstance(error, ReauthenticationFailed):
        try:
            record_login_failure(client_ip_for_request(request), current_user.email)
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            abort(503)
    return render_template("auth/accounts/conflict.html", target_user=target,
        error=str(error) if isinstance(error, ValueError) else "Nothing was saved. Please retry.",
        draft={key:value for key,value in request.form.items() if "password" not in key and key not in {"csrf_token","review_token"}},
        **_tabs("people")), 409 if isinstance(error, AccountConflict) else 400 if isinstance(error, ValueError) else 503


def _step_up_limit():
    if login_is_limited(client_ip_for_request(request), current_user.email):
        abort(429)


@bp.post("/accounts/people/<int:user_id>/fuel-driver-identity")
@login_required
def fuel_driver_identity(user_id):
    _guard()
    target = db.get_or_404(User, user_id)
    try:
        verify_ups_id(user_id, request.form.get("review_token", ""), request.form.get("ups_id"),
            request.form.get("verification_reference"), request.form.get("staffing_person_id"))
        db.session.commit()
    except (ValueError, SQLAlchemyError) as error:
        return _failure(error, target)
    flash("UPS ID verified and saved. Account identity and operational history were preserved.", "info")
    return redirect(url_for("auth.account_profile", user_id=user_id))


@bp.post("/accounts/people/<int:user_id>/vendor-company")
@login_required
def fuel_driver_company(user_id):
    _guard()
    _step_up_limit()
    target = db.get_or_404(User, user_id)
    try:
        raw_company = request.form.get("company_id", "").strip()
        if raw_company and (not raw_company.isascii() or not raw_company.isdecimal() or int(raw_company) < 1):
            raise ValueError("Select a valid verified company, or explicitly choose no company.")
        assign_company(user_id, request.form.get("review_token", ""), int(raw_company) if raw_company else None,
            request.form.get("is_supervisor") == "1", request.form.get("verification_reference"),
            request.form.get("current_password", ""), request.form.get("confirmed_user_id"))
        db.session.commit()
    except (ValueError, SQLAlchemyError) as error:
        return _failure(error, target)
    flash("Verified company access updated. Existing sessions were invalidated.", "info")
    return redirect(url_for("auth.account_profile", user_id=user_id, tab="access"))


@bp.route("/accounts/vendor-companies", methods=["GET", "POST"])
@login_required
def fuel_vendor_companies():
    _guard()
    gateway = get_current_gateway()
    if request.method == "POST":
        try:
            if request.form.get("action") == "status":
                _step_up_limit()
                set_company_active(request.form.get("company_id",type=int), request.form.get("active") == "1",
                    request.form.get("version"), request.form.get("verification_reference"), request.form.get("current_password", ""))
            else:
                create_company(gateway.id, request.form.get("name"), request.form.get("verification_reference"))
            db.session.commit()
        except (ValueError, SQLAlchemyError) as error:
            return _failure(error)
        flash("Verified company record saved.", "info")
        return redirect(url_for("auth.fuel_vendor_companies"))
    return render_template("auth/accounts/vendor_companies.html", companies=FuelVendorCompany.query.filter_by(gateway_id=gateway.id).order_by(FuelVendorCompany.name).all(), gateway=gateway, **_tabs("security"))


@bp.post("/accounts/signup-invitation")
@login_required
def fuel_driver_signup_invitation():
    _guard()
    from app.services.fuel_driver_invitations import send_signup_invitation
    try:
        sent = send_signup_invitation(request.form.get("email"), client_ip_for_request(request), get_current_gateway())
    except (ValueError, SQLAlchemyError) as error:
        return _failure(error)
    flash("NeoApps signup invitation sent." if sent else "Invitation delivery could not be confirmed. No account or access was created.", "info" if sent else "error")
    return redirect(url_for("auth.accounts_requests"))
