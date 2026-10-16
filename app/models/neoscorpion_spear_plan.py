"""Versioned, read-only planning evidence; never an operational assignment."""
from datetime import datetime
from sqlalchemy import event
from app.extensions import db


class NeoScorpionSpearPlanSnapshot(db.Model):
    __tablename__='neoscorpion_spear_plan_snapshots'
    __table_args__=(db.UniqueConstraint('operation_id','mode','fingerprint',name='uq_spear_plan_revision'),)
    id=db.Column(db.Integer,primary_key=True)
    gateway_id=db.Column(db.Integer,db.ForeignKey('gateways.id'),nullable=False)
    operation_id=db.Column(db.Integer,db.ForeignKey('sort_date_operations.id'),nullable=False,index=True)
    mode=db.Column(db.String(8),nullable=False)
    fingerprint=db.Column(db.String(64),nullable=False)
    algorithm_version=db.Column(db.String(40),nullable=False)
    token=db.Column(db.String(24),nullable=False)
    payload=db.Column(db.JSON,nullable=False)
    created_at=db.Column(db.DateTime,nullable=False,default=datetime.utcnow)


@event.listens_for(NeoScorpionSpearPlanSnapshot,'before_update')
def immutable_plan(*_args):
    raise ValueError('SPEAR plan snapshots are immutable.')
