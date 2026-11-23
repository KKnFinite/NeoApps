"""Serve one synthetic employee's session from the actual previous release checkout."""
import os
from pathlib import Path
import sys

from sqlalchemy.engine import make_url
import app
from app.extensions import db
from app.models.account_preference import AccountPreference
from tests.test_account_security_phase1 import fixture_user, PASSWORD
from tests.test_accounts_workspace import Inputs


def main(stage):
    assert Path(app.__file__).resolve().parents[1] == Path.cwd().resolve()
    uri = make_url(os.environ["DATABASE_URL"])
    assert uri.host == "127.0.0.1" and uri.database == "neostaffing_test_acl"
    assert uri.username == ("fixture_previous_runtime" if stage == "rollback" else "fixture_prior_owner")
    application = app.create_app(auto_bootstrap=False)
    client = application.test_client()
    session_name = application.config["SESSION_COOKIE_NAME"]
    cookie = Path(os.environ["HOME"]) / "isolated-employee-session"
    username = "release-user"
    if stage == "before":
        with application.app_context():
            fixture_user(username)
            db.session.commit()
        login_fields = Inputs(client.get("/login").data).values
        login = client.post("/login", data={"email": username + "@example.test", "password": PASSWORD,
                                            "csrf_token": login_fields.get("csrf_token", "")})
        assert login.status_code == 302, "Synthetic employee sign-in failed"
    else:
        client.set_cookie(session_name, cookie.read_text(encoding="utf-8"))
    page = client.get("/my-account")
    assert page.status_code == 200, "Previously signed-in employee lost access"
    fields = Inputs(page.data).values
    form = {"review_token": fields["review_token"], "csrf_token": fields.get("csrf_token", "")}
    if stage != "interrupted":
        form["show_cryocheck"] = "1"
    result = client.post("/my-account/preferences", data=form)
    assert result.status_code == 302 and "/my-account" in result.location, "Employee save failed"
    with application.app_context():
        from app.models import User
        employee = User.query.filter_by(username=username).one()
        assert db.session.get(AccountPreference, employee.id).show_cryocheck == (stage != "interrupted")
    denied = client.get("/accounts")
    assert (denied.status_code, denied.location) == (302, "/portal"), "Account administration was not denied"
    cookie.write_text(client.get_cookie(session_name).value, encoding="utf-8")
    print("Previous application verified employee read, save and authorization during " + stage)


if __name__ == "__main__":
    main(sys.argv[1])
