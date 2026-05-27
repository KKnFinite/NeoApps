from functools import wraps

from flask import flash, redirect, url_for
from flask_login import current_user, login_required


def role_required(*roles):
    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def wrapped_view(*args, **kwargs):
            if current_user.role in roles:
                return view_func(*args, **kwargs)

            flash("Access denied.", "error")
            return redirect(url_for("neomotherbrain.dashboard"))

        return wrapped_view

    return decorator


def mfa_required(view_func):
    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        # TODO: Add final MFA enforcement after MFA enrollment and challenge flows are built.
        return view_func(*args, **kwargs)

    return wrapped_view
