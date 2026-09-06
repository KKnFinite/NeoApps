"""NeoFontLite V2: canonical SVG polygon offsets, never raster tracing.

Build-only dependencies: fonttools, brotli, shapely==2.1.2.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path
from xml.etree import ElementTree as ET
from shapely import affinity
from shapely.geometry import Polygon
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'app/static/fonts/neofont/glyphs'
OUTPUT = ROOT / 'app/static/fonts/neofontlite'
CAP_HEIGHT, BEARING, TARGET_WEIGHT = 720, 40, .625


def polygons(shape):
    return [shape] if shape.geom_type == 'Polygon' else list(shape.geoms)


def topology(shape):
    return sorted(len(p.interiors) for p in polygons(shape))


def canonical(path):
    """Canonical input consists of closed M/L polygons with even-odd fill."""
    shape = Polygon()
    for element in ET.parse(path).iter('{http://www.w3.org/2000/svg}path'):
        data = element.attrib['d']
        if set(re.findall('[A-Za-z]',data)) - {'M','L','Z'}:
            raise ValueError(f'Unsupported SVG commands: {path}')
        for contour in data.strip().split('Z'):
            if not contour.strip():
                continue
            numbers = list(map(float, re.findall(r'-?\d+(?:\.\d+)?', contour)))
            ring = Polygon(list(zip(numbers[::2], numbers[1::2])))
            if not ring.is_valid:
                raise ValueError(f'Invalid contour: {path}')
            shape = shape.symmetric_difference(ring)
    if shape.is_empty or not shape.is_valid:
        raise ValueError(f'Invalid glyph: {path}')
    x0,y0,x1,y1 = shape.bounds
    scale = CAP_HEIGHT/(y1-y0)
    return affinity.scale(affinity.translate(shape,-x0,-y0),scale,scale,origin=(0,0))


def lighter(original):
    # Optical cleanup of sub-unit kinks (<0.14% cap height) prevents tiny
    # near-collinear canonical edges growing into mitre spikes during offset.
    clean = original.simplify(1, preserve_topology=True)
    low, high = 0., 60.
    for _ in range(48):
        inset = (low+high)/2
        candidate = clean.buffer(-inset, join_style='mitre', mitre_limit=2)
        if candidate.is_empty:
            high = inset
            continue
        x0,y0,x1,y1 = candidate.bounds
        scale = CAP_HEIGHT/(y1-y0)
        if candidate.area*scale*scale/original.area > TARGET_WEIGHT:
            low = inset
        else:
            high = inset
    shape = clean.buffer(-low, join_style='mitre', mitre_limit=2)
    if topology(shape) != topology(original):
        raise ValueError('Thinning changed counters/components; optical correction required')
    x0,y0,x1,y1 = shape.bounds
    scale = CAP_HEIGHT/(y1-y0)
    return affinity.scale(affinity.translate(shape,-x0,-y0),scale,scale,origin=(0,0)), low


def build(output=OUTPUT):
    output = Path(output)
    if output.resolve().is_relative_to(SOURCE.parent.resolve()):
        raise ValueError('Never overwrite NeoFont')
    letters = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
    shapes = {c:canonical(SOURCE/f'{c}.svg') for c in letters}
    derived = {c:lighter(shapes[c]) for c in letters}
    (output/'glyphs').mkdir(parents=True, exist_ok=True)
    glyphs = {n:TTGlyphPen(None).glyph() for n in ('.notdef','space')}
    metrics = {'.notdef':(400,0),'space':(320,0)}
    report = {}
    for c,(shape,inset) in derived.items():
        pen,paths = TTGlyphPen(None),[]
        for polygon in polygons(shape):
            for hole,ring in [(False,polygon.exterior)]+[(True,r) for r in polygon.interiors]:
                points = [(round(x+BEARING),round(CAP_HEIGHT-y)) for x,y in list(ring.coords)[:-1]]
                area = sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(points,points[1:]+points[:1]))
                if (not hole and area>0) or (hole and area<0):
                    points.reverse()
                pen.moveTo(points[0])
                for point in points[1:]:
                    pen.lineTo(point)
                pen.closePath()
                paths.append('M '+' L '.join(f'{x},{CAP_HEIGHT-y}' for x,y in points)+' Z')
        glyphs[c] = pen.glyph()
        width = round(shape.bounds[2])+BEARING*2
        metrics[c] = (width,BEARING)
        (output/f'glyphs/{c}.svg').write_text(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} 720"><path fill="black" fill-rule="evenodd" d="{" ".join(paths)}"/></svg>\n',encoding='utf-8')
        report[c] = dict(source_sha256=hashlib.sha256((SOURCE/f'{c}.svg').read_bytes()).hexdigest(),area_ratio=round(shape.area/shapes[c].area,6),inset=round(inset,4),topology=topology(shape),advance=width)
    fb = FontBuilder(1000,isTTF=True)
    fb.setupGlyphOrder(['.notdef','space']+list(letters))
    fb.setupCharacterMap({32:'space',**{ord(c):c for c in letters},**{ord(c.lower()):c for c in letters}})
    fb.setupGlyf(glyphs); fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(ascent=850,descent=-150)
    fb.setupOS2(sTypoAscender=850,sTypoDescender=-150,usWinAscent=850,usWinDescent=150,sCapHeight=720,usWeightClass=300)
    fb.setupNameTable(dict(familyName='NeoFontLite',styleName='Regular',uniqueFontIdentifier='NeoFontLite Regular 2.000',fullName='NeoFontLite Regular',psName='NeoFontLite-Regular',version='Version 2.000'))
    fb.setupPost(); fb.setupMaxp(); fb.font.recalcTimestamp=False
    fb.font['head'].created=fb.font['head'].modified=3861000000
    fb.font.save(output/'NeoFontLite.ttf')
    font=TTFont(output/'NeoFontLite.ttf',recalcTimestamp=False)
    font.flavor='woff2'; font.save(output/'NeoFontLite.woff2')
    (output/'build-metrics.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print('Built 26 vector glyphs; topology preserved; target filled area 62.5%.')


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=OUTPUT)
    build(parser.parse_args().output)
