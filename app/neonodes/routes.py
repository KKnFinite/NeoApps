from flask import render_template

from app.neonodes import bp


@bp.route("/")
def index():
    return render_template("neonodes/index.html")
