"""Trace the approved specimen, following the NeoFont OpenCV/FontBuilder pipeline.

Build-only dependencies: Pillow, opencv-python-headless, fonttools, brotli.
No existing NeoFont files are read as glyph sources or modified.
"""
import argparse
import hashlib
import shutil
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont

SOURCE_SHA = '50d9b3faefae694d2a24eb16e9ff5f49c0049b31517915a447c960a9470c3c15'
ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'app/static/fonts/neofontlite'
# Explicit source cells exclude the decorative title and example words. Some
# glyphs have disconnected strokes, so connected components are NOT letters.
ROWS = (
    ('ABCDEFGHI', 310, 415, 400, ((60,200),(220,350),(380,500),(520,650),(680,800),(830,950),(970,1100),(1130,1270),(1300,1375))),
    ('JKLMNOPQR', 470, 579, 558, ((60,170),(200,310),(340,455),(480,625),(650,780),(800,935),(950,1080),(1100,1230),(1270,1385))),
    ('STUVWXYZ', 625, 728, 714, ((100,230),(250,370),(400,530),(550,690),(710,883),(907,1030),(1050,1190),(1210,1350))),
)
THRESHOLD = 170  # Luminous stroke core, excluding the blue glow; same NeoFont threshold.
SCALE = 720 / 75  # One uniform x/y scale for ALL glyphs; retain relative proportions.
BEARING = 40


def build(source, output=OUTPUT):
    source = Path(source)
    if hashlib.sha256(source.read_bytes()).hexdigest() != SOURCE_SHA:
        raise ValueError('Approved specimen SHA-256 mismatch; no outputs written.')
    image = Image.open(source)
    if image.size != (1448, 1086) or image.mode != 'RGB':
        raise ValueError('Unexpected approved specimen format.')
    gray = np.array(image.convert('L'))
    (output / 'source').mkdir(parents=True, exist_ok=True)
    (output / 'glyphs').mkdir(exist_ok=True)
    target = output / 'source/neo-font-lite-alphabet-specimen.png'
    if source.resolve() != target.resolve():
        shutil.copyfile(source, target)
    letters = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
    glyphs = {name: TTGlyphPen(None).glyph() for name in ('.notdef','space')}
    metrics = {'.notdef': (400,0), 'space':(320,0)}
    for row, top, bottom, baseline, cells in ROWS:
        for letter, (left,right) in zip(row,cells,strict=True):
            _, mask = cv2.threshold(gray[top:bottom,left:right],THRESHOLD,255,cv2.THRESH_BINARY)
            ys,xs = np.where(mask>0)
            if not len(xs):
                raise ValueError(f'Empty glyph {letter}')
            xmin,xmax,ymin,ymax = int(xs.min()),int(xs.max()),int(ys.min()),int(ys.max())
            contours,hierarchy = cv2.findContours(mask,cv2.RETR_TREE,cv2.CHAIN_APPROX_SIMPLE)
            pen = TTGlyphPen(None)
            paths=[]
            for i,contour in enumerate(contours):
                if cv2.contourArea(contour)<2:
                    continue
                points=cv2.approxPolyDP(contour,0.45,True).reshape(-1,2)
                if len(points)<3:
                    continue
                paths.append('M '+' L '.join(f'{x-xmin+1},{y-ymin+1}' for x,y in points)+' Z')
                mapped=[(round((int(x)-xmin)*SCALE+BEARING),round((baseline-top-int(y))*SCALE)) for x,y in points]
                depth=0; parent=int(hierarchy[0][i][3])
                while parent!=-1:
                    depth+=1; parent=int(hierarchy[0][parent][3])
                area=sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(mapped,mapped[1:]+mapped[:1]))
                if (depth%2==0 and area>0) or (depth%2==1 and area<0):
                    mapped.reverse()
                pen.moveTo(mapped[0])
                for point in mapped[1:]: pen.lineTo(point)
                pen.closePath()
            glyphs[letter]=pen.glyph()
            metrics[letter]=(round((xmax-xmin)*SCALE)+BEARING*2,BEARING)
            svg=f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {xmax-xmin+2} {ymax-ymin+2}"><path fill="black" fill-rule="evenodd" d="{" ".join(paths)}"/></svg>\n'
            (output / f'glyphs/{letter}.svg').write_text(svg,encoding='utf-8')
            print(letter, 'source bounds', (left+xmin,top+ymin,left+xmax,top+ymax), 'advance',metrics[letter][0])
    fb=FontBuilder(1000,isTTF=True)
    fb.setupGlyphOrder(['.notdef','space']+list(letters))
    fb.setupCharacterMap({32:'space',**{ord(c):c for c in letters},**{ord(c.lower()):c for c in letters}})
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(ascent=850,descent=-150)
    fb.setupOS2(sTypoAscender=850,sTypoDescender=-150,usWinAscent=850,usWinDescent=150,sCapHeight=720,usWeightClass=300)
    fb.setupNameTable(dict(familyName='NeoFontLite',styleName='Regular',uniqueFontIdentifier='NeoFontLite Regular 1.000',fullName='NeoFontLite Regular',psName='NeoFontLite-Regular',version='Version 1.000'))
    fb.setupPost(); fb.setupMaxp()
    fb.font.recalcTimestamp=False
    fb.font['head'].created=fb.font['head'].modified=3861000000
    fb.font.save(output/'NeoFontLite.ttf')
    font=TTFont(output/'NeoFontLite.ttf',recalcTimestamp=False)
    font.flavor='woff2'; font.save(output/'NeoFontLite.woff2')
    for suffix in ('ttf','woff2'):
        with TTFont(output/f'NeoFontLite.{suffix}') as result:
            assert result['name'].getDebugName(1)=='NeoFontLite'
            for letter in letters:
                assert result.getBestCmap()[ord(letter)]==letter
                assert result['glyf'][letter].numberOfContours>0


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    args=parser.parse_args()
    build(args.source)
