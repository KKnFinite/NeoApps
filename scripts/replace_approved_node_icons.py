"""Resize approved square PNGs into the existing Gateway/Staffing icon packs.

No cropping, retouching or hero changes. Uses the same Lanczos/master and
maskable-safe-area convention as generate_neoapps_icons.py.
"""
import argparse
import hashlib
import shutil
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1] / 'app/static/images/icons'
APPROVED = {
    'neogateway': '532e895912b51f50c2dff62bc30852102e193740cf23556fb3a6ce05b3adb56f',
    'neostaffing': 'e17014714de730f8d7f78518ab674b12485d9e1e003380c7bf61f95aa4f31f90',
}

def generate(node, source):
    source = Path(source)
    if hashlib.sha256(source.read_bytes()).hexdigest() != APPROVED[node]:
        raise ValueError('Approved source SHA-256 mismatch')
    pack = ROOT / node
    with Image.open(source) as original:
        if original.size != (1254, 1254) or original.format != 'PNG':
            raise ValueError('Expected approved square PNG')
        master = original.convert('RGB').resize((1024, 1024), Image.Resampling.LANCZOS)
    destination = pack / 'source' / f'{node}-icon-original.png'
    if source.resolve() != destination.resolve():
        shutil.copyfile(source, destination)
    for path in pack.rglob('*.png'):
        if path == destination:
            continue
        with Image.open(path) as old:
            size = old.size
        image = master.resize(size, Image.Resampling.LANCZOS)
        if 'maskable' in path.name:
            inner = int(size[0] * .56)
            image = Image.new('RGB', size, 'black')
            image.paste(master.resize((inner, inner), Image.Resampling.LANCZOS), ((size[0]-inner)//2, (size[1]-inner)//2))
        image.save(path)
    ico_path = pack / 'favicon/favicon.ico'
    with Image.open(ico_path) as old:
        sizes = sorted(old.info['sizes'])
    frames = [master.resize(size, Image.Resampling.LANCZOS) for size in sizes]
    frames[-1].save(ico_path, format='ICO', sizes=sizes, append_images=frames[:-1])

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('node', choices=APPROVED)
    parser.add_argument('source', type=Path)
    args = parser.parse_args()
    generate(args.node, args.source)
