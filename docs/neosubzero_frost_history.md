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

The pipeline reconstructs meaningful 0200–0400 local departure exposure for
normal Monday-through-Thursday operational nights. Exact historical flight
counts are unknown, so these reconstructed records keep opportunity count and
treated percentage null. `departure-exposure-nights.txt` is optional additional
evidence for exceptional nights; a known count may follow the date, such as
`2026-01-05,12`, but counts must never be guessed.

Reconstruction excludes Memorial Day, Labor Day, July 4, Thanksgiving and the
day before, Christmas Eve and Day, New Year's Eve and Day, and MLK Day beginning
in 2025. Presidents Day, Juneteenth, Veterans Day, and unlisted holidays are not
excluded. An excluded date cannot become a clean-negative record, although an
actual Frost application on that date remains positive evidence.

The Cryotech parser accepts common header aliases for application ID, date,
start/end, tail, truck, fluid/type, surface, reason, precipitation, gallons,
concentration, and notes. It preserves raw Reason code, raw description, and
normalized description separately. The authoritative Reason mapping includes:

- `F` → Frost and confirmed Frost evidence.
- `P` → Preventative De-Ice/Anti-Ice and Pretreat evidence.
- `FG`, `FZFG`, `FZDZ`, `FZRA`, `GR`, `PL`, `IC`, `SG`, `SN`, `DZ`, and `CS`
  → their observed Fog/freezing precipitation/hail/ice/snow/drizzle/cold-soak
  descriptions.
- `GS` → either Small Hail or Snow Pellets. Its supplied description is retained
  and normalized; the code alone is intentionally not treated as unique.

Frost applications become positive examples.
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
