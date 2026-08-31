"""Build an offline NeoSubZero frost-training JSONL artifact."""

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from app.services.neosubzero_frost_history import (
    CsvHistoricalWeatherProvider,
    build_frost_training_dataset,
    parse_cryotech_csv,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cryotech", required=True, type=Path)
    parser.add_argument("--weather", required=True, type=Path)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument(
        "--exposure-dates",
        type=Path,
        help=(
            "Optional text file with one YYYY-MM-DD operational-night date per line. "
            "Only these event-free nights may become negative examples."
        ),
    )
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    cryotech = parse_cryotech_csv(args.cryotech)
    weather = CsvHistoricalWeatherProvider(args.weather)
    exposure_dates = _read_exposure_dates(args.exposure_dates)
    records = build_frost_training_dataset(
        cryotech.rows,
        weather,
        start_date=args.start_date,
        end_date=args.end_date,
        departure_exposure_nights=exposure_dates,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as output:
        for record in records:
            output.write(json.dumps(record.to_dict(), sort_keys=True) + "\n")
    print(
        f"Wrote {len(records)} training records to {args.output}; "
        f"parsed {len(cryotech.rows)} Cryotech rows with {len(cryotech.issues)} issues."
    )


def _read_exposure_dates(path):
    if path is None:
        return ()
    return tuple(
        line.strip()
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


if __name__ == "__main__":
    main()
