# NeoFontLite V2

A lighter vector-derived sibling of NeoFont. The sole geometry source is
`../neofont/glyphs/A.svg` through `Z.svg`. NeoFont is never modified.
The old PNG in `source/` is retained as historical provenance only; it is
not read by the V2 builder and is not the design authority.

## Deterministic build

Build-only dependencies: fonttools, brotli, shapely==2.1.2 (not app requirements).

```text
python tools/neofontlite_build.py
```

Closed canonical polygons are combined with even-odd fill. A negative vector
offset with mitred joins thins the filled strokes and expands counters. A
per-letter binary search targets 62.5% of the original filled area at equal cap
height. This is a measurable weight proxy, not a claim about perceptual weight.
Both axes use the SAME scale: no horizontal compression or raster operations.
Original components and counter counts must survive or the build fails.

Minimal optical cleanup removes sub-one-font-unit near-collinear kinks before
offsetting (<0.14% of cap height); a mitre limit of 2 prevents corner spikes.
No hand-drawn replacement shapes, round joins, or substituted glyphs are used.
Angular cuts and relative widths come from the original outlines. Offset geometry
is uniformly normalized to a 720-unit cap height and baseline zero, with 40-unit
side bearings. This small uniform renormalization may modestly change advance
widths; actual long UI labels are browser-tested.

Family remains NeoFontLite, weight 300, version 2.000, UPM 1000. Uppercase A–Z,
lowercase aliases and blank 320-unit space preserve the existing contract.
SVG and TTF outlines use the same integer coordinates. Timestamps are fixed.
`build-metrics.json` records canonical source hashes, weights and topology.

Open `preview.html` locally to inspect A–Z and real Sektor/navigation labels at
12, 16 and 24px. Existing Gateway/Sektor CSS keeps using the same family/URLs.
The static asset version is bumped when replacing the binaries.
