"""Font/static regressions without adding build dependencies to the runtime suite."""
import hashlib
import string
import struct
import unittest
from pathlib import Path
from xml.etree import ElementTree

ROOT=Path(__file__).resolve().parents[1]
FONT=ROOT/'app/static/fonts/neofontlite'


class NeoFontLiteTest(unittest.TestCase):
    def test_source_is_approved(self):
        self.assertEqual(hashlib.sha256((FONT/'source/neo-font-lite-alphabet-specimen.png').read_bytes()).hexdigest(),
                         '50d9b3faefae694d2a24eb16e9ff5f49c0049b31517915a447c960a9470c3c15')

    def test_font_formats_mapping_and_sample_words(self):
        data=(FONT/'NeoFontLite.ttf').read_bytes()
        self.assertEqual(data[:4],b'\x00\x01\x00\x00')
        u16=lambda offset:struct.unpack_from('>H',data,offset)[0]
        tables={}
        for i in range(u16(4)):
            tag,_,offset,length=struct.unpack_from('>4sIII',data,12+i*16)
            tables[tag]=offset
            self.assertLessEqual(offset+length,len(data))
        self.assertEqual(u16(tables[b'OS/2']+4),300)
        name=tables[b'name']; strings=name+u16(name+4)
        families=[]
        for i in range(u16(name+2)):
            platform,_,_,name_id,length,offset=struct.unpack_from('>6H',data,name+6+i*12)
            if platform==3 and name_id==1:
                families.append(data[strings+offset:strings+offset+length].decode('utf-16-be'))
        self.assertEqual(families,['NeoFontLite'])
        cmap=tables[b'cmap']
        for i in range(u16(cmap+2)):
            platform,encoding,offset=struct.unpack_from('>HHI',data,cmap+4+i*8)
            if (platform,encoding)==(3,1):
                cmap+=offset
                break
        self.assertEqual(u16(cmap),4)
        segments=u16(cmap+6)//2
        def glyph(code):
            for i in range(segments):
                end=u16(cmap+14+i*2); start=u16(cmap+16+segments*2+i*2)
                if start<=code<=end:
                    delta=u16(cmap+16+segments*4+i*2)
                    location=cmap+16+segments*6+i*2
                    jump=u16(location)
                    return (code+delta)&65535 if not jump else (u16(location+jump+(code-start)*2)+delta)&65535
            return 0
        for i,c in enumerate(string.ascii_uppercase,2):
            self.assertEqual(glyph(ord(c)),i)
            self.assertEqual(glyph(ord(c.lower())),i)
        for word in ('NEOGATEWAY','PORTAL','HOME','NODES','MENU','SETTINGS','DASHBOARD'):
            self.assertTrue(all(glyph(ord(c))>1 for c in word))
        woff=(FONT/'NeoFontLite.woff2').read_bytes()
        self.assertEqual(woff[:4],b'wOF2')
        self.assertEqual(struct.unpack_from('>I',woff,8)[0],len(woff))

    def test_all_vector_glyphs_and_narrow_css_scope(self):
        self.assertEqual({p.stem for p in (FONT/'glyphs').glob('*.svg')},set(string.ascii_uppercase))
        for path in (FONT/'glyphs').glob('*.svg'):
            tree=ElementTree.parse(path)
            self.assertTrue(tree.find('{http://www.w3.org/2000/svg}path').get('d'))
        css=(ROOT/'app/static/css/neofontlite.css').read_text()
        self.assertIn('@media (max-width: 900px)',css)
        self.assertIn('body .gateway-mobile-header .neo-mobile-product-name',css)
        self.assertNotIn('!important',css)
        template=(ROOT/'app/templates/base.html').read_text()
        self.assertIn("{% if is_rfd_hub_page %}\n    <link rel=\"stylesheet\" href=\"{{ url_for('static', filename='css/neofontlite.css'",template)
