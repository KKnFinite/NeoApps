"""Initialize the NeoStaffing roster journal in an approved PostgreSQL database."""

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--confirm", action="store_true", help="Confirm the additive schema and roster journal cutover.")
    mode.add_argument("--audit-legacy-marks", action="store_true",
                      help="Read-only eligibility counts. No new schema or copied records.")
    parser.add_argument("--import-legacy-marks", action="store_true",
                        help="With --confirm, also copy verified daily attendance and timecard marks.")
    args = parser.parse_args()
    if not os.environ.get("DATABASE_URL") or (args.import_legacy_marks and not args.confirm):
        parser.error("An explicit DATABASE_URL is required, and --import-legacy-marks requires --confirm.")

    from sqlalchemy import text
    from app import create_app
    from app.extensions import db
    from app.services.neostaffing_history_schema import initialize_reporting_history
    from app.services.neostaffing_history_legacy import audit_or_import_legacy_marks

    with create_app(auto_bootstrap=False).app_context():
        if db.session.get_bind().dialect.name != "postgresql":
            parser.error("History audit and initialization require PostgreSQL.")
        if args.audit_legacy_marks:
            db.session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            print(json.dumps(asdict(audit_or_import_legacy_marks()), sort_keys=True))
            return
        initialized = initialize_reporting_history()
        audit = None
        if args.import_legacy_marks:
            # Cutover takes a stable legacy inventory, outside the active Sort.
            db.session.execute(text("""
                LOCK TABLE staffing_daily_attendance, staffing_timecard_slices, staffing_timecard_edits IN SHARE MODE
            """))
            audit = audit_or_import_legacy_marks(write=True)
        db.session.commit()
        if audit is not None:
            print(json.dumps(asdict(audit), sort_keys=True))
        print("Staffing history initialized." if initialized else "Staffing history already initialized.")


if __name__ == "__main__":
    main()
