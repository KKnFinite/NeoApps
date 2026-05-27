import getpass

from app import create_app
from app.extensions import db
from app.models import User


app = create_app()


with app.app_context():
    username = input("Username: ").strip()
    password = getpass.getpass("Password: ")

    existing_user = User.query.filter_by(username=username).first()
    if existing_user:
        print("A user with that username already exists.")
    else:
        user = User(username=username, role="grandmaster", mfa_required=True)
        user.set_password(password)

        db.session.add(user)
        db.session.commit()

        print("Grandmaster user created.")
