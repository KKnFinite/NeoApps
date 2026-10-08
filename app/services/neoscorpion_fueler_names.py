"""NeoScorpion-only operational names; never change canonical User identity."""

from app.models import NeoScorpionFuelerNickname


def fueler_nicknames(gateway, users):
    """Load gateway preferences once for the users already in an operational view."""
    user_ids = {user.id for user in users if user is not None}
    if not user_ids:
        return {}
    return {
        row.user_id: row.nickname
        for row in NeoScorpionFuelerNickname.query.filter(
            NeoScorpionFuelerNickname.gateway_id == gateway.id,
            NeoScorpionFuelerNickname.user_id.in_(user_ids),
        ).all()
    }


def operational_fueler_name(user, nicknames_by_user_id):
    """Resolve nickname, then the normal display name; no persistent identity rewrite."""
    return nicknames_by_user_id.get(user.id) or user.display_name


class OperationalFuelerDisplay:
    """Read-only presentation adapter; never mutates a User or assignment."""

    def __init__(self, user, nicknames_by_user_id):
        self._user = user
        self._nicknames = nicknames_by_user_id

    @property
    def display_name(self):
        return operational_fueler_name(self._user, self._nicknames)

    def __getattr__(self, name):
        return getattr(self._user, name)
