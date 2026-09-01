# NeoSubZero historical frost dataset

The historical pipeline is deliberately offline. It reads exported Cryotech
applications plus an exported KRFD observation file and writes one normalized
JSON artifact; it does not use Flask routes, production database storage, or live
weather calls.

```powershell
python scripts/build_neosubzero_frost_dataset.py `
  --cryotech cryotech.csv `
  --weather krfd-asos.csv `
  --start-date 2025-10-01 `
  --end-date 2026-04-30 `
  --exposure-dates departure-exposure-nights.txt `
  --output artifacts/neosubzero-frost-training.json
```

`departure-exposure-nights.txt` contains one operational-night date per line,
optionally followed by the known departure-opportunity count, such as
`2026-01-05,12`. It is the evidence that relevant departure exposure occurred.
A Monday night entry represents its default Tuesday 0200–0400 local exposure window. An
event-free night not listed in this file stays `unlabeled`; the pipeline never
turns absence of a Cryotech spray into a negative by itself.

The Cryotech parser accepts common header aliases for application ID, date,
start/end, tail, truck, fluid/type, surface, reason, precipitation, gallons,
concentration, and notes. Frost applications become positive examples.
The artifact preserves raw truck/application rows, deduplicated aircraft
treatment events, night-level evidence metadata, and model-ready records.
Stable application IDs group first. Without one, same-night/tail/reason rows
group only inside a conservative 30-minute start-time window. Pretreat and
other applications remain separate outcomes. Invalid
rows are reported with their CSV row number without discarding valid rows.

Night evidence uses four explicit classes:

- `confirmed_positive`: one or more deduplicated departure Frost treatments.
- `uncertain_pretreat`: Pretreat occurred without confirmed departure Frost.
- `clean_negative`: confirmed departure exposure with neither Pretreat nor Frost.
- `unlabeled`: relevant departure exposure was not established.

The artifact records departure-opportunity counts when supplied, unique Frost
and Pretreat event counts, treated percentage, a one-or-two-event weak-evidence
flag, a broader-treatment flag, and Pretreat-plus-later-Frost flags. These are
input facts for later model comparison, not hard-coded training weights.

The weather adapter accepts an offline ASOS/IEM-style CSV. Its common fields
include `station`, `valid`, `tmpf`, `dwpf`, `relh`, `sknt`, `gust`, `drct`,
`vsby`, `wxcodes`, and `skyc*`. Timestamps default to UTC. The normalized
records include the nearest observation and clean three-hour trend features.

A real Cryotech export is still required to confirm its exact headers, whether
one application spans multiple fluid rows through a stable application ID,
and the units/formats used for truck, gallons, and concentration.
