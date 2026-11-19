"""Platform-owned driver identity/company controls; operational approval stays in Scorpion."""
from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import SQLAlchemyError

from app.auth import bp
from app.auth.accounts import _tabs
from app.extensions import db
from app.models import User
from app.services.access_control import get_current_gateway
from app.services.account_security import platform_grandmaster
from app.services.account_foundation import AccountConflict
from app.services.auth_rate_limits import client_ip_for_request, login_is_limited, record_login_failure
from app.services.fuel_driver_accounts import verify_ups_id
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
    return 'Company-based capability changes have retired. Use Accounts & Access account type approval.',410


@bp.route("/accounts/vendor-companies", methods=["GET", "POST"])
@login_required
def fuel_vendor_companies():
    _guard()
    if request.method == 'POST':
        return 'Company administration has retired. Use Accounts & Access account type approval.',410
    return redirect(url_for('auth.accounts_security'))


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
