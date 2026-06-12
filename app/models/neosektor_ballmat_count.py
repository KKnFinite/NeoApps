from datetime import datetime

from app.extensions import db


class NeoSektorBallmatCount(db.Model):
    __tablename__ = "neosektor_ballmat_counts"
    __table_args__ = (
        db.UniqueConstraint(
            "sort_state_id",
            "side",
            name="uq_neosektor_ballmat_counts_sort_side",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    sort_state_id = db.Column(
        db.Integer,
        db.ForeignKey("neosektor_sort_states.id"),
        nullable=False,
        index=True,
    )
    side = db.Column(db.String(16), nullable=False, index=True)
    count = db.Column(db.Integer, nullable=False, default=0)
    status = db.Column(db.String(32), nullable=False, default="Empty")
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    sort_state = db.relationship("NeoSektorSortState", backref="ballmat_counts")
