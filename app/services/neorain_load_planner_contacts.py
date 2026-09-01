"""Gateway-scoped operational contacts for canonical NeoRain Load Planners."""

from app.extensions import db
from app.models import NeoRainLoadPlannerContact, StaffingPerson


NEORAIN_LOAD_PLANNER_CONTACT_MAX_LENGTH = 64


def neorain_load_planner_contacts(gateway, planners=()):
    """Return bounded planner contacts keyed by canonical StaffingPerson id."""
    if gateway is None or gateway.id is None:
        return {}
    planner_ids = {
        planner.id
        for planner in planners or ()
        if isinstance(planner, StaffingPerson) and planner.id is not None
    }
    if not planner_ids:
        return {}
    rows = NeoRainLoadPlannerContact.query.filter(
        NeoRainLoadPlannerContact.gateway_id == gateway.id,
        NeoRainLoadPlannerContact.staffing_person_id.in_(planner_ids),
    ).all()
    return {row.staffing_person_id: row for row in rows}


def neorain_load_planner_contact(gateway, planner, *, contacts=None):
    """Read one planner contact without creating persistence."""
    if planner is None or planner.id is None:
        return None
    if contacts is not None:
        return contacts.get(planner.id)
    return neorain_load_planner_contacts(gateway, (planner,)).get(planner.id)


def set_neorain_load_planner_contact(
    gateway,
    planner,
    *,
    extension,
    radio_channel,
    contact=None,
):
    """Stage one validated per-planner contact; callers own the commit."""
    if gateway is None or gateway.id is None:
        raise ValueError("A gateway is required.")
    if not isinstance(planner, StaffingPerson) or planner.id is None:
        raise ValueError("Choose a valid Load Planner.")
    if contact is None:
        contact = NeoRainLoadPlannerContact.query.filter_by(
            gateway_id=gateway.id,
            staffing_person_id=planner.id,
        ).one_or_none()
    if contact is None:
        contact = NeoRainLoadPlannerContact(
            gateway_id=gateway.id,
            staffing_person_id=planner.id,
        )
        db.session.add(contact)
    contact.extension = _contact_text(extension, "Extension")
    contact.radio_channel = _contact_text(radio_channel, "Radio Channel")
    return contact


def neorain_load_planner_contact_values(contact):
    """Return template/row-safe display values without a second query."""
    return {
        "extension": contact.extension if contact and contact.extension else "",
        "radio_channel": (
            contact.radio_channel if contact and contact.radio_channel else ""
        ),
    }


def _contact_text(value, label):
    text = "" if value is None else str(value).strip()
    if len(text) > NEORAIN_LOAD_PLANNER_CONTACT_MAX_LENGTH:
        raise ValueError(
            f"{label} must be {NEORAIN_LOAD_PLANNER_CONTACT_MAX_LENGTH} characters or fewer."
        )
    return text
