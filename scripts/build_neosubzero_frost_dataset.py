"""Build an offline NeoSubZero frost-history JSON artifact."""

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from app.services.neosubzero_frost_history import (
    CsvHistoricalWeatherProvider,
    build_frost_history_dataset,
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
            "Optional supplemental exposure file with one YYYY-MM-DD operational-"
            "night date per line and an optional known comma-separated departure "
            "count. Normal non-holiday Monday-Thursday nights are reconstructed "
            "without guessed counts."
        ),
    )
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    cryotech = parse_cryotech_csv(args.cryotech)
    weather = CsvHistoricalWeatherProvider(args.weather)
    exposure_dates, opportunity_counts = _read_exposure_manifest(
        args.exposure_dates
    )
    dataset = build_frost_history_dataset(
        cryotech.rows,
        weather,
        start_date=args.start_date,
        end_date=args.end_date,
        departure_exposure_nights=exposure_dates,
        departure_opportunities_by_night=opportunity_counts,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as output:
        json.dump(dataset.to_dict(), output, sort_keys=True, separators=(",", ":"))
        output.write("\n")
    print(
        f"Wrote {len(dataset.training_records)} training records and "
        f"{len(dataset.treatment_events)} treatment events to {args.output}; "
        f"preserved {len(cryotech.rows)} Cryotech rows with "
        f"{len(cryotech.issues)} issues."
    )


def _read_exposure_manifest(path):
    if path is None:
        return (), {}
    dates = []
    counts = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        values = [value.strip() for value in line.split(",")]
        dates.append(values[0])
        if len(values) > 1 and values[1]:
            counts[values[0]] = int(values[1])
    return tuple(dates), counts


if __name__ == "__main__":
    main()
