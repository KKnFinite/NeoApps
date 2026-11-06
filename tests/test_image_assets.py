"""Image delivery must be content-stable, public and independent of user SQL."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from flask import url_for
from PIL import Image

from app import create_app
from app.extensions import login_manager
from app.services.image_assets import image_digest


class ImageAssetsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app(type('ImageFixture', (), dict(
            TESTING=True, SECRET_KEY='image-assets-isolated',
            SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
            SQLALCHEMY_TRACK_MODIFICATIONS=False)))
        cls.root = Path(cls.app.static_folder)
        cls.rows = json.loads((cls.root/'images/optimized/manifest.json').read_text())['images']

    def image_url(self, filename):
        with self.app.test_request_context():
            return url_for('static', filename=filename)

    def test_derivatives_keep_originals_proportions_alpha_and_verified_identity(self):
        for row in self.rows:
            with self.subTest(source=row['source'], file=row['file']):
                source = self.root/row['source']
                target = self.root/row['file']
                self.assertEqual(source.stat().st_size, row['source_bytes'])
                self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), row['source_sha256'])
                self.assertEqual(target.stat().st_size, row['bytes'])
                self.assertLess(row['bytes'], row['source_bytes'])
                self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), row['sha256'])
                with Image.open(source) as original, Image.open(target) as optimized:
                    self.assertEqual(optimized.size, (row['width'], row['height']))
                    self.assertAlmostEqual(original.width/original.height, optimized.width/optimized.height, delta=.01)
                    if row['lossless']:
                        expected = original.convert('RGBA')
                        expected.thumbnail(optimized.size, Image.Resampling.LANCZOS)
                        self.assertEqual(expected.tobytes(), optimized.convert('RGBA').tobytes())

    def test_menu_uses_64px_original_artwork_and_a_content_version(self):
        self.assertEqual(Image.open(self.root/'images/optimized/newlogo_scorpion_menu.png').size, (64,64))
        path = self.image_url('images/optimized/newlogo_scorpion_menu.png')
        self.assertEqual(parse_qs(urlsplit(path).query)['v'], [image_digest(self.root,'images/optimized/newlogo_scorpion_menu.png')])

    def test_all_derivative_references_resolve_without_changing_template_geometry(self):
        for row in self.rows:
            with self.subTest(file=row['file']):
                path = self.image_url(row['file'] if row.get('purpose') else row['source'])
                self.assertEqual(urlsplit(path).path, '/static/'+row['file'])
                response = self.app.test_client().get(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data, (self.root/row['file']).read_bytes())
                response.close()

    def test_image_url_survives_unrelated_release_and_css_js_still_use_release_version(self):
        before = self.image_url('images/logos/newlogo_scorpion.png')
        with patch.dict(self.app.config, STATIC_ASSET_VERSION='unrelated-next-release'):
            self.assertEqual(before, self.image_url('images/logos/newlogo_scorpion.png'))
            with self.app.test_request_context():
                for file in ('css/base.css','js/csrf.js'):
                    self.assertEqual(parse_qs(urlsplit(url_for('static',filename=file,v=self.app.config['STATIC_ASSET_VERSION'])).query)['v'], ['unrelated-next-release'])

    def test_content_changes_invalidate_url_but_deployment_mtime_does_not(self):
        import os
        with tempfile.TemporaryDirectory(prefix='neo-image-test-') as folder:
            path = Path(folder)/'images/test.png'
            path.parent.mkdir()
            path.write_bytes(b'first-artwork')
            original_folder = self.app.static_folder
            try:
                self.app.static_folder = folder
                before = self.image_url('images/test.png')
                os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns+1000000))
                self.assertEqual(before, self.image_url('images/test.png'))
                path.write_bytes(b'changed-artwork')
                self.assertNotEqual(before, self.image_url('images/test.png'))
            finally:
                self.app.static_folder = original_folder

    def test_public_image_immutable_get_head_etag_and_conditional_no_body(self):
        path = self.image_url('images/optimized/newlogo_scorpion_menu.png')
        client = self.app.test_client()
        with patch.object(login_manager, '_user_callback', side_effect=AssertionError('Images must not load users')):
            for method in ('GET','HEAD'):
                response = client.open(path, method=method)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers['Cache-Control'], 'public, max-age=31536000, immutable')
                self.assertEqual(response.get_etag()[0], parse_qs(urlsplit(path).query)['v'][0])
                conditional = client.open(path, method=method, headers={'If-None-Match':response.headers['ETag']})
                self.assertEqual(conditional.status_code, 304)
                self.assertEqual(conditional.data, b'')
                self.assertIn('immutable', conditional.headers['Cache-Control'])
                response.close(); conditional.close()

    def test_wrong_image_version_missing_assets_and_path_escape_are_not_immutable(self):
        client = self.app.test_client()
        for suffix in ('','?v=old-artwork'):
            response = client.get('/static/images/optimized/newlogo_scorpion_menu.png'+suffix)
            self.assertEqual(response.status_code, 200)
            self.assertNotIn('immutable', response.headers['Cache-Control'])
            response.close()
        self.assertIsNone(image_digest(self.root, 'images/../../config.py'))
        self.assertIsNone(image_digest(self.root, 'images/missing.png'))
        missing = client.get('/static/images/missing.png?v=old-artwork')
        self.assertEqual(missing.status_code,404)
        self.assertNotIn('immutable',missing.headers.get('Cache-Control',''))

    def test_authenticated_page_and_service_worker_do_not_gain_immutable_cache(self):
        client = self.app.test_client()
        for path in ('/login','/healthz','/service-worker.js','/manifest.webmanifest'):
            response = client.get(path)
            self.assertEqual(response.status_code,200)
            self.assertNotIn('immutable',response.headers.get('Cache-Control',''))
            if path == '/service-worker.js':
                self.assertIn(b'caches.open',response.data)
                self.assertEqual(response.headers['Service-Worker-Allowed'],'/')
            response.close()
