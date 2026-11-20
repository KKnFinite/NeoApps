"""Initialize the NeoStaffing roster journal in an approved PostgreSQL database."""

import argparse
import os
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm", action="store_true", help="Confirm the additive schema and roster journal cutover.")
    parser.add_argument("--retain-existing-timecard-marks", action="store_true",
                        help="Also copy available status-only evidence before normal timecard cleanup.")
    args = parser.parse_args()
    if not args.confirm or not os.environ.get("DATABASE_URL"):
        parser.error("An explicit DATABASE_URL and --confirm are required.")

    from app import create_app
    from app.extensions import db
    from app.services.neostaffing_history_schema import initialize_reporting_history

    with create_app(auto_bootstrap=False).app_context():
        initialized = initialize_reporting_history()
        if args.retain_existing_timecard_marks:
            from app.services.neostaffing_historical_records import import_available_timecard_marks

            print(f"Copied legacy timecard marks: {import_available_timecard_marks()}")
        db.session.commit()
        print("Staffing history initialized." if initialized else "Staffing history already initialized.")


if __name__ == "__main__":
    main()
