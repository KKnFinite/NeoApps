from app.services.access_control import get_current_gateway, user_can_access_node


def _is_authenticated(user):
    return bool(user and user.is_authenticated)


def can_enter_data(user):
    return _can_access_motherbrain(user, "operator")


def can_make_decisions(user):
    return _can_access_motherbrain(user, "simulator")


def can_manage_users(user):
    return _can_access_motherbrain(user, "master")


def can_manage_system(user):
    if not _is_authenticated(user):
        return False

    return _can_access_motherbrain(user, "grandmaster")


def _can_access_motherbrain(user, minimum_role):
    if not _is_authenticated(user):
        return False

    gateway = get_current_gateway()
    return user_can_access_node(
        user,
        gateway.code,
        "motherbrain",
        minimum_role=minimum_role,
    )
