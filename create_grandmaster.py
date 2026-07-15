import getpass

from app import create_app
from app.extensions import db
from app.models import User
from app.services.password_policy import set_user_password


def create_grandmaster_user(username, password, confirm_password, app=None):
    app = app or create_app()
    normalized_username = (username or "").strip()
    if not normalized_username:
        raise ValueError("Username is required.")

    with app.app_context():
        if User.query.filter_by(username=normalized_username).first():
            raise ValueError("A user with that username already exists.")

        user = User(username=normalized_username, role="grandmaster", mfa_required=True)
        set_user_password(user, password, confirm_password)
        db.session.add(user)
        db.session.commit()
        db.session.refresh(user)
        db.session.expunge(user)
        return user


def main():
    username = input("Username: ").strip()
    password = getpass.getpass("Password: ")
    confirm_password = getpass.getpass("Confirm password: ")
    try:
        create_grandmaster_user(username, password, confirm_password)
    except ValueError as error:
        print(f"Grandmaster user not created: {error}")
        raise SystemExit(1)

    print("Grandmaster user created.")


if __name__ == "__main__":
    main()
