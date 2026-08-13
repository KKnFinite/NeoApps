from flask import g, has_request_context, request
from sqlalchemy import event
from sqlalchemy.orm import Session


MISSING = object()
_CACHE_ATTRIBUTE = "_neoapps_request_cache"


def get_request_cached(namespace, key):
    if not has_request_context():
        return MISSING

    state = getattr(g, _CACHE_ATTRIBUTE, None)
    if state is None or state[0] is not request._get_current_object():
        return MISSING
    cache = state[1]
    return cache.get((namespace, key), MISSING)


def set_request_cached(namespace, key, value):
    if not has_request_context():
        return value

    request_object = request._get_current_object()
    state = getattr(g, _CACHE_ATTRIBUTE, None)
    if state is None or state[0] is not request_object:
        cache = {}
        setattr(g, _CACHE_ATTRIBUTE, (request_object, cache))
    else:
        cache = state[1]
    cache[(namespace, key)] = value
    return value


def request_cached(namespace, key, resolver):
    cached = get_request_cached(namespace, key)
    if cached is not MISSING:
        return cached

    return set_request_cached(namespace, key, resolver())


def clear_request_cache():
    if not has_request_context():
        return

    state = getattr(g, _CACHE_ATTRIBUTE, None)
    if state is not None and state[0] is request._get_current_object():
        state[1].clear()


@event.listens_for(Session, "after_commit")
@event.listens_for(Session, "after_rollback")
def _clear_request_cache_after_transaction(_session):
    # ORM instances and authorization results may change state at a transaction
    # boundary even though the surrounding HTTP request continues rendering.
    clear_request_cache()
