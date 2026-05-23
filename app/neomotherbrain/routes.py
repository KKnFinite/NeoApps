from flask import render_template

from app.neomotherbrain import bp


@bp.route("/")
def dashboard():
    return render_template("neomotherbrain/dashboard.html")
