"""Future work planning never changes the current physical assignment."""
from datetime import datetime
from uuid import uuid4
from sqlalchemy import text
from app.extensions import db

class NeoScorpionPendingUplift(db.Model):
    __tablename__='neoscorpion_pending_uplifts'
    __table_args__=(
        db.CheckConstraint("status IN ('pending','review','promoted','cancelled')",name='ck_scorpion_pending_uplift_status'),
        db.CheckConstraint('required_fuel_lbs > 0',name='ck_scorpion_pending_uplift_target'),
        db.CheckConstraint('version >= 1 AND parent_cycle_number >= 1',name='ck_scorpion_pending_uplift_versions'),
        # Continuations are an ordered queue (id order), not a single slot.
        # Legacy post-completion uplift keeps its original one-plan contract.
        db.Index('uq_scorpion_pending_legacy_uplift','mission_id','parent_cycle_number',unique=True,
            postgresql_where=text("status IN ('pending','review') AND plan_kind = 'uplift'"),
            sqlite_where=text("status IN ('pending','review') AND plan_kind = 'uplift'")),
    )
    id=db.Column(db.Integer,primary_key=True)
    sort_date_operation_id=db.Column(db.Integer,db.ForeignKey('sort_date_operations.id'),nullable=False,index=True)
    mission_id=db.Column(db.Integer,db.ForeignKey('sort_date_missions.id'),nullable=False)
    fuel_assignment_id=db.Column(db.Integer,db.ForeignKey('neoscorpion_fuel_assignments.id'),nullable=False)
    parent_cycle_number=db.Column(db.Integer,nullable=False)
    parent_work_state_id=db.Column(db.Integer,db.ForeignKey('neoscorpion_fuel_work_states.id'),nullable=True)
    tail_number=db.Column(db.String(32),nullable=False)
    pending_cycle_identity=db.Column(db.String(36),nullable=False,unique=True,default=lambda:str(uuid4()))
    required_fuel_lbs=db.Column(db.Integer,nullable=False)
    proposed_fueler_user_id=db.Column(db.Integer,db.ForeignKey('users.id'),nullable=True)
    proposed_truck_id=db.Column(db.Integer,db.ForeignKey('neoscorpion_fuel_trucks.id'),nullable=True)
    created_by_user_id=db.Column(db.Integer,db.ForeignKey('users.id'),nullable=False)
    updated_by_user_id=db.Column(db.Integer,db.ForeignKey('users.id'),nullable=False)
    created_at_utc=db.Column(db.DateTime,nullable=False,default=datetime.utcnow)
    updated_at_utc=db.Column(db.DateTime,nullable=False,default=datetime.utcnow)
    status=db.Column(db.String(16),nullable=False,default='pending')
    # Existing rows remain post-completion uplifts, never silently converted.
    plan_kind=db.Column(db.String(16),nullable=False,default='uplift',server_default='uplift')
    handoff_snapshot=db.Column(db.JSON,nullable=True)
    version=db.Column(db.Integer,nullable=False,default=1)
    review_reason=db.Column(db.Text,nullable=True)
    promoted_cycle_number=db.Column(db.Integer,nullable=True)
    promoted_work_state_id=db.Column(db.Integer,db.ForeignKey('neoscorpion_fuel_work_states.id'),nullable=True)
    proposed_fueler=db.relationship('User',foreign_keys=[proposed_fueler_user_id])
    proposed_truck=db.relationship('NeoScorpionFuelTruck',foreign_keys=[proposed_truck_id])
