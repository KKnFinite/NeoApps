"""Radio confirmation is independent of immutable treatment/CLEAR evidence."""


def radio_pending(event):
    return bool(event and event.status == 'cleared' and event.radio_state == 'pending')


def radio_complete(event):
    return bool(event and event.radio_state == 'complete'
                and event.radio_completed_at_utc and event.radio_completed_by_user_id)
