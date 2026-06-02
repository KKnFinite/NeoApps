from datetime import datetime

from flask import flash, redirect, render_template, request, url_for
from flask_login import login_user, logout_user
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError

from app.auth import bp
from app.auth.decorators import role_required
from app.extensions import db
from app.models import User


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = None
        if username:
            user = User.query.filter(func.lower(User.username) == username.lower()).first()

        if not user or not user.check_password(password):
            flash("Invalid username or password.", "error")
            return render_template("neomotherbrain/dashboard.html"), 401

        if not user.is_active:
            flash("This account is inactive.", "error")
            return render_template("neomotherbrain/dashboard.html"), 403

        login_user(user)
        user.last_login = datetime.utcnow()

        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            flash("Login failed. Please try again.", "error")
            return render_template("neomotherbrain/dashboard.html"), 500

        return redirect(url_for("neomotherbrain.motherbrain"))

    return render_template("neomotherbrain/dashboard.html")


@bp.route("/logout")
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))


@bp.route("/admin/users")
@role_required("grandmaster", "master")
def users():
    users = User.query.order_by(User.username.asc()).all()
    return render_template("auth/users.html", users=users)
