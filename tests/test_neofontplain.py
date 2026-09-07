"""Standalone font contracts; no application or font-build dependencies needed."""
import hashlib
import json
import string
import struct
import unittest
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
FONT = ROOT/'app/static/fonts/neofontplain'


class Sfnt:
    def __init__(self, path):
        self.data = path.read_bytes()
        self.tables = {}
        for i in range(self.u16(4)):
            tag, _, offset, length = struct.unpack_from('>4sIII', self.data, 12+i*16)
            assert offset+length <= len(self.data)
            self.tables[tag] = offset

    def u16(self, offset):
        return struct.unpack_from('>H', self.data, offset)[0]

    def names(self, name_id):
        start = self.tables[b'name']
        strings = start+self.u16(start+4)
        result = []
        for i in range(self.u16(start+2)):
            platform, _, _, ident, length, offset = struct.unpack_from('>6H', self.data, start+6+i*12)
            if platform == 3 and ident == name_id:
                result.append(self.data[strings+offset:strings+offset+length].decode('utf-16-be'))
        return result

    def cmap(self):
        start = self.tables[b'cmap']
        for i in range(self.u16(start+2)):
            platform, encoding, offset = struct.unpack_from('>HHI', self.data, start+4+i*8)
            if (platform, encoding) == (3, 1):
                start += offset
                break
        assert self.u16(start) == 4
        count = self.u16(start+6)//2
        ends = start+14
        starts = ends+2*count+2
        deltas = starts+2*count
        offsets = deltas+2*count
        result = {}
        for i in range(count):
            for code in range(self.u16(starts+2*i), self.u16(ends+2*i)+1):
                if code == 65535:
                    continue
                delta, offset = self.u16(deltas+2*i), self.u16(offsets+2*i)
                glyph = self.u16(offsets+2*i+offset+2*(code-self.u16(starts+2*i))) if offset else code
                result[code] = (glyph+delta) % 65536 if glyph else 0
        return result

    def glyph(self, index):
        loca = self.tables[b'loca']
        long = self.u16(self.tables[b'head']+50) == 1
        def offset(n):
            return struct.unpack_from('>I', self.data, loca+n*4)[0] if long else self.u16(loca+n*2)*2
        start, end = offset(index), offset(index+1)
        base = self.tables[b'glyf']
        return self.data[base+start:base+end]

    def advance(self, index):
        count = self.u16(self.tables[b'hhea']+34)
        return self.u16(self.tables[b'hmtx']+min(index, count-1)*4)


class NeoFontPlainTest(unittest.TestCase):
    def setUp(self):
        self.fonts = {style: Sfnt(FONT/f'NeoFontPlain-{style}.ttf') for style in ('Regular', 'SemiBold')}

    def test_real_formats_family_weights_and_line_metrics(self):
        for style, font in self.fonts.items():
            with self.subTest(style=style):
                self.assertEqual(font.data[:4], b'\x00\x01\x00\x00')
                self.assertEqual((FONT/f'NeoFontPlain-{style}.woff2').read_bytes()[:4], b'wOF2')
                self.assertEqual(font.names(1), ['NeoFontPlain'])
                self.assertEqual(font.names(2), [style])
                self.assertEqual(font.u16(font.tables[b'OS/2']+4), 400 if style == 'Regular' else 600)
                self.assertEqual(font.u16(font.tables[b'head']+18), 1000)
                self.assertEqual(struct.unpack_from('>hhh', font.data, font.tables[b'hhea']+4), (900, -250, 0))

    def test_full_ascii_symbols_and_genuine_lowercase(self):
        extra = '£€°×÷−–—·•…‘’“”←↑→↓✓\u00a0'
        for style, font in self.fonts.items():
            cmap = font.cmap()
            self.assertEqual(len(cmap), 116)
            for char in ''.join(chr(c) for c in range(32, 127))+extra:
                with self.subTest(style=style, char=char):
                    self.assertGreater(cmap[ord(char)], 0)
                    if char in ' \u00a0':
                        self.assertFalse(font.glyph(cmap[ord(char)]))
                    else:
                        self.assertTrue(font.glyph(cmap[ord(char)]))
            for char in string.ascii_lowercase:
                self.assertNotEqual(font.glyph(cmap[ord(char)]), font.glyph(cmap[ord(char.upper())]))

    def test_tabular_digits_weight_stability_and_disambiguation(self):
        advances = []
        for font in self.fonts.values():
            cmap = font.cmap()
            self.assertEqual({font.advance(cmap[ord(c)]) for c in string.digits}, {620})
            self.assertEqual(len({font.glyph(cmap[ord(c)]) for c in 'Il1O0o'}), 6)
            advances.append({code: font.advance(index) for code, index in cmap.items()})
        self.assertEqual(*advances)
        regular, bold = self.fonts.values()
        self.assertNotEqual(regular.glyph(regular.cmap()[ord('H')]), bold.glyph(bold.cmap()[ord('H')]))

    def test_safe_bounds_current_vectors_and_visible_counters(self):
        report = json.loads((FONT/'build-metrics.json').read_text())
        self.assertEqual(report['source_sha256'], hashlib.sha256((FONT/'source/geometry.py').read_text(encoding='utf-8').encode('utf-8')).hexdigest())
        for style, font in self.fonts.items():
            for code, index in font.cmap().items():
                glyph = font.glyph(index)
                record = report['weights'][style]['glyphs'][f'uni{code:04X}']
                tree = ElementTree.parse(FONT/f'glyphs/{style.lower()}/uni{code:04X}.svg')
                self.assertEqual(tree.getroot().get('viewBox'), f"0 0 {record['advance']} 1150")
                if not glyph:
                    continue
                count, left, bottom, right, top = struct.unpack_from('>hhhhh', glyph)
                self.assertEqual([left, bottom, right, top], record['bounds'])
                self.assertGreater(count, 0)
                self.assertGreaterEqual(left, 0)
                self.assertLessEqual(right, font.advance(index))
                self.assertGreaterEqual(bottom, -250)
                self.assertLessEqual(top, 900)
                if chr(code) in 'ABDOPQRabdgopq04689':
                    self.assertGreaterEqual(count, 2, chr(code))

    def test_specimen_only_no_application_rollout(self):
        preview = (FONT/'preview.html').read_text(encoding='utf-8')
        for style in self.fonts:
            self.assertIn(f'NeoFontPlain-{style}.woff2', preview)
        for sample in ('ABCDEFGHIJKLM', 'abcdefghijklm', '0123456789', 'font-size:12px', 'font-size:14px', 'font-size:16px'):
            self.assertIn(sample, preview)
        for directory in (ROOT/'app/templates', ROOT/'app/static/css', ROOT/'app/static/js'):
            for path in directory.rglob('*'):
                if path.is_file() and path.suffix in ('.html', '.css', '.js'):
                    self.assertNotIn('NeoFontPlain', path.read_text(encoding='utf-8'), str(path))


if __name__ == '__main__':
    unittest.main()
