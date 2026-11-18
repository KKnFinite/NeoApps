"""Extend the shared append-only history guards; never seed or map authority."""
from app.services.account_security_history import ensure_account_security_history


def ensure_account_history_guards(connection):
    ensure_account_security_history(connection, ("account_security_events", "account_access_decisions"))
