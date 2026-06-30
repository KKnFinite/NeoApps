# NeoFont build tools

These scripts were used to build the real NeoFont files from the locked Neo alphabet specimen.

## Source

app/static/fonts/neofont/source/neo-alphabet-specimen.png

## Output

app/static/fonts/neofont/NeoFont.ttf
app/static/fonts/neofont/NeoFont.woff2
app/static/fonts/neofont/glyphs/A.svg through Z.svg

## Local dependencies

These are local build dependencies only:

- pillow
- opencv-python
- fonttools
- brotli

They are not required at app runtime.

## Script order

Run from repo root:

1. python tools/neofont_prepare_preview.py
2. python tools/neofont_crop_letters.py
3. python tools/neofont_trace_svg.py
4. python tools/neofont_build_font.py
5. python tools/neofont_preview.py

## Hard rules

Do not regenerate or approximate the letters with AI or another sci-fi font.

The glyphs must come from the locked Neo alphabet specimen only.

NeoFont is opt-in only in app CSS. Do not apply it globally to body text, buttons, tables, forms, normal navigation text, or regular UI copy.
