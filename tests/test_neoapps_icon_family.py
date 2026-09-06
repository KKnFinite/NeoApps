"""Byte/pixel-level checks of the approved square NeoApps icon family."""
import hashlib
import unittest

from PIL import Image
from scripts.generate_neoapps_icons import ROOT, SOURCE_SHA, PWA_SIZES, INAPP_SIZES, FAVICON_SIZES, ICO_SIZES


class NeoAppsIconFamilyTest(unittest.TestCase):
    def test_source_master_and_all_derivatives(self):
        source = ROOT / 'source/neoapps-icon-original.png'
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), SOURCE_SHA)
        with Image.open(source) as original:
            self.assertEqual((original.size, original.mode, original.format), ((1254,1254),'RGB','PNG'))
            expected_master = original.resize((1024,1024), Image.Resampling.LANCZOS)
        with Image.open(ROOT / 'source/neoapps-icon-master-1024.png') as master:
            self.assertEqual(master.tobytes(), expected_master.tobytes())
        expected = {'source/neoapps-icon-original.png': (1254,1254),
                    'source/neoapps-icon-master-1024.png': (1024,1024),
                    'pwa/apple-touch-icon.png': (180,180)}
        for folder, prefix, sizes in [('pwa','neoapps-icon',PWA_SIZES),
                                     ('inapp','neoapps-inapp',INAPP_SIZES),
                                     ('favicon','favicon',FAVICON_SIZES)]:
            expected.update({f'{folder}/{prefix}-{s}.png': (s,s) for s in sizes})
        for name, size in expected.items():
            with self.subTest(name=name), Image.open(ROOT / name) as icon:
                icon.load()
                self.assertEqual((icon.size,icon.mode,icon.format), (size,'RGB','PNG'))
                if not name.startswith('source/'):
                    self.assertEqual(icon.tobytes(),expected_master.resize(size,Image.Resampling.LANCZOS).tobytes())
        for size in (192,512):
            name = f'pwa/neoapps-maskable-{size}.png'
            expected[name] = (size,size)
            inner = int(size*.56)
            offset = (size-inner)//2
            self.assertLessEqual((inner/2+.5)*2**.5,size*.4)
            canvas = Image.new('RGB',(size,size),'black')
            canvas.paste(expected_master.resize((inner,inner),Image.Resampling.LANCZOS),(offset,offset))
            with Image.open(ROOT / name) as icon:
                self.assertEqual((icon.size,icon.mode,icon.format),((size,size),'RGB','PNG'))
                self.assertEqual(icon.tobytes(),canvas.tobytes())
        self.assertEqual({p.relative_to(ROOT).as_posix() for p in ROOT.rglob('*.png')}, set(expected))
        with Image.open(ROOT / 'favicon/favicon.ico') as ico:
            self.assertEqual(ico.format,'ICO')
            self.assertEqual(ico.info['sizes'], {(s,s) for s in ICO_SIZES})
            for size in ICO_SIZES:
                frame=ico.ico.getimage((size,size)).convert('RGB')
                self.assertEqual(frame.tobytes(),expected_master.resize((size,size),Image.Resampling.LANCZOS).tobytes())

    def test_no_active_legacy_square_references(self):
        app = ROOT.parents[3]
        for folder, suffix in [('templates','*.html'),('static/css','*.css')]:
            for path in (app / folder).rglob(suffix):
                with self.subTest(path=path):
                    text=path.read_text(encoding='utf-8')
                    self.assertNotIn('images/neoapps_logo_transparent.png',text)
                    self.assertNotIn('images/icons/neoportal/',text)
