"""Verified restricted-account metadata; no legacy affiliation backfill."""
from datetime import datetime
from sqlalchemy import text
from app.extensions import db


class FuelDriverIdentity(db.Model):
    __tablename__ = "fuel_driver_identities"
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), primary_key=True)
    requested_driver = db.Column(db.Boolean, nullable=False, default=False)
    verified_ups_id = db.Column(db.String(80))
    verified_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    verified_at = db.Column(db.DateTime)
    verification_reference = db.Column(db.String(1000))
    version = db.Column(db.Integer, nullable=False, default=1)


class FuelVendorCompany(db.Model):
    __tablename__ = "fuel_vendor_companies"
    __table_args__ = (db.UniqueConstraint("gateway_id", "name_key", name="uq_fuel_vendor_company_name"),)
    id = db.Column(db.Integer, primary_key=True)
    gateway_id = db.Column(db.Integer, db.ForeignKey("gateways.id"), nullable=False, index=True)
    name = db.Column(db.String(160), nullable=False)
    name_key = db.Column(db.String(160), nullable=False)
    verified_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    verified_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    verification_reference = db.Column(db.String(1000), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    version = db.Column(db.Integer, nullable=False, default=1)


class FuelVendorMembership(db.Model):
    __tablename__ = "fuel_vendor_memberships"
    __table_args__ = (
        db.Index("uq_fuel_vendor_membership_current", "user_id", unique=True,
            sqlite_where=text("ended_at IS NULL"), postgresql_where=text("ended_at IS NULL")),
        db.CheckConstraint("ended_at IS NULL OR ended_at >= started_at", name="ck_fuel_vendor_membership_dates"),
    )
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    company_id = db.Column(db.Integer, db.ForeignKey("fuel_vendor_companies.id"), nullable=False, index=True)
    is_supervisor = db.Column(db.Boolean, nullable=False, default=False)
    started_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    ended_at = db.Column(db.DateTime)
    verified_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    verification_reference = db.Column(db.String(1000), nullable=False)
    company = db.relationship("FuelVendorCompany")
