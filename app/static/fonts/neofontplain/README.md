# NeoFontPlain v1

Original NeoApps workhorse sans. The existing Live Counts pilot remains the
only application use. NeoFont and NeoFontLite are unchanged.

## Design and source

`source/geometry.py` is the canonical, hand-authored vector design: M/L/Q/Z
centerlines in 1000 design units, with explicit per-character advances. No
third-party font, raster tracing, downloaded outline, or existing NeoFont glyph
is used. The existing font tools informed only the packaging/build architecture.

Long flat curve reaches, squared shoulders and bevelled angular joins give
restrained Neo character. Lowercase is independently drawn, with single-storey
`a` and `g`. `I` has bars, `l` has a foot, `1` an angled flag and base; `0` has
an internal diagonal, distinct from `O`. Open `C/G` and generous counters favor
small text. All digits have advance 620; both weights retain identical advances.

Regular 400 uses 64-unit strokes; SemiBold 600 uses 88. Vector expansion is
unioned before contour generation, preserving real counters rather than leaving
overlapping strokes in the fonts. Quadratic source curves are deterministically
sampled at 16 intervals and converted to integer contours. Optical vertical
alignment normalizes caps/digits to 720 and lowercase bodies to 520; ascenders
reach 760, with descenders below the baseline. Line metrics are 900/-250.
Outlines are unhinted and use the browser's native antialiasing; the specimen
includes 12–20px samples for visual review, not a claim of device certification.

## Repertoire and outputs

291 mapped characters: the original 116 (printable ASCII, NBSP, £ € ° × ÷, minus, en/em dashes,
middle dot/bullet, ellipsis, four arrows, checkmark, and typographic quote code
points; v1 quotes intentionally share straight-quote outlines), plus 175 Western
Latin additions. This includes every Latin-1 alphabetic letter from U+00C0–00FF,
Œ/œ, Š/š, Ž/ž, Ÿ, ẞ and the decomposable Latin Extended-A letters whose accents
are supported, plus Ł/ł. It is not a claim of complete Latin Extended-A coverage.

`source/western.py` composes finalized approved base contours with reusable grave,
acute, circumflex, tilde, diaeresis, ring, cedilla, macron, breve, dot, double acute,
caron and ogonek marks. Accented i replaces the dot only in derived characters.
Marks fit the existing 900/-250 line metrics; tall ascenders use compact marks.
Æ/æ and Œ/œ join existing letter outlines; Ø/ø, Ð and Ł/ł add bars; thorn, eth,
sharp s and capital sharp s have explicit new skeletons. Base outlines, advances,
weights and line metrics are unchanged, verified against the committed v1 fonts.
Coverage is precomposed Unicode (NFC); arbitrary combining-mark positioning is
not implemented. Application text handling and typography rules are unchanged.

- `NeoFontPlain-Regular.ttf` / `.woff2` — weight 400
- `NeoFontPlain-SemiBold.ttf` / `.woff2` — weight 600
- `glyphs/regular/uniXXXX.svg`, `glyphs/semibold/uniXXXX.svg` — generated outlines
  (Unicode filenames distinguish uppercase/lowercase on case-insensitive disks)
- `build-metrics.json` — source hash, weights, advances, bounds and contour counts
- `preview.html` — standalone local browser specimen, both actual weights

Spaces have no contours; each weight also includes a visible `.notdef` glyph.
The specimen is standalone and does not import app CSS.

## Deterministic build

Use an isolated build environment, not application requirements:

```powershell
python -m venv instance/neofontplain-build
instance/neofontplain-build/Scripts/python -m pip install -r app/static/fonts/neofontplain/source/requirements-build.txt
instance/neofontplain-build/Scripts/python tools/neofontplain_build.py
instance/neofontplain-build/Scripts/python -m unittest discover -s tests -p test_neofontplain.py -v
```

Pinned build-only dependencies, stable glyph/contour order, integer rounding,
LF output and fixed font timestamps make repeated builds byte-identical in the
pinned environment. `--output instance/neofontplain-rebuild` writes a separate
copy for comparison. Do not hand-edit generated glyphs or binaries; edit source
geometry and rebuild. No remote or application/database access is needed.

Open `preview.html` directly or through a local static server. It intentionally
contains review labels and samples that are not application copy.
