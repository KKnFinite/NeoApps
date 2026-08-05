from app.extensions import db
from app.models import SortDateAlpPreview


ALP_PREVIEW_MISSION_TYPES = {"arrival", "departure"}


def get_alp_preview_state(operation, mission_type, user):
    mission_type = _normalize_mission_type(mission_type)
    _user_id(user)
    return SortDateAlpPreview.query.filter_by(
        sort_date_operation_id=operation.id,
        mission_type=mission_type,
    ).first()


def save_alp_preview_state(operation, mission_type, paste_text, user):
    mission_type = _normalize_mission_type(mission_type)
    user_id = _user_id(user)
    state = get_alp_preview_state(operation, mission_type, user)
    if state is None:
        state = SortDateAlpPreview(
            sort_date_operation_id=operation.id,
            gateway_id=operation.gateway_id,
            gateway_code=operation.gateway_code,
            mission_type=mission_type,
            user_id=user_id,
        )
        db.session.add(state)
    state.gateway_id = operation.gateway_id
    state.gateway_code = operation.gateway_code
    state.user_id = user_id
    state.paste_text = str(paste_text or "")
    db.session.flush()
    return state


def clear_alp_preview_state(operation, mission_type, user):
    state = get_alp_preview_state(operation, mission_type, user)
    if state is None:
        return False
    db.session.delete(state)
    db.session.flush()
    return True


def _normalize_mission_type(mission_type):
    normalized = str(mission_type or "").strip().lower()
    if normalized not in ALP_PREVIEW_MISSION_TYPES:
        raise ValueError("ALP preview mission type must be arrival or departure.")
    return normalized


def _user_id(user):
    user_id = getattr(user, "id", None)
    if not user_id:
        raise ValueError("An authenticated user is required for an ALP preview.")
    return int(user_id)
