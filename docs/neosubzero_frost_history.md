# NeoSubZero historical frost dataset

The historical pipeline is deliberately offline. It reads exported Cryotech
applications plus an exported KRFD observation file and writes normalized
JSON Lines; it does not use Flask routes, production database storage, or live
weather calls.

```powershell
python scripts/build_neosubzero_frost_dataset.py `
  --cryotech cryotech.csv `
  --weather krfd-asos.csv `
  --start-date 2025-10-01 `
  --end-date 2026-04-30 `
  --exposure-dates departure-exposure-nights.txt `
  --output artifacts/neosubzero-frost-training.jsonl
```

`departure-exposure-nights.txt` contains one operational-night date per line.
It is the evidence that relevant departure exposure occurred. A Monday night
entry represents its default Tuesday 0200–0400 local exposure window. An
event-free night not listed in this file stays `unlabeled`; the pipeline never
turns absence of a Cryotech spray into a negative by itself.

The Cryotech parser accepts common header aliases for application ID, date,
start/end, tail, truck, fluid/type, surface, reason, precipitation, gallons,
concentration, and notes. Frost applications become positive examples.
Pretreat and other applications remain separate unlabeled outcomes. Invalid
rows are reported with their CSV row number without discarding valid rows.

The weather adapter accepts an offline ASOS/IEM-style CSV. Its common fields
include `station`, `valid`, `tmpf`, `dwpf`, `relh`, `sknt`, `gust`, `drct`,
`vsby`, `wxcodes`, and `skyc*`. Timestamps default to UTC. The normalized
records include the nearest observation and clean three-hour trend features.

A real Cryotech export is still required to confirm its exact headers, whether
one application spans multiple fluid rows through a stable application ID,
and the units/formats used for truck, gallons, and concentration.
