from functools import wraps

from flask import flash, redirect, url_for
from flask_login import current_user, login_required

from app.services.access_control import (
    get_current_gateway,
    get_user_node_role,
    user_can_access_node,
    user_has_gateway_access,
)


def role_required(*roles):
    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def wrapped_view(*args, **kwargs):
            gateway = get_current_gateway()
            node_role = get_user_node_role(current_user, gateway.code, "motherbrain")
            if node_role in roles:
                return view_func(*args, **kwargs)

            if not user_has_gateway_access(current_user, gateway.code):
                return redirect(url_for("auth.access_pending"))

            flash("Access denied.", "error")
            return redirect(url_for("neomotherbrain.rfd_hub"))

        return wrapped_view

    return decorator


def gateway_node_required(node_code, minimum_role="watcher"):
    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def wrapped_view(*args, **kwargs):
            gateway = get_current_gateway()
            effective_minimum_role = minimum_role
            if node_code == "motherbrain" and minimum_role == "watcher":
                effective_minimum_role = "simulator"

            if user_can_access_node(
                current_user,
                gateway.code,
                node_code,
                minimum_role=effective_minimum_role,
            ):
                return view_func(*args, **kwargs)

            if not user_has_gateway_access(current_user, gateway.code):
                return redirect(url_for("auth.access_pending"))

            flash("Access denied.", "error")
            return redirect(url_for("neomotherbrain.rfd_hub"))

        return wrapped_view

    return decorator


def mfa_required(view_func):
    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        # TODO: Add final MFA enforcement after MFA enrollment and challenge flows are built.
        return view_func(*args, **kwargs)

    return wrapped_view
