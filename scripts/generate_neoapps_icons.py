"""Rebuild only the approved square NeoApps icon family using Pillow Lanczos."""
import argparse
import hashlib
import shutil
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1] / 'app/static/images/icons/neoapps'
SOURCE_SHA = '4f99b3e6f4ec694f84c3b1d450a5f60c083f40c4d50559c413455c0ffc84fad5'
PWA_SIZES = (72, 96, 128, 144, 152, 167, 180, 192, 256, 384, 512, 1024)
INAPP_SIZES = (32, 40, 48, 64, 72, 96, 128, 160, 192, 256, 384, 512)
FAVICON_SIZES = (16, 24, 32, 48, 64)
ICO_SIZES = (16, 32, 48, 64)


def generate(source):
    source = Path(source)
    if hashlib.sha256(source.read_bytes()).hexdigest() != SOURCE_SHA:
        raise ValueError('Approved source SHA-256 mismatch')
    with Image.open(source) as original:
        if original.size != (1254, 1254) or original.mode != 'RGB' or original.format != 'PNG':
            raise ValueError('Approved source format mismatch')
        original.load()
        master = original.resize((1024, 1024), Image.Resampling.LANCZOS)
    destination = ROOT / 'source/neoapps-icon-original.png'
    if source.resolve() != destination.resolve():
        shutil.copyfile(source, destination)
    master.save(ROOT / 'source/neoapps-icon-master-1024.png')
    for folder, prefix, sizes in (
        ('pwa', 'neoapps-icon', PWA_SIZES),
        ('inapp', 'neoapps-inapp', INAPP_SIZES),
        ('favicon', 'favicon', FAVICON_SIZES),
    ):
        for size in sizes:
            master.resize((size, size), Image.Resampling.LANCZOS).save(ROOT / folder / f'{prefix}-{size}.png')
    master.resize((180, 180), Image.Resampling.LANCZOS).save(ROOT / 'pwa/apple-touch-icon.png')
    for size in (192, 512):
        # A centered 56% square fits inside the guaranteed 80%-diameter safe circle.
        inner = int(size * .56)
        canvas = Image.new('RGB', (size, size), (0, 0, 0))
        canvas.paste(master.resize((inner, inner), Image.Resampling.LANCZOS), ((size-inner)//2, (size-inner)//2))
        canvas.save(ROOT / 'pwa' / f'neoapps-maskable-{size}.png')
    frames = [master.resize((s, s), Image.Resampling.LANCZOS) for s in ICO_SIZES]
    frames[-1].save(ROOT / 'favicon/favicon.ico', format='ICO',
                    sizes=[(s,s) for s in ICO_SIZES], append_images=frames[:-1])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    generate(parser.parse_args().source)
