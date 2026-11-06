"""Rebuild bandwidth-focused derivatives; original artwork is never modified.

Run with the development Pillow environment from the repository root.
"""
import hashlib
import json
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1] / "app/static"


def build():
    specs = []
    for name in ("motherbrain", "sektor", "ermac", "scorpion", "rain", "subzero"):
        specs.append((f"images/logos/newlogo_{name}.png", 288, True))
    for name in ("motherbrain", "sektor", "ermac", "rain", "subzero", "reptile"):
        specs.append((f"images/logos/newlogo_{name}_small.png", 256, True))
    specs += [(f"images/hero/{name}.png", None, False) for name in (
        "neoapps_login_mobile", "neoapps_login_desktop", "neoapps_portal_desktop",
        "hero_gateway", "hero_gateway_small")]
    specs.append(("images/neoscorpion/spear-promo.png", None, False))
    rows = []
    for source, maximum, lossless in specs:
        target = "images/optimized/" + Path(source).stem + ".webp"
        image = Image.open(ROOT / source).convert("RGBA")
        if maximum:
            image.thumbnail((maximum, maximum), Image.Resampling.LANCZOS)
        destination = ROOT / target
        destination.parent.mkdir(parents=True, exist_ok=True)
        image.save(destination, "WEBP", lossless=lossless, quality=94, method=6, exact=True)
        rows.append(dict(source=source, file=target, width=image.width, height=image.height,
                         lossless=lossless, quality=94, source_bytes=(ROOT / source).stat().st_size,
                         source_sha256=hashlib.sha256((ROOT / source).read_bytes()).hexdigest(),
                         bytes=destination.stat().st_size,
                         sha256=hashlib.sha256(destination.read_bytes()).hexdigest()))
    # The shared menu has its own 28px use: 64px supplies more than 2x density.
    source = "images/logos/newlogo_scorpion.png"
    target = "images/optimized/newlogo_scorpion_menu.png"
    image = Image.open(ROOT / source).convert("RGBA")
    image.thumbnail((64, 64), Image.Resampling.LANCZOS)
    image.save(ROOT / target, "PNG", optimize=True)
    rows.append(dict(source=source, file=target, width=64, height=64, lossless=True,
                     purpose="character-menu", source_bytes=(ROOT / source).stat().st_size,
                     source_sha256=hashlib.sha256((ROOT / source).read_bytes()).hexdigest(),
                     bytes=(ROOT / target).stat().st_size,
                     sha256=hashlib.sha256((ROOT / target).read_bytes()).hexdigest()))
    (ROOT / "images/optimized/manifest.json").write_text(
        json.dumps(dict(generator="scripts/build_image_derivatives.py", images=rows), indent=2) + "\n",
        encoding="utf-8")
    print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    build()
