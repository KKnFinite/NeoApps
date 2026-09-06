# NeoFontLite

Thin display alphabet traced exclusively from the approved RGB 1448×1086 specimen.
Source SHA-256: `50d9b3faefae694d2a24eb16e9ff5f49c0049b31517915a447c960a9470c3c15`.
The source PNG is a byte-for-byte copy. Existing NeoFont is not modified.

## Build

From repository root, with the same build-only toolchain used for NeoFont
(Pillow, OpenCV, fonttools, brotli):

```text
python tools/neofontlite_build.py app/static/fonts/neofontlite/source/neo-font-lite-alphabet-specimen.png
```

Explicit A–I / J–R / S–Z source cells exclude the decorative title/sample text.
Luminance threshold 170 removes the surrounding blue glow, without dilation,
erosion, sharpening, or added stroke weight. OpenCV contour tracing uses a
0.45-source-pixel tolerance. Counters and disconnected strokes are preserved.
One uniform 720/75 scale preserves relative widths/heights, including Q's
descender; glyphs are not individually stretched to a target box. SVGs and
TrueType contours derive from the same outlines. Font timestamps are fixed.

Outputs: A–Z SVGs, NeoFontLite.ttf, NeoFontLite.woff2. Family: NeoFontLite;
weight: 300. Lowercase code points alias their uppercase shapes, as in NeoFont;
space is blank. No numerals/punctuation or substitute glyphs are invented.

Open preview.html locally (file://) to compare all letters and NEOGATEWAY, PORTAL, HOME, NODES,
MENU, SETTINGS, DASHBOARD with the source. This is specimen-based raster tracing,
not recovery of an original vector font. Anti-aliasing/glow is not font geometry.

Production use is restricted to the mobile NeoGateway product heading at widths
up to 900px. Desktop branding, other nodes, subtitle, dock, and body copy retain
their existing fonts. The dedicated CSS is only linked on Gateway.
