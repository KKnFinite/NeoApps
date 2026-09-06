"""Static, process-local asset metadata only. Never resolves users or database state."""
import json
from pathlib import Path

_CSS_ROOT = Path(__file__).resolve().parents[1] / 'static' / 'css'
CSS_SEQUENCE = tuple(json.loads((_CSS_ROOT / 'sequence.json').read_text(encoding='utf-8'))['stylesheets'])


def page_stylesheets(blueprint):
    """Keep the original cascade positions, omitting only other nodes' blocks."""
    return tuple('css/' + row['file'] for row in CSS_SEQUENCE
                 if row['scope'] in ('shared', blueprint))
