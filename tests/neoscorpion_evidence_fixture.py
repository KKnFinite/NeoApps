"""Canonical synthetic fueling evidence; no direct physical/event field writes.

Callers create their gateway/Sort/mission/resources, then use this one fixture to
record actual assignment, ON, data, OFF, handoff and Dispatcher verification.
A controlled clock represents the test operation, never reconstructed history.
"""
from contextlib import contextmanager
import json
from datetime import datetime
from unittest.mock import patch

from flask import current_app, g
from flask_login import login_user
from app.extensions import db
from app.models import NeoScorpionFuelAssignment, NeoScorpionFuelWorkState, NeoScorpionSortTruck
from app.services import neoscorpion as fuel, neoscorpion_assets as assets


class FuelCycle:
    def __init__(self, gateway, operation, mission, fueler, dispatcher, truck, *, assignment=None):
        self.gateway, self.operation, self.mission = gateway, operation, mission
        self.fueler, self.dispatcher, self.truck = fueler, dispatcher, truck
        self.assignment = assignment

    @contextmanager
    def scope(self, at=None, *, actor=None):
        # Choose only the fixture's genuine gateway-owned current operation.
        # All canonical operation/assignment/Work locks and guards still execute.
        operation = self.operation
        class Clock(datetime):
            @classmethod
            def utcnow(cls):
                return at if at is not None else datetime.utcnow()
        with patch.object(fuel, 'current_existing_operational_sort_operations',
                side_effect=lambda gateway, now=None: [operation] if gateway.id == operation.gateway_id else []), \
             patch.object(fuel, 'datetime', Clock), current_app.test_request_context('/'):
            previous = g.__dict__.copy()
            g.__dict__.clear()
            try:
                login_user(actor or self.fueler)
                yield
            finally:
                g.__dict__.clear()
                g.__dict__.update(previous)

    @classmethod
    def create(cls, gateway, operation, fueler, dispatcher, truck, *, flight, tail, required=25000):
        from app.models import SortDateMission
        mission = SortDateMission(sort_date_operation_id=operation.id,
            sort_date=operation.sort_date, gateway_code=operation.gateway_code,
            sort_name=operation.sort_name, mission_type='departure', mission_source='manual',
            flight_number=flight, origin=gateway.code, destination='SDF',
            assigned_tail_number=tail, tail_source='manual', planned_fuel_load=required,
            timezone='America/Chicago', departure_status='scheduled')
        db.session.add(mission)
        db.session.flush()
        return cls(gateway, operation, mission, fueler, dispatcher, truck)

    def record_arrival(self, at):
        """Explicit synthetic observed Block-In through the canonical manual writer."""
        from datetime import timezone
        from zoneinfo import ZoneInfo
        from app.models import SortDateMission
        from app.services.neorain_overrides import edit_override
        arrival = SortDateMission(sort_date_operation_id=self.operation.id,
            sort_date=self.operation.sort_date, gateway_code=self.gateway.code,
            sort_name=self.operation.sort_name, mission_type='arrival', mission_source='manual',
            flight_number='ARR-' + self.mission.flight_number, origin='SDF', destination=self.gateway.code,
            assigned_tail_number=self.mission.assigned_tail_number, timezone='America/Chicago')
        db.session.add(arrival); db.session.flush()
        local = at.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(arrival.timezone))
        assert self.dispatcher.id is not None  # Authenticated routes already load the actor before mutation.
        edit_override(arrival, self.operation, 'block_in', local.strftime('%H:%M'), self.dispatcher)
        db.session.commit()
        assert arrival.actual_block_in_datetime_utc == at, 'Fixture Block-In must belong to this Sort'
        return arrival

    def park(self, at, ramp, position):
        from app.services import parking_plan
        with self.scope(at, actor=self.dispatcher), patch.object(parking_plan, '_utc_now', return_value=at):
            parking_plan.assign_tail_to_lane(self.operation, self.mission.assigned_tail_number,
                ramp, position, 1, user=self.dispatcher)
            db.session.commit()

    def assign(self, at, *, current_gallons=10000):
        with self.scope(at, actor=self.dispatcher):
            assets.select_nightly_fueler(self.operation, self.fueler)
            selection = NeoScorpionSortTruck.query.filter_by(
                sort_date_operation_id=self.operation.id, fuel_truck_id=self.truck.id).first()
            if selection is None:
                assets.select_nightly_truck(self.operation, self.truck, status='available',
                    current_gallons=current_gallons, changed_by_user=self.dispatcher, now_utc=at)
            existing = NeoScorpionFuelAssignment.query.filter_by(sort_date_mission_id=self.mission.id).first()
            fuel.save_dispatch_assignment(self.gateway, dict(mission_id=self.mission.id,
                expected_assigned_fueler_user_id=existing.assigned_fueler_user_id if existing else '',
                expected_assigned_truck_id=existing.assigned_truck_id if existing else '',
                assigned_fueler_user_id=self.fueler.id, assigned_truck_id=self.truck.id))
            db.session.commit()
            self.assignment = NeoScorpionFuelAssignment.query.filter_by(sort_date_mission_id=self.mission.id).one()
        return self.assignment

    @property
    def work(self):
        return db.session.get(NeoScorpionFuelWorkState, self.assignment.active_work_state_id)

    def row(self):
        with self.scope():
            return fuel.fueler_context(self.gateway, self.fueler,
                assignment_id=self.assignment.id, include_timing=False)['rows'][0]

    def save(self, at, **values):
        with self.scope(at):
            result = fuel.save_fueler_entry(self.gateway, self.fueler,
                dict(assignment_id=self.assignment.id, expected=json.dumps(self.row()['edit_baseline']), **values),
                now_utc=at, require_expected=True)
            db.session.commit()
        return result

    def on(self, at):
        with self.scope(at):
            result = fuel.confirm_fueler_truck_on(self.gateway, self.fueler, self.assignment.id,
                expected_cycle=self.assignment.current_cycle_number,
                expected_tail=self.mission.assigned_tail_number, expected_truck_id=self.truck.id,
                expected_work_ref=self.assignment.active_work_state_id, require_work_ref=True, now_utc=at)
            db.session.commit()
        return result

    def off(self, at):
        with self.scope(at):
            result = fuel.mark_fueler_off(self.gateway, self.fueler, self.assignment.id,
                expected=self.row()['edit_baseline'], now_utc=at)
            db.session.commit()
        return result

    def submit(self, at):
        with self.scope(at):
            result = fuel.submit_fueler_assignment(self.gateway, self.fueler, self.assignment.id,
                expected=self.row()['edit_baseline'], now_utc=at)
            db.session.commit()
        return result

    def complete(self, at):
        with self.scope(at, actor=self.dispatcher):
            result = fuel.complete_fueled_assignment(self.gateway, self.dispatcher,
                self.assignment.id, now_utc=at)
            db.session.commit()
        return result

    def finish(self, start, end, **values):
        self.on(start)
        self.save(end, **values)
        self.off(end)
        self.submit(end)
        return self.complete(end).fueling_event


def transfer_readings(*, remaining='6', actual='15', gallons='3000'):
    """Explicit synthetic operator entries, not inferred or inherited measurements."""
    return dict(apu_running='no', remaining_left=remaining, remaining_ctr='0', remaining_right=remaining,
        actual_left=actual, actual_ctr='0', actual_right=actual, transfer_fuel_gallons=gallons)

