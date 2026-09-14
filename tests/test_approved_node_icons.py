import hashlib
import unittest
from pathlib import Path
from PIL import Image
from scripts.replace_approved_node_icons import ROOT, APPROVED

class ApprovedNodeIconsTest(unittest.TestCase):
    def test_portal_uses_canonical_packs_not_legacy_launch_icons(self):
        template = Path('app/templates/auth/portal.html').read_text()
        self.assertIn("'images/icons/neo' ~ key ~ '/inapp/neo' ~ key", template)
        self.assertNotIn("images/icons/icon_", template)
        sidebar = Path('app/static/css/12-neosektor.css').read_text()
        self.assertIn('.motherbrain-desktop-side-link[href="/neosektor/manage-employees"] .operational-side-link-label { text-transform:none; }', sidebar)

    def test_all_existing_sizes_use_exact_approved_artwork(self):
        for node, digest in APPROVED.items():
            pack = ROOT / node
            source = pack / 'source' / f'{node}-icon-original.png'
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), digest)
            with Image.open(source) as image:
                master = image.convert('RGB').resize((1024,1024), Image.Resampling.LANCZOS)
            for path in pack.rglob('*.png'):
                if path == source:
                    continue
                with self.subTest(node=node, file=path.name), Image.open(path) as icon:
                    expected = master.resize(icon.size, Image.Resampling.LANCZOS)
                    if 'maskable' in path.name:
                        inner = int(icon.width*.56)
                        expected = Image.new('RGB', icon.size, 'black')
                        expected.paste(master.resize((inner,inner), Image.Resampling.LANCZOS), ((icon.width-inner)//2,(icon.height-inner)//2))
                    self.assertEqual(icon.convert('RGB').tobytes(), expected.tobytes())
            with Image.open(pack/'favicon/favicon.ico') as icon:
                for size in icon.info['sizes']:
                    self.assertEqual(icon.ico.getimage(size).convert('RGB').tobytes(), master.resize(size, Image.Resampling.LANCZOS).tobytes())
