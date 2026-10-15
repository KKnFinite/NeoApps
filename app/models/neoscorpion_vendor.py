"""Vendor identity, nightly approvals and inventory-reading audit metadata."""
from datetime import datetime
from app.extensions import db


class NeoScorpionVendorAccount(db.Model):
    __tablename__ = 'neoscorpion_vendor_accounts'
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), primary_key=True)
    gateway_id = db.Column(db.Integer, db.ForeignKey('gateways.id'), nullable=False, index=True)
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    invited_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    user = db.relationship('User', foreign_keys=[user_id], backref=db.backref('scorpion_vendor', uselist=False))
    gateway = db.relationship('Gateway')


class NeoScorpionVendorNight(db.Model):
    __tablename__ = 'neoscorpion_vendor_nights'
    __table_args__ = (db.CheckConstraint("status IN ('pending','approved','denied','revoked')", name='ck_vendor_night_status'),)
    sort_date_operation_id = db.Column(db.Integer, db.ForeignKey('sort_date_operations.id'), primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), primary_key=True)
    status = db.Column(db.String(20), nullable=False, default='pending')
    changed_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    version = db.Column(db.Integer, nullable=False, default=0)


class NeoScorpionVendorRequest(db.Model):
    __tablename__ = 'neoscorpion_vendor_requests'
    __table_args__ = (
        db.CheckConstraint("kind IN ('claim','fuel')", name='ck_vendor_request_kind'),
        db.CheckConstraint("status IN ('pending','approved','denied','expired','superseded')", name='ck_vendor_request_status'),
        db.CheckConstraint('gallons IS NULL OR gallons >= 0', name='ck_vendor_request_gallons'),
    )
    id = db.Column(db.Integer, primary_key=True)
    sort_date_operation_id = db.Column(db.Integer, db.ForeignKey('sort_date_operations.id'), nullable=False, index=True)
    fuel_truck_id = db.Column(db.Integer, db.ForeignKey('neoscorpion_fuel_trucks.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    kind = db.Column(db.String(12), nullable=False)
    status = db.Column(db.String(16), nullable=False, default='pending', index=True)
    gallons = db.Column(db.Integer)
    calculated_gallons = db.Column(db.Integer)
    inventory_epoch = db.Column(db.Integer)
    interval_work_id = db.Column(db.Integer, db.ForeignKey('neoscorpion_fuel_work_states.id'))
    next_work_id = db.Column(db.Integer, db.ForeignKey('neoscorpion_fuel_work_states.id'))
    version = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    decided_at = db.Column(db.DateTime)
    decided_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    user = db.relationship('User', foreign_keys=[user_id])
    truck = db.relationship('NeoScorpionFuelTruck')


class NeoScorpionInventoryReading(db.Model):
    __tablename__ = 'neoscorpion_inventory_readings'
    __table_args__ = (db.UniqueConstraint('sort_date_operation_id','fuel_truck_id','epoch',name='uq_scorpion_reading_epoch'),)
    id = db.Column(db.Integer, primary_key=True)
    sort_date_operation_id = db.Column(db.Integer, db.ForeignKey('sort_date_operations.id'), nullable=False)
    fuel_truck_id = db.Column(db.Integer, db.ForeignKey('neoscorpion_fuel_trucks.id'), nullable=False)
    epoch = db.Column(db.Integer, nullable=False)
    mode = db.Column(db.String(16), nullable=False)
    gallons = db.Column(db.Integer, nullable=False)
    covered_work_ids = db.Column(db.JSON, nullable=False, default=list)
    actor_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class NeoScorpionVendorAudit(db.Model):
    __tablename__ = 'neoscorpion_vendor_audits'
    id = db.Column(db.Integer, primary_key=True)
    gateway_id = db.Column(db.Integer, db.ForeignKey('gateways.id'), nullable=False, index=True)
    sort_date_operation_id = db.Column(db.Integer, db.ForeignKey('sort_date_operations.id'))
    actor_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    subject_user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    fuel_truck_id = db.Column(db.Integer, db.ForeignKey('neoscorpion_fuel_trucks.id'))
    request_id = db.Column(db.Integer, db.ForeignKey('neoscorpion_vendor_requests.id'))
    action = db.Column(db.String(48), nullable=False)
    facts = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
