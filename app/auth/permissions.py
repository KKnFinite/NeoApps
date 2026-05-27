def _is_authenticated(user):
    return bool(user and user.is_authenticated)


def can_enter_data(user):
    if not _is_authenticated(user):
        return False

    return user.role in {"operator", "simulator", "master", "grandmaster"}


def can_make_decisions(user):
    if not _is_authenticated(user):
        return False

    return user.role in {"simulator", "master", "grandmaster"}


def can_manage_users(user):
    if not _is_authenticated(user):
        return False

    return user.role in {"master", "grandmaster"}


def can_manage_system(user):
    if not _is_authenticated(user):
        return False

    return user.role == "grandmaster"
