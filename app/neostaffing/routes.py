from functools import wraps

from flask import flash, redirect, render_template, url_for
from flask_login import current_user, login_required

from app.neostaffing import bp
from app.services.access_control import get_user_app_role, user_can_access_app, user_has_app_access


def neostaffing_app_required(minimum_role="watcher"):
    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def wrapped_view(*args, **kwargs):
            if user_can_access_app(current_user, "neostaffing", minimum_role=minimum_role):
                return view_func(*args, **kwargs)

            if user_has_app_access(current_user, "neostaffing"):
                flash("NeoStaffing App Management requires Master access.", "error")
                return redirect(url_for("neostaffing.index"))

            flash("Request NeoStaffing access from the NeoApps Portal.", "error")
            return redirect(url_for("auth.portal_dashboard"))

        return wrapped_view

    return decorator


@bp.route("")
@neostaffing_app_required()
def index():
    role = get_user_app_role(current_user, "neostaffing")
    return render_template(
        "neostaffing/index.html",
        app_role=role,
        can_manage_app=user_can_access_app(current_user, "neostaffing", minimum_role="master"),
    )


@bp.route("/")
@login_required
def index_slash():
    return redirect(url_for("neostaffing.index"))


@bp.route("/app-management")
@neostaffing_app_required(minimum_role="master")
def app_management():
    return render_template(
        "neostaffing/app_management.html",
        app_role=get_user_app_role(current_user, "neostaffing"),
    )
