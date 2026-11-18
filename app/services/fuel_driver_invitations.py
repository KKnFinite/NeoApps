"""Shared signup link only: no identity provisioning, passwords or approval tokens."""
import re
from hashlib import sha256
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import db
from app.services.account_security import lock_administration, platform_grandmaster, record_security_change
from app.services.auth_rate_limits import reserve_signup_invitation
from app.services import email_service


def send_signup_invitation(email, ip, gateway):
    email = str(email or "").strip().lower()
    if len(email) > 255 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise ValueError("Enter a valid invitation email address.")
    def authorize():
        from app.services.neoscorpion_vendors import dispatcher
        actor = lock_administration()
        if not platform_grandmaster(actor):
            dispatcher(gateway, actor)
        return actor
    authorize()
    # Reservation owns its short commit, before mail delivery or any account writes.
    retry = reserve_signup_invitation(ip, email)
    if retry:
        raise ValueError(f"Invitation limit reached. Try again in {retry} seconds.")
    actor = authorize()
    # Release authority locks before the bounded network call. Invitation creates
    # no access; a lost receipt never retries the provider automatically.
    db.session.commit()
    delivery = email_service.send_neoapps_signup_invitation(email)
    record_security_change("neoapps_signup_invitation_sent" if delivery.get("sent") else "neoapps_signup_invitation_unconfirmed",
        actor, recipient_digest=sha256(email.encode()).hexdigest(), access_granted=False, account_created=False)
    db.session.commit()
    return bool(delivery.get("sent"))
