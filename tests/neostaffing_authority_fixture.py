"""Canonical active management identity for direct attendance service fixtures."""
from app.extensions import db
from app.models import User
from app.services import neostaffing as staffing
from app.services.password_policy import set_user_password


def management_actor(work_area, *, user=None, employee_id="attendance-fixture-manager"):
    if user is None:
        user = User(username=employee_id, employee_id=employee_id, role="watcher", is_active=True)
        set_user_password(user, "FixturePassword123!")
        db.session.add(user)
    person = staffing.create_person(dict(employee_id=user.employee_id, first_name="Fixture",
        last_name="Manager", seniority_date="2020-01-01", classification="part_time_supervisor"))
    staffing.create_leadership_assignment(person, work_area)
    db.session.flush()
    return user
