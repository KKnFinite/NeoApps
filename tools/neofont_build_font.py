from pathlib import Path
import cv2
import numpy as np
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont

UPM = 1000
ASCENT = 850
DESCENT = -150
CAP_HEIGHT = 720
LEFT_BEARING = 45
RIGHT_BEARING = 65
MIN_AREA = 25
EPSILON = 0.9

letters_dir = Path("app/static/fonts/neofont/work/letters")
out_dir = Path("app/static/fonts/neofont")
out_dir.mkdir(parents=True, exist_ok=True)

letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

def signed_area(points):
    area = 0
    for i in range(len(points)):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % len(points)]
        area += x1 * y2 - x2 * y1
    return area / 2

def glyph_from_png(path):
    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise FileNotFoundError(path)

    _, thresh = cv2.threshold(img, 127, 255, cv2.THRESH_BINARY)

    ys, xs = np.where(thresh > 0)
    if len(xs) == 0:
        pen = TTGlyphPen(None)
        return pen.glyph(), 500, 0

    x_min, x_max = int(xs.min()), int(xs.max())
    y_min, y_max = int(ys.min()), int(ys.max())

    src_w = max(1, x_max - x_min)
    src_h = max(1, y_max - y_min)

    scale = CAP_HEIGHT / src_h
    glyph_w = int(round(src_w * scale))
    advance = glyph_w + LEFT_BEARING + RIGHT_BEARING

    contours, hierarchy = cv2.findContours(
        thresh,
        cv2.RETR_TREE,
        cv2.CHAIN_APPROX_SIMPLE
    )

    pen = TTGlyphPen(None)

    if hierarchy is None:
        return pen.glyph(), advance, LEFT_BEARING

    hierarchy = hierarchy[0]
    items = []

    for i, contour in enumerate(contours):
        if cv2.contourArea(contour) < MIN_AREA:
            continue

        approx = cv2.approxPolyDP(contour, EPSILON, True)
        pts = approx.reshape(-1, 2)

        if len(pts) < 3:
            continue

        mapped = []
        for x, y in pts:
            fx = int(round((float(x) - x_min) * scale + LEFT_BEARING))
            fy = int(round((float(y_max) - float(y)) * scale))
            mapped.append((fx, fy))

        parent = hierarchy[i][3]
        is_hole = parent != -1

        area = signed_area(mapped)

        # TrueType convention:
        # outer contours clockwise, inner hole contours counter-clockwise.
        if (not is_hole and area > 0) or (is_hole and area < 0):
            mapped = list(reversed(mapped))

        items.append((0 if not is_hole else 1, mapped))

    for _, mapped in sorted(items, key=lambda item: item[0]):
        pen.moveTo(mapped[0])
        for pt in mapped[1:]:
            pen.lineTo(pt)
        pen.closePath()

    return pen.glyph(), advance, LEFT_BEARING

fb = FontBuilder(UPM, isTTF=True)

glyph_order = [".notdef", "space"] + list(letters)
fb.setupGlyphOrder(glyph_order)

glyphs = {
    ".notdef": TTGlyphPen(None).glyph(),
    "space": TTGlyphPen(None).glyph(),
}

metrics = {
    ".notdef": (500, 0),
    "space": (400, 0),
}

for letter in letters:
    glyph, advance, lsb = glyph_from_png(letters_dir / f"{letter}.png")
    glyphs[letter] = glyph
    metrics[letter] = (advance, lsb)

fb.setupGlyf(glyphs)
fb.setupHorizontalMetrics(metrics)
fb.setupHorizontalHeader(ascent=ASCENT, descent=DESCENT)

cmap = {ord(c): c for c in letters}
cmap.update({ord(c.lower()): c.upper() for c in letters})
cmap[ord(" ")] = "space"
fb.setupCharacterMap(cmap)

fb.setupOS2(
    sTypoAscender=ASCENT,
    sTypoDescender=DESCENT,
    usWinAscent=ASCENT,
    usWinDescent=abs(DESCENT),
    sCapHeight=CAP_HEIGHT,
)

fb.setupNameTable({
    "familyName": "NeoFont",
    "styleName": "Regular",
    "uniqueFontIdentifier": "NeoFont Regular",
    "fullName": "NeoFont Regular",
    "psName": "NeoFont-Regular",
    "version": "Version 1.000",
})

fb.setupPost()
fb.setupMaxp()

ttf_path = out_dir / "NeoFont.ttf"
woff2_path = out_dir / "NeoFont.woff2"

fb.font.save(ttf_path)

woff2 = TTFont(ttf_path)
woff2.flavor = "woff2"
woff2.save(woff2_path)

print("created:", ttf_path)
print("created:", woff2_path)
print("done")
