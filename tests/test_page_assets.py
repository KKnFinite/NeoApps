"""Delivery metadata must preserve cascade, not regroup each node after shared CSS."""
import hashlib
import unittest
from pathlib import Path

from app.services.page_assets import CSS_SEQUENCE, page_stylesheets


class PageAssetsTest(unittest.TestCase):
    def test_every_fragment_exists_once_and_matches_its_source_digest(self):
        root = Path(__file__).resolve().parents[1] / 'app/static/css'
        self.assertEqual(len(CSS_SEQUENCE), len({row['file'] for row in CSS_SEQUENCE}))
        for row in CSS_SEQUENCE:
            with self.subTest(file=row['file']):
                source = (root / row['file']).read_text(encoding='utf-8').encode()
                self.assertEqual(len(source), row['bytes'])
                self.assertEqual(hashlib.sha256(source).hexdigest(), row['sha256'])

    def test_each_page_retains_original_shared_and_node_cascade_positions(self):
        shared = page_stylesheets(None)
        for blueprint in ('auth', 'neomotherbrain', 'neostaffing', 'neoermac',
                          'neosektor', 'neoscorpion', 'neorain', 'neosubzero'):
            with self.subTest(blueprint=blueprint):
                actual = page_stylesheets(blueprint)
                expected = tuple('css/' + row['file'] for row in CSS_SEQUENCE
                                 if row['scope'] in ('shared', blueprint))
                self.assertEqual(actual, expected)
                self.assertEqual(tuple(path for path in actual if path in shared), shared)
                self.assertEqual(actual[0], 'css/base.css')
                self.assertLess(len(actual), len(CSS_SEQUENCE))

    def test_native_feedback_is_opt_in_and_security_setup_stays_synchronous(self):
        root = Path(__file__).resolve().parents[1]
        base = (root / 'app/templates/base.html').read_text(encoding='utf-8')
        self.assertIn('if uses_live_update_controller', base)
        self.assertIn('if is_neostaffing_page', base)
        self.assertIn('script defer src="{{ url_for(\'static\', filename=\'js/interaction_states.js\'', base)
        self.assertIn('headers.set("X-CSRF-Token", token)', base)
        for page in ('login_hub', 'portal'):
            self.assertIn('data-interaction-form', (root / f'app/templates/auth/{page}.html').read_text(encoding='utf-8'))
