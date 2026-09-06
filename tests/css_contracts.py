"""Read the ordered CSS source contract, independent of delivery segmentation."""
import json
from pathlib import Path


def stylesheet_source():
    root = Path(__file__).resolve().parents[1] / 'app/static/css'
    sequence = json.loads((root / 'sequence.json').read_text(encoding='utf-8'))
    return ''.join((root / row['file']).read_text(encoding='utf-8')
                   for row in sequence['stylesheets'])
