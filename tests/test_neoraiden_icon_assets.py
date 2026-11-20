"""Approved NeoRaiden God Mode icon pack and integration contracts."""
import hashlib
import unittest
from pathlib import Path

from PIL import Image

from app.services.shell_metadata import NODE_IDENTITIES


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app/static"
PACK = STATIC / "images/icons/neoraiden"
SOURCE_SHA256 = "1ee756d4be58c3edafeee27904736cf070f09c8cc6840638d7dc2fff81549ee2"


class NeoRaidenIconAssetsTest(unittest.TestCase):
    def test_approved_source_is_byte_identical(self):
        original = PACK / "source/neoraiden-icon-original.png"
        self.assertEqual(hashlib.sha256(original.read_bytes()).hexdigest(), SOURCE_SHA256)
        with Image.open(original) as image:
            self.assertEqual((image.format, image.size), ("PNG", (1254, 1254)))

    def test_icon_sizes_and_node_logos(self):
        paths = {
            "source/neoraiden-icon-master-1024.png": 1024,
            "inapp/neoraiden-inapp-32.png": 32,
            "inapp/neoraiden-inapp-48.png": 48,
            "inapp/neoraiden-inapp-64.png": 64,
            "inapp/neoraiden-inapp-72.png": 72,
            "inapp/neoraiden-inapp-96.png": 96,
            "inapp/neoraiden-inapp-128.png": 128,
            "inapp/neoraiden-inapp-256.png": 256,
            "pwa/neoraiden-icon-192.png": 192,
            "pwa/neoraiden-icon-512.png": 512,
            "pwa/neoraiden-maskable-192.png": 192,
            "pwa/neoraiden-maskable-512.png": 512,
            "pwa/apple-touch-icon.png": 180,
        }
        for name, size in paths.items():
            with self.subTest(file=name), Image.open(PACK / name) as image:
                image.load()
                self.assertEqual((image.format, image.size), ("PNG", (size, size)))
        for name, size in [("newlogo_raiden.png", 512), ("newlogo_raiden_small.png", 256)]:
            with self.subTest(file=name), Image.open(STATIC / "images/logos" / name) as image:
                self.assertEqual((image.format, image.size), ("PNG", (size, size)))

    def test_shell_gateway_switcher_and_manifest_use_new_assets(self):
        identity = NODE_IDENTITIES["raiden"]
        self.assertEqual(identity["locked_icon"], "images/icons/neoraiden/inapp/neoraiden-inapp-128.png")
        self.assertEqual(identity["desktop_icon"], "images/logos/newlogo_raiden.png")
        gateway = (ROOT / "app/templates/neomotherbrain/rfd_hub.html").read_text(encoding="utf-8")
        self.assertIn('"raiden": "images/logos/newlogo_raiden_small.png"', gateway)
        self.assertIn('node_card("raiden", "Raiden", "God Mode"', gateway)
        startup = (ROOT / "app/__init__.py").read_text(encoding="utf-8")
        self.assertIn('icon_src="images/logos/newlogo_raiden_small.png"', startup)
        self.assertIn('return "neoraiden"', startup)
        self.assertIn('"start_url": "/neoraiden"', startup)


if __name__ == "__main__":
    unittest.main()
