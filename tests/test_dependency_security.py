"""Patched dependency floors, PDF compatibility and safe file-serving behavior."""
from importlib.metadata import requires, version
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from flask import Flask, send_from_directory
from packaging.requirements import Requirement
from packaging.version import Version
from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError
from reportlab.pdfgen import canvas
from PIL import Image
from werkzeug import security


class DependencySecurityTest(unittest.TestCase):
    def test_declared_and_installed_security_floors(self):
        requirements = {r.name.lower(): r for line in Path('requirements.txt').read_text(encoding='utf8').splitlines()
                        if line.strip() and not line.startswith('#') for r in [Requirement(line)]}
        self.assertEqual(str(requirements['werkzeug'].specifier), '==3.1.9')
        self.assertNotIn('6.21.2', requirements['pypdf'].specifier)
        self.assertNotIn('5.9.0', requirements['pypdf'].specifier)
        self.assertNotIn('6.21.3', requirements['pypdf'].specifier)
        self.assertIn('6.21.4', requirements['pypdf'].specifier)
        self.assertGreaterEqual(Version(version('Werkzeug')), Version('3.1.9'))
        self.assertGreaterEqual(Version(version('pypdf')), Version('6.21.4'))
        self.assertNotIn('12.3.0', requirements['pillow'].specifier)
        self.assertIn('13.0.0', requirements['pillow'].specifier)
        self.assertNotIn('14.0.0', requirements['pillow'].specifier)
        self.assertGreaterEqual(Version(version('Pillow')), Version('13.0.0'))
        flask_werkzeug = next(Requirement(r) for r in requires('Flask') if Requirement(r).name.lower() == 'werkzeug')
        self.assertIn(version('Werkzeug'), flask_werkzeug.specifier)

    def test_windows_device_ads_names_are_rejected_without_opening_them(self):
        # Patch only the dependency's OS facade, never process-wide os.name.
        with patch.object(security, 'os', SimpleNamespace(name='nt', path=security.os.path)):
            for name in ('NUL:', 'CON:', 'AUX:', 'COM1:', 'LPT1:', 'nested/NUL:', 'NUL.txt:'):
                with self.subTest(name=name):
                    self.assertIsNone(security.safe_join('trusted', name))
            self.assertEqual(security.safe_join('trusted', 'report.pdf'), 'trusted/report.pdf')

    def test_file_serving_keeps_range_conditional_and_traversal_protections(self):
        with TemporaryDirectory() as directory:
            Path(directory, 'report.txt').write_text('Synthetic report', encoding='utf8')
            app = Flask(__name__)
            @app.get('/files/<path:name>')
            def download(name):
                return send_from_directory(directory, name, conditional=True)
            client = app.test_client()
            with client.get('/files/report.txt') as response:
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data, b'Synthetic report')
                etag = response.headers['ETag']
            with client.get('/files/report.txt', headers={'If-None-Match': etag}) as response:
                self.assertEqual(response.status_code, 304)
            with client.get('/files/report.txt', headers={'Range': 'bytes=0-8'}) as response:
                self.assertEqual(response.status_code, 206)
                self.assertEqual(response.data, b'Synthetic')
            with client.get('/files/../outside.txt') as response:
                self.assertEqual(response.status_code, 404)

    def test_reportlab_generation_pypdf_parse_and_encrypted_roundtrip(self):
        source = BytesIO()
        report = canvas.Canvas(source)
        report.drawString(50, 750, 'Synthetic Fuel Report FOB 12.3')
        report.showPage()
        report.save()
        reader = PdfReader(BytesIO(source.getvalue()), strict=True)
        self.assertEqual(len(reader.pages), 1)
        self.assertIn('FOB 12.3', reader.pages[0].extract_text())
        writer = PdfWriter()
        writer.append(reader)
        writer.encrypt('synthetic-only-password', algorithm='AES-256')
        encrypted = BytesIO()
        writer.write(encrypted)
        restored = PdfReader(BytesIO(encrypted.getvalue()))
        self.assertTrue(restored.is_encrypted)
        self.assertTrue(restored.decrypt('synthetic-only-password'))
        self.assertEqual(len(restored.pages), 1)
        self.assertIn('FOB 12.3', restored.pages[0].extract_text())
        with self.assertRaises(PdfReadError):
            PdfReader(BytesIO(b'%PDF-1.7\ninvalid truncated fixture'), strict=True)

    def test_pillow_image_codecs_and_pdf_image_embedding(self):
        for format_name in ('PNG', 'JPEG', 'WEBP', 'TIFF'):
            with self.subTest(format=format_name):
                image = BytesIO()
                with Image.new('RGB', (8, 8), (80, 120, 160)) as original:
                    original.save(image, format=format_name)
                with Image.open(BytesIO(image.getvalue())) as decoded:
                    decoded.load()
                    self.assertEqual(decoded.size, (8, 8))
                    self.assertEqual(decoded.mode, 'RGB')
        from reportlab.lib.utils import ImageReader
        pdf = BytesIO()
        report = canvas.Canvas(pdf)
        report.drawImage(ImageReader(BytesIO(image.getvalue())), 50, 700)
        report.showPage()
        report.save()
        parsed = PdfReader(BytesIO(pdf.getvalue()), strict=True)
        self.assertEqual(len(parsed.pages), 1)
        self.assertEqual(len(parsed.pages[0].images), 1)
