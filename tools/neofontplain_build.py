"""Build original NeoFontPlain vectors; no raster input or third-party outlines.

Build-only packages are pinned in neofontplain/source/requirements-build.txt.
"""
import argparse
import hashlib
import importlib.util
import json
import re
from pathlib import Path

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont
from shapely.geometry import LineString
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'app/static/fonts/neofontplain'
SOURCE = OUTPUT / 'source/geometry.py'
WEIGHTS = {'Regular': (400, 64), 'SemiBold': (600, 88)}


def source_glyphs():
    spec = importlib.util.spec_from_file_location('neofontplain_geometry', SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.GLYPHS


def centerline(path):
    """Deterministically subdivide original quadratic vectors, never pixels.

    Sixteen intervals per quadratic keep curve sampling below one design unit
    for these control polygons (one thousandth em), even at the largest shoulder.
    """
    tokens = re.findall(r'[MLQZ]|-?\d+(?:\.\d+)?', path)
    points, i = [], 0
    while i < len(tokens):
        command = tokens[i]
        i += 1
        if command in ('M', 'L'):
            points.append((float(tokens[i]), float(tokens[i+1])))
            i += 2
        elif command == 'Q':
            a = points[-1]
            b = float(tokens[i]), float(tokens[i+1])
            c = float(tokens[i+2]), float(tokens[i+3])
            i += 4
            for step in range(1, 17):
                t = step / 16
                points.append(tuple((1-t)**2*a[j]+2*(1-t)*t*b[j]+t*t*c[j] for j in (0, 1)))
        elif command == 'Z':
            points.append(points[0])
        else:
            raise ValueError(f'Unsupported source command: {command}')
    return LineString(points)


def contours(paths, stroke):
    if not paths:
        return []
    shape = unary_union([centerline(p).buffer(stroke/2, cap_style='flat', join_style='bevel') for p in paths])
    if shape.is_empty or not shape.is_valid:
        raise ValueError('Invalid expanded vector')
    polygons = [shape] if shape.geom_type == 'Polygon' else list(shape.geoms)
    result = []
    for polygon in sorted(polygons, key=lambda p: p.bounds):
        for hole, ring in [(False, polygon.exterior)]+[(True, r) for r in sorted(polygon.interiors, key=lambda r: r.bounds)]:
            points = []
            for x, y in list(ring.coords)[:-1]:
                point = round(x), round(y)
                if not points or points[-1] != point:
                    points.append(point)
            if points[-1] == points[0]:
                points.pop()
            area = sum(a[0]*b[1]-b[0]*a[1] for a, b in zip(points, points[1:]+points[:1]))
            if len(points) < 3 or area == 0:
                raise ValueError('Degenerate rounded contour')
            # TrueType black-on-right winding; holes have the opposite direction.
            if (not hole and area > 0) or (hole and area < 0):
                points.reverse()
            first = min(range(len(points)), key=points.__getitem__)
            result.append(points[first:]+points[:first])
    return result


def build(output=OUTPUT):
    output = Path(output)
    for protected in ('neofont', 'neofontlite'):
        if output.resolve().is_relative_to((OUTPUT.parent/protected).resolve()):
            raise ValueError('Existing font families are read-only')
    output.mkdir(parents=True, exist_ok=True)
    glyphs = source_glyphs()
    # Git may check text out as CRLF on Windows; hash canonical UTF-8/LF source.
    source_hash = hashlib.sha256(SOURCE.read_text(encoding='utf-8').encode('utf-8')).hexdigest()
    report = {'family': 'NeoFontPlain', 'source_sha256': source_hash, 'weights': {}}
    for style, (weight, stroke) in WEIGHTS.items():
        glyph_dir = output/'glyphs'/style.lower()
        glyph_dir.mkdir(parents=True, exist_ok=True)
        outlines, metrics, cmap, records = {}, {}, {}, {}
        entries = [('.notdef', 600, ['M 90 0 L 90 720 L 510 720 L 510 0 Z', 'M 90 0 L 510 720'])]
        entries += [(f'uni{ord(c):04X}', *glyphs[c]) for c in sorted(glyphs, key=ord)]
        for name, advance, paths in entries:
            rings = contours(paths, stroke)
            # Optical vertical alignment after stroke expansion: horizontal
            # top/bottom strokes must not sit above/below flat-ended stems.
            char = chr(int(name[3:], 16)) if name != '.notdef' else ''
            reference, height = rings, None
            if char in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789' and char:
                height = 720
                if char == 'Q':
                    reference = contours(glyphs['O'][1], stroke)
            elif char in 'acemnorsuvwxz' and char:
                height = 520
            elif char in 'bdhklft' and char:
                height = 680 if char == 't' else 760
            elif char in 'gpq':
                height = 520
                reference = contours([paths[0] if char in 'gq' else paths[1]], stroke)
            if height:
                bottom = min(y for p in reference for _, y in p)
                top = max(y for p in reference for _, y in p)
                rings = [[(x, round((y-bottom)*height/(top-bottom))) for x, y in p] for p in rings]
            pen = TTGlyphPen(None)
            for points in rings:
                pen.moveTo(points[0])
                for point in points[1:]:
                    pen.lineTo(point)
                pen.closePath()
            outlines[name] = pen.glyph()
            left = min((x for points in rings for x, _ in points), default=0)
            metrics[name] = advance, left
            bounds = [min(x for p in rings for x, _ in p), min(y for p in rings for _, y in p),
                      max(x for p in rings for x, _ in p), max(y for p in rings for _, y in p)] if rings else [0]*4
            if bounds[0] < 0 or bounds[2] > advance or bounds[1] < -250 or bounds[3] > 900:
                raise ValueError(f'{style} {name} exceeds advance/line bounds: {bounds}')
            if name != '.notdef':
                cmap[int(name[3:], 16)] = name
            data = ' '.join('M '+' L '.join(f'{x},{900-y}' for x, y in points)+' Z' for points in rings)
            svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {advance} 1150"><path fill="black" fill-rule="evenodd" d="{data}"/></svg>\n'
            (glyph_dir/f'{name}.svg').write_text(svg, encoding='utf-8', newline='\n')
            records[name] = {'advance': advance, 'bounds': bounds, 'contours': len(rings)}
        fb = FontBuilder(1000, isTTF=True)
        fb.setupGlyphOrder(list(outlines))
        fb.setupCharacterMap(cmap)
        fb.setupGlyf(outlines)
        fb.setupHorizontalMetrics(metrics)
        fb.setupHorizontalHeader(ascent=900, descent=-250, lineGap=0)
        fb.setupOS2(version=4, sTypoAscender=900, sTypoDescender=-250, sTypoLineGap=0,
                    usWinAscent=900, usWinDescent=250, sCapHeight=720, sxHeight=520,
                    usWeightClass=weight, fsSelection=128 | (64 if weight == 400 else 0))
        fb.setupNameTable(dict(familyName='NeoFontPlain', styleName=style,
                              typographicFamily='NeoFontPlain', typographicSubfamily=style,
                              uniqueFontIdentifier=f'NeoFontPlain {style} 1.000',
                              fullName=f'NeoFontPlain {style}', psName=f'NeoFontPlain-{style}',
                              version='Version 1.000', copyright='Original NeoApps vector geometry.'))
        fb.setupPost()
        fb.setupMaxp()
        fb.font.recalcTimestamp = False
        fb.font['head'].created = fb.font['head'].modified = 3861000000
        ttf = output/f'NeoFontPlain-{style}.ttf'
        fb.font.save(ttf)
        font = TTFont(ttf, recalcTimestamp=False)
        font.flavor = 'woff2'
        font.save(ttf.with_suffix('.woff2'))
        report['weights'][style] = {'weight': weight, 'stroke': stroke, 'glyphs': records}
    (output/'build-metrics.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8', newline='\n')
    print(f'Built {len(glyphs)} characters in Regular 400 and SemiBold 600 from original vectors.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=OUTPUT)
    build(parser.parse_args().output)
